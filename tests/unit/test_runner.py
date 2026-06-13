import time
import pytest
from unittest.mock import MagicMock, patch
from etl_framework.runner.test_runner import TestRunner
from etl_framework.runner.state import TestStatus


def _make_mock_case(name: str, status: TestStatus = TestStatus.PASSED, duration: float = 0.01):
    case = MagicMock()
    case.name = name
    case.status = status
    case.duration_seconds = duration
    return case


def test_runner_executes_all_cases():
    runner = TestRunner(max_workers=2)
    results = []

    def fake_run(name, fn, *args, **kwargs):
        results.append(name)
        return _make_mock_case(name)

    cases = [("case_a", lambda: None), ("case_b", lambda: None)]
    with patch.object(runner, "_run_single", side_effect=fake_run):
        runner.run(cases)

    assert sorted(results) == ["case_a", "case_b"]


def test_runner_returns_all_results():
    runner = TestRunner(max_workers=2)
    cases = [("a", lambda: None), ("b", lambda: None), ("c", lambda: None)]

    with patch.object(runner, "_run_single",
                      side_effect=lambda name, fn, *a, **kw: _make_mock_case(name)):
        all_results = runner.run(cases)

    assert len(all_results) == 3
    assert {r.name for r in all_results} == {"a", "b", "c"}


def test_runner_single_worker_sequential():
    runner = TestRunner(max_workers=1)
    order = []

    def slow_run(name, fn, *args, **kwargs):
        order.append(name)
        return _make_mock_case(name)

    cases = [("x", lambda: None), ("y", lambda: None)]
    with patch.object(runner, "_run_single", side_effect=slow_run):
        runner.run(cases)

    assert order == ["x", "y"]


def test_runner_captures_exception_as_error():
    runner = TestRunner(max_workers=1)

    def failing_run(name, fn, *args, **kwargs):
        raise RuntimeError("boom")

    cases = [("bad_case", lambda: None)]
    with patch.object(runner, "_run_single", side_effect=failing_run):
        results = runner.run(cases)

    assert len(results) == 1
    assert results[0].status == TestStatus.ERROR
    assert "boom" in results[0].error_message


def test_runner_default_max_workers():
    runner = TestRunner()
    assert runner.max_workers >= 1


def test_runner_run_single_sets_status():
    runner = TestRunner(max_workers=1)
    mock_reconcile_result = MagicMock()
    mock_reconcile_result.status = TestStatus.PASSED
    mock_reconcile_result.duration_seconds = 0.5

    def fake_fn():
        return mock_reconcile_result

    state = runner._run_single("my_case", fake_fn)
    assert state.name == "my_case"
    assert state.status == TestStatus.PASSED


def test_runner_empty_cases_returns_empty():
    runner = TestRunner(max_workers=2)
    results = runner.run([])
    assert results == []
