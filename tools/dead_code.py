#!/usr/bin/env python3
"""Shape proof for proven-dead code sources (`kind: dead-insn`).

A source under src/<cpu>/asm/{functions,blocks}/ whose header says
`; kind: dead-insn` is code that can never execute: tools/reach.py's
closed-world closure (P1) never reaches it, and tests/test_reach.py
enforces that.  This module proves the other half, that the bytes really
are a self-consistent routine (not data dressed as code).

A source may carry that kind only when every check below passes:

1. clean decode: unidasm decodes the assembled bytes as contiguous
   instructions with no illegal/invalid words, ending exactly at the span
   end;
2. control flow: every branch/call target lands on an instruction start of
   the source itself or of another code source (vasm listings);
3. exit: the last instruction is an unconditional transfer, or the span end
   is an instruction start of another code source (fall-through);
4. operands: every absolute ROM operand lands in classified bytes, every
   other absolute address lies in mapped address space;
5. not executed: no recorded MAME instruction start lies inside it (an
   executed source has a root and must not be filed as unref code);
6. header: a `; Root evidence: dead:` line says why nothing reaches it.

Usage: python3 tools/dead_code.py [--verbose]   (exit 1 on any failure)
"""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))

KIND = '; kind: dead-insn'
CACHE = REPO / 'build' / 'analysis' / 'insn-starts.json'
EXEC_STARTS = REPO / 'config' / 'executed_starts.tsv'

CPUS = {
    'p1': {'arch': 'm68000', 'dirs': ('functions', 'blocks')},
    'm1': {'arch': 'z80', 'dirs': ('functions', 'blocks')},
}

P1_UNCOND = re.compile(r'^(rts|rte|rtr|bra|jmp)\b')
P1_BRANCH = re.compile(r'^(b[a-z]{2}|bra|bsr|db[a-z]+|jsr|jmp)\b')
M1_UNCOND = re.compile(r'^(ret|reti|retn|jp|jr)\b(?!\s+(nz|z|nc|c|po|pe|p|m)\b)')
M1_BRANCH = re.compile(r'^(jp|jr|call|djnz|rst)\b')
HEX = re.compile(r'\$([0-9a-fA-F]+)')


def dead_sources(cpu):
    out = []
    for d in CPUS[cpu]['dirs']:
        out += [p for p in sorted((REPO / 'src' / cpu / 'asm' / d).glob('*.s')) if KIND in p.read_text()]
    return out


def is_dead(src):
    return KIND in pathlib.Path(src).read_text()


def _assemble(cpu, src):
    if cpu == 'p1':
        import corpus
        return corpus.org_of(src.read_text()), corpus.assemble(src)
    import build_m1
    with tempfile.TemporaryDirectory(prefix='ntm-dead-') as td:
        out = pathlib.Path(td) / 'o.bin'
        build_m1.assemble(src, out)
        return build_m1.org_of(src), out.read_bytes()


