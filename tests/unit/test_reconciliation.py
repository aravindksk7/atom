import pandas as pd
import numpy as np
import pytest
import time
from datetime import datetime
from unittest.mock import MagicMock
from etl_framework.reconciliation.engine import ReconciliationEngine
from etl_framework.reconciliation.models import ReconciliationResult, MismatchRecord
from etl_framework.runner.state import TestStatus
from etl_framework.exceptions import SchemaValidationError


def _make_engine(source_df, target_df, key_columns=None, exclude_columns=None,
                 float_tolerance=1e-9, mismatch_row_limit=1000,
                 schema_mismatch_policy="warn", null_equals_null=True,
                 chunk_size=0, **kwargs):
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
        exclude_columns=exclude_columns,
        float_tolerance=float_tolerance,
        mismatch_row_limit=mismatch_row_limit,
        schema_mismatch_policy=schema_mismatch_policy,
        null_equals_null=null_equals_null,
        chunk_size=chunk_size,
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


def test_exclude_columns_match_normalized_names():
    source = pd.DataFrame({"id": [1], "val": ["x"], "Sequence Number": [1]})
    target = pd.DataFrame({"id": [1], "val": ["x"], "sequence-number": [999]})
    engine = _make_engine(source, target, exclude_columns=["sequence_number"])
    result = engine.reconcile("SELECT 1", "q")
    assert result.schema_diff is None
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


# --- NULL semantics tests (Task 7) ---

def test_null_equals_null_true_no_mismatch():
    """NaN == NaN → no mismatch when null_equals_null=True (default)."""
    source = pd.DataFrame({"id": [1], "val": [float("nan")]})
    target = pd.DataFrame({"id": [1], "val": [float("nan")]})
    engine = _make_engine(source, target, null_equals_null=True)
    result = engine.reconcile("SELECT 1", "null_test_true")
    assert result.value_mismatch_count == 0


def test_null_equals_null_false_records_mismatch():
    """NaN == NaN → mismatch when null_equals_null=False (ANSI NULL semantics)."""
    source = pd.DataFrame({"id": [1], "val": [float("nan")]})
    target = pd.DataFrame({"id": [1], "val": [float("nan")]})
    engine = _make_engine(source, target, null_equals_null=False)
    result = engine.reconcile("SELECT 1", "null_test_false")
    assert result.value_mismatch_count == 1


def test_null_in_source_not_null_in_target_always_mismatch():
    """NaN vs non-NaN is always a mismatch regardless of null_equals_null."""
    source = pd.DataFrame({"id": [1], "val": [float("nan")]})
    target = pd.DataFrame({"id": [1], "val": [42.0]})
    engine = _make_engine(source, target, null_equals_null=True)
    result = engine.reconcile("SELECT 1", "null_vs_value")
    assert result.value_mismatch_count == 1


def test_null_equals_null_default_is_true():
    """Default constructor behaviour: NaN==NaN is a match."""
    source = pd.DataFrame({"id": [1], "val": [None]})
    target = pd.DataFrame({"id": [1], "val": [None]})
    engine = _make_engine(source, target)  # no null_equals_null arg
    result = engine.reconcile("SELECT 1", "null_default")
    assert result.value_mismatch_count == 0


# --- Duration SLO tests (Task 8) ---

def test_duration_slo_not_exceeded_keeps_passed_status():
    source = pd.DataFrame({"id": [1], "val": ["x"]})
    target = pd.DataFrame({"id": [1], "val": ["x"]})
    engine = _make_engine(source, target)
    result = engine.reconcile("SELECT 1", "fast", max_duration_seconds=9999.0)
    assert result.status == TestStatus.PASSED


def test_duration_slo_exceeded_sets_slow_status():
    import unittest.mock as mock
    source = pd.DataFrame({"id": [1], "val": ["x"]})
    target = pd.DataFrame({"id": [1], "val": ["x"]})
    engine = _make_engine(source, target)

    # Simulate slow execution: monotonic returns increasing large values
    call_count = [0]
    base = [None]

    real_monotonic = time.monotonic

    def fake_monotonic():
        call_count[0] += 1
        if base[0] is None:
            base[0] = real_monotonic()
        return base[0] + call_count[0] * 100.0  # 100s per call

    with mock.patch("etl_framework.reconciliation.engine.time.monotonic", fake_monotonic):
        result = engine.reconcile("SELECT 1", "slow", max_duration_seconds=5.0)

    assert result.status == TestStatus.SLOW


