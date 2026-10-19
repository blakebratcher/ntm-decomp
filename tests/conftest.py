"""Shared guards for the corpus-independent public test suite."""
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

requires_corpus = pytest.mark.skipif(
    not (REPO / 'src').is_dir(),
    reason='source corpus is not present')
requires_lane_docs = pytest.mark.skipif(
    not (REPO / 'docs' / 'lanes').is_dir(),
    reason='lane evidence is not present')
requires_vasm = pytest.mark.skipif(
    not (REPO / 'tools' / 'vasm').exists(),
    reason='run make setup first')
