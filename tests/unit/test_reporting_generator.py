from datetime import datetime, timezone

from etl_framework.reporting.generator import to_local


def test_to_local_converts_utc_datetime_to_local_with_zone_abbreviation():
    utc_dt = datetime(2026, 7, 1, 18, 30, 0, tzinfo=timezone.utc)
    result = to_local(utc_dt)
    assert result == utc_dt.astimezone().strftime("%Y-%m-%d %H:%M %Z")


def test_to_local_returns_empty_string_for_none():
    assert to_local(None) == ""


def test_to_local_with_tz_name_converts_to_that_zone():
    from zoneinfo import ZoneInfo
    utc_dt = datetime(2026, 7, 1, 18, 30, 0, tzinfo=timezone.utc)
    result = to_local(utc_dt, "America/New_York")
    expected = utc_dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M %Z")
    assert result == expected


def test_to_local_none_tz_name_falls_back_to_system_local():
    utc_dt = datetime(2026, 7, 1, 18, 30, 0, tzinfo=timezone.utc)
    assert to_local(utc_dt, None) == to_local(utc_dt)


def test_report_generator_binds_configured_timezone_to_filter():
    from etl_framework.reporting.generator import ReportGenerator
    from zoneinfo import ZoneInfo
    gen = ReportGenerator(output_dir="./reports", timezone="America/New_York")
    filt = gen._jinja_env.filters["to_local"]
    utc_dt = datetime(2026, 7, 1, 18, 30, 0, tzinfo=timezone.utc)
    expected = utc_dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M %Z")
    assert filt(utc_dt) == expected
