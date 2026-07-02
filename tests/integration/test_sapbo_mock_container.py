from __future__ import annotations

import os
import time

import pytest
import requests
import urllib3

from etl_framework.config.models import EnvironmentConfig
from etl_framework.sap_bo.client import BORestClient


pytestmark = [
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_SAPBO_TESTS") != "1",
        reason="set RUN_LIVE_SAPBO_TESTS=1 and start docker-compose.integration.yml sapbo",
    ),
    pytest.mark.filterwarnings("ignore:Unverified HTTPS request"),
]


HOST = os.getenv("LIVE_SAPBO_HOST", "127.0.0.1")
PORT = int(os.getenv("LIVE_SAPBO_PORT", "18443"))
USER = os.getenv("LIVE_SAPBO_USER", "administrator")
PASSWORD = os.getenv("LIVE_SAPBO_PASSWORD", "Password1")
BASE_URL = f"https://{HOST}:{PORT}"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _wait_for_sapbo() -> None:
    last_error: Exception | None = None
    for _ in range(30):
        try:
            response = requests.get(f"{BASE_URL}/health", timeout=2, verify=False)
            response.raise_for_status()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise AssertionError(f"SAP BO mock did not become ready: {last_error}")


def _env() -> EnvironmentConfig:
    return EnvironmentConfig(
        name="sapbo-mock",
        db_host="unused",
        db_password="unused",
        bo_url=BASE_URL,
        bo_user=USER,
        bo_password=PASSWORD,
        bo_timeout=5,
        bo_verify_ssl=False,
    )


def test_sapbo_mock_supports_bo_rest_client_flows():
    _wait_for_sapbo()

    client = BORestClient(_env())
    client.authenticate()

    documents = client.list_documents()
    assert documents[0]["id"] == "1001"

    reports = client.list_reports("1001")
    assert reports[0]["id"] == "rpt-sales"

    df = client.fetch_report_data("1001")
    assert list(df.columns) == ["id", "sku", "amount", "status"]
    assert len(df) == 3

    csv_bytes = client.download_report("1001", "rpt-sales", "csv")
    assert b"id,sku,amount,status" in csv_bytes

    xlsx_bytes = client.download_report("1001", "rpt-sales", "xlsx")
    assert xlsx_bytes.startswith(b"PK")


def test_sapbo_mock_lists_reports_for_document_with_single_report_tab():
    """Document 1002 has exactly one report tab, so the mock (like a real
    on-premises biprws server) serializes 'reports' as a bare object instead
    of a one-element array. list_reports must not crash on that shape."""
    _wait_for_sapbo()

    client = BORestClient(_env())
    client.authenticate()

    reports = client.list_reports("1002")
    assert reports == [{"id": "rpt-inventory", "name": "Inventory", "reportIndex": 0}]


def test_sapbo_mock_rejects_mismatched_auth_type():
    """Mirrors on-premises AD deployments: the mock (like a real CMS) only accepts
    the auth type it's configured for, so a mismatched bo_auth_type must surface
    as an authentication failure rather than silently connecting."""
    _wait_for_sapbo()

    from etl_framework.exceptions import BOAPIError

    cfg = _env().model_copy(update={"bo_auth_type": "secWinAD"})
    client = BORestClient(cfg)

    with pytest.raises(BOAPIError) as exc_info:
        client.authenticate()
    assert exc_info.value.http_status == 401
