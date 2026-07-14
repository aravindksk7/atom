from __future__ import annotations

from datetime import datetime, timezone

from etl_framework.repository.query_utils import (
    clamp_limit,
    normalize_offset,
    normalize_sort_direction,
    parse_datetime_bound,
)


def test_pagination_bounds():
    assert clamp_limit(None) == 50
    assert clamp_limit(5000, maximum=100) == 100
    assert normalize_offset(-10) == 0


def test_sort_direction():
    assert normalize_sort_direction("asc") == "asc"
    assert normalize_sort_direction("anything") == "desc"


def test_parse_datetime_bound():
    value = parse_datetime_bound("2024-01-01T00:00:00Z")
    assert value == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert parse_datetime_bound(None) is None
