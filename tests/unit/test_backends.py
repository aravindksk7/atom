import pandas as pd
import pytest
from etl_framework.reconciliation.backends.base import ComparisonBackend
from etl_framework.reconciliation.backends.pandas_backend import PandasBackend
from etl_framework.reconciliation.models import MismatchRecord
from etl_framework.runner.state import TestStatus


def _src_tgt(src_data, tgt_data):
    src = pd.DataFrame(src_data)
    tgt = pd.DataFrame(tgt_data)
    return src, tgt


def test_pandas_backend_is_comparison_backend():
    backend = PandasBackend(key_columns=["id"], float_tolerance=1e-9,
                            null_equals_null=True, mismatch_row_limit=1000)
    # Protocol check — must have compare method
    assert hasattr(backend, "compare")


def test_pandas_backend_identical_data_no_mismatches():
    backend = PandasBackend(key_columns=["id"], float_tolerance=1e-9,
                            null_equals_null=True, mismatch_row_limit=1000)
    src, tgt = _src_tgt({"id": [1, 2], "val": ["a", "b"]},
                         {"id": [1, 2], "val": ["a", "b"]})
    mismatches = backend.compare(src, tgt)
    assert mismatches == []


def test_pandas_backend_detects_value_mismatch():
    backend = PandasBackend(key_columns=["id"], float_tolerance=1e-9,
                            null_equals_null=True, mismatch_row_limit=1000)
    src, tgt = _src_tgt({"id": [1], "val": ["a"]},
                         {"id": [1], "val": ["b"]})
    mismatches = backend.compare(src, tgt)
    assert len(mismatches) == 1
    assert mismatches[0].column_name == "val"
    assert mismatches[0].mismatch_type == "value_diff"


def test_pandas_backend_detects_missing_in_target():
    backend = PandasBackend(key_columns=["id"], float_tolerance=1e-9,
                            null_equals_null=True, mismatch_row_limit=1000)
    src, tgt = _src_tgt({"id": [1, 2], "val": ["a", "b"]},
                         {"id": [1], "val": ["a"]})
    mismatches = backend.compare(src, tgt)
    missing = [m for m in mismatches if m.mismatch_type == "missing_in_target"]
    assert len(missing) == 1


def test_pandas_backend_supports_dunder_key_column():
    """Positional-fallback key columns (e.g. '__row__') must not crash the
    per-column value-mismatch pass — itertuples() renames leading-underscore
    field names, so lookups must not rely on namedtuple attribute/dict access."""
    backend = PandasBackend(key_columns=["__row__"], float_tolerance=1e-9,
                            null_equals_null=True, mismatch_row_limit=1000)
    src, tgt = _src_tgt({"__row__": [1, 2], "val": ["a", "b"]},
                         {"__row__": [1, 2], "val": ["a", "changed"]})
    mismatches = backend.compare(src, tgt)
    assert len(mismatches) == 1
    assert mismatches[0].column_name == "val"
    assert mismatches[0].key_values == {"__row__": 2}


def test_pandas_backend_respects_mismatch_limit():
    backend = PandasBackend(key_columns=["id"], float_tolerance=1e-9,
                            null_equals_null=True, mismatch_row_limit=2)
    src, tgt = _src_tgt({"id": [1, 2, 3], "val": ["a", "b", "c"]},
                         {"id": [1, 2, 3], "val": ["x", "y", "z"]})
    mismatches = backend.compare(src, tgt)
    assert len(mismatches) <= 2


def test_polars_backend_importable():
    # Must be importable even when polars is not installed
    from etl_framework.reconciliation.backends.polars_backend import PolarsBackend
    assert PolarsBackend is not None


def test_polars_backend_raises_when_polars_missing(monkeypatch):
    import etl_framework.reconciliation.backends.polars_backend as pb
    monkeypatch.setattr(pb, "_POLARS_AVAILABLE", False)
    backend = pb.PolarsBackend(key_columns=["id"], float_tolerance=1e-9,
                               null_equals_null=True, mismatch_row_limit=1000)
    src = pd.DataFrame({"id": [1], "val": ["a"]})
    with pytest.raises(ImportError, match="polars"):
        backend.compare(src, src)


def test_pandas_backend_per_column_tolerance_passes_within_tolerance():
    backend = PandasBackend(key_columns=["id"], float_tolerance=1e-9,
                            column_tolerances={"price": 0.01})
    src = pd.DataFrame({"id": [1], "price": [100.0]})
    tgt = pd.DataFrame({"id": [1], "price": [100.005]})
    assert backend.compare(src, tgt) == []


