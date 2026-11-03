"""A step whose job crashes or no longer exists must count against the run."""
from __future__ import annotations

import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import RunSettings, SequenceStepRef
from api.services.dag_executor import StepOutcome
from api.services.run_executor import RunExecutor, _StepRepoAdapter
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    JobRepository, RunRepository, RunStepRepository,
)
from etl_framework.runner.state import TestCaseState, TestStatus


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_job(db, name):
    JobRepository(db).create({
        "name": name, "description": name, "tags": [],
        "job_type": "reconciliation", "query": f"SELECT * FROM {name}",
        "key_columns": ["id"], "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"source_rows": [{"id": 1}], "target_rows": [{"id": 1}]},
        "enabled": True,
    })


def _run(db, run_id, steps):
    RunRepository(db).create_run(run_id, "dev", "prod", {})
    RunExecutor(
        db=db, run_id=run_id, source_env="dev", target_env="prod",
        job_sequence=steps, run_settings=RunSettings(metrics_enabled=False),
    ).execute()
    return RunRepository(db).get_run(run_id)


def test_missing_job_makes_the_run_error():
    db = _session()
    run = _run(db, "run-ghost", [SequenceStepRef(step_id="ghost", job_name="ghost")])

    assert run.status == "ERROR"
    assert run.error == 1
    assert "ghost" in run.results[0].error_message
    assert RunStepRepository(db).list_steps("run-ghost")[0].status == "ERROR"


def test_exception_while_building_a_case_makes_the_run_error(monkeypatch):
    db = _session()
    _make_job(db, "job_a")

    def explode(self, job):
        raise RuntimeError("cannot build")

    monkeypatch.setattr(RunExecutor, "_build_case", explode)
    run = _run(db, "run-build", [SequenceStepRef(step_id="a", job_name="job_a")])

    assert run.status == "ERROR"
    assert run.error == 1
    assert "cannot build" in run.results[0].error_message


def test_persistence_failure_still_counts_as_an_error(monkeypatch):
    db = _session()
    _make_job(db, "job_a")

    def explode(self, states):
        raise RuntimeError("database went away")

    monkeypatch.setattr(RunExecutor, "_persist_states", explode)
    run = _run(db, "run-persist", [SequenceStepRef(step_id="a", job_name="job_a")])

    assert run.status == "ERROR"
    assert RunStepRepository(db).list_steps("run-persist")[0].status == "ERROR"


def test_crash_outside_step_handling_makes_the_run_error(monkeypatch):
    db = _session()
    _make_job(db, "job_a")

    def explode(self, step, jobs_index):
        raise RuntimeError("coordinator backstop")

    monkeypatch.setattr(RunExecutor, "_run_dag_step", explode)
    run = _run(db, "run-backstop", [SequenceStepRef(step_id="a", job_name="job_a")])

    assert run.status == "ERROR"
    assert RunStepRepository(db).list_steps("run-backstop")[0].status == "ERROR"


def _steps_that_wait_for(event, statuses):
    """A _run_dag_step stand-in: every step stays in flight until the event is set."""
    def run_step(self, step, jobs_index):
        assert event.wait(timeout=5)
        status = statuses[step.step_id]
        state = TestCaseState(name=step.job_name, test_type="reconciliation", status=TestStatus(status))
        return StepOutcome(status=status, state=state)
    return run_step


def test_cancel_poll_failure_leaves_in_flight_steps_their_real_status(monkeypatch):
    db = _session()
    monkeypatch.setattr("api.services.run_executor.HOLD_POLL_INTERVAL_SECONDS", 0.01)
    raised = threading.Event()
    real_poll = RunRepository.is_cancel_requested
    calls = []

    def poll_that_fails_once(self, run_id):
        calls.append(run_id)
        # The first call comes before anything is submitted, so the second one
        # is the first with both steps in flight.
        if len(calls) == 2:
            raised.set()
            raise RuntimeError("database went away")
        return real_poll(self, run_id)

    monkeypatch.setattr(RunRepository, "is_cancel_requested", poll_that_fails_once)
    monkeypatch.setattr(
        RunExecutor, "_run_dag_step", _steps_that_wait_for(raised, {"a": "PASSED", "b": "FAILED"}),
    )
    run = _run(db, "run-poll", [
        SequenceStepRef(step_id="a", job_name="job_a"),
        SequenceStepRef(step_id="b", job_name="job_b"),
    ])

    assert {s.step_id: s.status for s in RunStepRepository(db).list_steps("run-poll")} == {
        "a": "PASSED", "b": "FAILED",
    }
    assert run.status == "FAILED"
    assert (run.passed, run.failed, run.error) == (1, 1, 0)


def test_hold_poll_failure_answers_no_release_yet():
    class BrokenRepo:
        def get_release_by_step_id(self, run_id, step_id):
            raise RuntimeError("database went away")

    rollbacks = []
    lock = threading.Lock()
    adapter = _StepRepoAdapter(BrokenRepo(), "run", lock, rollback=lambda: rollbacks.append(True))

    assert adapter.get_release("step") is None
    assert rollbacks == [True]
    assert not lock.locked()


def test_a_failed_rollback_does_not_escape_the_poll():
    class BrokenRepo:
        def get_release_by_step_id(self, run_id, step_id):
            raise RuntimeError("database went away")

    def broken_rollback():
        raise RuntimeError("still gone")

    adapter = _StepRepoAdapter(BrokenRepo(), "run", rollback=broken_rollback)
    assert adapter.get_release("step") is None


def test_coordinator_session_polls_hold_the_lock(monkeypatch):
    db = _session()
    executor = RunExecutor(
        db=db, run_id="any", source_env="dev", target_env="prod",
        job_sequence=[], run_settings=RunSettings(metrics_enabled=False),
    )
    captured = {}
    monkeypatch.setattr("api.services.dag_executor.DagExecutor", lambda **kw: captured.update(kw))
    executor._build_dag_executor(RunStepRepository(db), {})

    locked = []
    monkeypatch.setattr(
        executor._run_repo, "is_cancel_requested",
        lambda run_id: locked.append(executor._db_lock.locked()) or False,
    )
    monkeypatch.setattr(db, "expire_all", lambda: locked.append(executor._db_lock.locked()))

    assert captured["is_cancel_requested"]() is False
    captured["expire_all"]()

    assert locked == [True, True]
    assert not executor._db_lock.locked()


def test_crash_with_on_failure_stop_makes_the_run_error_not_cancelled(monkeypatch):
    db = _session()
    _make_job(db, "job_a")

    def explode(self, step, jobs_index):
        raise RuntimeError("coordinator backstop")

    monkeypatch.setattr(RunExecutor, "_run_dag_step", explode)
    run = _run(db, "run-stop", [
        SequenceStepRef(step_id="a", job_name="job_a", on_failure="stop"),
        SequenceStepRef(step_id="b", job_name="job_a", depends_on=["a"]),
    ])

    assert run.status == "ERROR"
    assert run.error == 1
    assert {s.step_id: s.status for s in RunStepRepository(db).list_steps("run-stop")} == {
        "a": "ERROR", "b": "CANCELLED",
    }
