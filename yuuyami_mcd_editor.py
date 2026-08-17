#!/usr/bin/env python3
"""
Yuuyami Doori Tankentai (SLPS-02274) — Simplified MCD Editor RC3

Purpose:
  Load a raw 128 KiB PlayStation .mcd, edit rumor records and/or the saved day
  in one occupied Yuuyami internal save slot, recalculate frame CRCs, and save
  a new .mcd.

Supported rumor states:
  clear     -> 00 00 00 00
  acquired  -> 03 00 00 01
  solved    -> byte 0 = 0E; bytes 1-2 preserved; byte 3 preserved when
               nonzero, otherwise initialized to 01.

Other edits:
  --day N         Sets the saved-day byte and its summary mirror.
  --affliction N  EXPERIMENTAL arbitrary editing of the observed affliction byte.
                  The byte location is verified, but the full meaning/safe value
                  range has not been decoded.

Examples:
  python yuuyami_mcd_editor_simple_rc3.py input.mcd output.mcd --slot 3 \
      --set 38=solved --day 97

  python yuuyami_mcd_editor_simple_rc3.py input.mcd output.mcd --slot 3 \
      --affliction 2
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

CARD_SIZE = 128 * 1024
BLOCK_SIZE = 8192
DIR_FRAME_SIZE = 128
SAVE_NAME = "BISLPS-02274"
SAVE_SIZE = 0x4000

SLOT_SUMMARY_BASE = 0x200
SLOT_SUMMARY_SIZE = 0x08
SLOT_DATA_BASE = 0x280
SLOT_DATA_SIZE = 0xC00
SLOT_COUNT = 5
FRAME_SIZE = 128
FRAME_PAYLOAD = 126
FRAMES_PER_SLOT = 24
DEFRAMED_SIZE = FRAME_PAYLOAD * FRAMES_PER_SLOT
PERSISTENT_STATE_SIZE = 0xB88

RUMOR_COUNT = 44
RUMOR_SIZE = 4
AFFLICTION_OFFSET = 0x384
DAY_OFFSET = 0x385

CLEAR = bytes.fromhex("00000000")
ACQUIRED = bytes.fromhex("03000001")


class EditorError(Exception):
    pass


def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)) & 0xFFFF
    return crc


def find_save_chain(card: bytes) -> list[int]:
    starts = []
    for block in range(1, 16):
        off = block * DIR_FRAME_SIZE
        frame = card[off:off + DIR_FRAME_SIZE]
        status = frame[0]
        size = struct.unpack_from("<I", frame, 4)[0]
        name = frame[0x0A:0x20].split(b"\0", 1)[0].decode("ascii", "replace")
        if status == 0x51 and name == SAVE_NAME:
            starts.append((block, size, struct.unpack_from("<H", frame, 8)[0]))

    if len(starts) != 1:
        raise EditorError(f"expected exactly one {SAVE_NAME} save, found {len(starts)}")

    block, size, next_raw = starts[0]
    if size != SAVE_SIZE:
        raise EditorError(f"unexpected Yuuyami save size 0x{size:X}")

    chain = [block]
    while len(chain) < 2:
        if next_raw == 0xFFFF:
            raise EditorError("Yuuyami save chain ended early")
        block = next_raw + 1
        if block in chain or not 1 <= block <= 15:
            raise EditorError("invalid Yuuyami save chain")
        chain.append(block)
        off = block * DIR_FRAME_SIZE
        next_raw = struct.unpack_from("<H", card, off + 8)[0]
    return chain


def extract_save(card: bytes, chain: list[int]) -> bytearray:
    data = bytearray()
    for block in chain:
        off = block * BLOCK_SIZE
        data += card[off:off + BLOCK_SIZE]
    return data[:SAVE_SIZE]


def install_save(card: bytearray, chain: list[int], save: bytes) -> None:
    for i, block in enumerate(chain):
        off = block * BLOCK_SIZE
        card[off:off + BLOCK_SIZE] = save[i * BLOCK_SIZE:(i + 1) * BLOCK_SIZE]


def slot_offset(slot0: int) -> int:
    return SLOT_DATA_BASE + slot0 * SLOT_DATA_SIZE


def summary_offset(slot0: int) -> int:
    return SLOT_SUMMARY_BASE + slot0 * SLOT_SUMMARY_SIZE


def decode_slot(save: bytes, slot0: int) -> bytearray:
    base = slot_offset(slot0)
    payload = bytearray()
    for frame_no in range(FRAMES_PER_SLOT):
        off = base + frame_no * FRAME_SIZE
        frame = save[off:off + FRAME_SIZE]
        expected = struct.unpack_from("<H", frame, FRAME_PAYLOAD)[0]
        actual = crc16_xmodem(frame[:FRAME_PAYLOAD])
        if expected != actual:
            raise EditorError(f"slot {slot0 + 1} has an invalid frame CRC")
        payload += frame[:FRAME_PAYLOAD]
    return payload


def encode_slot(save: bytearray, slot0: int, payload: bytes) -> None:
    if len(payload) != DEFRAMED_SIZE:
        raise EditorError("internal slot size mismatch")
    base = slot_offset(slot0)
    for frame_no in range(FRAMES_PER_SLOT):
        src = frame_no * FRAME_PAYLOAD
        chunk = payload[src:src + FRAME_PAYLOAD]
        dst = base + frame_no * FRAME_SIZE
        save[dst:dst + FRAME_PAYLOAD] = chunk
        struct.pack_into("<H", save, dst + FRAME_PAYLOAD, crc16_xmodem(chunk))


def parse_u8(text: str) -> int:
    try:
        value = int(text, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer from 0 to 255") from exc
    if not 0 <= value <= 0xFF:
        raise argparse.ArgumentTypeError("value must be an integer from 0 to 255")
    return value


def parse_edit(text: str) -> tuple[int, str]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("use RECORD=STATE, e.g. 38=solved")
    left, right = text.split("=", 1)
    try:
        index = int(left, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("record index must be 0..43") from exc
    if not 0 <= index < RUMOR_COUNT:
        raise argparse.ArgumentTypeError("record index must be 0..43")
    state = right.strip().lower()
    if state not in {"clear", "acquired", "solved"}:
        raise argparse.ArgumentTypeError("state must be clear, acquired, or solved")
    return index, state


def apply_edit(state: bytearray, index: int, target: str) -> tuple[bytes, bytes]:
    off = index * RUMOR_SIZE
    old = bytes(state[off:off + RUMOR_SIZE])

    if target == "clear":
        new = CLEAR
    elif target == "acquired":
        new = ACQUIRED
    else:  # solved
        stage = old[3] if old[3] else 0x01
        new = bytes((0x0E, old[1], old[2], stage))

    state[off:off + RUMOR_SIZE] = new
    return old, new


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simplified Yuuyami MCD editor: input MCD -> edit slot -> output MCD."
    )
    parser.add_argument("input", type=Path, help="source raw 128 KiB .mcd")
    parser.add_argument("output", type=Path, help="output .mcd")
    parser.add_argument(
        "--slot", type=int, choices=range(1, SLOT_COUNT + 1), required=True,
        help="Yuuyami internal save slot (1..5)"
    )
    parser.add_argument(
        "--set", dest="edits", action="append", type=parse_edit, default=[],
        metavar="RECORD=STATE",
        help="set rumor record to clear, acquired, or solved; repeat as needed"
    )
    parser.add_argument(
        "--day", type=parse_u8,
        help="set saved day (verified field); also updates the slot summary day mirror"
    )
    parser.add_argument(
        "--affliction", type=parse_u8,
        help="EXPERIMENTAL: set observed affliction byte; arbitrary values are not fully understood"
    )
    args = parser.parse_args()

    if not args.edits and args.day is None and args.affliction is None:
        parser.error("specify at least one --set, --day, or --affliction edit")

    if not args.input.is_file():
        raise EditorError(f"input not found: {args.input}")

    card = bytearray(args.input.read_bytes())
    if len(card) != CARD_SIZE:
        raise EditorError(f"expected a raw 128 KiB .mcd, got {len(card)} bytes")

    chain = find_save_chain(card)
    save = extract_save(card, chain)
    slot0 = args.slot - 1
    payload = decode_slot(save, slot0)
    state = payload[:PERSISTENT_STATE_SIZE]

    print(f"Editing internal slot {args.slot}:")

    for index, target in args.edits:
        old, new = apply_edit(state, index, target)
        print(f"  record {index:02d}: {old.hex(' ').upper()} -> {new.hex(' ').upper()} ({target})")

    if args.day is not None:
        old = state[DAY_OFFSET]
        state[DAY_OFFSET] = args.day
        # Verified 8-byte slot summary mirrors the day as a little-endian u16 at +2.
        struct.pack_into("<H", save, summary_offset(slot0) + 2, args.day)
        print(f"  day: {old} -> {args.day}")

    if args.affliction is not None:
        old = state[AFFLICTION_OFFSET]
        state[AFFLICTION_OFFSET] = args.affliction
        print(f"  affliction [EXPERIMENTAL]: {old} -> {args.affliction}")

    payload[:PERSISTENT_STATE_SIZE] = state
    encode_slot(save, slot0, payload)
    install_save(card, chain, save)

    # Final verification before writing.
    verify_save = extract_save(card, find_save_chain(card))
    verify_payload = decode_slot(verify_save, slot0)
    if verify_payload[:PERSISTENT_STATE_SIZE] != bytes(state):
        raise EditorError("verification failed after rebuilding card")
    if args.day is not None:
        mirrored = struct.unpack_from("<H", verify_save, summary_offset(slot0) + 2)[0]
        if mirrored != args.day:
            raise EditorError("summary day mirror verification failed")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(card)
    print(f"Wrote: {args.output}")
    print("Verification: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EditorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