def decode(cpu, org, data):
    """[(addr, size, text)] from unidasm, sizes from the raw byte column."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(data)
        name = f.name
    try:
        out = subprocess.run(['unidasm', name, '-arch', CPUS[cpu]['arch'], '-basepc', hex(org)],
                             capture_output=True, text=True, check=True).stdout
    finally:
        pathlib.Path(name).unlink()
    rows = []
    for line in out.splitlines():
        m = re.match(r'^\s*([0-9a-fA-F]+):\s+((?:[0-9a-fA-F]{2,4} )+)\s*(.*)$', line)
        if not m:
            continue
        raw = m.group(2).split()
        size = sum(len(w) // 2 for w in raw)
        rows.append((int(m.group(1), 16), size, m.group(3).strip()))
    return rows


def _listing_starts(cpu, src):
    """Instruction starts of one source (lines that are not dc/ds/include)."""
    if cpu == 'p1':
        import corpus
        lines, _, _ = corpus.listing(src)
        return sorted(a for a, size, text in lines.values()
                      if size and not re.match(r'\s*(dc|ds|dcb)\.', text.strip(), re.I))
    org, data = _assemble(cpu, src)
    return [a for a, _, text in decode(cpu, org, data)]


def code_starts(cpu):
    """{source path: [instruction starts]} for every code source (cached)."""
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    out, dirty = {}, False
    for d in CPUS[cpu]['dirs']:
        for src in sorted((REPO / 'src' / cpu / 'asm' / d).glob('*.s')):
            key = f'{cpu}:{src.relative_to(REPO)}'
            digest = hashlib.sha1(src.read_bytes()).hexdigest()
            hit = cache.get(key)
            if not hit or hit['sha1'] != digest:
                hit = {'sha1': digest, 'starts': _listing_starts(cpu, src)}
                cache[key] = hit
                dirty = True
            out[str(src)] = hit['starts']
    if dirty:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache))
    return out


def classified_spans(cpu):
    report = json.loads((REPO / 'build' / f'{cpu}-spans.json').read_text())
    return sorted((r['org'], r['org'] + r['size']) for r in report['sources'])


def _covered(spans, addr):
    import bisect
    i = bisect.bisect_right(spans, (addr, float('inf'))) - 1
    return i >= 0 and spans[i][0] <= addr < spans[i][1]


def executed(cpu):
    if cpu != 'p1' or not EXEC_STARTS.exists():
        return set()
    return {int(l.split('\t')[0], 16) for l in EXEC_STARTS.read_text().splitlines()
            if l.strip() and not l.startswith('#')}


def mapped(cpu, addr):
    if cpu == 'm1':
        return 0 <= addr <= 0xffff
    a = addr & 0xffffff
    return (a < 0x100000 or 0x100000 <= a < 0x110000 or 0x200000 <= a < 0x300000
            or 0x300000 <= a < 0x400000 or 0x400000 <= a < 0x402000
            or 0xc00000 <= a < 0xc20000 or 0xd00000 <= a < 0xd10000)


def rom(cpu, addr):
    if cpu == 'm1':
        return addr < 0x8000
    a = addr & 0xffffff
    return a < 0x100000 or 0x200000 <= a < 0x300000


def targets(cpu, text):
    """Branch/call target of one instruction, or None."""
    if cpu == 'p1':
        if not P1_BRANCH.match(text) or re.search(r'\(a[0-7]', text, re.I):
            return None
        m = HEX.findall(text)
        return int(m[-1], 16) if m else None
    if not M1_BRANCH.match(text) or '(' in text:
        return None
    m = HEX.findall(text)
    return int(m[-1], 16) if m else None


def operands(cpu, text):
    """Absolute addresses used as memory operands (not immediates)."""
    if cpu == 'p1':
        out = [int(h, 16) for h in re.findall(r'(?<![#\w])\$([0-9a-fA-F]+)\.[lw]', text)]
        out += [int(h, 16) for h in re.findall(r'\(\$([0-9a-fA-F]+),PC', text)]
        return out
    return [int(h, 16) for h in re.findall(r'\(\$([0-9a-fA-F]+)\)', text)]


def check(cpu, src, starts=None, spans=None, ran=None):
    src = pathlib.Path(src)
    text = src.read_text()
    problems, notes = [], []
    if not re.search(r'^; Root evidence: dead:', text, re.M):
        problems.append('header lacks "; Root evidence: dead:"')
    org, data = _assemble(cpu, src)
    end = org + len(data)
    rows = decode(cpu, org, data)
    own = {a for a, _, _ in rows}
    pos = org
    for a, size, t in rows:
        if a != pos:
            problems.append(f'{a:#x}: decode not contiguous (expected {pos:#x})')
            break
        if re.search(r'illegal|invalid|dc\.w|opcode 1111|\?\?\?', t, re.I):
            problems.append(f'{a:#x}: undecodable word ({t})')
        pos = a + size
    if pos != end:
        problems.append(f'decode ends at {pos:#x}, span ends at {end:#x}')
    starts = starts if starts is not None else code_starts(cpu)
    others = set()
    for path, ss in starts.items():
        if path != str(src):
            others.update(ss)
    spans = spans if spans is not None else classified_spans(cpu)
    for a, size, t in rows:
        tgt = targets(cpu, t)
        if tgt is not None:
            if org <= tgt < end:
                if tgt not in own:
                    problems.append(f'{a:#x}: branch into mid-instruction {tgt:#x}')
            elif tgt not in others:
                problems.append(f'{a:#x}: target {tgt:#x} is not a code instruction start')
        for op in operands(cpu, t):
            if not mapped(cpu, op):
                problems.append(f'{a:#x}: operand {op:#x} outside mapped space')
            elif rom(cpu, op) and not (org <= op < end) and not _covered(spans, op):
                problems.append(f'{a:#x}: ROM operand {op:#x} in unclassified bytes')
            elif not rom(cpu, op):
                notes.append(f'{op:#x}')
    if rows:
        last = rows[-1][2]
        uncond = (P1_UNCOND if cpu == 'p1' else M1_UNCOND).match(last)
        if not uncond and end not in others:
            problems.append(f'ends with {last!r}: neither an unconditional transfer nor a fall-through into code')
    ran = ran if ran is not None else executed(cpu)
    hit = sorted(a for a in ran if org <= a < end)
    if hit:
        problems.append(f'executed in MAME at {", ".join(hex(a) for a in hit[:4])}: it is not dead')
    shown = src.relative_to(REPO) if src.resolve().is_relative_to(REPO) else src
    return {'source': str(shown), 'org': org, 'end': end,
            'insns': len(rows), 'problems': problems, 'ram': sorted(set(notes))}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('paths', nargs='*', help='check these sources (default: every dead-insn source)')
    args = ap.parse_args()
    bad = 0
    for cpu in CPUS:
        srcs = [pathlib.Path(p) for p in args.paths if f'/{cpu}/' in str(pathlib.Path(p).resolve())] \
            if args.paths else dead_sources(cpu)
        if not srcs:
            continue
        starts, spans, ran = code_starts(cpu), classified_spans(cpu), executed(cpu)
        for src in srcs:
            r = check(cpu, src, starts, spans, ran)
            status = 'FAIL' if r['problems'] else 'ok'
            print(f"{cpu} {r['source']} [{r['org']:#x},{r['end']:#x}) {r['insns']} insns: {status}")
            for p in r['problems']:
                print('   -', p)
            if args.verbose and r['ram']:
                print('   ram/hw:', ' '.join(r['ram']))
            bad += bool(r['problems'])
    if bad:
        raise SystemExit(f'{bad} dead-insn source(s) fail the shape proof')
    return 0


if __name__ == '__main__':
    main()
