from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import ObjectMetadataOut, RowCountOut, FormatValidationOut
from etl_framework.repository.database import Base
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository
from etl_framework.exceptions import S3ObjectNotFoundError, SchemaValidationError


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c


@pytest.fixture(autouse=True)
def mock_service():
    from api.main import app
    from api.routes.aws_s3 import get_aws_s3_service

    svc = MagicMock()
    svc.metadata.return_value = ObjectMetadataOut(
        bucket="b", key="k", size_bytes=5, last_modified="2026-01-01T00:00:00Z",
        etag="e", storage_class="STANDARD", content_type="text/csv")
    svc.row_count.return_value = RowCountOut(
        bucket="b", key="k", fmt="csv", row_count=3, engine="s3_select")
    app.dependency_overrides[get_aws_s3_service] = lambda: svc
    yield svc
    app.dependency_overrides.clear()


def test_metadata_route(client, mock_service):
    r = client.post("/api/aws/s3/metadata", json={"config_id": 1, "bucket": "b", "key": "k"})
    assert r.status_code == 200
    assert r.json()["size_bytes"] == 5


def test_row_count_route(client, mock_service):
    r = client.post("/api/aws/s3/row-count",
                    json={"config_id": 1, "bucket": "b", "key": "k", "fmt": "csv"})
    assert r.status_code == 200
    assert r.json()["engine"] == "s3_select"


def test_aws_error_maps_to_400(client, mock_service):
    mock_service.metadata.side_effect = S3ObjectNotFoundError("b", "missing")
    r = client.post("/api/aws/s3/metadata", json={"config_id": 1, "bucket": "b", "key": "missing"})
    assert r.status_code == 400
    body = r.json()["detail"]
    assert body["error_type"] == "S3ObjectNotFoundError"


def test_schema_drift_maps_to_400_with_columns(client, mock_service):
    mock_service.validate_format.side_effect = SchemaValidationError(
        "s3://b/k",
        missing_in_target=["email"],
        extra_in_target=["name"],
        type_mismatches=[{"column": "amount", "expected_type": "decimal(12,2)", "actual_type": "string"}],
    )
    r = client.post("/api/aws/s3/validate-format",
                    json={"config_id": 1, "bucket": "b", "key": "k", "fmt": "parquet",
                          "expected_schema": {"id": "int64", "email": "string"}})
    assert r.status_code == 400
    body = r.json()["detail"]
    assert body["error_type"] == "schema_validation"
    assert body["missing_in_target"] == ["email"]
    assert body["extra_in_target"] == ["name"]
    assert body["type_mismatches"] == [
        {"column": "amount", "expected_type": "decimal(12,2)", "actual_type": "string"}
    ]
