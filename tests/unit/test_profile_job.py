"""Tests for profile_service: compute_profile and detect_drift."""
import pandas as pd
import pytest
from api.services.profile_service import compute_profile, detect_drift


def _df():
    return pd.DataFrame({
        "amount": [10.0, 20.0, None, 40.0, 50.0],
        "status": ["A", "B", "A", "C", "B"],
    })


def test_compute_profile_columns():
    profile = compute_profile(_df(), columns=["amount", "status"])
    assert set(profile.keys()) == {"amount", "status"}


def test_compute_profile_null_rate():
    profile = compute_profile(_df(), columns=["amount"])
    assert profile["amount"]["null_rate"] == pytest.approx(0.2)


def test_compute_profile_distinct_count():
    profile = compute_profile(_df(), columns=["status"])
    assert profile["status"]["distinct_count"] == 3


def test_compute_profile_numeric_stats():
    profile = compute_profile(_df(), columns=["amount"])
    assert profile["amount"]["mean_val"] == pytest.approx(30.0)
    assert profile["amount"]["p50"] is not None


def test_compute_profile_all_columns_when_empty_list():
    profile = compute_profile(_df(), columns=[])
    assert "amount" in profile and "status" in profile


def test_detect_drift_no_drift():
    current = {"amount": {"mean_val": 100.0, "null_rate": 0.1}}
    previous = {"amount": {"mean_val": 100.0, "null_rate": 0.1}}
    assert detect_drift(current, previous, threshold_pct=20.0) == []


def test_detect_drift_flags_column():
    current = {"amount": {"mean_val": 200.0, "null_rate": 0.1}}  # mean doubled
    previous = {"amount": {"mean_val": 100.0, "null_rate": 0.1}}
    flagged = detect_drift(current, previous, threshold_pct=20.0)
    assert "amount" in flagged


def test_detect_drift_first_run_no_previous():
    current = {"amount": {"mean_val": 100.0}}
    assert detect_drift(current, {}, threshold_pct=20.0) == []
