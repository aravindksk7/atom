"""API tests for run timing, sequence history and the stream."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import SequenceStepRef

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(engine, monkeypatch):
    from api.main import app
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import TokenRepository

    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _blocked_run(engine, run_id="blocked-run"):
    from etl_framework.repository.repository import RunRepository, RunStepRepository

    with Session(engine) as db:
        RunRepository(db).create_run(run_id, "dev", "prod", {})
        RunStepRepository(db).materialize_steps(run_id, [
            SequenceStepRef(step_id="a", job_name="job_a"),
        ])
        RunStepRepository(db).set_status_by_step_id(run_id, "a", "BLOCKED")
        RunRepository(db).update_run_status(
            run_id, "BLOCKED", started_at=T0, completed_at=T0 + timedelta(seconds=30),
        )


def test_blocked_run_ends_the_stream(client, engine):
    _blocked_run(engine)
    with client.stream("GET", "/api/runs/blocked-run/stream") as response:
        body = response.read().decode()
    assert "event: done" in body
