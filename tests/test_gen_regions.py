import pathlib
import sys
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
import gen_regions


def write(root, cpu, text):
    d = root / 'config' / 'regions' / cpu
    d.mkdir(parents=True, exist_ok=True)
    (d / 'a.tsv').write_text(text)


def test_fill_needs_no_rom_and_data_reads_it(tmp_path):
    write(tmp_path, 'p1', '# c\n00000010\t4\tfill:ff\tpad_a\tconstant run after rts\n'
                          '00200000\t3\tdata\ttab_b\tread by 0x1234\n')
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'p1.fixed.cpu.bin').write_bytes(bytes(0x20))
    (work / 'p1.bank.cpu.bin').write_bytes(b'\x01\x02\x03\x04')
    out = tmp_path / 'gen'
    rows = gen_regions.generate('p1', out, tmp_path, work)
    assert [r['name'] for r in rows] == ['pad_a', 'tab_b']
    fill = (out / '00000010_pad_a.s').read_text()
    assert ' org $10' in fill and 'dcb.b 4,$ff' in fill and '; kind: fill:ff' in fill
    assert ' dc.b $01,$02,$03' in (out / '00200000_tab_b.s').read_text()


def test_m1_dialect(tmp_path):
    write(tmp_path, 'm1', '00000100\t2\tfill:00\tz\tunreferenced pad\n')
    out = tmp_path / 'gen'
    gen_regions.generate('m1', out, tmp_path, tmp_path)
    text = (out / '00000100_z.s').read_text()
    assert ' .org 0x100' in text and '.space 2,0x00' in text


@pytest.mark.parametrize('rows,msg', [
    ('00000000\t10\tdata\ta\tx\n00000008\t4\tdata\tb\ty\n', 'overlaps'),
    ('000ffff0\t20\tdata\ta\tx\n', 'outside'),
    ('00000000\t0\tdata\ta\tx\n', 'outside'),
    ('00000000\t4\tdata\ta\t\n', 'evidence'),
    ('00000000\t4\tcode\ta\tx\n', 'unknown kind'),
    ('00000000\t4\tdata?\ta\tx\n', 'unknown kind'),
    ('00000000\t4\tdata\ta\tx\n00000010\t4\tdata\ta\ty\n', 'duplicate'),
])
def test_rejects_bad_tables(tmp_path, rows, msg):
    write(tmp_path, 'p1', rows)
    with pytest.raises(ValueError, match=msg):
        gen_regions.load('p1', tmp_path)


def test_unref_is_built_like_data(tmp_path):
    write(tmp_path, 'p1', '00000000\t2\tunref\tu\t16-bit monotonic table, no consumer found\n')
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'p1.fixed.cpu.bin').write_bytes(b'\x01\x02')
    (work / 'p1.bank.cpu.bin').write_bytes(b'')
    out = tmp_path / 'gen'
    gen_regions.generate('p1', out, tmp_path, work)
    text = (out / '00000000_u.s').read_text()
    assert '; kind: unref' in text and ' dc.b $01,$02' in text
