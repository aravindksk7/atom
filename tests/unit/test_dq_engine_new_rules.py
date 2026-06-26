"""Tests for new DQ rule types added in the ETL capabilities expansion."""
from __future__ import annotations
import pandas as pd
import pytest
from etl_framework.reconciliation.dq_engine import DQEngine, DQViolation
from api.schemas import DQRule


def _rule(**kwargs) -> DQRule:
    return DQRule.model_validate(kwargs)


def _eval(df, **rule_kwargs) -> list[DQViolation]:
    return DQEngine().evaluate(df, [_rule(**rule_kwargs)])


# ── completeness_ratio ──────────────────────────────────────────────────────

def test_completeness_ratio_passes():
    df = pd.DataFrame({"x": [1.0, 2.0, None, 4.0, 5.0]})  # 80% non-null
    assert _eval(df, type="completeness_ratio", column="x", min_value=0.75) == []


def test_completeness_ratio_fails():
    df = pd.DataFrame({"x": [1.0, None, None, None, 5.0]})  # 40% non-null
    vs = _eval(df, type="completeness_ratio", column="x", min_value=0.75)
    assert len(vs) == 1 and vs[0].rule_type == "completeness_ratio"


def test_completeness_ratio_empty_df():
    df = pd.DataFrame({"x": pd.Series([], dtype=float)})
    assert _eval(df, type="completeness_ratio", column="x", min_value=0.9) == []


# ── distinct_count_between ──────────────────────────────────────────────────

def test_distinct_count_between_passes():
    df = pd.DataFrame({"status": ["A", "B", "C", "A", "B"]})  # 3 distinct
    assert _eval(df, type="distinct_count_between", column="status", min_value=2, max_value=5) == []


def test_distinct_count_between_fails_low():
    df = pd.DataFrame({"status": ["A", "A", "A"]})  # 1 distinct
    vs = _eval(df, type="distinct_count_between", column="status", min_value=2, max_value=5)
    assert len(vs) == 1


def test_distinct_count_between_fails_high():
    df = pd.DataFrame({"status": ["A", "B", "C", "D", "E", "F"]})  # 6 distinct
    vs = _eval(df, type="distinct_count_between", column="status", min_value=2, max_value=4)
    assert len(vs) == 1


# ── column_sum_between ──────────────────────────────────────────────────────

def test_column_sum_between_passes():
    df = pd.DataFrame({"amount": [10.0, 20.0, 30.0]})  # sum=60
    assert _eval(df, type="column_sum_between", column="amount", min_value=50.0, max_value=70.0) == []


def test_column_sum_between_fails():
    df = pd.DataFrame({"amount": [10.0, 20.0, 30.0]})  # sum=60
    vs = _eval(df, type="column_sum_between", column="amount", min_value=100.0, max_value=200.0)
    assert len(vs) == 1 and vs[0].actual_value == pytest.approx(60.0)


# ── column_std_dev_between ──────────────────────────────────────────────────

def test_column_std_dev_passes():
    df = pd.DataFrame({"price": [10.0, 10.0, 10.0, 10.0]})  # std=0
    assert _eval(df, type="column_std_dev_between", column="price", min_value=0.0, max_value=1.0) == []


def test_column_std_dev_fails():
    df = pd.DataFrame({"price": [1.0, 100.0, 1.0, 100.0]})  # high std
    vs = _eval(df, type="column_std_dev_between", column="price", min_value=0.0, max_value=5.0)
    assert len(vs) == 1


# ── column_percentile ──────────────────────────────────────────────────────

def test_column_percentile_passes():
    df = pd.DataFrame({"latency": list(range(100))})  # p95=94
    assert _eval(df, type="column_percentile", column="latency", percentile=95, min_value=90.0, max_value=96.0) == []


def test_column_percentile_fails():
    df = pd.DataFrame({"latency": list(range(100))})  # p95=94
    vs = _eval(df, type="column_percentile", column="latency", percentile=95, min_value=0.0, max_value=50.0)
    assert len(vs) == 1


# ── column_type_check ──────────────────────────────────────────────────────

def test_column_type_check_int_passes():
    df = pd.DataFrame({"qty": ["1", "2", "3"]})
    assert _eval(df, type="column_type_check", column="qty", expected_type="int") == []


def test_column_type_check_int_fails():
    df = pd.DataFrame({"qty": ["1", "two", "3"]})
    vs = _eval(df, type="column_type_check", column="qty", expected_type="int")
    assert len(vs) == 1


def test_column_type_check_date_passes():
    df = pd.DataFrame({"dt": ["2024-01-01", "2024-06-15"]})
    assert _eval(df, type="column_type_check", column="dt", expected_type="date") == []


def test_column_type_check_date_fails():
    df = pd.DataFrame({"dt": ["2024-01-01", "not-a-date"]})
    vs = _eval(df, type="column_type_check", column="dt", expected_type="date")
    assert len(vs) == 1


# ── column_value_between ───────────────────────────────────────────────────

