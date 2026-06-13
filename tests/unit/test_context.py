import logging
import pytest
from etl_framework.utils.context import set_run_id, get_run_id
from etl_framework.utils.logging import RunContextFilter, configure_logging


def test_get_run_id_default_is_empty_string():
    set_run_id("")
    assert get_run_id() == ""


def test_set_and_get_run_id_roundtrip():
    set_run_id("test-run-abc")
    assert get_run_id() == "test-run-abc"
    set_run_id("")  # reset


def test_run_context_filter_injects_run_id_into_log_record():
    set_run_id("inject-test-xyz")
    record = logging.LogRecord(
        name="etl_framework.test", level=logging.INFO,
        pathname="", lineno=0, msg="hello", args=(), exc_info=None,
    )
    f = RunContextFilter()
    result = f.filter(record)
    assert result is True
    assert record.run_id == "inject-test-xyz"
    set_run_id("")


def test_run_context_filter_empty_run_id_when_not_set():
    set_run_id("")
    record = logging.LogRecord(
        name="etl_framework.test", level=logging.INFO,
        pathname="", lineno=0, msg="msg", args=(), exc_info=None,
    )
    RunContextFilter().filter(record)
    assert record.run_id == ""


def test_run_id_appears_in_log_file(tmp_path):
    log_file = str(tmp_path / "test.log")
    set_run_id("file-run-999")
    configure_logging(level="DEBUG", log_file=log_file)
    logger = logging.getLogger("etl_framework.context_test")
    logger.info("checking run_id in file")
    content = (tmp_path / "test.log").read_text()
    assert "file-run-999" in content
    set_run_id("")


def test_contextvars_isolation_across_threads():
    import threading
    results = {}

    def set_and_read(run_id, slot):
        set_run_id(run_id)
        import time; time.sleep(0.05)
        results[slot] = get_run_id()

    t1 = threading.Thread(target=set_and_read, args=("run-thread-1", "t1"))
    t2 = threading.Thread(target=set_and_read, args=("run-thread-2", "t2"))
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert results["t1"] == "run-thread-1"
    assert results["t2"] == "run-thread-2"
