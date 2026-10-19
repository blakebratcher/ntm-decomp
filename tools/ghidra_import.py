#!/usr/bin/env python3
"""Convert Ghidra listing exports (out2/asm_all/*.s) into vasm mot source files,
verified byte-exact against the ROM at import time.

Ghidra 12.1.2's 68000 listings use their own dialect, e.g.:

    00002908<TAB>tst.l D0
    beq.w 0x00002c1e              branch target; '.b' means short
    moveq 0x0,D7                  moveq immediate without '#'
    move.l (0x00102c40).l,D0      absolute long in parens
    jsr 0x00002844.l              absolute target without parens
    movem.w { d0 d1},(a2)         brace-enclosed register list
    D0w / D7b register size suffixes; 'SP' instead of 'a7'

The ROM is the ground truth, listings are hints (the out2 exports were made
from an earlier Ghidra state and some render stale branch targets or stale
instruction text). Each converted file is assembled and byte-diffed against
the pristine image; on mismatch the branch targets are recomputed from the
ROM's actual displacement bytes; if the file still does not round-trip it is
emitted as labeled dc.b data - the standard treatment for data regions in
matching-decomp projects. Every imported function is byte-exact by
construction, and tools/build_p1.py --check re-verifies it.
"""
import argparse
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
WORK = REPO / 'build' / 'work'
VASM = REPO / 'tools' / 'vasm' / 'vasmm68k_mot'

BRANCHES = {'beq', 'bne', 'bpl', 'bmi', 'bcc', 'bcs', 'bvc', 'bvs',
            'bge', 'bgt', 'ble', 'blt', 'bhi', 'bls', 'bra', 'bsr',
            'dbt', 'dbf', 'dbhi', 'dbls', 'dbcc', 'dbcs', 'dbne', 'dbeq',
            'dbvc', 'dbvs', 'dbpl', 'dbmi', 'dbge', 'dbgt', 'dble'}


def convert_line(text):
    """Ghidra listing line -> vasm mot line (or None for separators)."""
    t = text.strip()
    if not t or t.startswith(';') or t.startswith('--'):
        return None
    addr = None
    m = re.match(r'^[0-9a-fA-F]{8}\s+(.*)$', t)
    if m:
        addr = int(t[:8], 16)
        t = m.group(1).strip()
        if not t or t.startswith(';') or t.startswith('--'):
            return None
    t = t.lower()
    t = re.sub(r'\bpc\b', 'PC', t)                          # vasm's PC keyword is uppercase
    t = re.sub(r'\bsp\b', 'a7', t)                          # Ghidra 'SP'
    t = re.sub(r'\b([da])([0-7])([bw])(?=\*)', r'\1\2.\3', t)  # indexed-mode size
    t = re.sub(r'\b([da])([0-7])[bw]\b', r'\1\2', t)        # register size suffixes
    t = re.sub(r'\(0x([0-9a-f]+)\)\.(l|w)', r'($\1).\2', t)  # (0x...).l -> ($...).l
    t = re.sub(r'\b0x([0-9a-f]+)\.(l|w)', r'($\1).\2', t)    # bare jsr/lea targets
    # (d16,PC): Ghidra prints the displacement; vasm treats the operand as the
    # target address, so compute target = insn_addr + 2 + disp.
    if addr is not None:
        def pc_repl(mm):
            sign = -1 if mm.group(1) else 1
            disp = sign * int(mm.group(2), 16)
            target = addr + 2 + disp
            return f'(-${-target:x},PC)' if target < 0 else f'(${target:x},PC)'
        t = re.sub(r'\((-)?0x([0-9a-f]+),PC\)', pc_repl, t)
    mnemonic = t.split(None, 1)[0] if t.split(None, 1) else ''
    base = mnemonic.split('.')[0]
    if base in BRANCHES:
        t = re.sub(r'\b0x([0-9a-f]+)\s*$', r'$\1', t)       # branch target
        t = re.sub(r'\.b\b', '.s', t, count=1)              # short branch spelling
    t = re.sub(r'^(moveq) 0x', r'\1 #$', t)                 # moveq needs '#'
    t = re.sub(r'^(moveq) -0x', r'\1 #-$', t)               # ... also for negatives
    t = re.sub(r'\{([^}]*)\}', _movem_list, t)              # { d0 d1} -> d0-d1
    t = t.replace('0x', '$')
    return t


