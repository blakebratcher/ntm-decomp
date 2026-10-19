#!/usr/bin/env python3
"""Inventory semantic review obligations without inferring understanding.

`semantic_decompilation_complete` is true only when every obligation is
`reviewed` or an accepted residual, AND docs/lanes/semantic-acceptance.json
pins exactly this state (the digest of every entry's source digest, status
and report). A count alone never sets it: the coordinator writes the
acceptance file with `--accept REPORT` after the reviews have been
independently verified, and any later edit or re-review changes the digest
and clears the flag until it is accepted again. An accepted residual is an
`uncertain` record whose open question is a whole-program reachability
question, not a question about what the code does; `--accept` lists every
uncertain record with its question (`--residual PATH` names each one
deliberately), and the report keeps them as `uncertain` in the counts and
in `semantic_residuals`.
"""
import argparse
import collections
import hashlib
import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
ORG = re.compile(r'^\s*\.?org\s+(?:\$|0x)?([0-9a-fA-F]+)(?:h)?\s*$', re.MULTILINE)


def code_inventory(root):
    rows = []
    for cpu in ('p1', 'm1'):
        for kind in ('functions', 'blocks'):
            for path in sorted((root / 'src' / cpu / 'asm' / kind).glob('*.s')):
                content = path.read_bytes()
                match = ORG.search(content.decode())
                if match is None:
                    raise ValueError(f'missing org: {path}')
                rows.append({'cpu': cpu, 'address': f'0x{int(match[1], 16):06x}',
                             'path': str(path.relative_to(root)), 'kind': 'code',
                             'sha256': hashlib.sha256(content).hexdigest(),
                             'status': 'unreviewed'})
    return rows


def data_inventory(root):
    rows = []
    for cpu in ('p1', 'm1'):
        for path in sorted((root / 'config' / 'regions' / cpu).glob('*.tsv')):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                fields = line.split('\t', 4)
                if len(fields) != 5:
                    raise ValueError(f'invalid region row: {path}:{number}')
                address, size, kind, name, evidence = fields
                rows.append({'cpu': cpu, 'address': f'0x{int(address, 16):06x}',
                             'size': int(size, 16), 'kind': kind, 'name': name,
                             'path': str(path.relative_to(root)),
                             'sha256': hashlib.sha256(line.encode()).hexdigest(),
                             'status': 'unreviewed', 'classification_evidence': evidence})
        for path in sorted((root / 'src' / cpu / 'data').glob('*.s')):
            content = path.read_bytes()
            match = ORG.search(content.decode())
            if match is None:
                raise ValueError(f'missing org: {path}')
            rows.append({'cpu': cpu, 'address': f'0x{int(match[1], 16):06x}',
                         'path': str(path.relative_to(root)), 'kind': 'legacy-data',
                         'sha256': hashlib.sha256(content).hexdigest(),
                         'status': 'unreviewed'})
    return rows


def chain_head(key, group):
    """The newest review of one obligation. A later review replaces an earlier
    one only by naming its report in `supersedes`; replacements may chain
    (A <- B <- C). The group must be exactly one chain: distinct reports,
    every `supersedes` names a report of the group, no report is superseded
    twice, one head, and walking back from the head visits every record."""
    by_report = {}
    for review in group:
        report = review.get('report')
        if report in by_report:
            raise ValueError(f'duplicate review: {key} ({report})')
        by_report[report] = review
    # A `supersedes` naming a report outside the group (a historical ledger
    # whose row was never bound to this path) is allowed only on the chain's
    # root, the oldest record; anywhere else the chain would be broken.
    targets = [r['supersedes'] for r in group
               if r.get('supersedes') and r['supersedes'] in by_report]
    dangling = [r for r in group if r.get('supersedes') and r['supersedes'] not in by_report]
    if len(dangling) > 1:
        raise ValueError(f'dangling supersedes in {key}: {[r["supersedes"] for r in dangling]}')
    if len(set(targets)) != len(targets):
        raise ValueError(f'duplicate review: {key} (a report is superseded twice)')
    heads = [r for r in group if r.get('report') not in set(targets)]
    if len(heads) != 1:
        raise ValueError(f'duplicate review: {key} ({len(heads)} unsuperseded records)')
    seen, review = set(), heads[0]
    while review is not None and review.get('report') not in seen:
        seen.add(review.get('report'))
        review = by_report.get(review['supersedes']) if review.get('supersedes') else None
    if review is not None or len(seen) != len(group):
        raise ValueError(f'duplicate review: {key} (records not on one chain)')
    root = [r for r in group if not r.get('supersedes') or r['supersedes'] not in by_report]
    if dangling and root != dangling:
        raise ValueError(f'dangling supersedes in {key} off the chain root')
    return heads[0]


