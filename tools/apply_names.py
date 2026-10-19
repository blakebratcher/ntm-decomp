#!/usr/bin/env python3
"""Rename the labels in src/p1/asm/functions/ and blocks/ from config/names.tsv
and config/names/*.tsv (tools/symbols.py).

Labels only - addresses, instructions and data are untouched, so the byte gate
stays green.  Every label whose assembled address carries a table name is
renamed: the first label of a source (the function entry) and, when a file
holds several labels, the inner/secondary entries too (a file whose last
instruction falls through into the next routine keeps that routine's entry as
a global label inside it - see docs/lanes/finish.md).  Inner label addresses
come from a vasm -L listing, so renames are exact and never guess offsets.

Missing files are reported and skipped (the address may not be a function in
the inventory).
"""
import pathlib
import re
import subprocess
import sys
import tempfile

import symbols

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC_DIR = REPO / 'src' / 'p1' / 'asm' / 'functions'
BLOCK_DIR = REPO / 'src' / 'p1' / 'asm' / 'blocks'
INC_DIR = REPO / 'build' / 'gen' / 'p1'
VASM = REPO / 'tools' / 'vasm' / 'vasmm68k_mot'

LABEL = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*):$')
SYMBOL = re.compile(r'^(\S+)\s+A:([0-9A-F]{8})\s*$')


def label_addresses(src):
    """{label: address} for every label in the source, via a vasm -L listing."""
    with tempfile.TemporaryDirectory() as td:
        lst = pathlib.Path(td) / 'l.txt'
        run = subprocess.run(
            [str(VASM), '-Fbin', '-no-opt', '-quiet', '-L', str(lst),
             '-o', str(pathlib.Path(td) / 'o.bin'),
             f'-I{INC_DIR}', str(src)],
            capture_output=True, text=True)
        if run.returncode != 0:
            raise SystemExit(f'vasm failed on {src.name}:\n{run.stderr}')
        out = {}
        in_symbols = False
        for line in lst.read_text().splitlines():
            if line.startswith('Symbols by name:'):
                in_symbols = True
                continue
            if in_symbols:
                if not line.strip():
                    break
                m = SYMBOL.match(line)
                if m and not m.group(1).startswith('.'):
                    out[m.group(1)] = int(m.group(2), 16)
        return out


def rename_labels(path, names):
    """Rename every label in path whose address is in names; report count."""
    lines = path.read_text().splitlines()
    named = {addr: name for addr, (name, _) in names.items()}
    addr_of = None
    changed = 0
    for i, line in enumerate(lines):
        m = LABEL.match(line)
        if not m:
            continue
        if addr_of is None:
            addr_of = label_addresses(path)
        addr = addr_of.get(m.group(1))
        if addr is None:
            raise SystemExit(f'{path.name}: label {m.group(1)!r} missing from the listing')
        if addr in named and named[addr] != m.group(1):
            lines[i] = f'{named[addr]}:'
            changed += 1
    if changed:
        path.write_text('\n'.join(lines) + '\n')
    return changed


def load_names():
    return symbols.load_names(REPO)


def main():
    names = load_names()
    applied = missing = 0
    for addr, (name, note) in sorted(names.items()):
        src = SRC_DIR / f'{addr:08x}.s'
        if not src.exists():
            src = BLOCK_DIR / f'{addr:08x}.s'
        if not src.exists():
            print(f'  skip {addr:08x}: no source file')
            missing += 1
            continue
        text = src.read_text()
        lines = text.splitlines()
        labels = LABEL.findall(text)
        if len(labels) == 1:
            for i, line in enumerate(lines):
                m = LABEL.match(line)
                if m and not line.startswith(' org'):
                    lines[i] = f'{name}:'
                    break
        # update the provenance comment, preserving the size/kind markers
        for i, line in enumerate(lines):
            if line.startswith('; function '):
                lines[i] = re.sub(r'^; function \S+', f'; function {name}', line)
                break
        src.write_text('\n'.join(lines) + '\n')
        applied += 1
    # multi-label files: rename every label whose assembled address carries a
    # table name (the first label may sit at a different address than the
    # file stem when a fall-through entry was merged into this source)
    renamed = 0
    for d in (SRC_DIR, BLOCK_DIR):
        for src in sorted(d.glob('*.s')):
            if len(LABEL.findall(src.read_text())) > 1:
                renamed += rename_labels(src, names)
    print(f'renamed {applied} labels, {missing} addresses have no source file; '
          f'{renamed} inner entry labels renamed')


if __name__ == '__main__':
    main()
