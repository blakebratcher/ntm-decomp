#!/usr/bin/env python3
"""Aggregate lane hole-play MAME evidence into docs/lanes/hole-play-evidence.json.

Inputs: build/analysis/lane-holeplay/runs/<SCENARIO>/out/{access,landmarks,
writes,watch}.tsv + play.log + summary.txt, the target ranges from
build/analysis/lane-modes2-ranges.json (uncovered bytes + `unref` rows) and
the extra windows of tools/lane_holeplay_drive.py (code inside data rows).

Evidence model for a ROM byte at address A hit by the read tap, with the
CPU's CURPC values recorded at each hit (MAME's 68000 prefetches: the
fetch of a word happens while an EARLIER instruction executes):

  executed   some recorded PC p satisfies  range_start <= p <= A, i.e. an
             instruction INSIDE the same target range was executing while
             A was fetched: the range itself ran.  This is the strong form
             (the modes-2/3 lanes accepted any PC 1-10 bytes below A).
  prefetch   only PCs 1-10 bytes below A and outside the range: the
             instruction before the range was executing; a branch there
             may have skipped the range, so this proves nothing.
  read       a PC farther away: a data read by the instruction at that PC.

Usage:
  python3 tools/lane_holeplay_agg.py [--out PATH]
"""
import argparse
import json
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import lane_holeplay_drive as drive

REPO = pathlib.Path(__file__).resolve().parent.parent
RUNS = drive.RUNS
TARGETS = REPO / 'build' / 'analysis' / 'lane-modes2-ranges.json'
DOC_OUT = REPO / 'docs' / 'lanes' / 'hole-play-evidence.json'
NEAR = 10


def load_targets(path=TARGETS):
    """[(start, end, kind, name)] for uncovered ranges, unref rows and the
    extra (code-in-data) windows."""
    d = json.loads(pathlib.Path(path).read_text())
    out = []
    for r in d['uncovered_ranges']:
        out.append((r['start'], r['end'], 'uncovered', ''))
    for u in d['unref_rows']:
        out.append((u['start'], u['end'], 'unref', u['name']))
    for s, e in drive.EXTRA_WINDOWS:
        out.append((s, e, 'code_in_data', ''))
    return sorted(out)


def parse_access(path):
    """{addr: {'count', 'first', 'last', 'pcs': set}} from access.tsv."""
    rows = {}
    for line in pathlib.Path(path).read_text().splitlines()[1:]:
        p = line.split('\t')
        if len(p) < 6:
            continue
        a = int(p[1], 16)
        pcs = {int(x, 16) for x in p[5].split(',') if x}
        rows[a] = {'count': int(p[2]), 'first': int(p[3]), 'last': int(p[4]),
                   'pcs': pcs}
    return rows


def classify(addr, pcs, start, end):
    """'executed' / 'prefetch' / 'read' (see module doc)."""
    if any(start <= p <= addr for p in pcs):
        return 'executed'
    if all(0 < addr - p <= NEAR for p in pcs):
        return 'prefetch'
    return 'read'


def intervals(addrs, width=1):
    """Sorted address list -> [[start, end_exclusive], ...]; each address
    covers `width` bytes (the tap reports 16-bit bus words: width 2)."""
    out = []
    for a in sorted(addrs):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], a + width)
        else:
            out.append([a, a + width])
    return out


GHIDRA = REPO / 'build' / 'analysis' / 'p1-ghidra' / 'instructions.tsv'
FIXED = REPO / 'build' / 'work' / 'p1.fixed.cpu.bin'
FLOW = ('rts', 'rte', 'rtr', 'jmp', 'jsr', 'bra', 'bsr', 'b', 'db')
_insn_cache = {}


def insn_text(pc):
    """Instruction text at pc: the Ghidra export, else a one-instruction
    unidasm decode of the pristine fixed image ('' when unavailable)."""
    if not _insn_cache and GHIDRA.exists():
        for line in GHIDRA.read_text().splitlines()[1:]:
            p = line.split('\t')
            if len(p) >= 7:
                _insn_cache[int(p[0], 16)] = p[6]
        _insn_cache[-1] = ''
    if pc in _insn_cache:
        return _insn_cache[pc]
    text = ''
    try:
        import subprocess
        import tempfile
        blob = FIXED.read_bytes()[pc:pc + 10]
        with tempfile.NamedTemporaryFile(suffix='.bin') as f:
            f.write(blob)
            f.flush()
            r = subprocess.run(['unidasm', '-arch', 'm68000', '-basepc',
                                f'{pc:x}', f.name], capture_output=True,
                               text=True, timeout=10)
        first = r.stdout.splitlines()[0] if r.stdout else ''
        text = ' '.join(first.split(':', 1)[-1].split()[1:]) if first else ''
        # drop the hex words unidasm prints before the mnemonic
        parts = first.split()
        for i, tok in enumerate(parts[1:], 1):
            if not all(c in '0123456789abcdef' for c in tok.lower()):
                text = ' '.join(parts[i:])
                break
    except (OSError, IndexError, subprocess.SubprocessError):
        text = ''
    _insn_cache[pc] = text
    return text


