"""Run status payloads drive the Monitor and History lists, so they must say which
environments a run compares and, while it is live, how many steps it has in all."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.reconciliation.models import ReconciliationResult
from etl_framework.runner.state import TestStatus


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.database import Base
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


def _result(name: str) -> ReconciliationResult:
    return ReconciliationResult(
        query_name=name, source_env="qa", target_env="prod",
        source_row_count=1, target_row_count=1, matched_count=1,
        missing_in_target_count=0, missing_in_source_count=0,
        value_mismatch_count=0, mismatches=[],
        status=TestStatus.PASSED, executed_at=datetime.now(timezone.utc),
        duration_seconds=0.1,
    )


def _seed_run(status: str, total_tests: int, finished_steps: int) -> str:
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository

    run_id = str(uuid.uuid4())
    db = _db_module.SessionLocal()
    try:
        repo = RunRepository(db)
        repo.create_run(run_id, "qa", "prod", {})
        repo.update_run_status(run_id, status, total_tests=total_tests)
        for index in range(finished_steps):
            repo.add_test_result(run_id, _result(f"job_{index}"))
    finally:
        db.close()
    return run_id


def test_status_names_the_environments(client):
    run_id = _seed_run("RUNNING", total_tests=5, finished_steps=1)

    data = client.get(f"/api/runs/{run_id}/status").json()

    assert data["source_env"] == "qa"
    assert data["target_env"] == "prod"


def test_run_list_names_the_environments(client):
    run_id = _seed_run("RUNNING", total_tests=5, finished_steps=1)

    listed = {run["run_id"]: run for run in client.get("/api/runs").json()}

    assert listed[run_id]["source_env"] == "qa"
    assert listed[run_id]["target_env"] == "prod"


def test_live_run_reports_its_full_size_not_the_steps_finished_so_far(client):
    run_id = _seed_run("RUNNING", total_tests=5, finished_steps=2)

    data = client.get(f"/api/runs/{run_id}/status").json()

    assert data["total_tests"] == 5
    assert data["passed"] == 2


def test_finished_run_still_counts_its_results(client):
    """An override or a restart can change results after the run, so a finished
    run keeps deriving its total from them."""
    run_id = _seed_run("PASSED", total_tests=5, finished_steps=2)

    data = client.get(f"/api/runs/{run_id}/status").json()

    assert data["total_tests"] == 2
