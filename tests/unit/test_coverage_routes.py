import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.routes import runs as runs_module
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

    monkeypatch.setattr(runs_module, "_execute_run", lambda *args, **kwargs: None)
    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


def test_coverage_empty_db_returns_empty_shape(client):
    resp = client.get("/api/coverage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tables"] == []
    assert data["summary"]["tables"] == 0


def test_coverage_reflects_saved_job(client):
    job = {
        "name": "cov_job", "job_type": "reconciliation",
        "query": "SELECT * FROM orders", "key_columns": ["id"],
        "rules": [{"type": "not_null", "column": "amt"}],
    }
    assert client.post("/api/jobs", json=job).status_code in (200, 201)
    data = client.get("/api/coverage").json()
    assert data["summary"]["tables"] == 1
    table = data["tables"][0]
    assert table["table"] == "orders"
    cols = {c["column"]: c for c in table["columns"]}
    assert cols["amt"]["level"] == "tested"
    assert "not_null" in cols["amt"]["rules"]
    assert cols["id"]["level"] == "tested"  # key column


def test_flaky_empty_db(client):
    resp = client.get("/api/coverage/flaky")
    assert resp.status_code == 200
    assert resp.json() == []


def test_flaky_window_param_validated(client):
    assert client.get("/api/coverage/flaky?window=1").status_code == 422
    assert client.get("/api/coverage/flaky?window=500").status_code == 422


def test_coverage_requires_auth(client):
    # a bare request without the bearer header
    resp = client.get("/api/coverage", headers={"Authorization": ""})
    assert resp.status_code in (401, 403)
