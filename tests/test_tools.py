"""Unit tests for the ntm-decomp pipeline (no ROM, no network)."""
import pathlib
import subprocess
import sys
import zipfile

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))

import extract          # noqa: E402
import ghidra_import    # noqa: E402
import build_p1         # noqa: E402


# ---------------------------------------------------------------- extract

def test_word_swap_roundtrip():
    data = bytes(range(16))
    swapped = extract.word_swap(data)
    assert swapped == bytes([1, 0, 3, 2, 5, 4, 7, 6, 9, 8, 11, 10, 13, 12, 15, 14])
    assert extract.word_swap(swapped) == data


def test_word_swap_header_survives():
    # 'NEO-GEO' at 0x100 in CPU order round-trips through the file order.
    cpu = bytearray(0x200)
    cpu[0x100:0x107] = b'NEO-GEO'
    as_file = extract.word_swap(bytes(cpu))
    assert extract.word_swap(as_file)[0x100:0x107] == b'NEO-GEO'


def test_load_hashes_is_complete():
    hashes = extract.load_hashes()
    assert len(hashes) == 9
    assert '200-p1.p1' in hashes
    assert all(len(h) == 64 and set(h) <= set('0123456789abcdef')
               for h in hashes.values())


def test_open_rom_source_zip(tmp_path):
    zip_path = tmp_path / 'fake.zip'
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr('dir/200-p1.p1', b'hello')
    roms = extract.open_rom_source(zip_path)
    assert roms == {'200-p1.p1': b'hello'}


def test_extract_rejects_bad_sha256(tmp_path):
    rom_dir = tmp_path / 'roms'
    rom_dir.mkdir()
    for name in extract.load_hashes():
        (rom_dir / name).write_bytes(b'\x00' * 8)
    with pytest.raises(SystemExit, match='sha256 mismatch'):
        extract.main(['--roms', str(rom_dir), '--out', str(tmp_path / 'build')])


# ------------------------------------------------------- data regions

def test_data_regions_in_windows_and_non_overlapping():
    regions = []
    for line in (REPO / 'config' / 'data_regions.tsv').read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        addr_s, size_s, name, _note = line.split('\t')[:4]
        addr, size = int(addr_s, 16), int(size_s, 16)
        assert size > 0
        in_fixed = 0 <= addr and addr + size <= 0x100000
        in_bank = 0x200000 <= addr and addr + size <= 0x300000
        assert in_fixed or in_bank, f'{name} outside the P-ROM windows'
        regions.append((addr, addr + size, name))
    regions.sort()
    for (a1, e1, n1), (a2, e2, n2) in zip(regions, regions[1:]):
        assert e1 <= a2, f'regions overlap: {n1} vs {n2}'


# ------------------------------------------------------- ghidra_import

def test_convert_line_battery():
    cases = [
        ('00002908\ttst.l D0',                 'tst.l d0'),
        ('beq.w 0x00002c1e',                   'beq.w $00002c1e'),
        ('bpl.b 0x000027d6',                   'bpl.s $000027d6'),
        ('moveq 0x0,D7',                       'moveq #$0,d7'),
        ('move.l (0x00102c40).l,D0',           'move.l ($00102c40).l,d0'),
        ('jsr 0x00002844.l',                   'jsr ($00002844).l'),
        ('move.w D7w,-(SP)',                   'move.w d7,-(a7)'),
        ('not.b D7b',                          'not.b d7'),
        ('cmpi.l #0xb50000,D0',                'cmpi.l #$b50000,d0'),
        ('dbf D0w,0x00001234',                 'dbf d0,$00001234'),
        ('clr.l (0x00102c80).l',               'clr.l ($00102c80).l'),
        ('addq.w #0x1,D0w',                    'addq.w #$1,d0'),
        ('bra.w 0x00007538',                   'bra.w $00007538'),
        ('movem.w { d0 d1},(a2)',              'movem.w d0-d1,(a2)'),
        ('movem.l {  a2 a1},-(a7)',            'movem.l a2/a1,-(a7)'),
        ('0000811e\tlea (0x48,PC),A0',         'lea ($8168,PC),a0'),
        ('0000945e\tlea (-0x358,PC),A0',       'lea ($9108,PC),a0'),
        ('moveq -0x1,D7',                      'moveq #-$1,d7'),
    ]
    for ghidra_line, vasm_line in cases:
        assert ghidra_import.convert_line(ghidra_line) == vasm_line, ghidra_line


