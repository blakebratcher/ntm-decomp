#!/usr/bin/env python3
"""Origin census: was NTM's 68000 program hand-written assembly or compiled?

Two modes, one metric set (see docs/ORIGIN.md for the verdict):

  --ntm (default)
      Parse the verified byte-exact .s corpus (src/p1/asm/functions and
      src/p1/asm/blocks, extents from build/p1-spans.json) and print the
      authorship metrics.  Parsing .s text (not ROM bytes) means inline
      dc.* data stays visible and branch encodings (.s/.w suffixes) are
      exact.

  --roms DIR [--titles a,b]
      Linear-sweep decode other Neo Geo sets' `-p1` members with unidasm
      (word-swapped, the MAME file layout), sanity-gate the decode, split
      it into RTS-delimited segments and run the same metrics.  Limitation:
      a linear sweep has no symbol boundaries and mis-decodes embedded
      data; segment statistics are comparative, not authoritative.  Nothing
      is decrypted - encrypted P-ROMs fail the sanity gate and are flagged.

JSON evidence lands under build/analysis/lane-origin/<name>.json.
"""
import argparse
import collections
import json
import pathlib
import re
import subprocess
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lane_origin_m68k import (parse_source_line, parse_unidasm_line,
                              effects, operand_roles, base_of, ea_class,
                              DREGS, AREGS, REGS)

REPO = pathlib.Path(__file__).resolve().parent.parent
FUNC_DIR = REPO / 'src' / 'p1' / 'asm' / 'functions'
BLOCK_DIR = REPO / 'src' / 'p1' / 'asm' / 'blocks'
SPANS = REPO / 'build' / 'p1-spans.json'
WORK = REPO / 'build' / 'work'
FIXED_BIN = WORK / 'p1.fixed.cpu.bin'
BANK_BIN = WORK / 'p1.bank.cpu.bin'
DECODE_CACHE = REPO / 'build' / 'analysis' / 'lane-origin' / 'ntm-decode.json'
OUT_DIR = REPO / 'build' / 'analysis' / 'lane-origin'

BRANCH = {'bra', 'bsr', 'bhi', 'bls', 'bcc', 'bcs', 'bne', 'beq', 'bvc',
          'bvs', 'bpl', 'bmi', 'bge', 'blt', 'bgt', 'ble'}
COND = BRANCH - {'bra', 'bsr'}
STOP = {'rts', 'rte', 'rtr'}
SCC = {'scc', 'seq', 'sne', 'sge', 'slt', 'sgt', 'sle', 'shi', 'sls',
       'spl', 'smi', 'svc', 'svs', 'sf'}
MEMOPS = ('move', 'movea', 'lea', 'pea', 'add', 'sub', 'and', 'or', 'eor',
          'cmp', 'cmpa', 'cmpi', 'tst', 'addq', 'subq', 'adda', 'suba',
          'addx', 'subx', 'abcd', 'sbcd', 'movem', 'clr', 'neg', 'negx',
          'not', 'nbcd', 'mulu', 'muls', 'divu', 'divs', 'chk', 'tas',
          'btst', 'bchg', 'bclr', 'bset', 'asl', 'asr', 'lsl', 'lsr',
          'rol', 'ror', 'roxl', 'roxr')
MEMBER = re.compile(r'-(p[0-9]|pg[0-9]|ph[0-9]|ep[0-9])\.[a-z0-9]+$', re.I)
TARGET = re.compile(r'^\(?\$([0-9a-f]{1,8})\)?(\.\w)?$', re.I)
DBCC = {'dbra', 'dbf', 'dbt', 'dbeq', 'dbne', 'dbcc', 'dbcs', 'dbmi',
        'dbpl', 'dbge', 'dblt', 'dbgt', 'dble', 'dbhi', 'dbls', 'dbvc',
        'dbvs'}


def load_spans():
    """Verified per-source extents: source path -> (org, size, category)."""
    manifest = json.loads(SPANS.read_text())
    return {s['source']: (s['org'], s['size'], s['category'])
            for s in manifest['sources']}


