"""Tests for BORestClient SAP BO REST API methods."""
from __future__ import annotations

from datetime import date

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
    # Keyset pagination probes one page past the first (the first page could be
    # a server cap below TOP, not the end); here the server re-serves the same
    # batch, so the cursor-didn't-advance guard stops at the second call
    # without double-counting the re-served rows.
    assert mock_post.call_count == 2
    first_call = mock_post.call_args_list[0]
    assert "cmsquery" in first_call[0][0]
    assert "SELECT" in first_call[1]["json"]["query"].upper()
    assert "SI_ID >" not in first_call[1]["json"]["query"]
    assert "SI_ID > 36" in mock_post.call_args_list[1][1]["json"]["query"]


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


def test_list_documents_cms_query_pages_past_its_own_default_result_cap(authenticated_client):
    """Live evidence: after the CMS-query fallback shipped, the on-prem
    deployment returned exactly one un-paginated batch (matching CeQL's
    server-side default result cap when no TOP/keyset bound is given) instead
    of the real 5000+ documents -- the CMS query endpoint pages too, just via
    its own mechanism, not query-string page/pagesize. Use `TOP N` plus a
    keyset `WHERE SI_ID > :last_seen_id` cursor, driven by rewriting the CeQL
    query body each request (not a page number or header), so nothing in
    front of biprws can defeat it: proxies don't cache POST bodies, and a
    strictly-increasing WHERE clause can't get stuck re-serving the same
    content the way `page`/`Range` did."""
    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"documents": [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(10)]}

    batch1 = [{"SI_ID": str(i), "SI_NAME": f"Doc {i}", "SI_ANCESTOR": "42"} for i in range(1, 201)]
    batch2 = [{"SI_ID": str(i), "SI_NAME": f"Doc {i}", "SI_ANCESTOR": "42"} for i in range(201, 401)]
    batch3 = [{"SI_ID": str(i), "SI_NAME": f"Doc {i}", "SI_ANCESTOR": "42"} for i in range(401, 451)]
    post_responses = []
    for batch in (batch1, batch2, batch3):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"documents": batch}
        post_responses.append(resp)

    with patch.object(authenticated_client._session, "get", return_value=get_resp), \
         patch.object(authenticated_client._session, "post", side_effect=post_responses) as mock_post:
        docs = authenticated_client.list_documents()

    assert len(docs) == 450
    assert [d["id"] for d in docs] == [str(i) for i in range(1, 451)]
    assert mock_post.call_count == 3
    first_query = mock_post.call_args_list[0][1]["json"]["query"]
    second_query = mock_post.call_args_list[1][1]["json"]["query"]
    third_query = mock_post.call_args_list[2][1]["json"]["query"]
    assert "SI_ID >" not in first_query
    assert "SI_ID > 200" in second_query
    assert "SI_ID > 400" in third_query


def test_list_documents_cms_query_pages_past_a_server_cap_below_requested_top(authenticated_client):
    """Live regression (the "only 50 documents shown" report): this on-prem
    CMS server caps every result set at 50 rows regardless of the requested
    `TOP 200`. Keyset pagination must treat "shorter than the previous page"
    (or empty) as the end -- NOT "shorter than TOP" -- exactly like the
    page-param path already does (`len(batch) < previous_batch_size`). A page
    shorter than what we *asked for* only means the server's own cap is below
    our TOP, not that the collection is exhausted. Before the fix the first
    50-row page tripped `len(batch) < TOP` and returned only 50 of 130 docs."""
    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"documents": [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(10)]}

    ranges = [(1, 51), (51, 101), (101, 131)]  # server caps at 50: 50, 50, then 30
    post_responses = []
    for start, end in ranges:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "documents": [{"SI_ID": str(i), "SI_NAME": f"Doc {i}", "SI_ANCESTOR": "42"} for i in range(start, end)]
        }
        post_responses.append(resp)

    with patch.object(authenticated_client._session, "get", return_value=get_resp), \
         patch.object(authenticated_client._session, "post", side_effect=post_responses) as mock_post:
        docs = authenticated_client.list_documents()

    assert [d["id"] for d in docs] == [str(i) for i in range(1, 131)]
    assert mock_post.call_count == 3
    assert "SI_ID > 50" in mock_post.call_args_list[1][1]["json"]["query"]
    assert "SI_ID > 100" in mock_post.call_args_list[2][1]["json"]["query"]


