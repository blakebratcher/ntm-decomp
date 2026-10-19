#!/bin/sh
# Regenerate the Ghidra listing/decomp exports from the workspace ELF, into a
# caller-chosen directory (never into the companion RE workspace).
#
# Usage: tools/ghidra_export.sh OUTDIR
#   OUTDIR must not contain a dot-directory anywhere in its path (Ghidra rule).
#
# This mirrors the companion workspace's ghidra/run.sh, reading its ELF and
# analyzer scripts read-only. Ghidra 12.1.2 headless at /opt/ghidra is
# required. Outputs: OUTDIR/out/{asm_all,decomp_all,decomp,functions.tsv,...}
set -eu

GHIDRA=${GHIDRA:-/opt/ghidra}
WS=${NTM_WORKSPACE:?set NTM_WORKSPACE to the companion RE workspace}
OUT=${1:?usage: tools/ghidra_export.sh OUTDIR}

mkdir -p "$OUT"
cp "$WS/turfmast_prom.elf" "$OUT/"
"$GHIDRA/support/analyzeHeadless" "$OUT" turfmast \
  -import "$OUT/turfmast_prom.elf" -overwrite \
  -processor 68000:BE:32:default \
  -scriptPath "$WS/ghidra/scripts" \
  -preScript NeoGeoSeed.java \
  -postScript NeoGeoExpand.java \
  -postScript NeoGeoAll.java "$OUT/out" \
  -postScript NeoGeoExport.java "$OUT/out" > "$OUT/export.log" 2>&1
grep -E 'NeoGeo|ERROR' "$OUT/export.log" | head -20
echo "exports in $OUT/out (log: $OUT/export.log)"
