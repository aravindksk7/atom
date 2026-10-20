"""POST /api/runs/{run_id}/steps/by-id/{step_id}/mark-passed."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import SequenceStepRef

URL = "/api/runs/seq/steps/by-id/ds_load/mark-passed"
BODY = {"note": "DS console shows the job succeeded", "released_by": "alice"}


@pytest.fixture
def ctx(monkeypatch):
    from api.main import app
    from api.routes import runs as runs_module
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import (
        RunRepository, RunStepRepository, TokenRepository,
    )

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(_db_module, "SessionLocal", factory)
    launched = []
    monkeypatch.setattr(runs_module, "_execute_run", lambda *a, **kw: launched.append(a))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        RunRepository(db).create_run("seq", "dev", "prod", {"run_settings": {}})
        RunRepository(db).update_run_status("seq", "BLOCKED")
        steps = RunStepRepository(db)
        steps.materialize_steps("seq", [
            SequenceStepRef(step_id="extract", job_name="extract"),
            SequenceStepRef(step_id="ds_load", job_name="ds_load", depends_on=["extract"]),
            SequenceStepRef(step_id="publish", job_name="publish", depends_on=["ds_load"]),
        ])
        steps.update_status("seq", 0, "PASSED")
        steps.update_status("seq", 1, "ERROR")
        steps.update_status("seq", 2, "BLOCKED")

    client = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
    return client, factory, launched


def test_mark_passed_restarts_run_and_launches_it(ctx):
    from etl_framework.repository.models import AuditEvent
    from etl_framework.repository.repository import RunStepRepository

    client, factory, launched = ctx
    resp = client.post(URL, json=BODY)

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["restarted_from_run_id"] == "seq"
    assert body["run_id"] != "seq"
    assert len(launched) == 1 and launched[0][0] == body["run_id"]
    with factory() as db:
        assert RunStepRepository(db).get_step_by_step_id("seq", "ds_load").status == "APPROVED"
        audit = db.query(AuditEvent).filter_by(action="run.step_marked_passed").one()
        assert audit.diff["step_id"] == "ds_load"
        assert audit.diff["previous_status"] == "ERROR"


def test_mark_passed_with_nothing_left_returns_original_run(ctx):
    from etl_framework.repository.repository import RunStepRepository

    client, factory, launched = ctx
    with factory() as db:
        RunStepRepository(db).set_status_by_step_id("seq", "publish", "PASSED")

    resp = client.post(URL, json=BODY)

    assert resp.status_code == 202, resp.text
    assert resp.json()["run_id"] == "seq"
    assert launched == []


def test_mark_passed_requires_note(ctx):
    client, _, _ = ctx
    resp = client.post(URL, json={"note": "   ", "released_by": "alice"})
    assert resp.status_code == 422


def test_mark_passed_conflicts_for_step_that_did_not_fail(ctx):
    client, _, _ = ctx
    resp = client.post("/api/runs/seq/steps/by-id/extract/mark-passed", json=BODY)
    assert resp.status_code == 409
    assert "not failed" in resp.json()["detail"]


def test_mark_passed_conflicts_while_run_in_progress(ctx):
    from etl_framework.repository.repository import RunRepository

    client, factory, _ = ctx
    with factory() as db:
        RunRepository(db).update_run_status("seq", "RUNNING")
    resp = client.post(URL, json=BODY)
    assert resp.status_code == 409


def test_mark_passed_unknown_step_is_404(ctx):
    client, _, _ = ctx
    resp = client.post("/api/runs/seq/steps/by-id/ghost/mark-passed", json=BODY)
    assert resp.status_code == 404
