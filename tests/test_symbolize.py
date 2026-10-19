"""Tests for tools/symbolize.py: operand rewriting, label handling, the
equates include and the byte gate on synthetic sources.

The gate uses tools/vasm/vasmm68k_mot (present after tools/setup_vasm.sh) and
compares assembled bytes before/after rewriting - no ROM input is needed.
"""
import pathlib
import subprocess
import sys

import pytest

from conftest import requires_corpus

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
import symbolize

VASM = symbolize.VASM
if not VASM.exists():
    pytest.skip('run make setup first', allow_module_level=True)


def asm_bytes(text, include_dir=None):
    """Assemble a source text with vasm -Fbin -no-opt; return the bytes."""
    src = pathlib.Path('/tmp') / 'symtest-input.s'
    src.write_text(text)
    out = src.with_suffix('.bin')
    cmd = [str(VASM), '-Fbin', '-no-opt', '-quiet', '-o', str(out), str(src)]
    if include_dir is not None:
        cmd.insert(4, f'-I{include_dir}')
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return out.read_bytes()


def make_config(tmp_path, names='', functions='', regions='', ram='',
                data_regions=''):
    (tmp_path / 'config' / 'names').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'config' / 'regions' / 'p1').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'config' / 'ram').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'config' / 'names.tsv').write_text(names)
    (tmp_path / 'config' / 'functions.tsv').write_text(functions)
    if regions:
        (tmp_path / 'config' / 'regions' / 'p1' / 'r.tsv').write_text(regions)
    if data_regions:
        (tmp_path / 'config' / 'data_regions.tsv').write_text(data_regions)
    if ram:
        (tmp_path / 'config' / 'ram' / 'r.tsv').write_text(ram)


def rewrite(text, config_root, src_dir, org, region_rows=True):
    """Run rewrite_source on one synthetic file; return (new_text, stats)."""
    src = src_dir / f'{org:08x}.s'
    src.write_text(text)
    listing, span_end = symbolize.vasm_listing(src, src_dir)
    merged, _name_addr, _conflicts = symbolize.load_tables(config_root)
    _count, excluded, _res, _con = symbolize.generate_include(
        config_root, [src_dir], src_dir / 'symbols.i')
    rows = symbolize.load_region_rows(config_root) if region_rows else ()
    new_text, stats, raw = symbolize.rewrite_source(
        text, listing, span_end, org, merged, excluded, rows)
    return new_text, stats, raw


# --------------------------------------------------------------------------
# local labels and size preservation

def test_local_labels_size_preserved_and_byte_identical(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config)
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            'start:\n'
            '        nop\n'
            '        nop\n'
            '        bne.s $100\n'      # backward .s to the entry
            '        beq.w $110\n'      # forward .w to an interior address
            '        bra.s $104\n'      # backward .s to the second nop
            '        dbf d0,$100\n'
            '        nop\n')            # 0x110
    before = asm_bytes(text)
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    assert 'bne.s start' in new_text
    assert 'beq.w l_110' in new_text
    assert 'bra.s l_104' in new_text
    assert 'dbf d0,start' in new_text
    assert new_text.count('l_110:') == 1
    assert ' include "symbols.i"' in new_text
    assert not raw
    assert asm_bytes(new_text, include_dir=src_dir) == before


def test_forward_reference_local_label(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config)
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $200\n'
            'top:\n'
            '        bra.w $20a\n'
            '        nop\n'
            '        nop\n'
            '        nop\n'
            '        nop\n')            # 0x20a
    new_text, stats, raw = rewrite(text, config, src_dir, 0x200)
    assert 'bra.w l_20a' in new_text
    assert '\nl_20a:\n' in new_text
    assert asm_bytes(new_text, include_dir=src_dir) == asm_bytes(text)


def test_negative_displacement_entry_target(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config)
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $300\n'
            'here:\n'
            '        nop\n'
            '        bne.w $300\n')     # backward branch to the entry
    new_text, stats, raw = rewrite(text, config, src_dir, 0x300)
    assert 'bne.w here' in new_text
    assert asm_bytes(new_text, include_dir=src_dir) == asm_bytes(text)


# --------------------------------------------------------------------------
# global symbols via the equates include

