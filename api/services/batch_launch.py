from __future__ import annotations

import time
from datetime import date
from typing import Callable

from sqlalchemy.orm import Session

from api.services.business_calendar import step_dates
from etl_framework.repository.repository import RunBatchRepository, RunRepository


_TERMINAL_STATUSES = {"PASSED", "FAILED", "SLOW", "ERROR", "COMPLETED", "CANCELLED"}
_FAILURE_STATUSES = {"FAILED", "ERROR", "BLOCKED"}

LaunchCallable = Callable[[Session, int, dict[str, str]], str]
SessionFactory = Callable[[], Session]


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
    poll_seconds: float = 0.1,
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
            overrides = {batch.variable_name: current_date.isoformat()}
            run_id = launch(db, batch.target_id, overrides)
            RunRepository(db).set_run_batch_id(run_id, batch_id)

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
    while True:
        with session_factory() as db:
            run = RunRepository(db).get_run(run_id)
            if run is None:
                return "ERROR"
            if run.status in _TERMINAL_STATUSES:
                return run.status
        time.sleep(poll_seconds)
