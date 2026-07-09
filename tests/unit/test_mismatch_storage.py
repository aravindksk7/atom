from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.schemas import ReconFileCompareRequest
from api.services.compare_service import CompareService
from etl_framework.repository.database import Base
from etl_framework.repository.repository import RunRepository


def _service_with_mock_repo():
    svc = CompareService.__new__(CompareService)
    svc._repo = MagicMock()
    svc._repo.update_run_status = MagicMock()
    svc._repo.add_test_result = MagicMock(return_value=SimpleNamespace(id=42))
    svc._repo.add_mismatch_details = MagicMock()
    return svc


def test_mismatch_details_stored_when_stats_differ(monkeypatch):
    svc = _service_with_mock_repo()

    stats = {
        "a": {"orders": {"status": "PASSED", "source_row_count": 100, "target_row_count": 100, "total_issues": 0}},
        "b": {"orders": {"status": "FAILED", "source_row_count": 100, "target_row_count": 90, "total_issues": 5}},
    }
    monkeypatch.setattr(svc, "_load_recon_source", lambda req, side: stats[side])

    with patch("api.services.compare_service.MetricsWriter") as mw:
        mw.return_value.write = MagicMock()
        req = ReconFileCompareRequest(stored_run_id="run-a", stored_run_id_b="run-b")
        svc.run_recon_file_compare(req, "run-x")

    svc._repo.add_mismatch_details.assert_called_once()
    result_id, records = svc._repo.add_mismatch_details.call_args[0]
    assert result_id == 42
    col_names = [r.column_name for r in records]
    assert "target_row_count" in col_names
    assert "total_issues" in col_names


def test_no_mismatch_details_when_stats_match(monkeypatch):
    svc = _service_with_mock_repo()

    same = {"orders": {"status": "PASSED", "source_row_count": 100, "target_row_count": 100, "total_issues": 0}}
    monkeypatch.setattr(svc, "_load_recon_source", lambda req, side: same)

    with patch("api.services.compare_service.MetricsWriter") as mw:
        mw.return_value.write = MagicMock()
        req = ReconFileCompareRequest(stored_run_id="run-a", stored_run_id_b="run-b")
        svc.run_recon_file_compare(req, "run-y")

    svc._repo.add_mismatch_details.assert_not_called()


# --- segment_summary persistence ---
# NOTE: this file otherwise tests CompareService against a mocked repo and has
# no real DB fixture. For persistence-plumbing coverage we mirror the `db`
# fixture + inline `RunRepository(db)` pattern used in test_repository.py.

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_add_test_result_persists_segment_summary(db):
    from datetime import datetime, timezone
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus

    repo = RunRepository(db)
    repo.create_run(run_id="run-segsum-1", source_env="dev", target_env="qa")

    summary = {"region": [{"value": "EMEA", "mismatch_count": 3,
                           "missing_in_target": 1, "missing_in_source": 0,
                           "value_diff": 2, "pct_of_total": 75.0}]}
    result = ReconciliationResult(
        query_name="q", source_env="dev", target_env="qa",
        source_row_count=4, target_row_count=4, matched_count=1,
        missing_in_target_count=1, missing_in_source_count=0,
        value_mismatch_count=2, mismatches=[], status=TestStatus.FAILED,
        executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
        segment_summary=summary,
    )
    tr = repo.add_test_result("run-segsum-1", result)
    assert tr.segment_summary == summary
