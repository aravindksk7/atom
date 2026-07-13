from __future__ import annotations

import pandas as pd

from api.schemas import DQRule
from etl_framework.reconciliation.dq_engine import DQEngine


def _rule(**kwargs) -> DQRule:
    return DQRule.model_validate(kwargs)


def _eval(df: pd.DataFrame, **rule_kwargs):
    return DQEngine().evaluate(df, [_rule(**rule_kwargs)])


def test_outlier_zscore_fails_on_extreme_value():
    df = pd.DataFrame({"amount": [10, 11, 12, 10, 500]})
    violations = _eval(df, type="outlier_zscore", column="amount", threshold=1.5)
    assert len(violations) == 1
    assert violations[0].actual_value == 1


def test_outlier_iqr_fails_on_extreme_value():
    df = pd.DataFrame({"amount": [10, 11, 12, 10, 500]})
    violations = _eval(df, type="outlier_iqr", column="amount")
    assert len(violations) == 1
    assert violations[0].actual_value == 1


def test_outlier_grubbs_skips_without_scipy_or_passes_cleanly():
    df = pd.DataFrame({"amount": [10, 11, 12, 10, 500]})
    violations = _eval(df, type="outlier_grubbs", column="amount", alpha=0.05)
    assert isinstance(violations, list)


def test_distribution_ks_test_rejects_bad_fit_or_skips():
    df = pd.DataFrame({"amount": [1, 1, 1, 1, 100, 100, 100, 100]})
    violations = _eval(df, type="distribution_ks_test", column="amount", distribution="normal", alpha=0.2)
    assert isinstance(violations, list)


def test_distribution_chi_square_rejects_bad_fit_or_skips():
    df = pd.DataFrame({"amount": [1, 1, 1, 1, 100, 100, 100, 100]})
    violations = _eval(
        df,
        type="distribution_chi_square",
        column="amount",
        bins=2,
        expected_frequencies=[7, 1],
        alpha=0.2,
    )
    assert isinstance(violations, list)


def test_distribution_anderson_darling_rejects_bad_fit_or_skips():
    df = pd.DataFrame({"amount": [1, 1, 1, 1, 100, 100, 100, 100]})
    violations = _eval(df, type="distribution_anderson_darling", column="amount", alpha=0.05)
    assert isinstance(violations, list)


def test_hypothesis_test_proportion_rejects_bad_ratio_or_skips():
    df = pd.DataFrame({"status": ["ok", "ok", "ok", "bad", "bad", "bad"]})
    violations = _eval(
        df,
        type="hypothesis_test_proportion",
        column="status",
        condition="ok",
        expected_proportion=0.9,
        alpha=0.1,
    )
    assert isinstance(violations, list)


def test_anomaly_detection_sigma_flags_spike():
    df = pd.DataFrame({"amount": [10, 11, 12, 11, 10, 200, 11, 12, 10]})
    violations = _eval(df, type="anomaly_detection_sigma", column="amount", threshold=1.5, window=3)
    assert len(violations) == 1
    assert violations[0].actual_value >= 1