def parse_corpus(spans):
    """All .s sources -> {addr: function dict} for functions and blocks."""
    funcs = {}
    for srcdir in (FUNC_DIR, BLOCK_DIR):
        for src in sorted(srcdir.glob('*.s')):
            rel = str(src.relative_to(REPO))
            if rel not in spans:
                continue
            org, size, category = spans[rel]
            insns, directives = [], []
            for line in src.read_text().splitlines():
                ev = parse_source_line(line)
                if not ev:
                    continue
                if ev[0] == 'insn':
                    mnem, ops, sz = ev[1], ev[2], ev[3]
                    reads, writes, flags = effects(mnem, ops)
                    insns.append(dict(mnem=mnem, ops=ops, size=sz,
                                      reads=reads, writes=writes,
                                      flags=flags))
                elif ev[0] == 'directive':
                    directives.append((ev[1], ev[2] or ''))
            funcs[org] = dict(addr=org, size=size, category=category,
                              source=rel, insns=insns,
                              directives=directives)
    return funcs


def analyze_insns(insns, dc_count=0):
    """Per-function metric dict over a decoded instruction list."""
    m = collections.Counter()
    m['n'] = len(insns)
    m['dc_count'] = dc_count
    if not insns:
        return m
    first_mention, last_mention = {}, {}
    for i, ins in enumerate(insns):
        for r in ins['reads'] | ins['writes']:
            if r in REGS and r not in first_mention:
                first_mention[r] = ('r' if r in ins['reads'] else 'w', i)
        for r in ins['reads'] | ins['writes']:
            if r in REGS:
                last_mention[r] = ('r' if r in ins['reads'] else 'w', i)
        mnem = ins['mnem']
        if mnem in STOP:
            m['stop'] += 1
        if mnem == 'link':
            m['link'] += 1
        if mnem == 'unlk':
            m['unlk'] += 1
        if mnem == 'rtd':
            m['rtd'] += 1
        if mnem in ('jsr', 'bsr'):
            m['calls'] += 1
            if mnem == 'jsr' and ins['ops'] and ea_class(
                    ins['ops'][0]) in ('abs', 'abs_l'):
                m['jsr_abs'] += 1
            if mnem == 'jsr' and ins['ops'] and ea_class(
                    ins['ops'][0]) in ('pcd', 'pci'):
                m['jsr_pc'] += 1
            if mnem == 'jsr' and ins['ops'] and ea_class(
                    ins['ops'][0]) in ('aind', 'aind_idx', 'pci'):
                m['computed_call'] += 1
            if mnem == 'bsr':
                m['bsr'] += 1
        if mnem in BRANCH:
            m['br_' + ins['size'] if ins['size'] else 'br_nosize'] += 1
        if mnem in SCC:
            m['scc'] += 1
        if mnem == 'dbra':
            m['dbra'] += 1
        if mnem in DBCC:
            m['dbcc'] += 1
        if mnem in ('mulu', 'muls'):
            m['mul'] += 1
        if mnem == 'ext':
            m['ext'] += 1
        if mnem == 'swap':
            m['swap'] += 1
        if mnem == 'movem':
            m['movem'] += 1
        if mnem == 'lea':
            m['lea'] += 1
        if mnem == 'jmp':
            m['jmp'] += 1
            if ins['ops'] and ea_class(ins['ops'][0]) in (
                    'aind', 'aind_idx', 'pci'):
                m['computed_jmp'] += 1
        # stack-frame access through (d,An) / (An,Xn)
        for role, op in operand_roles(mnem, ins['ops']):
            base, kind = base_of(op)
            if base is None or kind not in ('disp', 'idx'):
                continue
            if base == 'a7':
                if role in ('r', 'rw'):
                    m['a7_disp_reads'] += 1
                    m.setdefault('a7_disp_read_ops', []).append(op)
                if role in ('w', 'rw'):
                    m['a7_disp_writes'] += 1
                    m.setdefault('a7_disp_write_ops', []).append(op)
            elif base == 'a6':
                m['a6_disp'] += 1
                if role in ('r', 'rw'):
                    m['a6_disp_reads'] += 1
                if role in ('w', 'rw'):
                    m['a6_disp_writes'] += 1
        # explicit a7 adjusts (stack cleanup / frame teardown)
        if mnem in ('addq', 'subq', 'adda', 'suba') and len(ins['ops']) == 2:
            if ins['ops'][1].strip() == 'a7':
                m['a7_adjust'] += 1
        if mnem == 'lea' and len(ins['ops']) == 2 and \
                ins['ops'][1].strip() == 'a7':
            m['a7_adjust'] += 1
        # caller-side argument setup: registers written in the preceding
        # straight-line window (back to the last flow boundary, max 6)
        if mnem in ('jsr', 'bsr'):
            window = []
            for j in range(i - 1, max(-1, i - 7), -1):
                prev = insns[j]
                window.append(prev)
                if prev['mnem'] in BRANCH or prev['mnem'] in STOP or \
                        prev['mnem'] in ('jsr', 'bsr'):
                    break
            setup = set()
            for w in window:
                setup |= w['writes'] & (DREGS | (AREGS - {'a7'}))
            m.setdefault('call_setup_regs', []).extend(sorted(setup))
        # call-site idioms
        if mnem in ('jsr', 'bsr') and i + 1 < len(insns):
            nxt = insns[i + 1]
            if nxt['mnem'] in COND:
                m['flags_after_call'] += 1
            if nxt['mnem'] in ('addq', 'subq', 'adda', 'suba', 'lea') \
                    and 'a7' in nxt['writes'] and \
                    len(nxt['ops']) == 2 and nxt['ops'][1].strip() == 'a7':
                m['caller_cleanup'] += 1
        # compiler-ish idioms
        if mnem == 'tst' and i and insns[i - 1]['mnem'] in (
                'move', 'movea', 'moveq') and len(insns[i - 1]['ops']) == 2 \
                and insns[i - 1]['ops'][1] == ins['ops'][0]:
            m['tst_after_move'] += 1
        if mnem in SCC and i and insns[i - 1]['mnem'] == 'moveq' \
                and len(insns[i - 1]['ops']) == 2 \
                and insns[i - 1]['ops'][0].startswith('#') \
                and insns[i - 1]['ops'][0] == '#$0' \
                and insns[i - 1]['ops'][1] == ins['ops'][0]:
            m['bool_pair'] += 1
        if mnem in ('ext',) and i and insns[i - 1]['mnem'] in (
                'move', 'movea') and len(insns[i - 1]['ops']) == 2 \
                and insns[i - 1]['size'] == 'w' \
                and insns[i - 1]['ops'][1] == ins['ops'][0]:
            m['ext_promote'] += 1
    m['first_insn'] = insns[0]['mnem']
    m['last_insn'] = insns[-1]['mnem']
    # entry movem save: movem list,-(An) within first 6 insns (the saved
    # registers are *read* as they are pushed onto the stack)
    saved = DREGS | (AREGS - {'a7'})
    for ins in insns[:6]:
        if ins['mnem'] == 'movem' and len(ins['ops']) == 2 and \
                ea_class(ins['ops'][1]) == 'aind_pre':
            m['entry_movem'] = ins['ops'][0]
            m['entry_movem_regs'] = sorted(ins['reads'] & saved)
            break
    # epilogue movem restore: movem (An)+,list within last 8 insns (the
    # restored registers are *written* as they are popped)
    for ins in insns[-8:]:
        if ins['mnem'] == 'movem' and len(ins['ops']) == 2 and \
                ea_class(ins['ops'][0]) == 'aind_post':
            m['exit_movem'] = ins['ops'][0]
            m['exit_movem_regs'] = sorted(ins['writes'] & saved)
            break
    m['arg_reads'] = sorted(r for r, (k, _) in first_mention.items()
                            if k == 'r' and r in (DREGS | (AREGS - {'a7'})))
    m['a7_first_read'] = first_mention.get('a7', ('', 0))[0] == 'r'
    m['a5_first_read'] = first_mention.get('a5', ('', 0))[0] == 'r'
    m['a6_first_read'] = first_mention.get('a6', ('', 0))[0] == 'r'
    m['write_only'] = sorted(r for r, (k, _) in first_mention.items()
                             if k == 'w' and r in (DREGS | AREGS))
    m['ret_regs'] = sorted(r for r, (k, _) in last_mention.items()
                           if k == 'w' and r in (DREGS | AREGS))
    m['d0_written'] = any('d0' in ins['writes'] for ins in insns)
    m['d1_written'] = any('d1' in ins['writes'] for ins in insns)
    m['a0_written'] = any('a0' in ins['writes'] for ins in insns)
    m['a1_written'] = any('a1' in ins['writes'] for ins in insns)
    return m