def test_global_jsr_lea_ram_and_system_registers(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config,
                names='00000400\tvec2_normalize\tnamed function\n',
                functions='00000400\tFUN_00000400\t10\n'
                          '00000420\tother_fn\t6\n',
                regions='00000450\t10\tdata\tsome_table\tevidence\n',
                ram='0010fdb4\t1\tstart_latch\tcoin dispatch\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $500\n'
            'FUN_00000500:\n'
            '        jsr ($400).l\n'
            '        jsr ($420).l\n'
            '        jsr ($400,PC)\n'
            '        lea ($450,PC),a0\n'
            '        move.w d0,($10fdb4).l\n'
            '        move.w d0,($3c0000).l\n')
    new_text, stats, raw = rewrite(text, config, src_dir, 0x500)
    assert 'jsr (vec2_normalize).l' in new_text   # friendly name wins
    assert 'jsr (other_fn).l' in new_text
    assert 'jsr (vec2_normalize,PC)' in new_text
    assert 'lea (some_table,PC),a0' in new_text
    assert 'move.w d0,(start_latch).l' in new_text
    assert 'move.w d0,(REG_VRAMADDR).l' in new_text
    assert not raw
    assert asm_bytes(new_text, include_dir=src_dir) == asm_bytes(text)
    include = (src_dir / 'symbols.i').read_text()
    assert 'vec2_normalize equ $400' in include
    assert 'start_latch equ $10fdb4' in include
    assert 'REG_VRAMADDR equ $3c0000' in include


def test_region_interior_operand_uses_base_plus_offset(tmp_path):
    """An operand inside a classified data row becomes `row+$off`."""
    config = tmp_path / 'cfg'
    make_config(config, regions='00000700\t40\tdata\tpose_table\tevidence\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            't:\n'
            '        lea ($714,PC),a1\n'          # interior field of the row
            '        lea ($714).l,a1\n'
            '        lea $714(PC),a1\n'
            '        move.w ($73c).l,d0\n'
            '        lea ($700,PC),a1\n')          # the row start: exact name
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    assert 'lea (pose_table+$14,PC),a1' in new_text
    assert 'lea (pose_table+$14).l,a1' in new_text
    assert 'lea pose_table+$14(PC),a1' in new_text
    assert 'move.w (pose_table+$3c).l,d0' in new_text
    assert 'lea (pose_table,PC),a1' in new_text      # no offset at the start
    assert stats['abs-region-offset'] == 4
    assert stats['abs-global'] == 1
    assert not raw
    assert asm_bytes(new_text, include_dir=src_dir) == asm_bytes(text)


def test_exact_name_beats_base_plus_offset(tmp_path):
    """A symbol at the interior address is used instead of the offset form."""
    config = tmp_path / 'cfg'
    make_config(config,
                names='00000714\tpose_side\tnamed interior field\n',
                regions='00000700\t40\tdata\tpose_table\tevidence\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            't:\n'
            '        lea ($714,PC),a1\n')
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    assert 'lea (pose_side,PC),a1' in new_text
    assert stats['abs-global'] == 1
    assert 'abs-region-offset' not in stats
    assert asm_bytes(new_text, include_dir=src_dir) == asm_bytes(text)


def test_reference_into_fill_row_stays_raw(tmp_path):
    """Pointing into proven-unreferenced padding is a contradiction: raw."""
    config = tmp_path / 'cfg'
    make_config(config, regions='00000600\t20\tfill:00\tpad\tevidence\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            't:\n'
            '        lea ($604,PC),a0\n')
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    assert 'lea ($604,PC),a0' in new_text
    assert raw[('abs-fill-target', 0x604)] == 1
    assert 'abs-region-offset' not in stats
    assert asm_bytes(new_text, include_dir=src_dir) == asm_bytes(text)


def test_data_region_row_start_is_a_symbol(tmp_path):
    """config/data_regions.tsv names are symbols, and their rows too."""
    config = tmp_path / 'cfg'
    make_config(config, data_regions='00009000\t200\tframe_bank\tframes\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            't:\n'
            '        lea ($9000).l,a0\n'
            '        lea ($9020).l,a1\n')
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    assert 'lea (frame_bank).l,a0' in new_text
    assert 'lea (frame_bank+$20).l,a1' in new_text
    assert 'frame_bank equ $9000' in (src_dir / 'symbols.i').read_text()
    assert not raw
    assert asm_bytes(new_text, include_dir=src_dir) == asm_bytes(text)