def test_convert_line_skips_metadata():
    assert ghidra_import.convert_line('; a comment') is None
    assert ghidra_import.convert_line('') is None
    assert ghidra_import.convert_line('--') is None
    assert ghidra_import.convert_line('00002912\t--  Flow Override: CALL_RETURN') is None


def test_import_one_roundtrip(tmp_path):
    listing = tmp_path / '00002908.s'
    listing.write_text('00002908\ttst.l D0\n0000290a\tbeq.w 0x00002c1e\n'
                       '0000290e\trts\n')
    out_dir = tmp_path / 'out'
    out_dir.mkdir()
    fixed = bytearray(0x3000)
    fixed[0x2908:0x2910] = bytes.fromhex('4a80670003124e75')  # tst/beq.w/rts
    kind = ghidra_import.import_one(tmp_path, out_dir, 0x2908, 'FUN_00002908',
                                    8, bytes(fixed), bytes(0x3000))
    assert kind == 'insn'
    produced = (out_dir / '00002908.s').read_text()
    assert ' org $2908\n' in produced
    assert 'FUN_00002908:' in produced
    assert '        beq.w $00002c1e\n' in produced


def test_import_one_short_listing_gets_trailing_fill(tmp_path):
    listing = tmp_path / '00002908.s'
    listing.write_text('00002908\ttst.l D0\n')  # only 2 of 8 bytes listed
    out_dir = tmp_path / 'out'
    out_dir.mkdir()
    fixed = bytearray(0x3000)
    fixed[0x2908:0x2910] = bytes.fromhex('4a80670003124e75')
    kind = ghidra_import.import_one(tmp_path, out_dir, 0x2908, 'FUN_00002908',
                                    8, bytes(fixed), bytes(0x3000))
    assert kind == 'insn'
    produced = (out_dir / '00002908.s').read_text()
    # the unlisted trailing bytes are covered as dc.b data fill
    assert 'dc.b $67,$00,$03,$12,$4e,$75' in produced


def test_import_one_bad_listing_falls_back_to_data(tmp_path):
    listing = tmp_path / '00002908.s'
    listing.write_text('00002908\tmoveq #$5,d0\n')  # does not match the ROM
    out_dir = tmp_path / 'out'
    out_dir.mkdir()
    fixed = bytearray(0x3000)
    fixed[0x2908:0x2910] = bytes.fromhex('4a80670003124e75')
    kind = ghidra_import.import_one(tmp_path, out_dir, 0x2908, 'FUN_00002908',
                                    8, bytes(fixed), bytes(0x3000))
    assert kind == 'data'
    produced = (out_dir / '00002908.s').read_text()
    assert 'dc.b $4a,$80,$67,$00,$03,$12,$4e,$75' in produced


# ------------------------------------------------------------- build_p1

def test_org_of(tmp_path):
    src = tmp_path / 'f.s'
    src.write_text('; comment\n org $200008\nFUN:\n        rts\n')
    assert build_p1.org_of(src) == 0x200008


# ----------------------------------------------- import repair functions

def test_filler_repair_byte_imm_4byte():
    # ori.b #-0x72,(a0): ROM keeps d5 as the don't-care high byte
    span = bytes.fromhex('0010d58e')
    ins = bytes.fromhex('0010008e')
    out = ghidra_import.filler_repair(span, 0, ins, 'ori.b #-$72,(a0)')
    assert out == 'dc.w $0010,$d58e'


def test_filler_repair_indexed_6byte():
    # ori.b #$28,($3c,a2,d0.w*1): imm16 filler is 0x3028
    span = bytes.fromhex('00323028003c')
    ins = bytes.fromhex('00320028003c')
    out = ghidra_import.filler_repair(span, 0, ins, 'ori.b #$28,($3c,a2,d0.w*$1)')
    assert out == 'dc.w $0032,$3028,$003c'


