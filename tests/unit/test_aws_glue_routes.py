from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.services.aws_glue_service import GlueCatalogCompareResponse, GlueDatabasesResponse, GlueTableResponse, GlueTablesResponse
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
from etl_framework.repository.repository import TokenRepository
import etl_framework.repository.models  # noqa: F401


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c


@pytest.fixture(autouse=True)
def mock_service():
    from api.main import app
    from api.routes.aws_glue import get_aws_glue_service
    svc = MagicMock()
    svc.list_databases.return_value = GlueDatabasesResponse(databases=["raw"])
    svc.list_tables.return_value = GlueTablesResponse(database="raw", tables=["orders"])
    svc.describe_table.return_value = GlueTableResponse(database="raw", table="orders", columns=[{"name": "id", "type": "int64"}], partition_keys=[])
    svc.compare_tables.return_value = GlueCatalogCompareResponse(match=False, source={}, target={}, diff={"missing_columns": ["amount"], "extra_columns": [], "type_mismatches": [], "partition_key_mismatches": [], "location_mismatch": None, "format_mismatch": None})
    app.dependency_overrides[get_aws_glue_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_aws_glue_service, None)


def test_glue_databases_route(client):
    r = client.post("/api/aws/glue/databases", json={"config_id": 1})
    assert r.status_code == 200
    assert r.json()["databases"] == ["raw"]


def test_glue_tables_route(client):
    r = client.post("/api/aws/glue/tables", json={"config_id": 1, "database": "raw"})
    assert r.status_code == 200
    assert r.json()["tables"] == ["orders"]


def test_glue_table_route(client):
    r = client.post("/api/aws/glue/table", json={"config_id": 1, "database": "raw", "table": "orders"})
    assert r.status_code == 200
    assert r.json()["columns"][0]["name"] == "id"


def test_glue_compare_tables_route(client, mock_service):
    r = client.post("/api/aws/glue/compare-tables", json={"config_id": 1, "source_database": "raw", "source_table": "orders", "target_database": "curated", "target_table": "orders"})
    assert r.status_code == 200
    assert r.json()["match"] is False
    assert r.json()["diff"]["missing_columns"] == ["amount"]
    mock_service.compare_tables.assert_called_once_with(1, "raw", "orders", "curated", "orders", True, True, True)


def test_glue_compare_tables_route_passes_false_flags(client, mock_service):
    r = client.post(
        "/api/aws/glue/compare-tables",
        json={
            "config_id": 1,
            "source_database": "raw",
            "source_table": "orders",
            "target_database": "curated",
            "target_table": "orders",
            "compare_location": False,
            "compare_formats": False,
            "compare_partitions": False,
        },
    )
    assert r.status_code == 200
    mock_service.compare_tables.assert_called_once_with(1, "raw", "orders", "curated", "orders", False, False, False)


def test_glue_route_preserves_missing_config_http_exception(client, mock_service):
    mock_service.list_databases.side_effect = HTTPException(status_code=404, detail="Config not found")

    r = client.post("/api/aws/glue/databases", json={"config_id": 999})

    assert r.status_code == 404
    assert r.json() == {"detail": "Config not found"}


def test_glue_route_maps_generic_exception_to_structured_400(client, mock_service):
    mock_service.list_databases.side_effect = RuntimeError("AWS boom")

    r = client.post("/api/aws/glue/databases", json={"config_id": 1})

    assert r.status_code == 400
    assert r.json() == {"detail": {"error_type": "RuntimeError", "message": "AWS boom"}}
