import hashlib
import json
import pathlib
import sys
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
import semantic_inventory


def test_inventory_includes_both_cpus_and_pins_full_source(tmp_path):
    for cpu in ('p1', 'm1'):
        directory = tmp_path / 'src' / cpu / 'asm' / 'blocks'
        directory.mkdir(parents=True)
        source = directory / '00000010.s'
        source.write_text('; purpose unknown\n org $10\n nop\n')
    rows = semantic_inventory.code_inventory(tmp_path)
    assert len(rows) == 2
    assert {row['cpu'] for row in rows} == {'p1', 'm1'}
    assert all(row['address'] == '0x000010' for row in rows)
    assert all(row['status'] == 'unreviewed' for row in rows)
    assert rows[0]['sha256'] == hashlib.sha256(
        (tmp_path / rows[0]['path']).read_bytes()).hexdigest()


def test_review_must_match_source_digest_and_contain_evidence(tmp_path):
    directory = tmp_path / 'src' / 'p1' / 'asm' / 'functions'
    directory.mkdir(parents=True)
    source = directory / '00000010.s'
    source.write_text(' org $10\n rts\n')
    rows = semantic_inventory.code_inventory(tmp_path)
    review = dict(rows[0], verdict='correct', evidence='0x10: rts',
                  report='docs/lanes/review.md')
    assert semantic_inventory.apply_reviews(rows, [review])[0]['status'] == 'reviewed'
    review['sha256'] = '0' * 64
    assert semantic_inventory.apply_reviews(rows, [review])[0]['status'] == 'stale'
    review['sha256'] = rows[0]['sha256']
    review['evidence'] = ''
    assert semantic_inventory.apply_reviews(rows, [review])[0]['status'] == 'invalid'


def test_uncertain_review_does_not_count_as_complete(tmp_path):
    directory = tmp_path / 'src' / 'm1' / 'asm' / 'blocks'
    directory.mkdir(parents=True)
    (directory / '00000010.s').write_text(' org $10\n ret\n')
    rows = semantic_inventory.code_inventory(tmp_path)
    review = dict(rows[0], verdict='uncertain', evidence='0x10: ret',
                  report='docs/lanes/review.md')
    assert semantic_inventory.apply_reviews(rows, [review])[0]['status'] == 'uncertain'


def test_duplicate_reviews_are_rejected(tmp_path):
    directory = tmp_path / 'src' / 'p1' / 'asm' / 'blocks'
    directory.mkdir(parents=True)
    (directory / '00000010.s').write_text(' org $10\n rts\n')
    rows = semantic_inventory.code_inventory(tmp_path)
    review = dict(rows[0], verdict='correct', evidence='0x10: rts',
                  report='docs/lanes/review.md')
    with pytest.raises(ValueError, match='duplicate'):
        semantic_inventory.apply_reviews(rows, [review, review])


@pytest.mark.parametrize('directive', [' org 0000h', ' .org 0x0'])
def test_z80_org_is_recognized(tmp_path, directive):
    directory = tmp_path / 'src' / 'm1' / 'asm' / 'functions'
    directory.mkdir(parents=True)
    (directory / '00000000.s').write_text(directive + '\n ret\n')
    assert semantic_inventory.code_inventory(tmp_path)[0]['address'] == '0x000000'


def test_region_classification_evidence_is_not_semantic_review(tmp_path):
    directory = tmp_path / 'config' / 'regions' / 'p1'
    directory.mkdir(parents=True)
    line = '000010\t000004\tdata\ttable\tconsumer at 0x20'
    (directory / 'lane.tsv').write_text('# header\n' + line + '\n')
    rows = semantic_inventory.data_inventory(tmp_path)
    assert rows[0]['size'] == 4
    assert rows[0]['classification_evidence'] == 'consumer at 0x20'
    assert rows[0]['status'] == 'unreviewed'
    assert rows[0]['sha256'] == hashlib.sha256(line.encode()).hexdigest()


