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


def test_authenticate_returns_logon_token(env_config):
    from etl_framework.sap_bo.client import BORestClient

    client = BORestClient(env_config)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"X-SAP-LogonToken": "tok"}
    with patch.object(client._session, "post", return_value=mock_response):
        token = client.authenticate()

    assert token == "tok"
    assert client.logon_token == "tok"


def test_use_logon_token_sets_header_and_skips_logon(env_config):
    from etl_framework.sap_bo.client import BORestClient

    client = BORestClient(env_config)
    client.use_logon_token("external-token")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"documents": []}
    with patch.object(client._session, "post") as mock_post, \
         patch.object(client._session, "get", return_value=mock_response):
        assert client.list_documents() == []

    mock_post.assert_not_called()
    assert client._session.headers["X-SAP-LogonToken"] == "external-token"


def test_logout_does_not_logoff_caller_owned_token(env_config):
    from etl_framework.sap_bo.client import BORestClient

    client = BORestClient(env_config)
    client.use_logon_token("external-token")

    with patch.object(client._session, "post") as mock_post:
        client.logout()

    mock_post.assert_not_called()
    assert client.logon_token is None


def test_logout_posts_when_client_owns_token(env_config):
    from etl_framework.sap_bo.client import BORestClient

    client = BORestClient(env_config)
    client.use_logon_token("owned-token", owns_token=True)

    mock_response = MagicMock()
    mock_response.status_code = 200
    with patch.object(client._session, "post", return_value=mock_response) as mock_post:
        client.logout()

    assert mock_post.call_args[0][0].endswith("/biprws/logoff")
    assert client.logon_token is None


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
    empty_response = MagicMock()
    empty_response.status_code = 200
    empty_response.json.return_value = {"documents": []}
    with patch.object(authenticated_client._session, "get", side_effect=[mock_response, empty_response]):
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


def test_list_documents_unwraps_plural_container_nested_singular_child(authenticated_client):
    """On-premises biprws wraps the collection one level deeper than the mock
    assumed: {"documents": {"document": [...]}} instead of a flat
    {"documents": [...]} array (classic BIP RESTful plural-wraps-singular-child
    JSON convention). Reproduces the exact payload seen from a real on-prem
    server, which previously caused list_documents to treat the wrapper dict
    itself as a single document lacking an 'id', yielding an empty doc_id and
    a downstream 404 on GET .../documents//reports."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "documents": {
            "document": [
                {
                    "id": "123456",
                    "cuid": "AB123456789123456789012",
                    "name": "01.aCIS_Sum_Mon_Bran_Reg",
                    "description": "asasdadadad",
                    "folderid": 131373,
                    "scheduled": "false",
                }
            ]
        }
    }
    empty_response = MagicMock()
    empty_response.status_code = 200
    empty_response.json.return_value = {"documents": []}
    with patch.object(authenticated_client._session, "get", side_effect=[mock_response, empty_response]):
        docs = authenticated_client.list_documents()
    assert docs == [
        {"id": "123456", "name": "01.aCIS_Sum_Mon_Bran_Reg", "folder": ""}
    ]


def test_list_documents_handles_single_document_not_wrapped_in_list(authenticated_client):
    """SAP BO's biprws collapses a single-element collection into a bare object
    instead of a one-element JSON array (a known BI4 RESTful Web Services quirk)."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "documents": {"id": "101", "name": "Sales Report", "folder": "/Finance"}
    }
    empty_response = MagicMock()
    empty_response.status_code = 200
    empty_response.json.return_value = {"documents": []}
    with patch.object(authenticated_client._session, "get", side_effect=[mock_response, empty_response]):
        docs = authenticated_client.list_documents()
    assert docs == [{"id": "101", "name": "Sales Report", "folder": "/Finance"}]


