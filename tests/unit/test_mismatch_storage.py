from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from api.schemas import ReconFileCompareRequest
from api.services.compare_service import CompareService


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
