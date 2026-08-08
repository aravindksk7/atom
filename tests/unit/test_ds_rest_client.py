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
    assert sent_payload == {"userName": "admin", "password": "dspass", "authType": "secEnterprise"}


def test_login_sends_configured_auth_type(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    cfg = env_config.model_copy(update={"ds_auth_type": "secLDAP"})
    client = DSRestClient(cfg)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"X-DS-SessionToken": "tok"}
    with patch.object(client._session, "post", return_value=mock_response) as mock_post:
        client.login()

    sent_payload = mock_post.call_args[1]["json"]
    assert sent_payload["authType"] == "secLDAP"


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


# ---------------------------------------------------------------------------
# get_job_status / wait_for_completion
# ---------------------------------------------------------------------------

from etl_framework.runner.state import TestStatus


@pytest.mark.parametrize("raw_status,expected", [
    ("Completed", TestStatus.PASSED),
    ("completed", TestStatus.PASSED),
    ("Success", TestStatus.PASSED),
    ("Error", TestStatus.FAILED),
    ("Failed", TestStatus.FAILED),
    ("Cancelled", TestStatus.FAILED),
    ("Running", TestStatus.RUNNING),
    ("Pending", TestStatus.RUNNING),
    ("Queued", TestStatus.RUNNING),
])
def test_get_job_status_maps_known_statuses(authenticated_client, raw_status, expected):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "run-42", "status": raw_status}
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        status = authenticated_client.get_job_status("run-42")

    assert status == expected
    called_url = mock_get.call_args[0][0]
    assert called_url == "http://ds.example.com/BatchJob/DS_REPO/status/run-42"


def test_get_job_status_uses_repository_override(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "run-42", "status": "Completed"}
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        authenticated_client.get_job_status("run-42", repository="OTHER_REPO")

    called_url = mock_get.call_args[0][0]
    assert called_url == "http://ds.example.com/BatchJob/OTHER_REPO/status/run-42"


def test_get_job_status_treats_unrecognized_status_as_running(authenticated_client, caplog):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "run-42", "status": "SomeNewDSStatus"}
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with caplog.at_level("WARNING"):
            status = authenticated_client.get_job_status("run-42")

    assert status == TestStatus.RUNNING
    assert "SomeNewDSStatus" in caplog.text


def test_get_job_status_raises_ds_api_error_on_http_failure(authenticated_client):
    from etl_framework.exceptions import DSAPIError

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "server error"
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with pytest.raises(DSAPIError):
            authenticated_client.get_job_status("run-42")


def test_wait_for_completion_returns_immediately_on_success(authenticated_client):
    with patch.object(authenticated_client, "get_job_status", return_value=TestStatus.PASSED) as mock_get:
        status = authenticated_client.wait_for_completion("run-42", timeout_s=5, poll_interval_s=0.01)

    assert status == TestStatus.PASSED
    mock_get.assert_called_once_with("run-42", repository=None)


def test_wait_for_completion_polls_until_terminal_status(authenticated_client):
    with patch.object(
        authenticated_client, "get_job_status",
        side_effect=[TestStatus.RUNNING, TestStatus.RUNNING, TestStatus.PASSED],
    ) as mock_get:
        status = authenticated_client.wait_for_completion("run-42", timeout_s=5, poll_interval_s=0.01)

    assert status == TestStatus.PASSED
    assert mock_get.call_count == 3


def test_wait_for_completion_raises_timeout_error_when_never_terminal(authenticated_client):
    with patch.object(authenticated_client, "get_job_status", return_value=TestStatus.RUNNING):
        with pytest.raises(TimeoutError, match="run-42"):
            authenticated_client.wait_for_completion("run-42", timeout_s=0.05, poll_interval_s=0.01)


def test_wait_for_completion_passes_repository_override_through(authenticated_client):
    with patch.object(authenticated_client, "get_job_status", return_value=TestStatus.PASSED) as mock_get:
        authenticated_client.wait_for_completion("run-42", repository="OTHER_REPO", timeout_s=5, poll_interval_s=0.01)

    mock_get.assert_called_once_with("run-42", repository="OTHER_REPO")
