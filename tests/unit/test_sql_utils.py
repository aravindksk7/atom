from __future__ import annotations

import pytest

from etl_framework.db.sql_utils import (
    is_read_only_sql,
    quote_identifier,
    reject_mutating_sql,
    strip_trailing_semicolon,
    validate_identifier,
    wrap_query,
)


def test_read_only_sql_detection():
    assert is_read_only_sql("SELECT * FROM orders;")
    assert is_read_only_sql("WITH x AS (SELECT 1) SELECT * FROM x")
    assert not is_read_only_sql("DELETE FROM orders")
    assert not is_read_only_sql("SELECT * FROM a; SELECT * FROM b")


def test_reject_mutating_sql_returns_clean_query():
    assert reject_mutating_sql(" SELECT 1; ") == "SELECT 1"
    with pytest.raises(ValueError, match="read-only"):
        reject_mutating_sql("DROP TABLE orders")


def test_identifier_helpers():
    assert validate_identifier("orders_1") == "orders_1"
    assert quote_identifier("orders", "sqlserver") == "[orders]"
    assert wrap_query("SELECT 1", "src") == '(SELECT 1) AS "src"'
    with pytest.raises(ValueError):
        validate_identifier("orders;drop")


def test_strip_trailing_semicolon():
    assert strip_trailing_semicolon(" SELECT 1;; ") == "SELECT 1"
