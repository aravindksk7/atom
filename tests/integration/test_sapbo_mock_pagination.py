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
