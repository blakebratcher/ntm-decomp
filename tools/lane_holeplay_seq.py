#!/usr/bin/env python3
"""Scenario compiler for lane hole-play (natural-input play past the
hole-advance gate).

The step vocabulary is the one tools/lane_holeplay_run.lua implements.  It
has NO RAM-write step: scenarios reach every state through the coin, start
and joystick ports only.  The boot recipe (`natural_start`) is the inputs-
only path the static analysis verified: coin -> 1P start -> skip the
how-to-play demo with A -> mode select (1P) -> golfer -> course -> live tee
(root actor 0x100160 == 0xebb2).

Usage:
  python3 tools/lane_holeplay_seq.py --list
  python3 tools/lane_holeplay_seq.py --compile ALL
  python3 tools/lane_holeplay_seq.py --compile DISCOVER --out-dir DIR
"""
import argparse
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = REPO / 'build' / 'analysis' / 'lane-holeplay' / 'seq'

J1 = ':edge:joy:JOY1'
J2 = ':edge:joy:JOY2'
START = ':edge:joy:START'
COIN = ':AUDIO_COIN'

ROOT = 0x100160          # root flow actor state PC
LIVE_TEE = 0xebb2        # root state while a hole is being played

# ---- step constructors ----------------------------------------------------
# Each returns a tuple; compile_steps() turns them into seq lines.


def MARK(name):
    return ('MARK', name)


def SLEEP(n):
    return ('SLEEP', int(n))


def LIMIT(n):
    return ('LIMIT', int(n))


def PROBE(n):
    """Log the power/accuracy meter actor fields for n frames (parallel)."""
    return ('PROBE', int(n))


def PRESS(tag, field, dur=4):
    return ('PRESS', tag, field, int(dur))


def HOLD(tag, field, n):
    return ('HOLD', tag, field, int(n))


def WAIT(addr, mask, value, timeout):
    return ('WAIT', addr, mask, value, int(timeout))


def WAITW(addr, value, timeout):
    return ('WAITW', addr, value, int(timeout))


def WAITL(addr, value, timeout):
    return ('WAITL', addr, value, int(timeout))


def SAVE(name):
    return ('SAVE', name)


def LOAD(name):
    return ('LOAD', name)


def TAPS(count, every):
    return ('TAPS', int(count), int(every))


def PLAY(holes, frames, **opts):
    opts = {k.rstrip('_'): v for k, v in opts.items()}
    for k, v in opts.items():
        if not k.isidentifier() or any(c in str(v) for c in '\t= '):  # noqa
            raise ValueError(f'bad PLAY option {k}={v!r}')
    return ('PLAY', int(holes), int(frames), tuple(sorted(opts.items())))


def WATCH(addr):
    return ('WATCH', addr)


def WATCHW(addr):
    return ('WATCHW', addr)


def WATCHL(addr):
    return ('WATCHL', addr)


def WTAP(addr):
    return ('WTAP', addr)


def LMARK(addr):
    return ('LMARK', addr)


KINDS = {'MARK', 'SLEEP', 'LIMIT', 'PROBE', 'PRESS', 'HOLD', 'WAIT', 'WAITW', 'WAITL',
         'SAVE', 'LOAD', 'TAPS', 'PLAY', 'WATCH', 'WATCHW', 'WATCHL', 'WTAP',
         'LMARK'}
DECLS = {'WATCH', 'WATCHW', 'WATCHL', 'WTAP', 'LMARK'}


def _hex(v):
    if not isinstance(v, int) or v < 0:
        raise ValueError(f'bad address/value {v!r}')
    return f'{v:x}'


def compile_steps(steps):
    """Return the seq text: declarations first, then the steps in order."""
    decl, body = [], []
    for st in steps:
        k = st[0]
        if k not in KINDS:
            raise ValueError(f'unknown step kind {k!r}')
        if k in ('WATCH', 'WATCHW', 'WATCHL', 'WTAP', 'LMARK'):
            decl.append(f'{k}\t{_hex(st[1])}')
        elif k in ('MARK', 'SAVE', 'LOAD'):
            if not st[1] or '\t' in st[1] or ' ' in st[1]:
                raise ValueError(f'bad name {st[1]!r}')
            body.append(f'{k}\t{st[1]}')
        elif k in ('SLEEP', 'LIMIT', 'PROBE'):
            body.append(f'{k}\t{st[1]}')
        elif k in ('PRESS', 'HOLD'):
            body.append(f'{k}\t{st[1]}\t{st[2]}\t{st[3]}')
        elif k == 'WAIT':
            body.append(f'WAIT\t{_hex(st[1])}\t{_hex(st[2])}\t{_hex(st[3])}\t{st[4]}')
        elif k in ('WAITW', 'WAITL'):
            body.append(f'{k}\t{_hex(st[1])}\t{_hex(st[2])}\t{st[3]}')
        elif k == 'TAPS':
            body.append(f'TAPS\t{st[1]}\t{st[2]}')
        elif k == 'PLAY':
            opts = ''.join(f'\t{a}={b}' for a, b in st[3])
            body.append(f'PLAY\t{st[1]}\t{st[2]}{opts}')
    return '\n'.join(decl + body) + '\n'


