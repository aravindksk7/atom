"""API tests for run timing, sequence history and the stream."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import SequenceStepRef

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(engine, monkeypatch):
    from api.main import app
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import TokenRepository

    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _blocked_run(engine, run_id="blocked-run"):
    from etl_framework.repository.repository import RunRepository, RunStepRepository

    with Session(engine) as db:
        RunRepository(db).create_run(run_id, "dev", "prod", {})
        RunStepRepository(db).materialize_steps(run_id, [
            SequenceStepRef(step_id="a", job_name="job_a"),
        ])
        RunStepRepository(db).set_status_by_step_id(run_id, "a", "BLOCKED")
        RunRepository(db).update_run_status(
            run_id, "BLOCKED", started_at=T0, completed_at=T0 + timedelta(seconds=30),
        )


def test_blocked_run_ends_the_stream(client, engine):
    import threading

    _blocked_run(engine)
    box: dict = {}

    def read_stream():
        with client.stream("GET", "/api/runs/blocked-run/stream") as response:
            box["body"] = response.read().decode()

    # The stream loops forever if BLOCKED is not terminal, so bound the read.
    thread = threading.Thread(target=read_stream, daemon=True)
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive(), "stream never ended for a BLOCKED run"
    done = box["body"].split("event: done", 1)[1]
    done_payload = json.loads(next(l for l in done.splitlines() if l.startswith("data: "))[len("data: "):])
    assert done_payload["status"] == "BLOCKED"


def test_status_endpoint_reports_blocked_when_the_finished_steps_passed(client, engine):
    from etl_framework.repository.models import TestResult

    _blocked_run(engine, "blocked-with-pass")
    with Session(engine) as db:
        db.add(TestResult(run_id="blocked-with-pass", query_name="q_a", status="PASSED"))
        db.commit()

    response = client.get("/api/runs/blocked-with-pass/status")

    assert response.status_code == 200
    assert response.json()["status"] == "BLOCKED"


def _live_run(engine):
    from etl_framework.repository.repository import RunRepository, RunStepRepository

    with Session(engine) as db:
        RunRepository(db).create_run("live", "dev", "prod", {"sequence": {"id": 7, "name": "s"}})
        RunStepRepository(db).materialize_steps("live", [
            SequenceStepRef(step_id="a", job_name="job_a"),
            SequenceStepRef(step_id="b", job_name="job_b", depends_on=["a"]),
        ])
        RunStepRepository(db).set_status_by_step_id("live", "a", "RUNNING")
        RunRepository(db).update_run_status("live", "RUNNING", started_at=T0)


def test_timing_returns_the_document(client, engine):
    _live_run(engine)
    body = client.get("/api/runs/live/timing").json()
    assert body["run_id"] == "live"
    assert body["jobs_total"] == 2
    assert body["jobs_done"] == 0
    assert body["elapsed_seconds"] > 0
    assert body["steps"][0]["progress"]["state"] == "running_unknown"
    assert body["steps"][1]["progress"] == {"state": "queued", "percent": 0, "label": "queued"}
    assert body["steps"][0]["job_family"] == "other"


def test_timing_unknown_run_is_404(client):
    assert client.get("/api/runs/nope/timing").status_code == 404


def test_timing_of_run_without_steps_has_empty_steps(client, engine):
    from etl_framework.repository.repository import RunRepository

    with Session(engine) as db:
        RunRepository(db).create_run("suite", "dev", "prod", {})
    body = client.get("/api/runs/suite/timing").json()
    assert body["steps"] == []
    assert body["elapsed_seconds"] is None


def _read_stream(client, run_id, timeout=30):
    import threading

    box: dict = {}

    def read():
        with client.stream("GET", f"/api/runs/{run_id}/stream") as response:
            box["body"] = response.read().decode()

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    assert not thread.is_alive(), "stream did not end"
    return box["body"]


def _wait_for(predicate, timeout=20.0):
    """Poll for a real signal from the stream instead of sleeping a fixed time."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _events(body):
    out = []
    for block in body.split("\n\n"):
        lines = block.strip().splitlines()
        if len(lines) == 2 and lines[0].startswith("event: "):
            out.append((lines[0][7:], json.loads(lines[1][len("data: "):])))
    return out