def test_list_documents_cms_query_keeps_partial_data_when_a_later_page_fails(authenticated_client):
    """A failure on the *first* CMS query call means the endpoint is
    unsupported (return None, fall back to raylight's result -- see the
    unavailable test above). A failure on a *later* page during keyset
    pagination is different: the endpoint works, we already have real data
    from it, and should keep that partial result rather than discarding it
    or falling back to the (smaller) raylight result."""
    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"documents": [{"id": str(i), "name": f"Doc {i}", "folder": ""} for i in range(10)]}

    batch1 = [{"SI_ID": str(i), "SI_NAME": f"Doc {i}", "SI_ANCESTOR": "42"} for i in range(1, 201)]
    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"documents": batch1}
    fail_resp = MagicMock()
    fail_resp.status_code = 503
    fail_resp.text = "service unavailable"

    with patch.object(authenticated_client._session, "get", return_value=get_resp), \
         patch.object(authenticated_client._session, "post", side_effect=[ok_resp, fail_resp]):
        docs = authenticated_client.list_documents()

    assert len(docs) == 200


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


# ---------------------------------------------------------------------------
# list_document_ids_with_runs_on
# ---------------------------------------------------------------------------

def test_list_document_ids_with_runs_on_returns_distinct_parent_ids(authenticated_client):
    """Powers the Adapters tab's run-date filter: find every instance
    (a scheduled/saved run) that started on the given day and return the
    distinct set of documents (SI_PARENTID) those instances belong to."""
    batch = [
        {"SI_ID": "1", "SI_PARENTID": "500"},
        {"SI_ID": "2", "SI_PARENTID": "501"},
        {"SI_ID": "3", "SI_PARENTID": "500"},  # same document ran twice that day
    ]
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"documents": batch}
    with patch.object(authenticated_client._session, "post", return_value=resp) as mock_post:
        ids = authenticated_client.list_document_ids_with_runs_on(date(2026, 7, 20))
    assert ids == ["500", "501"]
    # First query: no keyset cursor yet, correct instance/date clauses. (The
    # keyset loop probes a second page; the server re-serves the same batch, so
    # the cursor-didn't-advance guard stops without adding duplicate parents.)
    query = mock_post.call_args_list[0][1]["json"]["query"]
    assert "SI_INSTANCE=1" in query
    # CeQL time literals are single-quoted 'yyyy.MM.dd.HH.mm.ss' strings, NOT
    # @-prefixed tokens -- the live on-prem CMS returns HTTP 500 on the @ form.
    assert "SI_STARTTIME >= '2026.07.20.00.00.00'" in query
    assert "SI_STARTTIME < '2026.07.21.00.00.00'" in query
    assert "SI_ID >" not in query


def test_list_document_ids_with_runs_on_shifts_window_to_utc(env_config):
    """CMS compares SI_STARTTIME in UTC, so a UTC+1 server's local day must be
    expressed as a UTC window shifted back one hour (23:00 the prior day to
    23:00 that day)."""
    from etl_framework.sap_bo.client import BORestClient
    client = BORestClient(env_config.model_copy(update={"bo_server_utc_offset_hours": 1}))
    client._token = "fake-token-123"
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"documents": []}
    with patch.object(client._session, "post", return_value=resp) as mock_post:
        client.list_document_ids_with_runs_on(date(2026, 7, 20))
    query = mock_post.call_args_list[0][1]["json"]["query"]
    assert "SI_STARTTIME >= '2026.07.19.23.00.00'" in query
    assert "SI_STARTTIME < '2026.07.20.23.00.00'" in query


def test_list_document_ids_with_runs_on_pages_via_keyset(authenticated_client):
    """Same keyset-pagination shape as _list_documents_via_cms_query: a busy
    day's instance count can exceed one CeQL default batch."""
    batch1 = [{"SI_ID": str(i), "SI_PARENTID": f"doc{i}"} for i in range(1, 201)]
    batch2 = [{"SI_ID": str(i), "SI_PARENTID": f"doc{i}"} for i in range(201, 210)]
    responses = []
    for batch in (batch1, batch2):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"documents": batch}
        responses.append(resp)
    with patch.object(authenticated_client._session, "post", side_effect=responses) as mock_post:
        ids = authenticated_client.list_document_ids_with_runs_on(date(2026, 7, 20))
    assert len(ids) == 209
    assert mock_post.call_count == 2
    second_query = mock_post.call_args_list[1][1]["json"]["query"]
    assert "SI_ID > 200" in second_query


def test_list_document_ids_with_runs_on_pages_past_a_server_cap_below_requested_top(authenticated_client):
    """Same "cap below TOP" regression as the document listing, for the
    run-date instance query: a 50-row server cap must not be mistaken for the
    last page, or the run-date filter silently matches only the first 50
    instances by SI_ID."""
    ranges = [(1, 51), (51, 101), (101, 131)]  # server caps at 50: 50, 50, then 30
    responses = []
    for start, end in ranges:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "documents": [{"SI_ID": str(i), "SI_PARENTID": f"doc{i}"} for i in range(start, end)]
        }
        responses.append(resp)
    with patch.object(authenticated_client._session, "post", side_effect=responses) as mock_post:
        ids = authenticated_client.list_document_ids_with_runs_on(date(2026, 7, 20))
    assert len(ids) == 130
    assert mock_post.call_count == 3
    assert "SI_ID > 100" in mock_post.call_args_list[2][1]["json"]["query"]