def test_unintegrated_correction_is_pending(tmp_path):
    directory = tmp_path / 'src' / 'p1' / 'asm' / 'functions'
    directory.mkdir(parents=True)
    (directory / '00000010.s').write_text(' org $10\n rts\n')
    rows = semantic_inventory.code_inventory(tmp_path)
    review = dict(rows[0], verdict='corrected', evidence='0x10: rts',
                  report='docs/lanes/review.md')
    assert semantic_inventory.apply_reviews(rows, [review])[0]['status'] == 'correction-pending'
    review['integrated'] = True
    assert semantic_inventory.apply_reviews(rows, [review])[0]['status'] == 'reviewed'


def test_reviews_for_deleted_sources_are_rejected():
    with pytest.raises(ValueError, match='missing inventory'):
        semantic_inventory.apply_reviews([], [{'cpu': 'p1', 'path': 'gone.s',
                                                'address': '0x000010'}])


def test_review_report_requires_one_explicit_json_array(tmp_path):
    path = tmp_path / 'review.md'
    rows = [{'cpu': 'p1', 'address': '0x000010', 'path': 'source.s'}]
    path.write_text('# Audit\n```json\n' + json.dumps(rows) + '\n```\n')
    assert semantic_inventory.load_reviews(path) == rows
    path.write_text('# An unstructured count is not review records\n')
    with pytest.raises(ValueError, match='one JSON array'):
        semantic_inventory.load_reviews(path)


def test_review_report_rejects_ambiguous_arrays(tmp_path):
    path = tmp_path / 'review.md'
    path.write_text('```json\n[]\n```\n```json\n[]\n```\n')
    with pytest.raises(ValueError, match='one JSON array'):
        semantic_inventory.load_reviews(path)


def test_string_integration_flag_does_not_approve_correction(tmp_path):
    directory = tmp_path / 'src' / 'p1' / 'asm' / 'functions'
    directory.mkdir(parents=True)
    (directory / '00000010.s').write_text(' org $10\n rts\n')
    rows = semantic_inventory.code_inventory(tmp_path)
    review = dict(rows[0], verdict='corrected', evidence='0x10: rts',
                  report='docs/lanes/review.md', integrated='false')
    assert semantic_inventory.apply_reviews(rows, [review])[0]['status'] == 'correction-pending'


@pytest.mark.skipif(
    not (pathlib.Path(__file__).resolve().parents[1] / 'src' / 'p1' / 'data' / 'frame_bank.s').exists(),
    reason='generated data sections missing; run make extract')
def test_every_obligation_has_a_current_review():
    """The digest-bound inventory over the lane review files is complete: every
    code source, region row and legacy data source has a review record whose
    digest matches (edit a reviewed source -> re-review it and rebind with
    tools/semantic_review_fill.py). Uncertain is an allowed, explicit status."""
    root = pathlib.Path(__file__).resolve().parents[1]
    reviews = [r for p in semantic_inventory.lane_review_files(root)
               for r in semantic_inventory.load_reviews(p)]
    rows = semantic_inventory.apply_reviews(
        semantic_inventory.code_inventory(root) + semantic_inventory.data_inventory(root), reviews)
    open_rows = [(r['status'], r['path'], r['address']) for r in rows
                 if r['status'] not in ('reviewed', 'uncertain')]
    assert not open_rows, open_rows[:10]


def test_supersession_chains_resolve_to_the_newest_review(tmp_path):
    directory = tmp_path / 'src' / 'p1' / 'asm' / 'blocks'
    directory.mkdir(parents=True)
    (directory / '00000010.s').write_text(' org $10\n rts\n')
    rows = semantic_inventory.code_inventory(tmp_path)
    a = dict(rows[0], verdict='uncertain', evidence='question: x', report='a.md')
    b = dict(rows[0], verdict='uncertain', evidence='question: y', report='b.md', supersedes='a.md')
    c = dict(rows[0], verdict='correct', evidence='0x10: rts', report='c.md', supersedes='b.md')
    for order in ([a, b, c], [c, a, b], [b, c, a]):
        assert semantic_inventory.apply_reviews(rows, order)[0]['status'] == 'reviewed'
    with pytest.raises(ValueError, match='duplicate'):
        semantic_inventory.apply_reviews(rows, [a, c])


