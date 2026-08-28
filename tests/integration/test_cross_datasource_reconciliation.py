"""Integration tests for Cross-Datasource Matrix Reconciliation."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.dependencies import get_session
from api.main import app
from api.schemas import DataSourceSpec, MatrixCompareRequest
from api.services.compare_service import CompareService
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base, get_db
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import ConfigRepository, RunRepository, TokenRepository


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)
    session = SessionMaker()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(monkeypatch):
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    SessionMaker = sessionmaker(bind=engine)
    monkeypatch.setattr(_db_module, "SessionLocal", SessionMaker)

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_session
    app.dependency_overrides[get_session] = override_session

    with Session(engine) as db:
        raw_token, _ = TokenRepository(db).create("test-matrix-token")

    with TestClient(app, headers={"Authorization": f"Bearer {raw_token}"}, raise_server_exceptions=True) as c:
        yield c, engine

    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous_overrides)


def test_sql_query_vs_file_reconciliation(db_session, tmp_path):
    """Test SQL query vs File reconciliation (CSV file vs SQLite query in database)."""
    db_file = tmp_path / "test_sql.db"
    sqlite_engine = create_engine(f"sqlite:///{db_file}")
    with sqlite_engine.connect() as conn:
        conn.execute(text("CREATE TABLE users (id INT PRIMARY KEY, name TEXT, amount REAL)"))
        conn.execute(text("INSERT INTO users VALUES (1, 'Alice', 100.50), (2, 'Bob', 250.75)"))
        conn.commit()

    csv_file = tmp_path / "users_target.csv"
    csv_file.write_text("id,name,amount\n1,Alice,100.50\n2,Bob,250.75\n", encoding="utf-8")

    run_id = "run-sql-vs-file-1"
    RunRepository(db_session).create_run(
        run_id=run_id, source_env="SQLite DB", target_env="CSV File", run_type="matrix_comparison"
    )

    req = MatrixCompareRequest(
        source_a=DataSourceSpec(
            source_type="sql",
            query_or_table="SELECT id, name, amount FROM users",
            connection_string=f"sqlite:///{db_file}",
        ),
        source_b=DataSourceSpec(
            source_type="file",
            file_path=str(csv_file),
        ),
        key_columns=["id"],
        label_a="SQLite DB",
        label_b="CSV File",
    )

    svc = CompareService(db_session, ConfigRepository(db_session))
    svc.run_matrix_comparison(req, run_id)

    run = RunRepository(db_session).get_run(run_id)
    assert run is not None
    assert run.status == "PASSED"
    assert run.total_tests == 1
    assert run.passed == 1
    assert run.failed == 0
    assert len(run.results) == 1
    assert run.results[0].status == "PASSED"


def test_file_vs_api_endpoint_reconciliation(db_session, tmp_path):
    """Test File vs API endpoint reconciliation using requests mock."""
    csv_file = tmp_path / "api_data.csv"
    csv_file.write_text("id,sku,stock\n101,WIDGET-A,50\n102,WIDGET-B,120\n", encoding="utf-8")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"id": 101, "sku": "WIDGET-A", "stock": 50},
        {"id": 102, "sku": "WIDGET-B", "stock": 120},
    ]
    mock_response.raise_for_status.return_value = None

    run_id = "run-file-vs-api-1"
    RunRepository(db_session).create_run(
        run_id=run_id, source_env="CSV File", target_env="API Endpoint", run_type="matrix_comparison"
    )

    req = MatrixCompareRequest(
        source_a=DataSourceSpec(
            source_type="file",
            file_path=str(csv_file),
        ),
        source_b=DataSourceSpec(
            source_type="api",
            url="https://api.inventory.internal/v1/stock",
            http_method="GET",
        ),
        key_columns=["id"],
        label_a="CSV Inventory",
        label_b="API Inventory",
    )

    with patch("requests.request", return_value=mock_response) as mock_req:
        svc = CompareService(db_session, ConfigRepository(db_session))
        svc.run_matrix_comparison(req, run_id)
        mock_req.assert_called_once()

    run = RunRepository(db_session).get_run(run_id)
    assert run is not None
    assert run.status == "PASSED"
    assert run.passed == 1
    assert run.failed == 0


def test_sql_vs_athena_mock_query_reconciliation(db_session, tmp_path):
    """Test SQL vs Athena mock query reconciliation."""
    db_file = tmp_path / "athena_sql.db"
    sqlite_engine = create_engine(f"sqlite:///{db_file}")
    with sqlite_engine.connect() as conn:
        conn.execute(text("CREATE TABLE orders (order_id INT PRIMARY KEY, status TEXT, total REAL)"))
        conn.execute(text("INSERT INTO orders VALUES (1001, 'COMPLETED', 99.99), (1002, 'PENDING', 49.50)"))
        conn.commit()

    class MockAthenaRunner:
        def run_query(self, query: str) -> pd.DataFrame:
            return pd.DataFrame([
                {"order_id": 1001, "status": "COMPLETED", "total": 99.99},
                {"order_id": 1002, "status": "PENDING", "total": 49.50},
            ])

    run_id = "run-sql-vs-athena-1"
    RunRepository(db_session).create_run(
        run_id=run_id, source_env="SQL DB", target_env="AWS Athena", run_type="matrix_comparison"
    )

    req = MatrixCompareRequest(
        source_a=DataSourceSpec(
            source_type="sql",
            query_or_table="SELECT order_id, status, total FROM orders",
            connection_string=f"sqlite:///{db_file}",
        ),
        source_b=DataSourceSpec(
            source_type="aws_athena",
            query_or_table="SELECT * FROM processed_orders",
            query_runner=MockAthenaRunner(),
        ),
        key_columns=["order_id"],
        label_a="SQL Source",
        label_b="Athena Target",
    )

    svc = CompareService(db_session, ConfigRepository(db_session))
    svc.run_matrix_comparison(req, run_id)

    run = RunRepository(db_session).get_run(run_id)
    assert run is not None
    assert run.status == "PASSED"
    assert run.passed == 1
    assert run.failed == 0


def test_full_api_matrix_compare_flow(client, tmp_path):
    """Test full API flow via TestClient calling POST /api/compare/matrix and fetching run results."""
    c, engine = client

    csv_a = tmp_path / "source_a.csv"
    csv_b = tmp_path / "source_b.csv"
    csv_a.write_text("id,metric,value\n1,CPU,45.2\n2,RAM,78.1\n", encoding="utf-8")
    csv_b.write_text("id,metric,value\n1,CPU,45.2\n2,RAM,78.1\n", encoding="utf-8")

    payload = {
        "source_a": {
            "source_type": "file",
            "file_path": str(csv_a),
        },
        "source_b": {
            "source_type": "file",
            "file_path": str(csv_b),
        },
        "key_columns": ["id"],
        "label_a": "Metric CSV A",
        "label_b": "Metric CSV B",
    }

    resp = c.post("/api/compare/matrix", json=payload)
    assert resp.status_code == 202
    res_data = resp.json()
    assert "run_id" in res_data
    run_id = res_data["run_id"]
    assert res_data["run_type"] == "matrix_comparison"

    run_resp = c.get(f"/api/runs/{run_id}")
    assert run_resp.status_code == 200
    detail = run_resp.json()
    assert detail["run_id"] == run_id
    assert detail["status"] == "PASSED"
    assert detail["passed"] == 1
    assert detail["failed"] == 0
    assert len(detail["results"]) == 1
