#!/usr/bin/env python3
"""Assemble src/m1/asm functions and rebuild 200-m1.m1; byte-diff against the
pristine ROM. Same span-patching gate as tools/build_p1.py, but the M1 is a
flat 128 KiB image: the Z80 sees the first 32 KiB fixed at 0x0000-0x7fff and
the NEO-ZMC banks the rest into 0x8000-0xffff (16/8/4/2 KiB granularity -
see docs/memory-map.md), so build addresses are plain Z80 addresses
0x000000-0x01ffff.

The Z80 assembler is vasm's z80/std module (tools/setup_vasm.sh builds it).
"""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import gen_regions
from rom_coverage import gaps, size as union_size

REPO = pathlib.Path(__file__).resolve().parent.parent
VASM = REPO / 'tools' / 'vasm' / 'vasmz80_std'
SRC_DIR = REPO / 'src' / 'm1' / 'asm' / 'functions'
WORK = REPO / 'build' / 'work'
BLOCK_DIR = REPO / 'src' / 'm1' / 'asm' / 'blocks'
DATA_DIR = REPO / 'src' / 'm1' / 'data'
GEN_DIR = REPO / 'build' / 'gen' / 'm1' / 'regions'


def assemble(src, out_bin):
    result = subprocess.run(
        [str(VASM), '-Fbin', '-quiet', '-o', str(out_bin), str(src)],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f'vasm failed on {src.name}:\n{result.stderr}')


def org_of(src):
    for line in src.read_text().splitlines():
        m = re.match(r'\s*\.?org\s+(?:\$|0x)([0-9a-fA-F]+)', line)
        if m:
            return int(m.group(1), 16)
    raise SystemExit(f'{src}: missing org directive')


def category(src):
    """Classify a source for the span manifest: function, block, dead_code, data, fill or extracted."""
    if src.parent == GEN_DIR:
        kind = re.search(r'^; kind: (\S+)', src.read_text(), re.M).group(1)
        return ('fill' if kind.startswith('fill:') else
                'unreferenced' if kind == 'unref' else
                'dead_data' if kind == 'dead' else 'extracted')
    if '; kind: dead-insn' in src.read_text():
        return 'dead_code'   # proven unreachable: tools/reach.py, shape: tools/dead_code.py
    return 'data' if src.parent == DATA_DIR else 'block' if src.parent == BLOCK_DIR else 'function'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--check', action='store_true', help='fail on byte differences')
    ap.add_argument('--source-only', action='store_true',
                    help='reconstruct without pristine input; reject coverage gaps')
    ap.add_argument('--tmp', default=str(REPO / 'build' / 'tmp' / 'm1'))
    args = ap.parse_args()
    manifest = REPO / 'build/m1-spans.json'
    output = REPO / 'build/200-m1.m1'
    manifest.unlink(missing_ok=True)
    output.unlink(missing_ok=True)
    if not VASM.exists():
        raise SystemExit(f'{VASM} missing - run tools/setup_vasm.sh')
    pristine = bytes(0x20000) if args.source_only else (WORK / 'p1.m1.pristine.bin').read_bytes()
    if len(pristine) != 0x20000:
        raise SystemExit('M1 must contain exactly 131072 bytes')
    image = bytearray(pristine)
    tmp = pathlib.Path(args.tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    gen_regions.generate('m1', GEN_DIR, REPO, WORK)
    sources = (sorted(SRC_DIR.glob('*.s')) + sorted(BLOCK_DIR.glob('*.s'))
               + sorted(DATA_DIR.glob('*.s')) + sorted(GEN_DIR.glob('*.s')))
    if not sources:
        raise SystemExit('no M1 sources')
    records = []
    for src in sources:
        org = org_of(src)
        out_bin = tmp / (src.parent.name + '-' + src.stem + '.bin')
        assemble(src, out_bin)
        span = out_bin.read_bytes()
        if not span or org < 0 or org + len(span) > len(image):
            raise SystemExit(f'{src.name}: empty or out-of-window span')
        if args.source_only:
            for r in records:
                start, stop = max(org, r['org']), min(org+len(span), r['org']+r['size'])
                if start < stop and image[start:stop] != span[start-org:stop-org]:
                    raise SystemExit(f'{src.name}: conflicting source overlap at ${start:x}')
        elif span != pristine[org:org+len(span)]:
            bad = next(i for i, value in enumerate(span) if value != pristine[org+i])
            raise SystemExit(f'{src.name}: DIFF at +${bad:x}')
        image[org:org+len(span)] = span
        records.append({'source': str(src.relative_to(REPO)), 'org': org,
                        'size': len(span), 'category': category(src),
                        'sha256': hashlib.sha256(span).hexdigest()})
        print(f'  {src.name} ${org:x} +{len(span):5d} MATCH')
    intervals = [(r['org'], r['org']+r['size']) for r in records]
    missing = gaps(intervals, [(0, len(image))])
    if args.source_only and missing:
        raise SystemExit(f'source-only M1 incomplete: {sum(b-a for a,b in missing)} bytes '
                         f'in {len(missing)} gaps; first [${missing[0][0]:x}, ${missing[0][1]:x})')
    hashes = dict((row.split()[1], row.split()[0]) for row in
                  (REPO / 'config/rom.sha256').read_text().splitlines()
                  if row.strip() and not row.startswith('#'))
    got = hashlib.sha256(image).hexdigest()
    if got != hashes.get('200-m1.m1'):
        raise SystemExit('M1 hash does not match pinned ROM')
    covered = union_size(intervals)
    output.write_bytes(image)
    manifest.write_text(json.dumps({'rom_sha256': got, 'source_only': args.source_only,
                                   'unique_bytes': covered,
                                   'gross_bytes': sum(r['size'] for r in records),
                                   'sources': records}, indent=2) + '\n')
    print(f'M1 hash {got}; {len(sources)} sources, {covered} unique bytes ({100*covered/len(image):.3f}%)')
    if missing:
        print('Uncovered bytes remain pristine copies; this is not a source-only rebuild.')
    else:
        print('M1 source coverage complete.')


if __name__ == '__main__':
    main()
