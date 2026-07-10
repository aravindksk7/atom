"""Tests for Module 6: /progress and /results/{id}/mismatches endpoints."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import RunProgressOut, MismatchOut


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
    mock_run_repo.count_mismatches.return_value = 0

    client.get("/api/runs/r1/results/42/mismatches?limit=25&offset=50")
    mock_run_repo.list_mismatches.assert_called_once_with(
        result_id=42, limit=25, offset=50,
        search=None, column=None, mismatch_type=None, accepted=None, sort="id",
    )


def test_mismatches_default_pagination_is_100_0(client, mock_run_repo):
    run = MagicMock()
    mock_run_repo.get_run.return_value = run
    mock_run_repo.list_mismatches.return_value = []
    mock_run_repo.count_mismatches.return_value = 0

    client.get("/api/runs/r1/results/7/mismatches")
    mock_run_repo.list_mismatches.assert_called_once_with(
        result_id=7, limit=100, offset=0,
        search=None, column=None, mismatch_type=None, accepted=None, sort="id",
    )


def test_mismatches_forwards_filters(client, mock_run_repo):
    run = MagicMock()
    mock_run_repo.get_run.return_value = run
    mock_run_repo.list_mismatches.return_value = []
    mock_run_repo.count_mismatches.return_value = 0

    client.get(
        "/api/runs/r1/results/42/mismatches"
        "?search=foo&column=amount&mismatch_type=value_diff&accepted=true&sort=column"
    )
    mock_run_repo.list_mismatches.assert_called_once_with(
        result_id=42, limit=100, offset=0,
        search="foo", column="amount", mismatch_type="value_diff", accepted=True, sort="column",
    )


def test_mismatches_sets_total_count_header(client, mock_run_repo):
    run = MagicMock()
    mock_run_repo.get_run.return_value = run
    mock_run_repo.list_mismatches.return_value = [_make_mismatch(1)]
    mock_run_repo.count_mismatches.return_value = 42

    resp = client.get("/api/runs/r1/results/42/mismatches")
    assert resp.headers["x-total-count"] == "42"


def test_mismatches_rejects_bad_mismatch_type(client, mock_run_repo):
    mock_run_repo.get_run.return_value = MagicMock()
    resp = client.get("/api/runs/r1/results/1/mismatches?mismatch_type=bogus")
    assert resp.status_code == 422


def test_mismatches_rejects_bad_accepted(client, mock_run_repo):
    mock_run_repo.get_run.return_value = MagicMock()
    resp = client.get("/api/runs/r1/results/1/mismatches?accepted=maybe")
    assert resp.status_code == 422


def test_mismatches_rejects_bad_sort(client, mock_run_repo):
    mock_run_repo.get_run.return_value = MagicMock()
    resp = client.get("/api/runs/r1/results/1/mismatches?sort=bogus")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /{run_id} detail — sample_rows pass-through
# ---------------------------------------------------------------------------

def _make_test_result(sample_rows):
    r = MagicMock()
    r.id = 1
    r.query_name = "orders_report"
    r.status = "PASSED"
    r.effective_status = "PASSED"
    r.duration_seconds = 0.5
    r.source_row_count = 2
    r.target_row_count = 2
    r.value_mismatch_count = 0
    r.missing_in_target_count = 0
    r.missing_in_source_count = 0
    r.error_message = None
    r.executed_at = None
    r.override_reason = None
    r.override_by = None
    r.override_at = None
    r.sample_rows = sample_rows
    r.segment_summary = None
    r.mismatch_summary = None
    return r


def test_run_detail_includes_sample_rows_read_from_source(client, mock_run_repo):
    run = MagicMock()
    run.run_id = "r-bo"
    run.status = "PASSED"
    run.started_at = None
    run.completed_at = None
    run.total_tests = 1
    run.passed = 1
    run.failed = 0
    run.slow = 0
    run.error = 0
    run.run_type = "reconciliation"
    run.pair_id = None
    run.source_env = "dev"
    run.target_env = "prod"
    run.config_snapshot = {}
    run.results = [_make_test_result([
        {"id": 1, "sku": "A100", "amount": 25.5},
        {"id": 2, "sku": "B200", "amount": 50.0},
    ])]
    mock_run_repo.get_run.return_value = run

    resp = client.get("/api/runs/r-bo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"][0]["sample_rows"] == [
        {"id": 1, "sku": "A100", "amount": 25.5},
        {"id": 2, "sku": "B200", "amount": 50.0},
    ]


# ---------------------------------------------------------------------------
# /stream
# ---------------------------------------------------------------------------

def test_stream_returns_404_for_unknown_run(client, mock_run_repo):
    mock_run_repo.get_run.return_value = None

    resp = client.get("/api/runs/ghost/stream")
    assert resp.status_code == 404


def test_stream_emits_terminal_progress_and_done(client, mock_run_repo):
    run = MagicMock()
    run.run_id = "done-run"
    run.status = "PASSED"
    run.total_tests = 2
    mock_run_repo.get_run.return_value = run
    mock_run_repo.count_completed_results.return_value = 2
    mock_run_repo.get_current_job.return_value = None

    with client.stream("GET", "/api/runs/done-run/stream") as resp:
        body = "".join(resp.iter_text())

    assert resp.status_code == 200
    assert "event: progress" in body
    assert "event: done" in body
    assert '"percent_complete": 100' in body


# ---------------------------------------------------------------------------
# /results/{result_id}/drilldown — on-demand live re-query
#
# Note: the `mock_run_repo` fixture above is autouse and replaces
# `api.routes.runs.RunRepository` with a MagicMock, so `client.get(f"/api/runs/{run_id}")`
# (which goes through that repo) cannot be used here to discover result ids. The
# drilldown endpoint itself talks to the DB directly (TestResult/TestRun/SavedJob),
# bypassing RunRepository entirely, so it works fine against a real run. To get a
# real completed run+result into the DB we build RunExecutor directly (same pattern
# as tests/unit/test_run_executor.py) against the client's own SessionLocal, then
# read the result id straight from the DB instead of via the mocked HTTP endpoint.
# ---------------------------------------------------------------------------

def _run_job_and_get_result(job_data: dict) -> tuple[str, int]:
    """Create a job and synchronously execute a run against it, returning (run_id, result_id)."""
    import uuid
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import JobRepository, RunRepository
    from etl_framework.repository.models import TestResult
    from api.services.run_executor import RunExecutor
    from api.schemas import RunSettings

    db = _db_module.SessionLocal()
    try:
        run_id = str(uuid.uuid4())
        RunRepository(db).create_run(run_id, "dev", "qa", {})
        JobRepository(db).create(job_data)
        RunExecutor(
            db=db,
            run_id=run_id,
            source_env="dev",
            target_env="qa",
            job_sequence=[job_data["name"]],
            run_settings=RunSettings(metrics_enabled=False),
        ).execute()
        result = db.query(TestResult).filter(TestResult.run_id == run_id).first()
        assert result is not None
        return run_id, result.id
    finally:
        db.close()


def test_drilldown_returns_side_by_side_counts(client):
    run_id, result_id = _run_job_and_get_result({
        "name": "drill_job",
        "description": "",
        "tags": [],
        "job_type": "reconciliation",
        "query": "SELECT * FROM t",
        "key_columns": ["id"],
        "exclude_columns": [],
        "source_env": None,
        "target_env": None,
        "params": {
            "segment_columns": ["region"],
            "source_rows": [{"id": 1, "region": "EMEA", "amt": 10},
                             {"id": 2, "region": "APAC", "amt": 20}],
            "target_rows": [{"id": 1, "region": "EMEA", "amt": 10},
                             {"id": 2, "region": "APAC", "amt": 99},
                             {"id": 3, "region": "APAC", "amt": 5}],
        },
        "enabled": True,
    })

    resp = client.post(
        f"/api/runs/{run_id}/results/{result_id}/drilldown",
        json={"segment_column": "region"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["segment_column"] == "region"
    assert data["job_name"] == "drill_job"
    by_value = {row["value"]: row for row in data["rows"]}
    assert by_value["APAC"]["source_count"] == 1
    assert by_value["APAC"]["target_count"] == 2
    assert by_value["APAC"]["delta"] == 1
    assert by_value["EMEA"]["source_count"] == 1
    assert by_value["EMEA"]["target_count"] == 1
    assert by_value["EMEA"]["delta"] == 0
    # sorted by absolute delta descending
    assert data["rows"][0]["value"] == "APAC"


def test_drilldown_rejects_non_reconciliation_job(client):
    run_id, result_id = _run_job_and_get_result({
        "name": "fresh_job",
        "description": "",
        "tags": [],
        "job_type": "freshness",
        "query": "SELECT * FROM t",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None,
        "target_env": None,
        "params": {"timestamp_column": "ts"},
        "enabled": True,
    })

    resp = client.post(
        f"/api/runs/{run_id}/results/{result_id}/drilldown",
        json={"segment_column": "region"},
    )
    assert resp.status_code == 400


def test_drilldown_404_on_unknown_result(client):
    resp = client.post(
        "/api/runs/nope/results/99999/drilldown",
        json={"segment_column": "region"},
    )
    assert resp.status_code == 404
