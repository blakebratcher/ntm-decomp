#!/usr/bin/env python3
"""Assert the MAME acceptance milestones recorded by tools/mame_accept.lua.

Usage: tools/mame_accept.py RESULT_TSV

The file holds one "milestone<TAB>frame" line per milestone. All four must be
present in boot order; anything less fails (exit 1).
"""
import pathlib
import sys

ORDER = ('game_entry', 'title_phase', 'attract', 'shot_fired')


def check(text):
    """Return (ok, frames, notes); frames maps milestone name -> frame."""
    frames = {}
    bad = []
    for line in text.splitlines():
        parts = line.strip().split('\t')
        if len(parts) != 2 or parts[0] == 'milestone':
            continue
        try:
            frames[parts[0]] = int(parts[1])
        except ValueError:
            bad.append(line)
    notes = []
    missing = [m for m in ORDER if m not in frames]
    if missing:
        notes.append('missing: ' + ', '.join(missing))
    seq = [frames[m] for m in ORDER if m in frames]
    if seq != sorted(seq):
        notes.append('out of boot order: '
                     + ', '.join(f'{m}={frames[m]}' for m in ORDER
                                 if m in frames))
    if bad:
        notes.append('unparseable lines: ' + '; '.join(bad))
    return not notes, frames, notes


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = pathlib.Path(argv[0] if argv else 'result.tsv')
    ok, frames, notes = check(path.read_text())
    for name in ORDER:
        if name in frames:
            print(f'  {name:11s} frame {frames[name]}')
    if ok:
        print(f'MAME acceptance: PASS ({path})')
        return 0
    for note in notes:
        print(f'  FAIL {note}')
    print(f'MAME acceptance: FAIL ({path})')
    return 1


if __name__ == '__main__':
    sys.exit(main())
