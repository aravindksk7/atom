"""Tests for GET /api/runs/{run_id}/junit."""
from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _make_run_with_results(run_id="run-junit-api-1"):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    from etl_framework.repository.models import TestResult

    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        repo.create_run(run_id=run_id, source_env="dev", target_env="qa")
        repo.update_run_status(run_id, "FAILED", total_tests=2, passed=1, failed=1)
        db.add(TestResult(
            run_id=run_id, query_name="orders_recon", status="PASSED",
            duration_seconds=12.4,
        ))
        db.add(TestResult(
            run_id=run_id, query_name="customer_feed", status="FAILED",
            duration_seconds=3.2, value_mismatch_count=5,
        ))
        db.commit()


def test_junit_endpoint_returns_xml_with_testcases(client):
    _make_run_with_results()
    resp = client.get("/api/runs/run-junit-api-1/junit")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(resp.text)
    suite = root.find("testsuite")
    assert suite.get("tests") == "2"
    assert suite.get("failures") == "1"
    names = [c.get("name") for c in suite.findall("testcase")]
    assert names == ["orders_recon", "customer_feed"]


def test_junit_endpoint_unknown_run_returns_404(client):
    resp = client.get("/api/runs/does-not-exist/junit")
    assert resp.status_code == 404


def test_junit_endpoint_requires_auth(client):
    _make_run_with_results(run_id="run-junit-api-2")
    resp = client.get(
        "/api/runs/run-junit-api-2/junit",
        headers={"Authorization": ""},
    )
    assert resp.status_code == 401
