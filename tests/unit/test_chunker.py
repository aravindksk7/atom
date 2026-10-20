import pandas as pd
import pytest
from etl_framework.reconciliation.chunker import (
    build_hash_query,
    hashes_match,
    load_in_chunks,
)


def test_build_hash_query_wraps_original():
    base = "SELECT id, val FROM orders"
    result = build_hash_query(base, key_columns=["id"])
    # Must reference the original query as a subquery
    assert "SELECT id, val FROM orders" in result
    # Must produce a CHECKSUM or hash aggregate column named hash_value
    assert "hash_value" in result.lower()


def test_hashes_match_same_df():
    df = pd.DataFrame({"id": [1, 2, 3], "hash_value": [10, 20, 30]})
    assert hashes_match(df, df) is True


def test_hashes_match_different_df():
    df1 = pd.DataFrame({"id": [1, 2], "hash_value": [10, 20]})
    df2 = pd.DataFrame({"id": [1, 2], "hash_value": [10, 99]})
    assert hashes_match(df1, df2) is False


def test_hashes_match_different_row_count():
    df1 = pd.DataFrame({"id": [1, 2, 3], "hash_value": [10, 20, 30]})
    df2 = pd.DataFrame({"id": [1, 2], "hash_value": [10, 20]})
    assert hashes_match(df1, df2) is False


def test_build_hash_query_raises_on_empty_key_columns():
    with pytest.raises(ValueError, match="key_columns"):
        build_hash_query("SELECT id FROM t", key_columns=[])


def test_build_hash_query_rejects_mutating_base_query():
    with pytest.raises(ValueError, match="read-only"):
        build_hash_query("DELETE FROM orders", key_columns=["id"])


def test_hashes_match_both_empty_returns_true():
    df_empty = pd.DataFrame({"id": [], "hash_value": []})
    assert hashes_match(df_empty, df_empty) is True


def test_hashes_match_empty_vs_nonempty_returns_false():
    df_empty = pd.DataFrame({"id": [], "hash_value": []})
    df_nonempty = pd.DataFrame({"id": [1], "hash_value": [42]})
    assert hashes_match(df_empty, df_nonempty) is False


def test_hashes_match_copy_not_identity():
    df = pd.DataFrame({"id": [1, 2], "hash_value": [10, 20]})
    assert hashes_match(df, df.copy()) is True


class _BatchEngine:
    """Fake engine with cursor batching, recording how it was called."""

    def __init__(self, df: pd.DataFrame) -> None:
        self._env = type("E", (), {"name": "dev"})()
        self._df = df
        self.batch_calls: list[tuple[str, dict | None, int]] = []
        self.execute_calls: list[str] = []

    def execute_query(self, query: str, params=None) -> pd.DataFrame:
        self.execute_calls.append(query)
        return self._df.copy()

    def iter_query_batches(self, query: str, params=None, *, batch_size: int):
        self.batch_calls.append((query, params, batch_size))
        if self._df.empty:
            yield self._df.iloc[0:0]
            return
        for start in range(0, len(self._df), batch_size):
            yield self._df.iloc[start:start + batch_size].reset_index(drop=True)


class _PlainEngine:
    """Fake engine without iter_query_batches (file-backed engines, test doubles)."""

    def __init__(self, df: pd.DataFrame) -> None:
        self._env = type("E", (), {"name": "dev"})()
        self._df = df
        self.queries: list[str] = []

    def execute_query(self, query: str, params=None) -> pd.DataFrame:
        self.queries.append(query)
        return self._df.copy()


def test_load_in_chunks_reads_one_query_in_batches():
    df = pd.DataFrame({"id": [1, 2, 3, 4, 5], "v": list("abcde")})
    engine = _BatchEngine(df)
    out = load_in_chunks(engine, "SELECT * FROM t", ["id"], 2)
    assert list(out["id"]) == [1, 2, 3, 4, 5]
    assert engine.batch_calls == [("SELECT * FROM t", None, 2)]
    assert engine.execute_calls == []


def test_load_in_chunks_does_not_rewrite_the_query():
    engine = _BatchEngine(pd.DataFrame({"id": [1]}))
    load_in_chunks(engine, "SELECT id FROM t", ["id"], 10)
    sent = engine.batch_calls[0][0]
    assert "OFFSET" not in sent.upper()
    assert "ORDER BY" not in sent.upper()


def test_load_in_chunks_batches_without_key_columns():
    engine = _BatchEngine(pd.DataFrame({"id": [1, 2, 3]}))
    out = load_in_chunks(engine, "SELECT id FROM t", [], 2)
    assert len(out) == 3
    assert len(engine.batch_calls) == 1


def test_load_in_chunks_passes_params():
    engine = _BatchEngine(pd.DataFrame({"id": [1]}))
    load_in_chunks(engine, "SELECT id FROM t WHERE d = :d", ["id"], 5, params={"d": "2026-10-19"})
    assert engine.batch_calls[0][1] == {"d": "2026-10-19"}


def test_load_in_chunks_single_read_when_disabled():
    engine = _BatchEngine(pd.DataFrame({"id": [1, 2]}))
    assert len(load_in_chunks(engine, "q", ["id"], 0)) == 2
    assert engine.batch_calls == []
    assert engine.execute_calls == ["q"]


def test_load_in_chunks_single_read_for_engine_without_batching():
    engine = _PlainEngine(pd.DataFrame({"id": [1, 2, 3]}))
    out = load_in_chunks(engine, "SELECT * FROM t", ["id"], 2)
    assert len(out) == 3
    assert engine.queries == ["SELECT * FROM t"]


def test_load_in_chunks_applies_normalize_per_batch():
    engine = _BatchEngine(pd.DataFrame({"id": [1, 2, 3], "v": [1, 2, 3]}))
    out = load_in_chunks(
        engine, "SELECT * FROM t", ["id"], 2,
        normalize=lambda d: d.assign(v=d["v"] * 10),
    )
    assert list(out["v"]) == [10, 20, 30]


def test_load_in_chunks_empty_result_returns_empty_frame():
    engine = _BatchEngine(pd.DataFrame({"id": []}))
    out = load_in_chunks(engine, "SELECT * FROM t", ["id"], 2)
    assert out.empty
    assert list(out.columns) == ["id"]
