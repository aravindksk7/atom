import pandas as pd
import numpy as np
import pytest
from datetime import datetime
from unittest.mock import MagicMock
from etl_framework.reconciliation.engine import ReconciliationEngine
from etl_framework.reconciliation.models import ReconciliationResult, MismatchRecord
from etl_framework.runner.state import TestStatus
from etl_framework.exceptions import SchemaValidationError


def _make_engine(source_df, target_df, key_columns=None, **kwargs):
    source_db = MagicMock()
    target_db = MagicMock()
    source_db.execute_query.return_value = source_df
    target_db.execute_query.return_value = target_df
    # Give the mock engines a name attribute (used in result fields)
    source_db._env = MagicMock(); source_db._env.name = "dev"
    target_db._env = MagicMock(); target_db._env.name = "qa"
    return ReconciliationEngine(
        source_engine=source_db,
        target_engine=target_db,
        key_columns=key_columns or ["id"],
        **kwargs,
    )


# --- Schema validation tests ---

def test_matching_schemas_produce_no_schema_diff():
    source = pd.DataFrame({"id": [1], "val": ["x"]})
    target = pd.DataFrame({"id": [1], "val": ["x"]})
    engine = _make_engine(source, target)
    result = engine.reconcile("SELECT 1", "q")
    assert result.schema_diff is None


def test_schema_mismatch_warn_records_diff_and_compares_common_columns():
    source = pd.DataFrame({"id": [1], "val": ["x"], "extra": ["y"]})
    target = pd.DataFrame({"id": [1], "val": ["x"]})
    engine = _make_engine(source, target, schema_mismatch_policy="warn")
    result = engine.reconcile("SELECT 1", "q")
    assert result.schema_diff is not None
    assert "extra" in result.schema_diff["missing_in_target"]
    assert result.value_mismatch_count == 0  # 'extra' excluded; 'id' and 'val' match


def test_schema_mismatch_error_raises_schema_validation_error():
    source = pd.DataFrame({"id": [1], "col_a": ["x"]})
    target = pd.DataFrame({"id": [1], "col_b": ["x"]})
    engine = _make_engine(source, target, schema_mismatch_policy="error")
    with pytest.raises(SchemaValidationError) as exc_info:
        engine.reconcile("SELECT 1", "q")
    assert "col_a" in exc_info.value.missing_in_target


def test_extra_column_in_target_recorded_in_schema_diff():
    source = pd.DataFrame({"id": [1], "val": ["x"]})
    target = pd.DataFrame({"id": [1], "val": ["x"], "extra_tgt": ["z"]})
    engine = _make_engine(source, target, schema_mismatch_policy="warn")
    result = engine.reconcile("SELECT 1", "q")
    assert "extra_tgt" in result.schema_diff["extra_in_target"]


# --- Comparison correctness tests ---

def test_identical_data_passes_with_zero_mismatches():
    df = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    engine = _make_engine(df, df.copy())
    result = engine.reconcile("SELECT 1", "q")
    assert result.status == TestStatus.PASSED
    assert result.total_issues == 0


def test_missing_row_in_target_counted():
    source = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    target = pd.DataFrame({"id": [1], "val": ["a"]})
    engine = _make_engine(source, target)
    result = engine.reconcile("SELECT 1", "q")
    assert result.missing_in_target_count == 1
    assert result.status == TestStatus.FAILED


def test_missing_row_in_source_counted():
    source = pd.DataFrame({"id": [1], "val": ["a"]})
    target = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    engine = _make_engine(source, target)
    result = engine.reconcile("SELECT 1", "q")
    assert result.missing_in_source_count == 1


def test_value_mismatch_recorded():
    source = pd.DataFrame({"id": [1], "val": ["x"]})
    target = pd.DataFrame({"id": [1], "val": ["y"]})
    engine = _make_engine(source, target)
    result = engine.reconcile("SELECT 1", "q")
    assert result.value_mismatch_count == 1
    assert result.mismatches[0].column_name == "val"
    assert result.mismatches[0].source_value == "x"
    assert result.mismatches[0].target_value == "y"


def test_exclude_columns_skipped_in_comparison():
    source = pd.DataFrame({"id": [1], "val": ["x"], "updated_at": ["2024-01-01"]})
    target = pd.DataFrame({"id": [1], "val": ["x"], "updated_at": ["2024-06-01"]})
    engine = _make_engine(source, target, exclude_columns=["updated_at"])
    result = engine.reconcile("SELECT 1", "q")
    assert result.total_issues == 0


def test_float_tolerance_avoids_false_mismatch():
    source = pd.DataFrame({"id": [1], "amount": [10.000000001]})
    target = pd.DataFrame({"id": [1], "amount": [10.0]})
    engine = _make_engine(source, target, float_tolerance=1e-6)
    result = engine.reconcile("SELECT 1", "q")
    assert result.value_mismatch_count == 0


def test_result_row_counts_correct():
    source = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    target = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    engine = _make_engine(source, target)
    result = engine.reconcile("SELECT 1", "q")
    assert result.source_row_count == 3
    assert result.target_row_count == 2
    assert result.matched_count == 2
    assert result.missing_in_target_count == 1


def test_mismatch_row_limit_caps_stored_records():
    source = pd.DataFrame({"id": list(range(20)), "val": ["x"] * 20})
    target = pd.DataFrame({"id": list(range(20)), "val": ["y"] * 20})
    engine = _make_engine(source, target, mismatch_row_limit=5)
    result = engine.reconcile("SELECT 1", "q")
    assert result.value_mismatch_count == 20
    assert len(result.mismatches) == 5  # capped at limit


def test_type_normalizer_invoked_before_comparison():
    """Decimal values should be normalised and compared correctly."""
    from decimal import Decimal
    source = pd.DataFrame({"id": [1], "price": pd.array([Decimal("9.99")], dtype=object)})
    target = pd.DataFrame({"id": [1], "price": pd.array([Decimal("9.99")], dtype=object)})
    engine = _make_engine(source, target)
    result = engine.reconcile("SELECT 1", "q")
    assert result.total_issues == 0


def test_float_tolerance_is_absolute_not_relative():
    """rtol=0 must be used so float_tolerance is a pure absolute tolerance."""
    # Difference = 1e-5; with rtol=1e-5 this would be EQUAL (bug); with rtol=0 it's a MISMATCH
    source = pd.DataFrame({"id": [1], "amount": [1.00001]})
    target = pd.DataFrame({"id": [1], "amount": [1.0]})
    engine = _make_engine(source, target, float_tolerance=1e-9)  # tight tolerance
    result = engine.reconcile("SELECT 1", "q")
    assert result.value_mismatch_count == 1, (
        "Values differing by 1e-5 should be a mismatch when float_tolerance=1e-9"
    )
