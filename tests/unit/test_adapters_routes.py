"""Tests for /api/adapters routes."""
from __future__ import annotations

import base64
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import (
    AdapterTestOut, BODocOut, BOAuthSessionOut, BOReportOut,
    AutomicJobStatusOut, JobDefinition,
)
from etl_framework.repository.database import Base
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c


@pytest.fixture(autouse=True)
def mock_adapter_service():
    """Replace AdapterService with a mock via FastAPI dependency_overrides."""
    from api.main import app
    from api.routes.adapters import get_adapter_service

    svc = MagicMock()
    svc.test_bo_connection.return_value = AdapterTestOut(ok=True, message="Connected", latency_ms=12)
    svc.list_bo_documents.return_value = [
        BODocOut(id="101", name="Sales", folder="/Finance"),
    ]
    svc.list_bo_reports.return_value = [
        BOReportOut(id="1", name="Page 1", report_index=0),
    ]
    svc.download_bo_report.return_value = b"PDF content"
    svc.create_bo_session.return_value = BOAuthSessionOut(
        ok=True,
        message="SAP BO logon successful",
        auth_scheme="basic",
        token="sap-token",
        latency_ms=11,
    )
    svc.logoff_bo_session.return_value = BOAuthSessionOut(
        ok=True,
        message="SAP BO logoff successful",
        auth_scheme="x-sap-logontoken",
        token=None,
        latency_ms=8,
    )
    svc.lookup_automic_job.return_value = AutomicJobStatusOut(
        identifier="MY_JOB", identifier_type="job_name",
        status="PASSED", environment="dev",
        checked_at=datetime.now(timezone.utc),
    )
    app.dependency_overrides[get_adapter_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_adapter_service, None)


# ---------------------------------------------------------------------------
# SAP BO routes
# ---------------------------------------------------------------------------

def test_test_bo_connection_returns_200(client):
    resp = client.post("/api/adapters/sap-bo/test", json={"config_id": 1})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["latency_ms"] == 12


def test_sap_bo_logon_accepts_basic_credentials(client, mock_adapter_service):
    raw = base64.b64encode(b"bo-user:bo-pass").decode("ascii")
    resp = client.post(
        "/api/adapters/sap-bo/logon",
        json={"config_id": 1, "auth_type": "secWinAD"},
        headers={"Authorization": f"Basic {raw}"},
    )

    assert resp.status_code == 200
    assert resp.json()["token"] == "sap-token"
    auth_ctx = mock_adapter_service.create_bo_session.call_args.args[1]
    assert auth_ctx.scheme == "basic"
    assert auth_ctx.username == "bo-user"
    assert auth_ctx.password == "bo-pass"
    assert auth_ctx.auth_type == "secWinAD"


def test_sap_bo_logon_accepts_x_sap_logontoken(client, mock_adapter_service):
    mock_adapter_service.create_bo_session.return_value = BOAuthSessionOut(
        ok=True,
        message="SAP BO token is valid",
        auth_scheme="x-sap-logontoken",
        token=None,
        latency_ms=7,
    )
    resp = client.post(
        "/api/adapters/sap-bo/logon",
        json={"config_id": 1},
        headers={"X-SAP-LogonToken": "existing-token"},
    )

    assert resp.status_code == 200
    auth_ctx = mock_adapter_service.create_bo_session.call_args.args[1]
    assert auth_ctx.scheme == "x-sap-logontoken"
    assert auth_ctx.token == "existing-token"


def test_sap_bo_logoff_requires_token_header(client):
    resp = client.post("/api/adapters/sap-bo/logoff", json={"config_id": 1})
    assert resp.status_code == 400


def test_sap_bo_logoff_passes_token(client, mock_adapter_service):
    resp = client.post(
        "/api/adapters/sap-bo/logoff",
        json={"config_id": 1},
        headers={"X-SAP-LogonToken": "sap-token"},
    )
    assert resp.status_code == 200
    assert mock_adapter_service.logoff_bo_session.call_args.args == (1, "sap-token")