def test_list_documents_pages_through_results_beyond_default_page_size(authenticated_client):
    """Defensive: extends the same explicit-pagesize paging confirmed necessary
    for list_reports to list_documents, since both are biprws collection
    endpoints subject to the same admin-configured page size cap."""
    page_size = 200
    first_page = [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(page_size)]
    second_page = [{"id": "200", "name": "Doc 200", "folder": ""}]
    responses = []
    for page_docs in (first_page, second_page):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"documents": page_docs}
        responses.append(resp)
    with patch.object(authenticated_client._session, "get", side_effect=responses) as mock_get:
        docs = authenticated_client.list_documents()
    assert len(docs) == page_size + 1
    assert docs[-1]["id"] == "200"
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0][1]["params"] == {"page": 1, "pagesize": page_size}
    assert mock_get.call_args_list[1][1]["params"] == {"page": 2, "pagesize": page_size}


def test_list_documents_pages_past_server_enforced_page_cap(authenticated_client):
    """Some on-prem biprws deployments admin-cap the page size (CMC setting)
    and silently clamp it below whatever `pagesize` the client requests —
    e.g. requesting pagesize=200 but the server always returns 10 per page.
    A page shorter than the *requested* size is then NOT proof there's no
    more data; only a page shorter than the *previous* (server's actual)
    page size, or an empty page, means the collection is exhausted."""
    server_cap = 10
    pages = [
        [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(server_cap)],
        [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(server_cap, 2 * server_cap)],
        [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(2 * server_cap, 2 * server_cap + 5)],
    ]
    responses = []
    for page_docs in pages:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"documents": page_docs}
        responses.append(resp)
    with patch.object(authenticated_client._session, "get", side_effect=responses):
        docs = authenticated_client.list_documents()
    assert len(docs) == 25
    assert docs[-1]["id"] == "24"


def test_list_documents_stops_when_server_ignores_page_param(authenticated_client):
    """Some on-prem biprws deployments ignore the `page` query param entirely
    and re-serve page 1's content on every request. A same-size batch is
    normally treated as "keep going" (see the page-cap test above), so
    without a repeat check this would loop until _MAX_PAGES re-appending the
    same rows -- inflating the count without adding real documents. Once the
    repeat is detected, one Range-header probe (see the recovery test below)
    is attempted before giving up; when that probe also re-serves the same
    content, the client stops with just the one real page."""
    same_page = [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(10)]
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"documents": same_page}
    post_resp = MagicMock()
    post_resp.status_code = 404
    post_resp.text = "not found"
    with patch.object(authenticated_client._session, "get", return_value=resp) as mock_get, \
         patch.object(authenticated_client._session, "post", return_value=post_resp):
        docs = authenticated_client.list_documents()
    assert len(docs) == 10
    assert mock_get.call_count == 3
    assert mock_get.call_args_list[2][1]["headers"]["Range"] == "elements=10-209"


def test_list_documents_recovers_via_range_header_when_page_param_ignored(authenticated_client):
    """When a deployment ignores `page` (previous test), some biprws gateways
    still honor the documented `Range: elements=N-M` header even though they
    silently ignore/cache the `page`/`pagesize` query params (e.g. a reverse
    proxy caching GETs by path only). Once the repeat is detected, the client
    should retry via Range and recover the real remaining documents instead
    of silently truncating the browse to just the first page."""
    page1 = [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(10)]
    page1_repeat = [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(10)]
    range_page1 = [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(10, 20)]
    range_page2: list[dict] = []
    responses = []
    for docs_batch in (page1, page1_repeat, range_page1, range_page2):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"documents": docs_batch}
        responses.append(resp)
    with patch.object(authenticated_client._session, "get", side_effect=responses) as mock_get:
        docs = authenticated_client.list_documents()
    assert [d["id"] for d in docs] == [str(i) for i in range(20)]
    assert mock_get.call_count == 4
    assert mock_get.call_args_list[2][1]["headers"]["Range"] == "elements=10-209"
    assert mock_get.call_args_list[3][1]["headers"]["Range"] == "elements=20-219"
    assert "params" not in mock_get.call_args_list[2][1] or mock_get.call_args_list[2][1]["params"] is None


