"""Tests for GET /api/logs — the server-wide log view (no run_id required)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository


@pytest.fixture
def client(monkeypatch, tmp_path):
    from api.main import app
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").mkdir()
        yield c


def _write_log(tmp_path, text):
    (tmp_path / "logs" / "etl_framework.log").write_text(text, encoding="utf-8")


def test_returns_404_when_log_file_missing(client):
    resp = client.get("/api/logs")
    assert resp.status_code == 404


def test_returns_all_events_with_no_filters(client, tmp_path):
    _write_log(tmp_path, (
        "2026-07-02 06:10:00 | INFO  |  | a | first\n"
        "2026-07-02 06:10:01 | INFO  |  | a | second\n"
    ))
    resp = client.get("/api/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_lines"] == 2
    assert body["run_id"] == ""


def test_filters_by_run_id(client, tmp_path):
    _write_log(tmp_path, (
        "2026-07-02 06:10:00 | INFO  | run-1 | a | first\n"
        "2026-07-02 06:10:01 | INFO  | run-2 | a | second\n"
    ))
    resp = client.get("/api/logs", params={"run_id": "run-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_lines"] == 1
    assert "run-1" in body["lines"][0]["text"]


def test_filters_by_level(client, tmp_path):
    _write_log(tmp_path, (
        "2026-07-02 06:10:00 | INFO  |  | a | first\n"
        "2026-07-02 06:10:01 | ERROR |  | a | boom\n"
    ))
    resp = client.get("/api/logs", params={"level": "ERROR"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_lines"] == 1
    assert "boom" in body["lines"][0]["text"]


def test_filters_by_search_query(client, tmp_path):
    _write_log(tmp_path, (
        "2026-07-02 06:10:00 | INFO  |  | a | needle here\n"
        "2026-07-02 06:10:01 | INFO  |  | a | nothing relevant\n"
    ))
    resp = client.get("/api/logs", params={"q": "needle"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_lines"] == 1
    assert "needle" in body["lines"][0]["text"]


def test_requires_auth(client):
    resp = client.get("/api/logs", headers={"Authorization": ""})
    assert resp.status_code == 401
