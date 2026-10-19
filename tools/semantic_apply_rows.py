#!/usr/bin/env python3
"""Apply a semantic data lane's rows patch to config/regions.

Usage: tools/semantic_apply_rows.py [--write] PATCH.tsv [...]

Each patch line is a full replacement region row (`start size kind name
evidence`, tab separated). The row with the same start in
config/regions/<cpu>/*.tsv is replaced only when start, size, kind and name
are unchanged: patches correct evidence text, never extents, kinds or names
(those go through the byte gate and tools/rename_symbol.py). The CPU is
taken from the patch file name (`semantic-data-m1-*` is M1, otherwise P1).
Without --write it reports what would change.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def rows(cpu):
    found = {}
    for path in sorted((REPO / 'config' / 'regions' / cpu).glob('*.tsv')):
        for number, line in enumerate(path.read_text().splitlines()):
            if line.strip() and not line.lstrip().startswith('#'):
                found.setdefault(int(line.split('\t', 1)[0], 16), []).append((path, number, line))
    return found


def main(argv):
    write = '--write' in argv
    patches = [a for a in argv if a != '--write']
    if not patches:
        sys.exit(__doc__)
    edits = {}
    for patch in patches:
        cpu = 'm1' if pathlib.Path(patch).name.startswith('semantic-data-m1') else 'p1'
        current = rows(cpu)
        for line in pathlib.Path(patch).read_text().splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            fields = line.split('\t')
            if len(fields) != 5 or not fields[4].strip():
                raise SystemExit(f'{patch}: not a 5-field row: {line[:80]}')
            start = int(fields[0], 16)
            matches = current.get(start, [])
            if len(matches) != 1:
                raise SystemExit(f'{patch}: {len(matches)} rows start at {start:#x}')
            path, number, old = matches[0]
            if old.split('\t')[:4] != fields[:4]:
                raise SystemExit(f'{patch}: {start:#x} changes start/size/kind/name; '
                                 f'only evidence may change\n  old {old[:90]}\n  new {line[:90]}')
            if old != line:
                edits.setdefault(path, {})[number] = line
    for path, changes in sorted(edits.items()):
        lines = path.read_text().splitlines()
        for number, line in changes.items():
            lines[number] = line
        print(f'{path.relative_to(REPO)}: {len(changes)} evidence rewrites')
        if write:
            path.write_text('\n'.join(lines) + '\n')
    if not write:
        print('(dry run; --write to apply)')


if __name__ == '__main__':
    main(sys.argv[1:])