def test_list_documents_sends_no_cache_headers_on_every_request(authenticated_client):
    """A reverse proxy/gateway sitting in front of an on-prem biprws server can
    cache GET responses keyed only on the URL path, blind to both the `page`
    query param and the `Range` header -- serving the identical cached page-1
    body no matter what pagination mechanism the client tries (this is exactly
    what happened live: the Range-header retry re-served the same 10 items).
    Sending `Cache-Control`/`Pragma: no-cache` asks any RFC 7234-compliant
    intermediary to revalidate/bypass its cache instead of serving stale
    content, on both the page-param and the Range-header requests."""
    page1 = [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(10)]
    empty_page = []
    responses = []
    for docs_batch in (page1, empty_page):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"documents": docs_batch}
        responses.append(resp)
    with patch.object(authenticated_client._session, "get", side_effect=responses) as mock_get:
        authenticated_client.list_documents()
    for call in mock_get.call_args_list:
        headers = call[1]["headers"]
        assert headers["Cache-Control"] == "no-cache, no-store"
        assert headers["Pragma"] == "no-cache"


def test_list_documents_logs_when_range_header_recovers_nothing(authenticated_client, caplog):
    """The Range-header retry (see the recovery test above) silently returned
    an empty `collected` list with no log line explaining why in production --
    exactly the gap that made the live on-prem failure (server re-serves the
    same 10 items via Range too) indistinguishable from "the fix isn't
    deployed" from the log alone. Both the empty-batch and identical-batch
    soft-fail branches of the Range continuation must log a warning."""
    same_page = [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(10)]
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"documents": same_page}
    post_resp = MagicMock()
    post_resp.status_code = 404
    post_resp.text = "not found"
    with caplog.at_level("WARNING", logger="etl_framework.sap_bo.client"):
        with patch.object(authenticated_client._session, "get", return_value=resp), \
             patch.object(authenticated_client._session, "post", return_value=post_resp):
            authenticated_client.list_documents()
    assert any(
        "range-header pagination" in rec.message.lower() and "did not return" in rec.message.lower()
        for rec in caplog.records
    )


def test_list_documents_falls_back_to_cms_query_when_range_also_stuck(authenticated_client):
    """Live escalation: even the Range-header retry re-served the identical
    10-item batch (an intermediary that's blind to page param, Range header,
    and no-cache headers alike -- or the server genuinely doesn't support
    Range pagination on this endpoint). As a non-paginated last resort, query
    the CMS directly via CeQL (`POST /biprws/v1/cmsquery`): a single request/
    response with no page/Range mechanism for anything in front of biprws to
    silently defeat. Only used when the paginated approach is confirmed stuck
    (not on every call), and only for documents -- WebI report tabs aren't
    CMS InfoObjects, so this has no reports equivalent."""
    same_page = [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(10)]
    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"documents": same_page}

    cms_entries = [
        {"SI_ID": str(i), "SI_NAME": f"Doc {i}", "SI_ANCESTOR": "42"} for i in range(37)
    ]
    post_resp = MagicMock()
    post_resp.status_code = 200
    post_resp.json.return_value = {"documents": cms_entries}

    with patch.object(authenticated_client._session, "get", return_value=get_resp), \
         patch.object(authenticated_client._session, "post", return_value=post_resp) as mock_post:
        docs = authenticated_client.list_documents()

    assert len(docs) == 37
    assert docs[0] == {"id": "0", "name": "Doc 0", "folder": "42"}
    assert mock_post.call_count == 1
    call_kwargs = mock_post.call_args[1]
    assert "cmsquery" in mock_post.call_args[0][0]
    assert "SELECT" in call_kwargs["json"]["query"].upper()


def test_list_documents_keeps_raylight_result_when_cms_query_unavailable(authenticated_client):
    """If the CMS query endpoint itself 404s/errors (older BOE version, or
    disabled), the browse must not fail -- fall back to whatever the
    page-param/Range-header attempts already collected rather than losing
    that real (if incomplete) data."""
    same_page = [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(10)]
    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"documents": same_page}
    post_resp = MagicMock()
    post_resp.status_code = 404
    post_resp.text = "not found"

    with patch.object(authenticated_client._session, "get", return_value=get_resp), \
         patch.object(authenticated_client._session, "post", return_value=post_resp):
        docs = authenticated_client.list_documents()

    assert len(docs) == 10