def merge_dicts(dicts):
    agg = collections.Counter()
    for d in dicts:
        for k, v in d.items():
            if isinstance(v, int):
                agg[k] += v
            elif isinstance(v, list):
                agg.setdefault(k, [])
                agg[k].extend(v)
    return agg


def report(rows, label):
    """Bounded authorship report over analyzed units."""
    agg = merge_dicts(rows)
    n = len(rows)
    if n == 0:
        print(f'{label}: no units')
        return agg
    p = lambda key: f'{agg[key]} ({agg[key] / max(1, agg["n"]):.4f}/insn)'
    print(f'{label}: {n} units, {agg["n"]} instructions')
    print(f'  link {agg["link"]}  unlk {agg["unlk"]}  rtd {agg["rtd"]}  '
          f'terminators {agg["stop"]}')
    print(f'  movem {p("movem")}  entry-movem '
          f'{sum(1 for r in rows if r.get("entry_movem"))}  exit-movem '
          f'{sum(1 for r in rows if r.get("exit_movem"))}')
    print(f'  calls {p("calls")} (abs-jsr {agg["jsr_abs"]}, bsr '
          f'{agg["bsr"]}, computed call {agg["computed_call"]})')
    print(f'  a7 disp reads {agg["a7_disp_reads"]}  a7 disp writes '
          f'{agg["a7_disp_writes"]}  a6 disp accesses {agg["a6_disp"]}  '
          f'a7_adjust {agg["a7_adjust"]}')
    print(f'  caller_cleanup {agg["caller_cleanup"]}  '
          f'flags_after_call {agg["flags_after_call"]}  '
          f'tst_after_move {agg["tst_after_move"]}  '
          f'bool_pair {agg["bool_pair"]}  ext_promote {agg["ext_promote"]}')
    print(f'  scc {agg["scc"]}  dbcc {agg["dbcc"]}  mul {agg["mul"]}  '
          f'ext {agg["ext"]}  swap {agg["swap"]}  computed_jmp '
          f'{agg["computed_jmp"]}')
    print(f'  units reading a7 first: '
          f'{sum(1 for r in rows if r.get("a7_first_read"))}  (true stack-arg'
          f' reads are the a7 disp>0 counts above)')
    print(f'  units reading a5 first (base reg): '
          f'{sum(1 for r in rows if r.get("a5_first_read"))}  '
          f'units reading a6 first: '
          f'{sum(1 for r in rows if r.get("a6_first_read"))}')
    print(f'  d0 written {sum(1 for r in rows if r.get("d0_written"))}  '
          f'd1 written {sum(1 for r in rows if r.get("d1_written"))}  '
          f'a0 written {sum(1 for r in rows if r.get("a0_written"))}  '
          f'a1 written {sum(1 for r in rows if r.get("a1_written"))}')
    print(f'  inline dc data: {sum(r["dc_count"] for r in rows)} directives '
          f'across {sum(1 for r in rows if r["dc_count"])} units')
    entries = collections.Counter(r['first_insn'] for r in rows)
    print(f'  entry insns (top 15): {entries.most_common(15)}')
    lasts = collections.Counter(r['last_insn'] for r in rows)
    print(f'  last insns (top 10): {lasts.most_common(10)}')
    argpos = collections.Counter()
    for r in rows:
        for reg in r.get('arg_reads', []):
            argpos[reg] += 1
    print(f'  first-use-before-def reads (arg candidates): '
          f'{argpos.most_common(12)}')
    csetup = collections.Counter()
    for r in rows:
        for reg in r.get('call_setup_regs', []):
            csetup[reg] += 1
    print(f'  registers written just before calls (caller setup): '
          f'{csetup.most_common(12)}')
    br = {k: v for k, v in agg.items() if k.startswith('br_')}
    print(f'  branch encodings: {br}')
    masks = collections.Counter(r['entry_movem'] for r in rows
                                if r.get('entry_movem'))
    print(f'  entry movem masks (top 12): {masks.most_common(12)}')
    return agg


