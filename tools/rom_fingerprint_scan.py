#!/usr/bin/env python3
"""Cross-ROM fingerprint scan: find NTM code bytes in other local ROM sets.

Cuts byte windows out of the pristine P-ROM (CPU order) and searches every
`*-pN.pN` member of every zip in a ROM directory (word-swapped first, the
MAME file layout). Reports each hit plus the maximal equal byte run around
it, so shared library routines show up as long runs while coincidental
3-byte matches are filtered by --min-run.

Example (the 0x2908 unrolled divide is one shared routine):
  python3 tools/rom_fingerprint_scan.py --zips /path/to/roms \
      --window 2930:16 --min-run 256

Run 'make extract' first (reads build/work/p1.fixed.cpu.bin).
"""
import argparse
import pathlib
import re
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
PRISTINE = REPO / 'build' / 'work' / 'p1.fixed.cpu.bin'
MEMBER = re.compile(r'-p\d\.[a-z0-9]+$', re.I)


def word_swap(data):
    out = bytearray(len(data))
    out[0::2], out[1::2] = data[1::2], data[0::2]
    return bytes(out)


def equal_bounds(a, b, ia, ib, cap=0x2000):
    """Equal run endpoints around the supplied anchors (bounded per side)."""
    left = 0
    while ia - left - 1 >= 0 and ib - left - 1 >= 0 \
            and a[ia - left - 1] == b[ib - left - 1] and left < cap:
        left += 1
    right = 0
    while ia + right < len(a) and ib + right < len(b) \
            and a[ia + right] == b[ib + right] and right < cap:
        right += 1
    return ia - left, ia + right, ib - left, ib + right


def equal_run(a, b, ia, ib, cap=0x2000):
    start, end, _, _ = equal_bounds(a, b, ia, ib, cap)
    return end - start


def find_runs(source, target, addr, length, minimum):
    """Search an explicit source anchor, never its first repeated occurrence.

    Advance one byte: equal-run length includes bytes *before* the hit and
    cannot safely be used as a forward skip. Deduplicate identical mappings.
    """
    if addr < 0 or length <= 0 or addr + length > len(source):
        raise ValueError('window must be nonempty and inside the source ROM')
    window = source[addr:addr + length]
    seen = set()
    off = target.find(window)
    while off >= 0:
        bounds = equal_bounds(source, target, addr, off)
        if bounds[1] - bounds[0] >= minimum and bounds not in seen:
            seen.add(bounds)
            yield off, bounds
        off = target.find(window, off + 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--zips', required=True, help='directory of ROM zips')
    ap.add_argument('--window', action='append', required=True,
                    metavar='ADDR:LEN', help='fixed-window source: hex address '
                    'and decimal length, repeatable (e.g. 2930:16)')
    ap.add_argument('--min-run', type=int, default=24,
                    help='ignore hits whose equal run is shorter (default 24)')
    args = ap.parse_args()

    if not PRISTINE.exists():
        raise SystemExit(f'{PRISTINE} missing - run make extract first')
    blob = PRISTINE.read_bytes()
    windows = []
    for w in args.window:
        try:
            addr_s, len_s = w.split(':')
            addr, length = int(addr_s, 16), int(len_s)
        except ValueError:
            ap.error(f'invalid window: {w}; use hex-address:decimal-length')
        if addr < 0 or length <= 0 or addr + length > len(blob):
            ap.error(f'invalid window: {w}')
        windows.append((w, addr, length))

    hits = 0
    for zp in sorted(pathlib.Path(args.zips).glob('*.zip')):
        try:
            zf = zipfile.ZipFile(zp)
        except zipfile.BadZipFile:
            continue
        for name in [n for n in zf.namelist() if MEMBER.search(n)]:
            data = word_swap(zf.read(name))
            for label, addr, length in windows:
                for off, (a0, a1, b0, b1) in find_runs(
                        blob, data, addr, length, args.min_run):
                    hits += 1
                    capped = ' (search cap reached)' if addr-a0 == 0x2000 or a1-addr == 0x2000 else ''
                    print(f'{zp.stem:12s} {name:12s} window {label:9s} '
                          f'at member offset 0x{off:06x}  equal run {a1-a0} bytes '
                          f'source [0x{a0:x},0x{a1:x}) '
                          f'member [0x{b0:x},0x{b1:x}){capped}')
        zf.close()
    print(f'{hits} hit(s)')


if __name__ == '__main__':
    sys.exit(main())
