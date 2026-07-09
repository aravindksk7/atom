from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from api.routes import runs as runs_module
from api.services.frame_engine import FrameEngine
from etl_framework.reconciliation.engine import ReconciliationEngine
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base, get_db
from etl_framework.repository.repository import RunRepository, TokenRepository
from etl_framework.runner.state import TestStatus


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(_db_module, "SessionLocal", session_factory)

    def override_get_db():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(runs_module, "_execute_run", lambda *a, **kw: None)
    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_mismatch_summary_counts_exceed_stored_row_limit():
    source = pd.DataFrame({"id": [1, 2, 3, 4, 5, 6], "amount": [1, 2, 3, 4, 5, 6]})
    target = pd.DataFrame({"id": [1, 2, 3, 4, 5, 6], "amount": [11, 12, 13, 14, 15, 6]})
    engine = ReconciliationEngine(
        FrameEngine(source, "source"),
        FrameEngine(target, "target"),
        key_columns=["id"],
        mismatch_row_limit=2,
    )

    result = engine.reconcile("__default__", "amount_check")

    assert len(result.mismatches) == 2
    assert result.value_mismatch_count == 5
    assert result.mismatch_summary["by_column"]["amount"] == 5
    assert result.mismatch_summary["compared_rows_by_column"]["amount"] == 6


def _create_run_with_result(total_issues: int, stored_rows: int) -> str:
    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        run = repo.create_run(
            run_id=f"run-export-{total_issues}-{stored_rows}",
            source_env="dev",
            target_env="prod",
            config_snapshot={"compare_request_type": "unknown", "request": {}},
        )
        result = ReconciliationResult(
            query_name="orders",
            source_env="dev",
            target_env="prod",
            source_row_count=10,
            target_row_count=10,
            matched_count=10,
            missing_in_target_count=0,
            missing_in_source_count=0,
            value_mismatch_count=total_issues,
            mismatches=[],
            status=TestStatus.FAILED if total_issues else TestStatus.PASSED,
            executed_at=datetime.now(timezone.utc),
            duration_seconds=0.1,
        )
        tr = repo.add_test_result(run.run_id, result)
        repo.add_mismatch_details(tr.id, [
            MismatchRecord(
                key_values={"id": idx + 1},
                column_name="amount",
                source_value=idx,
                target_value=idx + 10,
                mismatch_type="value_diff",
                delta=10.0,
                relative_delta=None,
            )
            for idx in range(stored_rows)
        ])
        return run.run_id


def test_full_difference_download_streams_stored_rows_when_complete(client):
    run_id = _create_run_with_result(total_issues=1, stored_rows=1)

    resp = client.get(f"/api/runs/{run_id}/differences/download?format=csv")

    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert rows[0]["test_name"] == "orders"
    assert rows[0]["column_name"] == "amount"
    assert rows[0]["delta"] == "10.0"


def test_full_difference_download_requires_job_when_stored_rows_are_truncated(client):
    run_id = _create_run_with_result(total_issues=2, stored_rows=1)

    resp = client.get(f"/api/runs/{run_id}/differences/download?format=csv")

    assert resp.status_code == 202
    body = resp.json()
    assert body["requires_export_job"] is True
    assert body["stored_rows"] == 1
    assert body["total_issues"] == 2