def apply_reviews(rows, reviews):
    groups = collections.defaultdict(list)
    for review in reviews:
        groups[review['cpu'], review['path'], review['address']].append(review)
    indexed = {}
    for key, group in groups.items():
        indexed[key] = chain_head(key, group)
    results = []
    for row in rows:
        result = dict(row)
        review = indexed.pop((row['cpu'], row['path'], row['address']), None)
        if review is not None:
            result['report'] = review.get('report', '')
            result['evidence'] = review.get('evidence', '')
            verdict = review.get('verdict')
            result['verdict'] = verdict
            if not result['report'] or not result['evidence'] or verdict not in (
                    'correct', 'corrected', 'uncertain'):
                result['status'] = 'invalid'
            elif review.get('sha256') != row['sha256']:
                result['status'] = 'stale'
            elif verdict == 'uncertain':
                result['status'] = 'uncertain'
            elif verdict == 'corrected' and review.get('integrated') is not True:
                result['status'] = 'correction-pending'
            else:
                result['status'] = 'reviewed'
        results.append(result)
    if indexed:
        raise ValueError(f'reviews reference missing inventory entries: {list(indexed)[:3]}')
    return results


def load_reviews(path):
    content = path.read_text()
    if path.suffix == '.md':
        blocks = re.findall(r'^```json\s*\n(.*?)\n```\s*$', content, re.MULTILINE | re.DOTALL)
        if len(blocks) != 1:
            raise ValueError(f'{path}: expected one JSON array of review records')
        content = blocks[0]
    rows = json.loads(content)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f'{path}: expected one JSON array of review records')
    return rows


def lane_review_files(root):
    """Every docs/lanes/semantic-*-reviews.json plus the Markdown reports that
    carry their review records as one JSON array block."""
    lanes = root / 'docs' / 'lanes'
    files = sorted(lanes.glob('semantic-*-reviews.json'))
    for path in sorted(lanes.glob('semantic-*.md')):
        blocks = re.findall(r'^```json\s*\n(.*?)\n```\s*$', path.read_text(), re.MULTILINE | re.DOTALL)
        if len(blocks) == 1:
            try:
                rows = json.loads(blocks[0])
            except ValueError:
                continue
            if isinstance(rows, list) and rows and all('sha256' in r for r in rows):
                files.append(path)
    return files


ACCEPTANCE = pathlib.Path('docs') / 'lanes' / 'semantic-acceptance.json'


def state_digest(rows):
    """Digest of the reviewed state: every entry with its source digest and
    the status, verdict, report and evidence of the review that binds it
    (so an accepted residual's question cannot change unnoticed)."""
    h = hashlib.sha256()
    for row in sorted(rows, key=key_of):
        h.update(json.dumps([row['cpu'], row['path'], row['address'], row['sha256'],
                             row['status'], row.get('verdict', ''), row.get('report', ''),
                             row.get('evidence', '')]).encode() + b'\n')
    return h.hexdigest()


def key_of(row):
    return row['cpu'], row['path'], row['address']


def residual_entry(row):
    return {'cpu': row['cpu'], 'path': row['path'], 'address': row['address'],
            'report': row.get('report', ''), 'question': row.get('evidence', '')}


def file_digest(root, path):
    target = root / path
    return hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None


def pinned_reports(root, rows, verification=()):
    """sha256 of every report a current review cites, and of the named
    independent verification reports (a missing file pins as None)."""
    paths = sorted({r['report'] for r in rows if r.get('report')} | set(verification))
    return {path: file_digest(root, path) for path in paths}


def acceptance_record(root, rows, report, verification=()):
    """The acceptance file content for the current state: every uncertain
    obligation becomes a listed residual; the cited and verification
    reports are pinned by digest."""
    return {'report': report, 'report_sha256': file_digest(root, report),
            'verification': sorted(verification),
            'reports': pinned_reports(root, rows, verification),
            'inventory_entries': len(rows), 'state_digest': state_digest(rows),
            'residuals': sorted((residual_entry(r) for r in rows if r['status'] == 'uncertain'),
                                key=lambda e: (e['cpu'], e['path'], e['address']))}


