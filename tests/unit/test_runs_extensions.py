"""Tests for Module 6: /progress and /results/{id}/mismatches endpoints."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from api.schemas import RunProgressOut, MismatchOut


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def mock_run_repo():
    """Override RunRepository with a mock via patch."""
    with patch("api.routes.runs.RunRepository") as MockRepo:
        inst = MagicMock()
        MockRepo.return_value = inst
        yield inst


# ---------------------------------------------------------------------------
# /progress
# ---------------------------------------------------------------------------

def test_progress_returns_run_progress_schema(client, mock_run_repo):
    run = MagicMock()
    run.run_id = "abc-123"
    run.status = "RUNNING"
    run.total_tests = 10
    mock_run_repo.get_run.return_value = run
    mock_run_repo.count_completed_results.return_value = 4
    mock_run_repo.get_current_job.return_value = "sales_reconciliation"

    resp = client.get("/api/runs/abc-123/progress")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "abc-123"
    assert data["status"] == "RUNNING"
    assert data["total_tests"] == 10
    assert data["completed_tests"] == 4
    assert data["current_job"] == "sales_reconciliation"
    assert data["percent_complete"] == 40


def test_progress_percent_zero_when_no_tests(client, mock_run_repo):
    run = MagicMock()
    run.run_id = "xyz"
    run.status = "PENDING"
    run.total_tests = 0
    mock_run_repo.get_run.return_value = run
    mock_run_repo.count_completed_results.return_value = 0
    mock_run_repo.get_current_job.return_value = None

    resp = client.get("/api/runs/xyz/progress")
    assert resp.status_code == 200
    assert resp.json()["percent_complete"] == 0


def test_progress_returns_404_for_unknown_run(client, mock_run_repo):
    mock_run_repo.get_run.return_value = None

    resp = client.get("/api/runs/no-such-run/progress")
    assert resp.status_code == 404


def test_progress_percent_100_when_all_done(client, mock_run_repo):
    run = MagicMock()
    run.run_id = "done-run"
    run.status = "COMPLETED"
    run.total_tests = 5
    mock_run_repo.get_run.return_value = run
    mock_run_repo.count_completed_results.return_value = 5
    mock_run_repo.get_current_job.return_value = None

    resp = client.get("/api/runs/done-run/progress")
    assert resp.json()["percent_complete"] == 100


# ---------------------------------------------------------------------------
# /results/{result_id}/mismatches
# ---------------------------------------------------------------------------

def _make_mismatch(id_: int):
    m = MagicMock()
    m.id = id_
    m.column_name = f"col_{id_}"
    m.key_values = {"pk": id_}
    m.source_value = "A"
    m.target_value = "B"
    m.mismatch_type = "value"
    m.accepted = False
    m.accepted_note = None
    m.accepted_at = None
    m.accepted_by = None
    return m


def test_mismatches_returns_list(client, mock_run_repo):
    run = MagicMock()
    run.run_id = "r1"
    mock_run_repo.get_run.return_value = run
    mock_run_repo.list_mismatches.return_value = [_make_mismatch(1), _make_mismatch(2)]

    resp = client.get("/api/runs/r1/results/99/mismatches")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["column_name"] == "col_1"
    assert data[1]["mismatch_type"] == "value"


def test_mismatches_404_when_run_not_found(client, mock_run_repo):
    mock_run_repo.get_run.return_value = None

    resp = client.get("/api/runs/ghost/results/1/mismatches")
    assert resp.status_code == 404


def test_mismatches_respects_pagination_params(client, mock_run_repo):
    run = MagicMock()
    mock_run_repo.get_run.return_value = run
    mock_run_repo.list_mismatches.return_value = []

    client.get("/api/runs/r1/results/42/mismatches?limit=25&offset=50")
    mock_run_repo.list_mismatches.assert_called_once_with(
        result_id=42, limit=25, offset=50
    )


def test_mismatches_default_pagination_is_100_0(client, mock_run_repo):
    run = MagicMock()
    mock_run_repo.get_run.return_value = run
    mock_run_repo.list_mismatches.return_value = []

    client.get("/api/runs/r1/results/7/mismatches")
    mock_run_repo.list_mismatches.assert_called_once_with(
        result_id=7, limit=100, offset=0
    )
