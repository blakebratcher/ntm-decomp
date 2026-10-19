#!/usr/bin/env python3
"""Assemble src/p1/asm functions and rebuild 200-p1.p1; byte-diff against the pristine ROM.

Flow: tools/extract.py stages the pristine halves (build/work/p1.{fixed,bank}.cpu.bin)
in CPU byte order. Each source file carries 'org $ADDR' and is assembled with
vasm -Fbin, which emits exactly the span from the org address (the leading gap
is trimmed). The span is patched over the pristine bytes and compared. On
success the rebuilt ROM - in the MAME word-swapped file layout (bank half
first, fixed half second) - is written to build/p1.p1.

With --check, any byte mismatch aborts with exit 1.
"""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import gen_regions
import symbolize
from rom_coverage import gaps

REPO = pathlib.Path(__file__).resolve().parent.parent
VASM = REPO / 'tools' / 'vasm' / 'vasmm68k_mot'
SRC_DIR = REPO / 'src' / 'p1' / 'asm' / 'functions'
DATA_DIR = REPO / 'src' / 'p1' / 'data'
BLOCK_DIR = REPO / 'src' / 'p1' / 'asm' / 'blocks'
GEN_DIR = REPO / 'build' / 'gen' / 'p1' / 'regions'
INC_DIR = REPO / 'build' / 'gen' / 'p1'
WORK = REPO / 'build' / 'work'

FIXED_BASE = 0x000000
BANK_BASE = 0x200000


def union_size(intervals):
    """Count unique bytes, including nested and overlapping source spans."""
    total = 0
    end = -1
    for start, stop in sorted(intervals):
        total += max(0, stop - max(start, end))
        end = max(end, stop)
    return total


def span_window(org, size, pristine):
    for base, blob in pristine.items():
        if size > 0 and base <= org and org + size <= base + len(blob):
            return base
    raise ValueError(f'${org:x} +{size}: empty span or outside a P-ROM window')


def load_map():
    return json.loads((REPO / 'config' / 'map.json').read_text())


def assemble(src, out_bin):
    result = subprocess.run(
        [str(VASM), '-Fbin', '-no-opt', '-quiet', f'-I{INC_DIR}',
         '-o', str(out_bin), str(src)],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f'vasm failed on {src.name}:\n{result.stderr}')


def org_of(src):
    for line in src.read_text().splitlines():
        m = re.match(r'\s*org\s+\$([0-9a-fA-F]+)', line)
        if m:
            return int(m.group(1), 16)
    raise SystemExit(f'{src}: missing org directive')


def word_swap(data):
    out = bytearray(len(data))
    out[0::2], out[1::2] = data[1::2], data[0::2]
    return bytes(out)


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


DEAD_RANGE = re.compile(r'^; dead-range: \[\$([0-9a-fA-F]+), \$([0-9a-fA-F]+)\)', re.M)


