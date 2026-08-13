"""Gates that decide whether a saved sequence should run at all.

Evaluated BEFORE a run is created, by both selection launch and the scheduler.
A failed gate means nothing happened -- no run row, no cancelled history entry.

`now` and `timezone_name` are injected so every rule is testable without
freezing the clock or touching app settings.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from etl_framework.repository.models import TestResult

if TYPE_CHECKING:  # pragma: no cover
    from api.schemas import SequencePrecondition

# A job that reached either of these is considered to have succeeded. SLOW
# passed its comparison, it just took longer than the threshold.
SUCCESS_STATUSES = ("PASSED", "SLOW")

_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class PreconditionResult:
    ok: bool
    gate: str | None = None
    reason: str | None = None


def _zone(timezone_name: str | None) -> ZoneInfo:
    """The app timezone, falling back to UTC rather than raising.

    A malformed settings row must not make every launch fail.
    """
    if not timezone_name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def _naive_utc(value: datetime) -> datetime:
    """Normalise to naive UTC so SQLite and Postgres timestamps compare.

    SQLite returns naive datetimes, Postgres returns aware ones; comparing the
    two directly raises TypeError.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _minutes(hhmm: str) -> int:
    hours, _, minutes = hhmm.partition(":")
    return int(hours) * 60 + int(minutes)


def _check_time_window(window, local: datetime) -> PreconditionResult | None:
    start, end = _minutes(window.start), _minutes(window.end)
    current = local.hour * 60 + local.minute
    inside = start <= current <= end if start <= end else (current >= start or current <= end)
    if inside:
        return None
    return PreconditionResult(
        ok=False, gate="time_window",
        reason=(
            f"Outside the allowed window {window.start}-{window.end} "
            f"({local.strftime('%H:%M')} local time)"
        ),
    )


def _check_weekdays(weekdays: list[int], local: datetime) -> PreconditionResult | None:
    if local.weekday() in weekdays:
        return None
    allowed = ", ".join(_DAY_NAMES[d] for d in sorted(weekdays)) or "no days"
    return PreconditionResult(
        ok=False, gate="weekdays",
        reason=f"{_DAY_NAMES[local.weekday()]} is not an allowed day (allowed: {allowed})",
    )


def _check_require_run_success(db: Session, rule, now: datetime) -> PreconditionResult | None:
    """The most recent run of the named job must have succeeded, recently.

    Fetches the newest matching row and compares in Python rather than
    filtering on the timestamp in SQL, which keeps it free of dialect-specific
    timezone behaviour.
    """
    latest = (
        db.query(TestResult)
        .filter(TestResult.query_name == rule.job_name)
        .filter(TestResult.executed_at.isnot(None))
        .order_by(TestResult.executed_at.desc())
        .first()
    )
    if latest is None:
        return PreconditionResult(
            ok=False, gate="require_run_success",
            reason=f"Job '{rule.job_name}' has never run",
        )

    cutoff = _naive_utc(now) - timedelta(hours=rule.within_hours)
    executed = _naive_utc(latest.executed_at)
    if executed < cutoff:
        return PreconditionResult(
            ok=False, gate="require_run_success",
            reason=(
                f"Job '{rule.job_name}' last ran at {executed:%Y-%m-%d %H:%M} UTC, "
                f"outside the last {rule.within_hours}h"
            ),
        )
    if latest.status not in SUCCESS_STATUSES:
        return PreconditionResult(
            ok=False, gate="require_run_success",
            reason=f"Job '{rule.job_name}' last finished {latest.status}, not a success",
        )
    return None


def check(
    db: Session,
    preconditions: "SequencePrecondition | None",
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> PreconditionResult:
    """Evaluate every gate, reporting the first failure."""
    if preconditions is None:
        return PreconditionResult(ok=True)

    now = now or datetime.now(timezone.utc)
    local = now.astimezone(_zone(timezone_name))

    if preconditions.time_window is not None:
        failure = _check_time_window(preconditions.time_window, local)
        if failure is not None:
            return failure

    if preconditions.weekdays is not None:
        failure = _check_weekdays(preconditions.weekdays, local)
        if failure is not None:
            return failure

    if preconditions.require_run_success is not None:
        failure = _check_require_run_success(db, preconditions.require_run_success, now)
        if failure is not None:
            return failure

    return PreconditionResult(ok=True)


def check_for_session(db: Session, preconditions: "SequencePrecondition | None") -> PreconditionResult:
    """check() with the app timezone read from settings. The production entry point."""
    from etl_framework.repository.repository import SettingsRepository

    try:
        timezone_name = SettingsRepository(db).get_timezone()
    except Exception:
        timezone_name = "UTC"
    return check(db, preconditions, timezone_name=timezone_name)