def decode_member(data):
    """unidasm a word-swapped member -> list of insn dicts (linear sweep)."""
    tmp = pathlib.Path('/tmp/lane-origin-member.bin')
    tmp.write_bytes(data)
    out = subprocess.run(['unidasm', str(tmp), '-arch', 'm68000',
                          '-basepc', '0'],
                         capture_output=True, text=True).stdout
    return [ev for ev in (parse_unidasm_line(line)
                          for line in out.splitlines()) if ev]


def pristine_span(org, size):
    """Bytes of one span from the pristine CPU-order halves."""
    for base, blob in ((0x000000, FIXED_BIN), (0x200000, BANK_BIN)):
        if base <= org and org + size <= base + 0x100000 and blob.exists():
            data = blob.read_bytes()
            return data[org - base:org - base + size]
    return None


def decode_span(org, size):
    """unidasm one known-code span -> list of insn dicts with addresses."""
    data = pristine_span(org, size)
    if data is None:
        return None
    tmp = pathlib.Path('/tmp/lane-origin-span.bin')
    tmp.write_bytes(data)
    out = subprocess.run(['unidasm', str(tmp), '-arch', 'm68000',
                          '-basepc', hex(org)],
                         capture_output=True, text=True).stdout
    return [ev for ev in (parse_unidasm_line(line)
                          for line in out.splitlines()) if ev]