def dead_ranges(src):
    """Declared unreachable ranges inside a live source (`; dead-range: [$a, $b) why`).

    tests/test_reach.py checks each against the tools/reach.py closure; the
    completion report counts those bytes as dead_code instead of the source's
    own category."""
    return [[int(a, 16), int(b, 16)] for a, b in DEAD_RANGE.findall(src.read_text())]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--check', action='store_true',
                    help='fail (exit 1) on any byte mismatch')
    ap.add_argument('--source-only', action='store_true',
                    help='reconstruct without pristine program inputs; fail on coverage gaps')
    ap.add_argument('--tmp', default=str(REPO / 'build' / 'tmp' / 'p1'),
                    help='scratch dir for per-function binaries')
    args = ap.parse_args()

    manifest = REPO / 'build' / 'p1-spans.json'
    manifest.unlink(missing_ok=True)  # a failed build must not retain stale proof
    output = REPO / 'build' / 'p1.p1'
    output.unlink(missing_ok=True)

    if not VASM.exists():
        raise SystemExit(f'{VASM} missing - run tools/setup_vasm.sh (or make setup)')

    pristine = ({FIXED_BASE: bytes(0x100000), BANK_BASE: bytes(0x100000)}
                if args.source_only else {
                    FIXED_BASE: (WORK / 'p1.fixed.cpu.bin').read_bytes(),
                    BANK_BASE: (WORK / 'p1.bank.cpu.bin').read_bytes(),
                })
    images = {base: bytearray(blob) for base, blob in pristine.items()}

    tmp = pathlib.Path(args.tmp)
    tmp.mkdir(parents=True, exist_ok=True)

    gen_regions.generate('p1', GEN_DIR, REPO, WORK)
    # global equates include for symbolized sources (tools/symbolize.py)
    symbolize.generate_include(REPO, [SRC_DIR, BLOCK_DIR],
                               INC_DIR / symbolize.INCLUDE_NAME)
    sources = (sorted(SRC_DIR.glob('*.s')) + sorted(DATA_DIR.glob('*.s'))
               + sorted(BLOCK_DIR.glob('*.s')) + sorted(GEN_DIR.glob('*.s')))
    if not sources:
        raise SystemExit('no sources under src/p1/asm/functions or '
                         'src/p1/data - run make extract to generate the data '
                         'sections and tools/ghidra_import.py for code')

    covered = 0
    covered_code = 0
    covered_data = 0
    failed = 0
    records = []
    for src in sources:
        org = org_of(src)
        out_bin = tmp / (src.parent.name + '-' + src.stem + '.bin')
        assemble(src, out_bin)
        span = out_bin.read_bytes()
        if not span:
            raise SystemExit(f'{src.name}: assembled to zero bytes - truncated source?')
        try:
            base = span_window(org, len(span), pristine)
        except ValueError as exc:
            raise SystemExit(f'{src.name}: {exc}') from exc
        offset = org - base
        expect = span if args.source_only else pristine[base][offset:offset + len(span)]
        if args.source_only:
            for record in records:
                start = max(org, record['org'])
                stop = min(org + len(span), record['org'] + record['size'])
                if start < stop and images[base][start-base:stop-base] != span[start-org:stop-org]:
                    raise SystemExit(f'{src.name}: conflicting source overlap at ${start:x}')
        if span == expect:
            covered += len(span)
            if src.parent in (DATA_DIR, GEN_DIR):
                covered_data += len(span)
            else:
                covered_code += len(span)
            print(f'  {src.name} ${org:x} +{len(span):5d} MATCH')
            record = {'source': str(src.relative_to(REPO)),
                      'org': org, 'size': len(span),
                      'category': category(src),
                      'sha256': hashlib.sha256(span).hexdigest()}
            dead = dead_ranges(src)
            if dead:
                record['dead'] = dead
            records.append(record)
        else:
            failed += 1
            bad = next(i for i in range(min(len(span), len(expect)))
                       if span[i] != expect[i])
            print(f'  {src.name} ${org:x} +{len(span):5d} DIFF at +${bad:x} '
                  f'got {span[bad]:02x} want {expect[bad]:02x}')
            if args.check:
                sys.exit(1)
        images[base][offset:offset + len(span)] = span

    rebuilt = word_swap(images[BANK_BASE]) + word_swap(images[FIXED_BASE])
    missing = gaps(((r['org'], r['org'] + r['size']) for r in records),
                   [(0, 0x100000), (BANK_BASE, BANK_BASE + 0x100000)])
    if args.source_only and missing:
        count = sum(end - start for start, end in missing)
        raise SystemExit(f'source-only P1 incomplete: {count} bytes in {len(missing)} gaps; '
                         f'first [${missing[0][0]:x}, ${missing[0][1]:x})')

    want = None
    for line in (REPO / 'config' / 'rom.sha256').read_text().splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == '200-p1.p1':
            want = fields[0]
    if want is None:
        raise SystemExit('missing 200-p1.p1 entry in config/rom.sha256')
    got = hashlib.sha256(rebuilt).hexdigest()
    unique = union_size((r['org'], r['org'] + r['size']) for r in records)

    print(f'built build/p1.p1  sha256 {got}')
    print(f'  expected         {want}')
    print(f'  assembled {len(sources)} sources: {covered_code} code bytes + '
          f'{covered_data} data bytes = {covered} gross bytes')
    print(f'  {unique} unique verified bytes '
          f'({unique * 100 / 0x200000:.3f}% of P-ROM); '
          f'{covered - unique} overlapping bytes')
    if failed:
        print(f'  {failed} function(s) DIFFER - fix the converter or source and re-run')
        sys.exit(1)
    if got != want:
        raise SystemExit('rebuilt P1 SHA-256 does not match the pinned ROM')
    output.write_bytes(rebuilt)
    manifest.write_text(json.dumps({'rom_sha256': got, 'source_only': args.source_only,
                                   'unique_bytes': unique,
                                   'gross_bytes': covered, 'sources': records},
                                  indent=2) + '\n')
    if unique == 0x200000:
        print('  P1 fully byte-exact: complete rebuild achieved.')
    else:
        print('  all covered bytes match the pristine ROM.')
        print('  (full-file sha256 also matches because unpatched regions are '
              'pristine copies; it only proves the rebuild at 100% coverage.)')


if __name__ == '__main__':
    main()
