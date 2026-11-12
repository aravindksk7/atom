"""Streaming / pushdown reconciliation strategies (DuckDB on disk).

The live Oracle / PostgreSQL / SQL Server behaviour of pushdown is covered by
tests/integration/test_large_table_live.py; these tests pin the streaming
comparison to the in-memory engine's answers and cover the plumbing.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from etl_framework.exceptions import SchemaValidationError
from etl_framework.reconciliation import pushdown_sql as pd_sql
from etl_framework.reconciliation.engine import ReconciliationEngine
from etl_framework.runner.state import TestStatus


def _batcher(df):
    def side_effect(query, params=None, *, batch_size):
        if df.empty:
            yield df.iloc[0:0]
            return
        for start in range(0, len(df), batch_size):
            yield df.iloc[start:start + batch_size].reset_index(drop=True)
    return side_effect


def _db(df, name, *, batches=True):
    db = MagicMock(spec=["execute_query", "iter_query_batches", "_env"] if batches else ["execute_query", "_env"])
    db.execute_query.return_value = df
    if batches:
        db.iter_query_batches.side_effect = _batcher(df)
    db._env = SimpleNamespace(name=name)
    return db


def _engine(source, target, strategy, **kwargs):
    kwargs.setdefault("key_columns", ["id"])
    batches = kwargs.pop("batches", True)
    return ReconciliationEngine(
        source_engine=_db(source, "dev", batches=batches),
        target_engine=_db(target, "qa", batches=batches),
        strategy=strategy,
        **kwargs,
    )


def _counts(result):
    return (
        result.status,
        result.source_row_count,
        result.target_row_count,
        result.matched_count,
        result.missing_in_target_count,
        result.missing_in_source_count,
        result.value_mismatch_count,
        result.mismatch_by_column,
    )


def _random_pair(n=2_000, seed=7):
    rng = np.random.default_rng(seed)
    ids = np.arange(n)
    source = pd.DataFrame({
        "id": ids,
        "amount": rng.normal(1000, 250, n).round(4),
        "qty": rng.integers(0, 50, n),
        "name": [f"name-{i}" for i in ids],
        "ts": pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 10_000_000, n), unit="s"),
    })
    source.loc[rng.choice(n, 50, replace=False), "name"] = None
    target = source.copy()
    changed = rng.choice(n, 40, replace=False)
    target.loc[changed[:10], "amount"] += 0.5
    target.loc[changed[10:20], "name"] = "renamed"
    target.loc[changed[20:30], "ts"] += pd.Timedelta(seconds=1)
    target.loc[changed[30:35], "qty"] += 1
    target.loc[changed[35:40], "amount"] += 1e-12          # inside the default tolerance
    target = target.drop(index=rng.choice(n, 25, replace=False))
    extra = source.head(15).copy()
    extra["id"] = extra["id"] + 10 * n
    target = pd.concat([target, extra], ignore_index=True)
    return source, target


@pytest.mark.parametrize("batch_size", [1_000, 137])
def test_streaming_matches_in_memory_counts(batch_size):
    source, target = _random_pair()
    expected = _engine(source, target, "in_memory").reconcile("q", "q")
    actual = _engine(source, target, "streaming", stream_batch_size=batch_size).reconcile("q", "q")
    assert _counts(actual) == _counts(expected)
    assert actual.mismatch_by_type == expected.mismatch_by_type
    assert expected.value_mismatch_count > 25


def test_streaming_identical_sides_pass():
    source, _ = _random_pair(n=500)
    result = _engine(source, source.copy(), "streaming", stream_batch_size=1_000).reconcile("q", "q")
    assert result.status == TestStatus.PASSED
    assert result.matched_count == 500
    assert result.mismatches == []


def test_streaming_mismatch_records_carry_values_and_deltas():
    source = pd.DataFrame({"id": [1, 2, 3], "amount": [10.0, 20.0, 30.0], "name": ["a", "b", "c"]})
    target = pd.DataFrame({"id": [1, 2, 4], "amount": [10.0, 25.0, 40.0], "name": ["a", "B", "d"]})
    result = _engine(source, target, "streaming", stream_batch_size=1_000).reconcile("q", "q")
    by_type = {(m.mismatch_type, m.column_name, m.key_values["id"]): m for m in result.mismatches}
    amount = by_type[("value_diff", "amount", 2)]
    assert (amount.source_value, amount.target_value, amount.delta) == (20.0, 25.0, 5.0)
    assert amount.relative_delta == pytest.approx(0.25)
    assert by_type[("value_diff", "name", 2)].target_value == "B"
    assert ("missing_in_target", "<row>", 3) in by_type
    assert ("missing_in_source", "<row>", 4) in by_type


def test_streaming_schema_drift_between_batches():
    """A column NULL in the first batch and typed later, and an int column that
    turns float once NULLs appear, still load and compare."""
    source = pd.DataFrame({
        "id": range(6),
        "note": [None, None, None, "x", "y", "z"],
        "qty": pd.array([1, 2, 3, None, 5, 6], dtype="object"),
    })
    target = source.copy()
    target.loc[4, "note"] = "changed"
    target.loc[5, "qty"] = 7
    result = _engine(source, target, "streaming", stream_batch_size=1_000).reconcile("q", "q")
    small = _engine(source, target, "streaming", stream_batch_size=3)._stream_batch_size
    assert small == 3
    streamed = ReconciliationEngine(
        source_engine=_db(source, "dev"), target_engine=_db(target, "qa"),
        key_columns=["id"], strategy="streaming", stream_batch_size=3,
    ).reconcile("q", "q")
    for r in (result, streamed):
        assert r.value_mismatch_count == 2
        assert r.mismatch_by_column == {"note": 1, "qty": 1}


def test_streaming_without_batch_reader_uses_execute_query():
    source, target = _random_pair(n=300)
    expected = _engine(source, target, "in_memory").reconcile("q", "q")
    actual = _engine(source, target, "streaming", batches=False).reconcile("q", "q")
    assert _counts(actual) == _counts(expected)


def test_streaming_empty_sides():
    empty = pd.DataFrame({"id": pd.Series([], dtype="int64"), "v": pd.Series([], dtype="object")})
    full = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
    both_empty = _engine(empty, empty, "streaming").reconcile("q", "q")
    assert both_empty.status == TestStatus.PASSED
    assert both_empty.source_row_count == 0
    one_empty = _engine(full, empty, "streaming").reconcile("q", "q")
    assert one_empty.missing_in_target_count == 2
    assert one_empty.target_row_count == 0


def test_streaming_composite_keys_and_duplicate_warning(caplog):
    source = pd.DataFrame({"a": [1, 1, 2, 2], "b": ["x", "y", "x", "x"], "v": [1, 2, 3, 3]})
    target = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "y", "x"], "v": [1, 9, 3]})
    with caplog.at_level(logging.WARNING, logger="etl_framework.reconciliation.large_table"):
        result = _engine(source, target, "streaming", key_columns=["a", "b"]).reconcile("q", "q")
    assert result.value_mismatch_count == 1
    assert result.mismatches[0].key_values == {"a": 1, "b": "y"}
    assert "duplicate key rows" in caplog.text


def test_streaming_null_semantics():
    source = pd.DataFrame({"id": [1, 2, 3], "v": [None, "a", None]})
    target = pd.DataFrame({"id": [1, 2, 3], "v": [None, None, "b"]})
    equal = _engine(source, target, "streaming").reconcile("q", "q")
    assert equal.value_mismatch_count == 2
    strict = _engine(source, target, "streaming", null_equals_null=False).reconcile("q", "q")
    assert strict.value_mismatch_count == 3


def test_streaming_tolerances():
    source = pd.DataFrame({"id": [1, 2], "a": [1.0, 1.0], "b": [1.0, 1.0],
                           "t": pd.to_datetime(["2024-01-01 00:00:00", "2024-01-01 00:00:00"])})
    target = pd.DataFrame({"id": [1, 2], "a": [1.05, 1.0], "b": [1.05, 1.0],
                           "t": pd.to_datetime(["2024-01-01 00:00:01", "2024-01-01 00:00:05"])})
    result = _engine(
        source, target, "streaming",
        float_tolerance=0.01, column_tolerances={"b": 0.1}, datetime_tolerance_seconds=2,
    ).reconcile("q", "q")
    assert result.mismatch_by_column == {"a": 1, "t": 1}


def test_streaming_exclude_and_schema_diff():
    source = pd.DataFrame({"id": [1], "v": ["a"], "only_src": [1], "skip": ["x"]})
    target = pd.DataFrame({"id": [1], "v": ["a"], "only_tgt": [2], "skip": ["y"]})
    result = _engine(source, target, "streaming", exclude_columns=["skip"]).reconcile("q", "q")
    assert result.status == TestStatus.PASSED
    assert result.schema_diff == {"missing_in_target": ["only_src"], "extra_in_target": ["only_tgt"]}
    with pytest.raises(SchemaValidationError):
        _engine(source, target, "streaming", exclude_columns=["skip"],
                schema_mismatch_policy="error").reconcile("q", "q")


def test_streaming_missing_key_column_raises():
    source = pd.DataFrame({"id": [1], "v": ["a"]})
    target = pd.DataFrame({"other": [1], "v": ["a"]})
    with pytest.raises(KeyError):
        _engine(source, target, "streaming").reconcile("q", "q")


def test_streaming_case_and_whitespace_options():
    source = pd.DataFrame({"id": [1, 2], "name": ["Alice  Smith", "BOB"]})
    target = pd.DataFrame({"id": [1, 2], "name": ["alice smith", "bob "]})
    result = _engine(
        source, target, "streaming",
        case_insensitive_columns=["name"], whitespace_normalize_columns=["name"],
    ).reconcile("q", "q")
    assert result.status == TestStatus.PASSED


def test_streaming_incremental_filter():
    source = pd.DataFrame({"id": [1, 2, 3], "v": ["a", "b", "c"],
                           "updated": pd.to_datetime(["2024-01-01", "2024-06-01", None])})
    target = pd.DataFrame({"id": [1, 2, 3], "v": ["x", "b", "c"],
                           "updated": pd.to_datetime(["2024-01-01", "2024-06-01", None])})
    result = _engine(
        source, target, "streaming",
        change_tracking_column="updated", since=datetime(2024, 3, 1),
    ).reconcile("q", "q")
    assert result.status == TestStatus.PASSED       # id 1 changed before `since`
    assert result.source_row_count == 2


def test_streaming_segment_values_and_row_limit():
    source = pd.DataFrame({"id": range(10), "region": ["eu"] * 5 + ["us"] * 5, "v": range(10)})
    target = source.copy()
    target["v"] = target["v"] + 1
    result = _engine(
        source, target, "streaming", segment_columns=["region"], mismatch_row_limit=4,
    ).reconcile("q", "q")
    assert result.value_mismatch_count == 10
    assert len(result.mismatches) == 4
    assert all(m.segment_values and m.segment_values["region"] in {"eu", "us"} for m in result.mismatches)


def test_streaming_cleans_up_spill_dir(tmp_path):
    source, target = _random_pair(n=200)
    _engine(source, target, "streaming", spill_dir=str(tmp_path)).reconcile("q", "q")
    assert os.listdir(tmp_path) == []


def test_streaming_cleans_up_on_failure(tmp_path):
    source = pd.DataFrame({"id": [1]})
    target = pd.DataFrame({"x": [1]})
    with pytest.raises(KeyError):
        _engine(source, target, "streaming", spill_dir=str(tmp_path)).reconcile("q", "q")
    assert os.listdir(tmp_path) == []


def test_streaming_ignores_max_compare_rows():
    source, target = _random_pair(n=300)
    result = _engine(source, target, "streaming", max_compare_rows=10).reconcile("q", "q")
    assert result.source_row_count == 300


def test_auto_and_pushdown_fall_back_to_streaming_without_sql_dialect(caplog):
    source, target = _random_pair(n=300)
    expected = _engine(source, target, "in_memory").reconcile("q", "q")
    for strategy in ("auto", "pushdown"):
        with caplog.at_level(logging.INFO, logger="etl_framework.reconciliation.large_table"):
            actual = _engine(source, target, strategy).reconcile("q", "q")
        assert _counts(actual) == _counts(expected)
    assert "pushdown unavailable" in caplog.text


def test_unknown_strategy_rejected():
    with pytest.raises(ValueError):
        _engine(pd.DataFrame(), pd.DataFrame(), "fastest")


def test_hash_precheck_skipped_for_non_sqlserver_dialects():
    source = pd.DataFrame({"id": [1], "v": ["a"]})
    eng = _engine(source, source.copy(), "in_memory", chunk_size=10, use_hash_precheck=True)
    for db in (eng._source_engine, eng._target_engine):
        db._engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    result = eng.reconcile("SELECT id, v FROM t", "q")
    assert result.status == TestStatus.PASSED
    for db in (eng._source_engine, eng._target_engine):
        assert all("CHECKSUM" not in str(c) for c in db.execute_query.call_args_list)


# ---------------------------------------------------------------------------
# pushdown SQL builders
# ---------------------------------------------------------------------------

def _cols(*specs):
    return [pd_sql.ColumnInfo(name=n.lower(), db_name=n, kind=k) for n, k in specs]


@pytest.mark.parametrize("tol,scale", [(1e-9, 9), (1e-6, 6), (0.01, 2), (5, 0), (1e-15, 9), (0, 9)])
def test_numeric_scale_for_tolerance(tol, scale):
    assert pd_sql.numeric_scale_for_tolerance(tol) == scale


@pytest.mark.parametrize("dialect", sorted(pd_sql.SUPPORTED_DIALECTS))
def test_summary_and_fetch_queries_render(dialect):
    keys = _cols(("ID", pd_sql.NUMBER))
    values = _cols(("NAME", pd_sql.STRING), ("TS", pd_sql.DATETIME), ("TZ", pd_sql.DATETIME_TZ),
                   ("FLAG", pd_sql.BOOL), ("U", pd_sql.UUID))
    summary = pd_sql.summary_query(dialect, "SELECT * FROM t;", keys, values, 64, 6)
    assert "GROUP BY atom_b" in summary
    assert "SELECT * FROM t)" in summary            # trailing semicolon stripped
    assert " AS atom_q" not in summary              # Oracle rejects AS before table aliases
    fetch = pd_sql.fetch_query(dialect, "SELECT * FROM t", keys + values, keys, 64, 6, list(range(2500)))
    assert fetch.count(" IN (") == 3                # 1000-literal chunks for Oracle
    assert pd_sql.fetch_query(dialect, "SELECT * FROM t", keys, keys, 64, 6, None).count(" IN (") == 0


def test_quote_uses_database_reported_names():
    assert pd_sql.quote("oracle", "ORDER_ID") == '"ORDER_ID"'
    assert pd_sql.quote("mssql", "we]ird") == "[we]]ird]"
    assert pd_sql.quote("postgresql", 'a"b') == '"a""b"'


def test_clob_facing_string_is_hashed_on_both_sides():
    clob = pd_sql.ColumnInfo("doc", "DOC", pd_sql.TEXT_LOB)
    text = pd_sql.ColumnInfo("doc", "doc", pd_sql.STRING)
    src, tgt = pd_sql.align_kinds(clob, text)
    assert (src.kind, tgt.kind) == (pd_sql.TEXT_LOB, pd_sql.STRING_HASHED)
    assert "DBMS_CRYPTO.HASH" in pd_sql.canonical_text("oracle", src, 6)
    assert pd_sql.canonical_text("postgresql", tgt, 6) == 'UPPER(MD5(CAST(atom_q."doc" AS TEXT)))'
    plain = pd_sql.ColumnInfo("n", "n", pd_sql.STRING)
    assert pd_sql.align_kinds(plain, plain) == (plain, plain)


@pytest.mark.parametrize("dialect", sorted(pd_sql.SUPPORTED_DIALECTS))
def test_fetch_returns_hash_for_hashed_columns(dialect):
    keys = _cols(("ID", pd_sql.NUMBER))
    blob = _cols(("PAYLOAD", pd_sql.BINARY))
    sql = pd_sql.fetch_query(dialect, "SELECT * FROM t", keys + blob, keys, 8, 6, [1], {"payload"})
    assert "IS NULL THEN NULL ELSE" in sql
    assert ("DBMS_CRYPTO" in sql) or ("MD5" in sql)


def test_unsupported_kind_cannot_render():
    with pytest.raises(ValueError):
        pd_sql.canonical_text("postgresql", _cols(("BLOB", pd_sql.UNSUPPORTED))[0], 6)


def test_mutating_query_rejected():
    with pytest.raises(ValueError):
        pd_sql.summary_query("postgresql", "DELETE FROM t", _cols(("ID", pd_sql.NUMBER)), [], 8, 6)


@pytest.mark.parametrize("dialect,code,kind", [
    ("postgresql", 23, pd_sql.NUMBER),
    ("postgresql", 1184, pd_sql.DATETIME_TZ),
    ("postgresql", 17, pd_sql.BINARY),
    ("postgresql", 1186, pd_sql.UNSUPPORTED),      # interval
    ("oracle", SimpleNamespace(name="DB_TYPE_NUMBER"), pd_sql.NUMBER),
    ("oracle", SimpleNamespace(name="DB_TYPE_DATE"), pd_sql.DATETIME),
    ("oracle", SimpleNamespace(name="DB_TYPE_CLOB"), pd_sql.TEXT_LOB),
    ("oracle", SimpleNamespace(name="DB_TYPE_BLOB"), pd_sql.BINARY),
    ("oracle", SimpleNamespace(name="DB_TYPE_INTERVAL_DS"), pd_sql.UNSUPPORTED),
    ("mssql", bool, pd_sql.BOOL),
    ("mssql", int, pd_sql.NUMBER),
    ("mssql", bytearray, pd_sql.BINARY),
    ("mssql", object, pd_sql.UNSUPPORTED),
])
def test_column_kind_mapping(dialect, code, kind):
    assert pd_sql.column_kind(dialect, code) == kind


def test_engine_dialect():
    assert pd_sql.engine_dialect(SimpleNamespace(_engine=SimpleNamespace(dialect=SimpleNamespace(name="oracle")))) == "oracle"
    assert pd_sql.engine_dialect(MagicMock()) is None
    assert pd_sql.engine_dialect(object()) is None


# ---------------------------------------------------------------------------
# Different SQL per side and column-name case (SQL Compare: Oracle vs others)
# ---------------------------------------------------------------------------

def _per_query_db(frames: dict, name: str):
    """Engine whose result depends on the query text."""
    db = MagicMock(spec=["execute_query", "iter_query_batches", "_env"])
    db.execute_query.side_effect = lambda q, params=None: frames[q]
    db.iter_query_batches.side_effect = lambda q, params=None, *, batch_size: _batcher(frames[q])(q, params, batch_size=batch_size)
    db._env = SimpleNamespace(name=name)
    return db


@pytest.mark.parametrize("strategy", ["in_memory", "streaming", "auto"])
def test_target_query_runs_on_target_only(strategy):
    source = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
    target = pd.DataFrame({"id": [1, 2], "v": ["a", "x"]})
    src_db = _per_query_db({"SELECT src": source}, "dev")
    tgt_db = _per_query_db({"SELECT tgt": target}, "qa")
    engine = ReconciliationEngine(src_db, tgt_db, key_columns=["id"], strategy=strategy)
    result = engine.reconcile("SELECT src", "q", target_query="SELECT tgt")
    assert result.value_mismatch_count == 1


def test_streaming_matches_columns_and_keys_by_case():
    """Oracle names arrive lower-cased by SQLAlchemy; SQL Server keeps its
    own case; the key may be typed in either case."""
    source = pd.DataFrame({"cust_id": [1, 2, 3], "amount": [1.0, 2.0, 3.0], "Name": ["a", "b", "c"]})
    target = pd.DataFrame({"CUST_ID": [1, 2, 4], "Amount": [1.0, 2.5, 4.0], "NAME": ["a", "b", "d"]})
    result = _engine(source, target, "streaming", key_columns=["CUST_ID"]).reconcile("q", "q")
    assert result.schema_diff is None
    assert result.missing_in_target_count == 1 and result.missing_in_source_count == 1
    assert result.mismatch_by_column == {"<row>": 2, "amount": 1}
    assert {tuple(m.key_values) for m in result.mismatches} == {("CUST_ID",)}


def test_case_alignment_skips_ambiguous_names():
    from etl_framework.reconciliation.large_table import _case_alignment
    assert _case_alignment(["id", "Amount"], ["ID", "AMOUNT"]) == {"ID": "id", "AMOUNT": "Amount"}
    assert _case_alignment(["a", "A"], ["a"]) == {}            # two source spellings
    assert _case_alignment(["x"], ["X", "x"]) == {}            # exact match exists / ambiguous