def _movem_list(m):
    """'{ d0 d1}' -> 'd0-d1'; non-contiguous groups joined with '/'."""
    regs = m.group(1).split()
    groups, cur = [], []
    for r in regs:
        cls, num = r[0], int(r[1])
        if cur and cur[-1][0] == cls and cur[-1][1] + 1 == num:
            cur.append((cls, num))
        else:
            if cur:
                groups.append(cur)
            cur = [(cls, num)]
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        out.append(f'{g[0][0]}{g[0][1]}' if len(g) == 1
                   else f'{g[0][0]}{g[0][1]}-{g[0][0]}{g[-1][1]}')
    return '/'.join(out)


def read_functions(tsv_path):
    funcs = {}
    for line in tsv_path.read_text().splitlines():
        if not line.strip():
            continue
        addr, name, size = line.split('\t')[:3]
        funcs[int(addr, 16)] = (name, int(size))
    return funcs


def load_names():
    """config/names.tsv: symbolic names for decoded functions (may be empty)."""
    import symbols
    return {addr: name for addr, (name, _note) in symbols.load_names(REPO).items()}


def load_rom_halves():
    return ((WORK / 'p1.fixed.cpu.bin').read_bytes(),
            (WORK / 'p1.bank.cpu.bin').read_bytes())


def blob_for(addr, fixed, bank):
    if addr < 0x100000:
        return fixed, 0
    if 0x200000 <= addr < 0x300000:
        return bank, 0x200000
    return None, None


