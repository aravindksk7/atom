"""Tests for /api/sequences CRUD, validation, and usage endpoints."""
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


CHAIN = [
    {"step_id": "load", "job_name": "load_orders", "depends_on": []},
    {"step_id": "recon", "job_name": "orders_recon", "depends_on": ["load"]},
]


def _create(client, name="nightly", steps=None):
    return client.post("/api/sequences", json={
        "name": name, "description": "d", "tags": ["t"], "steps": steps if steps is not None else CHAIN,
    })


def test_create_returns_201_with_version_one(client):
    resp = _create(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["latest_version"] == 1
    assert body["step_count"] == 2


def test_create_rejects_duplicate_name(client):
    _create(client)
    assert _create(client).status_code == 409


def test_create_rejects_cycle_with_422(client):
    resp = _create(client, steps=[
        {"step_id": "a", "job_name": "orders_recon", "depends_on": ["b"]},
        {"step_id": "b", "job_name": "load_orders", "depends_on": ["a"]},
    ])
    assert resp.status_code == 422
    assert any("cycle" in e["message"].lower() for e in resp.json()["detail"])


def test_create_rejects_unknown_job(client):
    resp = _create(client, steps=[{"step_id": "a", "job_name": "ghost", "depends_on": []}])
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "job_name"


def test_create_rejects_empty_steps(client):
    resp = _create(client, steps=[])
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "steps"


def test_create_accepts_a_trigger_rule(client):
    resp = _create(client, steps=[
        {"step_id": "a", "job_name": "orders_recon", "depends_on": []},
        {"step_id": "cleanup", "job_name": "load_orders", "depends_on": ["a"],
         "trigger_rule": "all_done"},
    ])
    assert resp.status_code == 201, resp.text



def test_create_accepts_retry_and_failure_policy(client):
    resp = _create(client, steps=[
        {"step_id": "a", "job_name": "orders_recon", "depends_on": [],
         "max_retries": 3, "retry_delay_seconds": 10, "on_failure": "continue"},
    ])
    assert resp.status_code == 201, resp.text


def test_create_accepts_preconditions(client):
    resp = client.post("/api/sequences", json={
        "name": "gated", "steps": CHAIN,
        "preconditions": {
            "time_window": {"start": "01:00", "end": "05:00"},
            "weekdays": [0, 1, 2, 3, 4],
        },
    })
    assert resp.status_code == 201, resp.text

    detail = client.get(f"/api/sequences/{resp.json()['id']}").json()
    stored = detail["versions"][0]["preconditions"]
    assert stored["time_window"] == {"start": "01:00", "end": "05:00"}
    assert stored["weekdays"] == [0, 1, 2, 3, 4]


def test_validate_endpoint_accepts_preconditions(client):
    resp = client.post("/api/sequences/validate", json={
        "steps": CHAIN, "preconditions": {"weekdays": [0]},
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_get_returns_detail_with_versions(client):
    seq_id = _create(client).json()["id"]
    body = client.get(f"/api/sequences/{seq_id}").json()
    assert len(body["versions"]) == 1
    assert [s["step_id"] for s in body["versions"][0]["steps"]] == ["load", "recon"]


def test_list_excludes_archived(client):
    seq_id = _create(client).json()["id"]
    assert len(client.get("/api/sequences").json()) == 1
    assert client.delete(f"/api/sequences/{seq_id}").status_code == 204
    assert client.get("/api/sequences").json() == []
    assert len(client.get("/api/sequences?include_archived=true").json()) == 1


def test_patch_updates_metadata_only(client):
    seq_id = _create(client).json()["id"]
    body = client.patch(f"/api/sequences/{seq_id}", json={"name": "renamed"}).json()
    assert body["name"] == "renamed"
    assert body["latest_version"] == 1


def test_post_version_increments_and_validates(client):
    seq_id = _create(client).json()["id"]
    resp = client.post(f"/api/sequences/{seq_id}/versions", json={
        "steps": [{"step_id": "solo", "job_name": "orders_recon", "depends_on": []}],
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["version_number"] == 2
    assert client.get(f"/api/sequences/{seq_id}/versions/1").json()["version_number"] == 1


def test_get_missing_version_returns_404(client):
    seq_id = _create(client).json()["id"]
    assert client.get(f"/api/sequences/{seq_id}/versions/9").status_code == 404


def test_validate_endpoint_reports_order_when_valid(client):
    resp = client.post("/api/sequences/validate", json={"steps": CHAIN})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "errors": [], "order": ["load", "recon"]}


def test_validate_endpoint_reports_errors_without_persisting(client):
    resp = client.post("/api/sequences/validate", json={
        "steps": [{"step_id": "a", "job_name": "ghost", "depends_on": []}],
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert client.get("/api/sequences").json() == []


def test_usage_is_empty_for_a_fresh_sequence(client):
    seq_id = _create(client).json()["id"]
    assert client.get(f"/api/sequences/{seq_id}/usage").json() == {
        "selections": [], "schedules": [],
    }
