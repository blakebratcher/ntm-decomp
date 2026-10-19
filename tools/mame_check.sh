#!/bin/sh
# Headless MAME acceptance: boot the REBUILT ROM set (build/rom-set) and let
# the attract demo play, asserting the decoded boot milestones (game entry,
# title, attract start, first shot) via tools/mame_accept.lua + .py.
# Self-contained mirror of the companion workspace's harness pattern: no
# window, no sound, scratch cfg/nvram, never ~/.mame.
# Usage: tools/mame_check.sh [extra mame args...]
set -eu
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/.." && pwd)
ROMDIR="$REPO/build/rom-set"
[ -f "$ROMDIR/turfmast.zip" ] || { echo "run 'make rom' first" >&2; exit 1; }
BIOS=${NEOGEO_BIOS:?set NEOGEO_BIOS to your Neo Geo BIOS zip}
[ -f "$BIOS" ] || { echo "BIOS not found: $BIOS (set NEOGEO_BIOS)" >&2; exit 1; }

S=${MAME_SCRATCH:-/tmp/ntm-mame}
mkdir -p "$S"/cfg "$S"/nvram "$S"/state "$S"/snap "$S"/diff "$S"/ini "$S"/home
ACCEPT=${ACCEPT_OUT:-$S/accept}
mkdir -p "$ACCEPT"
rm -f "$ACCEPT/result.tsv"
export ACCEPT_OUT="$ACCEPT"
cd "$S"

env SDL_VIDEODRIVER=offscreen SDL_AUDIODRIVER=dummy xvfb-run -a mame turfmast \
  -rompath "$ROMDIR;$(dirname "$BIOS")" -noreadconfig \
  -video none -sound none -window -nomaximize -nothrottle -skip_gameinfo \
  -nomouse -nojoystick \
  -cfg_directory "$S/cfg" -nvram_directory "$S/nvram" -state_directory "$S/state" \
  -snapshot_directory "$S/snap" -diff_directory "$S/diff" \
  -inipath "$S/ini" -homepath "$S/home" \
  -autoboot_script "$REPO/tools/mame_accept.lua" -autoboot_delay 0 \
  -seconds_to_run 300 -log "$@"

python3 "$REPO/tools/mame_accept.py" "$ACCEPT/result.tsv"