def test_list_document_ids_with_runs_on_returns_none_when_endpoint_unavailable(authenticated_client):
    """First-call failure means the CMS query endpoint itself isn't
    available -- distinct from "zero documents ran that day" (an empty
    list), so the caller (the service layer) can tell them apart and report
    `supported: false` instead of an empty result."""
    resp = MagicMock()
    resp.status_code = 404
    resp.text = "not found"
    with patch.object(authenticated_client._session, "post", return_value=resp):
        ids = authenticated_client.list_document_ids_with_runs_on(date(2026, 7, 20))
    assert ids is None


def test_list_document_ids_with_runs_on_keeps_partial_data_on_later_failure(authenticated_client):
    """A failure on a later page (the endpoint clearly works -- we already
    got real data from it) keeps what was already collected instead of
    discarding it or returning None."""
    batch1 = [{"SI_ID": str(i), "SI_PARENTID": f"doc{i}"} for i in range(1, 201)]
    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"documents": batch1}
    fail_resp = MagicMock()
    fail_resp.status_code = 503
    fail_resp.text = "service unavailable"
    with patch.object(authenticated_client._session, "post", side_effect=[ok_resp, fail_resp]):
        ids = authenticated_client.list_document_ids_with_runs_on(date(2026, 7, 20))
    assert len(ids) == 200


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


def test_download_report_exports_from_occurrence_zero(authenticated_client):
    """The export must read the SAME resource the prompt answers were written
    to: occurrence 0. The 2026-08-04 live UI trace exports with
    GET .../documents/{id}/occurrences/0?dpi=96&optimized=true&reportIds={n}
    (Accept picks the format). Exporting from .../reports/{id} instead is what
    produced a valid workbook with column headers and zero data rows: that
    resource never saw the refreshed data providers."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"data"
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        authenticated_client.download_report("DOC1", "RPT2", "pdf")
    called_url = mock_get.call_args[0][0]
    assert called_url.endswith("/documents/DOC1/occurrences/0")
    assert "/reports/" not in called_url
    params = mock_get.call_args[1]["params"]
    assert params["dpi"] == "96"
    assert params["optimized"] == "true"
    assert params["reportIds"] == "RPT2"
    # `c` is the UI's cache buster. This deployment sits behind a proxy that has
    # already been caught re-serving cached GETs (it defeats `page` and
    # `Range:` on the document listing), so a repeat export of the same
    # document must not be answerable from cache.
    assert params["c"]


def test_download_report_falls_back_to_the_report_resource_when_occurrence_404s(
    authenticated_client, caplog
):
    """A document with NO prompts is downloaded straight from the UI with no
    preceding answer PUT, so nothing guarantees it has an occurrence 0 — that
    resource has only ever been observed on a prompted document. Rather than
    breaking those downloads, fall back to .../reports/{id}, which served them
    until 2026-08-04, and say so: on a prompted document that same resource is
    what exported layout with no data rows."""
    missing = MagicMock()
    missing.status_code = 404
    missing.text = 'the resource of type "Occurrence" with identifier "0" does not exist.'
    misspelled_missing = MagicMock()
    misspelled_missing.status_code = 404
    misspelled_missing.text = "not found"
    fallback = MagicMock()
    fallback.status_code = 200
    fallback.content = b"id,sku\n1,A100\n"
    fallback.headers = {}
    with patch.object(authenticated_client._session, "get",
                      side_effect=[missing, misspelled_missing, fallback]) as mock_get:
        with caplog.at_level("WARNING", logger="etl_framework.sap_bo.client"):
            result = authenticated_client.download_report("DOC1", "RPT2", "csv")

    assert result == fallback.content
    urls = [call[0][0] for call in mock_get.call_args_list]
    assert urls[0].endswith("/documents/DOC1/occurrences/0")
    assert urls[1].endswith("/documents/DOC1/occurences/0")
    assert urls[2].endswith("/documents/DOC1/reports/RPT2")
    assert "occurrence" in caplog.text.lower()


def test_download_report_exports_the_whole_document_when_no_report_is_named(
    authenticated_client,
):
    """SAP's primary step 5 is the whole document — every tab in one file —
    and only its *alternative* narrows to a single report. Naming no report
    must drop `reportIds` rather than send an empty one, which BO would read
    as "the tab called ''"."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"data"
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        authenticated_client.download_report("DOC1", "", "xlsx")
    assert mock_get.call_args[0][0].endswith("/documents/DOC1/occurrences/0")
    assert "reportIds" not in mock_get.call_args[1]["params"]


