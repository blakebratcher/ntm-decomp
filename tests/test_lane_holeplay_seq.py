"""Lane hole-play: seq compiler, the no-RAM-write guarantee, evidence model."""
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))

import lane_holeplay_agg as agg  # noqa: E402
import lane_holeplay_seq as seq  # noqa: E402

RUN_LUA = REPO / 'tools' / 'lane_holeplay_run.lua'


def test_declarations_come_first():
    text = seq.compile_steps([seq.MARK('a'), seq.WATCH(0x102ebb), seq.SLEEP(5),
                              seq.WTAP(0x102ebb), seq.LMARK(0x58660),
                              seq.LIMIT(10)])
    assert text.splitlines() == ['WATCH\t102ebb', 'WTAP\t102ebb',
                                 'LMARK\t58660', 'MARK\ta', 'SLEEP\t5',
                                 'LIMIT\t10']


def test_step_formats():
    lines = seq.compile_steps([
        seq.PRESS(seq.J1, 'P1 A', 4),
        seq.WAIT(0x10ebbe, 0xff, 0x05, 3000),
        seq.WAITL(seq.ROOT, seq.LIVE_TEE, 900),
        seq.PLAY(18, 1000, continue_=0, stop_root='e5ce'),
        seq.SAVE('tee'), seq.TAPS(3, 60), seq.PROBE(20),
    ]).splitlines()
    assert lines[0] == ':edge:joy:JOY1'.join(['PRESS\t', '\tP1 A\t4'])
    assert lines[1] == 'WAIT\t10ebbe\tff\t5\t3000'
    assert lines[2] == 'WAITL\t100160\tebb2\t900'
    # option names lose the trailing underscore and are sorted
    assert lines[3] == 'PLAY\t18\t1000\tcontinue=0\tstop_root=e5ce'
    assert lines[4:] == ['SAVE\ttee', 'TAPS\t3\t60', 'PROBE\t20']


def test_bad_inputs_rejected():
    with pytest.raises(ValueError):
        seq.compile_steps([('WRITE', 0x102ebb, 0)])
    with pytest.raises(ValueError):
        seq.compile_steps([seq.MARK('two words')])
    with pytest.raises(ValueError):
        seq.PLAY(1, 10, bad='a=b')
    with pytest.raises(ValueError):
        seq.compile_steps([seq.WATCH(-1)])


@pytest.mark.parametrize('name', sorted(seq.SCENARIOS))
def test_every_scenario_compiles_without_ram_writes(name, tmp_path):
    path = seq.compile_scenario(name, tmp_path)
    kinds = {ln.split('\t')[0] for ln in path.read_text().splitlines()}
    assert kinds <= seq.KINDS
    assert not kinds & {'WRITE', 'WRITEW', 'HOLDW', 'HOLDWW', 'HOLDS',
                        'HOLDN', 'HOLDNW', 'COPY32'}
    assert 'LIMIT' in kinds


def test_natural_start_walks_the_select_screens():
    steps = seq.natural_start()
    waits = [st[2] for st in steps if st[0] == 'WAITL']
    assert waits == [0xe884, 0xe958, 0xe9e2, seq.LIVE_TEE]
    presses = [st[2] for st in steps if st[0] == 'PRESS']
    assert presses[:2] == ['Coin 1', '1 Player Start']
    assert presses.count('P1 A') == 4
    two = [st[2] for st in seq.natural_start(players=2) if st[0] == 'PRESS']
    assert two.count('Coin 1') == 2 and '2 Players Start' in two
    assert 'P2 A' in two


def test_runner_never_writes_memory():
    """The no-poke guarantee: the runner Lua has no memory write call, and
    its write taps never return a replacement value."""
    src = RUN_LUA.read_text()
    code = '\n'.join(ln.split('--')[0] for ln in src.splitlines())
    assert not re.search(r'mem:write|write_u(8|16|32|64)|write_i(8|16|32)|write_direct', code)
    assert 'install_write_tap' in code


def test_classify_and_intervals():
    s, e = 0x6a780, 0x6a810
    # an instruction inside the range was executing: the range ran
    assert agg.classify(0x6a784, {0x6a780}, s, e) == 'executed'
    # only the instruction before the range: prefetch, not evidence
    assert agg.classify(0x6a780, {0x6a77c}, s, e) == 'prefetch'
    assert agg.classify(0x6a782, {0x6a77c, 0x6a77e}, s, e) == 'prefetch'
    # a far PC reads it: data
    assert agg.classify(0x6a790, {0x1234}, s, e) == 'read'
    assert agg.intervals([5, 1, 2, 3, 7]) == [[1, 4], [5, 6], [7, 8]]


def test_course_moves_visit_four_distinct_slots():
    """Course input sets two bits (6b236); Right alone cannot cycle slots.

    Use the cursor's bit effects, established by the four live corner
    probes, to check the recipe's result rather than its exact step list.
    """
    slots = []
    for moves in range(4):
        steps = seq.natural_start(course_moves=moves)
        start = steps.index(seq.MARK('course-select')) + 1
        end = steps.index(seq.MARK('course-picked'))
        slot = 0
        for step in steps[start:end]:
            if step[0] != 'PRESS':
                continue
            if step[2] == 'P1 Up':
                slot &= 2
            elif step[2] == 'P1 Down':
                slot |= 1
            elif step[2] == 'P1 Left':
                slot &= 1
            elif step[2] == 'P1 Right':
                slot |= 2
        slots.append(slot)
    assert set(slots) == {0, 1, 2, 3}, slots
