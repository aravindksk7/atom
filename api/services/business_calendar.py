from __future__ import annotations

from datetime import date, timedelta
from typing import Literal


WeekendPolicy = Literal["skip", "shift", "ignore"]


def _is_weekend(value: date) -> bool:
    return value.weekday() >= 5


def _shift_to_monday(value: date) -> date:
    if value.weekday() == 5:
        return value + timedelta(days=2)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def resolve_weekend(value: date, weekend_policy: WeekendPolicy) -> date | None:
    """Resolve a single candidate against weekend_policy in place, without
    stepping it forward -- for a caller that already has a specific date and
    needs to know whether/how to use it *as this occurrence* (the scheduler's
    current firing), as opposed to `step_business_date` below, which computes
    the *next* candidate. Returns None for 'skip' when `value` is a weekend
    (the caller should not fire this occurrence at all); otherwise returns
    `value`, shifted to the following Monday first when `weekend_policy ==
    'shift'` and it lands on a weekend."""
    if weekend_policy == "shift" and _is_weekend(value):
        return _shift_to_monday(value)
    if weekend_policy == "skip" and _is_weekend(value):
        return None
    return value


def step_business_date(value: date, step_days: int, weekend_policy: WeekendPolicy) -> date:
    """Advance `value` by `step_days`, then resolve `weekend_policy` against
    the result -- the same per-candidate transition `step_dates` applies
    internally on each of its loop iterations, exposed for a caller (the
    scheduler) that advances one persisted cursor at a time rather than
    generating a whole batch up front. `skip` walks forward `step_days` at a
    time until landing on a weekday, eagerly resolving an entire weekend in
    one call -- the cursor this returns is always a date the caller can fire
    on, never a weekend date to retry later. `shift` moves a single weekend
    landing to the following Monday. `ignore` does nothing extra."""
    if step_days < 1:
        raise ValueError("step_days must be at least 1")
    if weekend_policy not in {"skip", "shift", "ignore"}:
        raise ValueError("weekend_policy must be skip, shift, or ignore")

    candidate = value + timedelta(days=step_days)
    if weekend_policy == "shift":
        return _shift_to_monday(candidate)
    if weekend_policy == "skip":
        while _is_weekend(candidate):
            candidate += timedelta(days=step_days)
        return candidate
    return candidate


def step_dates(start: date, count: int, step_days: int, weekend_policy: WeekendPolicy) -> list[date]:
    if count < 1:
        raise ValueError("count must be at least 1")
    if step_days < 1:
        raise ValueError("step_days must be at least 1")
    if weekend_policy not in {"skip", "shift", "ignore"}:
        raise ValueError("weekend_policy must be skip, shift, or ignore")

    dates: list[date] = []
    candidate = start
    while len(dates) < count:
        if weekend_policy == "ignore":
            dates.append(candidate)
        elif weekend_policy == "shift":
            dates.append(_shift_to_monday(candidate))
        elif not _is_weekend(candidate):
            dates.append(candidate)
        candidate += timedelta(days=step_days)
    return dates
