"""Tests for DSRestClient SAP Data Services Administrator API methods.

Endpoint paths, header names, and payload shapes here are best-effort,
modeled after commonly documented SAP DS Administrator conventions -- not
verified against a live SAP DS instance. Verify and adjust when a real
server is available, the same way etl_framework/sap_bo/client.py's biprws
quirks were discovered and documented over time.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from etl_framework.config.models import EnvironmentConfig


@pytest.fixture
def env_config():
    return EnvironmentConfig(
        name="test",
        db_host="localhost",
        db_password="secret",
        ds_url="http://ds.example.com",
        ds_user="admin",
        ds_password="dspass",
        ds_repository="DS_REPO",
        ds_timeout=30,
    )


@pytest.fixture
def authenticated_client(env_config):
    from etl_framework.sap_ds.client import DSRestClient
    client = DSRestClient(env_config)
    client._token = "fake-ds-token-123"
    client._session.headers.update({"X-DS-SessionToken": "fake-ds-token-123"})
    return client


def test_client_requires_url_scheme(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    cfg = env_config.model_copy(update={"ds_url": "ds.example.com"})
    with pytest.raises(ValueError, match="must include http:// or https://"):
        DSRestClient(cfg)


def test_client_applies_proxy_and_ssl_verification_config(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    cfg = env_config.model_copy(
        update={"ds_proxy_url": "http://proxy.example.com:8080", "ds_verify_ssl": False}
    )
    client = DSRestClient(cfg)

    assert client._session.proxies["https"] == "http://proxy.example.com:8080"
    assert client._session.proxies["http"] == "http://proxy.example.com:8080"
    assert client._verify_ssl is False


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

def test_login_posts_credentials_and_stores_token(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"X-DS-SessionToken": "tok"}
    with patch.object(client._session, "post", return_value=mock_response) as mock_post:
        token = client.login()

    assert token == "tok"
    assert client._token == "tok"
    called_url = mock_post.call_args[0][0]
    assert called_url == "http://ds.example.com/Login"
    sent_payload = mock_post.call_args[1]["json"]
    assert sent_payload == {"userName": "admin", "password": "dspass"}


def test_login_raises_ds_api_error_on_http_failure(env_config):
    from etl_framework.exceptions import DSAPIError
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "invalid credentials"
    with patch.object(client._session, "post", return_value=mock_response):
        with pytest.raises(DSAPIError) as exc_info:
            client.login()
    assert exc_info.value.http_status == 401


def test_logout_posts_logoff_and_clears_token(authenticated_client):
    authenticated_client._owns_token = True
    mock_response = MagicMock()
    mock_response.status_code = 200
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        authenticated_client.logout()

    mock_post.assert_called_once()
    assert authenticated_client._token is None
    assert "X-DS-SessionToken" not in authenticated_client._session.headers


def test_logout_is_noop_when_not_authenticated(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    with patch.object(client._session, "post") as mock_post:
        client.logout()
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# trigger_job
# ---------------------------------------------------------------------------

def test_trigger_job_posts_to_execute_endpoint_using_default_repository(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "run-42"}
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        run_id = authenticated_client.trigger_job("DS_NIGHTLY_LOAD")

    assert run_id == "run-42"
    called_url = mock_post.call_args[0][0]
    assert called_url == "http://ds.example.com/BatchJob/DS_REPO/DS_NIGHTLY_LOAD/Execute"


def test_trigger_job_uses_explicit_repository_override(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "run-43"}
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        authenticated_client.trigger_job("DS_NIGHTLY_LOAD", repository="OTHER_REPO")

    called_url = mock_post.call_args[0][0]
    assert called_url == "http://ds.example.com/BatchJob/OTHER_REPO/DS_NIGHTLY_LOAD/Execute"


def test_trigger_job_sends_job_params_as_json_body(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "run-44"}
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        authenticated_client.trigger_job("DS_NIGHTLY_LOAD", job_params={"$G_RUN_DATE": "2026-07-24"})

    assert mock_post.call_args[1]["json"] == {"$G_RUN_DATE": "2026-07-24"}


def test_trigger_job_authenticates_first_if_no_token(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    login_response = MagicMock()
    login_response.status_code = 200
    login_response.headers = {"X-DS-SessionToken": "tok"}
    trigger_response = MagicMock()
    trigger_response.status_code = 200
    trigger_response.json.return_value = {"id": "run-1"}
    with patch.object(client._session, "post", side_effect=[login_response, trigger_response]):
        run_id = client.trigger_job("DS_NIGHTLY_LOAD")

    assert run_id == "run-1"
    assert client._token == "tok"


def test_trigger_job_raises_ds_api_error_on_http_failure(authenticated_client):
    from etl_framework.exceptions import DSAPIError

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "job not found"
    with patch.object(authenticated_client._session, "post", return_value=mock_response):
        with pytest.raises(DSAPIError):
            authenticated_client.trigger_job("does-not-exist")


def test_trigger_job_raises_ds_api_error_when_response_has_no_id(authenticated_client):
    from etl_framework.exceptions import DSAPIError

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}
    with patch.object(authenticated_client._session, "post", return_value=mock_response):
        with pytest.raises(DSAPIError):
            authenticated_client.trigger_job("DS_NIGHTLY_LOAD")


def test_trigger_job_raises_value_error_when_no_repository_available(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    cfg = env_config.model_copy(update={"ds_repository": ""})
    client = DSRestClient(cfg)
    client._token = "tok"
    with pytest.raises(ValueError, match="repository"):
        client.trigger_job("DS_NIGHTLY_LOAD")
