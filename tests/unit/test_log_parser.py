"""Tests for api/services/log_parser.py, including uvicorn-style log lines
that don't use the app's own pipe-delimited format (see the logging
unification in etl_framework/utils/logging.py, which routes uvicorn's
logger through the same file as the app's own logger)."""
from api.services.log_parser import parse_log_events, filter_log_events


UNIFIED_LOG_SAMPLE = (
    "2026-07-02 06:10:00 | INFO     |  | etl_framework.db | starting up\n"
    "INFO:     Started server process [1234]\n"
    "INFO:     Waiting for application startup.\n"
    "INFO:     Application startup complete.\n"
    "2026-07-02 06:10:05 | ERROR    | abc-123 | api.routes.runs | Exception in ASGI application\n"
    "Traceback (most recent call last):\n"
    '  File "x.py", line 1, in <module>\n'
    "    raise ValueError(\"boom\")\n"
    "ValueError: boom\n"
    "2026-07-02 06:10:10 | INFO     |  | etl_framework.db | done\n"
)


def test_uvicorn_style_lines_each_become_their_own_event():
    events = parse_log_events(UNIFIED_LOG_SAMPLE)
    uvicorn_events = [e for e in events if e["text"].startswith("INFO:     ")]
    assert len(uvicorn_events) == 3
    assert all(e["level"] == "INFO" for e in uvicorn_events)


def test_traceback_lines_group_into_the_preceding_error_event():
    events = parse_log_events(UNIFIED_LOG_SAMPLE)
    error_events = [e for e in events if e["level"] == "ERROR"]
    assert len(error_events) == 1
    assert "Traceback (most recent call last):" in error_events[0]["text"]
    assert "ValueError: boom" in error_events[0]["text"]


def test_filter_log_events_by_level_finds_the_traceback_event():
    matches = filter_log_events(UNIFIED_LOG_SAMPLE, level="ERROR")
    assert len(matches) == 1
    assert "ValueError: boom" in matches[0]["text"]


def test_filter_log_events_by_run_id_ignores_uvicorn_lines():
    matches = filter_log_events(UNIFIED_LOG_SAMPLE, run_id="abc-123")
    assert len(matches) == 1
    assert matches[0]["level"] == "ERROR"
