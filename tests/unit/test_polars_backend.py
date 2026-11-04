"""Unit tests for PolarsBackend.compare.

All tests are skipped automatically when polars is not installed.
"""
from __future__ import annotations

import pandas as pd
import pytest

from etl_framework.reconciliation.backends.polars_backend import PolarsBackend


pytestmark = pytest.mark.skipif(
    not __import__("importlib").util.find_spec("polars"),
    reason="polars not installed",
)


def _backend(key_columns: list[str] | None = None, **kwargs) -> PolarsBackend:
    return PolarsBackend(key_columns=key_columns or ["id"], **kwargs)


# ---------------------------------------------------------------------------
# Join keys whose types differ between the two sides
# ---------------------------------------------------------------------------

def _counts(result) -> tuple[int, int, int]:
    return result.matched_count, result.missing_in_target_count, result.missing_in_source_count


def test_int_key_joins_a_whole_valued_float_key():
    source = pd.DataFrame({"curve_id": [1, 2], "val": ["a", "b"]})
    target = pd.DataFrame({"curve_id": [1.0, 2.0], "val": ["a", "c"]})

    result = _backend(["curve_id"]).compare_with_counts(source, target)

    assert _counts(result) == (2, 0, 0)
    assert result.value_mismatch_count == 1
    assert result.mismatches[0].key_values == {"curve_id": 2}
    assert isinstance(result.mismatches[0].key_values["curve_id"], int)


def test_float_key_on_the_source_side_joins_an_int_key():
    source = pd.DataFrame({"curve_id": [1.0, 2.0], "val": ["a", "b"]})
    target = pd.DataFrame({"curve_id": [1, 2], "val": ["a", "b"]})

    result = _backend(["curve_id"]).compare_with_counts(source, target)

    assert _counts(result) == (2, 0, 0)


def test_null_in_a_float_key_is_a_missing_row_not_an_error():
    source = pd.DataFrame({"curve_id": [1, 2], "val": ["a", "b"]})
    target = pd.DataFrame({"curve_id": [1.0, float("nan")], "val": ["a", "b"]})

    result = _backend(["curve_id"]).compare_with_counts(source, target)

    assert _counts(result) == (1, 1, 1)


def test_fractional_float_key_matches_only_equal_values():
    source = pd.DataFrame({"curve_id": [1, 2], "val": ["a", "b"]})
    target = pd.DataFrame({"curve_id": [1.5, 2.0], "val": ["a", "b"]})

    result = _backend(["curve_id"]).compare_with_counts(source, target)

    assert _counts(result) == (1, 1, 1)


def test_large_int_keys_stay_distinct_against_a_whole_valued_float_key():
    source = pd.DataFrame({"curve_id": [2**53, 2**53 + 1], "val": ["a", "b"]})
    target = pd.DataFrame({"curve_id": [float(2**53)], "val": ["a"]})

    result = _backend(["curve_id"]).compare_with_counts(source, target)

    assert _counts(result) == (1, 1, 0)


def test_int_keys_of_different_widths_join():
    source = pd.DataFrame({"curve_id": pd.Series([1, 2], dtype="int32"), "val": ["a", "b"]})
    target = pd.DataFrame({"curve_id": pd.Series([1, 2], dtype="int64"), "val": ["a", "b"]})

    assert _counts(_backend(["curve_id"]).compare_with_counts(source, target)) == (2, 0, 0)


def test_only_the_mismatched_key_of_a_composite_key_changes():
    source = pd.DataFrame({"curve_id": [1, 2], "region": ["eu", "us"], "val": ["a", "b"]})
    target = pd.DataFrame({"curve_id": [1.0, 2.0], "region": ["eu", "us"], "val": ["a", "b"]})

    result = _backend(["curve_id", "region"]).compare_with_counts(source, target)

    assert _counts(result) == (2, 0, 0)


# ---------------------------------------------------------------------------
# Basic match / mismatch detection
# ---------------------------------------------------------------------------

def test_identical_frames_produce_no_mismatches():
    df = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    assert _backend().compare(df, df.copy()) == []


def test_missing_in_target_detected():
    src = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    tgt = pd.DataFrame({"id": [1], "val": ["a"]})
    result = _backend().compare(src, tgt)
    missing = [m for m in result if m.mismatch_type == "missing_in_target"]
    assert len(missing) == 1
    assert missing[0].key_values == {"id": 2}


