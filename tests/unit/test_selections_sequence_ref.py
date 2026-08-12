"""Job Selections referencing a saved execution sequence."""
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
        for name in ("orders_recon", "load_orders"):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [], "params": {},
                "enabled": True,
            })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _sequence(client):
    return client.post("/api/sequences", json={
        "name": "nightly",
        "steps": [
            {"step_id": "load", "job_name": "load_orders", "depends_on": []},
            {"step_id": "recon", "job_name": "orders_recon", "depends_on": ["load"]},
        ],
    }).json()["id"]


def test_selection_can_reference_a_sequence(client):
    seq_id = _sequence(client)
    resp = client.post("/api/selections", json={
        "name": "sel", "sequence_ref": {"sequence_id": seq_id},
    })
    assert resp.status_code == 201, resp.text
    detail = client.get(f"/api/selections/{resp.json()['id']}").json()
    assert detail["versions"][0]["sequence_ref"] == {"sequence_id": seq_id, "sequence_version": None}


def test_selection_rejects_both_inline_and_ref(client):
    seq_id = _sequence(client)
    resp = client.post("/api/selections", json={
        "name": "sel", "job_sequence": ["orders_recon"],
        "sequence_ref": {"sequence_id": seq_id},
    })
    assert resp.status_code == 422


def test_selection_rejects_unknown_sequence(client):
    resp = client.post("/api/selections", json={
        "name": "sel", "sequence_ref": {"sequence_id": 999},
    })
    assert resp.status_code == 404


def test_launch_resolves_the_sequence_in_topological_order(client):
    seq_id = _sequence(client)
    sel_id = client.post("/api/selections", json={
        "name": "sel", "sequence_ref": {"sequence_id": seq_id},
    }).json()["id"]

    resp = client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 202, resp.text

    run = client.get(f"/api/runs/{resp.json()['run_id']}").json()
    snapshot = run["config_snapshot"]
    assert [s["job_name"] for s in snapshot["job_sequence"]] == ["load_orders", "orders_recon"]
    assert snapshot["sequence"] == {"id": seq_id, "name": "nightly", "version": 1}


def test_inline_selections_still_work(client):
    resp = client.post("/api/selections", json={"name": "sel", "job_sequence": ["orders_recon"]})
    assert resp.status_code == 201
    detail = client.get(f"/api/selections/{resp.json()['id']}").json()
    assert detail["versions"][0]["sequence_ref"] is None
