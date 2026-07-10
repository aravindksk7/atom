"""Tests for GET /runs/{run_id}/mismatches/insights."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus


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


def _seed_two_results(run_id: str):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    from etl_framework.repository.models import MismatchDetail

    db = _db_module.SessionLocal()
    try:
        repo = RunRepository(db)
        repo.create_run(run_id, "dev", "qa", {})

        result_a = repo.add_test_result(run_id, ReconciliationResult(
            query_name="orders", source_env="dev", target_env="qa",
            source_row_count=10, target_row_count=10, matched_count=7,
            missing_in_target_count=1, missing_in_source_count=1,
            value_mismatch_count=3, mismatches=[],
            status=TestStatus.FAILED, executed_at=datetime.now(timezone.utc),
            duration_seconds=1.0,
            mismatch_summary={
                "by_column": {"amount": 2, "status": 1},
                "by_type": {"value_diff": 3, "missing_in_target": 1, "missing_in_source": 1},
            },
        ))
        repo.add_mismatch_details(result_a.id, [
            MismatchRecord(key_values={"id": 1}, column_name="amount", source_value="1", target_value="2", mismatch_type="value_diff"),
            MismatchRecord(key_values={"id": 2}, column_name="amount", source_value="1", target_value="2", mismatch_type="value_diff"),
            MismatchRecord(key_values={"id": 3}, column_name="status", source_value="A", target_value="B", mismatch_type="value_diff"),
            MismatchRecord(key_values={"id": 4}, column_name=None, source_value=None, target_value=None, mismatch_type="missing_in_target"),
            MismatchRecord(key_values={"id": 5}, column_name=None, source_value=None, target_value=None, mismatch_type="missing_in_source"),
        ])

        result_b = repo.add_test_result(run_id, ReconciliationResult(
            query_name="invoices", source_env="dev", target_env="qa",
            source_row_count=5, target_row_count=5, matched_count=4,
            missing_in_target_count=0, missing_in_source_count=0,
            value_mismatch_count=1, mismatches=[],
            status=TestStatus.FAILED, executed_at=datetime.now(timezone.utc),
            duration_seconds=0.5,
            mismatch_summary={
                "by_column": {"amount": 1},
                "by_type": {"value_diff": 1, "missing_in_target": 0, "missing_in_source": 0},
            },
        ))
        repo.add_mismatch_details(result_b.id, [
            MismatchRecord(key_values={"id": 1}, column_name="amount", source_value="5", target_value="6", mismatch_type="value_diff"),
        ])

        # Mark one mismatch on result_a as accepted.
        first = (
            db.query(MismatchDetail)
            .filter(MismatchDetail.test_result_id == result_a.id)
            .first()
        )
        first.accepted = True
        db.commit()

        return result_a.id, result_b.id
    finally:
        db.close()


def test_insights_aggregates_across_results(client):
    run_id = str(uuid.uuid4())
    result_a_id, result_b_id = _seed_two_results(run_id)

    resp = client.get(f"/api/runs/{run_id}/mismatches/insights")
    assert resp.status_code == 200
    data = resp.json()

    assert data["run_id"] == run_id
    columns = {c["column"]: c["count"] for c in data["top_columns"]}
    assert columns["amount"] == 3
    assert columns["status"] == 1
    assert data["type_totals"]["value_diff"] == 4
    assert data["type_totals"]["missing_in_target"] == 1
    assert data["type_totals"]["missing_in_source"] == 1
    assert data["accepted_count"] == 1
    assert data["open_count"] == 5

    tests_by_name = {t["query_name"]: t for t in data["tests"]}
    assert tests_by_name["orders"]["result_id"] == result_a_id
    assert tests_by_name["orders"]["total_issues"] == 5
    assert tests_by_name["orders"]["stored_rows"] == 5
    assert tests_by_name["orders"]["stored_complete"] is True
    assert tests_by_name["invoices"]["result_id"] == result_b_id


def test_insights_404_for_unknown_run(client):
    resp = client.get("/api/runs/no-such-run/mismatches/insights")
    assert resp.status_code == 404


def test_insights_empty_run_returns_zeroed_aggregates(client):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository

    run_id = str(uuid.uuid4())
    db = _db_module.SessionLocal()
    try:
        RunRepository(db).create_run(run_id, "dev", "qa", {})
    finally:
        db.close()

    resp = client.get(f"/api/runs/{run_id}/mismatches/insights")
    assert resp.status_code == 200
    data = resp.json()
    assert data["top_columns"] == []
    assert data["accepted_count"] == 0
    assert data["open_count"] == 0
    assert data["tests"] == []