def completion(root, rows):
    """(complete, reason) for the acceptance-gated completion flag. The
    acceptance file must match the current tree exactly: the state digest,
    the entry count, the residual list (each current uncertain obligation
    once, with its current report and question, nothing else), and the
    sha256 of the acceptance report it names."""
    path = root / ACCEPTANCE
    accepted = json.loads(path.read_text()) if path.exists() else {}
    listed = accepted.get('residuals', [])
    residuals = {(r.get('cpu'), r.get('path'), r.get('address')) for r in listed}
    open_rows = [r for r in rows if r['status'] != 'reviewed'
                 and not (r['status'] == 'uncertain' and key_of(r) in residuals)]
    if open_rows:
        return False, f'{len(open_rows)} obligations are not reviewed or accepted residuals'
    if not path.exists():
        return False, f'every obligation is reviewed but {ACCEPTANCE} is missing'
    expected = sorted((residual_entry(r) for r in rows if r['status'] == 'uncertain'),
                      key=lambda e: (e['cpu'], e['path'], e['address']))
    checks = [
        (accepted.get('state_digest') == state_digest(rows), 'pins a different reviewed state'),
        (accepted.get('inventory_entries') == len(rows), 'names a different obligation count'),
        (listed == expected, 'residual list differs from the current uncertain records'),
        (bool(accepted.get('report')) and
         re.fullmatch(r'[0-9a-f]{64}', str(accepted.get('report_sha256'))) is not None and
         accepted.get('report_sha256') == file_digest(root, accepted.get('report', '')),
         'acceptance report is missing or changed'),
        (bool(accepted.get('verification')), 'names no independent verification report'),
        (accepted.get('reports') == pinned_reports(root, rows, accepted.get('verification', []))
         and None not in accepted.get('reports', {None: None}).values(),
         'cited or verification reports are missing or changed'),
        (set(accepted) == {'report', 'report_sha256', 'verification', 'reports',
                           'inventory_entries', 'state_digest', 'residuals'},
         'has missing or unexpected fields'),
    ]
    for ok, why in checks:
        if not ok:
            return False, f'{ACCEPTANCE} {why} (re-verify, then --accept)'
    n = len(listed)
    return True, (f'accepted by {accepted["report"]}'
                  + (f' with {n} accepted residual(s), still uncertain' if n else ''))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=pathlib.Path, default=REPO)
    parser.add_argument('--reviews', type=pathlib.Path, action='append', default=[],
                        help='JSON array or Markdown report containing one JSON array; repeatable')
    parser.add_argument('--lanes', action='store_true',
                        help='also load every lane review file under docs/lanes (see lane_review_files)')
    parser.add_argument('--out', type=pathlib.Path)
    parser.add_argument('--residual', action='append', default=[], metavar='PATH[@ADDRESS]',
                        help='with --accept: an uncertain obligation accepted as a residual '
                             '(repeatable; region rows need @0xADDRESS)')
    parser.add_argument('--verification', action='append', default=[], metavar='REPORT',
                        help='with --accept: an independent verification report (repeatable, required)')
    parser.add_argument('--accept', metavar='REPORT',
                        help='write the acceptance file pinning the current state (only when every '
                             'obligation is reviewed); REPORT names the verification record')
    args = parser.parse_args()
    paths = list(args.reviews) + (lane_review_files(args.root) if args.lanes else [])
    reviews = [review for path in paths for review in load_reviews(path)]
    rows = apply_reviews(code_inventory(args.root) + data_inventory(args.root), reviews)
    counts = dict(collections.Counter(row['status'] for row in rows))
    if args.accept:
        uncertain = {key_of(row): row for row in rows if row['status'] == 'uncertain'}
        named = set()
        for spec in args.residual:
            path, _, address = spec.partition('@')
            hits = [k for k in uncertain if k[1] == path and (not address or
                    int(k[2], 16) == int(address, 16))]
            if len(hits) != 1:
                raise SystemExit(f'--residual {spec}: matches {len(hits)} uncertain obligations')
            named.add(hits[0])
        others = [row for row in rows if row['status'] not in ('reviewed', 'uncertain')]
        if others or named != set(uncertain):
            raise SystemExit(f'cannot accept: {counts}; every uncertain obligation must be '
                             f'named exactly once by --residual: {sorted(uncertain)}')
        missing = [r for r in [args.accept] + args.verification if file_digest(args.root, r) is None]
        if missing or not args.verification:
            raise SystemExit(f'--accept needs existing report files and at least one '
                             f'--verification report (missing: {missing})')
        (args.root / ACCEPTANCE).write_text(
            json.dumps(acceptance_record(args.root, rows, args.accept, args.verification),
                       indent=1) + '\n')
    complete, reason = completion(args.root, rows)
    report = {'inventory_entries': len(rows), 'counts': counts,
              'semantic_decompilation_complete': complete,
              'completion_reason': reason,
              'semantic_residuals': [{'path': r['path'], 'address': r['address'],
                                      'report': r.get('report', '')}
                                     for r in rows if r['status'] == 'uncertain'],
              'qualification': 'Review records are evidence pointers, not automated semantic proof. '
                               'Historical Markdown ledgers require explicit reconciliation.',
              'entries': rows}
    output = args.out or args.root / 'build' / 'analysis' / 'semantic-inventory.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(f'{len(rows)} semantic obligations: {counts}')
    print(f'Wrote {output}; semantic_decompilation_complete={complete} ({reason})')


if __name__ == '__main__':
    main()
