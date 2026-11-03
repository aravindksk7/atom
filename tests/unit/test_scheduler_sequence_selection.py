"""Schedules that target a Job Selection backed by a saved sequence."""
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
    from api.dependencies import get_session
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.database import get_db
    from etl_framework.repository.repository import JobRepository, TokenRepository

    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(_db_module, "SessionLocal", factory)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session] = override_get_db

    with factory() as db:
        raw, _ = TokenRepository(db).create("test-runner")
        for name in ("load_orders", "orders_recon"):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [], "params": {},
                "enabled": True,
            })

    yield TestClient(app, headers={"Authorization": f"Bearer {raw}"})

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_session, None)


def _selection(client, preconditions=None):
    seq_id = client.post("/api/sequences", json={
        "name": "nightly",
        "steps": [
            {"step_id": "load", "job_name": "load_orders", "depends_on": []},
            {"step_id": "recon", "job_name": "orders_recon", "depends_on": ["load"]},
        ],
        "preconditions": preconditions,
    }).json()["id"]
    resp = client.post("/api/selections", json={
        "name": "sel", "sequence_ref": {"sequence_id": seq_id},
        "run_settings": {"max_workers": 7},
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _schedule(client, selection_id):
    return client.post("/api/schedules", json={
        "name": "sel-nightly", "cron_expr": "0 2 * * *",
        "selection_id": selection_id, "source_env": "dev", "target_env": "prod",
    })


def _disable_job(engine, name):
    from etl_framework.repository.repository import JobRepository

    with Session(engine) as db:
        job = next(j for j in JobRepository(db).list() if j.name == name)
        job.enabled = False
        db.commit()


def _fire(schedule_id):
    from api.services.scheduler import _run_schedule
    _run_schedule(schedule_id, "sel-nightly")


def test_schedule_runs_the_sequence_graph_with_selection_settings(client, monkeypatch):
    executed = []
    monkeypatch.setattr("api.routes.runs._execute_run", lambda **kw: executed.append(kw))

    resp = _schedule(client, _selection(client))
    assert resp.status_code == 201, resp.text
    _fire(resp.json()["id"])

    assert len(executed) == 1
    steps = executed[0]["job_sequence"]
    assert [(s.step_id, s.depends_on) for s in steps] == [("load", []), ("recon", ["load"])]
    assert executed[0]["run_settings"].max_workers == 7
    assert executed[0]["config_snapshot"]["sequence"]["name"] == "nightly"


def test_unresolvable_sequence_records_a_skip(client, engine, monkeypatch):
    executed = []
    monkeypatch.setattr("api.routes.runs._execute_run", lambda **kw: executed.append(kw))

    schedule_id = _schedule(client, _selection(client)).json()["id"]
    _disable_job(engine, "load_orders")
    _fire(schedule_id)

    assert executed == []
    with Session(engine) as db:
        skips = [e for e in db.query(SchedulerTelemetryEvent).all() if e.event_state == "skipped"]
        assert db.query(TestRun).count() == 0
    assert len(skips) == 1
    assert "load_orders" in (skips[0].error_summary or "")


def test_unmet_precondition_records_a_skip(client, engine, monkeypatch):
    executed = []
    monkeypatch.setattr("api.routes.runs._execute_run", lambda **kw: executed.append(kw))

    selection_id = _selection(client, {
        "require_run_success": {"job_name": "never_ran", "within_hours": 6},
    })
    _fire(_schedule(client, selection_id).json()["id"])

    assert executed == []
    with Session(engine) as db:
        skips = [e for e in db.query(SchedulerTelemetryEvent).all() if e.event_state == "skipped"]
    assert len(skips) == 1
    assert "never_ran" in (skips[0].error_summary or "")


def test_saving_validates_the_resolved_steps(client, engine):
    selection_id = _selection(client)
    _disable_job(engine, "load_orders")
    resp = _schedule(client, selection_id)
    assert resp.status_code == 422
    assert "load_orders" in str(resp.json()["detail"])


def test_plain_selection_schedule_is_unchanged(client, monkeypatch):
    executed = []
    monkeypatch.setattr("api.routes.runs._execute_run", lambda **kw: executed.append(kw))

    selection_id = client.post("/api/selections", json={
        "name": "plain", "job_sequence": ["orders_recon"],
    }).json()["id"]
    _fire(_schedule(client, selection_id).json()["id"])

    assert [s.job_name for s in executed[0]["job_sequence"]] == ["orders_recon"]
