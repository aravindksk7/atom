"""Tests for /api/expectations sync/export endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository
from api.main import app


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(engine, monkeypatch):
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as setup_db:
        raw, _ = TokenRepository(setup_db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


def test_sync_endpoint_returns_report(client, db, tmp_path):
    from etl_framework.expectations.suite import ExpectationSuite, dump_suite
    from etl_framework.repository.repository import JobRepository

    JobRepository(db).create({
        "name": "orders_reconciliation", "job_type": "reconciliation",
        "query": "SELECT 1", "params": {}, "enabled": True,
    })
    dump_suite(ExpectationSuite(job="orders_reconciliation",
                                rules=[{"type": "not_null", "column": "id"}]),
               tmp_path / "orders_reconciliation.yml")
    resp = client.post("/api/expectations/sync", json={"directory": str(tmp_path)})
    assert resp.status_code == 200
    assert resp.json()["synced"] == ["orders_reconciliation"]


def test_sync_rejects_missing_directory(client):
    resp = client.post("/api/expectations/sync", json={"directory": "Z:/does/not/exist"})
    assert resp.status_code == 400
