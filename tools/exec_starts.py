#!/usr/bin/env python3
"""Executed instruction starts recorded by the MAME evidence lanes.

Every lane that tapped MAME recorded CURPC values: the PC of the instruction
whose bus cycle fired the tap.  A CURPC is the start of an instruction that
executed, wherever the accessed byte lay, so the union of all recorded
CURPCs is a set of addresses that must be instruction starts of code
sources.  (Accessed *bytes* are not: an in-window data read by an executing
instruction - a PC-relative table read - lands in the "executed" byte
intervals of the hole-play model; see docs/lanes/hole-play.md, "Caveat on
executed".  Only the PCs are collected here, never byte intervals.)

Sources (committed JSON under docs/lanes/):
  hole-play-evidence.json   ranges[].executed.executing_pcs, the consumer
                            PCs of read/flow_fetch, gate_writes[].pc
                            (writers), landmarks (CURPC == landmark)
  unibios-evidence.json     windows[].executed_noncode, executed_by_owner[]
                            first/last,
                            data_reads[] / flow_fetches[] consumer_pc,
                            noncode_executed[] span starts, runs[] card
                            command PCs
  modes-3-evidence.json,    pools.pcs (every consumer-PC set the interval
  round-complete-evidence   records point into)
  sym-install-evidence.json sites[S] with site_executed > 0 (S fetched) and
                            target T with target_pumped_in > 0 (the pump
                            jsr'd T)
BIOS PCs (>= 0xc00000) are dropped; P1 CPU windows only.

Usage: python3 tools/exec_starts.py --write   (rewrites config/executed_starts.tsv)
       python3 tools/exec_starts.py --check   (classify every start against
                                               build/p1-spans.json, print the
                                               non-code / misaligned ones)
       python3 tools/exec_starts.py --static  (the same for the static code
                                               entries: spawn lea targets,
                                               branch/call targets, install
                                               proofs; ~15 s, not a test)
"""
import argparse
import collections
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
LANES = REPO / 'docs' / 'lanes'
TABLE = REPO / 'config' / 'executed_starts.tsv'
CODE_CATEGORIES = ('function', 'block')


def p1_cpu(pc):
    return pc < 0x100000 or 0x200000 <= pc < 0x300000


def _hex(v):
    if isinstance(v, int):
        return v
    return int(str(v).split()[0], 16)


def collect(lanes=LANES):
    """{pc: set(lane tags)} of executed instruction starts."""
    out = collections.defaultdict(set)

    def add(pc, tag):
        pc = _hex(pc)
        if p1_cpu(pc):
            out[pc].add(tag)

    p = lanes / 'hole-play-evidence.json'
    if p.exists():
        d = json.loads(p.read_text())
        for r in d['ranges']:
            for pc in r.get('executed', {}).get('executing_pcs', []):
                add(pc, 'hole-play')
            for k in ('read', 'flow_fetch'):
                for pc in r.get(k, {}).get('consumer_pcs', []):
                    add(pc, 'hole-play')
        for writes in d['gate_writes'].values():
            for w in writes:
                add(w['pc'], 'hole-play')
        for pc in d['landmarks']:
            add(pc, 'hole-play')

    p = lanes / 'unibios-evidence.json'
    if p.exists():
        d = json.loads(p.read_text())
        for w in d['windows']:
            for pc in w.get('executed_noncode', []):
                add(pc, 'unibios')
            for e in w.get('executed_by_owner', []):
                for k in ('first', 'last'):
                    if e.get(k):
                        add(e[k], 'unibios')
            for k in ('data_reads', 'flow_fetches'):
                for e in w.get(k, []):
                    add(e['consumer_pc'], 'unibios')
        for x in d['noncode_executed']:
            for s, _e in x['executed_spans']:
                add(s, 'unibios')
        for run in d['runs'].values():
            for _frame, pc, _cmd in run.get('card_commands', []):
                add(pc, 'unibios')

    for lane in ('modes-3', 'round-complete'):
        p = lanes / f'{lane}-evidence.json'
        if p.exists():
            d = json.loads(p.read_text())
            for pcs in d['pools']['pcs']:
                for pc in pcs:
                    add(pc, lane)

    p = lanes / 'sym-install-evidence.json'
    if p.exists():
        d = json.loads(p.read_text())
        for site, s in d['sites'].items():
            if s['site_executed']:
                add(site, 'sym-install')
            if s['target_pumped_in']:
                add(s['target'], 'sym-install')
    return out


def write_table(starts, path=TABLE):
    lines = ['# Executed instruction starts recorded by the MAME evidence lanes',
             '# (tools/exec_starts.py --write; CURPC values only, never accessed',
             '# bytes).  tests/test_exec_starts.py fails when one of them is not an',
             '# instruction start of a function/block source.',
             '# <hex pc>\t<lanes>']
    for pc in sorted(starts):
        lines.append(f'{pc:06x}\t{",".join(sorted(starts[pc]))}')
    path.write_text('\n'.join(lines) + '\n')


def load_table(path=TABLE):
    out = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        pc, lanes = line.split('\t')
        out[int(pc, 16)] = lanes
    return out


