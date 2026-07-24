"""Tests for /api/selections CRUD, run history, and launch endpoints."""
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
    monkeypatch.setattr("api.routes.selections._execute_run", lambda *a, **k: None)

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
        JobRepository(db).create({
            "name": "bo_job_trigger", "description": "", "tags": [],
            "job_type": "bo_job", "query": "", "key_columns": [],
            "exclude_columns": [], "params": {"object_id": "3001"}, "enabled": True,
        })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _create_selection(client, name="nightly-set", jobs=None):
    resp = client.post("/api/selections", json={
        "name": name, "description": "d", "tags": ["t"],
        "job_sequence": jobs or ["orders_recon"],
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_and_get_selection(client):
    created = _create_selection(client)
    resp = client.get(f"/api/selections/{created['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "nightly-set"
    assert len(data["versions"]) == 1
    assert data["versions"][0]["version_number"] == 1


def test_duplicate_name_rejected(client):
    _create_selection(client)
    resp = client.post("/api/selections", json={"name": "nightly-set", "job_sequence": ["orders_recon"]})
    assert resp.status_code == 409


def test_update_job_sequence_creates_new_version(client):
    created = _create_selection(client)
    resp = client.put(f"/api/selections/{created['id']}", json={"job_sequence": ["orders_recon", "bo_job"]})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["versions"]) == 2
    assert data["versions"][0]["job_sequence"] != data["versions"][1]["job_sequence"]


def test_update_metadata_only_does_not_create_new_version(client):
    created = _create_selection(client)
    resp = client.put(f"/api/selections/{created['id']}", json={"description": "new desc"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["versions"]) == 1
    assert data["description"] == "new desc"


def test_archive_succeeds_with_no_schedules(client):
    created = _create_selection(client)
    resp = client.delete(f"/api/selections/{created['id']}")
    assert resp.status_code == 204


def test_launch_creates_run_with_selection_fields(client):
    created = _create_selection(client)
    resp = client.post(f"/api/selections/{created['id']}/launch", json={"source_env": "dev", "target_env": "qa"})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    runs_resp = client.get(f"/api/selections/{created['id']}/runs")
    assert runs_resp.status_code == 200
    assert [r["run_id"] for r in runs_resp.json()] == [run_id]


def test_launch_single_env_job_type_succeeds_without_target(client):
    created = _create_selection(client, name="bo-only", jobs=["bo_job"])
    resp = client.post(f"/api/selections/{created['id']}/launch", json={"source_env": "dev"})
    assert resp.status_code == 202


def test_launch_bo_job_job_type_succeeds_without_target(client):
    created = _create_selection(client, name="bo-job-only", jobs=["bo_job_trigger"])
    resp = client.post(f"/api/selections/{created['id']}/launch", json={"source_env": "dev"})
    assert resp.status_code == 202


def test_launch_dual_env_job_type_without_target_fails_clearly(client):
    created = _create_selection(client, name="recon-only", jobs=["orders_recon"])
    resp = client.post(f"/api/selections/{created['id']}/launch", json={"source_env": "dev"})
    assert resp.status_code == 422
    assert "orders_recon" in resp.json()["detail"]
    assert "target_env" in resp.json()["detail"]


def test_launch_with_ci_context_stores_it_on_run(client):
    created = _create_selection(client)
    ctx = {"commit_sha": "deadbeef", "pipeline_url": "https://gitlab.example.com/p/9", "ref": "main"}
    resp = client.post(
        f"/api/selections/{created['id']}/launch",
        json={"source_env": "dev", "target_env": "qa", "ci_context": ctx},
    )
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    with _db_module.SessionLocal() as db:
        run = RunRepository(db).get_run(run_id)
        assert run.ci_context == ctx
