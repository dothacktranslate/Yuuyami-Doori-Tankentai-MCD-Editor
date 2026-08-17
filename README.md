Yuuyami Doori Tankentai — Simplified MCD Editor RC3
===================================================

This is the small testing-oriented version of the Yuuyami MCD editor.

It does only the core jobs:

1. Read a raw 128 KiB PlayStation MCD.
2. Select one Yuuyami internal save slot (1-5).
3. Optionally change one or more rumor records.
4. Optionally change the saved day.
5. Optionally change the observed affliction byte (experimental).
6. Recalculate the affected internal frame CRC-16/XMODEM values.
7. Verify the rebuilt slot and save a new MCD.

RUMOR STATES
------------

clear
    00 00 00 00

acquired
    03 00 00 01

solved
    Forces the verified successful-terminal flags byte 0E.
    Bytes 1 and 2 are preserved.
    Byte 3 is preserved if already nonzero; otherwise it is initialized to 01.

Examples:

    python yuuyami_mcd_editor_simple_rc3.py input.mcd output.mcd --slot 3 --set 38=solved

    python yuuyami_mcd_editor_simple_rc3.py input.mcd output.mcd --slot 3 \
        --set 0=solved --set 3=solved --set 38=acquired

DAY EDITING
-----------

Example:

    python yuuyami_mcd_editor_simple_rc3.py input.mcd output.mcd --slot 3 --day 97

Day accepts decimal or Python-style hexadecimal values from 0 to 255.

AFFLICTION EDITING — EXPERIMENTAL
---------------------------------

The affliction byte location is at persistent state offset 0x384.
However, the complete meaning of every possible value and
its safe range have not been reverse engineered.

For that reason, --affliction is intentionally labelled EXPERIMENTAL. It simply
writes the requested byte value and recalculates the save CRCs; it does not try
to validate the game's semantics.

Example:

    python yuuyami_mcd_editor_simple_rc3.py input.mcd output.mcd --slot 3 --affliction 2

You can combine everything in one command:

    python yuuyami_mcd_editor_simple_rc3.py input.mcd output.mcd --slot 3 \
        --set 38=solved --day 97 --affliction 2

NOT INCLUDED
------------

This simplified build intentionally omits research/transient rumor presets,
raw byte editing, slot cloning, slot-listing modes, in-place editing, and the other
advanced options from the larger research editor.

The goal is a small utility for making test MCDs quickly.
