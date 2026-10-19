#!/usr/bin/env python3
"""Observe Z80 timer-IRQ writes without modifying emulated game state."""
import argparse
import csv
import hashlib
import json
import os
import pathlib
import statistics
import subprocess

import lane_holeplay_drive

REPO = pathlib.Path(__file__).resolve().parents[1]


def timer_period():
    value = (0xac << 2) | 3
    return (1024 - value) * 24 * 6 / 8000000


def summarize(path):
    with path.open() as stream:
        rows = list(csv.DictReader(stream, delimiter='\t'))
    times = [float(row['seconds']) for row in rows]
    if len(times) < 2:
        raise ValueError('at least two IRQ samples are required')
    intervals = [second - first for first, second in zip(times, times[1:])]
    if any(interval <= 0 for interval in intervals):
        raise ValueError('IRQ timestamps must be strictly increasing')
    median = statistics.median(intervals)
    return {'samples': len(times), 'total_span_seconds': times[-1] - times[0],
            'median_interval_seconds': median, 'median_rate_hz': 1 / median,
            'minimum_interval_seconds': min(intervals),
            'maximum_interval_seconds': max(intervals),
            'long_gaps': sum(interval > 2 * timer_period() for interval in intervals),
            'predicted_period_seconds': timer_period(),
            'predicted_rate_hz': 1 / timer_period(),
            'observed_pcs': sorted({row['pc'] for row in rows}),
            'qualification': 'Sampled emulator IRQ service timing, not silicon or audible acceptance.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=pathlib.Path,
                        default=REPO / 'build' / 'analysis' / 'semantic-m1-clock')
    parser.add_argument('--summarize-only', action='store_true')
    args = parser.parse_args()
    directory = args.out.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if not args.summarize_only:
        command = lane_holeplay_drive.mame_cmd(directory)
        command[command.index('-autoboot_script') + 1] = str(REPO / 'tools/semantic_m1_clock_probe.lua')
        command[command.index('-seconds_to_run') + 1] = '30'
        environment = dict(os.environ, SDL_VIDEODRIVER='offscreen', SDL_AUDIODRIVER='dummy',
                           M1_CLOCK_OUT=str(directory), M1_CLOCK_FRAMES='1200')
        with (directory / 'mame.log').open('w') as log:
            run = subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
        text = (directory / 'mame.log').read_text(errors='replace')
        if run.returncode or 'Fatal error' in text or 'runtime error' in text or 'M1_CLOCK DONE' not in text:
            raise SystemExit(f'MAME probe failed; inspect {directory / "mame.log"}')
    result = summarize(directory / 'irq.tsv')
    result['sha256'] = {}
    for path in (REPO / 'build' / '200-m1.m1', REPO / 'tools/semantic_m1_clock_probe.lua',
                 directory / 'irq.tsv', directory / 'mame.log'):
        key = str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)
        result['sha256'][key] = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / 'evidence.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
