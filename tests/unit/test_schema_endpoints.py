from __future__ import annotations
import pytest
import pandas as pd
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

    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def config_id(client):
    resp = client.post("/api/configs", json={
        "env_name": "dev",
        "name": "test-cfg",
        "config_data": {"db_host": "localhost", "db_password": "pass"},
    })
    assert resp.status_code == 201
    return resp.json()["id"]


def _fake_engine_cls(schema_df):
    class FakeDBEngine:
        def __init__(self, env, **kw):
            pass
        def execute_query(self, q, **kw):
            return schema_df
        def dispose(self):
            pass
    return FakeDBEngine


def test_get_schema_returns_grouped_tables(client, config_id, monkeypatch):
    schema_df = pd.DataFrame({
        "TABLE_SCHEMA": ["dbo", "dbo", "staging"],
        "TABLE_NAME": ["orders", "orders", "raw"],
        "COLUMN_NAME": ["id", "amount", "batch"],
        "DATA_TYPE": ["int", "decimal", "varchar"],
    })
    monkeypatch.setattr("etl_framework.db.engine.DBEngine", _fake_engine_cls(schema_df))

    resp = client.get(f"/api/configs/{config_id}/schema")
    assert resp.status_code == 200
    data = resp.json()
    tables = {(t["schema"], t["table"]): t for t in data}
    assert ("dbo", "orders") in tables
    assert len(tables[("dbo", "orders")]["columns"]) == 2
    assert ("staging", "raw") in tables


def test_get_schema_404_for_unknown_config(client):
    resp = client.get("/api/configs/99999/schema")
    assert resp.status_code == 404


def test_get_schema_400_on_db_error(client, config_id, monkeypatch):
    class FailEngine:
        def __init__(self, env, **kw):
            raise ConnectionError("Cannot connect")
        def dispose(self):
            pass

    monkeypatch.setattr("etl_framework.db.engine.DBEngine", FailEngine)
    resp = client.get(f"/api/configs/{config_id}/schema")
    assert resp.status_code == 400
    assert "DB connection failed" in resp.json()["detail"]


# ── Task 6 tests (added here to share the fixture) ──────────────────────────

def test_preview_query_returns_columns_and_rows(client, config_id, monkeypatch):
    result_df = pd.DataFrame({"id": [1, 2], "status": ["pending", "shipped"]})

    class FakeEngine:
        def __init__(self, env, **kw): pass
        def execute_query(self, q, **kw): return result_df
        def dispose(self): pass

    monkeypatch.setattr("etl_framework.db.engine.DBEngine", FakeEngine)

    resp = client.post(f"/api/configs/{config_id}/preview-query", json={
        "query": "SELECT * FROM orders",
        "limit": 10,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["columns"] == ["id", "status"]
    assert data["rows"] == [[1, "pending"], [2, "shipped"]]


def test_preview_query_clamps_limit_at_200(client, config_id, monkeypatch):
    captured = {}

    class FakeEngine:
        def __init__(self, env, **kw): pass
        def execute_query(self, q, **kw):
            captured["q"] = q
            return pd.DataFrame({"id": [1]})
        def dispose(self): pass

    monkeypatch.setattr("etl_framework.db.engine.DBEngine", FakeEngine)

    client.post(f"/api/configs/{config_id}/preview-query", json={
        "query": "SELECT * FROM t", "limit": 9999,
    })
    assert "TOP 200" in captured.get("q", "")


def test_preview_query_422_on_bad_sql(client, config_id, monkeypatch):
    class FailEngine:
        def __init__(self, env, **kw): pass
        def execute_query(self, q, **kw): raise ValueError("column 'x' not found")
        def dispose(self): pass

    monkeypatch.setattr("etl_framework.db.engine.DBEngine", FailEngine)

    resp = client.post(f"/api/configs/{config_id}/preview-query", json={
        "query": "SELECT x FROM orders", "limit": 10,
    })
    assert resp.status_code == 422
    assert "Query failed" in resp.json()["detail"]
