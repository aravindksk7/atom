from unittest.mock import MagicMock

import pytest
import requests

from etl_framework.runner.state import TestStatus
from etl_framework.utils.polling import poll_until_terminal


def _poll(fetch, **overrides):
    kwargs = dict(timeout_s=5, poll_interval_s=0.0, max_poll_errors=3, describe="job 'x'")
    kwargs.update(overrides)
    return poll_until_terminal(fetch, **kwargs)


def test_returns_first_terminal_status():
    fetch = MagicMock(side_effect=[TestStatus.RUNNING, TestStatus.PASSED])
    assert _poll(fetch) == TestStatus.PASSED
    assert fetch.call_count == 2


def test_tolerates_read_timeouts_below_limit():
    fetch = MagicMock(side_effect=[
        requests.ReadTimeout("read timed out"),
        requests.ConnectionError("reset"),
        TestStatus.PASSED,
    ])
    assert _poll(fetch) == TestStatus.PASSED
    assert fetch.call_count == 3


def test_raises_after_max_consecutive_errors():
    fetch = MagicMock(side_effect=requests.ReadTimeout("read timed out"))
    with pytest.raises(requests.ReadTimeout):
        _poll(fetch, max_poll_errors=3)
    assert fetch.call_count == 3


def test_successful_poll_resets_error_counter():
    timeout = requests.ReadTimeout("read timed out")
    fetch = MagicMock(side_effect=[
        timeout, timeout, TestStatus.RUNNING, timeout, timeout, TestStatus.FAILED,
    ])
    assert _poll(fetch, max_poll_errors=3) == TestStatus.FAILED


def test_max_poll_errors_of_one_keeps_fail_fast_behaviour():
    fetch = MagicMock(side_effect=requests.ReadTimeout("read timed out"))
    with pytest.raises(requests.ReadTimeout):
        _poll(fetch, max_poll_errors=1)
    assert fetch.call_count == 1


def test_non_network_errors_propagate_immediately():
    fetch = MagicMock(side_effect=ValueError("bad payload"))
    with pytest.raises(ValueError):
        _poll(fetch)
    assert fetch.call_count == 1


def test_deadline_raises_timeout_error_with_description():
    fetch = MagicMock(return_value=TestStatus.RUNNING)
    with pytest.raises(TimeoutError, match="job 'x' did not complete within 0.05s"):
        _poll(fetch, timeout_s=0.05, poll_interval_s=0.01)


def test_deadline_also_applies_while_errors_are_tolerated():
    fetch = MagicMock(side_effect=requests.ReadTimeout("read timed out"))
    with pytest.raises(TimeoutError):
        _poll(fetch, timeout_s=0.05, poll_interval_s=0.01, max_poll_errors=10_000)
