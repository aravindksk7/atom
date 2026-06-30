"""Positional (row-index) fallback when no key column can be inferred from CSV data."""
from __future__ import annotations
import io
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import RunRepository, TokenRepository
from api.main import app
from api.routes import compare as compare_module
import base64


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SL = sessionmaker(bind=engine)
    monkeypatch.setattr(_db_module, "SessionLocal", SL)

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


# CSV with no standard key column (Product + Revenue only)
CSV_A = "Product,Revenue\nWidgets,100\nGadgets,200\n"
CSV_B = "Product,Revenue\nWidgets,110\nGadgets,200\n"  # Widgets revenue differs


def test_positional_fallback_completes_without_error(client, monkeypatch):
    """Run should reach PASSED/FAILED, not ERROR, when no key can be inferred."""
    called_with = {}

    original = __import__(
        "api.services.compare_service", fromlist=["CompareService"]
    ).CompareService.run_recon_file_compare

    def patched(self, req, run_id):
        called_with["req"] = req
        called_with["run_id"] = run_id
        original(self, req, run_id)

    monkeypatch.setattr(
        "api.services.compare_service.CompareService.run_recon_file_compare",
        patched,
    )

    resp = client.post("/api/compare/recon-file", json={
        "file_a_content_b64": _b64(CSV_A),
        "file_a_name": "source.csv",
        "file_b_content_b64": _b64(CSV_B),
        "file_b_name": "prod.csv",
        "label_a": "Source",
        "label_b": "Prod",
    })
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    # Poll until done (background task runs synchronously in TestClient)
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] in ("PASSED", "FAILED"), f"Expected PASSED/FAILED, got {detail['status']}"
    assert len(detail["results"]) == 1


def test_positional_fallback_detects_value_diff(client):
    """When row 1 differs, the reconciliation should report FAILED + mismatches."""
    resp = client.post("/api/compare/recon-file", json={
        "file_a_content_b64": _b64(CSV_A),
        "file_a_name": "a.csv",
        "file_b_content_b64": _b64(CSV_B),
        "file_b_name": "b.csv",
    })
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "FAILED"
    result_id = detail["results"][0]["id"]
    mismatches = client.get(f"/api/runs/{run_id}/results/{result_id}/mismatches").json()
    assert any(m["column_name"] == "Revenue" for m in mismatches)


def test_identical_csvs_without_key_pass(client):
    """Two identical CSVs with no key column should compare as PASSED."""
    resp = client.post("/api/compare/recon-file", json={
        "file_a_content_b64": _b64(CSV_A),
        "file_a_name": "a.csv",
        "file_b_content_b64": _b64(CSV_A),
        "file_b_name": "b.csv",
    })
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "PASSED"