def test_download_report_whole_document_falls_back_to_the_document_resource(
    authenticated_client,
):
    """When there is no occurrence to read, a whole-document export has to fall
    back to SAP's documented whole-document resource — GET …/documents/{id}
    with the format's Accept — not to a per-report one it was never given a
    report for."""
    missing = MagicMock()
    missing.status_code = 404
    missing.text = "no occurrence"
    fallback = MagicMock()
    fallback.status_code = 200
    fallback.content = b"xlsx bytes"
    fallback.headers = {}
    with patch.object(authenticated_client._session, "get",
                      side_effect=[missing, missing, fallback]) as mock_get:
        result = authenticated_client.download_report("DOC1", "", "xlsx")

    assert result == fallback.content
    url = mock_get.call_args_list[2][0][0]
    assert url.endswith("/biprws/raylight/v1/documents/DOC1")
    accept = mock_get.call_args_list[2][1]["headers"]["Accept"]
    assert accept == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_download_report_retries_the_misspelled_occurrence_path(authenticated_client):
    """BO has shipped the segment both ways — the captured trace says
    `occurrences`, some releases say `occurences`. A deployment on the
    misspelled build must not be told it has no occurrence and silently
    downgraded to the resource that exports layout with no data rows."""
    missing = MagicMock()
    missing.status_code = 404
    missing.text = "not found"
    found = MagicMock()
    found.status_code = 200
    found.content = b"rows"
    found.headers = {}
    with patch.object(authenticated_client._session, "get",
                      side_effect=[missing, found]) as mock_get:
        result = authenticated_client.download_report("DOC1", "RPT2", "csv")

    assert result == found.content
    urls = [call[0][0] for call in mock_get.call_args_list]
    assert urls[0].endswith("/documents/DOC1/occurrences/0")
    assert urls[1].endswith("/documents/DOC1/occurences/0")


def test_answer_document_parameters_retries_the_misspelled_occurrence_path(
    authenticated_client,
):
    """Same spelling split on the write side. A 404 here used to raise, which
    would fail every prompted download on a misspelled-segment deployment."""
    missing = MagicMock()
    missing.status_code = 404
    missing.text = "not found"
    with patch.object(authenticated_client._session, "get", return_value=MagicMock(status_code=200)), \
         patch.object(authenticated_client._session, "put",
                      side_effect=[missing, _refreshed_put_response()]) as mock_put:
        authenticated_client.answer_document_parameters(
            "124313", [{"id": 0, "type": "DateTime", "value": "x"}])

    urls = [call[0][0] for call in mock_put.call_args_list]
    assert urls[0].endswith("/documents/124313/occurrences/0/parameters")
    assert urls[1].endswith("/documents/124313/occurences/0/parameters")


def test_answer_document_parameters_reuses_the_spelling_that_worked(authenticated_client):
    """A deployment does not change spelling between two requests. Probing it
    again on every call would double the write traffic of every prompted
    download on the deployments that need the retry most."""
    missing = MagicMock()
    missing.status_code = 404
    missing.text = "not found"
    with patch.object(authenticated_client._session, "get", return_value=MagicMock(status_code=200)), \
         patch.object(authenticated_client._session, "put",
                      side_effect=[missing, _refreshed_put_response(),
                                   _refreshed_put_response()]) as mock_put:
        authenticated_client.answer_document_parameters(
            "124313", [{"id": 0, "type": "DateTime", "value": "x"}])
        authenticated_client.answer_document_parameters(
            "124313", [{"id": 0, "type": "DateTime", "value": "y"}])

    urls = [call[0][0] for call in mock_put.call_args_list]
    assert len(urls) == 3
    assert urls[2].endswith("/documents/124313/occurences/0/parameters")


def test_answer_document_parameters_raises_when_neither_spelling_exists(
    authenticated_client,
):
    """The retry is for a spelling difference, not for a missing occurrence.
    When both 404, the failure is real and must surface — silently continuing
    to the export is what produced blank workbooks."""
    from etl_framework.exceptions import BOAPIError
    missing = MagicMock()
    missing.status_code = 404
    missing.text = "not found"
    with patch.object(authenticated_client._session, "get", return_value=MagicMock(status_code=200)), \
         patch.object(authenticated_client._session, "put", return_value=missing):
        with pytest.raises(BOAPIError):
            authenticated_client.answer_document_parameters(
                "124313", [{"id": 0, "type": "DateTime", "value": "x"}])


def test_download_report_does_not_fall_back_on_other_errors(authenticated_client):
    """Only a 404 means "no such occurrence". A 500 or a 403 must surface."""
    from etl_framework.exceptions import BOAPIError
    failed = MagicMock()
    failed.status_code = 500
    failed.text = "boom"
    with patch.object(authenticated_client._session, "get", return_value=failed) as mock_get:
        with pytest.raises(BOAPIError):
            authenticated_client.download_report("DOC1", "RPT2", "csv")
    assert mock_get.call_count == 1


