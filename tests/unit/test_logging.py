import json
import logging
import re
import pytest
from etl_framework.utils.logging import configure_logging
from etl_framework.utils.context import set_run_id


def test_text_format_is_default(tmp_path):
    log_file = str(tmp_path / "text.log")
    configure_logging(level="INFO", log_file=log_file, log_format="text")
    logging.getLogger("etl_framework.text_test").info("plain message")
    content = (tmp_path / "text.log").read_text()
    assert "plain message" in content
    assert not content.strip().startswith("{")


def test_json_file_handler_emits_parseable_json(tmp_path):
    log_file = str(tmp_path / "json.log")
    set_run_id("json-run-001")
    configure_logging(level="DEBUG", log_file=log_file, log_format="json")
    logging.getLogger("etl_framework.json_test").info("structured message")
    lines = (tmp_path / "json.log").read_text().strip().splitlines()
    assert lines, "Log file should not be empty"
    record = json.loads(lines[-1])
    assert record["message"] == "structured message"
    assert "json-run-001" in str(record)
    set_run_id("")


def test_json_log_contains_level_and_logger(tmp_path):
    log_file = str(tmp_path / "json2.log")
    configure_logging(level="DEBUG", log_file=log_file, log_format="json")
    logging.getLogger("etl_framework.level_test").warning("warn message")
    lines = (tmp_path / "json2.log").read_text().strip().splitlines()
    record = json.loads(lines[-1])
    assert "WARNING" in str(record).upper() or "warning" in str(record).lower()
    assert "warn message" in record.get("message", "")


def test_console_handler_always_uses_text(tmp_path, capsys):
    log_file = str(tmp_path / "console.log")
    configure_logging(level="INFO", log_file=log_file, log_format="json")
    logging.getLogger("etl_framework.console_test").info("console output")
    captured = capsys.readouterr()
    # Console output should NOT be JSON
    console_out = captured.err  # StreamHandler writes to stderr by default
    if console_out.strip():
        assert not console_out.strip().startswith("{")


def test_unknown_log_format_falls_back_to_text(tmp_path):
    log_file = str(tmp_path / "fallback.log")
    # Should not raise; unknown format falls back to text
    configure_logging(level="INFO", log_file=log_file, log_format="unknown_format")
    logging.getLogger("etl_framework.fallback_test").info("fallback message")
    content = (tmp_path / "fallback.log").read_text()
    assert "fallback message" in content


def test_uvicorn_error_logger_writes_to_the_same_file(tmp_path):
    log_file = str(tmp_path / "unify.log")
    configure_logging(level="INFO", log_file=log_file, log_format="text")
    logging.getLogger("uvicorn.error").error("boom from uvicorn")
    content = (tmp_path / "unify.log").read_text()
    assert content.count("boom from uvicorn") == 1


def test_uvicorn_access_logger_writes_to_the_same_file(tmp_path):
    log_file = str(tmp_path / "unify_access.log")
    configure_logging(level="INFO", log_file=log_file, log_format="text")
    logging.getLogger("uvicorn.access").info("127.0.0.1 GET /api/health 200")
    content = (tmp_path / "unify_access.log").read_text()
    assert content.count("127.0.0.1 GET /api/health 200") == 1


def test_json_log_timestamp_has_real_utc_offset_not_fake_z(tmp_path):
    log_file = str(tmp_path / "json_tz.log")
    configure_logging(level="INFO", log_file=log_file, log_format="json")
    logging.getLogger("etl_framework.tz_test").info("tz message")
    lines = (tmp_path / "json_tz.log").read_text().strip().splitlines()
    record = json.loads(lines[-1])
    assert re.search(r"[+-]\d{4}$", record["timestamp"]), record["timestamp"]
