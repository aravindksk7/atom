"""Tests for AdapterService."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, date

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
# list_bo_document_ids_with_runs_on
# ---------------------------------------------------------------------------

def test_list_bo_document_ids_with_runs_on_returns_supported_result(service):
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.list_document_ids_with_runs_on.return_value = ["500", "501"]
        result = service.list_bo_document_ids_with_runs_on(config_id=1, day=date(2026, 7, 20))
    assert result.supported is True
    assert result.document_ids == ["500", "501"]
    MockClient.return_value.list_document_ids_with_runs_on.assert_called_once_with(date(2026, 7, 20))


def test_list_bo_document_ids_with_runs_on_reports_unsupported_when_client_returns_none(service):
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.list_document_ids_with_runs_on.return_value = None
        result = service.list_bo_document_ids_with_runs_on(config_id=1, day=date(2026, 7, 20))
    assert result.supported is False
    assert result.document_ids == []


def test_list_bo_document_ids_with_runs_on_404_config_raises(service, mock_config_repo):
    mock_config_repo.get.return_value = None
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        service.list_bo_document_ids_with_runs_on(config_id=99, day=date(2026, 7, 20))
    assert exc_info.value.status_code == 404


def test_list_bo_document_ids_with_runs_on_wraps_errors_as_502(service):
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.list_document_ids_with_runs_on.side_effect = Exception("boom")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            service.list_bo_document_ids_with_runs_on(config_id=1, day=date(2026, 7, 20))
    assert exc_info.value.status_code == 502


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


def test_download_bo_report_answers_parameters_before_downloading(monkeypatch):
    from api.services.adapter_service import AdapterService
    calls = []
    fake = MagicMock()
    fake.answer_document_parameters.side_effect = lambda *a, **k: calls.append("answer")
    fake.download_report.side_effect = lambda *a, **k: calls.append("download") or b"XLSXBYTES"

    svc = AdapterService(MagicMock())
    monkeypatch.setattr(svc, "_get_env_config", lambda cid: MagicMock(bo_auth_type="secEnterprise"))
    monkeypatch.setattr(svc, "_client_for_auth", lambda env, auth: fake)
    monkeypatch.setattr(svc, "_authenticate_if_needed", lambda c, a: None)

    out = svc.download_bo_report(
        1, "124267", "R1", "xlsx", auth=None,
        parameters=[{"id": 0, "type": "DateTime", "value": "2026-06-02"}],
        timezone="Etc/GMT-1",
    )
    assert out == b"XLSXBYTES"
    assert calls == ["answer", "download"]  # answer strictly before download
    built = fake.answer_document_parameters.call_args[0][1]
    assert built[0]["value"] == "2026-06-01T23:00:00.000Z"  # tz-converted


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


# ---------------------------------------------------------------------------
# REST API endpoints - response artifacts
# ---------------------------------------------------------------------------

def _api_saved_config():
    """A saved config carrying one resolvable api_endpoints entry."""
    cfg = _make_saved_config()
    cfg.config_json = {
        **cfg.config_json,
        "api_endpoints": {"orders": {"base_url": "https://api.example.com/orders"}},
    }
    return cfg


def _sentinel_sink(raw_bytes, page_number, response):  # pragma: no cover - never invoked
    return None


def _assert_adhoc_dest(dest):
    """The sink must write into a direct child of UPLOAD_ROOT named adhoc_*."""
    from api.services.upload_store import UPLOAD_ROOT

    assert dest.parent == UPLOAD_ROOT
    assert dest.name.startswith("adhoc_")


def test_test_api_endpoint_sinks_responses_to_adhoc_dir(service, mock_config_repo):
    mock_config_repo.get.return_value = _api_saved_config()

    with patch("api.services.adapter_service.APIEndpointClient") as MockClient, \
         patch("api.services.adapter_service.build_api_response_sink") as mock_build_sink:
        mock_build_sink.return_value = _sentinel_sink
        result = service.test_api_endpoint(config_id=7, endpoint_name="orders")

    assert result.ok is True
    kwargs = MockClient.return_value.fetch_dataframe.call_args.kwargs
    assert kwargs["max_pages"] == 1
    assert kwargs["on_response"] is _sentinel_sink
    dest, endpoint_name = mock_build_sink.call_args.args
    assert endpoint_name == "orders"
    _assert_adhoc_dest(dest)


def test_preview_api_endpoint_sinks_responses_to_adhoc_dir(service, mock_config_repo):
    import pandas as pd

    mock_config_repo.get.return_value = _api_saved_config()

    with patch("api.services.adapter_service.APIEndpointClient") as MockClient, \
         patch("api.services.adapter_service.build_api_response_sink") as mock_build_sink:
        MockClient.return_value.fetch_dataframe.return_value = pd.DataFrame({"id": [1, 2]})
        mock_build_sink.return_value = _sentinel_sink
        out = service.preview_api_endpoint(config_id=7, endpoint_name="orders", limit=10)

    assert out["columns"] == ["id"]
    kwargs = MockClient.return_value.fetch_dataframe.call_args.kwargs
    assert kwargs["max_pages"] == 1
    assert kwargs["on_response"] is _sentinel_sink
    dest, endpoint_name = mock_build_sink.call_args.args
    assert endpoint_name == "orders"
    _assert_adhoc_dest(dest)