def test_list_bo_documents_returns_list(client):
    resp = client.get("/api/adapters/sap-bo/documents?config_id=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "101"
    assert data[0]["folder"] == "/Finance"


def test_list_bo_documents_passes_x_sap_logontoken(client, mock_adapter_service):
    resp = client.get(
        "/api/adapters/sap-bo/documents?config_id=1",
        headers={"X-SAP-LogonToken": "existing-token"},
    )

    assert resp.status_code == 200
    auth_ctx = mock_adapter_service.list_bo_documents.call_args.args[1]
    assert auth_ctx.scheme == "x-sap-logontoken"
    assert auth_ctx.token == "existing-token"


def test_list_bo_reports_for_document(client):
    resp = client.get("/api/adapters/sap-bo/documents/101/reports?config_id=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["name"] == "Page 1"


def test_download_bo_report_returns_bytes(client):
    resp = client.get(
        "/api/adapters/sap-bo/documents/101/reports/1/download?config_id=1&format=pdf"
    )
    assert resp.status_code == 200
    assert resp.content == b"PDF content"
    assert "attachment" in resp.headers.get("content-disposition", "")


def test_download_bo_report_default_format_is_xlsx(client, mock_adapter_service):
    mock_adapter_service.download_bo_report.return_value = b"xlsx data"
    resp = client.get(
        "/api/adapters/sap-bo/documents/101/reports/1/download?config_id=1"
    )
    assert resp.status_code == 200
    call_args = mock_adapter_service.download_bo_report.call_args
    # fmt is passed as a keyword arg; fall back to positional if present
    fmt_value = call_args.kwargs.get("fmt") or (call_args.args[3] if len(call_args.args) > 3 else None)
    assert fmt_value == "xlsx"


# ---------------------------------------------------------------------------
# Automic routes
# ---------------------------------------------------------------------------

def test_lookup_automic_job_returns_status(client):
    resp = client.post("/api/adapters/automic/lookup", json={
        "config_id": 1, "identifier": "MY_JOB", "id_type": "job_name"
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "PASSED"
    assert resp.json()["identifier"] == "MY_JOB"


# ---------------------------------------------------------------------------
# Job creation routes
# ---------------------------------------------------------------------------

def test_create_job_from_bo_report_returns_201(client):
    with patch("api.routes.adapters.JobRepository") as MockRepo:
        MockRepo.return_value.upsert.return_value = MagicMock()
        resp = client.post("/api/adapters/jobs/from-bo-report", json={
            "name": "my_bo_job",
            "title": "Sales Report",
            "doc_id": "101",
            "report_id": "1",
            "key_columns": ["region"],
            "format": "xlsx",
        })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "my_bo_job"
    assert data["job_type"] == "bo_report"
    assert data["params"]["report_id"] == "101"


def test_create_job_from_automic_returns_201(client):
    with patch("api.routes.adapters.JobRepository") as MockRepo:
        MockRepo.return_value.upsert.return_value = MagicMock()
        resp = client.post("/api/adapters/jobs/from-automic", json={
            "name": "nightly_load",
            "job_name": "ETL_NIGHTLY_LOAD",
        })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "nightly_load"
    assert data["job_type"] == "automic_job"
    assert data["params"]["job_name"] == "ETL_NIGHTLY_LOAD"


def test_search_automic_returns_job_list(client, mock_adapter_service):
    from api.schemas import AutomicJobSummary
    mock_adapter_service.search_automic_jobs.return_value = [
        AutomicJobSummary(name="ETL_NIGHTLY", status="ENDED_OK"),
        AutomicJobSummary(name="ETL_WEEKLY", status="ENDED_OK"),
    ]
    resp = client.get("/api/adapters/automic/search?config_id=1&filter=ETL_*")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["name"] == "ETL_NIGHTLY"
    assert data[1]["status"] == "ENDED_OK"


def test_search_automic_missing_filter_returns_422(client):
    resp = client.get("/api/adapters/automic/search?config_id=1")
    assert resp.status_code == 422


def test_search_automic_missing_config_id_returns_422(client):
    resp = client.get("/api/adapters/automic/search?filter=ETL_*")
    assert resp.status_code == 422


def test_automic_client_search_jobs_returns_list():
    from unittest.mock import patch
    from etl_framework.automic.client import AutomicClient
    from etl_framework.config.models import EnvironmentConfig

    env = EnvironmentConfig(
        name="test",
        db_host="host",
        db_password="pass",
        automic_url="http://automic.test",
        automic_user="user",
        automic_password="pass",
    )
    client = AutomicClient(env)
    mock_response = {
        "data": [
            {"name": "ETL_NIGHTLY", "status": "ENDED_OK"},
            {"name": "ETL_WEEKLY", "status": "ACTIVE"},
        ]
    }
    with patch.object(client, "_request", return_value=mock_response):
        result = client.search_jobs("ETL_*")

    assert len(result) == 2
    assert result[0]["name"] == "ETL_NIGHTLY"
    assert result[1]["status"] == "ACTIVE"


def test_automic_client_search_jobs_empty_response():
    from unittest.mock import patch
    from etl_framework.automic.client import AutomicClient
    from etl_framework.config.models import EnvironmentConfig

    env = EnvironmentConfig(
        name="test", db_host="host", db_password="pass",
        automic_url="http://automic.test", automic_user="u", automic_password="p",
    )
    client = AutomicClient(env)
    with patch.object(client, "_request", return_value={}):
        result = client.search_jobs("NONEXISTENT_*")
    assert result == []


def test_automic_client_timeout_raises_contextual_error():
    import requests
    from etl_framework.automic.client import AutomicClient
    from etl_framework.config.models import EnvironmentConfig
    from etl_framework.exceptions import AutomicTimeoutError

    env = EnvironmentConfig(
        name="test",
        db_host="host",
        db_password="pass",
        automic_url="http://automic.test",
        automic_user="u",
        automic_password="p",
        automic_timeout=1,
        automic_max_retries=1,
    )
    client = AutomicClient(env)
    client._session.request = MagicMock(side_effect=requests.Timeout("boom"))

    with pytest.raises(AutomicTimeoutError) as exc_info:
        client.search_jobs("ETL_*")

    exc = exc_info.value
    assert exc.url == "http://automic.test/api/v1/jobs?filter=ETL_*&limit=100"
    assert exc.attempts == 1
    assert exc.timeout_seconds == 1


# ---------------------------------------------------------------------------
# Router is registered in main.py
# ---------------------------------------------------------------------------

def test_bulk_import_automic_jobs_returns_201(client):
    with patch("api.routes.adapters.JobRepository") as MockRepo, \
         patch("api.routes.adapters.AuditService"):
        MockRepo.return_value.upsert.return_value = MagicMock()
        resp = client.post("/api/adapters/jobs/from-automic/bulk", json={
            "config_id": 1,
            "job_names": ["ETL_NIGHTLY", "ETL_WEEKLY"],
        })
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["imported"]) == 2
    assert data["errors"] == {}
    names = [j["name"] for j in data["imported"]]
    assert "etl_nightly" in names
    assert "etl_weekly" in names


def test_bulk_import_automic_sets_job_type(client):
    with patch("api.routes.adapters.JobRepository") as MockRepo, \
         patch("api.routes.adapters.AuditService"):
        MockRepo.return_value.upsert.return_value = MagicMock()
        resp = client.post("/api/adapters/jobs/from-automic/bulk", json={
            "config_id": 1,
            "job_names": ["ETL_NIGHTLY"],
        })
    assert resp.status_code == 201
    assert resp.json()["imported"][0]["job_type"] == "automic_job"
    assert resp.json()["imported"][0]["params"]["job_name"] == "ETL_NIGHTLY"


def test_bulk_import_automic_empty_job_names_returns_422(client):
    resp = client.post("/api/adapters/jobs/from-automic/bulk", json={
        "config_id": 1,
        "job_names": [],
    })
    assert resp.status_code == 422


def test_adapters_prefix_registered(client):
    """Confirms /api/adapters/* is reachable (not 404 routing miss)."""
    resp = client.post("/api/adapters/sap-bo/test", json={"config_id": 1})
    assert resp.status_code != 404