def test_stream_includes_timing_and_keeps_existing_keys(client, engine):
    _blocked_run(engine, "with-timing")
    events = _events(_read_stream(client, "with-timing"))
    payload = events[0][1]
    assert payload["timing"]["elapsed_seconds"] == 30.0
    assert payload["steps"][0]["step_id"] == "a"
    assert "percent_complete" in payload


@pytest.mark.parametrize("status", ["PASSED", "FAILED", "SLOW", "ERROR", "COMPLETED", "CANCELLED", "BLOCKED"])
def test_done_fires_for_every_terminal_status_with_final_timing(client, engine, status):
    from etl_framework.repository.repository import RunRepository, RunStepRepository

    with Session(engine) as db:
        RunRepository(db).create_run("t", "dev", "prod", {})
        RunStepRepository(db).materialize_steps("t", [SequenceStepRef(step_id="a", job_name="job_a")])
        RunRepository(db).update_run_status(
            "t", status, started_at=T0, completed_at=T0 + timedelta(seconds=12),
        )
    done = [p for name, p in _events(_read_stream(client, "t")) if name == "done"]
    assert len(done) == 1
    assert done[0]["status"] == status
    assert done[0]["timing"]["status"] == status
    assert done[0]["timing"]["elapsed_seconds"] == 12.0


def test_stream_survives_a_timing_failure(client, engine, monkeypatch):
    from api.routes import runs as runs_module

    def boom(*args, **kwargs):
        raise RuntimeError("no timing")

    monkeypatch.setattr(runs_module, "load_timing_context", boom)
    _blocked_run(engine, "no-timing")
    body = _read_stream(client, "no-timing")
    assert '"timing": null' in body
    assert "event: done" in body


def test_stream_survives_a_timing_build_failure(client, engine, monkeypatch):
    from api.routes import runs as runs_module

    def boom(*args, **kwargs):
        raise RuntimeError("no timing")

    monkeypatch.setattr(runs_module, "timing_for_run", boom)
    _blocked_run(engine, "no-build")
    body = _read_stream(client, "no-build")
    assert '"timing": null' in body
    assert "event: done" in body


def test_live_stream_ticks_and_loads_the_context_once(client, engine, monkeypatch):
    import threading

    from api.routes import runs as runs_module
    from etl_framework.repository.repository import RunRepository

    calls = []
    ticks = []
    real = runs_module.load_timing_context
    real_timing = runs_module.timing_for_run

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    def counting_timing(*args, **kwargs):
        ticks.append(1)
        return real_timing(*args, **kwargs)

    monkeypatch.setattr(runs_module, "load_timing_context", counting)
    monkeypatch.setattr(runs_module, "timing_for_run", counting_timing)
    _live_run(engine)

    def finish_later():
        _wait_for(lambda: len(ticks) >= 3)
        with Session(engine) as db:
            RunRepository(db).update_run_status(
                "live", "PASSED", completed_at=datetime.now(timezone.utc),
            )

    threading.Thread(target=finish_later, daemon=True).start()
    events = _events(_read_stream(client, "live"))
    progress = [p for name, p in events if name == "progress"]
    # server_time moves every second, so an idle live run still emits.
    assert len({p["timing"]["server_time"] for p in progress}) >= 2
    assert events[-1][0] == "done"
    assert events[-1][1]["status"] == "PASSED"
    assert len(calls) == 1