def target_of(op):
    m = TARGET.match(op.strip())
    return int(m.group(1), 16) if m else None


def structural_pass(funcs):
    """unidasm each span (cached) and compute the structural metrics that
    need per-instruction addresses: branch displacement slack, interior
    targets (multi-entry), fall-through, alignment, decode agreement.

    Returns a dict of global stats keyed by metric."""
    cache = {}
    if DECODE_CACHE.exists():
        cache = json.loads(DECODE_CACHE.read_text())
    stats = collections.Counter()
    stats['spans'] = 0
    stats['decode_mismatch'] = 0
    stats['span_illegal'] = 0
    stats['word_branches'] = 0
    stats['short_branches'] = 0
    stats['word_shortable'] = 0
    stats['interior_targets'] = 0
    stats['fallthrough'] = 0
    stats['aligned4'] = 0
    stats['aligned2'] = 0
    stats['misaligned'] = 0
    examples = collections.defaultdict(list)
    real = {}  # addr -> (org, end, last_mnemonic, source)
    key_of = lambda org: f'{org:x}'
    for org in sorted(funcs):
        f = funcs[org]
        key = key_of(org)
        if key in cache:
            ud = [dict(e) for e in cache[key]]
        else:
            ud = decode_span(org, f['size'])
            cache[key] = ud if ud is not None else []
            if ud is None:
                stats['spans'] += 1
                continue
        stats['spans'] += 1
        parsed_n = len(f['insns'])
        ud_insn = [e for e in ud if not e['illegal']]
        n_illegal = len(ud) - len(ud_insn)
        if n_illegal:
            stats['span_illegal'] += 1
            if len(examples['illegal']) < 6:
                examples['illegal'].append(
                    f'{f["source"]} ({n_illegal} illegal events, '
                    f'{f["insns"] and len(f["insns"])} parsed insns)')
        if len(ud_insn) != parsed_n:
            stats['decode_mismatch'] += 1
            if len(examples['mismatch']) < 6:
                examples['mismatch'].append(
                    f'{f["source"]}: unidasm {len(ud_insn)} insns vs '
                    f'parsed {parsed_n}')
        if org % 4 == 0:
            stats['aligned4'] += 1
        elif org % 2 == 0:
            stats['aligned2'] += 1
        else:
            stats['misaligned'] += 1
        end = org + sum(e['length'] for e in ud) * 2
        last = ud_insn[-1]['mnemonic'] if ud_insn else ''
        real[org] = (org, end, last, f['source'])
        # branch displacement slack (length is 16-bit words)
        for e in ud_insn:
            if e['mnemonic'] in BRANCH and e['ops']:
                tgt = target_of(e['ops'][0])
                if tgt is None:
                    continue
                disp = tgt - (e['addr'] + 2)
                if e['length'] == 1:
                    stats['short_branches'] += 1
                elif e['length'] == 2:
                    stats['word_branches'] += 1
                    if -128 <= disp <= 127:
                        stats['word_shortable'] += 1
                        if len(examples['shortable']) < 8:
                            examples['shortable'].append(
                                f'{f["source"]} @{e["addr"]:x}: '
                                f'{e["raw"].strip()}')
    # interior targets and fall-through over the real extents
    starts = sorted(real)
    for i, org in enumerate(starts):
        s, e, last, src = real[org]
        if i + 1 < len(starts) and starts[i + 1] == e:
            if last not in STOP and last not in ('jmp', 'bra'):
                stats['fallthrough'] += 1
                if len(examples['fallthrough']) < 6:
                    examples['fallthrough'].append(
                        f'{src} ends {last}@{e:x}, next starts at {e:x}')
    # spans are disjoint, so one interval index answers "inside which span"
    by_start = sorted((s2, e2, o2) for o2, (s2, e2, _, _) in real.items())
    from bisect import bisect_right
    starts_only = [row[0] for row in by_start]
    for org in sorted(funcs):
        f = funcs[org]
        key = key_of(org)
        if key not in cache or not cache[key]:
            continue
        for e in cache[key]:
            if e['illegal'] or e['mnemonic'] not in (BRANCH | {'jsr', 'jmp'}):
                continue
            if not e['ops']:
                continue
            tgt = target_of(e['ops'][0])
            if tgt is None:
                continue
            idx = bisect_right(starts_only, tgt) - 1
            if idx < 0:
                continue
            s2, e2, o2 = by_start[idx]
            if o2 == org or not (s2 < tgt < e2 - 4):
                continue
            stats['interior_targets'] += 1
            if len(examples['interior']) < 8:
                examples['interior'].append(
                    f'{f["source"]}@{e["addr"]:x} -> ${tgt:x} '
                    f'inside {real[o2][3]} [${s2:x},${e2:x})')
    DECODE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    DECODE_CACHE.write_text(json.dumps(cache, indent=1))
    return stats, examples, real


