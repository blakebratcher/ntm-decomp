#!/usr/bin/env python3
"""Lane hole-play MAME driver: runs compiled scenarios headless.

Same isolation as tools/lane_modes2_drive.py (own cfg/nvram dirs, no
~/.mame, -video none -sound none, -nothrottle), with the hole-play runner
tools/lane_holeplay_run.lua.  Save states go to a directory shared by all
scenarios (build/analysis/lane-holeplay/states) so a scenario can LOAD a
checkpoint another one SAVEd.

Tap windows: the target windows of tools/lane_modes2_ranges.py (every
uncovered P1 byte and every `unref` row) plus EXTRA_WINDOWS, code that the
static pass found inside kind=data rows (to observe it executing).

Usage:
  python3 tools/lane_holeplay_drive.py DISCOVER [HOLE1 ...]
  python3 tools/lane_holeplay_drive.py --all
"""
import argparse
import os
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
ANALYSIS = REPO / 'build' / 'analysis' / 'lane-holeplay'
RUNS = ANALYSIS / 'runs'
SEQ_DIR = ANALYSIS / 'seq'
STATES = ANALYSIS / 'states'
BASE_WINDOWS = REPO / 'build' / 'analysis' / 'lane-modes2-windows.txt'
WINDOWS = ANALYSIS / 'windows.txt'
ROMDIR = REPO / 'build' / 'rom-set'
BIOS = os.environ.get('NEOGEO_BIOS')
RUN_LUA = REPO / 'tools' / 'lane_holeplay_run.lua'

# Executed code the static pass located inside kind=data rows (start, end).
EXTRA_WINDOWS = (
    (0xe558, 0xe7c2),     # root flow states, row p1_e4e8_palette_stream_strings
    (0x6a43e, 0x6a4a2),   # mode-select actor, row p1mid_data_6a3d8
    (0x60328, 0x603c0),   # per-player status actor, row p1mid_data_60282
    (0x6f0e2, 0x6f154),   # 2P round entry, row p1mid_data_6f064
    (0x71e1a, 0x71e40),   # round-end actor, row p1mid_data_7186e
    (0x7d2ce, 0x7d2e4),   # how-to-play actor, row p1mid_data_7cf10
    (0x6cdf2, 0x6ce84),   # eval-spawned actor, row p1mid_data_6cdf0
)


def write_windows():
    rows = []
    for line in BASE_WINDOWS.read_text().splitlines():
        p = line.split()
        if len(p) == 2:
            rows.append((int(p[0], 16), int(p[1], 16)))
    rows += list(EXTRA_WINDOWS)
    rows.sort()
    merged = []
    for s, e in rows:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    WINDOWS.parent.mkdir(parents=True, exist_ok=True)
    WINDOWS.write_text(''.join(f'{s:x} {e:x}\n' for s, e in merged))
    return len(merged)


def mame_cmd(d):
    if not BIOS:
        raise SystemExit('set NEOGEO_BIOS to your Neo Geo BIOS zip')
    for sub in ('cfg', 'nvram', 'snap', 'diff', 'ini', 'home'):
        (d / sub).mkdir(parents=True, exist_ok=True)
    STATES.mkdir(parents=True, exist_ok=True)
    return [
        'mame', 'turfmast',
        '-rompath', f'{ROMDIR};{pathlib.Path(BIOS).parent}',
        '-noreadconfig',
        '-video', 'none', '-sound', 'none',
        '-nothrottle', '-skip_gameinfo', '-nomouse', '-nojoystick',
        '-cfg_directory', str(d / 'cfg'),
        '-nvram_directory', str(d / 'nvram'),
        '-state_directory', str(STATES),
        '-snapshot_directory', str(d / 'snap'),
        '-diff_directory', str(d / 'diff'),
        '-inipath', str(d / 'ini'),
        '-homepath', str(d / 'home'),
        '-autoboot_script', str(RUN_LUA),
        '-autoboot_delay', '0',
        '-seconds_to_run', '7200',
    ]


def run_scenario(name, limit=None, state=False):
    seq = SEQ_DIR / f'{name}.seq'
    if not seq.exists():
        print(f'no seq for {name}; run: python3 tools/lane_holeplay_seq.py --compile {name}')
        return 1
    d = RUNS / name
    if d.exists():
        shutil.rmtree(d)
    (d / 'out').mkdir(parents=True)
    env = dict(os.environ, SDL_VIDEODRIVER='offscreen', SDL_AUDIODRIVER='dummy',
               LH_OUT=str(d / 'out'), LH_SEQ=str(seq), LH_RANGES=str(WINDOWS))
    if limit:
        env['LH_LIMIT'] = str(limit)
    if state:
        env['LH_STATE'] = '1'
    with open(d / 'mame.log', 'w') as log:
        r = subprocess.run(mame_cmd(d), env=env, stdout=log,
                           stderr=subprocess.STDOUT)
    lines = (d / 'mame.log').read_text(errors='replace').splitlines()
    keep = [ln for ln in lines if ln.startswith('LH')]
    print(f'=== {name} (rc {r.returncode}) ===')
    print('\n'.join(keep[-12:]))
    err = [ln for ln in lines if 'error' in ln.lower() or 'fatal' in ln.lower()]
    if err:
        print('errors:\n' + '\n'.join(err[-6:]))
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--state', action='store_true',
                    help='also write the per-frame state.tsv')
    ap.add_argument('scenarios', nargs='*')
    args = ap.parse_args()
    if not BASE_WINDOWS.exists():
        print('run first: python3 tools/lane_modes2_ranges.py')
        return 1
    print(f'{write_windows()} tap windows -> {WINDOWS}')
    names = args.scenarios
    if args.all:
        names = sorted(p.stem for p in SEQ_DIR.glob('*.seq'))
    rc = 0
    for name in names:
        rc |= run_scenario(name, args.limit, args.state)
    return rc


if __name__ == '__main__':
    sys.exit(main())
