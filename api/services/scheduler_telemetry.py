from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from sqlalchemy.orm import Session

from etl_framework.repository.models import ScheduledRun
from etl_framework.repository.repository import SchedulerTelemetryRepository

logger = logging.getLogger("api.scheduler.telemetry")


def _truncate_error(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:1000]


def record_scheduler_event(
    db: Session,
    schedule: ScheduledRun | None,
    event_state: str,
    status: str,
    *,
    schedule_id: int | None = None,
    schedule_name: str | None = None,
    run_id: str | None = None,
    exit_code: int | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    duration_ms: int | None = None,
    error_summary: str | None = None,
    metadata_json: dict | None = None,
) -> None:
    resolved_name = schedule.name if schedule is not None else (schedule_name or "unknown")
    try:
        SchedulerTelemetryRepository(db).record_event(
            schedule_id=schedule.id if schedule is not None else schedule_id,
            schedule_name=resolved_name,
            job_name=schedule.name if schedule is not None else schedule_name,
            selection_id=schedule.selection_id if schedule is not None else None,
            selection_version=schedule.selection_version if schedule is not None else None,
            run_id=run_id,
            event_state=event_state,
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            error_summary=_truncate_error(error_summary),
            metadata_json=metadata_json,
        )
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("Failed to record scheduler telemetry for %s: %s", resolved_name, exc)


def record_scheduler_event_best_effort(
    session_factory: Callable[[], Session],
    *,
    schedule_id: int | None,
    schedule_name: str,
    event_state: str,
    status: str,
    run_id: str | None = None,
    exit_code: int | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    duration_ms: int | None = None,
    error_summary: str | None = None,
    metadata_json: dict | None = None,
) -> None:
    db = None
    try:
        db = session_factory()
        schedule = db.get(ScheduledRun, schedule_id) if schedule_id is not None else None
        record_scheduler_event(
            db,
            schedule,
            event_state,
            status,
            schedule_id=schedule_id,
            schedule_name=schedule_name,
            run_id=run_id,
            exit_code=exit_code,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            error_summary=error_summary,
            metadata_json=metadata_json,
        )
    except Exception as exc:
        logger.warning("Failed to record scheduler telemetry for %s: %s", schedule_name, exc)
    finally:
        if db is not None:
            db.close()
