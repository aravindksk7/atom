from datetime import date

import pytest

from api.services.business_calendar import step_dates


def test_ignore_policy_returns_raw_calendar_steps():
    assert step_dates(date(2026, 9, 11), 4, 1, "ignore") == [
        date(2026, 9, 11),
        date(2026, 9, 12),
        date(2026, 9, 13),
        date(2026, 9, 14),
    ]


def test_shift_policy_moves_weekend_candidates_to_monday():
    assert step_dates(date(2026, 9, 11), 4, 1, "shift") == [
        date(2026, 9, 11),
        date(2026, 9, 14),
        date(2026, 9, 14),
        date(2026, 9, 14),
    ]


def test_skip_policy_omits_weekends_without_consuming_count():
    assert step_dates(date(2026, 9, 11), 4, 1, "skip") == [
        date(2026, 9, 11),
        date(2026, 9, 14),
        date(2026, 9, 15),
        date(2026, 9, 16),
    ]


def test_weekend_start_is_shifted_or_skipped_to_monday():
    assert step_dates(date(2026, 9, 12), 2, 1, "shift") == [
        date(2026, 9, 14),
        date(2026, 9, 14),
    ]
    assert step_dates(date(2026, 9, 12), 2, 1, "skip") == [
        date(2026, 9, 14),
        date(2026, 9, 15),
    ]


def test_rollover_and_step_days_greater_than_one():
    assert step_dates(date(2026, 12, 30), 4, 2, "skip") == [
        date(2026, 12, 30),
        date(2027, 1, 1),
        date(2027, 1, 5),
        date(2027, 1, 7),
    ]


def test_validates_count_step_days_and_policy():
    with pytest.raises(ValueError, match="count"):
        step_dates(date(2026, 9, 14), 0, 1, "ignore")
    with pytest.raises(ValueError, match="step_days"):
        step_dates(date(2026, 9, 14), 1, 0, "ignore")
    with pytest.raises(ValueError, match="weekend_policy"):
        step_dates(date(2026, 9, 14), 1, 1, "holiday")
