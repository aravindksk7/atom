"""End-to-end coverage for BORestClient pagination against the on-premises
CMC page-size cap, run against the *actual* docker/sapbo-mock/server.py
handler over a real (plain-HTTP, in-process) socket — not the mocked
`_session.get` used by tests/unit/test_bo_rest_client.py's
test_list_documents_pages_past_server_enforced_page_cap.

That unit test proves the pagination loop's *logic* is correct in isolation.
This test proves the real mock server's PAGE_CAP slicing (server.py's
DOCUMENTS[start:end]/REPORTS[doc_id][start:end]) and BORestClient actually
agree wire-to-wire: the bulk fixtures (25 extra documents, one with 25 report
tabs — server.py's _BULK_DOC_COUNT/_BULK_REPORT_COUNT) only get exercised if
PAGE_CAP is below their count, which is exactly the scenario an on-prem CMC
page-size cap creates.

No docker/TLS needed: SAPBOMockHandler is a plain BaseHTTPRequestHandler, so
it's spun up directly with http.server.HTTPServer on a loopback port.
"""
from __future__ import annotations

import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "docker" / "sapbo-mock"))

from etl_framework.config.models import EnvironmentConfig
from etl_framework.sap_bo.client import BORestClient


@pytest.fixture
def sapbo_mock_server():
    import server as sapbo_mock_module

    httpd = HTTPServer(("127.0.0.1", 0), sapbo_mock_module.SAPBOMockHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address, sapbo_mock_module
    finally:
        httpd.shutdown()
        thread.join()


@pytest.fixture
def client(sapbo_mock_server):
    address, _module = sapbo_mock_server
    host, port = address
    cfg = EnvironmentConfig(
        name="sapbo-mock-inprocess",
        db_host="unused",
        db_password="unused",
        bo_url=f"http://{host}:{port}",
        bo_user="administrator",
        bo_password="Password1",
        bo_timeout=5,
    )
    c = BORestClient(cfg)
    c.authenticate()
    return c


def test_list_documents_pages_past_real_mock_server_page_cap(client, sapbo_mock_server):
    _address, module = sapbo_mock_server
    documents = client.list_documents()

    assert len(documents) == len(module.DOCUMENTS)
    assert {d["id"] for d in documents} == {d["id"] for d in module.DOCUMENTS}


def test_list_reports_pages_past_real_mock_server_page_cap(client, sapbo_mock_server):
    _address, module = sapbo_mock_server
    bulk_doc_id = next(
        doc_id for doc_id, reports in module.REPORTS.items() if len(reports) > module.PAGE_CAP
    )

    reports = client.list_reports(bulk_doc_id)

    assert len(reports) == len(module.REPORTS[bulk_doc_id])
    assert {r["id"] for r in reports} == {r["id"] for r in module.REPORTS[bulk_doc_id]}


def test_discover_answer_download_flow_against_real_mock(client, sapbo_mock_server):
    """End-to-end date-prompt flow against the real mock server: discover the
    document's prompts (GET …/parameters), answer them (PUT …/parameters),
    then export the report. Proves the discovery GET and the answering PUT
    agree wire-to-wire with BORestClient."""
    doc_id, report_id = "1001", "rpt-sales"

    params = client.get_document_parameters(doc_id)
    assert any(p["type"] == "DateTime" for p in params)

    client.answer_document_parameters(
        doc_id,
        [{"id": 0, "type": "DateTime", "value": "2026-06-01T23:00:00.000Z"}],
    )

    data = client.download_report(doc_id, report_id, "xlsx")
    assert data


def test_mock_rejects_occurrence_1_answer_like_the_live_server(sapbo_mock_server):
    """Pins the one occurrence behaviour actually observed against the live
    server: a 404 for identifier "1", the session-scoped id copied out of a
    captured browser trace. Scoped deliberately to identifier 1 — see
    test_mock_takes_no_position_on_occurrence_0 for why."""
    import requests

    (host, port), _module = sapbo_mock_server
    resp = requests.put(
        f"http://{host}:{port}"
        "/biprws/raylight/v1/documents/1001/occurrences/1/parameters",
        json={"parameters": {"parameter": []}},
        headers={"X-SAP-LogonToken": "test-token", "Content-Type": "application/json"},
        timeout=10,
    )

    assert resp.status_code == 404
    assert "Occurrence" in resp.text


def test_mock_takes_no_position_on_occurrence_0(sapbo_mock_server):
    """Occurrence 0 is an open question, and the mock must not answer it.

    The 404 above was for identifier "1" and was generalised to "a stateless
    client has no occurrence at all". Index 0 was never tried, and the
    on-premises UI was later observed reading report data from
    …/documents/{id}/occurences/0?reportids={n}. If the mock kept emitting the
    live server's typed "Occurrence does not exist" error for index 0 it would
    fail a correct fix, which is exactly the rubber-stamping this mock exists
    to prevent — just inverted.

    So index 0 gets the generic no-handler 404: still a 404, but without the
    live server's error vocabulary, marking absence of evidence rather than
    evidence of absence. Replace this test with the real behaviour once the
    live probe reports it.
    """
    import requests

    (host, port), _module = sapbo_mock_server
    resp = requests.put(
        f"http://{host}:{port}"
        "/biprws/raylight/v1/documents/1001/occurences/0/parameters",
        json={"parameters": {"parameter": []}},
        headers={"X-SAP-LogonToken": "test-token", "Content-Type": "application/json"},
        timeout=10,
    )

    assert "Occurrence" not in resp.text
