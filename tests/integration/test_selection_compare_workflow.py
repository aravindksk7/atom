"""End-to-end: launch a selection twice against two environments, then pair
the resulting runs into the existing mismatch-diff compare endpoint."""
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

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def test_launch_twice_and_pair_runs_via_mismatch_diff(client):
    sel = client.post("/api/selections", json={
        "name": "cross-env-set", "job_sequence": ["orders_recon"],
    }).json()

    run_a = client.post(
        f"/api/selections/{sel['id']}/launch",
        json={"source_env": "dev", "target_env": "qa"},
    ).json()["run_id"]
    run_b = client.post(
        f"/api/selections/{sel['id']}/launch",
        json={"source_env": "staging", "target_env": "prod"},
    ).json()["run_id"]

    runs = client.get(f"/api/selections/{sel['id']}/runs").json()
    run_ids = {r["run_id"] for r in runs}
    assert run_ids == {run_a, run_b}

    diff_resp = client.post("/api/compare/mismatch-diff", json={
        "run_id_a": run_a, "run_id_b": run_b,
    })
    assert diff_resp.status_code == 200
    data = diff_resp.json()
    assert "new" in data and "resolved" in data and "persistent" in data