def test_slo_none_never_triggers_slow():
    source = pd.DataFrame({"id": [1], "val": ["x"]})
    target = pd.DataFrame({"id": [1], "val": ["x"]})
    engine = _make_engine(source, target)
    # max_duration_seconds defaults to None — should never produce SLOW
    result = engine.reconcile("SELECT 1", "no_slo")
    assert result.status != TestStatus.SLOW


# --- Chunked reconciliation tests (Task 9) ---

def _make_paginating_engine(source_df, target_df, chunk_size, key_columns=None):
    """Build an engine whose execute_query simulates real SQL pagination by
    slicing the DataFrame according to OFFSET/FETCH values in the query string."""
    import re

    def _paginate(df):
        def side_effect(query, params=None):
            m = re.search(r"OFFSET\s+(\d+)\s+ROWS\s+FETCH\s+NEXT\s+(\d+)\s+ROWS\s+ONLY", query, re.IGNORECASE)
            if m:
                offset, size = int(m.group(1)), int(m.group(2))
                return df.iloc[offset: offset + size].reset_index(drop=True)
            # Hash query or plain query — return full df
            return df
        return side_effect

    source_db = MagicMock()
    target_db = MagicMock()
    source_db.execute_query.side_effect = _paginate(source_df)
    target_db.execute_query.side_effect = _paginate(target_df)
    source_db._env = MagicMock(); source_db._env.name = "dev"
    target_db._env = MagicMock(); target_db._env.name = "qa"
    return ReconciliationEngine(
        source_engine=source_db,
        target_engine=target_db,
        key_columns=key_columns or ["id"],
        chunk_size=chunk_size,
        use_hash_precheck=False,  # disable hash pre-check for these tests
    )


def test_chunk_size_zero_uses_full_load():
    """chunk_size=0 (default) must NOT use chunked path — full DataFrame."""
    source = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    target = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    engine = _make_engine(source, target, chunk_size=0)
    result = engine.reconcile("SELECT 1", "full_load")
    assert result.status == TestStatus.PASSED
    assert result.source_row_count == 3


def test_chunk_size_nonzero_reconciles_correctly():
    """chunk_size > 0 must still return correct reconciliation result."""
    source = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    target = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "x"]})  # mismatch on row 3
    engine = _make_paginating_engine(source, target, chunk_size=2)
    result = engine.reconcile("SELECT 1", "chunked_mismatch")
    assert result.value_mismatch_count >= 1


def test_chunk_size_nonzero_unequal_row_counts_no_infinite_loop():
    """When source has fewer rows than target in chunked mode, engine must terminate."""
    source = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    target = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    engine = _make_paginating_engine(source, target, chunk_size=2)
    result = engine.reconcile("SELECT 1", "unequal_rows")
    # Must terminate and report missing_in_source_count
    assert result.missing_in_source_count == 1


# --- Hash pre-check tests (Task 9, use_hash_precheck=True) ---

def test_hash_precheck_identical_data_returns_early_pass():
    """Identical data with hash precheck enabled should short-circuit to PASSED."""
    df = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    engine = _make_engine(df, df.copy(), chunk_size=10, use_hash_precheck=True)
    result = engine.reconcile("SELECT 1", "hash_precheck_match")
    assert result.status == TestStatus.PASSED
    assert result.mismatches == []


def test_hash_precheck_differing_data_falls_through_to_full_compare():
    """Differing data should NOT short-circuit — full compare must run and catch mismatches."""
    source = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    target = pd.DataFrame({"id": [1, 2], "val": ["a", "X"]})
    engine = _make_paginating_engine(source, target, chunk_size=10)
    engine._use_hash_precheck = True
    result = engine.reconcile("SELECT 1", "hash_precheck_diff")
    assert result.value_mismatch_count >= 1


def test_hash_precheck_disabled_does_not_short_circuit():
    """use_hash_precheck=False must always run the full compare even on identical data."""
    df = pd.DataFrame({"id": [1, 2], "val": ["x", "y"]})
    engine = _make_engine(df, df.copy(), chunk_size=5, use_hash_precheck=False)
    result = engine.reconcile("SELECT 1", "no_hash_precheck")
    assert result.status == TestStatus.PASSED
    assert result.source_row_count == 2


