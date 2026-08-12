"""End-to-end workflow: build a sequence, attach it, run it."""
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
    monkeypatch.setattr("api.routes.selections._execute_run", lambda *a, **k: None)

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        for name in ("extract", "load", "verify"):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [], "params": {},
                "enabled": True,
            })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


DIAMOND = [
    {"step_id": "extract", "job_name": "extract", "depends_on": []},
    {"step_id": "load_b", "job_name": "load", "depends_on": ["extract"]},
    {"step_id": "load_a", "job_name": "load", "depends_on": ["extract"]},
    {"step_id": "verify", "job_name": "verify", "depends_on": ["load_a", "load_b"]},
]


def test_build_attach_and_launch(client):
    # 1. Validate before saving.
    check = client.post("/api/sequences/validate", json={"steps": DIAMOND}).json()
    assert check["ok"] is True
    assert check["order"] == ["extract", "load_b", "load_a", "verify"]

    # 2. Save it.
    seq_id = client.post("/api/sequences", json={"name": "etl", "steps": DIAMOND}).json()["id"]

    # 3. Attach to a selection and launch.
    sel_id = client.post("/api/selections", json={
        "name": "etl-sel", "sequence_ref": {"sequence_id": seq_id},
    }).json()["id"]
    run_id = client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    }).json()["run_id"]

    snapshot = client.get(f"/api/runs/{run_id}").json()["config_snapshot"]
    assert [s["job_name"] for s in snapshot["job_sequence"]] == ["extract", "load", "load", "verify"]
    assert snapshot["sequence"]["name"] == "etl"

    # 4. Attach to a schedule too.
    sched = client.post("/api/schedules", json={
        "name": "etl-nightly", "cron_expr": "0 2 * * *",
        "sequence_id": seq_id, "source_env": "dev", "target_env": "prod",
    })
    assert sched.status_code == 201

    # 5. Usage reports both consumers.
    usage = client.get(f"/api/sequences/{seq_id}/usage").json()
    assert [s["name"] for s in usage["selections"]] == ["etl-sel"]
    assert [s["name"] for s in usage["schedules"]] == ["etl-nightly"]


def test_new_version_does_not_disturb_a_pinned_schedule(client):
    seq_id = client.post("/api/sequences", json={"name": "etl", "steps": DIAMOND}).json()["id"]
    client.post("/api/schedules", json={
        "name": "etl-nightly", "cron_expr": "0 2 * * *",
        "sequence_id": seq_id, "source_env": "dev", "target_env": "prod",
    })
    client.post(f"/api/sequences/{seq_id}/versions", json={
        "steps": [{"step_id": "solo", "job_name": "verify", "depends_on": []}],
    })
    schedules = client.get("/api/schedules").json()
    assert schedules[0]["sequence_version"] == 1


def test_disabling_a_job_breaks_resolution_with_a_clear_error(client):
    seq_id = client.post("/api/sequences", json={"name": "etl", "steps": DIAMOND}).json()["id"]
    sel_id = client.post("/api/selections", json={
        "name": "etl-sel", "sequence_ref": {"sequence_id": seq_id},
    }).json()["id"]

    jobs = client.get("/api/jobs").json()
    load = next(j for j in jobs if j["name"] == "load")
    client.put(f"/api/jobs/{load['name']}", json={**load, "enabled": False})

    resp = client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 422
    assert "load" in resp.json()["detail"]
