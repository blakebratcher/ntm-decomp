#!/usr/bin/env python3
"""Decode P-ROM residue spans whose Ghidra listings never round-tripped.

MAME's unidasm disassembles the pristine bytes directly; vasm then has to
reproduce the span byte-exactly or nothing is written (the ROM stays ground
truth, same philosophy as tools/ghidra_import.py and the Z80 side's
tools/unidasm_import.py). A span that contains undecodable bytes (MAME
"illegal"/"invalid") is reported with its first mismatch for hand work.

The default span for each address runs to the next entry in the function
inventory; --end overrides it for one address.

Usage: python3 tools/unidasm_spans.py --addrs 4c8e 70fe4 ... [--end HEX]
       [--dry]   (--dry: print the converted source, write nothing)
"""
import argparse
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
VASM = REPO / 'tools' / 'vasm' / 'vasmm68k_mot'
WORK = REPO / 'build' / 'work'
OUT_DIR = REPO / 'src' / 'p1' / 'asm' / 'functions'

FIXED_BASE = 0x000000
BANK_BASE = 0x200000

BRANCHES = {'bra', 'bsr', 'bhi', 'bls', 'bcc', 'bcs', 'bne', 'beq',
            'bvc', 'bvs', 'bpl', 'bmi', 'bge', 'blt', 'bgt', 'ble'}
DBCC = {'dbt', 'dbf', 'dbra', 'dbeq', 'dbne', 'dbcc', 'dbcs', 'dbmi',
        'dbpl', 'dbge', 'dblt', 'dbgt', 'dble', 'dbhi', 'dbls',
        'dbvc', 'dbvs'}


def split_operands(text):
    """Split on commas outside parentheses (operands like (d8,PC,Xn))."""
    ops, depth, cur = [], 0, ''
    for ch in text:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            ops.append(cur.strip())
            cur = ''
        else:
            cur += ch
    if cur.strip():
        ops.append(cur.strip())
    return ops


def convert(ins_addr, length, text):
    """unidasm text (lowercased 'mnemonic operands') -> repo mot dialect.

    Note: MAME prints (d16,PC) operands as resolved targets but leaves
    (d8,PC,Xn) displacements raw (the index makes the target unknowable);
    vasm always wants a target there, so the raw form is resolved using the
    instruction address.
    """
    parts = text.split(None, 1)
    op = parts[0]
    ops = split_operands(parts[1]) if len(parts) > 1 else []
    base = op.split('.')[0]
    if base in BRANCHES:
        op += '.s' if length == 2 else '.w'
    size = op.split('.')[1] if '.' in op else ''
    out = []
    for operand in ops:
        m = re.match(r'^\((-?\$[0-9a-f]+),pc,([a-z0-9.]+)\)$', operand)
        if m:
            disp = int(m.group(1).replace('$', ''), 16)
            target = ins_addr + 2 + disp
            operand = f'(${target:x},pc,{m.group(2)})'
        operand = re.sub(r',pc([,)])', r',PC\1', operand)
        operand = re.sub(r'^\$([0-9a-f]+)\.([lw])$', r'($\1).\2', operand)
        m = re.match(r'^#\$([0-9a-f]+)$', operand)
        if m:
            val = int(m.group(1), 16)
            limit = {'b': 0x80, 'w': 0x8000, 'l': 0x80000000}.get(size, 0x80)
            if base == 'moveq':
                limit = 0x80
            if size and limit and val >= limit:
                val -= 1 << (8 if limit == 0x80 else
                             16 if limit == 0x8000 else 32)
                operand = f'#-${-val:x}'
        elif base in BRANCHES or base in DBCC:
            m = re.match(r'^\$([0-9a-f]+)$', operand)
            if m:
                operand = '$%08x' % int(m.group(1), 16)
        out.append(operand)
    return ' '.join([op] + [','.join(out)]) if ops else op


