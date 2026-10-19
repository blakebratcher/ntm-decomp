#!/usr/bin/env python3
"""Fresh byte gates plus an explicit reconstruction-completeness report.

This gate cannot certify semantic understanding. Unknown byte ranges fail;
complete source coverage still requires a separate code/data/semantics review.
"""
import json
import pathlib
import subprocess
import sys
from rom_coverage import gaps, size
import semantic_inventory

REPO = pathlib.Path(__file__).resolve().parent.parent


def cross_overlaps(sources):
    """Pairs of sources of different categories that claim the same bytes."""
    spans = sorted((r['org'], r['org'] + r['size'], r['category'], r['source']) for r in sources)
    found = []
    for i, (a0, a1, ac, an) in enumerate(spans):
        for b0, b1, bc, bn in spans[i + 1:]:
            if b0 >= a1:
                break
            if ac != bc:
                found.append({'start': f'0x{b0:06x}', 'end': f'0x{min(a1, b1):06x}',
                              'categories': sorted([ac, bc]), 'sources': [an, bn]})
    return found


def main():
    results = {}
    for cpu, windows in [('p1', [(0, 0x100000), (0x200000, 0x300000)]),
                         ('m1', [(0, 0x20000)])]:
        run = subprocess.run([sys.executable, str(REPO / f'tools/build_{cpu}.py'), '--check'],
                             capture_output=True, text=True)
        if run.returncode:
            raise SystemExit(run.stdout + run.stderr)
        report = json.loads((REPO / f'build/{cpu}-spans.json').read_text())
        missing = gaps([(r['org'], r['org'] + r['size']) for r in report['sources']], windows)
        by_category = {}
        for r in report['sources']:
            lo, hi = r['org'], r['org'] + r['size']
            for a, b in sorted(r.get('dead', [])):
                # declared dead ranges inside a live source count as dead code
                by_category.setdefault('dead_code', []).append((a, b))
                if lo < a:
                    by_category.setdefault(r['category'], []).append((lo, a))
                lo = b
            if lo < hi:
                by_category.setdefault(r['category'], []).append((lo, hi))
        overlaps = cross_overlaps(report['sources'])
        bad = [o for o in overlaps if 'fill' in o['categories']]
        if bad:
            raise SystemExit(f'{cpu}: fill overlaps other sources (a constant run cannot also be '
                             f'code or a table): {bad[:3]}')
        results[cpu] = {'unique_bytes': report['unique_bytes'],
                        'cross_category_overlaps': overlaps,
                        'unique_bytes_by_category': {k: size(v) for k, v in sorted(by_category.items())},
                        'uncovered_bytes': sum(end - start for start, end in missing),
                        'uncovered_ranges': [[f'0x{start:06x}', f'0x{end:06x}'] for start, end in missing]}
        print(f"{cpu.upper()}: {report['unique_bytes']:,} verified bytes; "
              f"{results[cpu]['uncovered_bytes']:,} uncovered in {len(missing)} ranges")
        print('    ' + ', '.join(f'{k} {v:,}' for k, v in results[cpu]['unique_bytes_by_category'].items()))
    # open evidence items: classified bytes whose role is still unproven
    # (`unref` rows: format known, reader not found and unreadness not proven)
    open_items = sum(r['unique_bytes_by_category'].get('unreferenced', 0) for r in results.values())
    rows = semantic_inventory.apply_reviews(
        semantic_inventory.code_inventory(REPO) + semantic_inventory.data_inventory(REPO),
        [r for path in semantic_inventory.lane_review_files(REPO)
         for r in semantic_inventory.load_reviews(path)])
    semantic_complete, semantic_reason = semantic_inventory.completion(REPO, rows)
    out = REPO / 'build/analysis/completion.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'source_coverage_complete': all(r['uncovered_bytes'] == 0 for r in results.values()),
                               'open_evidence_bytes': open_items,
                               'semantic_decompilation_complete': semantic_complete,
                               'semantic_completion_reason': semantic_reason,
                               'roms': results}, indent=2) + '\n')
    print(f'Open evidence items: {open_items:,} bytes (unref rows); dead code/data are proven '
          f'(tools/reach.py, tests/test_reach.py).')
    if any(r['uncovered_bytes'] for r in results.values()):
        raise SystemExit('INCOMPLETE: see build/analysis/completion.json')
    print('Source coverage complete.')
    print(f'Semantic decompilation complete: {semantic_complete} ({semantic_reason}; '
          f'tools/semantic_inventory.py --lanes).')


if __name__ == '__main__':
    main()
