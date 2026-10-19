"""Closed-world reachability (tools/reach.py) must stay closed and consistent."""
import pathlib
import re
import struct
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))

pytestmark = pytest.mark.skipif(
    not (REPO / 'src').is_dir()
    or not (REPO / 'build' / 'work' / 'p1.fixed.cpu.bin').exists(),
    reason='source corpus and build/p1.fixed.cpu.bin required')


@pytest.fixture(scope='module')
def world():
    import reach
    insns = reach._cached_corpus()
    targets = reach.load_targets()
    seen, open_sites, bad = reach.closure(insns, targets)
    return reach, insns, targets, seen, open_sites, bad, reach.closure.raw


def test_closure_is_closed(world):
    reach, insns, targets, seen, open_sites, bad, raw = world
    assert not open_sites, [hex(a) for a in sorted(open_sites)]
    assert not reach.roots.bad, [hex(a) for a in reach.roots.bad]
    assert not bad, [hex(a) for a in sorted(bad)]
    assert not raw, f'reachable starts held as dc.w: {[hex(a) for a in sorted(raw)]}'


def test_every_executed_start_is_reached(world):
    reach, insns, targets, seen, *_ = world
    missed = sorted(a for a in reach.executed() if a in insns and a not in seen)
    assert not missed, [hex(a) for a in missed]


def test_target_rows_match_the_indirect_sites(world):
    reach, insns, targets, *_ = world
    sites = {a for a, (size, text, src) in insns.items() if reach.INDIRECT.match(text)}
    assert set(targets) == sites, (sorted(hex(a) for a in set(targets) - sites),
                                   sorted(hex(a) for a in sites - set(targets)))


def test_windows_hold_no_dead_start(world):
    """A table site bounded only by its index register: no long in its full
    window (low 24 bits, as the 68000 decodes jmp (An)) may be dead code."""
    reach, insns, targets, seen, *_ = world
    dead = {a for a in insns if a not in seen}
    blob = reach.FIXED.read_bytes()
    hits = []
    for site, (kind, vals, window, evidence) in targets.items():
        if window == '-':
            continue
        lo, hi = (int(x, 16) for x in window.split('-'))
        for off in range(lo, hi - 3, 2):
            v = struct.unpack_from('>I', blob, off)[0] & 0xffffff
            if v in dead:
                hits.append((hex(site), hex(off), hex(v)))
    assert not hits, hits


def test_no_live_operand_names_dead_code(world):
    """No reached instruction carries a dead instruction start as an operand
    (immediate, absolute, PC-relative or branch target)."""
    reach, insns, targets, seen, *_ = world
    dead = {a for a in insns if a not in seen}
    hits = [(hex(a), t) for a, (size, t, src) in insns.items() if a in seen
            for v in reach.operand_addresses(a, t) if v in dead]
    assert not hits, hits[:10]


def test_dead_and_live_agree_with_the_closure(world):
    """dead-insn sources and declared dead ranges are exactly the unreached code."""
    import re as _re
    reach, insns, targets, seen, *_ = world
    by_src = {}
    for a, (size, text, src) in insns.items():
        by_src.setdefault(src, []).append(a)
    wrong = []
    for src, starts in sorted(by_src.items()):
        text = (REPO / src).read_text()
        dead_src = '; kind: dead-insn' in text
        ranges = [(int(a, 16), int(b, 16)) for a, b in
                  _re.findall(r'^; dead-range: \[\$([0-9a-f]+), \$([0-9a-f]+)\)', text, _re.M)]
        for a in starts:
            declared = dead_src or any(lo <= a < hi for lo, hi in ranges)
            if declared and a in seen:
                wrong.append(f'{src}: {a:#x} is declared dead but reached')
            if not declared and a not in seen:
                wrong.append(f'{src}: {a:#x} is unreached but not declared dead')
    assert not wrong, wrong[:20]


def test_no_live_address_operand_points_into_dead_data(world):
    """Dead tables: no reached instruction forms an address inside them
    (absolute or PC-relative EA, lea/pea, (d8,PC,Xn) base)."""
    import gen_regions
    reach, insns, targets, seen, *_ = world
    dead = [(r['start'], r['start'] + r['size'], r['name'])
            for r in gen_regions.load('p1') if r['kind'] == 'dead']
    hits = []
    for a, (size, t, src) in insns.items():
        if a not in seen:
            continue
        for v in reach.operand_addresses(a, t):
            for lo, hi, name in dead:
                if lo <= v < hi:
                    hits.append((hex(a), t, name))
    assert not hits, hits[:10]


def test_operand_resolver_sees_table_bases_and_address_immediates():
    import reach
    # movea.l ($4,PC,D3.w),A4 at 0x7e0c reads the table at 0x7e12 (d8 is raw)
    assert reach.operand_addresses(0x7e0c, 'movea.l ($4,PC,D3.w), A4') == [0x7e12]
    assert 0x59d10 in reach.operand_addresses(0x59c46, 'movea.l #$59d10, A0')
    assert reach.operand_addresses(0x1000, 'cmpi.w #$4a00, D0') == []


def test_complementary_branch_pair_never_falls_through():
    import reach
    insns = {0x10: (4, 'bcs     $20', 's'), 0x14: (4, 'bcc     $24', 's'),
             0x18: (2, 'nop', 's'), 0x20: (2, 'rts', 's'), 0x24: (2, 'rts', 's')}
    assert reach.complement_pairs(insns) == {0x14}
    # a second branch that something else enters may still fall through
    insns[0x30] = (2, 'bra     $14', 's')
    assert reach.complement_pairs(insns) == set()


def test_negative_pc_index_displacement_resolves_backwards():
    import reach
    # unidasm prints a negative d8 as -$xx: base = extension word address - xx
    assert reach.operand_addresses(0x9176, 'move.l  (-$54,PC,D2.w), D1') == [0x9124]
