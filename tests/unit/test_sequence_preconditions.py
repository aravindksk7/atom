"""The three sequence precondition gates."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import (
    RequireRunSuccess, SequencePrecondition, TimeWindow,
)
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.models import TestResult


def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _check(db, precondition, now, tz="UTC"):
    from api.services.sequence_preconditions import check
    return check(db, precondition, now=now, timezone_name=tz)


# --- nothing to check -------------------------------------------------------

def test_none_passes():
    assert _check(_db(), None, _at(2026, 8, 13, 12)).ok is True


def test_empty_precondition_passes():
    assert _check(_db(), SequencePrecondition(), _at(2026, 8, 13, 12)).ok is True


# --- time_window ------------------------------------------------------------

def test_inside_the_window_passes():
    p = SequencePrecondition(time_window=TimeWindow(start="09:00", end="17:00"))
    assert _check(_db(), p, _at(2026, 8, 13, 12)).ok is True


def test_before_the_window_fails():
    p = SequencePrecondition(time_window=TimeWindow(start="09:00", end="17:00"))
    result = _check(_db(), p, _at(2026, 8, 13, 8))
    assert result.ok is False
    assert result.gate == "time_window"
    assert "09:00" in result.reason


def test_after_the_window_fails():
    p = SequencePrecondition(time_window=TimeWindow(start="09:00", end="17:00"))
    assert _check(_db(), p, _at(2026, 8, 13, 18)).ok is False


def test_window_boundaries_are_inclusive():
    p = SequencePrecondition(time_window=TimeWindow(start="09:00", end="17:00"))
    assert _check(_db(), p, _at(2026, 8, 13, 9, 0)).ok is True
    assert _check(_db(), p, _at(2026, 8, 13, 17, 0)).ok is True


def test_window_wrapping_past_midnight():
    p = SequencePrecondition(time_window=TimeWindow(start="22:00", end="04:00"))
    assert _check(_db(), p, _at(2026, 8, 13, 23)).ok is True    # late evening
    assert _check(_db(), p, _at(2026, 8, 13, 2)).ok is True     # small hours
    assert _check(_db(), p, _at(2026, 8, 13, 12)).ok is False   # midday


def test_window_is_evaluated_in_the_app_timezone():
    p = SequencePrecondition(time_window=TimeWindow(start="09:00", end="17:00"))
    # 08:00 UTC is 09:00 in Berlin (CEST, +02:00 in August) -- inside there,
    # outside in UTC.
    assert _check(_db(), p, _at(2026, 8, 13, 8), tz="UTC").ok is False
    assert _check(_db(), p, _at(2026, 8, 13, 8), tz="Europe/Berlin").ok is True


def test_unknown_timezone_falls_back_to_utc():
    p = SequencePrecondition(time_window=TimeWindow(start="09:00", end="17:00"))
    assert _check(_db(), p, _at(2026, 8, 13, 12), tz="Not/AZone").ok is True


# --- weekdays ---------------------------------------------------------------

def test_allowed_weekday_passes():
    # 2026-08-13 is a Thursday -> weekday() == 3
    p = SequencePrecondition(weekdays=[0, 1, 2, 3, 4])
    assert _check(_db(), p, _at(2026, 8, 13, 12)).ok is True


def test_disallowed_weekday_fails():
    p = SequencePrecondition(weekdays=[5, 6])
    result = _check(_db(), p, _at(2026, 8, 13, 12))
    assert result.ok is False
    assert result.gate == "weekdays"
    assert "Thursday" in result.reason


def test_weekday_uses_the_app_timezone():
    # 23:30 UTC Thursday is already Friday in Tokyo (+09:00).
    p = SequencePrecondition(weekdays=[4])          # Friday only
    assert _check(_db(), p, _at(2026, 8, 13, 23, 30), tz="UTC").ok is False
    assert _check(_db(), p, _at(2026, 8, 13, 23, 30), tz="Asia/Tokyo").ok is True


# --- require_run_success ----------------------------------------------------

def _result(db, name, status, executed_at):
    db.add(TestResult(
        run_id="r-1", query_name=name, status=status,
        duration_seconds=0.0, source_row_count=0, target_row_count=0,
        value_mismatch_count=0, missing_in_target_count=0,
        missing_in_source_count=0, executed_at=executed_at,
    ))
    db.commit()


def test_recent_passed_run_satisfies_the_gate():
    db = _db()
    now = _at(2026, 8, 13, 12)
    _result(db, "upstream", "PASSED", now - timedelta(hours=2))
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="upstream", within_hours=6))
    assert _check(db, p, now).ok is True


def test_slow_also_counts_as_success():
    db = _db()
    now = _at(2026, 8, 13, 12)
    _result(db, "upstream", "SLOW", now - timedelta(hours=1))
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="upstream", within_hours=6))
    assert _check(db, p, now).ok is True


def test_too_old_a_run_fails():
    db = _db()
    now = _at(2026, 8, 13, 12)
    _result(db, "upstream", "PASSED", now - timedelta(hours=30))
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="upstream", within_hours=6))
    result = _check(db, p, now)
    assert result.ok is False
    assert result.gate == "require_run_success"
    assert "upstream" in result.reason


def test_a_failed_run_does_not_satisfy_the_gate():
    db = _db()
    now = _at(2026, 8, 13, 12)
    _result(db, "upstream", "FAILED", now - timedelta(hours=1))
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="upstream", within_hours=6))
    assert _check(db, p, now).ok is False


def test_no_run_at_all_fails():
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="never_ran", within_hours=6))
    result = _check(_db(), p, _at(2026, 8, 13, 12))
    assert result.ok is False
    assert "never" in result.reason.lower()


def test_the_newest_run_decides():
    db = _db()
    now = _at(2026, 8, 13, 12)
    _result(db, "upstream", "PASSED", now - timedelta(hours=5))
    _result(db, "upstream", "FAILED", now - timedelta(hours=1))
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="upstream", within_hours=6))
    # The most recent attempt failed, so the gate is not satisfied even though
    # an older one passed inside the window.
    assert _check(db, p, now).ok is False


def test_naive_timestamps_compare_correctly():
    # SQLite hands back naive datetimes; the checker must not blow up.
    db = _db()
    now = _at(2026, 8, 13, 12)
    _result(db, "upstream", "PASSED", datetime(2026, 8, 13, 11))   # naive
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="upstream", within_hours=6))
    assert _check(db, p, now).ok is True


# --- ordering ---------------------------------------------------------------

def test_the_first_failing_gate_is_reported():
    p = SequencePrecondition(
        time_window=TimeWindow(start="09:00", end="17:00"),
        weekdays=[5, 6],
    )
    # Both gates fail at 08:00 on a Thursday; time_window is checked first.
    assert _check(_db(), p, _at(2026, 8, 13, 8)).gate == "time_window"
