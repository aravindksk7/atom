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


from etl_framework.reconciliation.file_mapping import _column_signature_similarity, _row_count_ratio


def test_column_signature_similarity_identical_columns_scores_one() -> None:
    assert _column_signature_similarity(["id", "value"], ["id", "value"]) == 1.0


def test_column_signature_similarity_disjoint_columns_scores_zero() -> None:
    assert _column_signature_similarity(["id", "value"], ["foo", "bar"]) == 0.0


def test_column_signature_similarity_partial_overlap_is_jaccard() -> None:
    # intersection={id}, union={id,value,extra} -> 1/3
    assert _column_signature_similarity(["id", "value"], ["id", "extra"]) == 1 / 3


def test_column_signature_similarity_both_empty_scores_one() -> None:
    assert _column_signature_similarity([], []) == 1.0


def test_row_count_ratio_equal_counts_scores_one() -> None:
    assert _row_count_ratio(10, 10) == 1.0


def test_row_count_ratio_uses_min_over_max() -> None:
    assert _row_count_ratio(5, 20) == 0.25


def test_row_count_ratio_both_zero_scores_one() -> None:
    assert _row_count_ratio(0, 0) == 1.0