def instruction_starts(sources, root=REPO):
    """{org: (set(instruction starts), set(dc starts), end, category, path)} for code sources."""
    sys.path.insert(0, str(REPO / 'tools'))
    import corpus
    out = {}
    for s in sources:
        if s['category'] not in CODE_CATEGORIES:
            continue
        path = root / s['source']
        lines, end, _syms = corpus.listing(path)
        insn, dc = set(), set()
        for _n, (addr, size, text) in lines.items():
            if size <= 0:
                continue
            (dc if text.strip().lower().startswith('dc.') else insn).add(addr)
        out[s['org']] = (insn, dc, s['org'] + s['size'], s['category'], s['source'])
    return out


def owners_of(pc, sources):
    return [s for s in sources if s['org'] <= pc < s['org'] + s['size']]


def classify(starts, spans_path=REPO / 'build' / 'p1-spans.json', root=REPO):
    """[(pc, problem, owners)] for every start that is not a code instruction start.

    problem: 'unknown' (no source owns the byte), 'non-code' (a data /
    extracted / fill / unref row or legacy data section owns it),
    'dc-word' (a code source owns it but encodes it as a dc word) or
    'mid-instruction' (inside a decoded instruction).
    """
    sources = json.loads(spans_path.read_text())['sources']
    owned = {pc: owners_of(pc, sources) for pc in starts}
    needed = {s['org']: s for os_ in owned.values() for s in os_}
    code = instruction_starts(needed.values(), root)
    problems = []
    for pc in sorted(starts):
        owners = owned[pc]
        if not owners:
            problems.append((pc, 'unknown', []))
            continue
        noncode = [s for s in owners if s['category'] not in CODE_CATEGORIES]
        if noncode:
            problems.append((pc, 'non-code', [f"{s['category']}:{s['source']}" for s in noncode]))
            continue
        ok = any(pc in code[s['org']][0] for s in owners)
        if not ok:
            on_dc = any(pc in code[s['org']][1] for s in owners)
            problems.append((pc, 'dc-word' if on_dc else 'mid-instruction',
                             [s['source'] for s in owners]))
    return problems


LEA_A1 = re.compile(r'^lea\s+\(\$([0-9a-f]+)(?:,pc)?\)(?:\.l)?,a1$', re.I)
JSR_SPAWN = re.compile(r'^jsr\s+\(\$0*4c6\)(?:\.l)?$', re.I)
FLOW = re.compile(r'^(jsr|jmp|bsr(?:\.[sw])?|bra(?:\.[sw])?'
                  r'|b(?:hi|ls|cc|cs|ne|eq|vc|vs|pl|mi|ge|lt|gt|le)(?:\.[sw])?'
                  r'|db\w+\s+d\d,)\s*\(?\$([0-9a-f]+)(?:,pc)?\)?(?:\.[lw])?$', re.I)


def static_targets(spans_path=REPO / 'build' / 'p1-spans.json'):
    """{target: set(tags)} of statically proven code entries in the corpus:
    actor spawns (lea X,a1 + jsr $4c6), direct jsr/jmp/bsr/branch targets and
    the install proofs (config/install_proofs.tsv)."""
    sys.path.insert(0, str(REPO / 'tools'))
    import corpus
    syms = corpus.load_symbols()
    out = collections.defaultdict(set)
    for s in json.loads(spans_path.read_text())['sources']:
        if s['category'] not in CODE_CATEGORIES:
            continue
        path = REPO / s['source']
        text = path.read_text().splitlines()
        lines, _end, _syms = corpus.listing(path)
        items = sorted((a, corpus.canonicalize(text[n - 1].split(';', 1)[0], syms).strip())
                       for n, (a, size, _t) in lines.items() if size > 0)
        for i, (a, code) in enumerate(items):
            m = LEA_A1.match(code)
            if m and i + 1 < len(items) and JSR_SPAWN.match(items[i + 1][1]):
                out[int(m.group(1), 16)].add(f'spawn@{a:x}')
            m = FLOW.match(code)
            if m and p1_cpu(int(m.group(2), 16)):
                out[int(m.group(2), 16)].add(f'{m.group(1).split(".")[0].split()[0]}@{a:x}')
    proofs = REPO / 'config' / 'install_proofs.tsv'
    for line in proofs.read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            site, target = line.split('\t')[:2]
            out[int(target, 16)].add(f'install@{site}')
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--static', action='store_true',
                    help='also classify the static spawn/branch/install targets')
    args = ap.parse_args()
    starts = collect()
    if args.write:
        write_table(starts)
        print(f'{len(starts)} executed starts -> {TABLE.relative_to(REPO)}')
    if args.check:
        probs = classify(load_table() if TABLE.exists() and not args.write else starts)
        for pc, kind, owners in probs:
            print(f'{pc:06x}\t{kind}\t{" ".join(owners)}')
        print(f'{len(probs)} problems')
    if args.static:
        targets = static_targets()
        probs = classify(targets)
        for pc, kind, owners in probs:
            print(f'{pc:06x}\t{kind}\t{" ".join(owners)}\t<- {" ".join(sorted(targets[pc])[:4])}')
        print(f'{len(targets)} static targets, {len(probs)} problems')


if __name__ == '__main__':
    main()
