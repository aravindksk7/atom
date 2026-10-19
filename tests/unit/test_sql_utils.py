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


@pytest.mark.parametrize("sql", [
    "SELECT * FROM t WHERE name = 'x--' ; DROP TABLE t",
    "SELECT 1 /* ' */ ; DROP TABLE t",
    'SELECT * FROM t WHERE "a--" = 1 ; DROP TABLE t',
    "DELETE FROM t",
    "SELECT E'\\'' ; DROP TABLE t ; --'",
    "SELECT 'a\\' ; DROP TABLE t --'",
])
def test_read_only_sql_rejects_statements_hidden_near_literals_and_comments(sql):
    assert not is_read_only_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM t WHERE a = 'a;b'",
    "SELECT * FROM t WHERE action IN ('INSERT','UPDATE')",
    "SELECT 'it''s' AS x",
    "SELECT 'C:\\temp' AS p",
    "SELECT * FROM t WHERE p LIKE 'C:\\%'",
    "SELECT 1 -- trailing comment",
    "SELECT 1 /* c */ FROM t;",
])
def test_read_only_sql_accepts_literals_and_comments(sql):
    assert is_read_only_sql(sql)


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
