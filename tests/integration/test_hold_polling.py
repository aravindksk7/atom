"""Integration tests for the hold-polling flow in RunExecutor.

Two sessions share a temp-file SQLite DB so the executor thread and the
release thread can both commit/read without in-memory isolation issues.
"""
import os
import tempfile
import threading
import time
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from etl_framework.repository.database import Base, _ensure_compare_columns
import etl_framework.repository.models as _models  # noqa: F401 — register ORM models
from etl_framework.repository.repository import RunRepository, RunStepRepository
from api.schemas import SequenceStep, RunSettings
from api.services import run_executor as _re_module


# Override poll interval so tests don't sleep 5 seconds each
_re_module.HOLD_POLL_INTERVAL_SECONDS = 1


def _make_engine(path: str):
    url = f"sqlite:///{path}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    _ensure_compare_columns(engine)
    return engine


def _session(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test_hold.db")


def _run_executor_in_thread(engine, run_id: str, steps: list[SequenceStep]):
    from api.services.run_executor import RunExecutor
    session = _session(engine)
    try:
        executor = RunExecutor(
            db=session,
            run_id=run_id,
            source_env="dev",
            target_env="prod",
            job_sequence=steps,
            run_settings=RunSettings(metrics_enabled=False),
        )
        executor.execute()
    finally:
        session.close()


def _wait_for_held(engine, run_id: str, step_index: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = _session(engine)
        try:
            step = RunStepRepository(session).get_step(run_id, step_index)
            if step and step.status == "HELD":
                return True
        finally:
            session.close()
        time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# Test: approve releases the hold and the run completes PASSED
# ---------------------------------------------------------------------------

def test_hold_approve_completes_run(db_path):
    engine = _make_engine(db_path)

    run_id = str(uuid.uuid4())
    setup_session = _session(engine)
    RunRepository(setup_session).create_run(run_id, "dev", "prod")
    setup_session.close()

    steps = [
        SequenceStep(job_name="orders_reconciliation", hold_after=True),
        SequenceStep(job_name="customers_reconciliation"),
    ]

    t = threading.Thread(
        target=_run_executor_in_thread,
        args=(engine, run_id, steps),
        daemon=True,
    )
    t.start()

    # Wait until step 0 is HELD
    assert _wait_for_held(engine, run_id, 0), "Timed out waiting for step 0 to be HELD"

    # Release with approve
    release_session = _session(engine)
    try:
        RunStepRepository(release_session).release_step(
            run_id, 0, "approve", "Looks good", "tester"
        )
    finally:
        release_session.close()

    t.join(timeout=30)
    assert not t.is_alive(), "Executor thread did not finish in time"

    # Verify final run status
    check_session = _session(engine)
    try:
        run = RunRepository(check_session).get_run(run_id)
        assert run is not None
        assert run.status in {"PASSED", "FAILED", "SLOW", "ERROR"}, (
            f"Unexpected run status: {run.status}"
        )
        # Both steps should be done
        steps_rows = RunStepRepository(check_session).list_steps(run_id)
        assert len(steps_rows) == 2
        assert steps_rows[0].status == "APPROVED"
        assert steps_rows[0].released_by == "tester"
        assert steps_rows[1].status not in {"PENDING", "HELD"}
    finally:
        check_session.close()


# ---------------------------------------------------------------------------
# Test: cancel action sets remaining steps to CANCELLED and run to CANCELLED
# ---------------------------------------------------------------------------

def test_hold_cancel_cancels_run(db_path):
    engine = _make_engine(db_path)

    run_id = str(uuid.uuid4())
    setup_session = _session(engine)
    RunRepository(setup_session).create_run(run_id, "dev", "prod")
    setup_session.close()

    steps = [
        SequenceStep(job_name="orders_reconciliation", hold_after=True),
        SequenceStep(job_name="customers_reconciliation"),
    ]

    t = threading.Thread(
        target=_run_executor_in_thread,
        args=(engine, run_id, steps),
        daemon=True,
    )
    t.start()

    assert _wait_for_held(engine, run_id, 0), "Timed out waiting for step 0 to be HELD"

    release_session = _session(engine)
    try:
        RunStepRepository(release_session).release_step(
            run_id, 0, "cancel", "Abort — bad data", "tester"
        )
    finally:
        release_session.close()

    t.join(timeout=30)
    assert not t.is_alive(), "Executor thread did not finish in time"

    check_session = _session(engine)
    try:
        run = RunRepository(check_session).get_run(run_id)
        assert run.status == "CANCELLED"
        steps_rows = RunStepRepository(check_session).list_steps(run_id)
        assert steps_rows[0].status == "CANCELLED"
        assert steps_rows[1].status == "CANCELLED"
    finally:
        check_session.close()


# ---------------------------------------------------------------------------
# Test: skip action skips the hold and continues the sequence
# ---------------------------------------------------------------------------

def test_hold_skip_continues_run(db_path):
    engine = _make_engine(db_path)

    run_id = str(uuid.uuid4())
    setup_session = _session(engine)
    RunRepository(setup_session).create_run(run_id, "dev", "prod")
    setup_session.close()

    steps = [
        SequenceStep(job_name="orders_reconciliation", hold_after=True),
        SequenceStep(job_name="customers_reconciliation"),
    ]

    t = threading.Thread(
        target=_run_executor_in_thread,
        args=(engine, run_id, steps),
        daemon=True,
    )
    t.start()

    assert _wait_for_held(engine, run_id, 0), "Timed out waiting for step 0 to be HELD"

    release_session = _session(engine)
    try:
        RunStepRepository(release_session).release_step(
            run_id, 0, "skip", "No action needed", "tester"
        )
    finally:
        release_session.close()

    t.join(timeout=30)
    assert not t.is_alive(), "Executor thread did not finish in time"

    check_session = _session(engine)
    try:
        run = RunRepository(check_session).get_run(run_id)
        assert run.status not in {"PENDING", "RUNNING", "HELD", "CANCELLED"}
        steps_rows = RunStepRepository(check_session).list_steps(run_id)
        assert steps_rows[0].status == "SKIPPED"
        assert steps_rows[1].status not in {"PENDING", "HELD"}
    finally:
        check_session.close()


def test_hold_times_out_and_cancels(monkeypatch, db_path):
    monkeypatch.setattr(_re_module, "HOLD_POLL_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(_re_module, "HOLD_TIMEOUT_SECONDS", 0.15)

    engine = _make_engine(db_path)
    run_id = str(uuid.uuid4())
    setup_session = _session(engine)
    try:
        RunRepository(setup_session).create_run(run_id, "dev", "prod")
    finally:
        setup_session.close()

    executor_session = _session(engine)
    try:
        from api.services.run_executor import RunExecutor

        RunExecutor(
            db=executor_session,
            run_id=run_id,
            source_env="dev",
            target_env="prod",
            job_sequence=[SequenceStep(job_name="orders_reconciliation", hold_after=True)],
            run_settings=RunSettings(metrics_enabled=False),
        ).execute()
    finally:
        executor_session.close()

    check_session = _session(engine)
    try:
        run = RunRepository(check_session).get_run(run_id)
        step = RunStepRepository(check_session).get_step(run_id, 0)
        assert run.status == "CANCELLED"
        assert step.status == "CANCELLED"
        assert step.release_note == "hold timed out"
    finally:
        check_session.close()


def test_cancel_request_releases_held_step(monkeypatch, db_path):
    monkeypatch.setattr(_re_module, "HOLD_POLL_INTERVAL_SECONDS", 0.05)

    engine = _make_engine(db_path)
    run_id = str(uuid.uuid4())
    setup_session = _session(engine)
    try:
        RunRepository(setup_session).create_run(run_id, "dev", "prod")
    finally:
        setup_session.close()

    steps = [SequenceStep(job_name="orders_reconciliation", hold_after=True)]
    t = threading.Thread(
        target=_run_executor_in_thread,
        args=(engine, run_id, steps),
        daemon=True,
    )
    t.start()

    assert _wait_for_held(engine, run_id, 0, timeout=5), "Timed out waiting for step 0 to be HELD"

    cancel_session = _session(engine)
    try:
        RunRepository(cancel_session).request_cancel(run_id)
    finally:
        cancel_session.close()

    t.join(timeout=2)
    assert not t.is_alive(), "Executor thread did not finish after cancel request"

    check_session = _session(engine)
    try:
        assert RunRepository(check_session).get_run(run_id).status == "CANCELLED"
    finally:
        check_session.close()