def test_download_report_http_error_raises(authenticated_client):
    from etl_framework.exceptions import BOAPIError
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with pytest.raises(BOAPIError):
            authenticated_client.download_report("101", "1", "pdf")


# ---------------------------------------------------------------------------
# download_report export diagnostics
# ---------------------------------------------------------------------------

def _xlsx_with_rows(row_count: int) -> bytes:
    """Minimal xlsx: one worksheet carrying `row_count` <row> elements."""
    import io
    import zipfile

    rows = "".join(f'<row r="{i + 1}"><c r="A{i + 1}" t="inlineStr">'
                   f'<is><t>v{i}</t></is></c></row>' for i in range(row_count))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0"?><worksheet><sheetData>'
            f"{rows}</sheetData></worksheet>",
        )
    return buf.getvalue()


def test_download_report_logs_export_size_and_row_count(authenticated_client, caplog):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = _xlsx_with_rows(4)
    mock_response.headers = {"Content-Type": "application/vnd.openxmlformats"}
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with caplog.at_level("INFO", logger="etl_framework.sap_bo.client"):
            authenticated_client.download_report("DOC1", "RPT2", "xlsx")
    assert "rows=4" in caplog.text
    assert "bytes=" in caplog.text


def test_download_report_warns_and_probes_dataproviders_when_no_data_rows(
    authenticated_client, caplog
):
    """A header-only export is the on-premises blank-report symptom: the report
    layout comes back but no data rows. Say so, and dump the document's
    data-provider state so the next real run shows whether the data providers
    ever executed."""
    export = MagicMock()
    export.status_code = 200
    export.content = _xlsx_with_rows(1)
    export.headers = {"Content-Type": "application/vnd.openxmlformats"}
    probe = MagicMock()
    probe.status_code = 200
    probe.text = '{"dataproviders":{"dataprovider":[{"id":"DP0"}]}}'

    with patch.object(authenticated_client._session, "get",
                      side_effect=[export, probe, probe, probe, probe]) as mock_get:
        with caplog.at_level("INFO", logger="etl_framework.sap_bo.client"):
            result = authenticated_client.download_report("DOC1", "RPT2", "xlsx")

    assert result == export.content
    assert "collecting diagnostics" in caplog.text
    assert "DP0" in caplog.text
    urls = [call[0][0] for call in mock_get.call_args_list]
    assert any(u.endswith("/documents/DOC1/dataproviders") for u in urls)


def test_download_report_logs_cell_preview_so_layout_rows_are_recognisable(
    authenticated_client, caplog
):
    """A row count cannot tell "17 rows of data" from "17 rows of title, filter
    summary and column headers over an empty table" — the exact shape the
    on-premises server returned (rows=17, no data). The cell text can."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = _xlsx_with_rows(3)
    mock_response.headers = {}
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with caplog.at_level("INFO", logger="etl_framework.sap_bo.client"):
            authenticated_client.download_report("DOC1", "RPT2", "xlsx")
    assert "cell preview" in caplog.text
    assert "v0" in caplog.text and "v2" in caplog.text


def test_download_report_previews_csv_lines(authenticated_client, caplog):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"date,sku\n2026-07-29,A100\n"
    mock_response.headers = {}
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with caplog.at_level("INFO", logger="etl_framework.sap_bo.client"):
            authenticated_client.download_report("DOC1", "RPT2", "csv")
    assert "date,sku" in caplog.text


def test_export_diagnostics_can_be_forced_by_env_var(authenticated_client, monkeypatch):
    """The blank export on this deployment reports rows=17, so no row-count
    threshold fires on it without also firing on healthy pulls. Make the probes
    switchable on for a debugging run instead of guessing a threshold."""
    monkeypatch.setenv("ATOM_BO_EXPORT_DIAGNOSTICS", "1")
    export = MagicMock()
    export.status_code = 200
    export.content = _xlsx_with_rows(17)
    export.headers = {}
    probe = MagicMock()
    probe.status_code = 200
    probe.text = "{}"
    with patch.object(authenticated_client._session, "get",
                      side_effect=[export] + [probe] * 4) as mock_get:
        authenticated_client.download_report("DOC1", "RPT2", "xlsx")
    urls = [call[0][0] for call in mock_get.call_args_list]
    assert any(u.endswith("/dataproviders") for u in urls)


def test_download_report_does_not_probe_when_export_has_data(authenticated_client):
    export = MagicMock()
    export.status_code = 200
    export.content = _xlsx_with_rows(5)
    export.headers = {}
    with patch.object(authenticated_client._session, "get", return_value=export) as mock_get:
        authenticated_client.download_report("DOC1", "RPT2", "xlsx")
    assert mock_get.call_count == 1


def test_download_report_does_not_probe_for_pdf(authenticated_client):
    """Row counting only works for the tabular formats; a PDF must not be
    mistaken for a blank export."""
    export = MagicMock()
    export.status_code = 200
    export.content = b"%PDF-1.4 tiny"
    export.headers = {}
    with patch.object(authenticated_client._session, "get", return_value=export) as mock_get:
        authenticated_client.download_report("DOC1", "RPT2", "pdf")
    assert mock_get.call_count == 1


def test_download_report_survives_a_failing_dataprovider_probe(authenticated_client):
    """The probe is diagnostics only — it must never turn a successful export
    into an error."""
    export = MagicMock()
    export.status_code = 200
    export.content = b"id,name\n"          # header row only
    export.headers = {}
    with patch.object(authenticated_client._session, "get",
                      side_effect=[export, RuntimeError("probe boom")]):
        result = authenticated_client.download_report("DOC1", "RPT2", "csv")
    assert result == b"id,name\n"


def test_answer_document_parameters_logs_the_server_response(authenticated_client, caplog):
    """The answer PUT returning 200 does not prove the answer took effect; log
    what the server echoed back so a silently-ignored answer is visible."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"parameters":{"parameter":[{"id":0,"answer":{"info":"echoed"}}]}}'
    with patch.object(authenticated_client._session, "put", return_value=mock_response):
        with caplog.at_level("INFO", logger="etl_framework.sap_bo.client"):
            authenticated_client.answer_document_parameters(
                "DOC1", [{"id": 0, "type": "DateTime", "value": "2026-07-29T00:00:00.000Z"}]
            )
    assert "echoed" in caplog.text


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