def _tree(tmp_path, n=2):
    directory = tmp_path / 'src' / 'p1' / 'asm' / 'blocks'
    directory.mkdir(parents=True)
    for i in range(n):
        (directory / f'000000{i + 1}0.s').write_text(f' org ${i + 1}0\n rts\n')
    (tmp_path / 'docs' / 'lanes').mkdir(parents=True)
    (tmp_path / 'docs' / 'lanes' / 'v.md').write_text('verification\n')
    (tmp_path / 'r.md').write_text('lane report\n')
    return semantic_inventory.code_inventory(tmp_path)


def _accept(tmp_path, applied, **override):
    record = semantic_inventory.acceptance_record(tmp_path, applied, 'docs/lanes/v.md',
                                                  ['docs/lanes/v.md'])
    record.update(override)
    (tmp_path / semantic_inventory.ACCEPTANCE).write_text(json.dumps(record))
    return record


def test_completion_needs_all_reviewed_and_a_matching_acceptance(tmp_path):
    rows = _tree(tmp_path, 1)
    review = dict(rows[0], verdict='correct', evidence='0x10: rts', report='r.md')
    reviewed = semantic_inventory.apply_reviews(rows, [review])
    assert semantic_inventory.completion(tmp_path, reviewed)[0] is False   # no acceptance
    _accept(tmp_path, reviewed)
    assert semantic_inventory.completion(tmp_path, reviewed)[0] is True
    uncertain = semantic_inventory.apply_reviews(rows, [dict(review, verdict='uncertain')])
    assert semantic_inventory.completion(tmp_path, uncertain)[0] is False
    source = tmp_path / rows[0]['path']
    source.write_text(' org $10\n rts ; edited\n')
    rows2 = semantic_inventory.code_inventory(tmp_path)
    rereviewed = semantic_inventory.apply_reviews(rows2, [dict(review, sha256=rows2[0]['sha256'])])
    assert semantic_inventory.completion(tmp_path, rereviewed)[0] is False  # state changed


def test_accepted_residuals_complete_only_when_named_and_pinned(tmp_path):
    rows = _tree(tmp_path)
    reviews = [dict(rows[0], verdict='correct', evidence='0x10: rts', report='r.md'),
               dict(rows[1], verdict='uncertain', evidence='question: q', report='r.md')]
    applied = semantic_inventory.apply_reviews(rows, reviews)
    record = _accept(tmp_path, applied)
    complete, reason = semantic_inventory.completion(tmp_path, applied)
    assert complete is True and '1 accepted residual' in reason
    for bad in ({'residuals': []},                                         # residual not named
                {'residuals': record['residuals'] * 2},                    # duplicated
                {'residuals': [dict(record['residuals'][0], question='other')]},
                {'residuals': record['residuals'] + [dict(record['residuals'][0], address='0x000099')]},
                {'inventory_entries': 3}, {'extra': 1}, {'report_sha256': '0' * 64},
                {'verification': []}, {'reports': {}}):
        _accept(tmp_path, applied, **bad)
        assert semantic_inventory.completion(tmp_path, applied)[0] is False, bad
    _accept(tmp_path, applied)
    (tmp_path / 'docs' / 'lanes' / 'v.md').write_text('changed\n')         # report unpinned
    assert semantic_inventory.completion(tmp_path, applied)[0] is False


def test_digest_covers_the_residual_question(tmp_path):
    directory = tmp_path / 'src' / 'p1' / 'asm' / 'blocks'
    directory.mkdir(parents=True)
    (directory / '00000010.s').write_text(' org $10\n rts\n')
    rows = semantic_inventory.code_inventory(tmp_path)
    a = semantic_inventory.apply_reviews(rows, [dict(rows[0], verdict='uncertain',
                                                     evidence='question: a', report='r.md')])
    b = semantic_inventory.apply_reviews(rows, [dict(rows[0], verdict='uncertain',
                                                     evidence='question: b', report='r.md')])
    assert semantic_inventory.state_digest(a) != semantic_inventory.state_digest(b)