def test_list_documents_does_not_query_cms_when_pagination_succeeds_normally(authenticated_client):
    """The CMS-query fallback is a targeted last resort for the confirmed-stuck
    case, not a call made on every browse -- a normal, healthy paginated fetch
    must not touch the CMS query endpoint at all."""
    page_size = 200
    first_page = [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(page_size)]
    second_page = [{"id": "200", "name": "Doc 200", "folder": ""}]
    responses = []
    for page_docs in (first_page, second_page):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"documents": page_docs}
        responses.append(resp)
    with patch.object(authenticated_client._session, "get", side_effect=responses), \
         patch.object(authenticated_client._session, "post") as mock_post:
        docs = authenticated_client.list_documents()
    assert len(docs) == page_size + 1
    mock_post.assert_not_called()


def test_list_documents_dedupes_overlapping_pages(authenticated_client):
    """Defensive net for pages that overlap without being fully identical
    (e.g. an off-by-one server cursor) -- duplicate ids across pages should
    collapse to one entry each, keeping the first occurrence."""
    page_size = 10
    first_page = [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(page_size)]
    second_page = [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(5, 5 + page_size)]
    third_page = []
    responses = []
    for page_docs in (first_page, second_page, third_page):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"documents": page_docs}
        responses.append(resp)
    with patch.object(authenticated_client._session, "get", side_effect=responses):
        docs = authenticated_client.list_documents()
    assert [d["id"] for d in docs] == [str(i) for i in range(15)]


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
    empty_response = MagicMock()
    empty_response.status_code = 200
    empty_response.json.return_value = {"reports": []}
    with patch.object(authenticated_client._session, "get", side_effect=[mock_response, empty_response]):
        reports = authenticated_client.list_reports("101")
    assert len(reports) == 2
    assert reports[0]["id"] == "1"
    assert reports[1]["name"] == "Summary"


def test_list_reports_unwraps_plural_container_nested_singular_child(authenticated_client):
    """Defensive: extends the same plural-wraps-singular-child convention
    confirmed for list_documents ({"reports": {"report": [...]}}) in case the
    on-premises reports sub-resource is serialized the same way."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "reports": {"report": [{"id": "1", "name": "Page 1", "reportIndex": 0}]}
    }
    empty_response = MagicMock()
    empty_response.status_code = 200
    empty_response.json.return_value = {"reports": []}
    with patch.object(authenticated_client._session, "get", side_effect=[mock_response, empty_response]):
        reports = authenticated_client.list_reports("101")
    assert reports == [{"id": "1", "name": "Page 1", "reportIndex": 0}]


def test_list_reports_pages_through_results_beyond_default_page_size(authenticated_client):
    """biprws paginates collection responses (page size is admin-configured in
    CMC; observed capping a real on-premises document's report tabs at 10),
    silently truncating documents with more tabs than one page holds.
    list_reports must request an explicit pagesize and keep paging until a
    short page comes back, not stop after the first page."""
    page_size = 200
    first_page = [{"id": str(i), "name": f"Tab {i}", "reportIndex": i} for i in range(page_size)]
    second_page = [{"id": "200", "name": "Tab 200", "reportIndex": 200}]
    responses = []
    for page_reports in (first_page, second_page):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"reports": page_reports}
        responses.append(resp)
    with patch.object(authenticated_client._session, "get", side_effect=responses) as mock_get:
        reports = authenticated_client.list_reports("101")
    assert len(reports) == page_size + 1
    assert reports[0]["id"] == "0"
    assert reports[-1]["id"] == "200"
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0][1]["params"] == {"page": 1, "pagesize": page_size}
    assert mock_get.call_args_list[1][1]["params"] == {"page": 2, "pagesize": page_size}


def test_list_reports_pages_past_server_enforced_page_cap(authenticated_client):
    """Same server-clamped-page-size scenario as list_documents: the server
    ignores the requested pagesize=200 and always returns its own smaller
    admin-configured cap per page."""
    server_cap = 10
    pages = [
        [{"id": str(i), "name": f"Tab {i}", "reportIndex": i} for i in range(server_cap)],
        [{"id": str(i), "name": f"Tab {i}", "reportIndex": i} for i in range(server_cap, 2 * server_cap)],
        [{"id": str(i), "name": f"Tab {i}", "reportIndex": i} for i in range(2 * server_cap, 2 * server_cap + 5)],
    ]
    responses = []
    for page_reports in pages:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"reports": page_reports}
        responses.append(resp)
    with patch.object(authenticated_client._session, "get", side_effect=responses):
        reports = authenticated_client.list_reports("101")
    assert len(reports) == 25
    assert reports[-1]["id"] == "24"


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


def test_list_reports_handles_single_report_not_wrapped_in_list(authenticated_client):
    """Reproduces the on-premises 'str' object has no attribute 'get' crash: a
    WebI document with exactly one report tab gets a bare object for 'reports'
    instead of a one-element array, so the old code iterated over dict keys."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "reports": {"id": "1", "name": "Page 1", "reportIndex": 0}
    }
    empty_response = MagicMock()
    empty_response.status_code = 200
    empty_response.json.return_value = {"reports": []}
    with patch.object(authenticated_client._session, "get", side_effect=[mock_response, empty_response]):
        reports = authenticated_client.list_reports("101")
    assert reports == [{"id": "1", "name": "Page 1", "reportIndex": 0}]