def test_get_document_parameters_parses_nested_prompt_collection(authenticated_client):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"parameters": {"parameter": [
        {"id": 0, "name": "Start Date", "type": "DateTime", "mandatory": True},
        {"id": 1, "name": "Region", "type": "String"},
    ]}}
    with patch.object(authenticated_client._session, "get", return_value=resp) as mock_get:
        params = authenticated_client.get_document_parameters("124267")
    assert params == [
        {"id": 0, "name": "Start Date", "type": "DateTime", "mandatory": True, "default": ""},
        {"id": 1, "name": "Region", "type": "String", "mandatory": False, "default": ""},
    ]
    assert mock_get.call_args[0][0].endswith("/documents/124267/parameters")


def test_get_document_parameters_reads_datatype_from_nested_answer(authenticated_client):
    """Real BIP raylight nests the value data type under `answer` (top-level
    `type` is the parameter kind, "prompt"). The datatype must come from
    answer.type, else DateTime prompts are exported as raw text and BO 502s."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"parameters": {"parameter": [
        {"id": 0, "name": "Start Date", "type": "prompt", "mandatory": True,
         "answer": {"id": 0, "type": "DateTime"}},
        {"id": 1, "name": "Region", "type": "prompt",
         "answer": {"id": 1, "type": "String"}},
    ]}}
    with patch.object(authenticated_client._session, "get", return_value=resp):
        params = authenticated_client.get_document_parameters("124313")
    assert params == [
        {"id": 0, "name": "Start Date", "type": "DateTime", "mandatory": True, "default": ""},
        {"id": 1, "name": "Region", "type": "String", "mandatory": False, "default": ""},
    ]


def test_get_document_parameters_extracts_default_answer_value(authenticated_client):
    """A prompt carrying a current answer surfaces as an editable `default`;
    a DateTime instant is trimmed to YYYY-MM-DD for the date picker."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"parameters": {"parameter": [
        {"id": 0, "name": "Start Date", "type": "prompt", "mandatory": True,
         "answer": {"type": "DateTime", "values": {"value": [
             {"$": "2026-05-08T00:00:00.000Z", "@type": "DateTime"}]}}},
        {"id": 1, "name": "Region", "type": "prompt",
         "answer": {"type": "String", "values": {"value": [
             {"$": "ASX", "@type": "String"}]}}},
    ]}}
    with patch.object(authenticated_client._session, "get", return_value=resp):
        params = authenticated_client.get_document_parameters("124313")
    assert params == [
        {"id": 0, "name": "Start Date", "type": "DateTime", "mandatory": True, "default": "2026-05-08"},
        {"id": 1, "name": "Region", "type": "String", "mandatory": False, "default": "ASX"},
    ]


def test_get_document_parameters_404_raises_report_not_found(authenticated_client):
    from etl_framework.exceptions import ReportNotFoundError
    resp = MagicMock()
    resp.status_code = 404
    resp.text = "not found"
    with patch.object(authenticated_client._session, "get", return_value=resp):
        with pytest.raises(ReportNotFoundError):
            authenticated_client.get_document_parameters("999")


