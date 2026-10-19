#!/usr/bin/env python3
"""Whole-program reachability for the P1 68000 program (closed world).

Roots are the places the hardware and the BIOS enter the cartridge: the
long vectors in [0x0, 0x100) that land on instruction starts, and the
four header entry points at 0x122/0x128/0x12e/0x134 (USER, PLAYER_START,
DEMO_END, COIN_SOUND).  Flow follows fall-through (calls return), direct
branch/call targets, and, for each of the indirect `jmp (An)` / `jsr (An)`
sites, the complete target set recorded in config/indirect_targets.tsv
with its evidence.  An indirect site reached without a recorded target
set makes the closure open (reported, and the gate fails).

Runtime execution (config/executed_starts.tsv) is not a root here: it is
a cross-check.  Every executed start must be statically reachable, and
nothing proven dead may ever have executed.

Usage: python3 tools/reach.py [--json OUT] [--verbose]
"""
import argparse
import json
import pathlib
import re
import struct
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))
import dead_code  # noqa: E402

TARGETS = REPO / 'config' / 'indirect_targets.tsv'
EXEC = REPO / 'config' / 'executed_starts.tsv'
FIXED = REPO / 'build' / 'work' / 'p1.fixed.cpu.bin'
HEADER_ENTRIES = (0x122, 0x128, 0x12e, 0x134)

BRANCH = re.compile(r'^(b(?:ra|sr|hi|ls|cc|cs|ne|eq|vc|vs|pl|mi|ge|lt|gt|le)|db[a-z]+|jmp|jsr)\b')
NO_FALL = re.compile(r'^(rts|rte|rtr|bra|jmp|stop|illegal|reset)\b|^trap\s+#\$f$')
# trap #$f vectors (0xbc) to 0xe416, `move.b d0,$0.w; bra $e416`: a hang, no return
# any jmp/jsr whose EA involves a register: (An), (d16,An), (d8,An,Xn), (d8,PC,Xn).
# Each needs a row in config/indirect_targets.tsv or the closure stays open.
INDIRECT = re.compile(r'^(jmp|jsr)\s+\((?:\$[0-9a-f]+,)?(?:A[0-7]|PC,[AD][0-7])')


def corpus():
    """{addr: (size, text, source)} over every P1 code source (cached decode)."""
    out = {}
    starts = dead_code.code_starts('p1')
    for path, ss in starts.items():
        src = pathlib.Path(path)
        org, data = dead_code._assemble('p1', src)
        want = set(ss)        # vasm instruction lines; dc.w islands are not starts
        rel = str(src.relative_to(REPO))
        for a, size, text in dead_code.decode('p1', org, data):
            if a in want:
                out[a] = (size, text, rel)
        for a in sorted(want - set(out)):
            # the linear decode was desynchronised by a data island: decode alone
            rows = dead_code.decode('p1', a, data[a - org:a - org + 10])
            if rows and rows[0][0] == a:
                out[a] = (rows[0][1], rows[0][2], rel)
    return out


def _cached_corpus():
    cache = REPO / 'build' / 'analysis' / 'reach-corpus.json'
    import hashlib
    h = hashlib.sha1()
    for d in ('functions', 'blocks'):
        for p in sorted((REPO / 'src' / 'p1' / 'asm' / d).glob('*.s')):
            h.update(p.name.encode())
            h.update(p.read_bytes())
    key = h.hexdigest()
    if cache.exists():
        c = json.loads(cache.read_text())
        if c.get('key') == key:
            return {int(a): tuple(v) for a, v in c['insns'].items()}
    insns = corpus()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({'key': key, 'insns': {a: list(v) for a, v in insns.items()}}))
    return insns


def load_targets():
    """{site: (targets, evidence)} from config/indirect_targets.tsv."""
    out = {}
    if not TARGETS.exists():
        return out
    for n, line in enumerate(TARGETS.read_text().splitlines(), 1):
        if not line.strip() or line.startswith('#'):
            continue
        site, kind, targets, window, evidence = line.split('\t')[:5]
        vals = []
        for tok in (targets.split(',') if targets.strip() != '-' else []):
            if '@' in tok:          # target@writer: live only once the writer is reached
                t, w = tok.split('@')
                vals.append((int(t, 16), int(w, 16)))
            elif tok.strip():
                vals.append((int(tok, 16), None))
        out[int(site, 16)] = (kind, vals, window, evidence)
    return out