def test_column_value_between_passes():
    df = pd.DataFrame({"score": [5, 7, 9, 10]})
    assert _eval(df, type="column_value_between", column="score", min_value=1, max_value=10) == []


def test_column_value_between_fails():
    df = pd.DataFrame({"score": [5, 7, 15, 10]})  # 15 is out of range
    vs = _eval(df, type="column_value_between", column="score", min_value=1, max_value=10)
    assert len(vs) == 1 and vs[0].actual_value == 1


# ── cross_column_consistency ───────────────────────────────────────────────

def test_cross_column_consistency_passes():
    df = pd.DataFrame({"start": [1, 2, 3], "end": [4, 5, 6]})
    assert _eval(df, type="cross_column_consistency", column="start", column_b="end", operator="<=") == []


def test_cross_column_consistency_fails():
    df = pd.DataFrame({"start": [1, 10, 3], "end": [4, 5, 6]})  # row 1: 10 > 5
    vs = _eval(df, type="cross_column_consistency", column="start", column_b="end", operator="<=")
    assert len(vs) == 1 and vs[0].actual_value == 1


# ── pii_mask_check ─────────────────────────────────────────────────────────

def test_pii_mask_check_clean_column_passes():
    df = pd.DataFrame({"ssn": ["***-**-1234", "***-**-5678"]})
    assert _eval(df, type="pii_mask_check", column="ssn", pattern=r"\d{3}-\d{2}-\d{4}") == []


def test_pii_mask_check_finds_unmasked():
    df = pd.DataFrame({"ssn": ["***-**-1234", "123-45-6789"]})  # second row is unmasked
    vs = _eval(df, type="pii_mask_check", column="ssn", pattern=r"\d{3}-\d{2}-\d{4}")
    assert len(vs) == 1 and vs[0].actual_value == 1


# ── no_whitespace ──────────────────────────────────────────────────────────

def test_no_whitespace_passes():
    df = pd.DataFrame({"name": ["Alice", "Bob", "Charlie"]})
    assert _eval(df, type="no_whitespace", column="name") == []


def test_no_whitespace_fails():
    df = pd.DataFrame({"name": ["Alice", " Bob", "Charlie "]})
    vs = _eval(df, type="no_whitespace", column="name")
    assert len(vs) == 1 and vs[0].actual_value == 2


# ── referential_check ──────────────────────────────────────────────────────

def test_referential_check_passes():
    from unittest.mock import MagicMock
    df = pd.DataFrame({"customer_id": [1, 2, 3]})
    mock_engine = MagicMock()
    mock_engine.execute_query.return_value = pd.DataFrame({"id": [1, 2, 3, 4, 5]})
    rule = _rule(type="referential_check", column="customer_id", lookup_query="SELECT id FROM customers")
    vs = DQEngine().evaluate(df, [rule], engine=mock_engine)
    assert vs == []


def test_referential_check_fails():
    from unittest.mock import MagicMock
    df = pd.DataFrame({"customer_id": [1, 2, 99]})  # 99 not in lookup
    mock_engine = MagicMock()
    mock_engine.execute_query.return_value = pd.DataFrame({"id": [1, 2, 3]})
    rule = _rule(type="referential_check", column="customer_id", lookup_query="SELECT id FROM customers")
    vs = DQEngine().evaluate(df, [rule], engine=mock_engine)
    assert len(vs) == 1 and vs[0].actual_value == 1


def test_referential_check_skips_without_engine():
    df = pd.DataFrame({"customer_id": [1, 99]})
    rule = _rule(type="referential_check", column="customer_id", lookup_query="SELECT id FROM c")
    vs = DQEngine().evaluate(df, [rule])  # no engine
    assert vs == []


# ── custom_sql_assert ──────────────────────────────────────────────────────

def test_custom_sql_assert_passes():
    from unittest.mock import MagicMock
    mock_engine = MagicMock()
    mock_engine.execute_query.return_value = pd.DataFrame({"result": [42]})
    rule = _rule(type="custom_sql_assert", sql="SELECT COUNT(*) as result FROM orders", operator=">=", min_value=10)
    vs = DQEngine().evaluate(pd.DataFrame(), [rule], engine=mock_engine)
    assert vs == []


def test_custom_sql_assert_fails():
    from unittest.mock import MagicMock
    mock_engine = MagicMock()
    mock_engine.execute_query.return_value = pd.DataFrame({"result": [3]})
    rule = _rule(type="custom_sql_assert", sql="SELECT COUNT(*) as result FROM orders", operator=">=", min_value=10)
    vs = DQEngine().evaluate(pd.DataFrame(), [rule], engine=mock_engine)
    assert len(vs) == 1


def test_custom_sql_assert_skips_without_engine():
    rule = _rule(type="custom_sql_assert", sql="SELECT 1", operator=">=", min_value=1)
    vs = DQEngine().evaluate(pd.DataFrame(), [rule])
    assert vs == []
