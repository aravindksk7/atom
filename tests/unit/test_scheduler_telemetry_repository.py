from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import SchedulerTelemetryQuery, SchedulerTelemetryRepository


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_records_and_filters_scheduler_telemetry_events():
    db = _session()
    repo = SchedulerTelemetryRepository(db)
    now = datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc)

    repo.record_event(
        schedule_id=1,
        schedule_name="nightly",
        event_state="started",
        status="RUNNING",
        started_at=now,
    )
    failed = repo.record_event(
        schedule_id=1,
        schedule_name="nightly",
        event_state="failed",
        status="FAILED",
        exit_code=1,
        started_at=now,
        finished_at=now + timedelta(minutes=4),
        duration_ms=240000,
        run_id="run-1",
        error_summary="source timeout",
    )
    repo.record_event(
        schedule_id=2,
        schedule_name="hourly",
        event_state="completed",
        status="PASSED",
        exit_code=0,
        started_at=now + timedelta(hours=1),
    )

    rows = repo.query_events(SchedulerTelemetryQuery(job="night", status="failed", exit_code=1))

    assert [row.id for row in rows] == [failed.id]
    assert rows[0].schedule_id == 1
    assert rows[0].schedule_name == "nightly"
    assert rows[0].event_state == "failed"
    assert rows[0].status == "FAILED"
    assert rows[0].exit_code == 1
    assert rows[0].duration_ms == 240000
    assert rows[0].run_id == "run-1"
    assert rows[0].error_summary == "source timeout"


def test_latest_by_schedule_returns_newest_event_per_schedule():
    db = _session()
    repo = SchedulerTelemetryRepository(db)
    now = datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc)

    repo.record_event(schedule_id=1, schedule_name="nightly", event_state="started", status="RUNNING", started_at=now)
    latest = repo.record_event(schedule_id=1, schedule_name="nightly", event_state="completed", status="PASSED", started_at=now + timedelta(minutes=5))
    other = repo.record_event(schedule_id=2, schedule_name="hourly", event_state="failed", status="FAILED", started_at=now + timedelta(minutes=2))

    by_schedule = repo.latest_by_schedule()

    assert by_schedule[1].id == latest.id
    assert by_schedule[2].id == other.id


def test_prune_older_than_removes_only_old_telemetry():
    db = _session()
    repo = SchedulerTelemetryRepository(db)
    now = datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc)

    repo.record_event(schedule_id=1, schedule_name="old", event_state="completed", status="PASSED", created_at=now - timedelta(days=31))
    repo.record_event(schedule_id=2, schedule_name="new", event_state="completed", status="PASSED", created_at=now - timedelta(days=2))

    deleted = repo.prune_older_than(now - timedelta(days=30))
    remaining = repo.query_events()

    assert deleted == 1
    assert [row.schedule_name for row in remaining] == ["new"]
