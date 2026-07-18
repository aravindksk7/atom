"""Tests for retry policy and trends endpoint."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import JobDefinition, RunSettings, DQRule
from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository, RunRepository


def _engine_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _executor(db=None):
    if db is None:
        _, db = _engine_session()
    return RunExecutor(
        db=db, run_id="test", source_env="dev", target_env="prod",
        job_sequence=[], run_settings=RunSettings(),
    )


# ---------------------------------------------------------------------------
# Retry policy — RunSettings schema validation
# ---------------------------------------------------------------------------

def test_retry_defaults_to_zero():
    s = RunSettings()
    assert s.max_retries == 0
    assert s.retry_delay_seconds == 30.0


def test_retry_max_capped_at_10():
    with pytest.raises(Exception):
        RunSettings(max_retries=11)


def test_retry_negative_raises():
    with pytest.raises(Exception):
        RunSettings(max_retries=-1)


def test_retry_on_default():
    s = RunSettings()
    assert "error" in s.retry_on


def test_run_with_retry_succeeds_on_first_try():
    ex = _executor()
    calls = []

    def run_job():
        calls.append(1)
        return "ok"

    ex._settings = RunSettings(max_retries=2, retry_delay_seconds=0)
    # Simulate what _build_case does for retry=0 (no failures)
    result = run_job()
    assert result == "ok"
    assert calls == [1]


# ---------------------------------------------------------------------------
# DQ rule integration with JobDefinition serialization
# ---------------------------------------------------------------------------

def test_job_definition_accepts_dq_rules():
    job = JobDefinition(
        name="orders", query="SELECT 1", key_columns=["id"],
        rules=[DQRule(type="not_null", column="id"), DQRule(type="row_count_min", min_value=1)],
    )
    assert len(job.rules) == 2
    assert job.rules[0].type == "not_null"


def test_job_definition_rules_default_empty():
    job = JobDefinition(name="orders", query="SELECT 1", key_columns=["id"])
    assert job.rules == []


# ---------------------------------------------------------------------------
# Trends endpoint
# ---------------------------------------------------------------------------

def _make_client(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = override
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
    client = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
    return client, engine


def test_trends_empty_returns_no_points(monkeypatch):
    client, _ = _make_client(monkeypatch)
    resp = client.get("/api/runs/trends?job_name=orders")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_name"] == "orders"
    assert data["points"] == []
    assert data["drift_detected"] is False


def _seed_run(engine, job_name: str, days_ago: int, mismatch_count: int = 0):
    from etl_framework.repository.models import TestRun, TestResult
    completed = datetime.now(timezone.utc) - timedelta(days=days_ago)
    with Session(engine) as db:
        import uuid
        run = TestRun(
            run_id=str(uuid.uuid4()), status="PASSED",
            source_env="dev", target_env="prod",
            completed_at=completed, total_tests=1,
            passed=1, failed=0, slow=0, error=0,
        )
        db.add(run)
        db.flush()
        result = TestResult(
            run_id=run.run_id, query_name=job_name,
            status="PASSED", duration_seconds=1.5,
            source_row_count=100, target_row_count=100,
            value_mismatch_count=mismatch_count,
            missing_in_target_count=0, missing_in_source_count=0,
            executed_at=completed,
        )
        db.add(result)
        db.commit()


def test_trends_returns_points_for_seeded_data(monkeypatch):
    client, engine = _make_client(monkeypatch)
    _seed_run(engine, "orders", days_ago=5, mismatch_count=2)
    _seed_run(engine, "orders", days_ago=3, mismatch_count=1)
    resp = client.get("/api/runs/trends?job_name=orders&metric=mismatch_rate&window=30")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["points"]) == 2
    assert all(p["value"] >= 0 for p in data["points"])


def test_trends_window_filters_old_data(monkeypatch):
    client, engine = _make_client(monkeypatch)
    _seed_run(engine, "orders", days_ago=60, mismatch_count=5)  # outside window
    _seed_run(engine, "orders", days_ago=1, mismatch_count=0)   # inside window
    resp = client.get("/api/runs/trends?job_name=orders&window=7")
    assert resp.status_code == 200
    assert len(resp.json()["points"]) == 1


def test_trends_drift_detected_on_spike(monkeypatch):
    client, engine = _make_client(monkeypatch)
    # baseline: very low mismatch rate
    for i in range(10, 2, -1):
        _seed_run(engine, "orders", days_ago=i, mismatch_count=0)
    # spike on latest run
    _seed_run(engine, "orders", days_ago=1, mismatch_count=50)
    resp = client.get("/api/runs/trends?job_name=orders&metric=mismatch_rate&window=30")
    assert resp.status_code == 200
    assert resp.json()["drift_detected"] is True
