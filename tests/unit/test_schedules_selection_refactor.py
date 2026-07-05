"""Tests for schedules referencing Job Selections (pinned version resolution)."""
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
    from etl_framework.repository.repository import TokenRepository, JobRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.services.scheduler.add_job", lambda *a, **k: None)
    monkeypatch.setattr("api.services.scheduler.reload_job", lambda *a, **k: None)

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        JobRepository(db).create({
            "name": "orders_recon", "description": "", "tags": [],
            "job_type": "reconciliation", "query": "SELECT 1", "key_columns": ["id"],
            "exclude_columns": [], "params": {}, "enabled": True,
        })
        JobRepository(db).create({
            "name": "bo_job", "description": "", "tags": [],
            "job_type": "bo_report", "query": "", "key_columns": ["region"],
            "exclude_columns": [], "params": {"report_id": "R1"}, "enabled": True,
        })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _create_selection(client, name="s1"):
    resp = client.post("/api/selections", json={"name": name, "job_sequence": ["orders_recon"]})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_schedule_defaults_to_latest_version(client):
    sel = _create_selection(client)
    resp = client.post("/api/schedules", json={
        "name": "nightly", "cron_expr": "0 6 * * *",
        "selection_id": sel["id"], "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["selection_version"] == 1


def test_create_schedule_missing_selection_returns_404(client):
    resp = client.post("/api/schedules", json={
        "name": "nightly", "cron_expr": "0 6 * * *",
        "selection_id": 9999, "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 404


def test_schedule_stays_pinned_after_selection_is_edited(client):
    sel = _create_selection(client)
    sched_resp = client.post("/api/schedules", json={
        "name": "nightly", "cron_expr": "0 6 * * *",
        "selection_id": sel["id"], "source_env": "dev", "target_env": "prod",
    })
    assert sched_resp.json()["selection_version"] == 1

    client.put(f"/api/selections/{sel['id']}", json={"job_sequence": ["orders_recon", "bo_job"]})

    schedules = client.get("/api/schedules").json()
    assert schedules[0]["selection_version"] == 1


def test_target_env_optional_for_single_env_schedule(client):
    resp_sel = client.post("/api/selections", json={"name": "bo-set", "job_sequence": ["bo_job"]})
    sel_id = resp_sel.json()["id"]
    resp = client.post("/api/schedules", json={
        "name": "bo-nightly", "cron_expr": "0 6 * * *",
        "selection_id": sel_id, "source_env": "dev",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["target_env"] == ""


def test_create_schedule_dual_env_job_without_target_fails_clearly(client):
    resp_sel = client.post("/api/selections", json={"name": "recon-set", "job_sequence": ["orders_recon"]})
    sel_id = resp_sel.json()["id"]
    resp = client.post("/api/schedules", json={
        "name": "recon-nightly", "cron_expr": "0 6 * * *",
        "selection_id": sel_id, "source_env": "dev",
    })
    assert resp.status_code == 422
    assert "orders_recon" in resp.json()["detail"]
    assert "target_env" in resp.json()["detail"]
