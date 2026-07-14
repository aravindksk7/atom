"""Tests for DQEngine — standalone data-quality rule evaluation."""
from __future__ import annotations

import pandas as pd
import pytest

from etl_framework.reconciliation.dq_engine import DQEngine, DQViolation
from api.schemas import DQRule


def _rule(**kwargs) -> DQRule:
    return DQRule(**kwargs)


def _df(**cols) -> pd.DataFrame:
    return pd.DataFrame(cols)


engine = DQEngine()


class _FakeQueryEngine:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute_query(self, query: str) -> pd.DataFrame:
        self.queries.append(query)
        return pd.DataFrame({"value": [1]})


# ---------------------------------------------------------------------------
# not_null
# ---------------------------------------------------------------------------

def test_not_null_no_violation():
    df = _df(id=[1, 2], val=["a", "b"])
    assert engine.evaluate(df, [_rule(type="not_null", column="val")]) == []


def test_not_null_violation():
    df = _df(id=[1, 2], val=["a", None])
    v = engine.evaluate(df, [_rule(type="not_null", column="val")])
    assert len(v) == 1
    assert v[0].rule_type == "not_null"
    assert v[0].actual_value == 1


def test_not_null_missing_column_is_skipped():
    df = _df(id=[1, 2])
    assert engine.evaluate(df, [_rule(type="not_null", column="nonexistent")]) == []


# ---------------------------------------------------------------------------
# unique
# ---------------------------------------------------------------------------

def test_unique_no_violation():
    df = _df(id=[1, 2, 3])
    assert engine.evaluate(df, [_rule(type="unique", column="id")]) == []


def test_unique_violation():
    df = _df(id=[1, 1, 2])
    v = engine.evaluate(df, [_rule(type="unique", column="id")])
    assert len(v) == 1
    assert v[0].actual_value == 1


# ---------------------------------------------------------------------------
# row_count_min / max / between
# ---------------------------------------------------------------------------

def test_row_count_min_pass():
    df = _df(id=[1, 2, 3])
    assert engine.evaluate(df, [_rule(type="row_count_min", min_value=2)]) == []


def test_row_count_min_fail():
    df = _df(id=[1])
    v = engine.evaluate(df, [_rule(type="row_count_min", min_value=5)])
    assert v and v[0].actual_value == 1


def test_row_count_max_pass():
    df = _df(id=[1, 2])
    assert engine.evaluate(df, [_rule(type="row_count_max", max_value=10)]) == []


def test_row_count_max_fail():
    df = _df(id=[1, 2, 3, 4, 5])
    v = engine.evaluate(df, [_rule(type="row_count_max", max_value=3)])
    assert v and v[0].actual_value == 5


def test_row_count_between_pass():
    df = _df(id=[1, 2, 3])
    assert engine.evaluate(df, [_rule(type="row_count_between", min_value=2, max_value=5)]) == []


def test_row_count_between_fail():
    df = _df(id=[1])
    v = engine.evaluate(df, [_rule(type="row_count_between", min_value=2, max_value=5)])
    assert v and "not in" in v[0].message


# ---------------------------------------------------------------------------
# column_mean_between
# ---------------------------------------------------------------------------

def test_column_mean_between_pass():
    df = _df(amount=[10, 20, 30])
    assert engine.evaluate(df, [_rule(type="column_mean_between", column="amount", min_value=10, max_value=30)]) == []


def test_column_mean_between_fail():
    df = _df(amount=[1, 2, 3])
    v = engine.evaluate(df, [_rule(type="column_mean_between", column="amount", min_value=10, max_value=100)])
    assert v and v[0].column == "amount"


# ---------------------------------------------------------------------------
# match_regex
# ---------------------------------------------------------------------------

def test_match_regex_pass():
    df = _df(code=["ABC-001", "XYZ-999"])
    assert engine.evaluate(df, [_rule(type="match_regex", column="code", pattern=r"[A-Z]{3}-\d{3}")]) == []


def test_match_regex_fail():
    df = _df(code=["ABC-001", "bad_value"])
    v = engine.evaluate(df, [_rule(type="match_regex", column="code", pattern=r"[A-Z]{3}-\d{3}")])
    assert v and v[0].actual_value == 1


# ---------------------------------------------------------------------------
# severity propagation
# ---------------------------------------------------------------------------

def test_severity_warn_preserved():
    df = _df(val=[None])
    v = engine.evaluate(df, [_rule(type="not_null", column="val", severity="warn")])
    assert v[0].severity == "warn"


def test_severity_error_is_default():
    df = _df(val=[None])
    v = engine.evaluate(df, [_rule(type="not_null", column="val")])
    assert v[0].severity == "error"


@pytest.mark.parametrize(
    ("rule", "column", "values", "expected"),
    [
        ({"type": "column_max_length", "column": "v", "max_value": 3}, "v", ["ok", "long"], 1),
        ({"type": "column_min_length", "column": "v", "min_value": 3}, "v", ["ok", "long"], 1),
        ({"type": "value_in_set", "column": "v", "values": ["a", "b"]}, "v", ["a", "c"], 1),
        ({"type": "value_not_in_set", "column": "v", "values": ["x"]}, "v", ["x", "y"], 1),
        ({"type": "column_contains", "column": "v", "pattern": "."}, "v", ["a.b", "ab"], 1),
        ({"type": "positive_values", "column": "v"}, "v", [1, 0, -1, None], 3),
        ({"type": "negative_values", "column": "v"}, "v", [-1, 0, 1, None], 3),
    ],
)
def test_extended_rules(rule, column, values, expected):
    violations = engine.evaluate(_df(**{column: values}), [_rule(**rule)])
    assert len(violations) == 1
    assert violations[0].actual_value == expected


def test_date_range_accepts_iso_date_bounds():
    rule = _rule(
        type="date_range",
        column="created_at",
        min_date="2026-01-01",
        max_date="2026-12-31",
    )
    violations = engine.evaluate(
        _df(created_at=["2025-12-31", "2026-06-01", "invalid"]),
        [rule],
    )
    assert violations[0].actual_value == 2


def test_length_and_contains_rules_ignore_nulls():
    df = _df(v=[None, "abc"])
    rules = [
        _rule(type="column_min_length", column="v", min_value=2),
        _rule(type="column_contains", column="v", pattern="a"),
    ]
    assert engine.evaluate(df, rules) == []


def test_referential_check_rejects_mutating_lookup_query():
    violations = engine.evaluate(
        _df(id=[1]),
        [_rule(type="referential_check", column="id", lookup_query="DELETE FROM ids")],
        engine=_FakeQueryEngine(),
    )
    assert violations == []


def test_custom_sql_assert_rejects_mutating_sql():
    violations = engine.evaluate(
        _df(id=[1]),
        [_rule(type="custom_sql_assert", sql="DROP TABLE ids", operator=">=", min_value=1)],
        engine=_FakeQueryEngine(),
    )
    assert len(violations) == 1
    assert "read-only" in violations[0].message
