import hashlib
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
import semantic_apply_rows
import semantic_inventory
import semantic_rebind
import semantic_review_fill


def _source(tmp_path):
    directory = tmp_path / 'src' / 'p1' / 'asm' / 'blocks'
    directory.mkdir(parents=True)
    (directory / '00000010.s').write_text(' org $10\n rts\n')
    return semantic_inventory.code_inventory(tmp_path)


def test_later_review_supersedes_only_by_naming_the_earlier_report(tmp_path):
    rows = _source(tmp_path)
    old = dict(rows[0], verdict='uncertain', evidence='question: x', report='docs/lanes/a.md')
    new = dict(rows[0], verdict='correct', evidence='0x10: rts', report='docs/lanes/b.md')
    with pytest.raises(ValueError, match='duplicate'):
        semantic_inventory.apply_reviews(rows, [old, new])
    new['supersedes'] = 'docs/lanes/a.md'
    for order in ([old, new], [new, old]):
        result = semantic_inventory.apply_reviews(rows, order)[0]
        assert result['status'] == 'reviewed' and result['report'] == 'docs/lanes/b.md'


def test_fill_binds_source_and_region_row_digests(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic_review_fill, 'REPO', tmp_path)
    _source(tmp_path)
    regions = tmp_path / 'config' / 'regions' / 'm1'
    regions.mkdir(parents=True)
    row = '0020\t0004\tfill:00\tpad\tfour zeros, unreferenced'
    (regions / 'm1.tsv').write_text('# header\n' + row + '\n')
    code = semantic_review_fill.fill({'path': 'src/p1/asm/blocks/00000010.s', 'verdict': 'corrected',
                                      'report': 'r.md', 'evidence': 'e'})
    assert code['cpu'] == 'p1' and code['address'] == '0x000010' and code['integrated'] is True
    data = semantic_review_fill.fill({'path': 'config/regions/m1/m1.tsv', 'row': '0x20',
                                      'verdict': 'correct', 'report': 'r.md', 'evidence': 'e'})
    assert data['cpu'] == 'm1' and data['address'] == '0x000020'
    assert data['sha256'] == hashlib.sha256(row.encode()).hexdigest()
    with pytest.raises(ValueError, match='bad verdict'):
        semantic_review_fill.fill({'path': 'src/p1/asm/blocks/00000010.s', 'verdict': 'probably',
                                   'report': 'r.md', 'evidence': 'e'})


def test_rows_patch_may_change_evidence_only(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic_apply_rows, 'REPO', tmp_path)
    regions = tmp_path / 'config' / 'regions' / 'p1'
    regions.mkdir(parents=True)
    (regions / 'lo.tsv').write_text('000144\t000040\tfill:00\tpad\tcode at 0x184\n')
    patch = tmp_path / 'semantic-data-p1-lo-rows.tsv'
    patch.write_text('000144\t000040\tfill:00\tpad\tzeros before the pointer at 0x182\n')
    semantic_apply_rows.main([str(patch), '--write'])
    assert 'pointer at 0x182' in (regions / 'lo.tsv').read_text()
    patch.write_text('000144\t00003e\tfill:00\tpad\tzeros\n')
    with pytest.raises(SystemExit, match='only evidence'):
        semantic_apply_rows.main([str(patch)])


def test_rebind_substitutes_whole_words_like_rename_symbol(tmp_path):
    pairs = tmp_path / 'pairs.tsv'
    pairs.write_text('# batch\nold_name new_name\n- dropped\n')
    renames = semantic_rebind.pairs([pairs])
    assert renames == [('old_name', 'new_name'), ('dropped', False)]
    text = ' jsr (old_name,PC)\n; old_name_body old_name+$4\n'
    assert semantic_rebind.substitute(text, renames) == \
        ' jsr (new_name,PC)\n; old_name_body new_name+$4\n'
