#!/usr/bin/env python3
"""Compile C decompilation attempts and byte-diff them against the pristine ROM.

Phase-3 scaffold: each <dir>/<addr>.c (default src/p1/c) holds one function
whose address is its basename. The file is compiled with m68k-elf-gcc, linked
at the function's real address, and the .text span is diffed against the
pristine image - the same gate as the assembly path. Matching GCC output
byte-for-byte against hand-written 68000 is hard; a DIFF result is expected
and useful (flag exploration), and the assembly sources stay authoritative.

Requires the m68k-elf-gcc toolchain (AUR m68k-elf-gcc / m68k-elf-binutils;
see docs/TOOLING.md). Run with C_FLAGS to explore compiler settings.
"""
import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
C_DIR = REPO / 'src' / 'p1' / 'c'
WORK = REPO / 'build' / 'work'

FIXED_BASE = 0x000000
BANK_BASE = 0x200000

DEFAULT_FLAGS = '-m68000 -Os -ffreestanding -fomit-frame-pointer ' \
                '-fno-builtin -fno-inline'


def tool(name):
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f'{name} not found - install the m68k-elf toolchain '
                         f'(AUR m68k-elf-gcc/m68k-elf-binutils; see '
                         f'docs/TOOLING.md)')
    return path


ASM_SMELL = re.compile(r'__asm|\basm\s*\(|\.incbin|#embed')


def reject_source(text):
    """Return a reason when a C source smuggles assembly or raw bytes."""
    m = ASM_SMELL.search(text)
    return f'contains asm/embed construct ({m.group(0)!r})' if m else None


def span_verdict(span, expect, declared):
    """(is_match, note) for a compiled span.

    A match needs the bytes to equal the ROM AND at least the inventory's
    declared size: an empty or truncated span can otherwise prefix-equal the
    ROM and fake a match (an empty module assembles to zero bytes).
    """
    if not span:
        return False, 'empty span'
    if declared and len(span) < declared:
        return False, f'span {len(span)} < declared {declared}'
    return span == expect, None