# ---------------------------------------------------------------------------
# fetch_report_data
# ---------------------------------------------------------------------------

def test_fetch_report_data_multi_row_dataset_returns_dataframe(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "dataset": [
            {"sku": "A100", "amount": 25.5},
            {"sku": "B200", "amount": 50.0},
        ]
    }
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        df = authenticated_client.fetch_report_data("rpt-sales")
    assert list(df["sku"]) == ["A100", "B200"]


def test_fetch_report_data_handles_single_row_dataset_not_wrapped_in_list(authenticated_client):
    """Same biprws single-element collapse as list_reports, but for the dataset field."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"dataset": {"sku": "A100", "amount": 25.5}}
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        df = authenticated_client.fetch_report_data("rpt-sales")
    assert list(df["sku"]) == ["A100"]
    assert list(df["amount"]) == [25.5]


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


def test_download_report_calls_report_resource_without_content_suffix(authenticated_client):
    """The raylight export endpoint is GET .../documents/{docId}/reports/{reportId}
    with the format chosen via the Accept header -- there is no '/content'
    sub-resource. The old '/content' suffix hit a real on-premises server and
    got back 'SAP BO API error 404 for report' since that path doesn't exist."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"data"
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        authenticated_client.download_report("DOC1", "RPT2", "pdf")
    called_url = mock_get.call_args[0][0]
    assert called_url.endswith("/documents/DOC1/reports/RPT2")
    assert "content" not in called_url


def test_download_report_http_error_raises(authenticated_client):
    from etl_framework.exceptions import BOAPIError
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with pytest.raises(BOAPIError):
            authenticated_client.download_report("101", "1", "pdf")


# ---------------------------------------------------------------------------
# schedule_object
# ---------------------------------------------------------------------------

def test_schedule_object_posts_to_infostore_schedules_endpoint(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "inst-42"}
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        instance_id = authenticated_client.schedule_object("3001")

    assert instance_id == "inst-42"
    called_url = mock_post.call_args[0][0]
    assert called_url == "http://bo.example.com/biprws/infostore/3001/schedules"


def test_schedule_object_sends_schedule_params_as_json_body(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "inst-42"}
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        authenticated_client.schedule_object("3001", {"prompt_values": {"region": "EMEA"}})

    assert mock_post.call_args[1]["json"] == {"prompt_values": {"region": "EMEA"}}


