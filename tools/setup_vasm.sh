#!/bin/sh
# Build vasm 2.0f (vasmm68k_mot) from the pinned official snapshot.
# vasm is freeware by Volker Barthelmann (c) 2002-2026; see docs/TOOLING.md.
set -e

VASM_URL="http://sun.hasenbraten.de/vasm/release/vasm.tar.gz"
VASM_SHA256="c84b2de1cbb87831795fe64a85c5d9a7002a766e3a7c30b0a2d7d5e99d878f49"

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
WORK="$REPO/tools/vasm-src"
OUT="$REPO/tools/vasm"

mkdir -p "$WORK" "$OUT"
TAR="$WORK/vasm.tar.gz"

if [ ! -f "$TAR" ]; then
    echo "fetching $VASM_URL"
    curl -fsSL --max-time 120 -o "$TAR" "$VASM_URL"
fi

echo "$VASM_SHA256  $TAR" | sha256sum -c - >/dev/null || {
    echo "sha256 mismatch for the vasm snapshot (upstream moved? update the pin)" >&2
    exit 1
}

if [ ! -d "$WORK/src/vasm" ]; then
    mkdir -p "$WORK/src"
    tar -xzf "$TAR" -C "$WORK/src"
fi

if [ ! -x "$OUT/vasmm68k_mot" ]; then
    (cd "$WORK/src/vasm" && make CPU=m68k SYNTAX=mot) > "$WORK/build-m68k.log" 2>&1 || {
        tail -20 "$WORK/build-m68k.log" >&2
        echo "vasm m68k build failed (log: $WORK/build-m68k.log)" >&2
        exit 1
    }
    cp "$WORK/src/vasm/vasmm68k_mot" "$OUT/vasmm68k_mot"
fi

if [ ! -x "$OUT/vasmz80_std" ]; then
    (cd "$WORK/src/vasm" && make CPU=z80 SYNTAX=std) > "$WORK/build-z80.log" 2>&1 || {
        tail -20 "$WORK/build-z80.log" >&2
        echo "vasm z80 build failed (log: $WORK/build-z80.log)" >&2
        exit 1
    }
    cp "$WORK/src/vasm/vasmz80_std" "$OUT/vasmz80_std"
fi

echo "vasm ready at $OUT/ (vasmm68k_mot, vasmz80_std)"
