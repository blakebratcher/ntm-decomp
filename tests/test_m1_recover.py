"""Tests for tools/m1_recover.py: indirect-transfer resolution.

The recovery walk is exercised through resolve_indirect on synthetic
instruction sequences; the ROM-backed paths (unidasm fetch, vasm
round-trip) are covered by `make check`, which gates every emitted block
byte-exactly.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'tools'))
import m1_recover as mr  # noqa: E402


def insn(text, kind='normal', target=None, addr=0):
    return mr.Insn(addr, b'', text, kind=kind, target=target)


def rec_with(insns, top=0x8000, image=None):
    index = mr.StaticIndex(insns)
    index.image = image if image is not None else bytearray(top)
    return mr.Recovery(index, top=top)


def make_block(insns):
    """Minimal block-shaped list (as resolve_indirect sees it in walk())."""
    return [insn(t) if isinstance(t, str) else t
            for t in insns]


def jp_site(insns):
    return insns[-1]           # the site is the jp (hl) itself


def test_de_based_word_table_resolves():
    # the 0x4ac4 shape: push return, index with add a,a, DE-based lookup
    block = make_block([
        'ld hl,0x4ae0', 'push hl',
        'ld a,(ix+0x02)', 'add a,a', 'ld l,a', 'ld h,0x00',
        'ld de,0x4ad8', 'add hl,de',
        'ld e,(hl)', 'inc hl', 'ld d,(hl)', 'ex de,hl',
        insn('jp (hl)', kind='indirect'),
    ])
    image = bytearray(0x8000)
    for i, w in enumerate((0x2b02, 0x3605, 0x3f60, 0x42ca, 0xc937)):
        image[0x4ad8 + 2 * i] = w & 0xff
        image[0x4ad8 + 2 * i + 1] = w >> 8
    rec = rec_with(block, image=image)
    site = jp_site(block)
    follows = rec.resolve_indirect(site, block)
    targets = [s.addr for s in follows
               if s.via == 'table' or s.via == 'push_return']
    # four code words as table targets, the pushed 0x4ae0 as continuation
    assert 0x2b02 in targets and 0x42ca in targets
    assert 0x4ae0 in targets
    # the fifth word (banked-looking 0xc937) is evidence, not a target
    table = rec.tables[0]
    assert table['kind'] == 'word_dispatch'
    assert table['entries'] == 4
    assert table['extent_end'] == 0x4ae0
    assert table['tail_word'] == 0xc937
    assert table['tail_addr'] == 0x4ae0


def test_byte_jump_table_guard_arith():
    # the 0x53a3 shape: cp 0xda / cp 0xc0 guards, sub 0xc0, bc-based base
    block = make_block([
        'cp 0xda', insn('jp nc,0x578d', kind='conditional', target=0x578d),
        'cp 0xc0', insn('jp c,0x56ea', kind='conditional', target=0x56ea),
        'sub 0xc0', 'ld l,a', 'add a,a', 'add a,l', 'ld l,a', 'ld h,0x00',
        'ld bc,0x53a3', 'add hl,bc',
        insn('jp (hl)', kind='indirect'),
    ])
    # 27 consecutive jp stubs: 26 are entries, the 27th is post-table code
    insns = {a.addr: a for a in block}
    for i in range(27):
        addr = 0x53a3 + 3 * i
        insns[addr] = insn(f'jp 0x{0x5400 + i:04x}', kind='terminal',
                           target=0x5400 + i, addr=addr)
    rec = rec_with(list(insns.values()))
    site = jp_site(block)
    follows = rec.resolve_indirect(site, block)
    targets = [s.addr for s in follows if s.via == 'table']
    overflow = [s.addr for s in follows if s.via == 'table_overflow']
    assert len(targets) == 26
    assert targets[0] == 0x5400 and targets[25] == 0x5419
    # the 27th stub is seeded as code, not folded into the table
    assert overflow == [0x53f1]
    table = rec.tables[0]
    assert table['kind'] == 'byte_jump_table'
    assert table['entries'] == 26
    assert table['extent_end'] == 0x53f1
    assert table['guard_bound'] == 26
    assert table['scanned_entries'] == 27


def test_push_return_without_dispatch_table():
    block = make_block([
        'ld hl,0x4aa8', 'push hl', 'ld de,0x4aa0', 'add hl,de',
        insn('jp (hl)', kind='indirect'),
    ])
    rec = rec_with(block)
    follows = rec.resolve_indirect(jp_site(block), block)
    assert [s.addr for s in follows if s.via == 'push_return'] == [0x4aa8]


def test_index_arith_bound_missing_sub():
    block = make_block([
        'cp 0xda', insn('jp nc,0x578d', kind='conditional', target=0x578d),
        'ld l,a', 'ld bc,0x53a3', 'add hl,bc',
        insn('jp (hl)', kind='indirect'),
    ])
    rec = rec_with(block)
    assert rec.index_arith_bound(block, 4) == (None, None)


def test_index_arith_bound_returns_delta():
    block = make_block([
        'cp 0xda', insn('jp nc,0x578d', kind='conditional', target=0x578d),
        'sub 0xc0', 'ld l,a', 'ld bc,0x53a3', 'add hl,bc',
        insn('jp (hl)', kind='indirect'),
    ])
    rec = rec_with(block)
    assert rec.index_arith_bound(block, 5) == (26, 0)


def test_scan_word_table_stops_at_banked():
    image = bytearray(0x8000)
    for i, w in enumerate((0x2aa8, 0x3568, 0x3f41, 0x427f, 0xc421)):
        image[0x4aa0 + 2 * i] = w & 0xff
        image[0x4aa0 + 2 * i + 1] = w >> 8
    rec = rec_with([], image=image)
    targets, used, tail = rec.scan_word_table(0x4aa0, mr.TABLE_UNBOUNDED)
    assert targets == [0x2aa8, 0x3568, 0x3f41, 0x427f]
    assert used == 4
    assert tail == 0xc421


def test_scan_jump_table_stops_at_non_jp():
    insns = {}
    for i in range(3):
        addr = 0x53a3 + 3 * i
        insns[addr] = insn(f'jp 0x{0x5400 + i:04x}', kind='terminal',
                           target=0x5400 + i, addr=addr)
    insns[0x53ac] = insn('ret', kind='terminal')
    rec = rec_with(list(insns.values()))
    count, targets = rec.scan_jump_table(0x53a3)
    assert count == 3
    assert targets == [0x5400, 0x5401, 0x5402]