def roots(insns):
    """Header entries plus every vector in [0x0, 0x100) that points into the
    cartridge ROM; a cartridge vector that is not an instruction start is
    reported (roots.bad) instead of being dropped."""
    blob = FIXED.read_bytes()
    out = set(a for a in HEADER_ENTRIES if a in insns)
    roots.bad = [a for a in HEADER_ENTRIES if a not in insns]
    for off in range(4, 0x100, 4):          # 0x0 is the initial SSP, not code
        v = struct.unpack_from('>I', blob, off)[0] & 0xffffff
        if v in insns:
            out.add(v)
        elif 0x100 <= v < 0x100000:
            roots.bad.append(v)
    return out


COND = re.compile(r'^b(cc|cs|eq|ne|pl|mi|vc|vs|hi|ls|ge|lt|gt|le)\b')
COMPLEMENT = {'cc': 'cs', 'cs': 'cc', 'eq': 'ne', 'ne': 'eq', 'pl': 'mi', 'mi': 'pl',
              'vc': 'vs', 'vs': 'vc', 'hi': 'ls', 'ls': 'hi', 'ge': 'lt', 'lt': 'ge',
              'gt': 'le', 'le': 'gt'}
NEVER_FALLS = set()   # filled by complement_pairs() before a closure


def complement_pairs(insns, targets=()):
    """Second branches of `Bcc x; B!cc y` pairs: the two test the same flags, so
    one of them always branches and the second never falls through.  Only when
    nothing else can enter the second branch (no direct branch/call target,
    no recorded indirect target): then its only predecessor is the first."""
    entered = set()
    for a, (size, text, _) in insns.items():
        if BRANCH.match(text) and not INDIRECT.match(text):
            vals = re.findall(r'\$([0-9a-f]+)', text)
            if vals:
                entered.add(int(vals[-1], 16))
    for site, (_, vals, *_) in dict(targets).items():
        entered.update(t for t, _ in vals)
    out = set()
    for a, (size, text, _) in insns.items():
        first = COND.match(text)
        b = a + size
        second = COND.match(insns[b][1]) if b in insns else None
        if first and second and COMPLEMENT[first.group(1)] == second.group(1) and b not in entered:
            out.add(b)
    return out


def successors(addr, size, text):
    succ = []
    if BRANCH.match(text) and not INDIRECT.match(text):
        vals = re.findall(r'\$([0-9a-f]+)', text)
        if vals:
            succ.append(int(vals[-1], 16))
    if not NO_FALL.match(text) and addr not in NEVER_FALLS:
        succ.append(addr + size)
    return succ


def _raw(a):
    """Decode one instruction straight from the ROM (flow into a dc.w island)."""
    blob = FIXED.read_bytes() if not hasattr(_raw, 'blob') else _raw.blob
    _raw.blob = blob
    rows = dead_code.decode('p1', a, blob[a:a + 10])
    if rows and rows[0][0] == a and not re.search(r'illegal|invalid|dc\.w|opcode 1111', rows[0][2], re.I):
        return rows[0][1], rows[0][2]
    return None


def closure(insns, targets):
    NEVER_FALLS.clear()
    NEVER_FALLS.update(complement_pairs(insns, targets))
    seen, work = set(), list(roots(insns))
    open_sites, bad_edges, raw = set(), [], {}
    pending = {}   # indirect site reached -> its conditional targets not yet live
    while True:
        _drain(insns, targets, seen, work, open_sites, bad_edges, raw, pending)
        for site, conds in pending.items():
            for t, w in conds:
                if w in seen and t not in seen:
                    work.append(t)
        if not work:
            break
    closure.raw = raw
    return seen, open_sites, bad_edges


def _drain(insns, targets, seen, work, open_sites, bad_edges, raw, pending):
    while work:
        a = a0 = work.pop()
        if a in seen:
            continue
        if a not in insns:
            if not (a < 0x100000):
                if 0x200000 <= a < 0x300000:
                    bad_edges.append(a)
                continue
            r = _raw(a)
            if r is None:
                bad_edges.append(a)
                continue
            raw[a] = r
            size, text = r
        else:
            size, text, _ = insns[a]
        seen.add(a)
        nxt = successors(a, size, text)
        if INDIRECT.match(text):
            if a in targets:
                vals = targets[a][1]
                nxt += [t for t, w in vals if w is None or w in seen]
                pending[a] = [(t, w) for t, w in vals if w is not None]
            else:
                open_sites.add(a)
            if text.startswith('jsr'):
                nxt.append(a + size)
        work.extend(nxt)


