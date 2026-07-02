from datetime import datetime, timezone

from etl_framework.reporting.generator import to_local


def test_to_local_converts_utc_datetime_to_local_with_zone_abbreviation():
    utc_dt = datetime(2026, 7, 1, 18, 30, 0, tzinfo=timezone.utc)
    result = to_local(utc_dt)
    assert result == utc_dt.astimezone().strftime("%Y-%m-%d %H:%M %Z")


def test_to_local_returns_empty_string_for_none():
    assert to_local(None) == ""
