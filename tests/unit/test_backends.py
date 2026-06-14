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
    import sys
    monkeypatch.setitem(sys.modules, "polars", None)
    import importlib
    import etl_framework.reconciliation.backends.polars_backend as pb
    importlib.reload(pb)
    backend = pb.PolarsBackend(key_columns=["id"], float_tolerance=1e-9,
                               null_equals_null=True, mismatch_row_limit=1000)
    src = pd.DataFrame({"id": [1], "val": ["a"]})
    with pytest.raises(ImportError, match="polars"):
        backend.compare(src, src)


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