# ---- instrumentation ------------------------------------------------------
# Byte watches (value-change log).
GATE_BYTES = (
    0x10ebbe,  # phase byte (5 screens, 0x15 golf)
    0x102ebb,  # attract-demo index (0 = live game)
    0x102eba,  # play mode (0xff unset, 0 = 1P, 1/2 = 2P)
    0x102eb9,  # start_join_lock
    0x10fdae,  # BIOS_USER_REQUEST
    0x10fdaf,  # BIOS_USER_MODE
    0x10fdb6,  # BIOS_PLAYER_MOD1
    0x10fdb7,  # BIOS_PLAYER_MOD2
    0x10fec5,  # BIOS_TITLE_MODE
    0x100002,  # demo-cycle counter
    0x102e58,  # challenger_request (challenger re-entry request)
    0x107005,  # P1 golfer id
    0x10702b,  # P2 golfer id
    0x107006,  # P1 strokes
    0x10702c,  # P2 strokes
    0x107026,  # P1 record +0x22 (1 holed, 0xff rank fail)
    0x10704c,  # P2 record +0x22
    0x107083,  # ball_holed_flag (hole-out)
    0x107085,  # shot_end_code
    0x10d4ed,  # shot_result_flag
    0x10d4b9,  # course index lo
    0x10d4bd,  # hole number lo
    0x106fec,  # swing_lock_flag
    0x10ebe4,  # accuracy result
    0x106fe7,  # power byte
)
WORD_WATCH = (
    0x106fde,  # club_bag_index
    0x10d4aa,  # target distance x4
    0x106fe8,  # accuracy meter position
)
LONG_WATCH = (
    ROOT,      # root flow actor state PC
    0x102eb0,  # resume_hook
    0x10707a,  # hole_record_ptr
)
# Shot-state longs, only for the DISCOVER-style scenarios (they change
# every frame in flight).
SHOT_LONGS = (0x102c68, 0x102c6c, 0x10d4c6, 0x10d4ca, 0x102cbc, 0x102cc0)
# Bytes whose writers are logged with their PC from power-on.
WTAPS = (0x102ebb, 0x102eba, 0x107085, 0x107026, 0x10704c, 0x10d4bd,
         0x102e58, 0x10fdb6, 0x10fdb7, 0x100002, 0x10d4ed, 0x107083)
# Instruction landmarks (counted when executed).
LANDMARKS = (
    0xe5b6, 0xe6aa, 0xe72c, 0xe7a8, 0xe828,   # 0x102ebb writers
    0xe2c2, 0xe32c,                           # PLAYER_START P1/P2
    0x6a69a,                                  # play mode commit
    0xbc78,                                   # ball_hole_out_event
    0x6cda2,                                  # player rank check
    0x6c0da, 0x6c11e, 0x6c276,                # hole_over writes
    0x6d4b0, 0x6d55e, 0x6d686, 0x6d666,       # scene driver / result actors
    0x6cff4,                                  # next-hole transition actor
    0x58aee, 0x58660,                         # hole_complete_check, hole++
    0xeca4, 0xed08, 0xedbc, 0xed94, 0xee70,   # scorecard / continue / end
    0xeb2e,                                   # hole_start_continue
    0xef14, 0xef6a,                           # round end spawn, exit_to_bios
    0xd6ce,                                   # challenger re-entry
    0x7b398,                                  # score_pose_screen_actor
    0x75898,                                  # scorecard_screen_init
    0x73b72, 0x7ce5a,                         # scorecard fade, continue scr
    0x71e1a, 0x85534,                         # round-end actors
)


def instrumentation():
    return ([WATCH(a) for a in GATE_BYTES] + [WATCHW(a) for a in WORD_WATCH]
            + [WATCHL(a) for a in LONG_WATCH] + [WTAP(a) for a in WTAPS]
            + [LMARK(a) for a in LANDMARKS])


# ---- the inputs-only boot recipe -----------------------------------------

