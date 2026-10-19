import pathlib
import sys
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
import rom_coverage as coverage_intervals


def test_intervals_nested_and_cross_window_gaps():
    spans = [(2, 8), (3, 4), (8, 10), (22, 24)]
    assert coverage_intervals.gaps(spans, [(0, 10), (20, 30)]) == [(0, 2), (20, 22), (24, 30)]
    assert coverage_intervals.size(spans) == 10


def test_duplicates_do_not_close_missing_bank():
    assert coverage_intervals.gaps([(0, 16)] * 2, [(0, 16), (32, 48)]) == [(32, 48)]


def test_out_of_order_and_invalid_intervals():
    assert coverage_intervals.merge([(8, 10), (2, 8), (4, 4)]) == [(2, 10)]
    with pytest.raises(ValueError):
        coverage_intervals.merge([(3, 2)])

@pytest.mark.parametrize('cpu,total,bases', [('p1',0x200000,[0,0x200000]),('m1',0x20000,[0])])
@pytest.mark.parametrize('complete', [False, True])
def test_source_only_uses_sources_and_rejects_zero_filled_holes(tmp_path, monkeypatch, cpu, total, bases, complete):
    import hashlib
    import importlib
    module = importlib.import_module('build_' + cpu)
    sources = tmp_path / 'src'
    sources.mkdir()
    (tmp_path / 'build').mkdir()
    (tmp_path / 'config').mkdir()
    name = '200-p1.p1' if cpu == 'p1' else '200-m1.m1'
    (tmp_path / 'config/rom.sha256').write_text(hashlib.sha256(bytes(total)).hexdigest() + '  ' + name + '\n')
    for base in bases:
        (sources / f'{base:08x}.s').write_text(f' org ${base:x}\n')
    monkeypatch.setattr(module, 'REPO', tmp_path)
    monkeypatch.setattr(module, 'SRC_DIR', sources)
    monkeypatch.setattr(module, 'BLOCK_DIR', tmp_path / 'no-blocks')
    monkeypatch.setattr(module, 'DATA_DIR', tmp_path / 'no-data')
    monkeypatch.setattr(module, 'GEN_DIR', tmp_path / 'gen')
    # INC_DIR is computed from REPO at import time: patch it too, or the
    # generated symbols.i include would land in (and clobber) the real
    # build/gen/p1 of the checkout running the test
    if hasattr(module, 'INC_DIR'):
        monkeypatch.setattr(module, 'INC_DIR', tmp_path / 'gen-inc')
    monkeypatch.setattr(module, 'WORK', tmp_path / 'no-pristine-inputs')
    monkeypatch.setattr(module, 'VASM', next(sources.iterdir()))
    span_size = total // len(bases) if complete else 2
    monkeypatch.setattr(module, 'assemble', lambda src, out: out.write_bytes(bytes(span_size)))
    monkeypatch.setattr(sys, 'argv', [f'build_{cpu}.py','--source-only','--tmp',str(tmp_path / 'scratch')])
    if complete:
        module.main()
        assert (tmp_path / ('build/p1.p1' if cpu == 'p1' else 'build/200-m1.m1')).read_bytes() == bytes(total)
    else:
        with pytest.raises(SystemExit, match='incomplete'):
            module.main()
        assert not (tmp_path / f'build/{cpu}-spans.json').exists()


def test_cross_overlaps_flag_only_mixed_categories():
    import completion
    src = [{'org': 0, 'size': 8, 'category': 'function', 'source': 'a'},
           {'org': 4, 'size': 8, 'category': 'fill', 'source': 'b'},
           {'org': 20, 'size': 4, 'category': 'function', 'source': 'c'},
           {'org': 22, 'size': 4, 'category': 'function', 'source': 'd'}]
    assert completion.cross_overlaps(src) == [
        {'start': '0x000004', 'end': '0x000008', 'categories': ['fill', 'function'], 'sources': ['a', 'b']}]