def disassemble(path, base):
    r = subprocess.run(['unidasm', str(path), '-arch', 'm68000',
                        '-basepc', hex(base), '-lower', '-norawbytes'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f'unidasm failed: {r.stderr[:300]}')
    events = []
    for line in r.stdout.splitlines():
        m = re.match(r'^([0-9a-f]+):\s+(.*)$', line.strip())
        if m:
            events.append((int(m.group(1), 16), m.group(2).strip()))
    return events


def next_entry(addrs, addr):
    return next((a for a in addrs if a > addr), None)


def build_source(addr, events, span_end):
    lines = []
    for i, (a, text) in enumerate(events):
        if 'illegal' in text or 'invalid' in text or text.startswith('dc.'):
            raise ValueError(f'un-decodable at ${a:x}: {text}')
        length = events[i + 1][0] - a if i + 1 < len(events) else span_end - a
        lines.append('        ' + convert(a, length, text))
    return '\n'.join(lines) + '\n'


def assemble(org, body_lines):
    src = pathlib.Path(f'/tmp/ntm-span-{org:08x}.s')
    src.write_text(f' org ${org:x}\nspan:\n' + body_lines)
    out = pathlib.Path(f'/tmp/ntm-span-{org:08x}.bin')
    r = subprocess.run([str(VASM), '-Fbin', '-no-opt', '-quiet',
                        '-o', str(out), str(src)], capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(f'vasm failed:\n{r.stderr[:300]}')
    return out.read_bytes()


def load_inventory():
    return sorted(int(l.split('\t')[0], 16)
                  for l in (REPO / 'config' / 'functions.tsv').read_text().splitlines()
                  if l and not l.startswith('#'))


def load_names():
    import symbols
    return {addr: name for addr, (name, _note) in symbols.load_names(REPO).items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--addrs', nargs='+', metavar='HEX', required=True)
    ap.add_argument('--end', metavar='HEX',
                    help='span end for the address (default: next entry)')
    ap.add_argument('--dry', action='store_true',
                    help='print the source instead of writing')
    args = ap.parse_args()

    if not VASM.exists():
        raise SystemExit(f'{VASM} missing - run tools/setup_vasm.sh')

    pristine = {
        FIXED_BASE: (WORK / 'p1.fixed.cpu.bin').read_bytes(),
        BANK_BASE: (WORK / 'p1.bank.cpu.bin').read_bytes(),
    }
    addrs = load_inventory()
    names = load_names()
    for addr_s in args.addrs:
        addr = int(addr_s, 16)
        base = (FIXED_BASE if addr < 0x100000 else BANK_BASE)
        span_end = (int(args.end, 16) if args.end
                    else next_entry(addrs, addr) or addr + 0x10)
        span = pristine[base][addr - base:span_end - base]
        tmp = pathlib.Path(f'/tmp/ntm-span-{addr:08x}.raw')
        tmp.write_bytes(span)
        try:
            events = disassemble(tmp, addr)
            body = build_source(addr, events, span_end)
            got = assemble(addr, body)
        except ValueError as e:
            print(f'{addr:08x}: FAIL {e}')
            continue
        if got != span:
            bad = next((i for i in range(min(len(got), len(span)))
                        if got[i] != span[i]), min(len(got), len(span)))
            print(f'{addr:08x}: FAIL assembled {len(got)} bytes vs {len(span)}, '
                  f'first mismatch +${bad:x}')
            continue
        name = names.get(addr, f'FUN_{addr:08x}')
        source = (
            f'; Neo Turf Masters - decompiled fragment (research)\n'
            f'; decoded from the ROM bytes with unidasm (the Ghidra listing did\n'
            f'; not round-trip); re-checked byte-exact by tools/unidasm_spans.py\n'
            f'; function {name}, {len(span)} bytes, kind: insn\n'
            f'\n'
            f' org ${addr:x}\n'
            f'{name}:\n'
            f'{body}')
        if args.dry:
            print(source)
        else:
            (OUT_DIR / f'{addr:08x}.s').write_text(source)
            print(f'{addr:08x}: OK {len(span)} bytes, {len(events)} insns '
                  f'-> {OUT_DIR / f"{addr:08x}.s"}')


if __name__ == '__main__':
    main()