def test_filler_repair_rejects_real_mismatch():
    span = bytes.fromhex('00101234')
    ins = bytes.fromhex('0010008e')
    assert ghidra_import.filler_repair(span, 0, ins, 'ori.b #-$72,(a0)') is None


def test_pcrel_repair_bare_jsr():
    # jsr 0x0000bdc6 rendered bare; ROM is jsr (d16,PC) with disp 0x7100
    span = bytes.fromhex('4eba71004eba')
    out = ghidra_import.pcrel_repair(span, 0, 0x4cc4, 'jsr $0000bdc6', None)
    # target = 0x4cc4 + 2 + 0x7100 = 0xbdc6 (matches the rendered target)
    assert out == 'jsr ($bdc6,PC)'


def test_pcrel_repair_paren_form():
    span = bytes.fromhex('4ebaefa84eba')
    out = ghidra_import.pcrel_repair(span, 0, 0x84b2, 'jsr ($000074c0).l', None)
    # disp 0xefa8 -> target = 0x84b2 + 2 - 0x1058 = 0x745c
    assert out == 'jsr ($745c,PC)'


def test_pcrel_repair_rejects_absolute_rom():
    span = bytes.fromhex('4eb90000bdc6')
    assert ghidra_import.pcrel_repair(span, 0, 0x4c46, 'jsr $0000bdc6', None) is None


def test_vasm_byte_exact(tmp_path):
    vasm = REPO / 'tools' / 'vasm' / 'vasmm68k_mot'
    if not vasm.exists():
        pytest.skip('vasm not built (run tools/setup_vasm.sh)')
    src = tmp_path / 't.s'
    src.write_text(' org $2908\nFUN:\n tst.l d0\n beq.w $2c1e\n rts\n')
    out_bin = tmp_path / 't.bin'
    subprocess.run([str(vasm), '-Fbin', '-no-opt', '-quiet',
                    '-o', str(out_bin), str(src)], check=True)
    # tst.l d0 = 4a80, beq.w +0x0312 = 6700 0312, rts = 4e75
    assert out_bin.read_bytes() == bytes.fromhex('4a80670003124e75')


# ----------------------------------------------------------- mame_accept

import mame_accept  # noqa: E402


def test_mame_accept_pass():
    ok, frames, notes = mame_accept.check(
        'milestone\tframe\ngame_entry\t300\ntitle_phase\t310\n'
        'attract\t2400\nshot_fired\t4200\n')
    assert ok and not notes
    assert frames['shot_fired'] == 4200


def test_mame_accept_missing_milestone():
    ok, frames, notes = mame_accept.check(
        'milestone\tframe\ngame_entry\t300\ntitle_phase\t310\nattract\t2400\n')
    assert not ok and 'missing: shot_fired' in notes[0]


def test_mame_accept_rejects_out_of_order():
    ok, _, notes = mame_accept.check(
        'milestone\tframe\ngame_entry\t300\ntitle_phase\t310\n'
        'attract\t2400\nshot_fired\t100\n')
    assert not ok and any('out of boot order' in n for n in notes)


# ------------------------------------------------------- m1_family_scan

import m1_family_scan  # noqa: E402


def test_m1_family_scan_full_match_single_run():
    a = bytes(range(256)) * 4
    n, cov = m1_family_scan.shared_coverage(a, m1_family_scan.windows(a))
    assert n == len(a)
    assert m1_family_scan.runs(cov, minlen=16) == [(0, len(a))]


def test_m1_family_scan_flags_divergence():
    a = bytes(range(256))
    b = bytearray(a)
    b[100] = a[100] ^ 0xFF
    n, cov = m1_family_scan.shared_coverage(a, m1_family_scan.windows(bytes(b)))
    # only byte 100 loses every covering window; its neighbors keep one
    assert n == len(a) - 1
    rs = m1_family_scan.runs(cov, minlen=16)
    assert rs == [(0, 100), (101, len(a))]


# ------------------------------------------------------- unidasm_spans

import unidasm_spans  # noqa: E402