PC_INDEX = re.compile(r'\((-?)\$([0-9a-f]+),PC,[AD][0-7]')


def operand_addresses(addr, text):
    """Addresses one instruction can name (low 24 bits): branch/call targets,
    absolute and (d16,PC) EAs as unidasm resolves them, (d8,PC,Xn) bases
    (unidasm prints the raw d8: base = extension word address + d8), and
    immediates that load an address register or a long (movea #, move.l #)."""
    out = []
    for m in PC_INDEX.finditer(text):
        if m.group(1):      # unidasm prints a negative d8 as -$xx
            out.append((addr + 2 - int(m.group(2), 16)) & 0xffffff)
            continue
        d8 = int(m.group(2), 16) & 0xff
        out.append((addr + 2 + (d8 - 0x100 if d8 & 0x80 else d8)) & 0xffffff)
    rest = PC_INDEX.sub('', text)
    for h in re.findall(r'(?<!#)\$([0-9a-f]+)', rest):
        out.append(int(h, 16) & 0xffffff)
    if re.match(r'^(movea\.[wl]|move\.l|lea|pea)\s+#', text):
        out += [int(h, 16) & 0xffffff for h in re.findall(r'#\$([0-9a-f]+)', text)]
    return out


def executed():
    if not EXEC.exists():
        return set()
    return {int(l.split('\t')[0], 16) for l in EXEC.read_text().splitlines()
            if l.strip() and not l.startswith('#')}


def report():
    insns = _cached_corpus()
    targets = load_targets()
    seen, open_sites, bad = closure(insns, targets)
    raw = sorted(closure.raw)
    by_src = {}
    for a, (size, text, src) in insns.items():
        by_src.setdefault(src, []).append(a)
    unreached = {s: sorted(v) for s, v in by_src.items() if not any(a in seen for a in v)}
    partial = {s: sorted(a for a in v if a not in seen) for s, v in by_src.items()
               if s not in unreached and any(a not in seen for a in v)}
    ran = executed()
    exec_unreached = sorted(a for a in ran if a in insns and a not in seen)
    stale = sorted(s for s in targets if s not in insns or not INDIRECT.match(insns[s][1]))
    return {'reached': len(seen), 'instructions': len(insns),
            'open_sites': sorted(open_sites), 'bad_edges': sorted(set(bad)),
            'unreached_sources': unreached, 'partial_sources': partial,
            'executed_but_unreached': exec_unreached, 'stale_target_rows': stale,
            'raw_reached': raw, 'bad_roots': sorted(set(roots.bad))}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--json')
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()
    r = report()
    print(f"reached {r['reached']:,} of {r['instructions']:,} instruction starts")
    print(f"open indirect sites (no target set): {len(r['open_sites'])} "
          + ' '.join(hex(a) for a in r['open_sites']))
    print(f"edges into unclassified/non-instruction ROM: {len(r['bad_edges'])} "
          + ' '.join(hex(a) for a in r['bad_edges'][:20]))
    print(f"executed in MAME but not reached: {len(r['executed_but_unreached'])} "
          + ' '.join(hex(a) for a in r['executed_but_unreached'][:20]))
    print(f"cartridge vectors that are not instruction starts: {r['bad_roots']}")
    print(f"reachable starts held as dc.w (representation fixes): {len(r['raw_reached'])} "
          + ' '.join(hex(a) for a in r['raw_reached'][:30]))
    print(f"sources with no reached instruction: {len(r['unreached_sources'])}")
    for s, v in sorted(r['unreached_sources'].items(), key=lambda x: x[1][0]):
        print(f'   {v[0]:06x} {s}')
    if args.verbose:
        print(f"sources partly reached: {len(r['partial_sources'])}")
        for s, v in sorted(r['partial_sources'].items(), key=lambda x: x[1][0]):
            print(f'   {s}: {len(v)} unreached starts from {v[0]:06x}')
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(r, indent=1))
    return r


if __name__ == '__main__':
    main()
