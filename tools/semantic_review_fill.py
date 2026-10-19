#!/usr/bin/env python3
"""Bind draft semantic review records to the current sources.

Usage: tools/semantic_review_fill.py REVIEWS.json [...]

Each draft record needs `path`, `verdict` (correct / corrected / uncertain),
`report` and `evidence`. Code and legacy-data records name a source file;
region records also carry `row` (the region's hex start) and name the
config/regions TSV as `path`. The tool fills `cpu`, `address` and the
`sha256` that tools/semantic_inventory.py compares (the file for sources,
the TSV line for region rows), and sets `integrated: true` on corrections
unless the draft says `"integrated": false`. A record that replaces an
earlier review of the same source names that review's report in
`supersedes`. Run it after the last edit to
the reviewed sources: a later edit makes the record stale.
"""
import hashlib
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
ORG = re.compile(r'^\s*\.?org\s+(?:\$|0x)?([0-9a-fA-F]+)(?:h)?\s*$', re.MULTILINE)
VERDICTS = ('correct', 'corrected', 'uncertain')


def fill(record):
    for key in ('path', 'verdict', 'report', 'evidence'):
        if not record.get(key):
            raise ValueError(f'record without {key}: {record}')
    if record['verdict'] not in VERDICTS:
        raise ValueError(f'bad verdict {record["verdict"]!r} for {record["path"]}')
    path = REPO / record['path']
    parts = pathlib.Path(record['path']).parts
    cpu = parts[1] if parts[0] in ('src', 'config') and parts[1] in ('p1', 'm1') else \
        parts[2] if parts[:2] == ('config', 'regions') else None
    if cpu not in ('p1', 'm1'):
        raise ValueError(f'cannot tell the CPU of {record["path"]}')
    if 'row' in record:
        start = int(str(record['row']), 16)
        lines = [line for line in path.read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')
                 and int(line.split('\t', 1)[0], 16) == start]
        if len(lines) != 1:
            raise ValueError(f'{record["path"]}: {len(lines)} rows start at {start:#x}')
        digest = hashlib.sha256(lines[0].encode()).hexdigest()
    else:
        content = path.read_bytes()
        start = int(ORG.search(content.decode())[1], 16)
        digest = hashlib.sha256(content).hexdigest()
    filled = {'cpu': cpu, 'address': f'0x{start:06x}', 'path': record['path'],
              'sha256': digest, 'verdict': record['verdict'],
              'report': record['report'], 'evidence': record['evidence']}
    if 'row' in record:
        filled['row'] = record['row']  # kept so a record file can be refilled
    if record['verdict'] == 'corrected':
        filled['integrated'] = record.get('integrated', True)
    if record.get('supersedes'):
        filled['supersedes'] = record['supersedes']
    return filled


def main(paths):
    if not paths:
        sys.exit(__doc__)
    for name in paths:
        path = pathlib.Path(name)
        records = [fill(record) for record in json.loads(path.read_text())]
        keys = [(r['cpu'], r['path'], r['address']) for r in records]
        if len(set(keys)) != len(keys):
            raise ValueError(f'{name}: duplicate records')
        path.write_text(json.dumps(records, indent=1) + '\n')
        print(f'{name}: {len(records)} records bound')


if __name__ == '__main__':
    main(sys.argv[1:])
