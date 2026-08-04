"""The web UI's SAP BO download, end to end with nothing mocked but the server.

Covers exactly what a user does in Adapters → SAP BO → browse documents →
expand a document → fill the date prompt → Download xlsx:

    GET  /api/adapters/sap-bo/documents                       (browse)
    GET  /api/adapters/sap-bo/documents/{id}/reports          (expand)
    GET  /api/adapters/sap-bo/documents/{id}/parameters       (prompt discovery)
    POST /api/adapters/sap-bo/documents/{id}/reports/{r}/download   (answer+export)

Every layer is the real one — FastAPI routes, AdapterService, BORestClient —
against docker/sapbo-mock/server.py over a loopback socket. tests/unit/
test_adapters_routes.py replaces AdapterService with a MagicMock, so it can
prove the routes' shape and nothing about the wire; this proves the flow the
2026-08-04 fix changed (answer + export both on occurrence 0) survives all the
way from the endpoint the browser calls.

The assertion is on cell contents, never on the status code: the failure this
guards against returned HTTP 200 and a valid workbook with the report layout
and zero data rows.
"""
from __future__ import annotations

import io
import sys
import threading
import zipfile
from http.server import HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "docker" / "sapbo-mock"))

from etl_framework.repository.database import Base
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import ConfigRepository, TokenRepository


@pytest.fixture
def mock_bo():
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
def api(mock_bo, monkeypatch):
    """The real app, with one saved config pointing at the in-process mock."""
    from api.main import app

    (host, port), module = mock_bo
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
        cfg = ConfigRepository(db).create(
            name="sapbo-mock",
            env_name="mock",
            config_data={
                "db_host": "unused",
                "db_password": "unused",
                "bo_url": f"http://{host}:{port}",
                "bo_user": module.USER,
                "bo_password": module.PASSWORD,
                "bo_timeout": 5,
            },
        )
        config_id = cfg.id
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as client:
        yield client, config_id


def _sheet_text(xlsx: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(xlsx)) as archive:
        return archive.read("xl/worksheets/sheet1.xml").decode("utf-8")


def test_ui_browse_answer_and_download_xlsx(api):
    """Document 1003's rows differ per answered day, so an export that ignored
    the date prompt cannot pass this."""
    client, config_id = api

    documents = client.get(f"/api/adapters/sap-bo/documents?config_id={config_id}")
    assert documents.status_code == 200
    assert "1003" in {d["id"] for d in documents.json()}

    reports = client.get(
        f"/api/adapters/sap-bo/documents/1003/reports?config_id={config_id}")
    assert reports.status_code == 200
    assert reports.json()[0]["id"] == "rpt-daily-sales"

    params = client.get(
        f"/api/adapters/sap-bo/documents/1003/parameters?config_id={config_id}")
    assert params.status_code == 200
    assert [p["type"] for p in params.json()] == ["DateTime", "String"]

    # Exactly what frontend/features/adapters.js posts: every prompt, the date
    # as the picker's bare YYYY-MM-DD, types echoed from the listing above.
    download = client.post(
        f"/api/adapters/sap-bo/documents/1003/reports/rpt-daily-sales/download"
        f"?config_id={config_id}",
        json={"format": "xlsx", "parameters": [
            {"id": 0, "type": "DateTime", "value": "2026-06-03"},
            {"id": 1, "type": "String", "value": "ASX"},
        ]},
    )

    assert download.status_code == 200
    assert download.content.startswith(b"PK")
    assert "spreadsheetml" in download.headers["content-type"]
    assert 'filename="report_1003_rpt-daily-sales.xlsx"' in \
        download.headers["content-disposition"]

    sheet = _sheet_text(download.content)
    assert "D400" in sheet and "E500" in sheet     # the answered day's rows
    assert "A100" not in sheet                     # the other day's rows


def test_ui_download_honours_a_different_answered_date(api):
    """Second date, different rows — pins that the answer drives the export
    rather than the export happening to contain data."""
    client, config_id = api

    download = client.post(
        f"/api/adapters/sap-bo/documents/1003/reports/rpt-daily-sales/download"
        f"?config_id={config_id}",
        json={"format": "xlsx", "parameters": [
            {"id": 0, "type": "DateTime", "value": "2026-06-02"},
            {"id": 1, "type": "String", "value": "ASX"},
        ]},
    )

    sheet = _sheet_text(download.content)
    assert "A100" in sheet
    assert "D400" not in sheet


def test_ui_download_accepts_the_lowercase_type_the_live_listing_reports(api):
    """The live listing reported document 124313's string prompt as lowercase
    'string' while the answer PUT requires "String". The UI echoes the listing's
    type verbatim, so the normalisation has to happen server-side."""
    client, config_id = api

    download = client.post(
        f"/api/adapters/sap-bo/documents/1003/reports/rpt-daily-sales/download"
        f"?config_id={config_id}",
        json={"format": "xlsx", "parameters": [
            {"id": 0, "type": "datetime", "value": "2026-06-03"},
            {"id": 1, "type": "string", "value": "ASX"},
        ]},
    )

    assert download.status_code == 200
    assert "D400" in _sheet_text(download.content)


def test_ui_download_without_prompts_uses_the_plain_get(api):
    """Document 1002 has no prompts, so the UI takes the GET path with no
    answer PUT before it — the case that has no occurrence to rely on."""
    client, config_id = api

    download = client.get(
        f"/api/adapters/sap-bo/documents/1002/reports/rpt-inventory/download"
        f"?config_id={config_id}&format=xlsx"
    )

    assert download.status_code == 200
    assert "A100" in _sheet_text(download.content)
