import re

import pandas as pd
import pytest
from etl_framework.reconciliation.chunker import (
    build_hash_query,
    build_chunk_query,
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


def test_build_chunk_query_adds_offset_fetch():
    base = "SELECT id, val FROM orders"
    result = build_chunk_query(base, key_columns=["id"], offset=0, chunk_size=1000)
    upper = result.upper()
    assert "ORDER BY" in upper
    assert "OFFSET" in upper
    assert "ROWS FETCH NEXT" in upper or "FETCH NEXT" in upper


def test_build_chunk_query_offset_non_zero():
    base = "SELECT id, val FROM orders"
    result = build_chunk_query(base, key_columns=["id"], offset=500, chunk_size=250)
    assert "500" in result
    assert "250" in result


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


def test_build_chunk_query_rejects_unsafe_key_column():
    with pytest.raises(ValueError, match="Invalid SQL identifier"):
        build_chunk_query("SELECT id FROM t", key_columns=["id;drop"], offset=0, chunk_size=10)


def test_build_chunk_query_raises_on_empty_key_columns():
    with pytest.raises(ValueError, match="key_columns"):
        build_chunk_query("SELECT id FROM t", key_columns=[], offset=0, chunk_size=10)


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


class _WindowEngine:
    """Fake engine honoring the OFFSET/FETCH pagination emitted by build_chunk_query."""

    def __init__(self, name: str, df: pd.DataFrame) -> None:
        self._env = type("E", (), {"name": name})()
        self._df = df

    def execute_query(self, query: str, params=None) -> pd.DataFrame:
        m = re.search(r"OFFSET\s+(\d+)\s+ROWS\s+FETCH\s+NEXT\s+(\d+)", query, re.I)
        if not m:
            return self._df.copy()
        o, n = int(m.group(1)), int(m.group(2))
        return self._df.iloc[o:o + n].reset_index(drop=True)


def test_load_in_chunks_paginates_fully():
    df = pd.DataFrame({"id": [1, 2, 3, 4, 5], "v": list("abcde")})
    out = load_in_chunks(_WindowEngine("dev", df), "SELECT * FROM t", ["id"], 2)
    assert len(out) == 5
    assert list(out["id"]) == [1, 2, 3, 4, 5]


def test_load_in_chunks_single_read_when_disabled():
    df = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
    assert len(load_in_chunks(_WindowEngine("dev", df), "q", ["id"], 0)) == 2
    assert len(load_in_chunks(_WindowEngine("dev", df), "q", [], 5)) == 2


def test_load_in_chunks_applies_normalize():
    df = pd.DataFrame({"id": [1, 2, 3], "v": [1, 2, 3]})
    out = load_in_chunks(
        _WindowEngine("dev", df), "SELECT * FROM t", ["id"], 2,
        normalize=lambda d: d.assign(v=d["v"] * 10),
    )
    assert list(out["v"]) == [10, 20, 30]
