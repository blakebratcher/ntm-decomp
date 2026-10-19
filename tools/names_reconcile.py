#!/usr/bin/env python3
"""Resolve name collisions after merging a lane's name tables.

Precedence: the canonical hardware/BIOS tables (config/ram/hardware.tsv,
config/names/bios.tsv) beat everything; otherwise names already on main beat
the newly merged lane's. A losing row is removed from the lane's table and its
name is rewritten to the winner in that lane's source comments, so the corpus
speaks one vocabulary. Prints what it changed.

    tools/names_reconcile.py name-6        # lane tables config/{names,ram}/name-6.tsv
"""
import argparse
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
CANON = {'ram': 'hardware.tsv', 'names': 'bios.tsv'}


def rows(path, name_col):
    out = []
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        f = line.split('\t')
        out.append((int(f[0], 16), f[name_col].strip()))
    return out


def reconcile(kind, lane, root=REPO):
    name_col = 2 if kind == 'ram' else 1
    folder = root / 'config' / kind
    mine = folder / f'{lane}.tsv'
    canon = folder / CANON[kind]
    others = [p for p in sorted(folder.glob('*.tsv')) if p not in (mine, canon)]
    if kind == 'names':
        others.append(root / 'config' / 'names.tsv')
    by_addr, by_name = {}, {}
    for p in [canon] + others:          # canonical first: it wins every tie
        for addr, name in rows(p, name_col):
            by_addr.setdefault(addr, name)
            by_name.setdefault(name, addr)
    other = 'ram' if kind == 'names' else 'names'
    other_kind = {n for p in sorted((root / 'config' / other).glob('*.tsv'))
                  for _, n in rows(p, 2 if other == 'ram' else 1)}
    if other == 'names':
        other_kind |= {n for _, n in rows(root / 'config' / 'names.tsv', 1)}
    renames, kept, dropped = {}, [], []
    if not mine.exists():
        return renames, dropped
    for line in mine.read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            kept.append(line)
            continue
        f = line.split('\t')
        addr, name = int(f[0], 16), f[name_col].strip()
        if addr in by_addr and by_addr[addr] != name:
            if name not in by_name and name not in other_kind:
                # rewrite prose only when the losing name means nothing else
                renames[name] = by_addr[addr]
            dropped.append((addr, name, by_addr[addr]))
            continue
        if name in by_name and by_name[name] != addr:
            # same name, different address: suffix the row only; comment text
            # cannot be rewritten safely because the name is ambiguous there
            new = f'{name}_{addr:x}'
            f[name_col] = new
            dropped.append((addr, name, new))
            line = '\t'.join(f)
        if addr in by_addr:             # identical duplicate of a winner: drop the copy
            continue
        kept.append(line)
    mine.write_text('\n'.join(kept) + '\n')
    return renames, dropped


def rewrite_sources(renames, root=REPO):
    if not renames:
        return 0
    # plain words ('total') are too ambiguous to rewrite in prose
    renames = {old: new for old, new in renames.items() if '_' in old}
    if not renames:
        return 0
    pattern = re.compile(r'\b(' + '|'.join(map(re.escape, sorted(renames, key=len, reverse=True))) + r')\b')
    changed = 0
    for path in list((root / 'src' / 'p1' / 'asm').rglob('*.s')) + list((root / 'src' / 'm1' / 'asm').rglob('*.s')):
        text = path.read_text()
        # only comment text and label definitions change; instructions carry no names yet
        new = '\n'.join(pattern.sub(lambda m: renames[m.group(1)], line) if (';' in line or line.endswith(':')) else line
                        for line in text.split('\n'))
        if new != text:
            path.write_text(new)
            changed += 1
    for doc in (root / 'docs' / 'subsystems').glob('*.md'):
        text = doc.read_text()
        new = pattern.sub(lambda m: renames[m.group(1)], text)
        if new != text:
            doc.write_text(new)
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('lane')
    args = ap.parse_args()
    renames = {}
    for kind in ('names', 'ram'):
        r, dropped = reconcile(kind, args.lane)
        renames.update(r)
        for addr, old, new in dropped:
            print(f'  {kind}: {addr:#x} {old} -> {new}')
    print(f'{len(renames)} renames; {rewrite_sources(renames)} source files rewritten')


if __name__ == '__main__':
    main()