def natural_start(players=1, golfer_moves=0, course_moves=0, course_dirs=None):
    """Boot -> attract -> coin -> start -> A taps through the how-to-play
    skip, mode select, golfer select and course select, until the root
    actor reaches the live tee (0xebb2).

    golfer_moves / course_moves: P1 Right presses on the golfer and course
    select screens before the confirming tap (0 = the defaults).
    course_dirs: an explicit list of direction field names ('P1 Down', ...)
    pressed on course select instead of the Right/Down/Left/Up walk
    (used by the r6-tee hunt to reach G.CANYON with one P1 Down)."""
    s = [MARK('boot'),
         WAIT(0x10ebbe, 0xff, 0x05, 3000),
         MARK('game-up'),
         SLEEP(60),
         PRESS(COIN, 'Coin 1', 6)]
    if players == 2:
        s += [SLEEP(30), PRESS(COIN, 'Coin 1', 6)]
    s += [MARK('coined'), SLEEP(180),
          PRESS(START, '1 Player Start', 6)]
    if players == 2:
        s += [SLEEP(20), PRESS(START, '2 Players Start', 6)]
    s += [MARK('started'), SLEEP(300),
          # A#1 skips the how-to-play demo -> mode select
          PRESS(J1, 'P1 A', 4), MARK('howto-skipped'),
          WAITL(ROOT, 0xe884, 600), SLEEP(100),
          # A#2 commits the play mode
          PRESS(J1, 'P1 A', 4), MARK('mode-committed')]
    if players == 2:
        s += [SLEEP(10), PRESS(J2, 'P2 A', 4)]
    s += [WAITL(ROOT, 0xe958, 900), SLEEP(40), MARK('golfer-select')]
    for _ in range(golfer_moves):
        s += [PRESS(J1, 'P1 Right', 4), SLEEP(20)]
    s += [PRESS(J1, 'P1 A', 4)]
    if players == 2:
        s += [SLEEP(10), PRESS(J2, 'P2 A', 4)]
    s += [MARK('golfer-picked'), WAITL(ROOT, 0xe9e2, 900), SLEEP(60),
          MARK('course-select')]
    # select_cursor_input ($6b236) sets two selection bits; repeated
    # Right only sets bit 1. Walk the four corners in screen order.
    # Give select_intro_state time to spawn the cursor before steering.
    if course_moves or course_dirs:
        s += [SLEEP(600)]
    if course_dirs is None:
        course_dirs = [('P1 Right', 'P1 Down', 'P1 Left', 'P1 Up')[i % 4]
                       for i in range(course_moves)]
    for direction in course_dirs:
        s += [PRESS(J1, direction, 4), SLEEP(30)]
    s += [PRESS(J1, 'P1 A', 4), MARK('course-picked'),
          WAITL(ROOT, LIVE_TEE, 3000), MARK('live-tee')]
    return s


# ---- scenarios ------------------------------------------------------------
SCENARIOS = {}


def scenario(fn):
    SCENARIOS[fn.__name__.upper()] = fn
    return fn


@scenario
def discover():
    """Reach the tee, then observe the shot protocol: idle, A, A."""
    return (instrumentation() + [WATCHL(a) for a in SHOT_LONGS]
            + natural_start()
            + [PROBE(1000), SLEEP(600), MARK('a1'), PRESS(J1, 'P1 A', 4), SLEEP(60),
               MARK('a2'), PRESS(J1, 'P1 A', 4), SLEEP(5200), MARK('end'),
               LIMIT(9000)])


@scenario
def hole1():
    """Play course 1 hole 1 to the cup with the natural-input player and
    follow the game to the next tee."""
    return (instrumentation() + natural_start()
            + [MARK('play'), PLAY(1, 30000), SLEEP(600), MARK('end'),
               LIMIT(40000)])


@scenario
def round18():
    """A full 18-hole 1P round on the default course, continuing on the
    continue prompt, then the round end and the return to the BIOS."""
    return (instrumentation() + natural_start()
            + [MARK('play'), PLAY(99, 250000, stop_root='e5ce'),
               MARK('after'), SLEEP(3000), MARK('end'), LIMIT(260000)])


@scenario
def green():
    """Play to the first putt address, save the state 'green', and probe
    the putting controls (actor list, meter) under A presses."""
    return (instrumentation() + [WATCHL(a) for a in SHOT_LONGS]
            + natural_start()
            + [MARK('play'), PLAY(1, 20000, stop_putt=1), MARK('green'),
               SAVE('green'), PROBE(2400), SLEEP(300), MARK('pA1'),
               PRESS(J1, 'P1 A', 4), SLEEP(300), MARK('pA2'),
               PRESS(J1, 'P1 A', 4), SLEEP(300), MARK('pA3'),
               PRESS(J1, 'P1 A', 4), SLEEP(1500), MARK('end'),
               LIMIT(30000)])


def _round(course_moves=0, golfer_moves=0, players=1, **opts):
    opts.setdefault('stop_root', 'e5ce')
    if players == 2:
        opts['twop'] = 1
    return (instrumentation()
            + natural_start(players=players, golfer_moves=golfer_moves,
                            course_moves=course_moves)
            + [MARK('play'), PLAY(99, 320000, **opts),
               MARK('after'), SLEEP(3000), MARK('end'), LIMIT(340000)])