def test_residuals_are_keyed_per_region_row(tmp_path):
    directory = tmp_path / 'config' / 'regions' / 'p1'
    directory.mkdir(parents=True)
    lines = ['000010\t000004\tdata\ta\tconsumer', '000020\t000004\tdata\tb\tconsumer']
    (directory / 'lane.tsv').write_text('\n'.join(lines) + '\n')
    rows = semantic_inventory.data_inventory(tmp_path)
    applied = semantic_inventory.apply_reviews(rows, [
        dict(row, verdict='uncertain', evidence='question', report='r.md') for row in rows])
    (tmp_path / 'docs' / 'lanes').mkdir(parents=True)
    (tmp_path / 'docs' / 'lanes' / 'v.md').write_text('verification\n')
    record = semantic_inventory.acceptance_record(tmp_path, applied, 'docs/lanes/v.md',
                                                  ['docs/lanes/v.md'])
    record['residuals'] = record['residuals'][:1]                         # second row not named
    (tmp_path / semantic_inventory.ACCEPTANCE).write_text(json.dumps(record))
    assert semantic_inventory.completion(tmp_path, applied)[0] is False


@pytest.mark.parametrize('chain', [
    [('a', None), ('b', 'c'), ('c', 'b')],          # disconnected cycle beside a head
    [('a', None), ('b', 'a'), ('c', 'a')],          # fork
    [('a', None), ('b', 'a'), ('c', 'zzz')],        # dangling link off the root
])
def test_broken_supersession_chains_are_rejected(tmp_path, chain):
    directory = tmp_path / 'src' / 'p1' / 'asm' / 'blocks'
    directory.mkdir(parents=True)
    (directory / '00000010.s').write_text(' org $10\n rts\n')
    rows = semantic_inventory.code_inventory(tmp_path)
    reviews = [dict(rows[0], verdict='correct', evidence='0x10: rts', report=r,
                    **({'supersedes': s} if s else {})) for r, s in chain]
    with pytest.raises(ValueError):
        semantic_inventory.apply_reviews(rows, reviews)


def test_a_dangling_link_on_the_chain_root_is_allowed(tmp_path):
    directory = tmp_path / 'src' / 'p1' / 'asm' / 'blocks'
    directory.mkdir(parents=True)
    (directory / '00000010.s').write_text(' org $10\n rts\n')
    rows = semantic_inventory.code_inventory(tmp_path)
    reviews = [dict(rows[0], verdict='uncertain', evidence='question', report='a', supersedes='old.md'),
               dict(rows[0], verdict='correct', evidence='0x10: rts', report='b', supersedes='a')]
    assert semantic_inventory.apply_reviews(rows, reviews)[0]['report'] == 'b'


def test_withdrawing_a_cited_report_clears_the_flag(tmp_path):
    rows = _tree(tmp_path, 1)
    (tmp_path / 'docs' / 'lanes' / 'lane.md').write_text('lane report\n')
    applied = semantic_inventory.apply_reviews(rows, [dict(
        rows[0], verdict='correct', evidence='0x10: rts', report='docs/lanes/lane.md')])
    _accept(tmp_path, applied)
    assert semantic_inventory.completion(tmp_path, applied)[0] is True
    (tmp_path / 'docs' / 'lanes' / 'lane.md').unlink()
    assert semantic_inventory.completion(tmp_path, applied)[0] is False


def test_a_missing_acceptance_report_never_completes(tmp_path):
    rows = _tree(tmp_path, 1)
    applied = semantic_inventory.apply_reviews(rows, [dict(
        rows[0], verdict='correct', evidence='0x10: rts', report='r.md')])
    record = semantic_inventory.acceptance_record(tmp_path, applied, 'does-not-exist.md',
                                                  ['docs/lanes/v.md'])
    assert record['report_sha256'] is None
    (tmp_path / semantic_inventory.ACCEPTANCE).write_text(json.dumps(record))
    assert semantic_inventory.completion(tmp_path, applied)[0] is False
