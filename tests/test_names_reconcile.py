import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
import names_reconcile


def test_canonical_wins_and_comments_follow(tmp_path):
    ram = tmp_path / 'config' / 'ram'
    ram.mkdir(parents=True)
    (tmp_path / 'config' / 'names').mkdir()
    (tmp_path / 'config' / 'names.tsv').write_text('')
    (ram / 'hardware.tsv').write_text('0010fd83\t1\tBIOS_COUNTRY_CODE\tngdevkit\n')
    (ram / 'name-1.tsv').write_text('00100000\t2\tscore\tx\n')
    (ram / 'name-2.tsv').write_text('# h\n0010fd83\t1\tbios_mode\tx\n00100000\t2\ttotal\ty\n00100010\t2\tscore\tz\n00100020\t2\tlives\tw\n')
    renames, dropped = names_reconcile.reconcile('ram', 'name-2', tmp_path)
    assert renames == {'bios_mode': 'BIOS_COUNTRY_CODE', 'total': 'score'}
    assert (ram / 'name-2.tsv').read_text().splitlines()[1:] == [
        '00100010\t2\tscore_100010\tz', '00100020\t2\tlives\tw']
    src = tmp_path / 'src' / 'p1' / 'asm' / 'functions'
    src.mkdir(parents=True)
    (src / 'a.s').write_text('; reads bios_mode\n move.b ($10fd83).l,d0\n')
    assert names_reconcile.rewrite_sources({'bios_mode': 'BIOS_COUNTRY_CODE', 'total': 'score'}, tmp_path) == 1
    assert (src / 'a.s').read_text() == '; reads BIOS_COUNTRY_CODE\n move.b ($10fd83).l,d0\n'


def test_losing_name_still_used_elsewhere_is_not_rewritten(tmp_path):
    ram = tmp_path / 'config' / 'ram'
    names = tmp_path / 'config' / 'names'
    ram.mkdir(parents=True)
    names.mkdir()
    (tmp_path / 'config' / 'names.tsv').write_text('')
    (ram / 'hardware.tsv').write_text('')
    (names / 'name-1.tsv').write_text('0000958c\tpin_distance\tfunction\n')
    (ram / 'name-1.tsv').write_text('0010d4c6\t4\ttarget_distance\tx\n')
    (ram / 'name-5.tsv').write_text('0010d4c6\t4\tpin_distance\ty\n')
    renames, dropped = names_reconcile.reconcile('ram', 'name-5', tmp_path)
    assert renames == {} and dropped == [(0x10d4c6, 'pin_distance', 'target_distance')]
