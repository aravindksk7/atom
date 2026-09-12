"""Tests for DSRestClient — SAP Data Services SOAP web service client.

Verified against the live WSDL at
https://qetl111/DataServices/servlet/webservices?ver=2.0
(service DataServices_Server, targetNamespace http://www.businessobjects.com).

Operation contract used here (from that WSDL):
  Logon                (SOAPAction "function=Logon")   LogonRequest{username,password,cms_system,cms_authentication} -> session{SessionID}
  Ping                 (SOAPAction "function=Ping")     Ping_Input{} -> pingVersion
  Run_Batch_Job        (SOAPAction "jobAdmin=Run_Batch_Job")   RunBatchJobRequest{jobName,repoName,...} -> BatchJobResponse{pid,cid,rid,repoName,returnCode?,errorMessage?}
  Get_BatchJob_Status  (SOAPAction "jobAdmin=Get_BatchJob_Status") batchJobStatusRequest{runID,repoName} -> batchJobStatusResponse{returnCode,status}
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from etl_framework.config.models import EnvironmentConfig
from etl_framework.runner.state import TestStatus

DS_NS = "http://www.businessobjects.com"


@pytest.fixture
def env_config():
    return EnvironmentConfig(
        name="test",
        db_host="localhost",
        db_password="secret",
        ds_url="https://ds.example.com/DataServices/servlet/webservices",
        ds_user="admin",
        ds_password="dspass",
        ds_repository="DS_REPO",
        ds_cms_system="cms-host",
        ds_timeout=30,
    )


def _soap(body_inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soapenv:Body>{body_inner}</soapenv:Body></soapenv:Envelope>"
    )


def _mock_soap_response(body_inner: str, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = _soap(body_inner)
    resp.content = resp.text.encode()
    return resp


@pytest.fixture
def authenticated_client(env_config):
    from etl_framework.sap_ds.client import DSRestClient
    client = DSRestClient(env_config)
    client._token = "SESS-123"
    client._owns_token = True
    return client


# ---------------------------------------------------------------------------
# construction / proxy / ssl
# ---------------------------------------------------------------------------

def test_client_requires_url_scheme(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    cfg = env_config.model_copy(update={"ds_url": "ds.example.com"})
    with pytest.raises(ValueError, match="must include http:// or https://"):
        DSRestClient(cfg)


def test_client_ignores_env_proxy_when_no_proxy_configured(env_config, monkeypatch):
    from etl_framework.sap_ds.client import DSRestClient

    monkeypatch.setenv("HTTPS_PROXY", "http://zproxy.example.com:8080")
    monkeypatch.setenv("HTTP_PROXY", "http://zproxy.example.com:8080")
    client = DSRestClient(env_config.model_copy(update={"ds_proxy_url": ""}))
    assert client._session.trust_env is False


def test_client_explicit_proxy_still_wins(env_config, monkeypatch):
    from etl_framework.sap_ds.client import DSRestClient

    monkeypatch.setenv("HTTPS_PROXY", "http://zproxy.example.com:8080")
    client = DSRestClient(
        env_config.model_copy(update={"ds_proxy_url": "http://proxy.example.com:8080"})
    )
    assert client._session.proxies["https"] == "http://proxy.example.com:8080"
    assert client._session.proxies["http"] == "http://proxy.example.com:8080"


def test_client_applies_ssl_verification_config(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config.model_copy(update={"ds_verify_ssl": False}))
    assert client._verify_ssl is False


# ---------------------------------------------------------------------------
# login (Logon SOAP op)
# ---------------------------------------------------------------------------

def test_login_sends_logon_envelope_and_parses_sessionid(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    resp = _mock_soap_response(
        f'<session xmlns="{DS_NS}"><SessionID>SESS-ABC</SessionID></session>'
    )
    with patch.object(client._session, "post", return_value=resp) as mock_post:
        token = client.login()

    assert token == "SESS-ABC"
    assert client._token == "SESS-ABC"
    # Endpoint is the DS webservices URL with the WSDL's ?ver=2.0 query.
    assert mock_post.call_args[0][0] == env_config.ds_url + "?ver=2.0"
    sent = mock_post.call_args[1]
    # SOAPAction header identifies the Logon function.
    headers = sent["headers"]
    assert headers["SOAPAction"] == '"function=Logon"'
    assert "text/xml" in headers["Content-Type"]
    # Body carries the credentials and cms_authentication (auth type).
    body = sent["data"] if "data" in sent else mock_post.call_args[0][1]
    body = body.decode() if isinstance(body, bytes) else body
    assert "<username>admin</username>" in body
    assert "<password>dspass</password>" in body
    assert "<cms_system>cms-host</cms_system>" in body
    assert "<cms_authentication>secEnterprise</cms_authentication>" in body
    assert "LogonRequest" in body
    # Body must be UNQUALIFIED — a namespaced LogonRequest is rejected by the
    # live server. Assert the ServerX types namespace is NOT applied to it.
    assert 'LogonRequest xmlns=' not in body


def test_login_requires_cms_system(env_config):
    """cms_system is mandatory: the live server rejects a Logon without a
    valid CMS host. Missing ds_cms_system must fail fast with a clear error,
    not produce the server's misleading 'requires Username node' fault."""
    from etl_framework.exceptions import DSAPIError
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config.model_copy(update={"ds_cms_system": ""}))
    with pytest.raises(DSAPIError) as exc_info:
        client.login()
    assert "CMS system" in (exc_info.value.response_body or "")


