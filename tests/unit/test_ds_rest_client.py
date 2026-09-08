"""Tests for DSRestClient SAP Data Services Administrator API methods.

login/logout and trigger_job are modeled on live DevTools captures against
a real on-prem instance (2026-09-03) -- see each test section's comment for
what's confirmed vs. still-unverified. get_job_status/wait_for_completion
below are still the original best-effort guess, not verified against a live
server yet -- same situation etl_framework/sap_bo/client.py's biprws quirks
were in before they were discovered and documented over time.
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
    assert called_url == "http://ds.example.com/logon"
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
#
# Modeled on a live DevTools capture (2026-09-03) of the real "Execute Batch
# Job" form submission: a legacy servlet form POST
# (application/x-www-form-urlencoded to /DataServices/servlet/
# AwBatchJobExecute), not the JSON REST call originally assumed. See
# DSRestClient.trigger_job's docstring for what's still unverified
# (X-CSRF-TOKEN, JOB_SERVER sourcing, response shape).
# ---------------------------------------------------------------------------

def test_trigger_job_posts_to_execute_servlet_using_default_repository(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html>submitted</html>"
    with patch("uuid.uuid4", return_value="fixed-guid"), \
         patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        run_id = authenticated_client.trigger_job("DS_NIGHTLY_LOAD")

    assert run_id == "fixed-guid"
    called_url = mock_post.call_args[0][0]
    assert called_url == "http://ds.example.com/DataServices/servlet/AwBatchJobExecute"
    sent_form = mock_post.call_args[1]["data"]
    assert sent_form["JobName"] == "DS_NIGHTLY_LOAD"
    assert sent_form["REPOSITORY_NAME"] == "DS_REPO"
    assert sent_form["ACTION_REQUEST"] == "Execute"
    assert sent_form["GUID"] == "fixed-guid"
    assert sent_form["JOB_SERVER"] == "DS.EXAMPLE.COM:3500"


def test_trigger_job_ignores_ds_url_path_and_uses_server_origin(env_config):
    """Regression test for the 2026-09-08 live 404: this on-prem instance's
    ds_url is "https://qetl111/DataServices/launch/" -- login is genuinely
    nested under "/launch", but AwBatchJobExecute is a sibling servlet at
    the origin. trigger_job must resolve against scheme+host only, not
    ds_url's configured path, or it doubles "/DataServices" and pulls in
    the extra "/launch" segment."""
    from etl_framework.sap_ds.client import DSRestClient

    cfg = env_config.model_copy(update={"ds_url": "https://qetl111/DataServices/launch/"})
    client = DSRestClient(cfg)
    client._token = "fake-ds-token-123"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html>submitted</html>"
    with patch.object(client._session, "post", return_value=mock_response) as mock_post:
        client.trigger_job("DS_NIGHTLY_LOAD")

    called_url = mock_post.call_args[0][0]
    assert called_url == "https://qetl111/DataServices/servlet/AwBatchJobExecute"


def test_trigger_job_uses_explicit_repository_override(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html>submitted</html>"
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        authenticated_client.trigger_job("DS_NIGHTLY_LOAD", repository="OTHER_REPO")

    sent_form = mock_post.call_args[1]["data"]
    assert sent_form["REPOSITORY_NAME"] == "OTHER_REPO"


def test_trigger_job_flattens_job_params_into_form_body(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html>submitted</html>"
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        authenticated_client.trigger_job("DS_NIGHTLY_LOAD", job_params={"$G_RUN_DATE": "2026-07-24"})

    sent_form = mock_post.call_args[1]["data"]
    assert sent_form["$G_RUN_DATE"] == "2026-07-24"


def test_trigger_job_authenticates_first_if_no_token(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    login_response = MagicMock()
    login_response.status_code = 200
    login_response.headers = {"X-DS-SessionToken": "tok"}
    trigger_response = MagicMock()
    trigger_response.status_code = 200
    trigger_response.text = "<html>submitted</html>"
    with patch.object(client._session, "post", side_effect=[login_response, trigger_response]):
        run_id = client.trigger_job("DS_NIGHTLY_LOAD")

    assert run_id
    assert client._token == "tok"


def test_trigger_job_raises_ds_api_error_on_http_failure(authenticated_client):
    from etl_framework.exceptions import DSAPIError

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "job not found"
    with patch.object(authenticated_client._session, "post", return_value=mock_response):
        with pytest.raises(DSAPIError):
            authenticated_client.trigger_job("does-not-exist")


def test_trigger_job_raises_value_error_when_no_repository_available(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    cfg = env_config.model_copy(update={"ds_repository": ""})
    client = DSRestClient(cfg)
    client._token = "tok"
    with pytest.raises(ValueError, match="repository"):
        client.trigger_job("DS_NIGHTLY_LOAD")


# ---------------------------------------------------------------------------
# get_job_status / wait_for_completion
#
# Complete rework (2026-09-08) modeled on live captures of the Management
# Console's own "Batch Job Status" (AwBatchJobHistory) and "Job Trace Log"
# (AwBatchJobLogs) pages -- see DSRestClient.get_job_status's docstring for
# what's confirmed live vs. still inferred (only circgreen.gif -> PASSED is
# directly observed; circred.gif -> FAILED is a traffic-light inference).
# ---------------------------------------------------------------------------

from etl_framework.runner.state import TestStatus


def _history_row(job_name: str, icon: str, object_key: str = "306") -> str:
    """A minimal single-row AwBatchJobHistory table body, matching the real
    structure captured live: a class=tablerow (lowercase) <TR>, a status
    icon cell, and a bare job-name cell with no other attributes."""
    return f"""
    <TABLE CLASS="JCActaHTMLTableSortable">
    <TBODY>
    <TR  class=tablerow ><TD ><input type="checkbox" Name="CBG1" Value= "{object_key}" ></TD>
    <TD  class="cell" nowrap align=CENTER><IMG alt='' id=IMG1 align=absmiddle src='../images/circ{icon}.gif'></TD>
    <TD  class="cell" nowrap>{job_name}</TD>
    <TD  class="cell" nowrap><a HREF=AwBatchJobLogs?ObjectKey={object_key}&JobName={job_name}>Trace</a></TD>
    </TR>
    </TBODY>
    </TABLE>
    """


def _history_response(job_name: str, icon: str, object_key: str = "306") -> MagicMock:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = _history_row(job_name, icon, object_key)
    return mock_response


def test_get_job_status_maps_green_icon_to_passed(authenticated_client):
    mock_response = _history_response("DS_NIGHTLY_LOAD", "green")
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        status = authenticated_client.get_job_status("DS_NIGHTLY_LOAD", run_id="some-guid")

    assert status == TestStatus.PASSED
    called_url = mock_get.call_args[0][0]
    assert called_url == "http://ds.example.com/DataServices/servlet/AwBatchJobHistory"
    called_params = mock_get.call_args[1]["params"]
    assert called_params["JobName"] == "DS_NIGHTLY_LOAD"
    assert called_params["REPOSITORY_NAME"] == "DS_REPO"
    assert called_params["GROUP_TIME_RADIO"] == "LAST_EXECUTION_RADIO"


def test_get_job_status_maps_red_icon_to_failed(authenticated_client):
    mock_response = _history_response("DS_NIGHTLY_LOAD", "red")
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        status = authenticated_client.get_job_status("DS_NIGHTLY_LOAD")

    assert status == TestStatus.FAILED


def test_get_job_status_uses_repository_override(authenticated_client):
    mock_response = _history_response("DS_NIGHTLY_LOAD", "green")
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        authenticated_client.get_job_status("DS_NIGHTLY_LOAD", repository="OTHER_REPO")

    called_params = mock_get.call_args[1]["params"]
    assert called_params["REPOSITORY_NAME"] == "OTHER_REPO"


def test_get_job_status_ignores_ds_url_path_and_uses_server_origin(env_config):
    """Regression test for the 2026-09-08 live 404: same bug class as
    trigger_job's -- ds_url "https://qetl111/DataServices/launch/" must not
    have its "/launch" path carried into the history check URL."""
    from etl_framework.sap_ds.client import DSRestClient

    cfg = env_config.model_copy(update={"ds_url": "https://qetl111/DataServices/launch/"})
    client = DSRestClient(cfg)
    client._token = "fake-ds-token-123"
    mock_response = _history_response("DS_NIGHTLY_LOAD", "green")
    with patch.object(client._session, "get", return_value=mock_response) as mock_get:
        client.get_job_status("DS_NIGHTLY_LOAD")

    called_url = mock_get.call_args[0][0]
    assert called_url == "https://qetl111/DataServices/servlet/AwBatchJobHistory"


def test_get_job_status_treats_unrecognized_icon_as_running(authenticated_client, caplog):
    mock_response = _history_response("DS_NIGHTLY_LOAD", "yellow")
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with caplog.at_level("WARNING"):
            status = authenticated_client.get_job_status("DS_NIGHTLY_LOAD")

    assert status == TestStatus.RUNNING
    assert "yellow" in caplog.text


def test_get_job_status_treats_no_matching_row_as_running(authenticated_client):
    """Job not in the history listing yet (e.g. just triggered, DS hasn't
    registered the run) -- keep polling rather than erroring."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = _history_row("SOME_OTHER_JOB", "green")
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        status = authenticated_client.get_job_status("DS_NIGHTLY_LOAD")

    assert status == TestStatus.RUNNING