def test_unidasm_spans_convert_dialect():
    c = unidasm_spans.convert
    assert c(0x4c96, 4, 'beq $d6f6') == 'beq.w $0000d6f6'
    assert c(0x70ff4, 2, 'bra $71012') == 'bra.s $00071012'
    assert c(0x854d6, 6, 'move.b d0, $300001.l') == 'move.b d0,($300001).l'
    assert c(0x854c8, 4, 'jsr ($85a74,pc)') == 'jsr ($85a74,PC)'
    assert c(0x554d6, 6, 'move.b #$ff, ($11,a0)') == 'move.b #-$1,($11,a0)'
    assert c(0x8559a, 2, 'move.w #$fff, d4') == 'move.w #$fff,d4'
    assert c(0x688, 4, 'dbf d0,$688') == 'dbf d0,$00000688'


def test_unidasm_spans_resolves_pc_index_disp():
    # MAME leaves (d8,PC,Xn) displacements raw; the target must be resolved
    out = unidasm_spans.convert(0x6a5c4, 6, 'move.l ($a,pc,d0.w), ($2c,a6)')
    assert out == 'move.l ($6a5d0,PC,d0.w),($2c,a6)'


def test_unidasm_spans_split_operands_keeps_parens():
    assert unidasm_spans.split_operands('($a,pc,d0.w), ($2c,a6)') == \
        ['($a,pc,d0.w)', '($2c,a6)']


# ------------------------------------------------------------- build_c

import build_c  # noqa: E402


def test_build_c_span_verdict_flags_short_spans():
    assert build_c.span_verdict(b'', b'', 10) == (False, 'empty span')
    assert build_c.span_verdict(b'ab', b'ab', 10) == (False, 'span 2 < declared 10')
    assert build_c.span_verdict(b'ab', b'ab', 2) == (True, None)
    assert build_c.span_verdict(b'abc', b'abc', 2) == (True, None)
    ok, note = build_c.span_verdict(b'ac', b'ab', 2)
    assert ok is False and note is None


def test_build_c_rejects_asm_smuggling():
    assert build_c.reject_source('int f(void){return 0;}') is None
    assert build_c.reject_source('__asm("moveq #0,d0");') is not None
    assert build_c.reject_source('int x = 1; #embed "f.bin"') is not None
    assert build_c.reject_source('char *s = "x.incbin y";') is not None


# -------------------------------------------------- rom_fingerprint_scan

import rom_fingerprint_scan as rfs  # noqa: E402


def test_rfs_word_swap_roundtrip():
    a = bytes(range(32))
    assert rfs.word_swap(rfs.word_swap(a)) == a


def test_rfs_equal_run_bounds():
    a = b'xxxxHELLOWORLDxxxx'
    b = b'yyyyHELLOWORLDyyyy'
    # anchor inside the common region (H at 4/4); run = 10 either side
    assert rfs.equal_run(a, b, 4, 4) == 10
    assert rfs.equal_run(a, b, 0, 0) == 0


def test_rfs_uses_requested_repeated_anchor():
    source = b'abcdBAD----abcdGOOD'
    target = b'!!!!abcdGOOD'
    hits = list(rfs.find_runs(source, target, 11, 4, 8))
    assert hits == [(4, (11, 19, 4, 12))]


def test_rfs_does_not_skip_hits_after_long_left_extension():
    source = b'A' * 10
    target = b'A' * 11
    assert [off for off, _ in rfs.find_runs(source, target, 8, 2, 10)] == [8, 9]


@pytest.mark.parametrize('addr,length', [(0, 0), (-1, 2), (2, 4)])
def test_rfs_rejects_invalid_windows(addr, length):
    with pytest.raises(ValueError):
        list(rfs.find_runs(b'abcd', b'abcd', addr, length, 1))


def test_coverage_union_handles_nested_duplicate_and_adjacent_spans():
    assert build_p1.union_size([(10, 20), (12, 15), (10, 20),
                               (20, 25), (30, 40)]) == 25
    assert build_p1.union_size([]) == 0


def test_duplicate_sources_cannot_prove_complete_rom_coverage():
    assert build_p1.union_size([(0, 0x100000)] * 2) == 0x100000


def test_span_window_rejects_overflow_and_empty():
    images = {0: bytes(16), 0x200000: bytes(16)}
    assert build_p1.span_window(14, 2, images) == 0
    assert build_p1.span_window(0x200000, 16, images) == 0x200000
    for addr, size in [(14, 4), (0, 0), (0x100000, 2), (0x20000f, 2)]:
        with pytest.raises(ValueError):
            build_p1.span_window(addr, size, images)


