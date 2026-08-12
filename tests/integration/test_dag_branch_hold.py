"""A held branch must not stop an independent branch from finishing."""
from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.schemas import RunSettings, SequenceStepRef
from api.services import run_executor as _re_module
from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base, _ensure_compare_columns
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    JobRepository, RunRepository, RunStepRepository,
)

_re_module.HOLD_POLL_INTERVAL_SECONDS = 0.2


@pytest.fixture()
def engine(tmp_path):
    db_path = tmp_path / "dag.db"
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.close()

    eng = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30}
    )
    Base.metadata.create_all(eng)
    _ensure_compare_columns(eng)
    return eng


def _session(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def _seed_jobs(engine):
    db = _session(engine)
    try:
        for name in ("root", "held", "free"):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [],
                "source_env": None, "target_env": None,
                "params": {
                    "source_rows": [{"id": 1, "amount": 1.0}],
                    "target_rows": [{"id": 1, "amount": 1.0}],
                },
                "enabled": True,
            })
        db.commit()
    finally:
        db.close()


def test_parallel_branches_use_distinct_sqlalchemy_sessions(engine, monkeypatch):
    _seed_jobs(engine)
    db = _session(engine)
    RunRepository(db).create_run("dag-sessions", "dev", "prod", {})
    db.close()

    steps = [
        SequenceStepRef(step_id="root", job_name="root"),
        SequenceStepRef(step_id="left", job_name="held", depends_on=["root"]),
        SequenceStepRef(step_id="right", job_name="free", depends_on=["root"]),
    ]
    seen_sessions = {}
    branches_ready = threading.Barrier(2)
    original = RunExecutor._build_case

    def recording_build_case(self, job):
        case = original(self, job)
        if job.name not in {"held", "free"}:
            return case

        def run_case():
            seen_sessions[job.name] = id(self._db)
            branches_ready.wait(timeout=10)
            return case()

        return run_case

    monkeypatch.setattr(RunExecutor, "_build_case", recording_build_case)
    session = _session(engine)
    try:
        RunExecutor(
            db=session, run_id="dag-sessions", source_env="dev", target_env="prod",
            job_sequence=steps,
            run_settings=RunSettings(metrics_enabled=False, max_workers=2),
        ).execute()
    finally:
        session.close()

    assert set(seen_sessions) == {"held", "free"}
    assert len(set(seen_sessions.values())) == 2


def test_independent_branch_completes_while_another_is_held(engine):
    _seed_jobs(engine)
    db = _session(engine)
    RunRepository(db).create_run("dag-1", "dev", "prod", {})
    db.commit()
    db.close()

    steps = [
        SequenceStepRef(step_id="root", job_name="root"),
        SequenceStepRef(step_id="held", job_name="held", depends_on=["root"], hold_after=True),
        SequenceStepRef(step_id="free", job_name="free", depends_on=["root"]),
    ]

    def _run():
        session = _session(engine)
        try:
            RunExecutor(
                db=session, run_id="dag-1", source_env="dev", target_env="prod",
                job_sequence=steps,
                run_settings=RunSettings(metrics_enabled=False, max_workers=4),
            ).execute()
        finally:
            session.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    # The free branch must finish while 'held' is still waiting on a human.
    deadline = time.monotonic() + 20
    rows = {}
    while time.monotonic() < deadline:
        probe = _session(engine)
        try:
            rows = {s.step_id: s.status for s in RunStepRepository(probe).list_steps("dag-1")}
        finally:
            probe.close()
        if rows.get("free") == "PASSED" and rows.get("held") == "HELD":
            break
        time.sleep(0.2)
    else:
        pytest.fail(f"free branch never completed while held; last saw {rows}")

    # Release the hold and let the run finish.
    releaser = _session(engine)
    try:
        row = RunStepRepository(releaser).get_step_by_step_id("dag-1", "held")
        RunStepRepository(releaser).release_step("dag-1", row.step_index, "approve", "ok", "alice")
    finally:
        releaser.close()

    thread.join(timeout=20)
    assert not thread.is_alive()

    final = _session(engine)
    try:
        run = RunRepository(final).get_run("dag-1")
        rows = {s.step_id: s.status for s in RunStepRepository(final).list_steps("dag-1")}
    finally:
        final.close()

    assert rows["free"] == "PASSED"
    assert rows["held"] == "APPROVED"
    assert run.status in {"PASSED", "SLOW"}
