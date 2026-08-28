from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository, RunRepository
from api.main import app
from api.schemas import DataSourceSpec, MatrixCompareRequest


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


def test_data_source_spec_defaults():
    spec = DataSourceSpec(source_type="sql", config_id=1, query_or_table="SELECT 1")
    assert spec.source_type == "sql"
    assert spec.config_id == 1
    assert spec.http_method == "GET"
    assert spec.connection_name is None
    assert spec.headers is None


def test_matrix_compare_request_defaults():
    src_a = DataSourceSpec(source_type="sql", config_id=1)
    src_b = DataSourceSpec(source_type="file", file_path="/path/to/file.csv")
    req = MatrixCompareRequest(source_a=src_a, source_b=src_b)
    assert req.label_a == "Source A"
    assert req.label_b == "Source B"
    assert req.key_columns == []
    assert req.exclude_columns == []
    assert req.numeric_tolerance == 0.0
    assert req.ignore_case is False
    assert req.trim_whitespace is True


def test_matrix_compare_validation_error(client):
    # Invalid payload missing source_b and source_type
    resp = client.post("/api/compare/matrix", json={"source_a": {}})
    assert resp.status_code == 422


def test_matrix_compare_returns_202_and_creates_run(client, monkeypatch):
    bg_calls = []

    def mock_bg(req, run_id):
        bg_calls.append((req, run_id))

    import api.routes.compare as cmp_module
    monkeypatch.setattr(cmp_module, "_run_matrix_bg", mock_bg)

    payload = {
        "source_a": {
            "source_type": "sql",
            "config_id": 1,
            "connection_name": "main",
            "query_or_table": "users",
        },
        "source_b": {
            "source_type": "file",
            "file_path": "/tmp/users.csv",
        },
        "label_a": "DB Users",
        "label_b": "CSV Users",
        "key_columns": ["id"],
        "numeric_tolerance": 0.01,
    }

    resp = client.post("/api/compare/matrix", json=payload)
    assert resp.status_code == 202

    data = resp.json()
    assert "run_id" in data
    assert data["run_type"] == "matrix_comparison"
    assert data["status"] in ("PENDING", "RUNNING", "COMPLETED", "CREATED")

    # Verify background task was scheduled
    assert len(bg_calls) == 1
    req, run_id = bg_calls[0]
    assert req.label_a == "DB Users"
    assert req.source_a.source_type == "sql"
    assert req.source_b.source_type == "file"
    assert run_id == data["run_id"]
