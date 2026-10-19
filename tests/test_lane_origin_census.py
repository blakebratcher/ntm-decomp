import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))

import lane_origin_m68k as m68k
import lane_origin_census as census


def ins(mnem, ops, size='', reads=(), writes=()):
    return dict(mnem=mnem, ops=list(ops), size=size,
                reads=set(reads), writes=set(writes), flags=False)


# ---- line parsers ----

def test_source_line_instruction_with_size():
    ev = m68k.parse_source_line('        move.l d0,-(a7)')
    assert ev[0] == 'insn' and ev[1] == 'move' and ev[2] == ['d0', '-(a7)']
    assert ev[3] == 'l'


def test_source_line_branch_sizes_kept():
    assert m68k.parse_source_line(' beq.s $000028e0')[3] == 's'
    assert m68k.parse_source_line(' beq.w $00002c1e')[3] == 'w'


def test_source_line_comments_labels_directives():
    assert m68k.parse_source_line('foo:') == ('label', 'foo', None)
    assert m68k.parse_source_line('  ; comment') is None
    ev = m68k.parse_source_line(' dc.b $12,$34 ; inline data')
    assert ev[0] == 'directive' and ev[1] == 'dc.b'
    assert m68k.parse_source_line(' org $2908')[1] == 'org'


def test_source_line_label_then_insn():
    ev = m68k.parse_source_line('vec2_normalize: move.l d0,d1')
    assert ev[0] == 'insn' and ev[1] == 'move'


def test_unidasm_line_and_illegal():
    ev = m68k.parse_unidasm_line('2908: 4a80       tst.l   D0')
    assert ev['addr'] == 0x2908 and ev['mnemonic'] == 'tst'
    assert ev['ops'] == ['d0'] and not ev['illegal']
    ev = m68k.parse_unidasm_line('000: 00c0       dc.w    $00c0; ILLEGAL')
    assert ev['illegal'] and ev['mnemonic'] == 'dc'
    assert m68k.parse_unidasm_line('') is None


# ---- effects ----

def test_effects_move_directions():
    r, w, _ = m68k.effects('move', ['d0', 'd1'])
    assert r == {'d0'} and w == {'d1'}
    r, w, _ = m68k.effects('move', ['d0', '-(a7)'])
    assert 'a7' in w and 'd0' in r
    r, w, _ = m68k.effects('move', ['(a7)', 'd0'])
    assert 'a7' in r and 'd0' in w
    r, w, _ = m68k.effects('move', ['(a7)+', 'd0'])
    assert 'a7' in r and 'a7' in w  # postincrement updates the pointer


def test_effects_common():
    r, w, f = m68k.effects('lea', ['($100000).l', 'a5'])
    assert w == {'a5'} and not f
    r, w, _ = m68k.effects('cmp', ['d0', 'd1'])
    assert w == set()
    # store form: listed registers are read (pushed), a7 updated
    r, w, _ = m68k.effects('movem', ['d0-d2/a0', '-(a7)'])
    assert 'a7' in w and 'd1' in r and 'd2' in r and 'a0' in r
    # load form: listed registers are written (popped), a7 updated
    r, w, _ = m68k.effects('movem', ['(a7)+', 'd0-d2/a0'])
    assert 'a7' in w and 'd1' in w and 'd0' in w and 'a0' in w
    r, w, _ = m68k.effects('rts', [])
    assert 'a7' in r and 'a7' in w
    r, w, _ = m68k.effects('link', ['a6', '#-$4'])
    assert 'a6' in w and 'a7' in w
    r, w, _ = m68k.effects('bsr', ['$1234'])
    assert 'a7' in w
    r, w, _ = m68k.effects('move', ['sr', 'd0'])
    assert 'sr' in r


def test_effects_moveq_no_flag_issue():
    r, w, f = m68k.effects('moveq', ['#0', 'd0'])
    assert w == {'d0'} and f


def test_operand_roles():
    assert m68k.operand_roles('move', ['d0', '-(a7)']) == [
        ('r', 'd0'), ('w', '-(a7)')]
    assert m68k.operand_roles('tst', ['(a0)']) == [('r', '(a0)')]
    assert m68k.operand_roles('clr', ['d0']) == [('w', 'd0')]
    assert m68k.operand_roles('addq', ['#1', 'd0']) == [('r', '#1'),
                                                       ('rw', 'd0')]
    assert m68k.operand_roles('cmp', ['($4,a7)', 'd0']) == [
        ('r', '($4,a7)'), ('r', 'd0')]


def test_base_of():
    assert m68k.base_of('(a7)') == ('a7', 'indirect')
    assert m68k.base_of('($4,a7)') == ('a7', 'disp')
    assert m68k.base_of('(a6,d0.w)') == ('a6', 'idx')
    assert m68k.base_of('d0') == (None, None)
    assert m68k.base_of('($100000).l') == (None, None)


# ---- per-function metrics ----

def test_stack_arg_read_detected():
    rows = [ins('move', ['($4,a7)', 'd0'], reads={'a7'}, writes={'d0'}),
            ins('rts', [], reads={'a7'}, writes={'a7'})]
    met = census.analyze_insns(rows)
    assert met['a7_disp_reads'] == 1
    assert met['a7_first_read']  # first mention of a7 is a read


