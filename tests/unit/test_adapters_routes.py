"""Tests for /api/adapters routes."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import (
    AdapterTestOut, BODocOut, BOReportOut,
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


def test_list_bo_documents_returns_list(client):
    resp = client.get("/api/adapters/sap-bo/documents?config_id=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "101"
    assert data[0]["folder"] == "/Finance"


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


# ---------------------------------------------------------------------------
# Router is registered in main.py
# ---------------------------------------------------------------------------

def test_adapters_prefix_registered(client):
    """Confirms /api/adapters/* is reachable (not 404 routing miss)."""
    resp = client.post("/api/adapters/sap-bo/test", json={"config_id": 1})
    assert resp.status_code != 404
