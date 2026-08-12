"""Schedules targeting a saved execution sequence instead of a selection."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import JobRepository, TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        for name in ("orders_recon", "load_orders"):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [], "params": {},
                "enabled": True,
            })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _sequence(client, name="nightly"):
    return client.post("/api/sequences", json={
        "name": name,
        "steps": [
            {"step_id": "load", "job_name": "load_orders", "depends_on": []},
            {"step_id": "recon", "job_name": "orders_recon", "depends_on": ["load"]},
        ],
    }).json()["id"]


def _payload(**kw):
    body = {"name": "nightly-sched", "cron_expr": "0 1 * * *",
            "source_env": "dev", "target_env": "prod"}
    body.update(kw)
    return body


def test_schedule_can_target_a_sequence(client):
    seq_id = _sequence(client)
    resp = client.post("/api/schedules", json=_payload(sequence_id=seq_id))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sequence_id"] == seq_id
    assert body["sequence_version"] == 1     # null pins to latest at save time
    assert body["selection_id"] is None


def test_schedule_pins_an_explicit_version(client):
    seq_id = _sequence(client)
    body = client.post("/api/schedules", json=_payload(
        sequence_id=seq_id, sequence_version=1)).json()
    assert body["sequence_version"] == 1


def test_schedule_rejects_both_targets(client):
    seq_id = _sequence(client)
    resp = client.post("/api/schedules", json=_payload(sequence_id=seq_id, selection_id=1))
    assert resp.status_code == 422


def test_schedule_rejects_neither_target(client):
    assert client.post("/api/schedules", json=_payload()).status_code == 422


def test_schedule_rejects_unknown_sequence(client):
    assert client.post("/api/schedules", json=_payload(sequence_id=999)).status_code == 404


def test_schedule_rejects_unknown_sequence_version(client):
    seq_id = _sequence(client)
    resp = client.post("/api/schedules", json=_payload(sequence_id=seq_id, sequence_version=9))
    assert resp.status_code == 404


def test_archiving_a_scheduled_sequence_is_blocked(client):
    seq_id = _sequence(client)
    client.post("/api/schedules", json=_payload(sequence_id=seq_id))
    assert client.delete(f"/api/sequences/{seq_id}").status_code == 409


def test_usage_reports_the_schedule(client):
    seq_id = _sequence(client)
    client.post("/api/schedules", json=_payload(sequence_id=seq_id))
    usage = client.get(f"/api/sequences/{seq_id}/usage").json()
    assert [s["name"] for s in usage["schedules"]] == ["nightly-sched"]