def assemble_text(source_text):
    src = REPO / 'build' / 'tmp' / 'ntm-import.s'
    src.parent.mkdir(parents=True, exist_ok=True)
    out = REPO / 'build' / 'tmp' / 'ntm-import.bin'
    src.write_text(source_text)
    result = subprocess.run([str(VASM), '-Fbin', '-no-opt', '-quiet',
                             '-o', str(out), str(src)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return out.read_bytes()


def source_from_events(org, events):
    lines = [f' org ${org:x}']
    lines += ['        ' + t for _, t in events]
    return '\n'.join(lines) + '\n'


def repair_branches(events, blob, base):
    """Recompute branch targets from the ROM's actual displacement bytes."""
    repaired = []
    for addr, text in events:
        mnemonic = text.split(None, 1)[0]
        base_mnemonic = mnemonic.split('.')[0]
        if base_mnemonic in BRANCHES:
            if '.s' in mnemonic:
                disp = blob[addr - base + 1]
                if disp >= 0x80:
                    disp -= 0x100
            else:
                disp = int.from_bytes(blob[addr - base + 2:addr - base + 4],
                                      'big', signed=True)
            target = addr + 2 + disp
            text = re.sub(r'\$[0-9a-f]+\s*$', f'${target:x}', text)
        repaired.append((addr, text))
    return repaired


def dc_b_lines(org, span_end, blob, base):
    lines = []
    p = org
    while p < span_end:
        chunk = blob[p - base:p - base + 8]
        lines.append('        dc.b ' + ','.join(f'${b:02x}' for b in chunk))
        p += len(chunk)
    return '\n'.join(lines)


_INSTR_CACHE = {}


def instr_bytes(text, org):
    """Assemble one instruction alone -> its bytes.

    Cached only for position-independent texts; branches and PC-relative
    operands depend on the assembly address, so they are assembled per call.
    """
    mnemonic = text.split(None, 1)[0].split('.')[0]
    depends_on_address = mnemonic in BRANCHES or 'PC' in text
    key = (text, org) if depends_on_address else (text, 0)
    if key in _INSTR_CACHE:
        return _INSTR_CACHE[key]
    src = REPO / 'build' / 'tmp' / 'ntm-one.s'
    src.parent.mkdir(parents=True, exist_ok=True)
    out = REPO / 'build' / 'tmp' / 'ntm-one.bin'
    src.write_text(f' org ${org:x}\n        {text}\n')
    result = subprocess.run([str(VASM), '-Fbin', '-no-opt', '-quiet',
                             '-o', str(out), str(src)],
                            capture_output=True, text=True)
    got = None if result.returncode != 0 else out.read_bytes()
    if not depends_on_address:
        _INSTR_CACHE[key] = got
    return got


class _StaleListing(Exception):
    pass


_BYTE_IMM = re.compile(r'^(ori|andi|subi|addi|eori|cmpi)\.b\s+#')
_BIT_IMM = re.compile(r'^(btst|bset|bclr|bchg)(\.l)?\s+#')


def filler_repair(span, pos, ins_bytes, text):
    """68000 immediates whose upper bits are ignored by the CPU: byte-size
    ori/andi/subi/addi/eori/cmpi carry a 16-bit immediate (low byte used) and
    btst/bset/bclr/bchg carry a bit number the CPU masks. The ROM's upper
    byte(s) are arbitrary while vasm emits the clean form; when the only
    difference is that immediate word, return dc.w lines with the ROM's exact
    words so the output stays byte-exact. Covers 4-byte and indexed
    6/8-byte encodings."""
    if not (_BYTE_IMM.match(text) or _BIT_IMM.match(text)):
        return None
    n = len(ins_bytes)
    if n < 4 or len(span) < pos + n:
        return None
    rom = span[pos:pos + n]
    if rom[:2] == ins_bytes[:2] and rom[3] == ins_bytes[3] \
            and rom[4:] == ins_bytes[4:] and rom[2] != ins_bytes[2]:
        words = [(rom[i] << 8) | rom[i + 1] for i in range(0, n, 2)]
        return 'dc.w ' + ','.join(f'${w:04x}' for w in words)
    return None


def pcrel_repair(span, pos, ins_addr, text, ins_bytes):
    """Ghidra renders pc-relative jsr/jmp/lea/pea targets the same way as
    absolute ones; the ROM's opcode decides. When the ROM encodes the
    (d16,PC) form, rewrite the operand as a vasm (target,PC) reference.
    Handles both the ( $x).l and the bare jsr $x renderings."""
    m = re.match(r'^(jsr|jmp|lea|pea)\s+(?:\(\$([0-9a-f]+)\)\.l|\$([0-9a-f]+))$', text)
    if not m:
        return None
    opcodes = {'jsr': 0x4eba, 'jmp': 0x4efa, 'lea': 0x41fa, 'pea': 0x487a}
    rom = span[pos:pos + 4]
    if len(rom) < 4:
        return None
    if int.from_bytes(rom[:2], 'big') != opcodes[m.group(1)]:
        return None
    disp = int.from_bytes(rom[2:4], 'big', signed=True)
    target = ins_addr + 2 + disp
    return f'{m.group(1)} (${target:x},PC)'


def build_with_gaps(org, span, events, blob, base):
    """Assemble events, inserting dc.b fills wherever the ROM has bytes
    between instructions (data islands). Returns the vasm source whose output
    equals span, or raises _StaleListing."""
    items = []          # (fill_len, text) pairs; fill_len 0 = no fill
    pos = 0
    for ins_addr, text in events:
        ins_bytes = instr_bytes(text, ins_addr)
        if ins_bytes is None:
            raise _StaleListing
        n = len(ins_bytes)
        expected = org + pos
        if ins_addr > expected:
            # the listing's own addresses give the exact data-island size
            fill = ins_addr - expected
            if pos + fill + n > len(span):
                raise _StaleListing
            items.append((fill, text))
            pos += fill + n
            continue
        if ins_addr < expected:
            # duplicated/stale event address; drop it
            continue
        if span[pos:pos + n] == ins_bytes:
            items.append((0, text))
            pos += n
            continue
        repaired = filler_repair(span, pos, ins_bytes, text)
        if repaired is not None:
            items.append((0, repaired))
            pos += n
            continue
        repaired = pcrel_repair(span, pos, ins_addr, text, ins_bytes)
        if repaired is not None:
            repaired_bytes = instr_bytes(repaired, ins_addr)
            if repaired_bytes is not None and span[pos:pos + len(repaired_bytes)] == repaired_bytes:
                items.append((0, repaired))
                pos += len(repaired_bytes)
                continue
        raise _StaleListing
    trailing = len(span) - pos
    if trailing < 0:
        raise _StaleListing
    if trailing:
        items.append((trailing, None))

    lines = [f' org ${org:x}']
    cur = 0
    for fill, text in items:
        if fill:
            chunk = span[cur:cur + fill]
            lines.append('        dc.b ' + ','.join(f'${b:02x}' for b in chunk))
            cur += fill
        if text is not None:
            lines.append('        ' + text)
            cur += len(instr_bytes(text, org + cur))
    source = '\n'.join(lines) + '\n'
    body = assemble_text(source)
    if body != span:
        raise _StaleListing
    return source


def import_one(asm_dir, out_dir, addr, name, size, fixed, bank):
    src = asm_dir / f'{addr:08x}.s'
    blob, base = blob_for(addr, fixed, bank)
    if blob is None:
        raise SystemExit(f'{addr:08x}: address outside P-ROM windows')

    events = []
    if src.exists():
        for line in src.read_text().splitlines():
            m = re.match(r'^([0-9a-fA-F]{8})\s+(.*)$', line)
            if not m:
                continue
            ins_addr = int(m.group(1), 16)
            converted = convert_line(line)
            if converted is not None:
                events.append((ins_addr, converted))
    usable = [(a, t) for a, t in events if org_ok(a, addr, size)]

    span_end = max(addr + size, max((a for a, _ in usable), default=addr) + 0x10)
    # Ghidra's function body can end at the last instruction's START, so the
    # listing straddles the recorded size; keep the span inside the ROM window.
    win_end = 0x100000 if addr < 0x100000 else 0x300000
    span_end = min(span_end, win_end)
    span = blob[addr - base:span_end - base]

    def tries():
        yield usable
        yield repair_branches(usable, blob, base)

    how = 'data'
    lines = None
    for evs in tries():
        if not evs:
            break
        try:
            source = build_with_gaps(addr, span, evs, blob, base)
        except _StaleListing:
            source = None
        if source is not None:
            how = 'insn'
            lines = '\n'.join(source.splitlines()[1:])
            break
    if how == 'data':
        lines = dc_b_lines(addr, span_end, blob, base)

    out = out_dir / f'{addr:08x}.s'
    out.write_text(
        f'; Neo Turf Masters - decompiled fragment (research)\n'
        f'; imported from a Ghidra listing: {src}\n'
        f'; function {name}, {size} bytes, kind: {how}\n'
        f'\n'
        f' org ${addr:x}\n'
        f'{name}:\n'
        f'{lines}\n'
    )
    return how


def org_ok(a, org, size):
    return org <= a <= org + size + 0x10


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--asm-dir',
                    default=str(REPO / 'build' / 'ghidra' / 'asm_all'),
                    help='Ghidra per-function listing directory (set explicitly)')
    ap.add_argument('--functions',
                    default=str(REPO / 'config' / 'functions.tsv'))
    ap.add_argument('--out',
                    default=str(REPO / 'src' / 'p1' / 'asm' / 'functions'))
    ap.add_argument('--addrs', nargs='*', metavar='HEX',
                    help='import only these addresses (no 0x prefix); default: all')
    args = ap.parse_args()

    if not VASM.exists():
        raise SystemExit(f'{VASM} missing - run tools/setup_vasm.sh (or make setup)')

    funcs = read_functions(pathlib.Path(args.functions))
    if args.addrs:
        addrs = [int(a, 16) for a in args.addrs]
        unknown = [hex(a) for a in addrs if a not in funcs]
        if unknown:
            raise SystemExit(f'addresses not in the function inventory: {unknown}')
    else:
        addrs = sorted(funcs)

    fixed, bank = load_rom_halves()
    names = load_names()
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    stats = {'insn': 0, 'data': 0}
    for addr in addrs:
        ghidra_name, size = funcs[addr]
        kind = import_one(pathlib.Path(args.asm_dir), out_dir, addr,
                          names.get(addr, ghidra_name), size, fixed, bank)
        stats[kind] += 1
    print(f'imported {len(addrs)} functions into {out_dir}/: '
          f"{stats['insn']} instructions, {stats['data']} data fallback")


if __name__ == '__main__':
    main()