def is_flow(text):
    m = text.split()[0].lower() if text.split() else ''
    m = m.split('.')[0]
    return m in ('rts', 'rte', 'rtr', 'jmp', 'jsr', 'bra', 'bsr') or (
        len(m) == 3 and m[0] == 'b') or m.startswith('db')


def range_evidence(target, runs):
    s, e, kind, name = target
    per = {'executed': defaultdict(set), 'prefetch': defaultdict(set),
           'read': defaultdict(set), 'flow_fetch': defaultdict(set)}
    read_pcs = defaultdict(set)
    exec_pcs = set()
    first = {}
    for scen, acc in runs.items():
        for a, ent in acc.items():
            if not s <= a < e:
                continue
            c = classify(a, ent['pcs'], s, e)
            if c == 'read':
                far = [p for p in ent['pcs'] if not 0 < a - p <= NEAR]
                if far and all(is_flow(insn_text(p)) for p in far):
                    c = 'flow_fetch'
            per[c][a].add(scen)
            if c in ('read', 'flow_fetch'):
                read_pcs[a] |= {p for p in ent['pcs'] if not 0 < a - p <= NEAR}
            if c == 'executed':
                exec_pcs |= {p for p in ent['pcs'] if s <= p < e}
            key = (c, scen)
            first[key] = min(first.get(key, 1 << 40), ent['first'])
    rec = {'start': f'{s:#08x}', 'end': f'{e:#08x}', 'bytes': e - s,
           'kind': kind}
    if name:
        rec['name'] = name
    for c in ('executed', 'prefetch', 'read', 'flow_fetch'):
        if not per[c]:
            continue
        scens = sorted({x for v in per[c].values() for x in v})
        ivs = [[max(a, s), min(b, e)] for a, b in intervals(per[c], 2)]
        ent = {'intervals': [f'{a:#x}-{b:#x}' for a, b in ivs],
               'bytes': sum(b - a for a, b in ivs), 'scenarios': scens,
               'first_frame': {sc: first[(c, sc)] for sc in scens}}
        if c in ('read', 'flow_fetch'):
            pcs = sorted({p for a2 in per[c] for p in read_pcs[a2]})
            ent['consumer_pcs'] = [f'{p:#x} {insn_text(p)}'.strip()
                                   for p in pcs[:24]]
        if c == 'executed':
            # instruction starts inside the range seen executing (CURPC)
            ent['executing_pcs'] = [f'{p:#x}' for p in sorted(exec_pcs)[:80]]
        rec[c] = ent
    touched = set()
    for c in per:
        for a in per[c]:
            touched |= {a, a + 1}
    rec['untouched_bytes'] = (e - s) - len({a for a in touched if s <= a < e})
    return rec


def parse_landmarks(path):
    out = {}
    for line in pathlib.Path(path).read_text().splitlines()[1:]:
        p = line.split('\t')
        if len(p) >= 4 and int(p[1]) > 0:
            out[p[0]] = {'count': int(p[1]), 'first': int(p[2])}
    return out


def parse_writes(path):
    """{addr: {(pc, new): [count, first_frame]}}"""
    out = defaultdict(dict)
    for line in pathlib.Path(path).read_text().splitlines()[1:]:
        p = line.split('\t')
        if len(p) < 6:
            continue
        frame, pc, addr, new = int(p[0]), p[2], p[3], p[5]
        ent = out[addr].setdefault((pc, new), [0, frame])
        ent[0] += 1
    return out


