"""Tests for tools/corpus.py: the form-agnostic source view.

Every capability is exercised on a synthetic raw source and its symbolized
equivalents (tools/symbolize.py --write forms: symbols.i include, symbolic
operands, `; name:` entry marker when the table names the org, l_XXXX local
labels, or a kept live label when the table does not name the org).  All
forms must yield the same org, labels, assembled bytes, listing facts and
resolved operand addresses.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))

import corpus  # noqa: E402

# raw form: the entry 'start' is not in any name table, so symbolization
# keeps the live label and rewrites only the named operands
RAW = (' org $100\n'
       'start:\n'
       '        nop\n'
       '        bne.s $100\n'
       '        bsr.w $108\n'
       '        jsr ($200).l\n'
       '        lea ($300,PC),a0\n'
       '        movea.w #$400,a1\n'
       '        rts\n'
       'l_118:\n'
       '        nop\n')
# symbolized form of RAW (live kept label + local label + named operands)
SYM = (' org $100\n'
       ' include "symbols.i"\n'
       'start:\n'
       '        nop\n'
       '        bne.s start\n'
       '        bsr.w $108\n'
       '        jsr (fn_b).l\n'
       '        lea (tbl_a,PC),a0\n'
       '        movea.w #pose_data,a1\n'
       '        rts\n'
       'l_118:\n'
       '        nop\n')

# a second entry case: the table names the org, so the top label becomes a
# commented `; name:` marker and the equate defines the name
RAW2 = (' org $100\n'
        'start:\n'
        '        nop\n'
        '        bne.s $100\n'
        '        rts\n')
MARKED = (' org $100\n'
          ' include "symbols.i"\n'
          '; start:\n'
          '        nop\n'
          '        bne.s start\n'
          '        rts\n')

NAMES = ('00000200\tfn_b\tnamed function\n'
         '00000300\ttbl_a\ttable\n'
         '00000400\tpose_data\tpose table\n')

SYMBOLS = {'fn_b': 0x200, 'tbl_a': 0x300, 'pose_data': 0x400,
           'start': 0x100}


def make_config(tmp_path, names=NAMES):
    (tmp_path / 'config').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'config' / 'names.tsv').write_text(names)
    for d in ('functions', 'blocks'):
        (tmp_path / 'src' / 'p1' / 'asm' / d).mkdir(parents=True,
                                                    exist_ok=True)
    return tmp_path / 'src' / 'p1' / 'asm' / 'functions'


# ---------------------------------------------------------------- org/labels

def test_org_and_labels_both_forms():
    assert corpus.org_of(RAW) == corpus.org_of(SYM) == 0x100
    raw = corpus.labels(RAW)
    assert raw == [(2, 'start', False), (10, 'l_118', False)], raw
    sym = corpus.labels(SYM)
    assert sym == [(3, 'start', False), (11, 'l_118', False)], sym


def test_commented_entry_marker_is_a_label():
    marked = corpus.labels(MARKED)
    assert marked == [(3, 'start', True)], marked


def test_entry_label_includes_commented_marker():
    assert corpus.entry_label(RAW) == 'start'
    assert corpus.entry_label(SYM) == 'start'
    assert corpus.entry_label(MARKED) == 'start'
    assert corpus.entry_label(' org $100\n        rts\n') is None


# ---------------------------------------------------------------- assembly

def test_assemble_both_forms_byte_identical(tmp_path):
    funcs = make_config(tmp_path)
    raw_src = funcs / '00000100.s'
    raw_src.write_text(RAW)
    sym_src = funcs / '00000101.s'
    sym_src.write_text(SYM)
    # ensure_include generates symbols.i from the synthetic config tables
    inc = corpus.ensure_include(tmp_path)
    assert 'fn_b equ $200' in inc.read_text()
    raw_bytes = corpus.assemble(raw_src, config_root=tmp_path)
    sym_bytes = corpus.assemble(sym_src, config_root=tmp_path)
    assert raw_bytes == sym_bytes
    assert len(raw_bytes) == 0x1a  # 2+2+4+6+4(lea pc)+4+2+2


def test_marked_entry_assembles_via_equate(tmp_path):
    funcs = make_config(tmp_path, names='00000100\tstart\tentry\n')
    marked = funcs / '00000100.s'
    marked.write_text(MARKED)
    inc = corpus.ensure_include(tmp_path)
    assert 'start equ $100' in inc.read_text()
    assert corpus.assemble(marked, config_root=tmp_path) == \
        bytes.fromhex('4e71 66fc 4e75')


# ---------------------------------------------------------------- listing

def test_listing_per_line_facts_and_file_symbols(tmp_path):
    funcs = make_config(tmp_path)
    sym_src = funcs / '00000100.s'
    sym_src.write_text(SYM)
    lines, span_end, file_symbols = corpus.listing(sym_src,
                                                   config_root=tmp_path)
    assert span_end == 0x11a
    # vasm normalizes the text column (no leading indentation)
    assert lines[4] == (0x100, 2, 'nop')
    assert lines[5] == (0x102, 2, 'bne.s start')
    assert lines[6] == (0x104, 4, 'bsr.w $108')
    assert file_symbols['l_118'] == 0x118


def test_listing_raw_file_labels(tmp_path):
    funcs = make_config(tmp_path)
    raw_src = funcs / '00000100.s'
    raw_src.write_text(RAW)
    _lines, _span, file_symbols = corpus.listing(raw_src, config_root=tmp_path)
    assert file_symbols['start'] == 0x100


# ---------------------------------------------------------------- operands

def test_canonicalize_symbols_locals_and_passthrough():
    c = corpus.canonicalize
    assert c('        jsr (fn_b).l', SYMBOLS) == '        jsr ($200).l'
    assert c('        lea (tbl_a,PC),a0', SYMBOLS) == '        lea ($300,PC),a0'
    assert c('        bsr.w l_118', SYMBOLS) == '        bsr.w $118'
    assert c('        bne.s start', SYMBOLS) == '        bne.s $100'
    assert c('        jsr ($200).l', SYMBOLS) == '        jsr ($200).l'
    # comments are stripped, the mnemonic is never a symbol operand
    assert c('        move.l #fn_b,(a6) ; install', SYMBOLS) == \
        '        move.l #$200,(a6)'
    assert c('fn_b equ $200', SYMBOLS) == 'fn_b equ $200'


def test_line_addrs_resolves_both_forms():
    for text in (RAW, SYM):
        got = set()
        for line in text.splitlines():
            got |= corpus.line_addrs(line, SYMBOLS)
        assert got == {0x100, 0x108, 0x200, 0x300, 0x400}, got


# ---------------------------------------------------------------- refs

def test_refs_finds_both_forms(tmp_path):
    funcs = make_config(tmp_path)
    (funcs / 'raw.s').write_text(RAW)
    (funcs / 'sym.s').write_text(SYM)
    hits = corpus.refs(0x200, dirs=[funcs], symbols=SYMBOLS)
    by_file = {}
    for path, _n, _text, kind in hits:
        by_file.setdefault(pathlib.Path(path).name, set()).add(kind)
    assert 'raw.s' in by_file and 'sym.s' in by_file
    # ($200).l is an absolute operand in both forms
    assert by_file['raw.s'] == {'abs'} and by_file['sym.s'] == {'abs'}


def test_refs_classifies_pcrel_and_imm(tmp_path):
    funcs = make_config(tmp_path)
    (funcs / 'x.s').write_text(
        ' org $100\nx:\n        jsr ($200,PC)\n        move.l #$200,(a6)\n')
    hits = corpus.refs(0x200, dirs=[funcs], symbols={})
    kinds = {k for _p, _n, _t, k in hits}
    assert kinds == {'pcrel', 'imm'}, hits


def test_canonicalize_row_plus_offset():
    assert corpus.canonicalize('        lea (tab+$10).l,a0', {'tab': 0x78b40}) == '        lea ($78b50).l,a0'
    assert corpus.canonicalize('        lea (tab+16,PC),a0', {'tab': 0x100}) == '        lea ($110,PC),a0'
    assert corpus.canonicalize('        lea (zz+$4).l,a0', {}) == '        lea (zz+$4).l,a0'