def test_nonfill_row_without_a_usable_name_stays_raw(tmp_path):
    """Base+offset needs the row start to carry a usable symbol: a row whose
    name is shadowed by a label at another address resolves to nothing."""
    config = tmp_path / 'cfg'
    make_config(config, regions='00000700\t40\tdata\tshadowed\tevidence\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    (src_dir / '00000200.s').write_text(' org $200\nshadowed:\n        nop\n')
    text = (' org $100\n'
            't:\n'
            '        lea ($714,PC),a1\n')
    new_text, _stats, raw = rewrite(text, config, src_dir, 0x100)
    assert 'lea ($714,PC),a1' in new_text
    assert raw[('abs-no-name', 0x714)] == 1


def test_unnamed_targets_stay_raw(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config)
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            'x:\n'
            '        jsr ($600).l\n'     # outside the file, no name
            '        bsr.w $700\n'       # outside the file, no name
            '        move.w d0,($107076).l\n')  # RAM without a table row
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    assert 'jsr ($600).l' in new_text
    assert 'bsr.w $700' in new_text
    assert '($107076).l' in new_text
    assert raw[('no-name', 0x600)] == 1
    assert raw[('no-name', 0x700)] == 1
    assert raw[('abs-no-name', 0x107076)] == 1


# --------------------------------------------------------------------------
# label/equate coexistence

def test_top_label_commented_out_when_table_names_the_org(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config, functions='00000100\tboot\tentry code\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            'boot:\n'
            '        nop\n'
            '        bne.w $100\n')
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    assert '; boot:' in new_text
    assert 'bne.w boot' in new_text      # resolves via the equate
    assert asm_bytes(new_text, include_dir=src_dir) == asm_bytes(text)


def test_top_label_kept_when_names_differ(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config, names='00000100\tfriendly\tnamed\n',
                functions='00000100\tFUN_00000100\t10\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            'FUN_00000100:\n'
            '        nop\n'
            '        bne.w $100\n')
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    assert 'FUN_00000100:' in new_text     # label survives
    assert 'bne.w FUN_00000100' in new_text
    assert asm_bytes(new_text, include_dir=src_dir) == asm_bytes(text)


def test_label_at_wrong_address_excludes_the_name(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config, functions='00000200\tbar\tother fn\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            'bar:\n'                      # label bar at $100, table says $200
            '        nop\n'
            '        jsr ($200).l\n')
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    assert 'bar:' in new_text             # label survives
    assert 'jsr ($200).l' in new_text     # name dropped from the include
    assert 'bar equ' not in (src_dir / 'symbols.i').read_text()
    assert raw[('no-name', 0x200)] == 1


def test_interior_entry_at_its_table_address_keeps_the_name(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config, functions='00000104\tinside\thelper\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            'top:\n'
            '        nop\n'               # $100
            '        nop\n'               # $102
            'inside:\n'
            '        jsr ($104).l\n')     # label and target agree at $104
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    # an inner entry at its table address is a secondary entry point: the
    # include defines the name and the label stays as a comment marker
    assert '; inside:' in new_text
    assert 'jsr (inside).l' in new_text
    assert 'inside equ $104' in (src_dir / 'symbols.i').read_text()
    assert asm_bytes(new_text, include_dir=src_dir) == asm_bytes(text)


# --------------------------------------------------------------------------
# immediates

def test_movea_immediate_named_and_unnamed(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config, regions='000070cb\t10\tdata\tpose_table\tev\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            't:\n'
            '        movea.w #$70cb,a1\n'
            '        movea.l #$3c0000,a0\n'
            '        movea.w #$7000,a1\n')   # unnamed: stays raw
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    assert 'movea.w #pose_table,a1' in new_text
    assert 'movea.l #REG_VRAMADDR,a0' in new_text
    assert 'movea.w #$7000,a1' in new_text
    assert stats['movea-imm'] == 2
    assert raw[('movea-no-name', 0x7000)] == 1


def test_install_immediate_requires_same_file_jump(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config, functions='00000f08\tactor_draw\tactor\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    proven = (' org $100\n'
              't:\n'
              '        move.l #$f08,(a6)\n'
              '        jmp ($f08).l\n')
    new_text, stats, raw = rewrite(proven, config, src_dir, 0x100)
    assert 'move.l #actor_draw,(a6)' in new_text
    assert stats['install-imm'] == 1
    assert not raw

    unproven = (' org $100\n'
                't:\n'
                '        move.l #$f08,(a6)\n'
                '        rts\n')
    new_text, stats, raw = rewrite(unproven, config, src_dir, 0x100)
    assert 'move.l #$f08,(a6)' in new_text     # left raw: no jump evidence
    assert raw[('install-unproven', 0xf08)] == 1


