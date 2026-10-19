#!/usr/bin/env python3
"""Driver-family scan: compare an M1 against candidate M1s from other sets.

Usage: tools/m1_family_scan.py REF_M1 ZIP [ZIP ...]

For every zip, the `*.m1` member is scanned for the known driver signature
strings and then compared with REF_M1 by 16-byte-window sharing, both over
the whole file and over the driver code window (0x0000-0x6000). Cross-family
"sharing" inside padding or low-entropy data blocks is not driver kinship,
so the code-window column and the largest contiguous runs carry the signal.
"""
import pathlib
import sys
import zipfile

W = 16
CODE_WINDOW = 0x6000   # driver code lives below 0x6000 (docs/m1.md)
SIGS = (b'MAKOTO', b'Mr.Pac', b'Mr.Noric', b'To SNK', b'to SK',
        b'Sound Driver', b'M.C.')


def windows(data):
    return {data[i:i + W] for i in range(len(data) - W + 1)}


def shared_coverage(ref, other_windows, upto=None):
    """Covered-byte map of ref[0:upto] against other's window set."""
    upto = len(ref) if upto is None else min(upto, len(ref))
    cov = bytearray(len(ref))
    for i in range(upto - W + 1):
        if ref[i:i + W] in other_windows:
            for j in range(i, i + W):
                cov[j] = 1
    return sum(cov[:upto]), cov


def runs(cov, minlen=256):
    out, start = [], None
    for i, c in enumerate(cov):
        if c and start is None:
            start = i
        elif not c and start is not None:
            if i - start >= minlen:
                out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(cov)))
    return out


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    ref = pathlib.Path(sys.argv[1]).read_bytes()
    ref_w = windows(ref)
    for zip_path in sys.argv[2:]:
        with zipfile.ZipFile(zip_path) as zf:
            members = [n for n in zf.namelist() if n.lower().endswith('.m1')]
            if not members:
                print(f'{zip_path}: no .m1 member')
                continue
            data = zf.read(sorted(members)[0])
        sigs = [s.decode() for s in SIGS if s in data]
        whole, _ = shared_coverage(ref, windows(data))
        code, cov = shared_coverage(ref, windows(data), CODE_WINDOW)
        print(f'{pathlib.Path(zip_path).stem:10s} {sorted(members)[0]:12s} '
              f'sigs={sigs} shared: whole {100*whole/len(ref):5.1f}%, '
              f'code {100*code/CODE_WINDOW:5.1f}%')
        for s, e in sorted(runs(cov), key=lambda r: r[1] - r[0],
                           reverse=True)[:3]:
            print(f'    code run 0x{s:05x}-0x{e:05x} ({e - s} bytes)')


if __name__ == '__main__':
    main()