@scenario
def csel():
    """Course select probe: one P1 Right on the course and golfer screens."""
    return (instrumentation() + natural_start(golfer_moves=1, course_moves=1)
            + [SLEEP(300), MARK('end'), LIMIT(8000)])


@scenario
def cam_tee():
    """r6-tee hunt baseline: G.CANYON (P1 Down on course select), hole 1,
    one overshooting shot toward the top line."""
    return (instrumentation() + natural_start(course_dirs=['P1 Down'])
            + [MARK('play'), PLAY(1, 9000, power_scale='1.3'), SLEEP(600),
               MARK('end'), LIMIT(16000)])


@scenario
def round_c2():
    """Full 1P round on the second course (one Right on course select)."""
    return _round(course_moves=1, golfer_moves=1)


@scenario
def round_c3():
    """Full 1P round at the third course-selection corner."""
    return _round(course_moves=2, golfer_moves=2)


@scenario
def round_c4():
    """Full 1P round at the fourth course-selection corner."""
    return _round(course_moves=3, golfer_moves=3)


@scenario
def erratic():
    """Full round on the third course with the accuracy lock cycling
    through off-centre positions (hooks, slices, mis-hits): more hazards,
    trees and out-of-bounds outcomes than the straight player."""
    return _round(course_moves=2, golfer_moves=4, acc_list='17,11,23,9,25,17,28')


@scenario
def gameover():
    """1P game without continues: the continue countdown expires, game
    over, round end and the return to the BIOS."""
    return _round(continue_=0)


@scenario
def rankfail():
    """Deliberately short full shots (power x0.3): strokes > par+3 sets
    the record +0x22 = 0xff (rank fail); no continues."""
    return _round(power_scale='0.3', continue_=0)


@scenario
def twop():
    """Two-player game (both players coined in), continues for both."""
    return _round(players=2, max_continues=6)


@scenario
def twop_restart():
    """Two-player game with unlimited continues until the scorecard
    continue prompt (0xedbc) sends both players back to char select."""
    return (instrumentation() + natural_start(players=2)
            + [MARK('play'), PLAY(99, 260000, twop=1, stop_root='e958'),
               MARK('restart'), SLEEP(3000), MARK('end'), LIMIT(270000)])


@scenario
def challenger():
    """A second player joins a running 1P game (coin + 2 Players Start on
    hole 2): the 'here comes a challenger' re-entry, then a 2P game."""
    return (instrumentation() + natural_start()
            + [MARK('play1p'), PLAY(1, 30000), WAITL(ROOT, 0xed08, 3000),
               WAITL(ROOT, LIVE_TEE, 3000),
               SLEEP(60), MARK('challenger'), PRESS(COIN, 'Coin 1', 6),
               SLEEP(60),
               PRESS(START, '2 Players Start', 6), SLEEP(120),
               MARK('play2p'),
               PLAY(99, 200000, twop=1, max_continues=4, stop_root='e5ce'),
               MARK('after'), SLEEP(3000), MARK('end'), LIMIT(260000)])


@scenario
def howto():
    """Watch the how-to-play demo after START without skipping it, then
    play one hole."""
    return (instrumentation()
            + [MARK('boot'), WAIT(0x10ebbe, 0xff, 0x05, 3000), SLEEP(60),
               PRESS(COIN, 'Coin 1', 6), SLEEP(180),
               PRESS(START, '1 Player Start', 6), MARK('howto'),
               WAITL(ROOT, 0xe7f4, 20000), MARK('howto-over'), SLEEP(100),
               PRESS(J1, 'P1 A', 4), WAITL(ROOT, 0xe958, 2000), SLEEP(40),
               PRESS(J1, 'P1 A', 4), WAITL(ROOT, 0xe9e2, 2000), SLEEP(60),
               PRESS(J1, 'P1 A', 4), WAITL(ROOT, LIVE_TEE, 3000),
               MARK('play'), PLAY(1, 20000), SLEEP(600), MARK('end'),
               LIMIT(60000)])


def compile_scenario(name, out_dir):
    text = compile_steps(SCENARIOS[name]())
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f'{name}.seq'
    path.write_text(text)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--compile', nargs='*', default=None)
    ap.add_argument('--out-dir', default=str(OUT_DIR))
    args = ap.parse_args(argv)
    if args.list:
        for name in sorted(SCENARIOS):
            doc = (SCENARIOS[name].__doc__ or '').strip().splitlines()[0]
            print(f'{name:14s} {doc}')
        return 0
    names = args.compile or ['ALL']
    if names == ['ALL']:
        names = sorted(SCENARIOS)
    for name in names:
        if name not in SCENARIOS:
            print(f'unknown scenario {name}', file=sys.stderr)
            return 1
        print(f'compiled {name}: {compile_scenario(name, args.out_dir)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
