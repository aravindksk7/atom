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
        JobRepository(db).create({
            "name": "ds_job_trigger", "description": "", "tags": [],
            "job_type": "ds_job", "query": "", "key_columns": [],
            "exclude_columns": [], "params": {"job_name": "DS_NIGHTLY_LOAD"}, "enabled": True,
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


def test_create_selection_persists_use_live_connections(client):
    """Regression test: the create/edit modal never sent run_settings at all,
    so every job selection was silently pinned to use_live_connections=False
    forever -- with no UI control to change it. A bo_report/automic_job added
    to a selection could then never run (guarded ValueError, or before that
    fix, a pandas IndexError), even though the identical job runs fine from
    the Launch tab's Jobs sub-tab where the live-connections toggle exists."""
    resp = client.post("/api/selections", json={
        "name": "live-set", "job_sequence": ["bo_job"],
        "run_settings": {"use_live_connections": True},
    })
    assert resp.status_code == 201, resp.text
    selection_id = resp.json()["id"]

    detail = client.get(f"/api/selections/{selection_id}").json()
    assert detail["versions"][-1]["run_settings"]["use_live_connections"] is True


def test_update_run_settings_creates_new_version_with_live_connections(client):
    created = _create_selection(client, jobs=["bo_job"])
    resp = client.put(f"/api/selections/{created['id']}", json={
        "run_settings": {"use_live_connections": True},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["versions"][-1]["run_settings"]["use_live_connections"] is True


def test_update_can_clear_config_id_explicitly(client):
    """Regression test: the edit modal always sends config_id (int or null)
    alongside run_settings, so PUT with config_id: null must clear the saved
    config -- not silently carry the previous version's config_id forward,
    which is what a bare `body.config_id is None` check would do."""
    from etl_framework.repository.repository import ConfigRepository
    from etl_framework.repository.database import SessionLocal

    with SessionLocal() as db:
        cfg_id = ConfigRepository(db).create("bo-prod", "prod", {"bo_url": "x"}).id

    created = client.post("/api/selections", json={
        "name": "with-config", "job_sequence": ["bo_job"],
        "run_settings": {"use_live_connections": True}, "config_id": cfg_id,
    }).json()

    resp = client.put(f"/api/selections/{created['id']}", json={
        "run_settings": {"use_live_connections": False}, "config_id": None,
    })
    assert resp.status_code == 200
    assert resp.json()["versions"][-1]["config_id"] is None


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


def test_launch_uses_config_saved_on_the_selection(client, monkeypatch):
    """Regression test: the Launch Job Selection modal had no config picker,
    so launching a bo_report job always sent an empty config_id/config_data,
    which produced an empty bo_credentials dict and blew up EnvironmentConfig
    validation (missing db_host/db_password) deep inside RunExecutor. The
    config should be picked once when the selection is created/edited and
    reused on every launch, not re-supplied at launch time."""
    from etl_framework.repository.repository import ConfigRepository
    from etl_framework.repository.database import SessionLocal

    with SessionLocal() as db:
        cfg = ConfigRepository(db).create(
            "bo-prod", "prod",
            {"bo_url": "https://bo.example.com", "bo_user": "admin", "bo_password": "secret", "db_host": "bo-host"},
        )
        cfg_id = cfg.id

    resp = client.post("/api/selections", json={
        "name": "bo-live-set", "job_sequence": ["bo_job"],
        "run_settings": {"use_live_connections": True},
        "config_id": cfg_id,
    })
    assert resp.status_code == 201, resp.text
    selection_id = resp.json()["id"]

    detail = client.get(f"/api/selections/{selection_id}").json()
    assert detail["versions"][-1]["config_id"] == cfg_id

    captured = {}
    monkeypatch.setattr(
        "api.routes.selections._execute_run",
        lambda run_id, job_sequence, source_env, target_env, run_settings, config_snapshot: captured.update(
            config_snapshot=config_snapshot
        ),
    )

    resp = client.post(f"/api/selections/{selection_id}/launch", json={"source_env": "dev"})
    assert resp.status_code == 202, resp.text

    assert captured["config_snapshot"]["bo_credentials"]["db_host"] == "bo-host"
    assert captured["config_snapshot"]["bo_credentials"]["bo_password"] == "secret"


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


def test_launch_ds_job_job_type_succeeds_without_target(client):
    created = _create_selection(client, name="ds-job-only", jobs=["ds_job_trigger"])
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