def test_pandas_backend_per_column_tolerance_fails_outside_tolerance():
    backend = PandasBackend(key_columns=["id"], float_tolerance=1e-9,
                            column_tolerances={"price": 0.001})
    src = pd.DataFrame({"id": [1], "price": [100.0]})
    tgt = pd.DataFrame({"id": [1], "price": [100.005]})
    mismatches = backend.compare(src, tgt)
    assert len(mismatches) == 1
    assert mismatches[0].column_name == "price"


def test_pandas_backend_datetime_tolerance_passes_within_window():
    backend = PandasBackend(key_columns=["id"], datetime_tolerance_seconds=2.0)
    src = pd.DataFrame({"id": [1], "ts": [pd.Timestamp("2024-01-01 12:00:00")]})
    tgt = pd.DataFrame({"id": [1], "ts": [pd.Timestamp("2024-01-01 12:00:01")]})
    assert backend.compare(src, tgt) == []


def test_pandas_backend_datetime_tolerance_fails_outside_window():
    backend = PandasBackend(key_columns=["id"], datetime_tolerance_seconds=0.5)
    src = pd.DataFrame({"id": [1], "ts": [pd.Timestamp("2024-01-01 12:00:00")]})
    tgt = pd.DataFrame({"id": [1], "ts": [pd.Timestamp("2024-01-01 12:00:02")]})
    mismatches = backend.compare(src, tgt)
    assert len(mismatches) == 1


def test_duckdb_backend_detects_missing_rows_not_value_diffs():
    """merged.itertuples() renames dunder fields (__in_src__/__in_tgt__), so
    dict-style lookups via ._asdict() must not be used — otherwise every
    missing-in-target/source row is silently misclassified as a value_diff."""
    pytest.importorskip("duckdb")
    from etl_framework.reconciliation.backends.duckdb_backend import DuckDBBackend

    backend = DuckDBBackend(key_columns=["id"], mismatch_row_limit=1000)
    src, tgt = _src_tgt({"id": [1, 2], "val": ["a", "b"]},
                         {"id": [1], "val": ["a"]})
    mismatches = backend.compare(src, tgt)
    assert len(mismatches) == 1
    assert mismatches[0].mismatch_type == "missing_in_target"


def test_duckdb_backend_supports_dunder_key_column():
    pytest.importorskip("duckdb")
    from etl_framework.reconciliation.backends.duckdb_backend import DuckDBBackend

    backend = DuckDBBackend(key_columns=["__row__"], mismatch_row_limit=1000)
    src, tgt = _src_tgt({"__row__": [1, 2], "val": ["a", "b"]},
                         {"__row__": [1, 2], "val": ["a", "changed"]})
    mismatches = backend.compare(src, tgt)
    assert len(mismatches) == 1
    assert mismatches[0].column_name == "val"
    assert mismatches[0].key_values == {"__row__": 2}


def test_engine_accepts_backend_parameter():
    from unittest.mock import MagicMock
    import pandas as pd
    from etl_framework.reconciliation.engine import ReconciliationEngine

    src_df = pd.DataFrame({"id": [1], "val": ["a"]})
    tgt_df = pd.DataFrame({"id": [1], "val": ["a"]})

    source_db = MagicMock()
    target_db = MagicMock()
    source_db.execute_query.return_value = src_df
    target_db.execute_query.return_value = tgt_df

    backend = PandasBackend(key_columns=["id"], float_tolerance=1e-9,
                            null_equals_null=True, mismatch_row_limit=1000)
    engine = ReconciliationEngine(
        source_engine=source_db,
        target_engine=target_db,
        key_columns=["id"],
        backend=backend,
    )
    result = engine.reconcile("SELECT 1", "test_with_backend")
    assert result.status == TestStatus.PASSED


def test_engine_backend_reports_full_counts_when_detail_rows_are_capped():
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from etl_framework.reconciliation.engine import ReconciliationEngine

    n = 20
    src_df = pd.DataFrame({"id": list(range(n)), "val": ["a"] * n})
    tgt_df = pd.DataFrame({"id": list(range(n)), "val": ["b"] * n})

    source_db = MagicMock()
    target_db = MagicMock()
    source_db._env = SimpleNamespace(name="source")
    target_db._env = SimpleNamespace(name="target")
    source_db.execute_query.return_value = src_df
    target_db.execute_query.return_value = tgt_df

    backend = PandasBackend(key_columns=["id"], mismatch_row_limit=5)
    engine = ReconciliationEngine(
        source_engine=source_db,
        target_engine=target_db,
        key_columns=["id"],
        backend=backend,
    )

    result = engine.reconcile("SELECT 1", "capped_backend")

    assert result.source_row_count == n
    assert result.target_row_count == n
    assert result.value_mismatch_count == n
    assert len(result.mismatches) == 5
    assert result.status == TestStatus.FAILED