def test_stream_holds_no_transaction_while_it_sleeps(client, engine, monkeypatch):
    """An open read transaction across the sleep pins a pooled connection: on
    Postgres, 16 streams exhausted the pool and /api/health took 29 s."""
    import asyncio
    import threading

    from api.routes import runs as runs_module
    from etl_framework.repository.repository import RunRepository, RunStepRepository

    timeline = []
    sessions = []
    real_rollback = Session.rollback
    real_sleep = asyncio.sleep
    real_expire_all = Session.expire_all

    def spy_rollback(self):
        timeline.append("rollback")
        return real_rollback(self)

    def spy_expire_all(self):
        # Only the stream's own session calls this, once at the top of each tick.
        if self not in sessions:
            sessions.append(self)
        return real_expire_all(self)

    async def spy_sleep(seconds, *args, **kwargs):
        # The stream's own sleep is the 1 second one; leave anyio's alone.
        if seconds == 1:
            timeline.append(("sleep", [s.in_transaction() for s in sessions]))
        return await real_sleep(seconds, *args, **kwargs)

    monkeypatch.setattr(Session, "rollback", spy_rollback)
    monkeypatch.setattr(Session, "expire_all", spy_expire_all)
    monkeypatch.setattr(runs_module.asyncio, "sleep", spy_sleep)
    _live_run(engine)

    def sleeps():
        return [i for i, entry in enumerate(timeline) if isinstance(entry, tuple)]

    def change_a_step_then_finish():
        _wait_for(lambda: len(sleeps()) >= 2)
        with Session(engine) as db:
            RunStepRepository(db).set_status_by_step_id("live", "a", "PASSED")
        seen = len(sleeps())
        _wait_for(lambda: len(sleeps()) >= seen + 2)
        with Session(engine) as db:
            RunRepository(db).update_run_status("live", "PASSED", completed_at=datetime.now(timezone.utc))

    threading.Thread(target=change_a_step_then_finish, daemon=True).start()
    events = _events(_read_stream(client, "live"))

    indexes = sleeps()
    assert len(indexes) >= 3
    for index in indexes:
        assert timeline[index - 1] == "rollback", "the rollback must come right before the sleep"
        assert sessions and not any(timeline[index][1]), "a transaction stayed open across the sleep"

    # Ending the transaction must not stop later ticks from seeing new data.
    statuses = [p["steps"][0]["status"] for name, p in events if name == "progress"]
    assert statuses[0] == "RUNNING"
    assert "PASSED" in statuses
    assert events[-1][0] == "done"
    assert events[-1][1]["status"] == "PASSED"


def _sequence(engine):
    from etl_framework.repository.models import ExecutionSequence

    with Session(engine) as db:
        sequence = ExecutionSequence(name="timing-seq")
        db.add(sequence)
        db.commit()
        return sequence.id


def test_sequence_runs_are_newest_first_with_elapsed(client, engine):
    from etl_framework.repository.repository import RunRepository

    sequence_id = _sequence(engine)
    with Session(engine) as db:
        repo = RunRepository(db)
        repo.create_run("old", "dev", "prod", {"sequence": {"id": sequence_id}})
        repo.update_run_status("old", "PASSED", started_at=T0, completed_at=T0 + timedelta(seconds=60))
        repo.create_run("new", "dev", "prod", {"sequence": {"id": sequence_id}})
        repo.update_run_status("new", "FAILED", started_at=T0, completed_at=T0 + timedelta(seconds=90))
        repo.create_run("elsewhere", "dev", "prod", {"sequence": {"id": sequence_id + 1}})

    rows = client.get(f"/api/sequences/{sequence_id}/runs").json()
    assert [r["run_id"] for r in rows] == ["new", "old"]
    assert rows[0]["elapsed_seconds"] == 90.0
    assert rows[0]["status"] == "FAILED"
    assert rows[1]["active_seconds"] == 60.0

    paged = client.get(f"/api/sequences/{sequence_id}/runs?limit=1&offset=1").json()
    assert [r["run_id"] for r in paged] == ["old"]


def test_sequence_runs_rejects_bad_limit_and_unknown_sequence(client, engine):
    sequence_id = _sequence(engine)
    assert client.get(f"/api/sequences/{sequence_id}/runs?limit=201").status_code == 422
    assert client.get("/api/sequences/999999/runs").status_code == 404


