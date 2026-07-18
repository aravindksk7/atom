from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.models import ScheduledRun
from etl_framework.repository.repository import SchedulerTelemetryRepository
from api.services.scheduler_telemetry import record_scheduler_event, record_scheduler_event_best_effort


def _engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def test_record_scheduler_event_uses_schedule_metadata():
    engine = _engine()
    db = Session(engine)
    sched = ScheduledRun(
        name="nightly",
        cron_expr="0 2 * * *",
        selection_id=7,
        selection_version=3,
        source_env="dev",
        target_env="prod",
    )
    db.add(sched)
    db.commit()
    db.refresh(sched)
    started_at = datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc)

    record_scheduler_event(db, sched, "started", "RUNNING", run_id="run-1", started_at=started_at)

    event = SchedulerTelemetryRepository(db).query_events()[0]
    assert event.schedule_id == sched.id
    assert event.schedule_name == "nightly"
    assert event.selection_id == 7
    assert event.selection_version == 3
    assert event.run_id == "run-1"
    assert event.event_state == "started"
    assert event.status == "RUNNING"


def test_best_effort_swallows_listener_database_errors():
    def broken_factory():
        raise RuntimeError("db unavailable")

    record_scheduler_event_best_effort(
        broken_factory,
        schedule_id=123,
        schedule_name="nightly",
        event_state="failed",
        status="FAILED",
        error_summary="boom",
    )


def test_best_effort_records_without_changing_caller_semantics():
    engine = _engine()
    db = Session(engine)
    sched = ScheduledRun(
        name="nightly",
        cron_expr="0 2 * * *",
        selection_id=7,
        selection_version=3,
        source_env="dev",
        target_env="prod",
    )
    db.add(sched)
    db.commit()
    db.refresh(sched)
    db.close()

    def factory():
        return Session(engine)

    record_scheduler_event_best_effort(
        factory,
        schedule_id=sched.id,
        schedule_name="fallback-name",
        event_state="completed",
        status="PASSED",
        run_id="run-1",
        exit_code=0,
    )

    verify = Session(engine)
    event = SchedulerTelemetryRepository(verify).query_events()[0]
    assert event.schedule_id == sched.id
    assert event.schedule_name == "nightly"
    assert event.job_name == "nightly"
    assert event.selection_id == 7
    assert event.selection_version == 3
    assert event.run_id == "run-1"
    assert event.status == "PASSED"
    assert event.exit_code == 0