def _install_rewrite(tmp_path, text, proofs_tsv):
    config = tmp_path / 'cfg'
    make_config(config, functions='00000f08\tactor_draw\tactor\n'
                                  '00000f2e\tactor_draw_plain\tactor\n')
    (config / 'config' / 'install_proofs.tsv').write_text(proofs_tsv)
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    src = src_dir / '00000100.s'
    src.write_text(text)
    listing, span_end = symbolize.vasm_listing(src, src_dir)
    merged, _n, _c = symbolize.load_tables(config)
    _count, excluded, _r, _c2 = symbolize.generate_include(
        config, [src_dir], src_dir / 'symbols.i')
    proofs = symbolize.load_install_proofs(config)
    new_text, stats, raw = symbolize.rewrite_source(
        text, listing, span_end, 0x100, merged, excluded, (), proofs)
    return new_text, stats, raw, src_dir


def test_install_proof_rewrites_only_the_proven_site(tmp_path):
    # two installs of the same target; only the site at 0x106 is proven
    text = (' org $100\n'
            't:\n'
            '        move.l #$f08,(a6)\n'       # 0x100, not proven
            '        move.l #$f08,(a6)\n'       # 0x106, proven
            '        move.l #$f2e,(a6)\n'       # 0x10c, row names 0xf08
            '        rts\n')
    proofs = ('# site\ttarget\tidiom\tevidence\n'
              '000106\t000f08\tactor-state\ttrace: test\n'
              '00010c\t000f08\tactor-state\ttrace: wrong immediate\n')
    before = asm_bytes(text)
    new_text, stats, raw, src_dir = _install_rewrite(tmp_path, text, proofs)
    lines = new_text.splitlines()
    assert lines.count('        move.l #$f08,(a6)') == 1
    assert '        move.l #actor_draw,(a6)' in lines
    assert '        move.l #$f2e,(a6)' in lines   # row's target differs
    assert stats['install-proven'] == 1
    assert raw[('install-unproven', 0xf08)] == 1
    assert raw[('install-unproven', 0xf2e)] == 1
    assert asm_bytes(new_text, src_dir) == before


def test_install_proofs_reject_rows_without_evidence(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config)
    (config / 'config' / 'install_proofs.tsv').write_text(
        '000106\t000f08\tactor-state\t\n')
    with pytest.raises(ValueError):
        symbolize.load_install_proofs(config)
    (config / 'config' / 'install_proofs.tsv').unlink()
    assert symbolize.load_install_proofs(config) == set()


def test_arbitrary_immediates_never_touched(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config, functions='00000008\tthing\tfn\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            't:\n'
            '        move.l #$8,(a6)\n'        # $8 is a named fn addr, but .l
            '        move.w #$8,(a6)\n'        # .w installs are never touched
            '        addi.l #$1234,d0\n'
            '        cmpi.l #$10fdb4,d0\n')    # RAM value as a compare
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    assert 'move.l #$8,(a6)' in new_text
    assert 'move.w #$8,(a6)' in new_text
    assert 'addi.l #$1234,d0' in new_text
    assert 'cmpi.l #$10fdb4,d0' in new_text
    assert asm_bytes(new_text, include_dir=src_dir) == asm_bytes(text)


# --------------------------------------------------------------------------
# edge cases

def test_mid_instruction_target_stays_raw(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config)
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            't:\n'
            '        nop\n'                    # $100-$101
            '        nop\n'                    # $102-$103
            '        bne.w $103\n')            # lands inside the second nop
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    assert 'bne.w $103' in new_text
    assert raw[('mid-instruction', 0x103)] == 1


def test_dc_lines_untouched(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config)
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    text = (' org $100\n'
            't:\n'
            '        dc.w $0000\n'
            '        jsr ($600).l\n')
    new_text, stats, raw = rewrite(text, config, src_dir, 0x100)
    assert 'dc.w $0000' in new_text


