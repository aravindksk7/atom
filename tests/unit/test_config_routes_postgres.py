"""Schema discovery and preview SQL for postgresql / aurora_postgresql configs."""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import ConfigRepository, TokenRepository


class _RecordingEngine:
    queries: list[str] = []

    def __init__(self, env) -> None:
        self.env = env

    def execute_query(self, query: str, params=None) -> pd.DataFrame:
        _RecordingEngine.queries.append(query)
        return pd.DataFrame({
            "table_schema": ["public"], "table_name": ["orders"],
            "column_name": ["id"], "data_type": ["integer"],
        })

    def dispose(self) -> None:
        pass


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("etl_framework.db.engine.DBEngine", _RecordingEngine)
    _RecordingEngine.queries = []
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("admin", is_admin=True)
        for name, db_type in (("pg", "postgresql"), ("aurora", "aurora_postgresql")):
            ConfigRepository(db).create(name, "dev", {
                "db_type": db_type, "db_host": "h", "db_name": "d",
                "db_user": "u", "db_password": "p",
            })
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c


def _config_id(client, name: str) -> int:
    return next(c["id"] for c in client.get("/api/configs").json() if c["name"] == name)


@pytest.mark.parametrize("name", ["pg", "aurora"])
def test_schema_discovery_uses_information_schema_without_system_schemas(client, name):
    resp = client.get(f"/api/configs/{_config_id(client, name)}/schema")
    assert resp.status_code == 200, resp.text
    sql = _RecordingEngine.queries[-1]
    assert "information_schema.columns" in sql.lower()
    assert "'pg_catalog'" in sql and "'information_schema'" in sql
    assert resp.json() == [{"schema": "public", "table": "orders", "columns": [{"name": "id", "type": "integer"}]}]


@pytest.mark.parametrize("name", ["pg", "aurora"])
def test_preview_uses_limit(client, name):
    resp = client.post(
        f"/api/configs/{_config_id(client, name)}/preview-query",
        json={"query": "SELECT * FROM orders", "limit": 7},
    )
    assert resp.status_code == 200, resp.text
    assert _RecordingEngine.queries[-1] == "SELECT * FROM (SELECT * FROM orders) AS _preview LIMIT 7"
