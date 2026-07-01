from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import RunRepository
from api.services.pytest_runner import PytestRunExecutor


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_run(db: Session, run_id: str = "run-p1") -> None:
    RunRepository(db).create_run(run_id, None, None, run_type="test_suite")


def _executor(db: Session, run_id: str = "run-p1", args: list[str] | None = None) -> PytestRunExecutor:
    return PytestRunExecutor(db=db, run_id=run_id, pytest_args=args or [])


# --- helpers ---

def _fake_process(stdout_lines: list[str], exit_code: int = 0):
    proc = MagicMock()
    proc.stdout = iter(stdout_lines)
    proc.wait.return_value = exit_code
    proc.returncode = exit_code
    return proc


# --- output parsing ---

def test_parses_collected_items():
    db = _session()
    _make_run(db)
    proc = _fake_process(["collected 7 items\n", ""])

    with patch("subprocess.Popen", return_value=proc):
        _executor(db).execute()

    run = RunRepository(db).get_run("run-p1")
    assert run.total_tests == 7


def test_increments_passed_count():
    db = _session()
    _make_run(db)
    lines = [
        "collected 2 items\n",
        "tests/unit/test_foo.py::test_a PASSED   [ 50%]\n",
        "tests/unit/test_foo.py::test_b PASSED   [100%]\n",
        "",
    ]
    with patch("subprocess.Popen", return_value=_fake_process(lines, exit_code=0)):
        _executor(db).execute()

    run = RunRepository(db).get_run("run-p1")
    assert run.passed == 2
    assert run.failed == 0


def test_increments_failed_count():
    db = _session()
    _make_run(db)
    lines = [
        "collected 2 items\n",
        "tests/unit/test_foo.py::test_a PASSED   [ 50%]\n",
        "tests/unit/test_foo.py::test_b FAILED   [100%]\n",
        "",
    ]
    with patch("subprocess.Popen", return_value=_fake_process(lines, exit_code=1)):
        _executor(db).execute()

    run = RunRepository(db).get_run("run-p1")
    assert run.passed == 1
    assert run.failed == 1


def test_increments_error_count():
    db = _session()
    _make_run(db)
    lines = [
        "collected 1 items\n",
        "tests/unit/test_foo.py::test_a ERROR   [100%]\n",
        "",
    ]
    with patch("subprocess.Popen", return_value=_fake_process(lines, exit_code=1)):
        _executor(db).execute()

    run = RunRepository(db).get_run("run-p1")
    assert run.error == 1


# --- terminal status mapping ---

def test_exit_0_sets_passed():
    db = _session()
    _make_run(db)
    with patch("subprocess.Popen", return_value=_fake_process([""])):
        _executor(db).execute()
    assert RunRepository(db).get_run("run-p1").status == "PASSED"


def test_exit_1_sets_completed():
    db = _session()
    _make_run(db)
    with patch("subprocess.Popen", return_value=_fake_process([""], exit_code=1)):
        _executor(db).execute()
    assert RunRepository(db).get_run("run-p1").status == "COMPLETED"


def test_exit_2_sets_error():
    db = _session()
    _make_run(db)
    with patch("subprocess.Popen", return_value=_fake_process([""], exit_code=2)):
        _executor(db).execute()
    assert RunRepository(db).get_run("run-p1").status == "ERROR"


# --- cancellation ---

def test_cancel_terminates_process():
    db = _session()
    _make_run(db)

    lines = [
        "collected 3 items\n",
        "tests/unit/test_foo.py::test_a PASSED   [ 33%]\n",
        "tests/unit/test_foo.py::test_b PASSED   [ 66%]\n",
        "tests/unit/test_foo.py::test_c PASSED   [100%]\n",
        "",
    ]
    proc = _fake_process(lines, exit_code=0)

    call_count = {"n": 0}

    def fake_is_cancel(run_id):
        call_count["n"] += 1
        return call_count["n"] >= 2  # signal cancel after first test line

    with patch("subprocess.Popen", return_value=proc):
        with patch.object(RunRepository, "is_cancel_requested", lambda self, rid: fake_is_cancel(rid)):
            _executor(db).execute()

    proc.terminate.assert_called_once()
    assert RunRepository(db).get_run("run-p1").status == "CANCELLED"