def test_answer_document_parameters_puts_trace_shaped_body(authenticated_client):
    """Reproduces the 2026-08-04 live UI trace byte for byte:
    PUT .../documents/124313/occurrences/0/parameters
        ?dataproviderScope=accessible&lovInfo=false&prepare=false

    Occurrence 0 — not the document-level parameters resource. Answering the
    document accepted the values with 200 but left the export blank; only the
    occurrence PUT reports allDataprovidersRefreshed=true, i.e. only it runs
    the refresh the export then reads. (The earlier 404 was for occurrence
    "1", a viewing session's own instance; index 0 is the persisted one.)"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"success": {"details": {"property": [
        {"@key": "allDataprovidersRefreshed", "$": "true"}]}}}
    with patch.object(authenticated_client._session, "put", return_value=resp) as mock_put:
        authenticated_client.answer_document_parameters(
            "124313",
            [{"id": 0, "type": "DateTime", "value": "2026-05-08T00:00:00.000Z"},
             {"id": 1, "type": "String", "value": "ASX"}],
        )
    url = mock_put.call_args[0][0]
    assert url.endswith("/documents/124313/occurrences/0/parameters")
    assert mock_put.call_args[1]["params"] == {
        "dataproviderScope": "accessible", "lovInfo": "false", "prepare": "false",
    }
    assert mock_put.call_args[1]["json"] == {"parameters": {"parameter": [
        {"id": 0, "answer": {"values": {"value": [
            {"$": "2026-05-08T00:00:00.000Z", "@type": "DateTime"}]}}},
        {"id": 1, "answer": {"values": {"value": [
            {"$": "ASX", "@type": "String"}]}}}]}}


def test_answer_document_parameters_warns_when_dataproviders_did_not_refresh(
    authenticated_client, caplog
):
    """`allDataprovidersRefreshed: "true"` is the only positive evidence in the
    whole flow that the answers reached the data. HTTP 200 is not — the blank
    export was a 200. A response without that flag must say so loudly, in the
    same run, instead of surfacing later as an empty workbook."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"success": {"message": "updated"}}
    with patch.object(authenticated_client._session, "put", return_value=resp):
        with caplog.at_level("WARNING", logger="etl_framework.sap_bo.client"):
            authenticated_client.answer_document_parameters(
                "124313", [{"id": 0, "type": "DateTime", "value": "x"}])
    assert "allDataprovidersRefreshed" in caplog.text


def test_answer_document_parameters_warns_about_blank_answers(authenticated_client, caplog):
    """The web UI sends every prompt and turns an untouched optional one into
    "" (frontend/features/adapters.js). An empty answer is not the same as an
    unanswered prompt — BO can filter on it and match nothing. Name the prompt
    in the log so a blank export has a suspect ready."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"success": {"details": {"property": [
        {"@key": "allDataprovidersRefreshed", "$": "true"}]}}}
    with patch.object(authenticated_client._session, "put", return_value=resp):
        with caplog.at_level("WARNING", logger="etl_framework.sap_bo.client"):
            authenticated_client.answer_document_parameters(
                "124313",
                [{"id": 0, "type": "DateTime", "value": "2026-05-08T00:00:00.000Z"},
                 {"id": 1, "type": "String", "value": ""}],
            )
    assert "empty" in caplog.text.lower()
    assert "[1]" in caplog.text


def test_answer_document_parameters_tolerates_a_non_json_response(authenticated_client):
    """The refresh-flag check is an observation, not a gate: a deployment that
    answers with something unparseable must not break the download."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("not json")
    resp.text = "<html>ok</html>"
    with patch.object(authenticated_client._session, "put", return_value=resp):
        authenticated_client.answer_document_parameters(
            "124313", [{"id": 0, "type": "DateTime", "value": "x"}])


def test_answer_document_parameters_raises_on_http_error(authenticated_client):
    from etl_framework.exceptions import BOAPIError
    resp = MagicMock()
    resp.status_code = 400
    resp.text = "bad prompt"
    with patch.object(authenticated_client._session, "put", return_value=resp):
        with pytest.raises(BOAPIError):
            authenticated_client.answer_document_parameters(
                "124267", [{"id": 0, "type": "DateTime", "value": "x"}])


def _refreshed_put_response(refreshed: str | None = "true") -> MagicMock:
    """A prompt-answer PUT response, with or without SAP BO's one piece of
    positive evidence that the data providers actually ran."""
    resp = MagicMock()
    resp.status_code = 200
    if refreshed is None:
        resp.json.return_value = {"success": {"message": "updated"}}
    else:
        resp.json.return_value = {"success": {"details": {"property": [
            {"@key": "allDataprovidersRefreshed", "$": refreshed}]}}}
    return resp