def test_stream_reloads_the_timing_context_when_steps_appear(client, engine, monkeypatch):
    import threading

    from api.routes import runs as runs_module
    from etl_framework.repository.models import SavedJob, TestResult, TestRun
    from etl_framework.repository.repository import RunRepository, RunStepRepository

    with Session(engine) as db:
        db.add(SavedJob(name="job_a", job_type="bo_report"))
        db.add(TestRun(run_id="history", status="PASSED"))
        for _ in range(3):
            db.add(TestResult(run_id="history", query_name="job_a", status="PASSED", duration_seconds=40.0))
        db.commit()
        RunRepository(db).create_run("late", "dev", "prod", {})

    calls = []
    ticks = []
    real = runs_module.load_timing_context
    real_timing = runs_module.timing_for_run

    def counting(db, names):
        calls.append(sorted(names))
        return real(db, names)

    def counting_timing(*args, **kwargs):
        ticks.append(1)
        return real_timing(*args, **kwargs)

    monkeypatch.setattr(runs_module, "load_timing_context", counting)
    monkeypatch.setattr(runs_module, "timing_for_run", counting_timing)

    def materialize_later():
        _wait_for(lambda: len(ticks) >= 1)  # the stream is open and has seen no steps
        with Session(engine) as db:
            RunStepRepository(db).materialize_steps("late", [SequenceStepRef(step_id="a", job_name="job_a")])
            RunStepRepository(db).set_status_by_step_id("late", "a", "RUNNING")
            RunRepository(db).update_run_status("late", "RUNNING", started_at=T0)
        seen = len(ticks)
        _wait_for(lambda: len(calls) >= 2 and len(ticks) >= seen + 2)
        with Session(engine) as db:
            RunRepository(db).update_run_status("late", "PASSED", completed_at=datetime.now(timezone.utc))

    threading.Thread(target=materialize_later, daemon=True).start()
    events = _events(_read_stream(client, "late"))
    steps = [p["timing"]["steps"][0] for _, p in events if p["timing"] and p["timing"]["steps"]]
    assert steps, "no payload carried a step"
    assert steps[-1]["job_type"] == "bo_report"
    assert steps[-1]["job_family"] == "sap"
    assert steps[-1]["estimate_seconds"] == 40.0
    assert calls == [[], ["job_a"]]


def _count_rollbacks(monkeypatch):
    rollbacks = []
    real = Session.rollback

    def spy(self):
        rollbacks.append(1)
        return real(self)

    monkeypatch.setattr(Session, "rollback", spy)
    return rollbacks


def test_context_load_failure_rolls_back_and_the_stream_keeps_going(client, engine, monkeypatch):
    from api.routes import runs as runs_module

    rollbacks = _count_rollbacks(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("no context")

    monkeypatch.setattr(runs_module, "load_timing_context", boom)
    _blocked_run(engine, "rb-load")
    events = _events(_read_stream(client, "rb-load"))
    assert rollbacks, "the failed load left the transaction open"
    assert events[-1][0] == "done"
    assert events[-1][1]["timing"] is None


def test_timing_build_failure_rolls_back_and_the_stream_keeps_going(client, engine, monkeypatch):
    from api.routes import runs as runs_module

    rollbacks = _count_rollbacks(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("no timing")

    monkeypatch.setattr(runs_module, "timing_for_run", boom)
    _blocked_run(engine, "rb-build")
    events = _events(_read_stream(client, "rb-build"))
    assert rollbacks, "the failed build left the transaction open"
    assert events[-1][0] == "done"
    assert events[-1][1]["timing"] is None


def test_sequence_runs_query_count_does_not_grow_with_the_run_count(client, engine):
    from sqlalchemy import event

    from etl_framework.repository.repository import RunRepository, RunStepRepository

    sequence_id = _sequence(engine)

    def add_runs(prefix, count):
        with Session(engine) as db:
            for i in range(count):
                run_id = f"{prefix}{i}"
                RunRepository(db).create_run(run_id, "dev", "prod", {"sequence": {"id": sequence_id}})
                RunStepRepository(db).materialize_steps(run_id, [
                    SequenceStepRef(step_id=f"s{n}", job_name=f"j{n}") for n in range(3)
                ])
                RunRepository(db).update_run_status(
                    run_id, "PASSED", started_at=T0, completed_at=T0 + timedelta(seconds=10),
                )

    def statements():
        seen = []

        def listener(conn, cursor, statement, *args):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", listener)
        try:
            assert client.get(f"/api/sequences/{sequence_id}/runs").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        return len(seen)

    add_runs("a", 1)
    statements()  # warm-up: the first request does one-off token and schema work
    few = statements()
    add_runs("b", 4)
    many = statements()
    assert many == few
