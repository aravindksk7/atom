"""Tests for AdapterService."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from etl_framework.config.models import EnvironmentConfig
from etl_framework.repository.models import SavedConfig


def _make_saved_config(bo_url="http://bo.example.com"):
    cfg = MagicMock(spec=SavedConfig)
    cfg.env_name = "dev"
    cfg.config_json = {
        "db_host": "localhost",
        "db_password": "secret",
        "bo_url": bo_url,
        "bo_user": "admin",
        "bo_password": "pass",
    }
    return cfg


@pytest.fixture
def mock_config_repo():
    repo = MagicMock()
    repo.get.return_value = _make_saved_config()
    return repo


@pytest.fixture
def service(mock_config_repo):
    from api.services.adapter_service import AdapterService
    return AdapterService(mock_config_repo)


# ---------------------------------------------------------------------------
# test_bo_connection
# ---------------------------------------------------------------------------

def test_test_bo_connection_ok(service):
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value = mock_client
        result = service.test_bo_connection(config_id=1)
    assert result.ok is True
    assert "success" in result.message.lower()
    mock_client.authenticate.assert_called_once()


def test_test_bo_connection_failure_returns_ok_false(service):
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.authenticate.side_effect = Exception("Auth failed")
        result = service.test_bo_connection(config_id=1)
    assert result.ok is False
    assert "Auth failed" in result.message


def test_test_bo_connection_network_error_mentions_backend_route(service):
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.authenticate.side_effect = RuntimeError(
            "Max retries exceeded with url: /biprws (host='bo.example.com', port=443)"
        )
        result = service.test_bo_connection(config_id=1)

    assert result.ok is False
    assert "bo.example.com:443" in result.message
    assert "application server" in result.message
    assert "proxy" in result.message


def test_test_bo_connection_404_config_raises(service, mock_config_repo):
    mock_config_repo.get.return_value = None
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        service.test_bo_connection(config_id=99)
    assert exc_info.value.status_code == 404


def test_friendly_bo_error_includes_response_body():
    from api.services.adapter_service import _friendly_error
    from etl_framework.exceptions import BOAPIError

    message = _friendly_error(BOAPIError("rpt-sales", 404, '{"error":"report not found"}'))

    assert "SAP BO API error 404" in message
    assert "report not found" in message


def test_friendly_401_error_hints_at_auth_type_when_non_default():
    from api.services.adapter_service import _friendly_error

    message = _friendly_error(Exception("401 Client Error: Unauthorized"), auth_type="secWinAD")

    assert "secWinAD" in message
    assert "auth type" in message.lower()


def test_friendly_401_error_default_message_when_secEnterprise():
    from api.services.adapter_service import _friendly_error

    message = _friendly_error(Exception("401 Client Error: Unauthorized"), auth_type="secEnterprise")

    assert message == "Authentication failed - check username and password"


def test_test_bo_connection_401_with_ad_auth_type_hints_at_configured_type(mock_config_repo, service):
    cfg = _make_saved_config()
    cfg.config_json["bo_auth_type"] = "secWinAD"
    mock_config_repo.get.return_value = cfg
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.authenticate.side_effect = Exception("401 Client Error: Unauthorized")
        result = service.test_bo_connection(config_id=1)

    assert result.ok is False
    assert "secWinAD" in result.message


# ---------------------------------------------------------------------------
# list_bo_documents
# ---------------------------------------------------------------------------

def test_list_bo_documents_401_with_ad_auth_type_hints_at_configured_type(mock_config_repo, service):
    cfg = _make_saved_config()
    cfg.config_json["bo_auth_type"] = "secWinAD"
    mock_config_repo.get.return_value = cfg
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.authenticate.side_effect = Exception("401 Client Error: Unauthorized")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            service.list_bo_documents(config_id=1)

    assert "secWinAD" in exc_info.value.detail


def test_list_bo_documents_returns_bo_doc_out_list(service):
    raw_docs = [
        {"id": "101", "name": "Sales", "folder": "/Finance"},
        {"id": "102", "name": "Inventory", "folder": "/Ops"},
    ]
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.list_documents.return_value = raw_docs
        docs = service.list_bo_documents(config_id=1)
    assert len(docs) == 2
    assert docs[0].id == "101"
    assert docs[1].folder == "/Ops"


def test_list_bo_documents_empty(service):
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.list_documents.return_value = []
        docs = service.list_bo_documents(config_id=1)
    assert docs == []


# ---------------------------------------------------------------------------
# list_bo_reports
# ---------------------------------------------------------------------------

def test_list_bo_reports_returns_bo_report_out_list(service):
    raw = [{"id": "1", "name": "Tab 1", "reportIndex": 0}]
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.list_reports.return_value = raw
        reports = service.list_bo_reports(config_id=1, doc_id="101")
    assert len(reports) == 1
    assert reports[0].id == "1"
    assert reports[0].report_index == 0


# ---------------------------------------------------------------------------
# download_bo_report
# ---------------------------------------------------------------------------

def test_download_bo_report_returns_bytes(service):
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.download_report.return_value = b"PDF bytes"
        result = service.download_bo_report(1, "101", "1", "pdf")
    assert result == b"PDF bytes"


# ---------------------------------------------------------------------------
# lookup_automic_job
# ---------------------------------------------------------------------------

def test_lookup_automic_job_returns_status_out(service):
    from etl_framework.automic.models import JobStatus
    from etl_framework.runner.state import TestStatus
    mock_status = JobStatus(
        identifier="MY_JOB",
        identifier_type="job_name",
        status=TestStatus.PASSED,
        environment="prod",
        checked_at=datetime.now(timezone.utc),
        raw_response={},
    )
    with patch("api.services.adapter_service.AutomicClient") as MockClient:
        MockClient.return_value.get_status_by_job_name.return_value = mock_status
        result = service.lookup_automic_job(1, "MY_JOB", "job_name")
    assert result.identifier == "MY_JOB"
    assert result.status == "PASSED"


def test_lookup_automic_job_by_run_id(service):
    from etl_framework.automic.models import JobStatus
    from etl_framework.runner.state import TestStatus
    mock_status = JobStatus(
        identifier="run-123",
        identifier_type="run_id",
        status=TestStatus.FAILED,
        environment="dev",
        checked_at=datetime.now(timezone.utc),
        raw_response={},
    )
    with patch("api.services.adapter_service.AutomicClient") as MockClient:
        MockClient.return_value.get_status_by_run_id.return_value = mock_status
        result = service.lookup_automic_job(1, "run-123", "run_id")
    assert result.status == "FAILED"
    MockClient.return_value.get_status_by_run_id.assert_called_once_with("run-123")
