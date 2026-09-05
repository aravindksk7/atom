from __future__ import annotations

from unittest.mock import MagicMock, patch

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


def test_list_jobs_route(client, mock_service):
    mock_service.list_jobs.return_value = [
        {"name": "etl-job", "description": "ETL", "role": "r", "script_location": "s3://b/s", "worker_type": "G.1X"},
    ]
    r = client.get("/api/aws/glue/jobs?config_id=1")
    assert r.status_code == 200
    data = r.json()
    assert data["jobs"] == [{"name": "etl-job", "description": "ETL", "role": "r", "script_location": "s3://b/s", "worker_type": "G.1X"}]
    mock_service.list_jobs.assert_called_once_with("1")


def test_list_jobs_audit_log(client, mock_service):
    mock_service.list_jobs.return_value = [{"name": "j1"}, {"name": "j2"}]
    with patch("api.routes.aws_glue.AuditService") as MockAudit:
        r = client.get("/api/aws/glue/jobs?config_id=1")
    assert r.status_code == 200
    MockAudit.return_value.log.assert_called_once()
    call_args = MockAudit.return_value.log.call_args
    assert call_args.args[1] == "aws_glue.check"
    assert call_args.args[2] == "aws_glue_jobs"
    assert call_args.args[3] == "1"
    assert call_args.args[4] == {"count": 2}


def test_get_job_route(client, mock_service):
    mock_service.get_job.return_value = {"name": "etl-job", "description": "ETL", "role": "r", "script_location": "s3://b/s", "worker_type": "G.1X", "max_capacity": 10.0}
    r = client.get("/api/aws/glue/jobs/etl-job?config_id=1")
    assert r.status_code == 200
    assert r.json()["name"] == "etl-job"
    mock_service.get_job.assert_called_once_with("1", "etl-job")


def test_start_job_route(client, mock_service):
    mock_service.start_job_run.return_value = {"job_run_id": "jr_123", "job_name": "etl-job"}
    r = client.post("/api/aws/glue/jobs/etl-job/start", json={"config_id": 1, "arguments": {"--foo": "bar"}})
    assert r.status_code == 200
    data = r.json()
    assert data["job_run_id"] == "jr_123"
    assert data["job_name"] == "etl-job"
    mock_service.start_job_run.assert_called_once_with(1, "etl-job", {"--foo": "bar"})


def test_start_job_route_without_arguments(client, mock_service):
    mock_service.start_job_run.return_value = {"job_run_id": "jr_123", "job_name": "etl-job"}
    r = client.post("/api/aws/glue/jobs/etl-job/start", json={"config_id": 1})
    assert r.status_code == 200
    mock_service.start_job_run.assert_called_once_with(1, "etl-job", None)


def test_get_job_run_status_route(client, mock_service):
    mock_service.get_job_run_status.return_value = {
        "job_run_id": "jr_123", "job_name": "etl-job", "job_run_state": "SUCCEEDED", "execution_time": 42, "error_message": None,
    }
    r = client.get("/api/aws/glue/jobs/etl-job/runs/jr_123?config_id=1")
    assert r.status_code == 200
    data = r.json()
    assert data["job_run_id"] == "jr_123"
    assert data["job_run_state"] == "SUCCEEDED"
    mock_service.get_job_run_status.assert_called_once_with("1", "etl-job", "jr_123")


def test_run_job_to_completion_route(client, mock_service):
    mock_service.run_job_to_completion.return_value = {
        "job_run_id": "jr_123", "job_name": "etl-job", "job_run_state": "SUCCEEDED", "execution_time": 42, "error_message": None,
    }
    r = client.post("/api/aws/glue/jobs/etl-job/run", json={"config_id": 1, "arguments": {"--foo": "bar"}})
    assert r.status_code == 200
    assert r.json()["job_run_state"] == "SUCCEEDED"
    mock_service.run_job_to_completion.assert_called_once_with(1, "etl-job", {"--foo": "bar"}, 2.0, 120)


def test_run_job_to_completion_custom_poll(client, mock_service):
    mock_service.run_job_to_completion.return_value = {
        "job_run_id": "jr_123", "job_name": "etl-job", "job_run_state": "SUCCEEDED", "execution_time": 42, "error_message": None,
    }
    r = client.post("/api/aws/glue/jobs/etl-job/run", json={"config_id": 1, "poll_interval_seconds": 0.5, "max_attempts": 10})
    assert r.status_code == 200
    mock_service.run_job_to_completion.assert_called_once_with(1, "etl-job", None, 0.5, 10)


def test_jobs_route_maps_generic_exception_to_structured_400(client, mock_service):
    mock_service.list_jobs.side_effect = RuntimeError("AWS boom")
    r = client.get("/api/aws/glue/jobs?config_id=1")
    assert r.status_code == 400
    assert r.json()["detail"] == {"error_type": "RuntimeError", "message": "AWS boom"}


def test_jobs_route_preserves_http_exception(client, mock_service):
    mock_service.list_jobs.side_effect = HTTPException(status_code=404, detail="Config not found")
    r = client.get("/api/aws/glue/jobs?config_id=999")
    assert r.status_code == 404
    assert r.json()["detail"] == "Config not found"


def test_run_job_timeout_maps_to_400(client, mock_service):
    mock_service.run_job_to_completion.side_effect = TimeoutError("timed out")
    r = client.post("/api/aws/glue/jobs/etl-job/run", json={"config_id": 1})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["error_type"] == "TimeoutError"
    assert "timed out" in detail["message"]