def test_login_sends_configured_auth_type(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config.model_copy(update={"ds_auth_type": "secLDAP"}))
    resp = _mock_soap_response(
        f'<session xmlns="{DS_NS}"><SessionID>x</SessionID></session>'
    )
    with patch.object(client._session, "post", return_value=resp) as mock_post:
        client.login()

    body = mock_post.call_args[1].get("data") or mock_post.call_args[0][1]
    body = body.decode() if isinstance(body, bytes) else body
    assert "<cms_authentication>secLDAP</cms_authentication>" in body


def test_login_raises_on_soap_fault(env_config):
    from etl_framework.exceptions import DSAPIError
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    fault = (
        "<soapenv:Fault xmlns:soapenv='http://schemas.xmlsoap.org/soap/envelope/'>"
        "<faultcode>soapenv:Server</faultcode>"
        "<faultstring>Logon failed: invalid credentials</faultstring>"
        "</soapenv:Fault>"
    )
    resp = _mock_soap_response(fault, status_code=500)
    with patch.object(client._session, "post", return_value=resp):
        with pytest.raises(DSAPIError) as exc_info:
            client.login()
    assert "invalid credentials" in (exc_info.value.response_body or "")


def test_ping_returns_true_on_pingversion(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    client._token = "SESS-1"
    resp = _mock_soap_response(f'<pingVersion xmlns="{DS_NS}">14.2.0</pingVersion>')
    with patch.object(client._session, "post", return_value=resp) as mock_post:
        assert client.ping() is True
    assert mock_post.call_args[1]["headers"]["SOAPAction"] == '"function=Ping"'


def test_logout_sends_logout_and_clears_token(authenticated_client):
    resp = _mock_soap_response("<Logout_Input/>")
    with patch.object(authenticated_client._session, "post", return_value=resp) as mock_post:
        authenticated_client.logout()
    mock_post.assert_called_once()
    assert authenticated_client._token is None


def test_logout_is_noop_when_not_authenticated(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    with patch.object(client._session, "post") as mock_post:
        client.logout()
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# trigger_job (Run_Batch_Job SOAP op) — returns rid as run id
# ---------------------------------------------------------------------------

def test_trigger_job_sends_runbatchjob_and_returns_rid(authenticated_client):
    resp = _mock_soap_response(
        f'<BatchJobResponse xmlns="{DS_NS}"><pid>10</pid><cid>2</cid>'
        f"<rid>4242</rid><repoName>DS_REPO</repoName><returnCode>0</returnCode>"
        f"</BatchJobResponse>"
    )
    with patch.object(authenticated_client._session, "post", return_value=resp) as mock_post:
        run_id = authenticated_client.trigger_job("DS_NIGHTLY_LOAD")

    assert run_id == "4242"
    headers = mock_post.call_args[1]["headers"]
    assert headers["SOAPAction"] == '"jobAdmin=Run_Batch_Job"'
    body = mock_post.call_args[1].get("data") or mock_post.call_args[0][1]
    body = body.decode() if isinstance(body, bytes) else body
    assert "<jobName>DS_NIGHTLY_LOAD</jobName>" in body
    assert "<repoName>DS_REPO</repoName>" in body
    # SessionID travels in the SOAP header.
    assert "SESS-123" in body


def test_trigger_job_uses_repository_override(authenticated_client):
    resp = _mock_soap_response(
        f'<BatchJobResponse xmlns="{DS_NS}"><pid>1</pid><cid>1</cid>'
        f"<rid>7</rid><repoName>OTHER_REPO</repoName></BatchJobResponse>"
    )
    with patch.object(authenticated_client._session, "post", return_value=resp) as mock_post:
        authenticated_client.trigger_job("J", repository="OTHER_REPO")
    body = mock_post.call_args[1].get("data") or mock_post.call_args[0][1]
    body = body.decode() if isinstance(body, bytes) else body
    assert "<repoName>OTHER_REPO</repoName>" in body


def test_trigger_job_quotes_string_global_variable_as_bods_literal(authenticated_client):
    # SAP DS's Run_Batch_Job evaluates each global variable value as a BODS
    # expression. A varchar variable needs a quoted string literal -- the
    # live console always sends one (verified via HAR capture: it posts
    # $G_BUSINESS_DATE='31-Jul-2026', not bare 31-Jul-2026). An unquoted
    # date is not a valid expression, so SAP DS silently falls back to the
    # variable's compiled default instead of erroring -- this is the exact
    # "custom variable didn't get passed" bug.
    resp = _mock_soap_response(
        f'<BatchJobResponse xmlns="{DS_NS}"><pid>1</pid><cid>1</cid>'
        f"<rid>1</rid><repoName>DS_REPO</repoName></BatchJobResponse>"
    )
    with patch.object(authenticated_client._session, "post", return_value=resp) as mock_post:
        authenticated_client.trigger_job(
            "J", job_params={"$G_BUSINESS_DATE": "31-Jul-2026"},
        )
    body = mock_post.call_args[1].get("data") or mock_post.call_args[0][1]
    body = body.decode() if isinstance(body, bytes) else body
    assert '<variable name="$G_BUSINESS_DATE">\'31-Jul-2026\'</variable>' in body


def test_trigger_job_leaves_numeric_global_variable_unquoted(authenticated_client):
    # BODS int/float variables expect a bare numeric literal -- quoting a
    # numeric value would make it a string expression and fail type
    # conversion on the SAP DS side.
    resp = _mock_soap_response(
        f'<BatchJobResponse xmlns="{DS_NS}"><pid>1</pid><cid>1</cid>'
        f"<rid>1</rid><repoName>DS_REPO</repoName></BatchJobResponse>"
    )
    with patch.object(authenticated_client._session, "post", return_value=resp) as mock_post:
        authenticated_client.trigger_job(
            "J", job_params={"$G_MONTHS_TO_SEND_OLD_RC": "5", "$G_RATIO": "-3.5"},
        )
    body = mock_post.call_args[1].get("data") or mock_post.call_args[0][1]
    body = body.decode() if isinstance(body, bytes) else body
    assert '<variable name="$G_MONTHS_TO_SEND_OLD_RC">5</variable>' in body
    assert '<variable name="$G_RATIO">-3.5</variable>' in body


def test_trigger_job_escapes_embedded_single_quote_in_string_variable(authenticated_client):
    resp = _mock_soap_response(
        f'<BatchJobResponse xmlns="{DS_NS}"><pid>1</pid><cid>1</cid>'
        f"<rid>1</rid><repoName>DS_REPO</repoName></BatchJobResponse>"
    )
    with patch.object(authenticated_client._session, "post", return_value=resp) as mock_post:
        authenticated_client.trigger_job(
            "J", job_params={"$G_NAME": "O'Brien"},
        )
    body = mock_post.call_args[1].get("data") or mock_post.call_args[0][1]
    body = body.decode() if isinstance(body, bytes) else body
    # BODS doubles an embedded single quote to escape it inside a literal.
    assert "<variable name=\"$G_NAME\">'O''Brien'</variable>" in body


def test_trigger_job_passes_through_function_call_expression_unquoted(authenticated_client):
    # A date-typed global variable can't take a quoted string literal as its
    # substitution value -- SAP DS needs a real BODS date expression such as
    # to_date(...), and silently falls back to the variable's compiled
    # default (confirmed live: a date-typed $G_BUSINESS_DATE kept running
    # with sysdate() instead of the supplied date even once the value was
    # auto-quoted as a string). Since job_params carries no type info, a
    # value shaped like a BODS function call is passed through unchanged
    # instead of being re-quoted as a string.
    resp = _mock_soap_response(
        f'<BatchJobResponse xmlns="{DS_NS}"><pid>1</pid><cid>1</cid>'
        f"<rid>1</rid><repoName>DS_REPO</repoName></BatchJobResponse>"
    )
    with patch.object(authenticated_client._session, "post", return_value=resp) as mock_post:
        authenticated_client.trigger_job(
            "J", job_params={"$G_BUSINESS_DATE": "to_date('10-Jun-2026','dd-mon-yyyy')"},
        )
    body = mock_post.call_args[1].get("data") or mock_post.call_args[0][1]
    body = body.decode() if isinstance(body, bytes) else body
    assert (
        "<variable name=\"$G_BUSINESS_DATE\">to_date('10-Jun-2026','dd-mon-yyyy')</variable>"
        in body
    )


def test_trigger_job_passes_through_already_quoted_literal_unchanged(authenticated_client):
    # A value the caller already single-quoted (e.g. hand-written to match
    # exactly what a HAR capture showed) shouldn't be double-quoted.
    resp = _mock_soap_response(
        f'<BatchJobResponse xmlns="{DS_NS}"><pid>1</pid><cid>1</cid>'
        f"<rid>1</rid><repoName>DS_REPO</repoName></BatchJobResponse>"
    )
    with patch.object(authenticated_client._session, "post", return_value=resp) as mock_post:
        authenticated_client.trigger_job(
            "J", job_params={"$G_BUSINESS_DATE": "'31-Jul-2026'"},
        )
    body = mock_post.call_args[1].get("data") or mock_post.call_args[0][1]
    body = body.decode() if isinstance(body, bytes) else body
    assert "<variable name=\"$G_BUSINESS_DATE\">'31-Jul-2026'</variable>" in body


def test_trigger_job_authenticates_first_if_no_token(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    logon = _mock_soap_response(
        f'<session xmlns="{DS_NS}"><SessionID>SESS-9</SessionID></session>'
    )
    run = _mock_soap_response(
        f'<BatchJobResponse xmlns="{DS_NS}"><pid>1</pid><cid>1</cid>'
        f"<rid>55</rid><repoName>DS_REPO</repoName></BatchJobResponse>"
    )
    with patch.object(client._session, "post", side_effect=[logon, run]):
        run_id = client.trigger_job("J")
    assert run_id == "55"
    assert client._token == "SESS-9"


def test_trigger_job_raises_when_returncode_nonzero(authenticated_client):
    from etl_framework.exceptions import DSAPIError

    resp = _mock_soap_response(
        f'<BatchJobResponse xmlns="{DS_NS}"><pid>0</pid><cid>0</cid><rid>0</rid>'
        f"<repoName>DS_REPO</repoName><returnCode>1</returnCode>"
        f"<errorMessage>job not found</errorMessage></BatchJobResponse>"
    )
    with patch.object(authenticated_client._session, "post", return_value=resp):
        with pytest.raises(DSAPIError) as exc_info:
            authenticated_client.trigger_job("does-not-exist")
    assert "job not found" in (exc_info.value.response_body or "")


def test_trigger_job_raises_on_soap_fault(authenticated_client):
    from etl_framework.exceptions import DSAPIError

    fault = (
        "<soapenv:Fault xmlns:soapenv='http://schemas.xmlsoap.org/soap/envelope/'>"
        "<faultstring>boom</faultstring></soapenv:Fault>"
    )
    resp = _mock_soap_response(fault, status_code=500)
    with patch.object(authenticated_client._session, "post", return_value=resp):
        with pytest.raises(DSAPIError):
            authenticated_client.trigger_job("J")


def test_trigger_job_raises_value_error_when_no_repository_available(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config.model_copy(update={"ds_repository": ""}))
    client._token = "SESS"
    with pytest.raises(ValueError, match="repository"):
        client.trigger_job("J")


# ---------------------------------------------------------------------------
# get_job_status (Get_BatchJob_Status SOAP op)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw_status,expected", [
    ("Completed", TestStatus.PASSED),
    ("completed", TestStatus.PASSED),
    ("Success", TestStatus.PASSED),
    ("succeeded", TestStatus.PASSED),
    ("Succeeded", TestStatus.PASSED),
    ("Warning", TestStatus.PASSED),
    ("Error", TestStatus.FAILED),
    ("Failed", TestStatus.FAILED),
    ("Cancelled", TestStatus.FAILED),
    ("Stopped", TestStatus.FAILED),
    ("Running", TestStatus.RUNNING),
    ("Pending", TestStatus.RUNNING),
    ("Queued", TestStatus.RUNNING),
    ("Started", TestStatus.RUNNING),
])
def test_get_job_status_maps_known_statuses(authenticated_client, raw_status, expected):
    resp = _mock_soap_response(
        f'<batchJobStatusResponse xmlns="{DS_NS}"><returnCode>0</returnCode>'
        f"<status>{raw_status}</status></batchJobStatusResponse>"
    )
    with patch.object(authenticated_client._session, "post", return_value=resp) as mock_post:
        status = authenticated_client.get_job_status("4242")

    assert status == expected
    headers = mock_post.call_args[1]["headers"]
    assert headers["SOAPAction"] == '"jobAdmin=Get_BatchJob_Status"'
    body = mock_post.call_args[1].get("data") or mock_post.call_args[0][1]
    body = body.decode() if isinstance(body, bytes) else body
    assert "<runID>4242</runID>" in body
    assert "<repoName>DS_REPO</repoName>" in body


def test_get_job_status_uses_repository_override(authenticated_client):
    resp = _mock_soap_response(
        f'<batchJobStatusResponse xmlns="{DS_NS}"><returnCode>0</returnCode>'
        f"<status>Completed</status></batchJobStatusResponse>"
    )
    with patch.object(authenticated_client._session, "post", return_value=resp) as mock_post:
        authenticated_client.get_job_status("4242", repository="OTHER_REPO")
    body = mock_post.call_args[1].get("data") or mock_post.call_args[0][1]
    body = body.decode() if isinstance(body, bytes) else body
    assert "<repoName>OTHER_REPO</repoName>" in body


def test_get_job_status_treats_unrecognized_status_as_running(authenticated_client, caplog):
    resp = _mock_soap_response(
        f'<batchJobStatusResponse xmlns="{DS_NS}"><returnCode>0</returnCode>'
        f"<status>SomeNewDSStatus</status></batchJobStatusResponse>"
    )
    with patch.object(authenticated_client._session, "post", return_value=resp):
        with caplog.at_level("WARNING"):
            status = authenticated_client.get_job_status("4242")
    assert status == TestStatus.RUNNING
    assert "SomeNewDSStatus" in caplog.text


def test_get_job_status_raises_on_soap_fault(authenticated_client):
    from etl_framework.exceptions import DSAPIError

    fault = (
        "<soapenv:Fault xmlns:soapenv='http://schemas.xmlsoap.org/soap/envelope/'>"
        "<faultstring>server error</faultstring></soapenv:Fault>"
    )
    resp = _mock_soap_response(fault, status_code=500)
    with patch.object(authenticated_client._session, "post", return_value=resp):
        with pytest.raises(DSAPIError):
            authenticated_client.get_job_status("4242")


# ---------------------------------------------------------------------------
# wait_for_completion (unchanged polling logic on top of get_job_status)
# ---------------------------------------------------------------------------

def test_wait_for_completion_returns_immediately_on_success(authenticated_client):
    with patch.object(authenticated_client, "get_job_status", return_value=TestStatus.PASSED) as mock_get:
        status = authenticated_client.wait_for_completion("4242", timeout_s=5, poll_interval_s=0.01)
    assert status == TestStatus.PASSED
    mock_get.assert_called_once_with("4242", repository=None)


def test_wait_for_completion_polls_until_terminal_status(authenticated_client):
    with patch.object(
        authenticated_client, "get_job_status",
        side_effect=[TestStatus.RUNNING, TestStatus.RUNNING, TestStatus.PASSED],
    ) as mock_get:
        status = authenticated_client.wait_for_completion("4242", timeout_s=5, poll_interval_s=0.01)
    assert status == TestStatus.PASSED
    assert mock_get.call_count == 3


def test_wait_for_completion_raises_timeout_error_when_never_terminal(authenticated_client):
    with patch.object(authenticated_client, "get_job_status", return_value=TestStatus.RUNNING):
        with pytest.raises(TimeoutError, match="4242"):
            authenticated_client.wait_for_completion("4242", timeout_s=0.05, poll_interval_s=0.01)


def test_wait_for_completion_passes_repository_override_through(authenticated_client):
    with patch.object(authenticated_client, "get_job_status", return_value=TestStatus.PASSED) as mock_get:
        authenticated_client.wait_for_completion("4242", repository="OTHER_REPO", timeout_s=5, poll_interval_s=0.01)
    mock_get.assert_called_once_with("4242", repository="OTHER_REPO")
