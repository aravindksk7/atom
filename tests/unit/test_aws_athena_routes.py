from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.services.aws_athena_service import AthenaQueryFailedError, AthenaQueryResultsResponse, AthenaQueryStatusResponse, AthenaRunQueryResponse, AthenaStartQueryResponse
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
    from api.routes.aws_athena import get_aws_athena_service
    svc = MagicMock()
    svc.start_query.return_value = AthenaStartQueryResponse(query_execution_id="qid-1")
    svc.get_query_status.return_value = AthenaQueryStatusResponse(query_execution_id="qid-1", state="SUCCEEDED", engine_execution_time_ms=42, data_scanned_bytes=1024)
    svc.get_query_results.return_value = AthenaQueryResultsResponse(columns=["id"], rows=[{"id": "1"}])
    svc.run_query.return_value = AthenaRunQueryResponse(query_execution_id="qid-1", status=svc.get_query_status.return_value, results=svc.get_query_results.return_value, dq_metrics={"row_count": 1, "columns": ["id"], "null_counts": {"id": 0}, "distinct_counts": {"id": 1}, "numeric": {"id": {"min": 1.0, "max": 1.0, "avg": 1.0}}})
    app.dependency_overrides[get_aws_athena_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_aws_athena_service, None)


def test_start_query_route(client, mock_service):
    r = client.post("/api/aws/athena/start-query", json={"config_id": 1, "database": "curated", "query": "select 1", "output_location": "s3://out/", "workgroup": "primary"})
    assert r.status_code == 200
    assert r.json()["query_execution_id"] == "qid-1"
    mock_service.start_query.assert_called_once_with(1, "curated", "select 1", "s3://out/", "primary")


def test_query_status_route(client, mock_service):
    r = client.post("/api/aws/athena/query-status", json={"config_id": 1, "query_execution_id": "qid-1"})
    assert r.status_code == 200
    assert r.json()["state"] == "SUCCEEDED"
    mock_service.get_query_status.assert_called_once_with(1, "qid-1")


def test_query_results_route(client, mock_service):
    r = client.post("/api/aws/athena/query-results", json={"config_id": 1, "query_execution_id": "qid-1", "max_rows": 100})
    assert r.status_code == 200
    assert r.json()["rows"] == [{"id": "1"}]
    mock_service.get_query_results.assert_called_once_with(1, "qid-1", 100)


def test_run_query_route(client, mock_service):
    r = client.post("/api/aws/athena/run-query", json={"config_id": 1, "database": "curated", "query": "select 1", "output_location": "s3://out/"})
    assert r.status_code == 200
    assert r.json()["dq_metrics"]["row_count"] == 1
    mock_service.run_query.assert_called_once_with(1, "curated", "select 1", "s3://out/", None, 0.2, 20, 100)


def test_route_preserves_missing_config_http_exception(client, mock_service):
    mock_service.start_query.side_effect = HTTPException(status_code=404, detail="Config not found")
    r = client.post("/api/aws/athena/start-query", json={"config_id": 404, "query": "select 1", "output_location": "s3://out/"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Config not found"


def test_run_query_maps_athena_query_failed_error_to_structured_400(client, mock_service):
    status = AthenaQueryStatusResponse(
        query_execution_id="qid-failed",
        state="FAILED",
        state_change_reason="syntax error",
        submitted_at=None,
        completed_at=None,
    )
    mock_service.run_query.side_effect = AthenaQueryFailedError(status)
    r = client.post("/api/aws/athena/run-query", json={"config_id": 1, "query": "select broken", "output_location": "s3://out/"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["error_type"] == "AthenaQueryFailedError"
    assert detail["message"] == "Athena query ended with FAILED: syntax error"
    assert detail["status"] == {
        "query_execution_id": "qid-failed",
        "state": "FAILED",
        "state_change_reason": "syntax error",
        "engine_execution_time_ms": None,
        "data_scanned_bytes": None,
        "submission_time": None,
        "completion_time": None,
    }


def test_route_maps_generic_exception_to_structured_400(client, mock_service):
    mock_service.run_query.side_effect = RuntimeError("Athena failed")
    r = client.post("/api/aws/athena/run-query", json={"config_id": 1, "query": "select 1", "output_location": "s3://out/"})
    assert r.status_code == 400
    assert r.json()["detail"] == {"error_type": "RuntimeError", "message": "Athena failed"}