def parse_play(path):
    """Key events from play.log (bounded)."""
    ev = {'holes_done': 0, 'shots': 0, 'continues': 0, 'putts_holed': 0,
          'root_states': [], 'events': []}
    roots = []
    for line in pathlib.Path(path).read_text().splitlines():
        try:
            frame, msg = line.split('\t', 1)
        except ValueError:
            continue
        if msg.startswith('HOLE advance'):
            ev['holes_done'] += 1
            ev['events'].append(f'{frame} {msg}')
        elif msg.startswith('PLAY end'):
            for kv in msg.split()[2:]:
                k, _, v = kv.partition('=')
                if k in ('shots', 'continues'):
                    ev[k] = int(v)
            ev['events'].append(f'{frame} {msg}')
        elif msg.startswith('root '):
            new = msg.split()[-1]
            if new not in roots:
                roots.append(new)
        elif msg.startswith(('PLAYER_MOD', 'continue prompt', 'MARK')):
            ev['events'].append(f'{frame} {msg}')
        elif 'holed 1' in msg:
            ev['putts_holed'] += 1
    ev['root_states'] = roots
    if len(ev['events']) > 80:
        ev['events'] = ev['events'][:40] + ['...'] + ev['events'][-39:]
    return ev


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(DOC_OUT))
    ap.add_argument('--runs', default=str(RUNS))
    args = ap.parse_args(argv)
    runs_dir = pathlib.Path(args.runs)
    targets = load_targets()
    access, scen_doc = {}, {}
    landmarks = defaultdict(dict)
    writes = defaultdict(lambda: defaultdict(lambda: [0, 1 << 40, set()]))
    for d in sorted(runs_dir.iterdir()):
        out = d / 'out'
        if not (out / 'access.tsv').exists():
            continue
        name = d.name
        access[name] = parse_access(out / 'access.tsv')
        info = parse_play(out / 'play.log')
        summ = (out / 'summary.txt').read_text().split()
        info['frames'] = int(summ[0].split('=')[1])
        scen_doc[name] = info
        for a, ent in parse_landmarks(out / 'landmarks.tsv').items():
            landmarks[a][name] = ent['first']
        for addr, pairs in parse_writes(out / 'writes.tsv').items():
            for (pc, new), (cnt, fr) in pairs.items():
                w = writes[addr][f'{pc}:{new}']
                w[0] += cnt
                w[1] = min(w[1], fr)
                w[2].add(name)
    if not access:
        print('no runs under', runs_dir)
        return 1
    ranges = [range_evidence(t, access) for t in targets]
    gate_writes = {}
    for addr in sorted(writes):
        lst = []
        for key, (cnt, fr, sc) in sorted(writes[addr].items(),
                                         key=lambda kv: kv[1][1]):
            pc, new = key.split(':')
            lst.append({'pc': f'0x{pc}', 'value': f'0x{new}', 'writes': cnt,
                        'first_frame': fr, 'scenarios': len(sc)})
        gate_writes[f'0x{addr}'] = lst[:40]
    doc = {
        'lane': 'hole-play',
        'generated_by': 'tools/lane_holeplay_agg.py',
        'notes': [
            'All runs use inputs only (coin/start/joystick); the runner has no '
            'RAM-write step and the PLAY controller only reads RAM.',
            'executed = a CURPC inside the same target range was recorded at a '
            'fetch of the byte (the range itself ran); prefetch = only PCs '
            '1-10 bytes below the byte and outside the range (not evidence); '
            'read = a farther PC (a data read by that PC); flow_fetch = the '
            'farther PCs are all control transfers (rts/rte/jmp/jsr/bra/bsr/'
            'Bcc/DBcc): a jump-target or return prefetch, not a data read. '
            'The tap reports 16-bit bus words, so intervals are word-wide.',
            'Caveat on executed: a data read by an instruction inside the '
            'range (e.g. a PC-relative table read) also has its CURPC inside '
            'the range, so executed byte intervals can include in-range data '
            '(0x603b0-0x603c0 is a jump table read by 0x603aa). Block extents '
            'must come from the executing instruction starts plus a decode, '
            'never from the executed byte intervals.',
            'landmarks: CURPC == address at a fetch of address..address+15 '
            '(the instruction was executing); first frame per scenario.',
            'gate_writes: writers of the watched RAM bytes from power-on, '
            '(pc, value) pairs with counts; frame 1-278 entries are the BIOS '
            'RAM test.',
        ],
        'scenarios': scen_doc,
        'landmarks': {a: landmarks[a] for a in sorted(landmarks)},
        'gate_writes': gate_writes,
        'ranges': ranges,
    }
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + '\n')
    ex = [r for r in ranges if 'executed' in r]
    rd = [r for r in ranges if 'read' in r]
    print(f'wrote {out} ({out.stat().st_size} bytes), {len(access)} scenarios')
    print(f'ranges executed: {len(ex)}, read: {len(rd)} of {len(ranges)}')
    for r in ranges:
        tags = [c for c in ('executed', 'read', 'flow_fetch', 'prefetch')
                if c in r]
        if tags:
            print(f"  {r['start']}-{r['end']} {r['kind']:12s} "
                  + ' '.join(f"{c}={r[c]['bytes']}" for c in tags))
    return 0


if __name__ == '__main__':
    sys.exit(main())
