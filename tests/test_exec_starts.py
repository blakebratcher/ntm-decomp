"""Executed instruction starts recorded under MAME must be code.

config/executed_starts.tsv is the committed, bounded list of CURPC values the
evidence lanes recorded (tools/exec_starts.py; hole-play, unibios, modes-3,
round-complete, sym-install).  A CURPC is the start of an instruction that
ran, so each one has to be an instruction start of a function or block
source - never a byte of a data / extracted / fill / unref row or legacy data
section, never an unclassified byte, never the middle of a decoded
instruction.  Accessed bytes are not in the list (an in-window data read
by an executing instruction is not execution; docs/lanes/hole-play.md).
"""
import json
import pathlib
import sys

import pytest

from conftest import requires_lane_docs

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools'))

import exec_starts as ES  # noqa: E402

SPANS = REPO / 'build' / 'p1-spans.json'
KNOWN_LANES = {'hole-play', 'unibios', 'modes-3', 'round-complete', 'sym-install'}


def test_table_wellformed_and_bounded():
    rows = [l for l in ES.TABLE.read_text().splitlines()
            if l.strip() and not l.startswith('#')]
    pcs = [int(r.split('\t')[0], 16) for r in rows]
    assert pcs == sorted(set(pcs)), 'rows must be sorted and unique'
    assert 500 <= len(pcs) <= 5000, len(pcs)
    for r in rows:
        pc, lanes = r.split('\t')
        assert ES.p1_cpu(int(pc, 16)), pc
        assert set(lanes.split(',')) <= KNOWN_LANES, r


@requires_lane_docs
def test_table_matches_the_committed_evidence():
    """The list is regenerated from lane evidence, not edited."""
    want = {pc: ','.join(sorted(tags)) for pc, tags in ES.collect().items()}
    assert ES.load_table() == want, 'run python3 tools/exec_starts.py --write'


def test_every_executed_start_is_a_code_instruction_start():
    if not SPANS.exists():
        pytest.skip('build/p1-spans.json missing (run make check)')
    problems = ES.classify(ES.load_table())
    assert not problems, '\n'.join(f'{pc:06x} {kind} {owners}' for pc, kind, owners in problems)


def test_classify_flags_noncode_unknown_and_misaligned(tmp_path):
    src = tmp_path / 'src' / 'p1' / 'asm' / 'functions'
    src.mkdir(parents=True)
    # 0x100: moveq #0,d0 (2) / dc.w $4e71 (2) / move.l #$12345678,d1 (6) / rts
    (src / '00000100.s').write_text(
        ' org $100\n        moveq #0,d0\n        dc.w $4e71\n'
        '        move.l #$12345678,d1\n        rts\n')
    spans = tmp_path / 'spans.json'
    spans.write_text(json.dumps({'sources': [
        {'org': 0x100, 'size': 12, 'category': 'function',
         'source': 'src/p1/asm/functions/00000100.s'},
        {'org': 0x200, 'size': 0x10, 'category': 'extracted',
         'source': 'build/gen/p1/regions/00000200_tbl.s'},
    ]}))
    got = {pc: kind for pc, kind, _o in ES.classify(
        {0x100: 'x', 0x102: 'x', 0x104: 'x', 0x106: 'x', 0x10a: 'x',
         0x204: 'x', 0x300: 'x'}, spans, root=tmp_path)}
    assert got == {0x102: 'dc-word', 0x106: 'mid-instruction',
                   0x204: 'non-code', 0x300: 'unknown'}