def test_answer_document_parameters_opens_the_document_before_answering(authenticated_client):
    """SAP's documented Raylight flow opens the document — GET
    …/documents/{id} — to create the server-side session cache BEFORE any
    parameter is written or any refresh triggered. This client had been
    writing straight to the occurrence, which only worked because occurrence 0
    happened to be reachable cold on this deployment; `probe_occurrence.py`
    was written precisely because "does it need the document opened first" was
    never settled. One idempotent GET removes the question."""
    order: list[tuple[str, str]] = []
    opened = MagicMock()
    opened.status_code = 200

    def fake_get(url, **kwargs):
        order.append(("GET", url))
        return opened

    put_resp = _refreshed_put_response()

    def fake_put(url, **kwargs):
        order.append(("PUT", url))
        return put_resp

    with patch.object(authenticated_client._session, "get", side_effect=fake_get), \
         patch.object(authenticated_client._session, "put", side_effect=fake_put):
        authenticated_client.answer_document_parameters(
            "124313", [{"id": 0, "type": "DateTime", "value": "2026-05-08T00:00:00.000Z"}])

    assert [method for method, _ in order] == ["GET", "PUT"]
    assert order[0][1].endswith("/biprws/raylight/v1/documents/124313")
    assert order[1][1].endswith("/documents/124313/occurrences/0/parameters")


def test_answer_document_parameters_survives_a_failed_document_open(
    authenticated_client, caplog
):
    """The open is a cache-seeding courtesy, not a gate. A deployment that
    refuses it must still get the answer PUT, whose own error is the one worth
    surfacing — but the failed open is logged so it cannot be the silent cause
    of a later blank export."""
    failed_open = MagicMock()
    failed_open.status_code = 500
    failed_open.text = "boom"
    with patch.object(authenticated_client._session, "get", return_value=failed_open), \
         patch.object(authenticated_client._session, "put",
                      return_value=_refreshed_put_response()) as mock_put:
        with caplog.at_level("WARNING", logger="etl_framework.sap_bo.client"):
            authenticated_client.answer_document_parameters(
                "124313", [{"id": 0, "type": "DateTime", "value": "x"}])
    assert mock_put.called
    assert "open" in caplog.text.lower()


def test_answer_document_parameters_posts_the_documented_refresh_when_the_put_did_not(
    authenticated_client,
):
    """SAP documents a separate refresh trigger — POST …/documents/{id}/parameters
    with an empty body — as the step that evaluates the stored answers and
    fetches rows. This client relied entirely on the occurrence PUT doing it,
    and merely *warned* when `allDataprovidersRefreshed` came back anything
    other than "true", then exported blank anyway. Use SAP's own escalation
    instead of exporting a workbook already known to be empty."""
    post_resp = _refreshed_put_response("true")
    with patch.object(authenticated_client._session, "get", return_value=MagicMock(status_code=200)), \
         patch.object(authenticated_client._session, "put",
                      return_value=_refreshed_put_response(None)), \
         patch.object(authenticated_client._session, "post", return_value=post_resp) as mock_post:
        authenticated_client.answer_document_parameters(
            "124313", [{"id": 0, "type": "DateTime", "value": "x"}])

    url = mock_post.call_args[0][0]
    assert url.endswith("/biprws/raylight/v1/documents/124313/parameters")
    assert mock_post.call_args[1]["json"] == {}


def test_answer_document_parameters_skips_the_refresh_post_when_the_put_refreshed(
    authenticated_client,
):
    """The occurrence PUT already ran the refresh on this deployment. Don't
    make every healthy download pay for a second execution of the data
    providers."""
    with patch.object(authenticated_client._session, "get", return_value=MagicMock(status_code=200)), \
         patch.object(authenticated_client._session, "put",
                      return_value=_refreshed_put_response("true")), \
         patch.object(authenticated_client._session, "post") as mock_post:
        authenticated_client.answer_document_parameters(
            "124313", [{"id": 0, "type": "DateTime", "value": "x"}])
    assert not mock_post.called


def test_answer_document_parameters_survives_a_failed_refresh_post(
    authenticated_client, caplog
):
    """The refresh POST is a recovery attempt for a state that was already
    going to export blank. If it fails, the export and its diagnostics must
    still run — failing here would replace an inspectable empty workbook with
    an opaque error."""
    failed_post = MagicMock()
    failed_post.status_code = 405
    failed_post.text = "method not allowed"
    with patch.object(authenticated_client._session, "get", return_value=MagicMock(status_code=200)), \
         patch.object(authenticated_client._session, "put",
                      return_value=_refreshed_put_response(None)), \
         patch.object(authenticated_client._session, "post", return_value=failed_post):
        with caplog.at_level("WARNING", logger="etl_framework.sap_bo.client"):
            authenticated_client.answer_document_parameters(
                "124313", [{"id": 0, "type": "DateTime", "value": "x"}])
    assert "405" in caplog.text
