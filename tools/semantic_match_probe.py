#!/usr/bin/env python3
"""Read-only register probe during an inputs-only two-player match."""
import argparse
import csv
import hashlib
import json
import os
import pathlib
import subprocess

import lane_holeplay_drive
import lane_holeplay_seq

REPO = pathlib.Path(__file__).resolve().parents[1]


def scenario_text():
    steps = lane_holeplay_seq.instrumentation() + lane_holeplay_seq.natural_start(players=2)
    steps += [lane_holeplay_seq.MARK('play'), lane_holeplay_seq.PLAY(2, 60000, twop=1),
              lane_holeplay_seq.SLEEP(1500), lane_holeplay_seq.LIMIT(68000)]
    return lane_holeplay_seq.compile_steps(steps)


def summarize(path):
    with path.open() as stream:
        rows = list(csv.DictReader(stream, delimiter='\t'))
    samples = [{key: int(value, 16) for key, value in row.items()} for row in rows]
    entries = [row for row in samples if row['pc'] == 0x6cb30]
    return {'samples': samples, 'actor_entry_samples': len(entries),
            'stale_player_pointer_observed': any(row['a1'] == 0x7313 for row in entries),
            'qualification': 'Only sampled executions are confirmed; absence is not unreachability.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=pathlib.Path,
                        default=REPO / 'build' / 'analysis' / 'semantic-match')
    parser.add_argument('--summarize-only', action='store_true')
    args = parser.parse_args()
    directory = args.out.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if not args.summarize_only:
        (directory / 'scenario.seq').write_text(scenario_text())
        (directory / 'windows.txt').write_text('6c93e 6cbde\n')
        command = lane_holeplay_drive.mame_cmd(directory)
        command[command.index('-autoboot_script') + 1] = str(REPO / 'tools/semantic_match_probe.lua')
        environment = dict(os.environ, SDL_VIDEODRIVER='offscreen', SDL_AUDIODRIVER='dummy',
                           LH_OUT=str(directory / 'out'), LH_SEQ=str(directory / 'scenario.seq'),
                           LH_RANGES=str(directory / 'windows.txt'), LH_LIMIT='68000',
                           SEMANTIC_GAME_RUNNER=str(REPO / 'tools/lane_holeplay_run.lua'))
        with (directory / 'mame.log').open('w') as log:
            run = subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
        text = (directory / 'mame.log').read_text(errors='replace')
        if run.returncode or 'Fatal error' in text or 'runtime error' in text or 'DONE frames=' not in text:
            raise SystemExit(f'MAME scenario failed; inspect {directory / "mame.log"}')
    result = summarize(directory / 'out' / 'match-registers.tsv')
    result['sha256'] = {}
    for path in (REPO / 'build' / 'p1.p1', directory / 'scenario.seq',
                 REPO / 'tools/semantic_match_probe.lua', directory / 'mame.log',
                 directory / 'out' / 'match-registers.tsv'):
        result['sha256'][str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)] = (
            hashlib.sha256(path.read_bytes()).hexdigest())
    (directory / 'evidence.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
