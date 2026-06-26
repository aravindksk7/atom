import pytest
import threading
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models as _models  # registers all ORM models
from etl_framework.repository.models import RunStep, TestRun


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


# ---------------------------------------------------------------------------
# Task 1: RunStep model
# ---------------------------------------------------------------------------

def test_run_step_model_columns():
    db = _session()
    run = TestRun(run_id="run-s1", status="PENDING", source_env="dev", target_env="prod")
    db.add(run)
    db.commit()

    step = RunStep(
        run_id="run-s1",
        job_name="orders",
        step_index=0,
        status="PENDING",
        hold_after=True,
        condition={"require_status": ["PASSED"], "max_mismatch_count": 5},
        wait_seconds=10,
    )
    db.add(step)
    db.commit()
    db.refresh(step)

    assert step.id is not None
    assert step.status == "PENDING"
    assert step.hold_after is True
    assert step.condition["max_mismatch_count"] == 5
    assert step.wait_seconds == 10
    assert step.held_at is None
    assert step.release_action is None


# ---------------------------------------------------------------------------
# Task 2: RunStepRepository
# ---------------------------------------------------------------------------

from etl_framework.repository.repository import RunStepRepository, RunRepository
from api.schemas import SequenceStep, StepCondition


def _make_run(db: Session, run_id: str) -> None:
    RunRepository(db).create_run(run_id, "dev", "prod", {})


def test_materialize_steps_creates_rows():
    db = _session()
    _make_run(db, "run-m1")
    repo = RunStepRepository(db)
    steps = [
        SequenceStep(job_name="orders", hold_after=True,
                     condition=StepCondition(require_status=["PASSED"]),
                     wait_seconds=5),
        SequenceStep(job_name="customers"),
    ]
    rows = repo.materialize_steps("run-m1", steps)
    assert len(rows) == 2
    assert rows[0].job_name == "orders"
    assert rows[0].step_index == 0
    assert rows[0].hold_after is True
    cond = rows[0].condition
    assert cond["require_status"] == ["PASSED"]
    assert cond["max_mismatch_count"] is None
    assert cond["min_row_count"] is None
    assert cond["max_row_count"] is None
    assert cond["max_value_mismatches"] is None
    assert cond["max_missing_in_target"] is None
    assert cond["max_missing_in_source"] is None
    assert rows[0].wait_seconds == 5
    assert rows[1].step_index == 1
    assert rows[1].hold_after is False


def test_update_status_and_get_step():
    db = _session()
    _make_run(db, "run-m2")
    repo = RunStepRepository(db)
    repo.materialize_steps("run-m2", [SequenceStep(job_name="orders")])

    updated = repo.update_status("run-m2", 0, "RUNNING")
    assert updated.status == "RUNNING"

    step = repo.get_step("run-m2", 0)
    assert step.status == "RUNNING"


def test_release_step_approve():
    db = _session()
    _make_run(db, "run-m3")
    repo = RunStepRepository(db)
    repo.materialize_steps("run-m3", [SequenceStep(job_name="orders", hold_after=True)])

    now = datetime.now(timezone.utc)
    repo.update_status("run-m3", 0, "HELD", held_at=now)

    released = repo.release_step("run-m3", 0, "approve", "Looks good", "alice")
    assert released.status == "APPROVED"
    assert released.release_action == "approve"
    assert released.release_note == "Looks good"
    assert released.released_by == "alice"


def test_release_step_returns_none_when_not_held():
    db = _session()
    _make_run(db, "run-m4")
    repo = RunStepRepository(db)
    repo.materialize_steps("run-m4", [SequenceStep(job_name="orders")])
    result = repo.release_step("run-m4", 0, "approve", "note", "alice")
    assert result is None


def test_cancel_remaining_steps():
    db = _session()
    _make_run(db, "run-m5")
    repo = RunStepRepository(db)
    repo.materialize_steps("run-m5", [
        SequenceStep(job_name="a"),
        SequenceStep(job_name="b"),
        SequenceStep(job_name="c"),
    ])
    repo.update_status("run-m5", 0, "PASSED")
    repo.cancel_remaining("run-m5", from_index=1)

    steps = repo.list_steps("run-m5")
    assert steps[0].status == "PASSED"
    assert steps[1].status == "CANCELLED"
    assert steps[2].status == "CANCELLED"