def test_include_generation_deterministic_and_reserved_skipped(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config, functions='00000100\tboot\tentry\n'
                                  '00000200\torg\tbad directive name\n'
                                  '00000300\tthing\tfn\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    (src_dir / '00000100.s').write_text(' org $100\nboot:\n nop\n')
    count, excluded, reserved_names, conflicts = symbolize.generate_include(
        config, [src_dir], src_dir / 'symbols.i')
    include = (src_dir / 'symbols.i').read_text()
    assert 'boot equ $100' in include
    assert 'thing equ $300' in include
    assert 'org equ' not in include          # reserved directive name
    assert 'org' in reserved_names
    # the include must assemble cleanly on its own
    assert asm_bytes(' include "symbols.i"\n nop\n', include_dir=src_dir)
    # deterministic
    (src_dir / 'symbols2.i').write_text('')
    count2, *_ = symbolize.generate_include(config, [src_dir], src_dir / 'symbols2.i')
    assert (src_dir / 'symbols2.i').read_text() == include
    assert count2 == count


def test_generate_include_excludes_shadowed_and_reports_conflicts(tmp_path):
    config = tmp_path / 'cfg'
    make_config(config, functions='00000100\ta\tfn\n'
                                  '00000200\tb\tfn\n'
                                  '00000300\ta\tduplicate name\n',
                names='00000100\ta\tfriendly\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    (src_dir / '00000100.s').write_text(' org $100\na:\n nop\n')
    count, excluded, reserved_names, conflicts = symbolize.generate_include(
        config, [src_dir], src_dir / 'symbols.i')
    include = (src_dir / 'symbols.i').read_text()
    assert 'a equ $100' in include
    assert 'b equ $200' in include
    assert 'a equ $300' not in include       # one name, two addresses
    assert any('names' in c for c in conflicts)


# --------------------------------------------------------------------------
# build_p1 integration: the include is generated and reachable via -I

@requires_corpus
def test_build_p1_assembles_a_rewritten_real_source(tmp_path):
    import build_p1
    src = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'p1' / \
        'asm' / 'functions' / '00000428.s'
    listing, span_end = symbolize.vasm_listing(src, tmp_path)
    merged, _na, _conflicts = symbolize.load_tables()
    _count, excluded, _reserved, _con = symbolize.generate_include(
        symbolize.REPO, [symbolize.FUNC_DIR, symbolize.BLOCK_DIR],
        build_p1.INC_DIR / symbolize.INCLUDE_NAME)
    new_text, _stats, _raw = symbolize.rewrite_source(
        src.read_text(), listing, span_end, symbolize.org_of(src.read_text()),
        merged, excluded)
    assert ' include "symbols.i"' in new_text
    rewritten = tmp_path / '00000428.s'
    rewritten.write_text(new_text)
    out = tmp_path / 'out.bin'
    build_p1.assemble(rewritten, out)
    # the raw form assembles without an include, the rewritten form needs
    # -I build/gen/p1; both must yield the same bytes
    original = asm_bytes(src.read_text(), include_dir=build_p1.INC_DIR)
    assert out.read_bytes() == original


def test_rewrite_is_idempotent_on_symbolized_input(tmp_path):
    """Re-running the rewrite on an already symbolized source is a no-op."""
    config = tmp_path / 'cfg'
    make_config(config, functions='00000100\tboot\tentry code\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    src = src_dir / '00000100.s'
    text = (' org $100\n'
            ' include "symbols.i"\n'
            '; boot:\n'
            '        nop\n'
            '        bne.w boot\n')
    src.write_text(text)
    symbolize.generate_include(config, [src_dir], src_dir / 'symbols.i')
    before = asm_bytes(text, include_dir=src_dir)
    listing, span_end = symbolize.vasm_listing(src, src_dir)
    merged, _na, _conflicts = symbolize.load_tables(config)
    _count, excluded, _res, _con = symbolize.generate_include(
        config, [src_dir], src_dir / 'symbols.i')
    new_text, stats, raw = symbolize.rewrite_source(
        text, listing, span_end, symbolize.org_of(text), merged, excluded)
    assert new_text == text            # nothing to rewrite a second time
    assert stats['include-added'] == 0
    assert asm_bytes(new_text, include_dir=src_dir) == before


def test_dry_run_gate_byte_identical_on_real_corpus_window():
    """tools/symbolize.py --dry-run must rewrite a real window byte-identically."""
    work = pathlib.Path(__file__).resolve().parents[1] / 'build' / 'work' / \
        'p1.fixed.cpu.bin'
    if not work.exists():
        pytest.skip('pristine halves missing - run make extract')
    r = subprocess.run(
        [sys.executable, 'tools/symbolize.py', '--dry-run',
         '--min-org', '0x428', '--max-org', '0x4c6'],
        cwd=pathlib.Path(__file__).resolve().parents[1],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'MATCH, 0 DIFFER' in r.stdout


# --------------------------------------------------------------------------
# fix-layer VRAM immediates (config/vram/fix.tsv + VramFlow)

FIX_SINK = (' org $d7a6\n'          # the shape of vram_tile_block_fill
            'sink:\n'
            '        movem.l d5/d4/d3,-(a7)\n'
            '        move.w d0,d4\n'
            '        move.l a1,d3\n'
            '        cmpi.w #$74ff,d3\n'
            '        bgt.s skip\n'
            '        movem.w d3-d4,($3c0000).l\n'
            'skip:\n'
            '        movem.l (a7)+,d3-d5\n'
            '        rts\n')


def vram_setup(tmp_path, user, names='', extra=(), fix=None):
    """Config + sources; returns (config, src_dir, user path)."""
    config = tmp_path / 'cfg'
    make_config(config, names=names)
    (config / 'config' / 'vram').mkdir(parents=True, exist_ok=True)
    (config / 'config' / 'vram' / 'fix.tsv').write_text(
        fix if fix is not None else
        '# test\n7063\tFIX_X03_Y03\tx=3 y=3; test row\n')
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    (src_dir / '0000d7a6.s').write_text(FIX_SINK)
    for org, text in extra:
        (src_dir / f'{org:08x}.s').write_text(text)
    path = src_dir / '00001000.s'
    path.write_text(user)
    return config, src_dir, path


def vram_rewrite(config, src_dir, path, rom=None):
    merged, name_addr, _c = symbolize.load_tables(config)
    plan = symbolize.plan_vram(config, [src_dir], name_addr, rom)
    _n, excluded, _r, _c2 = symbolize.generate_include(
        config, [src_dir], src_dir / 'symbols.i')
    text = path.read_text()
    listing, span_end = symbolize.vasm_listing(path, src_dir)
    new_text, stats, raw = symbolize.rewrite_source(
        text, listing, span_end, symbolize.org_of(text), merged, excluded,
        vram=plan.get(path))
    return new_text, stats, raw, plan


def test_fix_position_names():
    assert symbolize.fix_position_name(0x7000) == 'FIX_X00_Y00'
    assert symbolize.fix_position_name(0x715a) == 'FIX_X10_Y26'
    assert symbolize.fix_position_name(0x74ff) == 'FIX_X39_Y31'
    with pytest.raises(ValueError):
        symbolize.fix_position_name(0x7500)


@pytest.mark.parametrize('row', [
    '7064\tFIX_X03_Y03\tev\n',           # positional name of another cell
    '6fff\tFIX_LOW\tev\n',               # outside the fix map
    '7063\tFIX_X03_Y03\t\n',             # no evidence
    '7063\tFIX_X03_Y03\tev\n7063\tFIX_A\tev\n',   # two names, one address
    '7063\tFIX_A\tev\n7064\tFIX_A\tev\n',          # one name, two addresses
])
def test_fix_table_validation(tmp_path, row):
    (tmp_path / 'config' / 'vram').mkdir(parents=True)
    (tmp_path / 'config' / 'vram' / 'fix.tsv').write_text(row)
    with pytest.raises(ValueError):
        symbolize.load_fix_names(tmp_path)


def test_vram_movea_feeding_the_block_writer_is_named(tmp_path):
    user = (' org $1000\n'
            'u:\n'
            '        movea.w #$7063,a1\n'
            '        move.w #$300,d0\n'
            '        jsr ($d7a6).l\n'
            '        rts\n')
    config, src_dir, path = vram_setup(tmp_path, user)
    before = asm_bytes(user)
    new_text, stats, raw, plan = vram_rewrite(config, src_dir, path)
    assert plan[path][3][2] == '0000d7a6.s:8'     # the movem.w to $3c0000
    assert 'movea.w #FIX_X03_Y03,a1' in new_text
    assert stats['movea-vram'] == 1
    assert not [k for k in raw if k[0] != 'no-name']   # jsr ($d7a6) unnamed
    assert 'FIX_X03_Y03 equ $7063' in (src_dir / 'symbols.i').read_text()
    assert asm_bytes(new_text, include_dir=src_dir) == before


def test_vram_value_parked_in_ram_stays_raw(tmp_path):
    """A fix-map-looking value that is stored, not written to REG_VRAMADDR,
    is unproven (and a ROM coincidence too): never named."""
    user = (' org $1000\n'
            'u:\n'
            '        movea.w #$7063,a1\n'
            '        move.w a1,($40,a6)\n'
            '        rts\n')
    config, src_dir, path = vram_setup(tmp_path, user)
    new_text, stats, raw, plan = vram_rewrite(config, src_dir, path)
    assert path not in plan
    assert 'movea.w #$7063,a1' in new_text
    assert raw[('movea-no-name', 0x7063)] == 1


def test_vram_register_clobbered_before_the_call_stays_raw(tmp_path):
    user = (' org $1000\n'
            'u:\n'
            '        movea.w #$7063,a1\n'
            '        movea.l a2,a1\n'
            '        jsr ($d7a6).l\n'
            '        rts\n')
    config, src_dir, path = vram_setup(tmp_path, user)
    _new, _stats, raw, plan = vram_rewrite(config, src_dir, path)
    assert path not in plan
    assert raw[('movea-no-name', 0x7063)] == 1


def test_vram_write_to_the_data_port_is_not_an_address(tmp_path):
    """The value written to REG_VRAMRW ($3c0002) is data, not an address."""
    user = (' org $1000\n'
            'u:\n'
            '        movea.w #$7063,a1\n'
            '        move.w a1,($3c0002).l\n'
            '        rts\n')
    config, src_dir, path = vram_setup(tmp_path, user)
    _new, _stats, _raw, plan = vram_rewrite(config, src_dir, path)
    assert path not in plan


def test_vram_value_saved_across_a_call_is_tracked(tmp_path):
    """move.w a1,-(a7) / jsr / movea.w (a7)+,a1 restores the value; a pop of
    another size forgets it."""
    other = (' org $2000\n'
             'o:\n'
             '        moveq #0,d0\n'
             '        movea.l d0,a1\n'
             '        rts\n')
    user = (' org $1000\n'
            'u:\n'
            '        movea.w #$7063,a1\n'
            '        move.w a1,-(a7)\n'
            '        jsr ($2000).l\n'
            '        movea.w (a7)+,a1\n'
            '        move.w a1,d0\n'
            '        move.w d0,($3c0000).l\n'
            '        movea.w #$7064,a1\n'
            '        move.w a1,-(a7)\n'
            '        jsr ($2000).l\n'
            '        movea.l (a7)+,a1\n'
            '        move.w a1,($3c0000).l\n'
            '        rts\n')
    config, src_dir, path = vram_setup(
        tmp_path, user, extra=[(0x2000, other)],
        fix='7063\tFIX_X03_Y03\tev\n7064\tFIX_X03_Y04\tev\n')
    new_text, stats, raw, plan = vram_rewrite(config, src_dir, path)
    assert sorted(plan[path]) == [3]
    assert 'movea.w #FIX_X03_Y03,a1' in new_text
    assert 'movea.w #$7064,a1' in new_text


def test_vram_dispatch_table_and_vramaddr_pointer(tmp_path):
    """movea.l ($T,PC,d1.w*$1),a2 + jmp (a2) follows the table entries (read
    from the ROM image); a register loaded with REG_VRAMADDR addresses the
    port."""
    disp = (' org $3000\n'
            'disp:\n'
            '        moveq #4,d1\n'                       # 3000
            '        movea.l ($300a,PC,d1.w*$1),a2\n'     # 3002
            '        jmp (a2)\n'                          # 3006
            '        nop\n'                               # 3008
            '        dc.l $3012,$3014\n'                  # 300a
            '        rts\n'                               # 3012
            '        movea.l #$3c0000,a5\n'               # 3014
            '        move.w a1,(a5)\n'
            '        rts\n')
    user = (' org $1000\n'
            'u:\n'
            '        movea.w #$7063,a1\n'
            '        jmp ($3000).l\n')
    config, src_dir, path = vram_setup(tmp_path, user,
                                       extra=[(0x3000, disp)])
    _new, _s, _r, plan = vram_rewrite(config, src_dir, path)
    assert path not in plan                  # no ROM image: table unknown
    rom = {0x3000: asm_bytes(disp)}
    new_text, _s, _r, plan = vram_rewrite(config, src_dir, path, rom)
    assert plan[path][3][2] == '00003000.s:10'
    assert 'movea.w #FIX_X03_Y03,a1' in new_text


def test_vram_name_never_used_for_rom_operands(tmp_path):
    """The fix table is a separate namespace: a ROM operand at the same
    number keeps its ROM name (or stays raw), and a proven VRAM movea takes
    the fix name even when a ROM symbol shares the number."""
    user = (' org $1000\n'
            'u:\n'
            '        lea ($7063).l,a0\n'
            '        movea.w #$7063,a3\n'
            '        move.w a3,($40,a6)\n'
            '        movea.w #$7063,a1\n'
            '        jsr ($d7a6).l\n'
            '        rts\n')
    config, src_dir, path = vram_setup(tmp_path, user,
                                       names='007063\trom_thing\trom\n')
    before = asm_bytes(user)
    new_text, stats, raw, _plan = vram_rewrite(config, src_dir, path)
    assert 'lea (rom_thing).l,a0' in new_text
    assert 'movea.w #rom_thing,a3' in new_text
    assert 'movea.w #FIX_X03_Y03,a1' in new_text
    assert asm_bytes(new_text, include_dir=src_dir) == before


def test_vram_proven_value_without_a_table_row_stays_raw(tmp_path):
    user = (' org $1000\n'
            'u:\n'
            '        movea.w #$7065,a1\n'
            '        jsr ($d7a6).l\n'
            '        movea.w #$0fff,a1\n'           # outside the fix map
            '        jsr ($d7a6).l\n'
            '        rts\n')
    config, src_dir, path = vram_setup(tmp_path, user)
    new_text, stats, raw, plan = vram_rewrite(config, src_dir, path)
    assert sorted(plan[path]) == [3]
    assert raw[('vram-no-name', 0x7065)] == 1
    assert raw[('movea-no-name', 0xfff)] == 1
    assert 'movea.w #$7065,a1' in new_text


def test_vram_rewrite_is_idempotent(tmp_path):
    user = (' org $1000\n'
            'u:\n'
            '        movea.w #$7063,a1\n'
            '        jsr ($d7a6).l\n'
            '        rts\n')
    config, src_dir, path = vram_setup(tmp_path, user)
    new_text, _s, _r, _p = vram_rewrite(config, src_dir, path)
    path.write_text(new_text)
    again, stats, raw, plan = vram_rewrite(config, src_dir, path)
    # the written form is still proven (the proof re-reads FIX_ operands)
    # and the rewrite leaves it alone
    assert again == new_text and sorted(plan[path]) == [4]   # + include
    assert stats['movea-vram'] == 0
    assert not [k for k in raw if k[0] in ('movea-no-name', 'vram-no-name')]


def test_vram_value_not_saved_across_a_call_is_forgotten(tmp_path):
    """A call that returns without proving the write clobbers what it may:
    the movea before it is not credited to a later block-writer call."""
    other = (' org $2000\n'
             'o:\n'
             '        rts\n')
    user = (' org $1000\n'
            'u:\n'
            '        movea.w #$7063,a1\n'
            '        jsr ($2000).l\n'
            '        jsr ($d7a6).l\n'
            '        rts\n')
    config, src_dir, path = vram_setup(tmp_path, user, extra=[(0x2000, other)])
    _new, _s, raw, plan = vram_rewrite(config, src_dir, path)
    assert path not in plan
    assert raw[('movea-no-name', 0x7063)] == 1


def test_vram_taken_branch_edge_is_followed(tmp_path):
    user = (' org $1000\n'
            'u:\n'
            '        movea.w #$7063,a1\n'
            '        tst.w d0\n'
            '        beq.s go\n'
            '        rts\n'
            'go:\n'
            '        exg a1,d2\n'
            '        move.w d2,($3c0000).l\n'
            '        rts\n')
    config, src_dir, path = vram_setup(tmp_path, user)
    _new, _s, _r, plan = vram_rewrite(config, src_dir, path)
    assert plan[path][3][2] == '00001000.s:9'