def test_missing_in_source_detected():
    src = pd.DataFrame({"id": [1], "val": ["a"]})
    tgt = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    result = _backend().compare(src, tgt)
    missing = [m for m in result if m.mismatch_type == "missing_in_source"]
    assert len(missing) == 1
    assert missing[0].key_values == {"id": 2}


def test_value_mismatch_detected():
    src = pd.DataFrame({"id": [1], "amount": [10.0]})
    tgt = pd.DataFrame({"id": [1], "amount": [20.0]})
    result = _backend().compare(src, tgt)
    assert len(result) == 1
    assert result[0].mismatch_type == "value_diff"
    assert result[0].column_name == "amount"
    assert result[0].source_value == 10.0
    assert result[0].target_value == 20.0


def test_key_values_populated_in_mismatch_record():
    src = pd.DataFrame({"id": [42], "val": ["x"]})
    tgt = pd.DataFrame({"id": [42], "val": ["y"]})
    result = _backend().compare(src, tgt)
    assert result[0].key_values == {"id": 42}


# ---------------------------------------------------------------------------
# Float tolerance
# ---------------------------------------------------------------------------

def test_float_within_tolerance_is_not_a_mismatch():
    src = pd.DataFrame({"id": [1], "amount": [10.000000001]})
    tgt = pd.DataFrame({"id": [1], "amount": [10.0]})
    result = _backend(float_tolerance=1e-6).compare(src, tgt)
    assert result == []


def test_float_outside_tolerance_is_a_mismatch():
    src = pd.DataFrame({"id": [1], "amount": [10.01]})
    tgt = pd.DataFrame({"id": [1], "amount": [10.0]})
    result = _backend(float_tolerance=1e-9).compare(src, tgt)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# NULL semantics
# ---------------------------------------------------------------------------

def test_null_equals_null_true_produces_no_mismatch():
    src = pd.DataFrame({"id": [1], "val": [None]})
    tgt = pd.DataFrame({"id": [1], "val": [None]})
    assert _backend(null_equals_null=True).compare(src, tgt) == []


def test_null_equals_null_false_produces_mismatch():
    src = pd.DataFrame({"id": [1], "val": [None]})
    tgt = pd.DataFrame({"id": [1], "val": [None]})
    result = _backend(null_equals_null=False).compare(src, tgt)
    assert len(result) == 1
    assert result[0].mismatch_type == "value_diff"


def test_null_vs_non_null_is_always_a_mismatch():
    src = pd.DataFrame({"id": [1], "val": [None]})
    tgt = pd.DataFrame({"id": [1], "val": [42.0]})
    result = _backend(null_equals_null=True).compare(src, tgt)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Row limit
# ---------------------------------------------------------------------------

def test_mismatch_row_limit_caps_returned_records():
    n = 20
    src = pd.DataFrame({"id": list(range(n)), "val": ["x"] * n})
    tgt = pd.DataFrame({"id": list(range(n)), "val": ["y"] * n})
    result = _backend(mismatch_row_limit=5).compare(src, tgt)
    assert len(result) <= 5


# ---------------------------------------------------------------------------
# Composite keys
# ---------------------------------------------------------------------------

def test_composite_key_columns_single_mismatch():
    src = pd.DataFrame({"a": [1, 1], "b": [1, 2], "val": ["x", "y"]})
    tgt = pd.DataFrame({"a": [1, 1], "b": [1, 2], "val": ["x", "z"]})
    result = _backend(key_columns=["a", "b"]).compare(src, tgt)
    assert len(result) == 1
    assert result[0].key_values == {"a": 1, "b": 2}
    assert result[0].column_name == "val"


def test_composite_key_missing_row_uses_all_key_fields():
    src = pd.DataFrame({"a": [1, 1], "b": [1, 2], "val": ["x", "y"]})
    tgt = pd.DataFrame({"a": [1], "b": [1], "val": ["x"]})
    result = _backend(key_columns=["a", "b"]).compare(src, tgt)
    missing = [m for m in result if m.mismatch_type == "missing_in_target"]
    assert len(missing) == 1
    assert missing[0].key_values == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# ImportError when polars unavailable
# ---------------------------------------------------------------------------

def test_raises_import_error_when_polars_unavailable(monkeypatch):
    import etl_framework.reconciliation.backends.polars_backend as mod
    monkeypatch.setattr(mod, "_POLARS_AVAILABLE", False)
    src = pd.DataFrame({"id": [1], "val": ["x"]})
    with pytest.raises(ImportError, match="polars is required"):
        PolarsBackend(key_columns=["id"]).compare(src, src.copy())
