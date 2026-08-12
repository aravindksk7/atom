"""DAG-aware fields on the runs API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import SequenceStepRef


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import (
        RunRepository, RunStepRepository, TokenRepository,
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        RunRepository(db).create_run("run-dag", "dev", "prod", {})
        RunStepRepository(db).materialize_steps("run-dag", [
            SequenceStepRef(step_id="root", job_name="a"),
            SequenceStepRef(step_id="leaf", job_name="b", depends_on=["root"],
                            trigger_rule="all_done", hold_after=True),
        ])

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def test_steps_endpoint_exposes_dag_fields(client):
    steps = client.get("/api/runs/run-dag/steps").json()
    assert steps[0]["step_id"] == "root"
    assert steps[1]["depends_on"] == ["root"]
    assert steps[1]["trigger_rule"] == "all_done"
    assert steps[1]["attempt"] == 0
    assert steps[1]["on_failure"] == "skip_downstream"


def test_release_by_step_id(client):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunStepRepository
    db = _db_module.SessionLocal()
    try:
        RunStepRepository(db).set_status_by_step_id("run-dag", "leaf", "HELD")
    finally:
        db.close()

    resp = client.post("/api/runs/run-dag/steps/by-id/leaf/release", json={
        "action": "approve", "note": "looks fine", "released_by": "alice",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "APPROVED"


def test_release_by_unknown_step_id_is_404(client):
    resp = client.post("/api/runs/run-dag/steps/by-id/ghost/release", json={
        "action": "approve", "note": "n", "released_by": "alice",
    })
    assert resp.status_code == 404


def test_release_by_step_id_conflicts_when_not_held(client):
    resp = client.post("/api/runs/run-dag/steps/by-id/root/release", json={
        "action": "approve", "note": "n", "released_by": "alice",
    })
    assert resp.status_code == 409