def test_caller_cleanup_detected():
    rows = [ins('jsr', ['$1234'], reads={'a7'}, writes={'a7'}),
            ins('addq', ['#4', 'a7'], reads={'a7'}, writes={'a7'}),
            ins('rts', [], reads={'a7'}, writes={'a7'})]
    met = census.analyze_insns(rows)
    assert met['caller_cleanup'] == 1
    assert met['a7_adjust'] == 1


def test_flags_after_call_detected():
    rows = [ins('jsr', ['$1234'], reads={'a7'}, writes={'a7'}),
            ins('beq', ['$99'], 's'),
            ins('rts', [], reads={'a7'}, writes={'a7'})]
    met = census.analyze_insns(rows)
    assert met['flags_after_call'] == 1


def test_entry_movem_detected():
    rows = [ins('movem', ['d2-d7/a2-a5', '-(a7)'],
                reads={'d2', 'd3', 'd4', 'd5', 'd6', 'd7',
                       'a2', 'a3', 'a4', 'a5'},
                writes={'a7'}),
            ins('rts', [], reads={'a7'}, writes={'a7'})]
    met = census.analyze_insns(rows)
    assert met['entry_movem'] == 'd2-d7/a2-a5'
    assert met['entry_movem_regs'] == ['a2', 'a3', 'a4', 'a5', 'd2',
                                       'd3', 'd4', 'd5', 'd6', 'd7']


def test_compiler_idioms():
    rows = [ins('move', ['($1234).w', 'd0'], 'w', writes={'d0'}),
            ins('ext', ['d0'], 'l', reads={'d0'}, writes={'d0'}),
            ins('moveq', ['#$0', 'd1'], writes={'d1'}),
            ins('seq', ['d1'], reads={'d1'}, writes={'d1'}),
            ins('move', ['($5678).w', 'd2'], 'w', writes={'d2'}),
            ins('tst', ['d2'], 'w', reads={'d2'}),
            ins('rts', [], reads={'a7'}, writes={'a7'})]
    met = census.analyze_insns(rows)
    assert met['ext_promote'] == 1
    assert met['bool_pair'] == 1
    assert met['tst_after_move'] == 1


def test_hand_asm_idioms():
    rows = [ins('dbra', ['d7', '$1000'], 'w', reads={'d7'}, writes={'d7'}),
            ins('jmp', ['(a0)'], reads={'a0'}),
            ins('move', ['($10,a7)', 'd0'], reads={'a7'}, writes={'d0'}),
            ins('rts', [], reads={'a7'}, writes={'a7'})]
    met = census.analyze_insns(rows)
    assert met['dbra'] == 1
    assert met['computed_jmp'] == 1


def test_arg_register_inference():
    rows = [ins('tst', ['d0'], reads={'d0'}),
            ins('move', ['d1', 'd2'], reads={'d1'}, writes={'d2'}),
            ins('moveq', ['#1', 'd3'], writes={'d3'}),
            ins('rts', [], reads={'a7'}, writes={'a7'})]
    met = census.analyze_insns(rows)
    assert met['arg_reads'] == ['d0', 'd1']
    assert 'd3' in met['write_only']
    assert met['first_insn'] == 'tst' and met['last_insn'] == 'rts'


def test_caller_setup_regs_recorded():
    rows = [ins('moveq', ['#1', 'd0'], writes={'d0'}),
            ins('movea', ['a6', 'a1'], writes={'a1'}, reads={'a6'}),
            ins('jsr', ['$1234'], reads={'a7'}, writes={'a7'}),
            ins('rts', [], reads={'a7'}, writes={'a7'})]
    met = census.analyze_insns(rows)
    assert set(met['call_setup_regs']) == {'d0', 'a1'}


def test_analyze_empty():
    met = census.analyze_insns([])
    assert met['n'] == 0


# ---- segmentation (cross-ROM path) ----

def test_segment_splits_at_rts():
    evs = [dict(mnemonic='tst', ops=['d0'], length=2, illegal=False),
           dict(mnemonic='rts', ops=[], length=2, illegal=False),
           dict(mnemonic='move', ops=['d0', 'd1'], length=2, illegal=False),
           dict(mnemonic='rte', ops=[], length=2, illegal=False)]
    segs = census.segment(evs)
    assert len(segs) == 2 and len(segs[0]) == 2 and len(segs[1]) == 2


def test_ud_adapter_sizes_and_illegal():
    evs = [dict(mnemonic='beq', ops=['$99'], length=1, illegal=False),
           dict(mnemonic='beq', ops=['$99'], length=2, illegal=False),
           dict(mnemonic='bra', ops=['$99'], length=3, illegal=False),
           dict(mnemonic='tst', ops=['d0'], length=1, illegal=True)]
    out = census.insn_dicts_from_ud(evs)
    assert out[0]['size'] == 's' and out[1]['size'] == 'w'
    assert out[2]['size'] == 'l'
    assert out[3]['mnem'] == 'illegal'


def test_merge_dicts():
    agg = census.merge_dicts([{'n': 3, 'link': 1, 'x': [1]},
                              {'n': 4, 'link': 0, 'x': [2]}])
    assert agg['n'] == 7 and agg['link'] == 1 and agg['x'] == [1, 2]