def org_of_c(path):
    m = re.match(r'^([0-9a-fA-F]{8})\.c$', path.name)
    if not m:
        raise SystemExit(f'{path.name}: basename must be the hex address '
                         f'(e.g. 00002908.c)')
    return int(m.group(1), 16)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--check', action='store_true',
                    help='fail (exit 1) on any byte mismatch')
    ap.add_argument('--flags', default=DEFAULT_FLAGS,
                    help='extra compiler flags')
    ap.add_argument('--compiler', choices=('gcc', 'vbcc'), default='gcc',
                    help='gcc (m68k-elf-gcc) or vbcc (tools/vbcc/vbccm68k)')
    ap.add_argument('--tmp', default=str(REPO / 'build' / 'tmp' / 'c'),
                    help='scratch dir for objects')
    ap.add_argument('--dir', default=None,
                    help='directory of <addr>.c attempts (default: src/p1/c)')
    args = ap.parse_args()

    if args.compiler == 'vbcc':
        # Vendored compiler only: tools/setup_vbcc.sh installs it; build with
        # NTMD_VBCC_NAKED=1 to get the #pragma naked/noalign backend.
        cc = str(REPO / 'tools' / 'vbcc' / 'vbccm68k')
        vasm = str(REPO / 'tools' / 'vasm' / 'vasmm68k_mot')
        if not pathlib.Path(cc).exists():
            raise SystemExit(f'{cc} missing - run tools/setup_vbcc.sh '
                             f'(NTMD_VBCC_NAKED=1 for the naked/noalign '
                             f'pragmas)')
    else:
        cc = tool('m68k-elf-gcc')
    ld = tool('m68k-elf-ld')
    objcopy = tool('m68k-elf-objcopy')

    pristine = {
        FIXED_BASE: (WORK / 'p1.fixed.cpu.bin').read_bytes(),
        BANK_BASE: (WORK / 'p1.bank.cpu.bin').read_bytes(),
    }

    tmp = pathlib.Path(args.tmp)
    tmp.mkdir(parents=True, exist_ok=True)

    src_dir = C_DIR if args.dir is None else pathlib.Path(args.dir)
    if not src_dir.is_absolute():
        src_dir = REPO / src_dir
    sources = sorted(src_dir.glob('*.c'))
    if not sources:
        raise SystemExit(f'no sources under {src_dir} - place one function per '
                         f'<addr>.c file')

    # For the vbcc path, cross-function calls need their ROM addresses at
    # assemble time; take them from the name table the asm labels use too.
    sym_table = {}
    if args.compiler == 'vbcc':
        import symbols
        sym_table = {name: addr for addr, (name, _note) in symbols.load_names(REPO).items()}

    # inventory sizes gate short-span false matches (see span_verdict)
    sizes = {}
    for line in (REPO / 'config' / 'functions.tsv').read_text().splitlines():
        if line and not line.startswith('#'):
            f = line.split('\t')
            sizes[int(f[0], 16)] = int(f[2])

    matched = failed = 0
    for src in sources:
        org = org_of_c(src)
        base = (FIXED_BASE if org < 0x100000
                else BANK_BASE if BANK_BASE <= org < 0x300000 else None)
        if base is None:
            print(f'  REJECTED {src.name}: address outside the P-ROM windows')
            failed += 1
            continue
        if org not in sizes or sizes[org] <= 0:
            print(f'  REJECTED {src.name}: missing positive inventory size')
            failed += 1
            continue
        bad = reject_source(src.read_text())
        if bad:
            print(f'  {src.name} REJECTED: {bad}')
            failed += 1
            if args.check:
                sys.exit(1)
            continue
        obj = tmp / (src.stem + '.o')
        elf = tmp / (src.stem + '.elf')
        raw = tmp / (src.stem + '.bin')
        if args.compiler == 'vbcc':
            # vbcc -> mot-syntax asm (with org prepended) -> vasm -> span.
            # vbccm68k run directly emits assembly itself: no -S, -o takes
            # the attached =file form, and the GCC flag set does not apply
            # (only user-supplied --flags pass through).
            asm = tmp / (src.stem + '.asm')
            env = dict(os.environ, VBCC=str(REPO / 'tools' / 'vbcc'))
            extra = [] if args.flags == DEFAULT_FLAGS else args.flags.split()
            r = subprocess.run([cc, '-c99', '-O=2', '-cpu=68000',
                                f'-o={asm}', str(src)] + extra,
                               capture_output=True, text=True, env=env)
            if r.returncode != 0:
                print(f'  {src.name} VBCC FAILED:\n{r.stderr[:400]}')
                failed += 1
                continue
            # Equate the other named ROM functions; the file's own symbol is
            # its label. Unresolved vbcc runtime helpers (__divu and friends)
            # stay errors: the original code uses no such helpers.
            syms = ''.join(f'_{n} equ ${a:x}\n'
                           for n, a in sorted(sym_table.items()) if a != org)
            # The org must sit inside the code section: an org before the
            # section directive makes vasm -Fbin pad the output back to that
            # address. The equates go first, where they emit nothing.
            out, stamped = [], False
            for ln in asm.read_text().splitlines(keepends=True):
                out.append(ln)
                if not stamped and ln.lstrip().startswith('section'):
                    out.append(f' org ${org:x}\n')
                    stamped = True
            if not stamped:
                out.insert(0, f' org ${org:x}\n')
            asm.write_text(syms + ''.join(out))
            r = subprocess.run([vasm, '-Fbin', '-no-opt', '-quiet',
                                '-o', str(raw), str(asm)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                print(f'  {src.name} VASM FAILED:\n{r.stderr[:400]}')
                failed += 1
                continue
            span = raw.read_bytes()
            expect = pristine[base][org - base:org - base + len(span)]
            ok, note = span_verdict(span, expect, sizes.get(org))
            if ok:
                matched += 1
                print(f'  {src.name} ${org:x} +{len(span):5d} MATCH (vbcc)')
            else:
                failed += 1
                bad = next((i for i in range(min(len(span), len(expect)))
                            if span[i] != expect[i]), None)
                pos = f'+${bad:x} got {span[bad]:02x} want {expect[bad]:02x}' \
                    if bad is not None else f'length {len(span)} vs {len(expect)}'
                suffix = f'  ({note})' if note else ''
                print(f'  {src.name} ${org:x} +{len(span):5d} DIFF {pos}{suffix}')
                if args.check:
                    sys.exit(1)
            continue
        r = subprocess.run([cc, '-c', '-o', str(obj), str(src)] + args.flags.split(),
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f'  {src.name} COMPILE FAILED:\n{r.stderr[:400]}')
            failed += 1
            continue
        r = subprocess.run([ld, '-Ttext', hex(org), '-o', str(elf), str(obj)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f'  {src.name} LINK FAILED:\n{r.stderr[:400]}')
            failed += 1
            continue
        r = subprocess.run([objcopy, '-O', 'binary', '-j', '.text',
                            str(elf), str(raw)], capture_output=True, text=True)
        if r.returncode != 0:
            print(f'  {src.name} OBJCOPY FAILED:\n{r.stderr[:400]}')
            failed += 1
            continue
        span = raw.read_bytes()
        expect = pristine[base][org - base:org - base + len(span)]
        ok, note = span_verdict(span, expect, sizes.get(org))
        if ok:
            matched += 1
            print(f'  {src.name} ${org:x} +{len(span):5d} MATCH')
        else:
            failed += 1
            bad = next((i for i in range(min(len(span), len(expect)))
                        if span[i] != expect[i]), None)
            pos = f'+${bad:x} got {span[bad]:02x} want {expect[bad]:02x}' \
                if bad is not None else f'length {len(span)} vs {len(expect)}'
            suffix = f'  ({note})' if note else ''
            print(f'  {src.name} ${org:x} +{len(span):5d} DIFF {pos}{suffix}')
            if args.check:
                sys.exit(1)

    flags_note = ''
    if args.compiler == 'gcc' or args.flags != DEFAULT_FLAGS:
        flags_note = f', flags: {args.flags}'
    print(f'C attempts: {matched} match, {failed} differ/fail '
          f'(compiler: {args.compiler}{flags_note})')
    if args.check and failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