# --- Custom backend integration tests ---

def test_polars_backend_integration_value_mismatch():
    """ReconciliationEngine with PolarsBackend detects value mismatches correctly."""
    pytest.importorskip("polars")
    from etl_framework.reconciliation.backends.polars_backend import PolarsBackend

    src = pd.DataFrame({"id": [1, 2], "val": ["x", "y"]})
    tgt = pd.DataFrame({"id": [1, 2], "val": ["x", "z"]})
    backend = PolarsBackend(key_columns=["id"])
    engine = _make_engine(src, tgt, backend=backend)
    result = engine.reconcile("SELECT 1", "polars_integration")
    assert result.value_mismatch_count == 1


def test_polars_backend_integration_no_mismatches():
    pytest.importorskip("polars")
    from etl_framework.reconciliation.backends.polars_backend import PolarsBackend

    df = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    backend = PolarsBackend(key_columns=["id"])
    engine = _make_engine(df, df.copy(), backend=backend)
    result = engine.reconcile("SELECT 1", "polars_clean")
    assert result.status == TestStatus.PASSED


# --- Composite key columns ---

def test_composite_keys_value_mismatch_attributed_correctly():
    src = pd.DataFrame({"a": [1, 1], "b": [1, 2], "val": ["x", "y"]})
    tgt = pd.DataFrame({"a": [1, 1], "b": [1, 2], "val": ["x", "z"]})
    engine = _make_engine(src, tgt, key_columns=["a", "b"])
    result = engine.reconcile("SELECT 1", "composite_key_val")
    assert result.value_mismatch_count == 1
    assert result.mismatches[0].key_values == {"a": 1, "b": 2}


def test_composite_keys_missing_row_detected():
    src = pd.DataFrame({"a": [1, 1], "b": [1, 2], "val": ["x", "y"]})
    tgt = pd.DataFrame({"a": [1], "b": [1], "val": ["x"]})
    engine = _make_engine(src, tgt, key_columns=["a", "b"])
    result = engine.reconcile("SELECT 1", "composite_key_missing")
    assert result.missing_in_target_count == 1
    missing = [m for m in result.mismatches if m.mismatch_type == "missing_in_target"]
    assert missing[0].key_values == {"a": 1, "b": 2}


def test_executed_at_is_timezone_aware():
    df = pd.DataFrame({"id": [1], "val": ["x"]})
    engine = _make_engine(df, df.copy())
    result = engine.reconcile("SELECT 1", "q")
    assert result.executed_at.tzinfo is not None


# --- Segment value enrichment ---

def test_segment_values_attached_from_source_frame():
    source = pd.DataFrame({"id": [1, 2], "region": ["EMEA", "APAC"], "amt": [10, 20]})
    target = pd.DataFrame({"id": [1, 2], "region": ["EMEA", "APAC"], "amt": [10, 99]})
    engine = _make_engine(source, target, segment_columns=["region"])
    result = engine.reconcile("SELECT 1", "q")
    diff = [m for m in result.mismatches if m.mismatch_type == "value_diff"]
    assert diff and diff[0].segment_values == {"region": "APAC"}


def test_segment_values_fall_back_to_target_for_missing_in_source():
    source = pd.DataFrame({"id": [1], "region": ["EMEA"], "amt": [10]})
    target = pd.DataFrame({"id": [1, 2], "region": ["EMEA", "APAC"], "amt": [10, 20]})
    engine = _make_engine(source, target, segment_columns=["region"])
    result = engine.reconcile("SELECT 1", "q")
    miss = [m for m in result.mismatches if m.mismatch_type == "missing_in_source"]
    assert miss and miss[0].segment_values == {"region": "APAC"}


def test_no_segment_columns_leaves_segment_values_none():
    source = pd.DataFrame({"id": [1], "amt": [10]})
    target = pd.DataFrame({"id": [1], "amt": [99]})
    engine = _make_engine(source, target)
    result = engine.reconcile("SELECT 1", "q")
    assert result.mismatches[0].segment_values is None


def test_segment_column_absent_from_frames_is_skipped():
    source = pd.DataFrame({"id": [1], "amt": [10]})
    target = pd.DataFrame({"id": [1], "amt": [99]})
    engine = _make_engine(source, target, segment_columns=["nonexistent"])
    result = engine.reconcile("SELECT 1", "q")
    assert result.mismatches[0].segment_values is None