@pytest.mark.parametrize('check', [False, True])
def test_failed_p1_build_does_not_publish_rom_or_proof(tmp_path, monkeypatch, check):
    import hashlib
    work = tmp_path / 'build/work'
    work.mkdir(parents=True)
    for half in ['fixed', 'bank']:
        (work / f'p1.{half}.cpu.bin').write_bytes(b'\0' * 4)
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'rom.sha256').write_text(hashlib.sha256(b'\0' * 8).hexdigest()
                                     + '  200-p1.p1\n')
    sources = tmp_path / 'src'
    sources.mkdir()
    (sources / 'bad.s').write_text(' org $0\n')
    output = tmp_path / 'build/p1.p1'
    manifest = tmp_path / 'build/p1-spans.json'
    output.write_bytes(b'previous output')
    manifest.write_text('stale proof')
    monkeypatch.setattr(build_p1, 'REPO', tmp_path)
    monkeypatch.setattr(build_p1, 'WORK', work)
    monkeypatch.setattr(build_p1, 'SRC_DIR', sources)
    monkeypatch.setattr(build_p1, 'DATA_DIR', tmp_path / 'no-data')
    monkeypatch.setattr(build_p1, 'GEN_DIR', tmp_path / 'gen')
    monkeypatch.setattr(build_p1, 'BLOCK_DIR', tmp_path / 'no-blocks')
    # INC_DIR is computed from REPO at import time: patch it too, or the
    # generated symbols.i include clobbers the real build/gen/p1/symbols.i
    monkeypatch.setattr(build_p1, 'INC_DIR', tmp_path / 'gen-inc')
    monkeypatch.setattr(build_p1, 'VASM', sources / 'bad.s')
    monkeypatch.setattr(build_p1, 'assemble', lambda src, out: out.write_bytes(b'\1\2'))
    monkeypatch.setattr(sys, 'argv', ['build_p1.py', '--tmp', str(tmp_path / 'scratch')]
                        + (['--check'] if check else []))
    with pytest.raises(SystemExit):
        build_p1.main()
    assert not output.exists()
    assert not manifest.exists()


def test_divide_call_candidates_use_signed_pc_relative_displacements():
    from divide_contracts import direct_calls
    # Synthetic code: absolute call to 0; bsr.w back to 0; bsr.s back to 0.
    blob = bytes.fromhex('4eb9000000006100fff861f4')
    assert direct_calls(blob, 0) == [
        {'address': '0x000000', 'encoding': 'jsr.l'},
        {'address': '0x000006', 'encoding': 'bsr.w'},
        {'address': '0x00000a', 'encoding': 'bsr.s'},
    ]


@pytest.mark.parametrize('failure_stage', [0, 1, 2])
def test_c_check_fails_on_compiler_linker_or_objcopy_error(tmp_path, monkeypatch, failure_stage):
    from types import SimpleNamespace
    work = tmp_path / 'work'
    work.mkdir()
    for half in ['fixed', 'bank']:
        (work / f'p1.{half}.cpu.bin').write_bytes(b'\0' * 4)
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'functions.tsv').write_text('00000000\tf\t2\n')
    sources = tmp_path / 'c'
    sources.mkdir()
    (sources / '00000000.c').write_text('int f(void) { return 0; }')
    monkeypatch.setattr(build_c, 'REPO', tmp_path)
    monkeypatch.setattr(build_c, 'WORK', work)
    monkeypatch.setattr(build_c, 'C_DIR', sources)
    monkeypatch.setattr(build_c, 'tool', lambda name: name)
    results = iter([SimpleNamespace(returncode=0, stderr='')] * failure_stage
                   + [SimpleNamespace(returncode=1, stderr='synthetic tool failure')])
    monkeypatch.setattr(build_c.subprocess, 'run', lambda *a, **kw: next(results))
    monkeypatch.setattr(sys, 'argv', ['build_c.py', '--check', '--tmp', str(tmp_path / 'scratch')])
    with pytest.raises(SystemExit) as exc:
        build_c.main()
    assert exc.value.code == 1
