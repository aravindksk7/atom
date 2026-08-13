"""Preconditions gate run creation at both entry points."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.models import SchedulerTelemetryEvent, TestRun


@pytest.fixture
def engine():
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def client(monkeypatch, engine):
    from api.main import app
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import JobRepository, TokenRepository

    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.routes.selections._execute_run", lambda *a, **k: None)

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        JobRepository(db).create({
            "name": "orders_recon", "description": "", "tags": [],
            "job_type": "reconciliation", "query": "SELECT 1",
            "key_columns": ["id"], "exclude_columns": [], "params": {},
            "enabled": True,
        })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


STEPS = [{"step_id": "a", "job_name": "orders_recon", "depends_on": []}]


def _sequence_with(client, preconditions, name="gated"):
    resp = client.post("/api/sequences", json={
        "name": name, "steps": STEPS, "preconditions": preconditions,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _selection_for(client, seq_id, name="sel"):
    return client.post("/api/selections", json={
        "name": name, "sequence_ref": {"sequence_id": seq_id},
    }).json()["id"]


def test_launch_is_refused_when_a_gate_fails(client):
    # No job has ever run, so require_run_success cannot be satisfied.
    seq_id = _sequence_with(client, {
        "require_run_success": {"job_name": "never_ran", "within_hours": 6},
    })
    sel_id = _selection_for(client, seq_id)

    resp = client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 422
    assert "never_ran" in resp.json()["detail"]


def test_no_run_row_is_created_when_a_gate_fails(client):
    seq_id = _sequence_with(client, {
        "require_run_success": {"job_name": "never_ran", "within_hours": 6},
    })
    sel_id = _selection_for(client, seq_id)
    client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    })

    assert client.get(f"/api/selections/{sel_id}/runs").json() == []


def test_launch_proceeds_when_every_gate_passes(client):
    seq_id = _sequence_with(client, {"weekdays": [0, 1, 2, 3, 4, 5, 6]})
    sel_id = _selection_for(client, seq_id)

    resp = client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 202, resp.text


def test_a_selection_without_a_sequence_is_unaffected(client):
    sel_id = client.post("/api/selections", json={
        "name": "plain", "job_sequence": ["orders_recon"],
    }).json()["id"]

    resp = client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 202, resp.text


def test_scheduler_records_skipped_and_creates_no_run(client, engine, monkeypatch):
    from etl_framework.repository.repository import RunRepository

    seq_id = _sequence_with(client, {
        "require_run_success": {"job_name": "never_ran", "within_hours": 6},
    })
    sched = client.post("/api/schedules", json={
        "name": "gated-nightly", "cron_expr": "0 2 * * *",
        "sequence_id": seq_id, "source_env": "dev", "target_env": "prod",
    })
    assert sched.status_code == 201, sched.text
    schedule_id = sched.json()["id"]

    executed = []
    monkeypatch.setattr("api.routes.runs._execute_run", lambda *a, **k: executed.append(a))

    from api.services.scheduler import _run_schedule
    _run_schedule(schedule_id, "gated-nightly")

    with Session(engine) as db:
        events = db.query(SchedulerTelemetryEvent).all()
        runs = db.query(TestRun).all()

    assert executed == []
    assert runs == []
    skips = [e for e in events if e.event_state == "skipped"]
    assert len(skips) == 1
    assert "never_ran" in (skips[0].error_summary or "")
