from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from api.routes import runs as runs_module
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base, get_db
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository

pytestmark = pytest.mark.usefixtures("pdf_env")


def _up(path: Path, name: str | None = None) -> dict[str, str]:
    return {
        "source_type": "upload",
        "file_name": name or path.name,
        "file_content_b64": base64.b64encode(path.read_bytes()).decode(),
    }


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(runs_module, "_execute_run", lambda *a, **kw: None)
    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


def test_preview_upload_returns_tables_and_suggested_key(client, real_pdf, bo_expected):
    e = bo_expected["sample3"]
    r = client.post("/api/compare/bo-report/pdf-preview", json={"source": _up(real_pdf("sample3.pdf"))})
    assert r.status_code == 200
    body = r.json()
    assert body["chosen"]["suggested_key"] == e["key"][0]
    assert body["chosen"]["column_types"] == e["column_types"]


def test_preview_rejects_non_pdf(client):
    r = client.post(
        "/api/compare/bo-report/pdf-preview",
        json={"source": {"source_type": "upload", "file_name": "a.csv", "file_content_b64": "eA=="}},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "source is not a PDF"


def test_preview_requires_auth(real_pdf):
    with TestClient(app) as anon:
        baseline = anon.post("/api/compare/bo-report", json={}).status_code
        r = anon.post("/api/compare/bo-report/pdf-preview", json={"source": _up(real_pdf("sample3.pdf"))})
    assert r.status_code == baseline and r.status_code in (401, 403)


def test_bo_report_without_key_is_a_synchronous_422_with_suggestion(client, real_pdf, bo_expected):
    p = real_pdf("sample3.pdf")
    r = client.post("/api/compare/bo-report", json={"source_a": _up(p), "source_b": _up(p)})
    assert r.status_code == 422
    assert r.json()["detail"] == {
        "message": "set key columns for a PDF compare",
        "suggested_key": bo_expected["sample3"]["key"][0],
    }
    assert client.get("/api/runs").json() == []


def test_bo_report_one_pdf_side_is_a_synchronous_422(client, real_pdf):
    r = client.post(
        "/api/compare/bo-report",
        json={
            "source_a": {"source_type": "path", "file_path": "/a.xlsx"},
            "source_b": _up(real_pdf("sample3.pdf")),
            "key_columns": ["K"],
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "a PDF compare needs a PDF on both sides"


def test_bo_report_busy_is_503(client, real_pdf, bo_expected, monkeypatch):
    import api.services.compare_service as cs_mod
    from etl_framework.bo_layout.model import PdfReadError

    def busy(*a, **k):
        raise PdfReadError("busy", "PDF reader busy")

    monkeypatch.setattr(cs_mod, "read_pdf_report", busy)
    p = real_pdf("sample3.pdf")
    r = client.post(
        "/api/compare/bo-report",
        json={"source_a": _up(p), "source_b": _up(p), "key_columns": bo_expected["sample3"]["key"]},
    )
    assert (r.status_code, r.json()["detail"]) == (503, "PDF reader busy")


def test_bo_report_valid_pdf_request_returns_202(client, real_pdf, bo_expected):
    p = real_pdf("sample3.pdf")
    r = client.post(
        "/api/compare/bo-report",
        json={"source_a": _up(p), "source_b": _up(p), "key_columns": bo_expected["sample3"]["key"]},
    )
    assert r.status_code == 202
