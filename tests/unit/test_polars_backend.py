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
