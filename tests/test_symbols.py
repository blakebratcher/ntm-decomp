import pathlib
import sys
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
import symbols


def setup(tmp_path, main='', lanes=None, ram=None):
    (tmp_path / 'config' / 'names').mkdir(parents=True)
    (tmp_path / 'config' / 'ram').mkdir()
    (tmp_path / 'config' / 'names.tsv').write_text(main)
    for k, v in (lanes or {}).items():
        (tmp_path / 'config' / 'names' / k).write_text(v)
    for k, v in (ram or {}).items():
        (tmp_path / 'config' / 'ram' / k).write_text(v)


def test_merges_lane_files_and_repo_table(tmp_path):
    setup(tmp_path, '# h\n00000100\tboot\tentry\n', {'a.tsv': '00000200\tdraw_hud\tcalls fix writer\n'})
    assert symbols.load_names(tmp_path) == {0x100: ('boot', 'entry'), 0x200: ('draw_hud', 'calls fix writer')}


@pytest.mark.parametrize('lanes,msg', [
    ({'a.tsv': '00000100\tx\tn\n', 'b.tsv': '00000100\ty\tn\n'}, 'named'),
    ({'a.tsv': '00000100\tx\tn\n', 'b.tsv': '00000200\tx\tn\n'}, 'names'),
    ({'a.tsv': '00000100\t9bad\tn\n'}, 'bad name'),
])
def test_conflicts_rejected(tmp_path, lanes, msg):
    setup(tmp_path, '', lanes)
    with pytest.raises(ValueError, match=msg):
        symbols.load_names(tmp_path)


def test_ram_table(tmp_path):
    setup(tmp_path, ram={'a.tsv': '0010fdb4\t1\tstart_latch\tset by coin dispatch\n'})
    assert symbols.load_ram(tmp_path) == {0x10fdb4: ('start_latch', (1, 'set by coin dispatch'))}


def test_repo_namespaces_do_not_collide():
    symbols.check_namespaces()


def test_namespace_clash_detected(tmp_path):
    setup(tmp_path, '00000100\tscore\tfn\n', ram={'a.tsv': '00100000\t2\tscore\tvar\n'})
    with pytest.raises(ValueError, match='score'):
        symbols.check_namespaces(tmp_path)
