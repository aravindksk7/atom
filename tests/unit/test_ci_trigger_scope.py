"""End-to-end: a role=ci_trigger token can drive the real launch/poll/artifact flow the
atom CLI and CI scripts use, and gets 403 on anything outside that surface.

Unlike test_auth.py's allow/deny table (which checks route-by-route in isolation), this
exercises one full, real Job Selection launch through a ci_trigger token, the same way
scripts/ci/run-atom-target.sh actually would.
"""
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
    from etl_framework.repository.repository import JobRepository, JobSelectionRepository, TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.routes.selections._execute_run", lambda *a, **k: None)

    with Session(engine) as db:
        admin_raw, _ = TokenRepository(db).create("admin", is_admin=True)
        ci_raw, _ = TokenRepository(db).create("ci-bot", role="ci_trigger")
        JobRepository(db).create({
            "name": "orders_recon", "description": "", "tags": [],
            "job_type": "reconciliation", "query": "SELECT 1", "key_columns": ["id"],
            "exclude_columns": [], "params": {}, "enabled": True,
        })
        selection = JobSelectionRepository(db).create(
            name="nightly", description="", tags=[], job_sequence=["orders_recon"],
            run_settings={},
        )
        selection_id = selection.id

    return {
        "client": TestClient(app),
        "admin_raw": admin_raw,
        "ci_raw": ci_raw,
        "selection_id": selection_id,
    }


def test_ci_trigger_token_drives_real_launch_and_artifact_fetch(client):
    c = client["client"]
    headers = {"Authorization": f"Bearer {client['ci_raw']}"}

    # orders_recon is job_type "reconciliation", which is dual-env (not in
    # SINGLE_ENV_JOB_TYPES), so validate_env_requirements requires target_env
    # here or the launch 422s before ever reaching the ci_trigger scope check.
    launch_resp = c.post(
        f"/api/selections/{client['selection_id']}/launch",
        json={"source_env": "dev", "target_env": "dev"}, headers=headers,
    )
    assert launch_resp.status_code == 202, launch_resp.text
    run_id = launch_resp.json()["run_id"]

    status_resp = c.get(f"/api/runs/{run_id}/status", headers=headers)
    assert status_resp.status_code == 200

    junit_resp = c.get(f"/api/runs/{run_id}/junit", headers=headers)
    assert junit_resp.status_code == 200


def test_ci_trigger_token_still_denied_on_configs(client):
    c = client["client"]
    resp = c.get("/api/configs", headers={"Authorization": f"Bearer {client['ci_raw']}"})
    assert resp.status_code == 403
