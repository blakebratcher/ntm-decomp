#!/bin/sh
# Produce a reference Z80 disassembly of the M1 sound driver (on demand, not
# committed): unidasm from MAME 0.289, flat 128 KiB image.
# Usage: tools/m1_disasm.sh [OUTFILE]   (default: build/m1-disassembly.txt)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/.." && pwd)
OUT=${1:-$REPO/build/m1-disassembly.txt}
M1="$REPO/build/assets/200-m1.m1"
[ -f "$M1" ] || { echo "run 'make extract' first" >&2; exit 1; }
unidasm "$M1" -arch z80 -basepc 0x0 > "$OUT"
echo "wrote $OUT ($(wc -l < "$OUT") lines)"
