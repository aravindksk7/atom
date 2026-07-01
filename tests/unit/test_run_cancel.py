from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import RunRepository, TokenRepository
from api.main import app
from api.routes import runs as runs_module


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


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_run(db: Session, run_id: str = "run-001", status: str = "RUNNING") -> None:
    repo = RunRepository(db)
    repo.create_run(run_id, None, None, run_type="reconciliation")
    repo.update_run_status(run_id, status)


# --- request_cancel ---

def test_request_cancel_sets_flag():
    db = _session()
    _make_run(db)
    repo = RunRepository(db)
    result = repo.request_cancel("run-001")
    assert result is True
    run = repo.get_run("run-001")
    assert run.cancel_requested is True


def test_request_cancel_returns_false_for_missing_run():
    db = _session()
    repo = RunRepository(db)
    assert repo.request_cancel("no-such-run") is False


def test_request_cancel_returns_false_for_terminal_run():
    db = _session()
    _make_run(db, status="PASSED")
    repo = RunRepository(db)
    assert repo.request_cancel("run-001") is False


# --- is_cancel_requested ---

def test_is_cancel_requested_false_by_default():
    db = _session()
    _make_run(db)
    assert RunRepository(db).is_cancel_requested("run-001") is False


def test_is_cancel_requested_true_after_request():
    db = _session()
    _make_run(db)
    repo = RunRepository(db)
    repo.request_cancel("run-001")
    assert repo.is_cancel_requested("run-001") is True


# --- cancel endpoint ---

def test_cancel_endpoint_returns_202(client):
    resp = client.post("/api/runs", json={
        "source_env": "dev",
        "target_env": "prod",
        "job_names": [],
    })
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    cancel_resp = client.post(f"/api/runs/{run_id}/cancel")
    assert cancel_resp.status_code == 202
    data = cancel_resp.json()
    assert data["run_id"] == run_id
    assert "cancel_requested" in data


def test_cancel_endpoint_404_for_unknown_run(client):
    resp = client.post("/api/runs/no-such-id/cancel")
    assert resp.status_code == 404


def test_cancel_endpoint_idempotent_when_called_twice(client):
    resp = client.post("/api/runs", json={
        "source_env": "dev",
        "target_env": "prod",
        "job_names": [],
    })
    run_id = resp.json()["run_id"]
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 202
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 202


# --- executor cooperative cancellation ---

from unittest.mock import patch
from api.schemas import RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.repository.repository import JobRepository


def _session() -> Session:  # noqa: F811 — redefine for local use
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _create_job(db: Session, name: str) -> None:
    JobRepository(db).create({
        "name": name,
        "description": "",
        "tags": [],
        "job_type": "reconciliation",
        "query": f"SELECT * FROM {name}",
        "key_columns": ["id"],
        "exclude_columns": [],
        "source_env": None,
        "target_env": None,
        "params": {"source_rows": [{"id": 1}], "target_rows": [{"id": 1}]},
        "enabled": True,
    })


def test_executor_stops_after_current_step_when_cancel_requested():
    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-x", None, None)
    repo.update_run_status("run-x", "RUNNING")
    _create_job(db, "job-a")
    _create_job(db, "job-b")

    call_count = {"n": 0}

    def fake_is_cancel(self, run_id):
        call_count["n"] += 1
        return call_count["n"] >= 1  # True from first call onward

    with patch.object(RunRepository, "is_cancel_requested", fake_is_cancel):
        RunExecutor(
            db=db,
            run_id="run-x",
            source_env="dev",
            target_env="prod",
            job_sequence=["job-a", "job-b"],
            run_settings=RunSettings(metrics_enabled=False),
        ).execute()

    assert RunRepository(db).get_run("run-x").status == "CANCELLED"
