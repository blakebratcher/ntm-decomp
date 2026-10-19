#!/usr/bin/env python3
"""Verify the owner-provided Neo Turf Masters ROM set and stage build inputs.

Nothing this script writes is committed (build/ is gitignored). The ROMs are
never distributed with the repo; every file is pinned by SHA-256 in
config/rom.sha256 and any mismatch aborts.
"""
import argparse
import hashlib
import os
import pathlib
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_ROM = os.environ.get('NTMD_ROM', '/path/to/roms/turfmast.zip')


def load_hashes():
    hashes = {}
    for line in (REPO / 'config' / 'rom.sha256').read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        digest, name = line.split()
        hashes[name] = digest
    return hashes


def word_swap(data):
    """Undo MAME's ROM_LOAD16_WORD_SWAP: swap each 16-bit word's bytes."""
    out = bytearray(len(data))
    out[0::2], out[1::2] = data[1::2], data[0::2]
    return bytes(out)


def open_rom_source(path):
    """Return {name: bytes} for a zip archive or a directory of ROM files."""
    path = pathlib.Path(path)
    if path.is_dir():
        return {p.name: p.read_bytes()
                for p in sorted(path.iterdir()) if p.is_file()}
    if path.suffix == '.zip':
        with zipfile.ZipFile(path) as zf:
            return {name.split('/')[-1]: zf.read(name)
                    for name in zf.namelist() if not name.endswith('/')}
    raise SystemExit(f'unsupported ROM source: {path}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--roms', default=DEFAULT_ROM,
                    help='zip or directory holding the 200- files (default: $NTMD_ROM)')
    ap.add_argument('--out', default=str(REPO / 'build'))
    args = ap.parse_args(argv)

    src = pathlib.Path(args.roms)
    if not src.exists():
        raise SystemExit(f'ROM source not found: {src} (set NTMD_ROM or pass --roms)')

    expected = load_hashes()
    roms = open_rom_source(src)
    missing = sorted(set(expected) - set(roms))
    if missing:
        raise SystemExit(f'ROM source is missing files: {missing}')
    for name, want in expected.items():
        got = hashlib.sha256(roms[name]).hexdigest()
        if got != want:
            raise SystemExit(f'sha256 mismatch for {name}: got {got}, want {want}')

    out = pathlib.Path(args.out)
    for sub in ('work', 'assets', 'rom'):
        (out / sub).mkdir(parents=True, exist_ok=True)

    p1 = roms['200-p1.p1']
    cpu = word_swap(p1)
    fixed = cpu[0x100000:0x200000]   # file second half  -> CPU 0x000000
    bank = cpu[0x000000:0x100000]    # file first half   -> CPU 0x200000
    if fixed[0x100:0x107] != b'NEO-GEO':
        raise SystemExit('P-ROM header check failed (word-swap convention changed?)')

    (out / 'work' / 'p1.fixed.cpu.bin').write_bytes(fixed)  # pristine, CPU order
    (out / 'work' / 'p1.bank.cpu.bin').write_bytes(bank)
    (out / 'work' / 'p1.orig.bin').write_bytes(p1)
    (out / 'work' / 'p1.m1.pristine.bin').write_bytes(roms['200-m1.m1'])

    for name in expected:
        data = roms[name]
        (out / 'assets' / name).write_bytes(data)
        (out / 'rom' / name).write_bytes(data)

    print('extract ok: all nine files verified against config/rom.sha256')
    print(f'  fixed {len(fixed)} bytes at CPU 0x000000, bank {len(bank)} bytes at CPU 0x200000')
    print(f'  staged under {out}/')


if __name__ == '__main__':
    sys.exit(main())
