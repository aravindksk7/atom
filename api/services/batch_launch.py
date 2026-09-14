from __future__ import annotations

import logging
import time
from datetime import date
from typing import Callable

from sqlalchemy.orm import Session

from api.schemas import RunBatchOut
from api.services.business_calendar import step_dates
from etl_framework.repository.models import TERMINAL_STATUSES
from etl_framework.repository.repository import RunBatchRepository, RunRepository

logger = logging.getLogger("api.batch_launch")

_FAILURE_STATUSES = {"FAILED", "ERROR", "BLOCKED"}

LaunchCallable = Callable[[Session, int, dict[str, str]], str]
SessionFactory = Callable[[], Session]


def batch_out(db: Session, batch) -> RunBatchOut:
    """Shared `RunBatch` ORM row -> `RunBatchOut` serializer, used by every
    route that returns a batch (create, cancel, get) across run_batches.py,
    selections.py, and sequences.py -- one place to add a field instead of
    three copies that can silently drift out of sync."""
    runs = RunBatchRepository(db).member_runs(batch.batch_id)
    return RunBatchOut(
        **{k: getattr(batch, k) for k in (
            "batch_id", "target_type", "target_id", "status", "variable_name", "start_value",
            "iterations", "step_days", "weekend_policy", "stop_on_failure", "completed",
            "current_iteration", "current_value", "created_at", "completed_at",
        )},
        runs=[{"run_id": r.run_id, "status": r.status, "started_at": r.started_at, "completed_at": r.completed_at} for r in runs],
    )


def validate_batch_variable(db: Session, variable_name: str) -> None:
    from fastapi import HTTPException
    from etl_framework.repository.repository import CustomVariableRepository

    variable = CustomVariableRepository(db).get_by_name(variable_name)
    if variable is None:
        raise HTTPException(status_code=422, detail=f"Custom variable '{variable_name}' not found")
    if variable.var_type != "date":
        raise HTTPException(status_code=422, detail=f"Custom variable '{variable_name}' must have var_type='date'")


def run_batch(
    batch_id: str,
    session_factory: SessionFactory,
    launch: LaunchCallable,
    *,
    poll_seconds: float = 1.0,
) -> None:
    while True:
        with session_factory() as db:
            batch_repo = RunBatchRepository(db)
            batch = batch_repo.get(batch_id)
            if batch is None or batch.status in {"STOPPED", "COMPLETED", "FAILED"}:
                return
            dates = step_dates(
                date.fromisoformat(batch.start_value),
                batch.iterations,
                batch.step_days,
                batch.weekend_policy,
            )
            completed = batch.completed or 0
            if completed >= batch.iterations:
                batch_repo.complete(batch_id)
                return
            current_date = dates[completed]
            batch_repo.set_current(batch_id, completed + 1, current_date.isoformat())
            target_id = batch.target_id
            overrides = {batch.variable_name: current_date.isoformat()}
        # The session above is closed before calling launch(): `launch` may run
        # the whole reconciliation synchronously (it's called with
        # background_tasks=None), which can take as long as the run itself --
        # holding a DB session open for that whole duration would waste a pool
        # connection for nothing (launch opens whatever session it needs on
        # its own). A launch-time failure (env validation, precondition gate,
        # an unexpected error) is also caught here rather than left to kill
        # this whole background task silently mid-batch.
        try:
            with session_factory() as launch_db:
                run_id = launch(launch_db, target_id, overrides)
                RunRepository(launch_db).set_run_batch_id(run_id, batch_id)
        except Exception:
            logger.exception("Batch %s: iteration launch failed, stopping batch", batch_id)
            with session_factory() as db:
                RunBatchRepository(db).complete(batch_id, "FAILED")
            return

        terminal_status = _wait_for_terminal(session_factory, run_id, poll_seconds)
        with session_factory() as db:
            batch_repo = RunBatchRepository(db)
            batch = batch_repo.get(batch_id)
            if batch is None:
                return
            batch_repo.increment_completed(batch_id)
            failed_count = 0
            run = RunRepository(db).get_run(run_id)
            if run is not None:
                failed_count = (run.failed or 0) + (run.error or 0)
            if batch.stop_on_failure and (terminal_status in _FAILURE_STATUSES or failed_count > 0):
                batch_repo.complete(batch_id, "STOPPED")
                return


def _wait_for_terminal(session_factory: SessionFactory, run_id: str, poll_seconds: float) -> str:
    """Polls with one long-lived session (`expire_all()` before each read, so
    the other transaction's commits are actually seen) instead of opening and
    closing a fresh session/connection on every tick."""
    with session_factory() as db:
        run_repo = RunRepository(db)
        while True:
            db.expire_all()
            run = run_repo.get_run(run_id)
            if run is None:
                return "ERROR"
            if run.status in TERMINAL_STATUSES:
                return run.status
            time.sleep(poll_seconds)
