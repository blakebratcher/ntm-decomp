"""Finish-lane corpus invariants.

Lock in the contracts this lane owns: the finish name/ram tables stay
well-formed and collision-free, the header applier is idempotent (a second
run is a no-op, so it can never drift the sources), the merged symbol
tables keep one namespace for code and RAM, and - once the per-range lanes
have resolved every overlap - no ROM byte has two instruction owners or is
both code and data.

The ownership tests read build/p1-spans.json (written by build_p1) and are
scoped by the NTMD_INV_RANGE environment variable ("<lo>,<hi>" hex): with
the variable set they assert only that window, so each lane can gate its
own range while the other lanes are still merging.  Unset, they cover the
whole ROM - and are skipped for now: flip WHOLE_ROM_SKIP to False only
once every lane's range is clean and merged.
"""
import json
import os
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))

import symbols                               # noqa: E402

NAME = re.compile(r'^[a-z][a-z0-9_]*$')

WHOLE_ROM_SKIP = False  # all per-range lanes merged (2026-10-12)

INSTRUCTION_CATEGORIES = ('function', 'block')
REGION_CATEGORIES = ('data', 'extracted', 'fill', 'unreferenced')
SPANS = REPO / 'build' / 'p1-spans.json'


def active_window():
    """(lo, hi) from NTMD_INV_RANGE, or None for the whole ROM."""
    raw = os.environ.get('NTMD_INV_RANGE', '')
    if not raw:
        return None
    lo, hi = raw.split(',')
    return int(lo, 16), int(hi, 16)


def load_spans():
    if not SPANS.exists():
        pytest.skip('build/p1-spans.json missing - run make check first')
    return json.loads(SPANS.read_text())['sources']


def in_window(lo, hi):
    """Whether [lo, hi) intersects the active window (True if unscoped)."""
    win = active_window()
    return win is None or (lo < win[1] and hi > win[0])


def violating_pairs():
    """All same-byte source pairs within the active window, by kind."""
    spans = [(r['org'], r['org'] + r['size'], r['category'], r['source'])
             for r in load_spans()]
    spans.sort()
    dual_code, code_data = [], []
    for i, (a0, a1, ac, an) in enumerate(spans):
        for b0, b1, bc, bn in spans[i + 1:]:
            if b0 >= a1:
                break
            lo, hi = b0, min(a1, b1)
            if not in_window(lo, hi):
                continue
            pair = (lo, hi, ac, an, bc, bn)
            if ac in INSTRUCTION_CATEGORIES and bc in INSTRUCTION_CATEGORIES:
                dual_code.append(pair)
            elif (ac in INSTRUCTION_CATEGORIES) != (bc in INSTRUCTION_CATEGORIES):
                code_data.append(pair)
    return dual_code, code_data


def test_no_instruction_byte_has_two_owners():
    dual_code, _ = violating_pairs()
    if active_window() is None and WHOLE_ROM_SKIP:
        pytest.skip('whole-ROM ownership still pending the per-range lanes')
    assert not dual_code, 'instruction bytes with two owners: ' + ', '.join(
        f'[{lo:#x},{hi:#x}) {an} vs {bn}' for lo, hi, _, an, _, bn in dual_code)


def test_no_byte_is_both_code_and_data():
    _, code_data = violating_pairs()
    if active_window() is None and WHOLE_ROM_SKIP:
        pytest.skip('whole-ROM ownership still pending the per-range lanes')
    assert not code_data, 'bytes claimed as both code and data: ' + ', '.join(
        f'[{lo:#x},{hi:#x}) {an} vs {bn}' for lo, hi, _, an, _, bn in code_data)


def test_apply_names_renames_inner_entry_labels(tmp_path):
    """apply_names must rename secondary entries, not only the first label."""
    import apply_names as AN
    (REPO / 'build' / 'gen' / 'p1').mkdir(parents=True, exist_ok=True)
    src = tmp_path / 'x.s'
    src.write_text(' org $100\nold_entry:\n        rts\nold_inner:\n        rts\n')
    names = {0x100: ('canonical_entry', 'e'), 0x102: ('canonical_inner', 'e')}
    assert AN.rename_labels(src, names) == 2
    text = src.read_text()
    assert 'canonical_entry:' in text and 'canonical_inner:' in text
    assert 'old_entry:' not in text and 'old_inner:' not in text
    # a second run is a no-op
    assert AN.rename_labels(src, names) == 0


def test_apply_names_renames_only_named_inner_labels(tmp_path):
    import apply_names as AN
    (REPO / 'build' / 'gen' / 'p1').mkdir(parents=True, exist_ok=True)
    src = tmp_path / 'x.s'
    src.write_text(' org $100\nentry:\n        rts\nkeep_me:\n        rts\n')
    names = {0x100: ('entry', 'e')}
    assert AN.rename_labels(src, names) == 0
    assert 'keep_me:' in src.read_text()


def test_finish_tsv_wellformed_and_unique():
    rows = (REPO / 'config' / 'names' / 'finish.tsv').read_text().splitlines()
    data = [(int(r.split('\t')[0], 16), r.split('\t')[1], r.split('\t')[2])
            for r in rows if r.strip() and not r.startswith('#')]
    assert data, 'finish.tsv is empty'
    seen_addr, seen_name = set(), set()
    for addr, name, evidence in data:
        assert NAME.match(name), f'bad name {name!r}'
        assert evidence.strip(), f'{addr:#x}: empty evidence'
        assert addr not in seen_addr, f'duplicate address {addr:#x}'
        assert name not in seen_name, f'duplicate name {name}'
        seen_addr.add(addr)
        seen_name.add(name)


def test_finish_ram_rows_are_wellformed():
    rows = (REPO / 'config' / 'ram' / 'finish.tsv').read_text().splitlines()
    for r in rows:
        if not r.strip() or r.startswith('#'):
            continue
        parts = r.split('\t')
        addr, size, name, note = int(parts[0], 16), int(parts[1], 16), parts[2], parts[3]
        assert NAME.match(name), f'bad name {name!r}'
        assert size > 0, f'{addr:#x}: zero size'
        assert note.strip(), f'{addr:#x}: empty evidence'
        assert 0x100000 <= addr <= 0x10ffff, f'{addr:#x}: not work RAM'




def test_namespaces_one_owner():
    symbols.check_namespaces(REPO)



# test_header_apply_is_idempotent was dropped at merge: names_reconcile edits
# merged headers, so the lane generator is historical (same policy as name-8).
