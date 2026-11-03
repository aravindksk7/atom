"""A step whose job crashes or no longer exists must count against the run."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import RunSettings, SequenceStepRef
from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    JobRepository, RunRepository, RunStepRepository,
)


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