def test_schedule_object_authenticates_first_if_no_token(env_config):
    from etl_framework.sap_bo.client import BORestClient

    client = BORestClient(env_config)
    auth_response = MagicMock()
    auth_response.status_code = 200
    auth_response.headers = {"X-SAP-LogonToken": "tok"}
    schedule_response = MagicMock()
    schedule_response.status_code = 200
    schedule_response.json.return_value = {"id": "inst-1"}
    with patch.object(client._session, "post", side_effect=[auth_response, schedule_response]):
        instance_id = client.schedule_object("3001")

    assert instance_id == "inst-1"
    assert client.logon_token == "tok"


def test_schedule_object_raises_bo_api_error_on_http_failure(authenticated_client):
    from etl_framework.exceptions import BOAPIError

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "object not found"
    with patch.object(authenticated_client._session, "post", return_value=mock_response):
        with pytest.raises(BOAPIError):
            authenticated_client.schedule_object("does-not-exist")


def test_schedule_object_raises_bo_api_error_when_response_has_no_id(authenticated_client):
    from etl_framework.exceptions import BOAPIError

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}
    with patch.object(authenticated_client._session, "post", return_value=mock_response):
        with pytest.raises(BOAPIError):
            authenticated_client.schedule_object("3001")


# ---------------------------------------------------------------------------
# get_schedule_status / wait_for_completion
# ---------------------------------------------------------------------------

from etl_framework.runner.state import TestStatus


@pytest.mark.parametrize("raw_status,expected", [
    ("Success", TestStatus.PASSED),
    ("success", TestStatus.PASSED),
    ("Failed", TestStatus.FAILED),
    ("Running", TestStatus.RUNNING),
    ("Pending", TestStatus.RUNNING),
    ("Recurring", TestStatus.RUNNING),
    ("Paused", TestStatus.RUNNING),
])
def test_get_schedule_status_maps_known_statuses(authenticated_client, raw_status, expected):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "inst-42", "status": raw_status}
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        status = authenticated_client.get_schedule_status("inst-42")

    assert status == expected
    called_url = mock_get.call_args[0][0]
    assert called_url == "http://bo.example.com/biprws/infostore/inst-42"


def test_get_schedule_status_treats_unrecognized_status_as_running(authenticated_client, caplog):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "inst-42", "status": "SomeNewBOEStatus"}
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with caplog.at_level("WARNING"):
            status = authenticated_client.get_schedule_status("inst-42")

    assert status == TestStatus.RUNNING
    assert "SomeNewBOEStatus" in caplog.text


def test_get_schedule_status_raises_bo_api_error_on_http_failure(authenticated_client):
    from etl_framework.exceptions import BOAPIError

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "server error"
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with pytest.raises(BOAPIError):
            authenticated_client.get_schedule_status("inst-42")


def test_wait_for_completion_returns_immediately_on_success(authenticated_client):
    with patch.object(authenticated_client, "get_schedule_status", return_value=TestStatus.PASSED) as mock_get:
        status = authenticated_client.wait_for_completion("inst-42", timeout_s=5, poll_interval_s=0.01)

    assert status == TestStatus.PASSED
    mock_get.assert_called_once_with("inst-42")


def test_wait_for_completion_polls_until_terminal_status(authenticated_client):
    with patch.object(
        authenticated_client, "get_schedule_status",
        side_effect=[TestStatus.RUNNING, TestStatus.RUNNING, TestStatus.PASSED],
    ) as mock_get:
        status = authenticated_client.wait_for_completion("inst-42", timeout_s=5, poll_interval_s=0.01)

    assert status == TestStatus.PASSED
    assert mock_get.call_count == 3


def test_wait_for_completion_raises_timeout_error_when_never_terminal(authenticated_client):
    with patch.object(authenticated_client, "get_schedule_status", return_value=TestStatus.RUNNING):
        with pytest.raises(TimeoutError, match="inst-42"):
            authenticated_client.wait_for_completion("inst-42", timeout_s=0.05, poll_interval_s=0.01)
