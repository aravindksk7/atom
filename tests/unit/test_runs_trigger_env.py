"""POST /api/runs applies the same single-vs-dual environment gate as the
sequences, selections and schedules launch routes.

Single-environment job types (file_transfer, file_watcher, ...) may launch with
``target_env`` omitted entirely; everything else must still name one.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from api.routes import runs as runs_module
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base, get_db
import etl_framework.repository.models  # noqa: F401 -- registers ORM models with Base
from etl_framework.repository.repository import TokenRepository


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(runs_module, "_execute_run", lambda *args, **kwargs: None)
    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


def _save_file_transfer_job(client, name: str = "stage_sales") -> None:
    resp = client.post(
        "/api/jobs",
        json={
            "name": name,
            "job_type": "file_transfer",
            "query": "",
            "key_columns": [],
            "params": {
                "source": {"kind": "local", "root": "/data/in", "pattern": "SALES_*.csv"},
                "destination": {"kind": "local", "root": "/data/out"},
                "on_exists": "skip",
            },
        },
    )
    assert resp.status_code == 201, resp.text


def _save_file_watcher_job(client, name: str = "watch_sales") -> None:
    resp = client.post(
        "/api/jobs",
        json={
            "name": name,
            "job_type": "file_watcher",
            "query": "",
            "key_columns": [],
            "params": {
                "location": {"kind": "local", "root": "/data/in", "pattern": "SALES_*.csv"},
                "max_tries": 5,
                "poll_interval_seconds": 10,
            },
        },
    )
    assert resp.status_code == 201, resp.text


def _save_reconciliation_job(client, name: str = "orders_check") -> None:
    resp = client.post(
        "/api/jobs",
        json={
            "name": name,
            "job_type": "reconciliation",
            "query": "SELECT id FROM orders",
            "key_columns": ["id"],
        },
    )
    assert resp.status_code == 201, resp.text


def test_file_transfer_job_launches_without_a_target_env(client):
    _save_file_transfer_job(client)
    resp = client.post(
        "/api/runs", json={"source_env": "dev", "job_names": ["stage_sales"]},
    )
    assert resp.status_code == 202, resp.text


def test_file_watcher_job_launches_without_a_target_env(client):
    _save_file_watcher_job(client)
    resp = client.post(
        "/api/runs", json={"source_env": "dev", "job_names": ["watch_sales"]},
    )
    assert resp.status_code == 202, resp.text


def test_reconciliation_job_rejected_when_target_env_is_omitted(client):
    _save_reconciliation_job(client)
    resp = client.post(
        "/api/runs", json={"source_env": "dev", "job_names": ["orders_check"]},
    )
    assert resp.status_code == 422, resp.text
    assert "requires a target_env" in resp.text


def test_reconciliation_job_rejected_when_target_env_is_blank(client):
    _save_reconciliation_job(client)
    resp = client.post(
        "/api/runs",
        json={"source_env": "dev", "target_env": "", "job_names": ["orders_check"]},
    )
    assert resp.status_code == 422, resp.text
    assert "requires a target_env" in resp.text


def test_reconciliation_job_still_launches_with_a_real_target_env(client):
    _save_reconciliation_job(client)
    resp = client.post(
        "/api/runs",
        json={"source_env": "dev", "target_env": "prod", "job_names": ["orders_check"]},
    )
    assert resp.status_code == 202, resp.text


def test_mixed_sequence_is_rejected_when_any_step_needs_a_target_env(client):
    _save_file_transfer_job(client)
    _save_reconciliation_job(client)
    resp = client.post(
        "/api/runs",
        json={"source_env": "dev", "job_names": ["stage_sales", "orders_check"]},
    )
    assert resp.status_code == 422, resp.text
    assert "requires a target_env" in resp.text
    assert "orders_check" in resp.text
