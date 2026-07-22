# tests/unit/test_file_mapping_similarity.py
from __future__ import annotations

from etl_framework.reconciliation.file_mapping import _filename_similarity


def test_filename_similarity_identical_stems_scores_one() -> None:
    assert _filename_similarity("sales_east_20260101.csv", "sales_east_20260101.dat") == 1.0


def test_filename_similarity_unrelated_names_scores_low() -> None:
    score = _filename_similarity("sales_east_20260101.csv", "zzz_completely_different.dat")
    assert score < 0.5


def test_filename_similarity_partial_overlap_scores_between() -> None:
    high = _filename_similarity("sales_east_20260101.csv", "financials_east_20260101.dat")
    low = _filename_similarity("sales_east_20260101.csv", "zzz_completely_different.dat")
    assert high > low
