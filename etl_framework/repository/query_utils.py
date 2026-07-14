from __future__ import annotations

from datetime import datetime
from typing import Any


def clamp_limit(limit: int | None, default: int = 50, maximum: int = 1000) -> int:
    try:
        value = int(default if limit is None else limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def normalize_offset(offset: int | None) -> int:
    try:
        return max(0, int(offset or 0))
    except (TypeError, ValueError):
        return 0


def normalize_sort_direction(direction: str | None) -> str:
    return "asc" if str(direction or "").lower() == "asc" else "desc"


def parse_datetime_bound(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError("datetime bound must be datetime, ISO string, or None")


def apply_date_range(query, column, start: Any = None, end: Any = None):
    start_dt = parse_datetime_bound(start)
    end_dt = parse_datetime_bound(end)
    if start_dt is not None:
        query = query.filter(column >= start_dt)
    if end_dt is not None:
        query = query.filter(column <= end_dt)
    return query


def apply_pagination(query, limit: int | None, offset: int | None, maximum: int = 1000):
    return query.offset(normalize_offset(offset)).limit(clamp_limit(limit, maximum=maximum))