def test_get_job_status_raises_ds_api_error_on_http_failure(authenticated_client):
    from etl_framework.exceptions import DSAPIError

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "server error"
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with pytest.raises(DSAPIError):
            authenticated_client.get_job_status("DS_NIGHTLY_LOAD")


def test_wait_for_completion_returns_immediately_on_success(authenticated_client):
    with patch.object(authenticated_client, "get_job_status", return_value=TestStatus.PASSED) as mock_get:
        status = authenticated_client.wait_for_completion(
            "DS_NIGHTLY_LOAD", run_id="some-guid", timeout_s=5, poll_interval_s=0.01,
        )

    assert status == TestStatus.PASSED
    mock_get.assert_called_once_with("DS_NIGHTLY_LOAD", run_id="some-guid", repository=None)


def test_wait_for_completion_polls_until_terminal_status(authenticated_client):
    with patch.object(
        authenticated_client, "get_job_status",
        side_effect=[TestStatus.RUNNING, TestStatus.RUNNING, TestStatus.PASSED],
    ) as mock_get:
        status = authenticated_client.wait_for_completion("DS_NIGHTLY_LOAD", timeout_s=5, poll_interval_s=0.01)

    assert status == TestStatus.PASSED
    assert mock_get.call_count == 3


def test_wait_for_completion_raises_timeout_error_when_never_terminal(authenticated_client):
    with patch.object(authenticated_client, "get_job_status", return_value=TestStatus.RUNNING):
        with pytest.raises(TimeoutError, match="DS_NIGHTLY_LOAD"):
            authenticated_client.wait_for_completion("DS_NIGHTLY_LOAD", timeout_s=0.05, poll_interval_s=0.01)


def test_wait_for_completion_passes_repository_override_through(authenticated_client):
    with patch.object(authenticated_client, "get_job_status", return_value=TestStatus.PASSED) as mock_get:
        authenticated_client.wait_for_completion(
            "DS_NIGHTLY_LOAD", repository="OTHER_REPO", timeout_s=5, poll_interval_s=0.01,
        )

    mock_get.assert_called_once_with("DS_NIGHTLY_LOAD", run_id=None, repository="OTHER_REPO")
