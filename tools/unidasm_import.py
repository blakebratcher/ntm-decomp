#!/usr/bin/env python3
"""Import Z80 functions from unidasm listings into src/m1/asm/functions/,
verified byte-exact against the pristine M1 (same philosophy as
tools/ghidra_import.py for the 68000 side).

unidasm output is nearly vasm z80/std compatible; the differences handled
here: std syntax wants '.org 0x…' and '0x' hex (not '$'), and unidasm prints
'jr' targets RELATIVE TO THE DUMP BASE (the -skip address) rather than
absolute. Function bodies run from the entry to the first UNCONDITIONAL
ret/reti/jp/jr (conditional returns continue the body). Anything that does
not round-trip is held as dc.b data; the ROM is always ground truth.

Usage: python3 tools/unidasm_import.py --addrs 1ed6 2050 ... [--names name1,name2,...]
"""
import argparse
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
VASM = REPO / 'tools' / 'vasm' / 'vasmz80_std'
M1 = REPO / 'build' / 'assets' / '200-m1.m1'
OUT_DIR = REPO / 'src' / 'm1' / 'asm' / 'functions'

BOUNDARY = re.compile(r'^(ret|reti|jp|jr)\b')   # unconditional forms only


def convert_line(line, dump_base):
    """unidasm line -> vasm z80/std text, or None.

    unidasm format: '<offset>: <hex bytes column>  <mnemonic> <operands>'"""
    t = line.strip()
    if not t:
        return None
    m = re.match(r'^[0-9a-fA-F]{5}:\s+(?:[0-9a-fA-F]{2}\s+)+\s*(.*)$', t)
    if m:
        t = m.group(1).strip()
    if not t:
        return None
    t = t.lower()
    # jr targets are relative to the dump base; make them absolute
    t = re.sub(r'^jr\s+([a-z]+),\$([0-9a-f]+)$',
               lambda mm: f'jr {mm.group(1)},0x{dump_base + int(mm.group(2), 16):x}',
               t)
    t = re.sub(r'\$([0-9a-f]+)', r'0x\1', t)
    return t


def is_boundary(text):
    """True if the line is an unconditional ret/reti/retn/jp/jr (ends the body)."""
    parts = text.split(None, 1)
    op = parts[0] if parts else ''
    conditions = ('z', 'nz', 'c', 'nc', 'po', 'pe', 'p', 'm')
    rest = parts[1] if len(parts) > 1 else ''
    first = rest.split(',')[0].strip()
    if op in ('reti', 'retn'):
        return True
    if op in ('ret', 'jp', 'jr'):
        return first not in conditions
    return False


def assemble(org, lines):
    src = REPO / 'build' / 'tmp' / 'm1-import.s'
    src.parent.mkdir(parents=True, exist_ok=True)
    body = '\n'.join('        ' + l for l in lines)
    src.write_text(f' .org 0x{org:x}\nf_{org:x}:\n{body}\n')
    out = pathlib.Path('/tmp/ntm-m1-import.bin')
    r = subprocess.run([str(VASM), '-Fbin', '-quiet', '-o', str(out), str(src)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stderr
    return out.read_bytes(), None


def import_one(addr, name, note, pristine):
    listing = subprocess.run(
        ['unidasm', str(M1), '-arch', 'z80', '-basepc', '0x0',
         '-skip', str(addr), '-count', '0x100'],
        capture_output=True, text=True).stdout
    lines = []
    for raw in listing.splitlines():
        converted = convert_line(raw, addr)
        if converted is None:
            continue
        lines.append(converted)
        if is_boundary(converted):
            break
    if not lines:
        raise SystemExit(f'{addr:08x}: empty listing')

    body, err = assemble(addr, lines)
    how = 'data'
    if body is None:
        how = 'data'
    else:
        expect = pristine[addr:addr + len(body)]
        if body == expect:
            how = 'insn'
        else:
            how = 'data'
    if how == 'data':
        lines = []
        p = addr
        while p < addr + 0x100:
            chunk = pristine[p:p + 8]
            lines.append('        .byte ' + ','.join(f'0x{b:02x}' for b in chunk))
            p += len(chunk)
    (OUT_DIR / f'{addr:08x}.s').write_text(
        f'; Neo Turf Masters M1 (Z80 sound driver) - decompiled fragment\n'
        f'; {name}: {note}\n'
        f'\n'
        f' .org 0x{addr:x}\n'
        f'{name}:\n'
        + '\n'.join(lines) + '\n'
    )
    return how


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--addrs', nargs='*', metavar='HEX', required=True)
    ap.add_argument('--names', help='comma-separated symbolic names (same order)')
    args = ap.parse_args()

    if not VASM.exists():
        raise SystemExit(f'{VASM} missing - run tools/setup_vasm.sh')
    if not M1.exists():
        raise SystemExit('build/assets/200-m1.m1 missing - run make extract')

    names = (args.names or '').split(',') if args.names else []
    pristine = M1.read_bytes()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stats = {'insn': 0, 'data': 0}
    for i, addr_s in enumerate(args.addrs):
        addr = int(addr_s, 16)
        name = names[i] if i < len(names) and names[i] else f'm1_{addr_s}'
        how = import_one(addr, name, f'unidasm import at 0x{addr:x}', pristine)
        stats[how] += 1
        print(f'  {addr:08x} {name}: {how}')
    print(f"imported {len(args.addrs)}: {stats['insn']} instructions, "
          f"{stats['data']} data fallback")


if __name__ == '__main__':
    main()
