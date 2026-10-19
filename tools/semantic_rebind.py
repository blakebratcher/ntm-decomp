#!/usr/bin/env python3
"""Re-bind semantic review digests after a mechanical rename batch.

Usage: tools/semantic_rebind.py [--write] --rev REV --renames PAIRS.tsv [...]
                                [--subst SUBST.tsv ...] REVIEWS.json [...]

A rename batch (tools/rename_symbol.py --file PAIRS.tsv) rewrites names as
whole words across sources and region rows, which makes every review of a
touched file stale although nothing reviewed changed. For each stale record
this tool takes the file (or region row) as it was at REV, requires its
digest to equal the record's digest, applies the batch's OLD -> NEW
whole-word substitutions to that old text and requires the result to equal
the current text. Only then is the record's digest replaced. Anything else
stays stale and is listed: it needs a real re-review.

--subst names files of literal `OLD<TAB>NEW` (or `FILE<TAB>OLD<TAB>NEW`,
limited to one repository path; `\\n` in a field is a newline) replacements
applied before the renames: operand rebases such as `old_row+$30` -> `new_row`, made when a
region row's start moved, and recorded in the batch with their reason.
"""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]


def pairs(paths):
    """OLD NEW renames; `! NAME` unlabels become (NAME, None), `- NAME` drops
    (NAME, False): only their entry-marker removal is replayed."""
    out = []
    for path in paths:
        for line in pathlib.Path(path).read_text().splitlines():
            fields = line.split()
            if len(fields) == 2 and not line.startswith('#') and fields[0] not in '-!':
                out.append((fields[0], fields[1]))
            elif len(fields) == 2 and fields[0] == '!':
                out.append((fields[1], None))
            elif len(fields) == 2 and fields[0] == '-':
                out.append((fields[1], False))
    return out


def literals(paths):
    """[(file or None, OLD, NEW)]: `OLD<TAB>NEW` applies to every file,
    `FILE<TAB>OLD<TAB>NEW` only to that repository path."""
    out = []
    for path in paths:
        for line in pathlib.Path(path).read_text().splitlines():
            if line.strip() and not line.startswith('#'):
                fields = [f.replace('\\n', '\n') for f in line.split('\t')]
                out.append((None, *fields[:2]) if len(fields) == 2 else tuple(fields[:3]))
    return out


def substitute(text, renames, subst=(), path=None):
    for where, old, new in subst:
        if where is None or where == path:
            text = text.replace(old, new)
    for old, new in renames:
        if new is False:    # drop: rename_symbol removes the `; NAME:` entry marker
            text = re.sub(r'^; ' + re.escape(old) + r':\s*\n', '', text, flags=re.M)
            continue
        if new is None:     # unlabel, as tools/rename_symbol.py does it
            text = re.sub(r'^(?:; )?' + re.escape(old) + r':[ \t]*\n', '', text, flags=re.M)
            continue
        text = re.sub(rf'(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])', new, text)
    return text


def old_text(rev, path):
    return subprocess.run(['git', 'show', f'{rev}:{path}'], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout


def row_line(text, start):
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith('#')
             and int(l.split('\t', 1)[0], 16) == start]
    return lines[0] if len(lines) == 1 else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--rev', required=True)
    ap.add_argument('--renames', action='append', default=[])
    ap.add_argument('--subst', action='append', default=[])
    ap.add_argument('reviews', nargs='+')
    args = ap.parse_args()
    renames = pairs(args.renames)
    subst = literals(args.subst)
    for name in args.reviews:
        content = pathlib.Path(name).read_text()
        block = None
        if name.endswith('.md'):    # a report holding one JSON array block
            found = re.findall(r'^```json\s*\n(.*?)\n```\s*$', content, re.M | re.S)
            if len(found) != 1:
                continue
            block = found[0]
            records = json.loads(block)
        else:
            records = json.loads(content)
        rebound, refused = 0, []
        for record in records:
            if 'sha256' not in record:      # draft record, not bound yet
                continue
            path = record['path']
            current = (REPO / path).read_text()
            is_row = path.startswith('config/regions/')
            if is_row:
                current = row_line(current, int(record['address'], 16))
            if current is not None and hashlib.sha256(current.encode()).hexdigest() == record['sha256']:
                continue
            before = old_text(args.rev, path)
            if is_row:
                before = row_line(before, int(record['address'], 16))
            if (before is None or current is None
                    or hashlib.sha256(before.encode()).hexdigest() != record['sha256']
                    or substitute(before, renames, subst, path) != current):
                refused.append(path)
                continue
            record['sha256'] = hashlib.sha256(current.encode()).hexdigest()
            rebound += 1
        print(f'{name}: {rebound} rebound after renames, {len(refused)} still stale'
              + ''.join(f'\n   {p}' for p in refused))
        if args.write and rebound:
            if block is None:
                pathlib.Path(name).write_text(json.dumps(records, indent=1) + '\n')
            else:
                new = json.dumps(records, indent=1)
                pathlib.Path(name).write_text(content.replace(block, new, 1))


if __name__ == '__main__':
    main()
