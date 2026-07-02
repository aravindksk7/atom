"""Tests for BORestClient SAP BO REST API methods."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import pandas as pd

from etl_framework.config.models import EnvironmentConfig


@pytest.fixture
def env_config():
    return EnvironmentConfig(
        name="test",
        db_host="localhost",
        db_password="secret",
        bo_url="http://bo.example.com",
        bo_user="admin",
        bo_password="bopass",
        bo_timeout=30,
    )


@pytest.fixture
def authenticated_client(env_config):
    from etl_framework.sap_bo.client import BORestClient
    client = BORestClient(env_config)
    client._token = "fake-token-123"
    client._session.headers.update({"X-SAP-LogonToken": "fake-token-123"})
    return client


def test_client_applies_proxy_and_ssl_verification_config(env_config):
    from etl_framework.sap_bo.client import BORestClient

    cfg = env_config.model_copy(
        update={
            "bo_proxy_url": "http://proxy.example.com:8080",
            "bo_verify_ssl": False,
        }
    )
    client = BORestClient(cfg)

    assert client._session.proxies["https"] == "http://proxy.example.com:8080"
    assert client._session.proxies["http"] == "http://proxy.example.com:8080"
    assert client._verify_ssl is False


def test_client_requires_url_scheme(env_config):
    from etl_framework.sap_bo.client import BORestClient

    cfg = env_config.model_copy(update={"bo_url": "bo.example.com"})
    with pytest.raises(ValueError, match="must include http:// or https://"):
        BORestClient(cfg)


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------

def test_authenticate_sends_configured_auth_type_for_on_premises_AD(env_config):
    from etl_framework.sap_bo.client import BORestClient

    cfg = env_config.model_copy(update={"bo_auth_type": "secWinAD"})
    client = BORestClient(cfg)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"X-SAP-LogonToken": "tok"}
    with patch.object(client._session, "post", return_value=mock_response) as mock_post:
        client.authenticate()

    sent_payload = mock_post.call_args[1]["json"]
    assert sent_payload["auth"] == "secWinAD"


def test_authenticate_defaults_to_secEnterprise(env_config):
    from etl_framework.sap_bo.client import BORestClient

    client = BORestClient(env_config)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"X-SAP-LogonToken": "tok"}
    with patch.object(client._session, "post", return_value=mock_response) as mock_post:
        client.authenticate()

    assert mock_post.call_args[1]["json"]["auth"] == "secEnterprise"


# ---------------------------------------------------------------------------
# list_documents
# ---------------------------------------------------------------------------

def test_list_documents_returns_list_of_dicts(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "documents": [
            {"id": "101", "name": "Sales Report", "folder": "/Finance"},
            {"id": "102", "name": "Inventory Daily", "folder": "/Operations"},
        ]
    }
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        docs = authenticated_client.list_documents()
    assert len(docs) == 2
    assert docs[0]["id"] == "101"
    assert docs[0]["name"] == "Sales Report"
    assert docs[0]["folder"] == "/Finance"


def test_list_documents_empty_returns_empty_list(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"documents": []}
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        docs = authenticated_client.list_documents()
    assert docs == []


def test_list_documents_http_error_raises(authenticated_client):
    from etl_framework.exceptions import BOAPIError
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with pytest.raises(BOAPIError):
            authenticated_client.list_documents()


# ---------------------------------------------------------------------------
# list_reports
# ---------------------------------------------------------------------------

def test_list_reports_returns_reports_for_document(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "reports": [
            {"id": "1", "name": "Page 1", "reportIndex": 0},
            {"id": "2", "name": "Summary", "reportIndex": 1},
        ]
    }
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        reports = authenticated_client.list_reports("101")
    assert len(reports) == 2
    assert reports[0]["id"] == "1"
    assert reports[1]["name"] == "Summary"


def test_list_reports_calls_correct_endpoint(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"reports": []}
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        authenticated_client.list_reports("MY_DOC_99")
    called_url = mock_get.call_args[0][0]
    assert "MY_DOC_99" in called_url
    assert "reports" in called_url


def test_list_reports_404_raises(authenticated_client):
    from etl_framework.exceptions import ReportNotFoundError
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "Not found"
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with pytest.raises(ReportNotFoundError):
            authenticated_client.list_reports("MISSING_DOC")


# ---------------------------------------------------------------------------
# download_report
# ---------------------------------------------------------------------------

def test_download_report_pdf_returns_bytes(authenticated_client):
    fake_pdf = b"%PDF-1.4 fake content"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = fake_pdf
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        result = authenticated_client.download_report("101", "1", "pdf")
    assert result == fake_pdf


def test_download_report_xlsx_sends_correct_accept_header(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"PK fake xlsx"
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        authenticated_client.download_report("101", "1", "xlsx")
    headers_sent = mock_get.call_args[1].get("headers", {}) or mock_get.call_args[0][1] if len(mock_get.call_args[0]) > 1 else {}
    # Accept header must be xlsx MIME
    accept = mock_get.call_args[1].get("headers", {}).get("Accept", "")
    assert "spreadsheetml" in accept or "openxmlformats" in accept


def test_download_report_csv_sends_csv_accept_header(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"id,name\n1,foo"
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        authenticated_client.download_report("101", "1", "csv")
    accept = mock_get.call_args[1].get("headers", {}).get("Accept", "")
    assert "csv" in accept.lower()


def test_download_report_calls_content_endpoint(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"data"
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        authenticated_client.download_report("DOC1", "RPT2", "pdf")
    called_url = mock_get.call_args[0][0]
    assert "DOC1" in called_url
    assert "RPT2" in called_url
    assert "content" in called_url


def test_download_report_http_error_raises(authenticated_client):
    from etl_framework.exceptions import BOAPIError
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with pytest.raises(BOAPIError):
            authenticated_client.download_report("101", "1", "pdf")
