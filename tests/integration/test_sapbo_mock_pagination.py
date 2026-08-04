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
    document's prompts (GET …/parameters), answer them (PUT …/occurrences/0
    /parameters), then export that occurrence. Proves the discovery GET and the answering PUT
    agree wire-to-wire with BORestClient."""
    doc_id, report_id = "1001", "rpt-sales"

    params = client.get_document_parameters(doc_id)
    assert any(p["type"] == "DateTime" for p in params)

    client.answer_document_parameters(
        doc_id,
        [{"id": 0, "type": "DateTime", "value": "2026-06-01T23:00:00.000Z"}],
    )

    data = client.download_report(doc_id, report_id, "csv")
    # Not just "a file came back" — the live bug returned a well-formed file
    # with the layout and no data rows, at HTTP 200. Assert on the rows.
    assert b"A100" in data


def test_answered_date_reaches_the_export(client, sapbo_mock_server):
    """The whole point of the two-step occurrence-0 flow: the answered prompt
    has to change what the export contains. Doc 1003's rows differ per answered
    day, so a download that ignored the answer cannot pass this."""
    doc_id, report_id = "1003", "rpt-daily-sales"

    client.answer_document_parameters(
        doc_id,
        [{"id": 0, "type": "DateTime", "value": "2026-06-03T00:00:00.000Z"},
         {"id": 1, "type": "String", "value": "ASX"}],
    )
    data = client.download_report(doc_id, report_id, "csv")

    assert b"D400" in data and b"E500" in data
    assert b"A100" not in data          # the 2026-06-02 row set


def test_mock_rejects_occurrence_1_answer_like_the_live_server(sapbo_mock_server):
    """Pins the one occurrence behaviour actually observed against the live
    server: a 404 for identifier "1", the session-scoped id copied out of a
    captured browser trace. Scoped deliberately to identifier 1 — index 0
    is the document's persisted occurrence and answers there."""
    import requests

    (host, port), module = sapbo_mock_server
    resp = requests.put(
        f"http://{host}:{port}"
        "/biprws/raylight/v1/documents/1001/occurrences/1/parameters",
        json={"parameters": {"parameter": []}},
        headers={"X-SAP-LogonToken": module.TOKEN, "Content-Type": "application/json"},
        timeout=10,
    )

    assert resp.status_code == 404
    assert "Occurrence" in resp.text


def test_occurrence_0_answer_reports_the_refresh_like_the_live_server(sapbo_mock_server):
    """Occurrence 0 is the persisted one, and answering it reports
    allDataprovidersRefreshed=true — the 2026-08-04 live trace. That flag is
    the only positive evidence the flow produces, so the mock has to emit it
    for the client's warning path to mean anything."""
    import requests

    (host, port), module = sapbo_mock_server
    resp = requests.put(
        f"http://{host}:{port}"
        "/biprws/raylight/v1/documents/1001/occurrences/0/parameters",
        json={"parameters": {"parameter": []}},
        headers={"X-SAP-LogonToken": module.TOKEN, "Content-Type": "application/json"},
        timeout=10,
    )

    assert resp.status_code == 200
    props = resp.json()["success"]["details"]["property"]
    assert {"@key": "allDataprovidersRefreshed", "$": "true"} in props


def test_document_level_answer_is_accepted_but_leaves_the_export_blank(sapbo_mock_server):
    """The failure being fixed, pinned so it can't come back silently: the
    document-level PUT returns 200 with no refresh flag, and the
    …/reports/{id} export of a prompted document then yields the layout with
    zero data rows."""
    import requests

    (host, port), module = sapbo_mock_server
    base = f"http://{host}:{port}/biprws/raylight/v1/documents/1001"
    headers = {"X-SAP-LogonToken": module.TOKEN, "Content-Type": "application/json"}

    answered = requests.put(
        f"{base}/parameters",
        json={"parameters": {"parameter": [{"id": 0, "answer": {"values": {"value": [
            {"$": "2026-06-03T00:00:00.000Z", "@type": "DateTime"}]}}}]}},
        headers=headers,
        timeout=10,
    )
    assert answered.status_code == 200
    assert "allDataprovidersRefreshed" not in answered.text

    export = requests.get(
        f"{base}/reports/rpt-sales",
        headers={"X-SAP-LogonToken": module.TOKEN, "Accept": "text/csv"},
        timeout=10,
    )
    assert export.status_code == 200          # 200 and a well-formed file …
    assert b"A100" not in export.content      # … with no data in it


def test_answering_opens_the_document_without_a_failed_open_warning(
    client, sapbo_mock_server, caplog
):
    """SAP's documented flow opens the document before writing parameters, and
    the client now does. The mock has to serve that GET: an open the mock 404s
    would train every e2e run to show a "could not open" warning, which is the
    exact line that has to stay meaningful when a real deployment refuses it."""
    with caplog.at_level("WARNING", logger="etl_framework.sap_bo.client"):
        client.answer_document_parameters(
            "1001",
            [{"id": 0, "type": "DateTime", "value": "2026-06-01T23:00:00.000Z"}],
        )

    assert "could not open document" not in caplog.text.lower()
