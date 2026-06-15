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
