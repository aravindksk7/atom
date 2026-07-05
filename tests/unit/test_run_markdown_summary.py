"""Tests for GET /api/runs/{run_id}/markdown-summary."""
from __future__ import annotations

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


def _make_run_with_results(client, run_id="run-md-1", ci_context=None):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    from etl_framework.repository.models import TestResult

    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        run = repo.create_run(run_id=run_id, source_env="dev", target_env="qa", ci_context=ci_context)
        repo.update_run_status(run_id, "FAILED", total_tests=2, passed=1, failed=1)
        db.add(TestResult(
            run_id=run_id, query_name="orders_recon", status="PASSED",
            duration_seconds=12.4, source_row_count=10, target_row_count=10,
            value_mismatch_count=0, missing_in_target_count=0, missing_in_source_count=0,
        ))
        db.add(TestResult(
            run_id=run_id, query_name="customer_feed", status="FAILED",
            duration_seconds=3.2, source_row_count=10, target_row_count=9,
            value_mismatch_count=0, missing_in_target_count=1, missing_in_source_count=0,
        ))
        db.commit()


def test_markdown_summary_lists_each_job_with_status(client):
    _make_run_with_results(client)
    resp = client.get("/api/runs/run-md-1/markdown-summary")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    body = resp.text
    assert "orders_recon" in body
    assert "customer_feed" in body
    assert "✅" in body
    assert "❌" in body


def test_markdown_summary_shows_ci_context_when_present(client):
    _make_run_with_results(client, run_id="run-md-2", ci_context={
        "commit_sha": "a1b2c3d", "pipeline_url": "https://gitlab.example.com/p/4821", "ref": "main",
    })
    resp = client.get("/api/runs/run-md-2/markdown-summary")
    assert "a1b2c3d" in resp.text
    assert "https://gitlab.example.com/p/4821" in resp.text


def test_markdown_summary_shows_manual_when_no_ci_context(client):
    _make_run_with_results(client, run_id="run-md-3")
    resp = client.get("/api/runs/run-md-3/markdown-summary")
    assert "manual" in resp.text.lower()


def test_markdown_summary_missing_run_returns_404(client):
    resp = client.get("/api/runs/does-not-exist/markdown-summary")
    assert resp.status_code == 404
