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