def segment(insns):
    """RTS-delimited segments of a linear sweep -> list of lists."""
    segs, cur = [], []
    for ins in insns:
        cur.append(ins)
        if ins['mnemonic'] in STOP:
            if len(cur) >= 2:
                segs.append(cur)
            cur = []
    if len(cur) >= 2:
        segs.append(cur)
    return segs


def insn_dicts_from_ud(insns):
    """Adapt unidasm dicts to the analyzer's insn dicts."""
    out = []
    for ev in insns:
        if ev['illegal']:
            out.append(dict(mnem='illegal', ops=[], size='', reads=set(),
                            writes=set(), flags=False))
            continue
        reads, writes, flags = effects(ev['mnemonic'], ev['ops'])
        size = ''
        if ev['mnemonic'] in BRANCH:
            size = {1: 's', 2: 'w', 3: 'l'}.get(ev['length'], '')
        out.append(dict(mnem=ev['mnemonic'], ops=ev['ops'], size=size,
                        reads=reads, writes=writes, flags=flags))
    return out


def print_roms(romdir, titles):
    for zp in sorted(pathlib.Path(romdir).glob('*.zip')):
        stem = zp.stem
        if titles and stem not in titles:
            continue
        try:
            zf = zipfile.ZipFile(zp)
        except zipfile.BadZipFile:
            continue
        for name in sorted(n for n in zf.namelist() if MEMBER.search(n)):
            data = bytearray(zf.read(name))
            data[0::2], data[1::2] = data[1::2], data[0::2]
            insns = decode_member(bytes(data))
            illegal = sum(1 for i in insns if i['illegal'])
            frac = illegal / max(1, len(insns))
            stops = sum(1 for i in insns if i['mnemonic'] in STOP)
            flag = 'SANITY FAIL (encrypted/garbage?)' if frac > 0.25 else 'ok'
            print(f'\n== {stem} {name} {len(data)} bytes ==')
            print(f'decoded {len(insns)} insns, illegal {illegal} '
                  f'({frac:.3f}), rts {stops} -> {flag}')
            segs = segment(insns)
            all_rows = []
            clean_rows = []
            clean_insns = 0
            for seg in segs:
                n_ill = sum(1 for i in seg if i['illegal'])
                row = analyze_insns(insn_dicts_from_ud(seg))
                row['seg_illegal_frac'] = n_ill / max(1, len(seg))
                row['seg_len'] = len(seg)
                all_rows.append(row)
                if n_ill / max(1, len(seg)) <= 0.05 and 3 <= len(seg) <= 600:
                    clean_rows.append(row)
                    clean_insns += len(seg)
            agg = report(all_rows, f'{stem}/{name} '
                         f'({len(segs)} sweep segments, unfiltered)')
            if clean_rows:
                print(f'  clean code segments: {len(clean_rows)} '
                      f'({clean_insns} insns)')
                cagg = report(clean_rows, f'{stem}/{name} clean')
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUT_DIR / f'rom-{stem}-{name}.json').write_text(
                json.dumps(dict(agg=dict(agg), units=len(all_rows),
                                illegal_frac=frac,
                                clean_agg=dict(merge_dicts(clean_rows))
                                if clean_rows else {},
                                clean_units=len(clean_rows)), indent=1))
        zf.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--roms', help='directory of ROM zips for the '
                    'cross-ROM comparison')
    ap.add_argument('--titles', default='',
                    help='comma list limiting --roms scan')
    args = ap.parse_args()
    if args.roms:
        titles = [t for t in args.titles.split(',') if t]
        print_roms(args.roms, titles)
        return 0
    spans = load_spans()
    funcs = parse_corpus(spans)
    rows, agg = None, None
    rows_all = []
    for addr in sorted(funcs):
        f = funcs[addr]
        rows_all.append(analyze_insns(
            f['insns'],
            dc_count=sum(1 for d in f['directives']
                         if d[0].startswith('dc'))))
    nfun = sum(1 for f in funcs.values() if f['category'] == 'function')
    nblk = sum(1 for f in funcs.values() if f['category'] == 'block')
    print(f'corpus: {len(funcs)} sources ({nfun} functions, {nblk} blocks)')
    agg = report(rows_all, 'NTM corpus')
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(agg=dict(agg), units=len(rows_all))
    if FIXED_BIN.exists() and BANK_BIN.exists():
        stats, examples, real = structural_pass(funcs)
        print('\nstructural pass (unidasm per span, byte addresses):')
        print(f'  spans {stats["spans"]}  decode_mismatch '
              f'{stats["decode_mismatch"]}  spans_with_illegal_events '
              f'{stats["span_illegal"]}')
        print(f'  branches: short {stats["short_branches"]}, word '
              f'{stats["word_branches"]}, word-but-shortable '
              f'{stats["word_shortable"]}')
        print(f'  interior targets (multi-entry): {stats["interior_targets"]}')
        print(f'  fall-through adjacencies: {stats["fallthrough"]}')
        print(f'  alignment: 4B {stats["aligned4"]}, 2B {stats["aligned2"]}, '
              f'odd {stats["misaligned"]}')
        for kind, ex in examples.items():
            if ex:
                print(f'  {kind} examples:')
                for e in ex:
                    print(f'    {e}')
        payload['structural'] = dict(stats)
        payload['structural_examples'] = dict(examples)
    else:
        print('\n(structural pass skipped: run make extract first)')
    per = []
    for addr in sorted(funcs):
        f = funcs[addr]
        met = analyze_insns(f['insns'])
        per.append({k: met[k] for k in
                    ('addr', 'n', 'arg_reads', 'a5_first_read',
                     'a6_first_read', 'entry_movem', 'exit_movem',
                     'a7_disp_reads', 'a7_disp_writes', 'dc_count')
                    if k in met})
    payload['per_unit'] = per
    (OUT_DIR / 'ntm.json').write_text(json.dumps(payload, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
