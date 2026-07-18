from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.models import ScheduledRun
from etl_framework.repository.repository import SchedulerTelemetryRepository
from api.services.scheduler_reporting import SchedulerReportFilters, SchedulerReportingService


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _schedule(db: Session, name: str, enabled: bool = True) -> ScheduledRun:
    sched = ScheduledRun(
        name=name,
        cron_expr="0 2 * * *",
        selection_id=1,
        selection_version=1,
        source_env="dev",
        target_env="prod",
        enabled=enabled,
    )
    db.add(sched)
    db.commit()
    db.refresh(sched)
    return sched


def test_summary_grid_timeline_and_metrics_share_filtered_telemetry():
    db = _session()
    nightly = _schedule(db, "nightly")
    hourly = _schedule(db, "hourly")
    now = datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc)
    telemetry = SchedulerTelemetryRepository(db)
    telemetry.record_event(schedule_id=nightly.id, schedule_name="nightly", event_state="completed", status="PASSED", exit_code=0, started_at=now - timedelta(hours=3), finished_at=now - timedelta(hours=3) + timedelta(minutes=2), duration_ms=120000, created_at=now - timedelta(hours=3))
    telemetry.record_event(schedule_id=nightly.id, schedule_name="nightly", event_state="failed", status="FAILED", exit_code=1, started_at=now - timedelta(hours=1), finished_at=now - timedelta(hours=1) + timedelta(minutes=5), duration_ms=300000, error_summary="boom", created_at=now - timedelta(hours=1))
    telemetry.record_event(schedule_id=hourly.id, schedule_name="hourly", event_state="completed", status="PASSED", exit_code=0, started_at=now - timedelta(days=5), duration_ms=60000, created_at=now - timedelta(days=5))

    service = SchedulerReportingService(db, runtime_snapshot={"available": True, "running": True, "job_count": 2, "timezone": "UTC", "jobs": {nightly.id: {"next_run_at": "2026-07-19T02:00:00+00:00"}}})
    filters = SchedulerReportFilters(from_dt=now - timedelta(days=1), to_dt=now, job="night")

    summary = service.summary(filters)
    grid = service.grid(filters)
    timeline = service.timeline(filters)
    metrics = service.metrics(filters)

    assert summary["summary"]["total_events"] == 2
    assert summary["summary"]["passed"] == 1
    assert summary["summary"]["failed"] == 1
    assert summary["summary"]["success_rate"] == 50.0
    assert summary["scheduler"]["running"] is True
    assert grid["rows"][0]["schedule_name"] == "nightly"
    assert grid["rows"][0]["last_status"] == "FAILED"
    assert grid["rows"][0]["next_run_at"] == "2026-07-19T02:00:00+00:00"
    assert len(timeline["segments"]) == 2
    assert {point["status"] for point in metrics["outcomes"]} == {"PASSED", "FAILED"}


def test_export_rows_and_csv_share_filtered_telemetry():
    db = _session()
    nightly = _schedule(db, "nightly")
    now = datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc)
    telemetry = SchedulerTelemetryRepository(db)
    telemetry.record_event(
        schedule_id=nightly.id,
        schedule_name="nightly",
        job_name="orders",
        selection_id=7,
        selection_version=3,
        run_id="run-1",
        event_state="completed",
        status="PASSED",
        exit_code=0,
        started_at=now - timedelta(minutes=2),
        finished_at=now,
        duration_ms=120000,
        created_at=now,
    )

    service = SchedulerReportingService(db)
    filters = SchedulerReportFilters(from_dt=now - timedelta(hours=1), to_dt=now, job="night")

    rows = service.export_rows(filters)
    csv_text = service.export_csv(filters)

    assert rows == [
        {
            "schedule_id": nightly.id,
            "schedule_name": "nightly",
            "job_name": "orders",
            "selection_id": 7,
            "selection_version": 3,
            "run_id": "run-1",
            "event_state": "completed",
            "status": "PASSED",
            "exit_code": 0,
            "started_at": "2026-07-18T04:58:00",
            "finished_at": "2026-07-18T05:00:00",
            "duration_seconds": 120.0,
            "error_summary": None,
            "created_at": "2026-07-18T05:00:00",
        }
    ]
    assert csv_text.startswith("schedule_id,schedule_name,job_name")
    assert "nightly,orders,7,3,run-1,completed,PASSED" in csv_text


def test_service_returns_warning_when_telemetry_empty():
    db = _session()
    _schedule(db, "nightly")
    service = SchedulerReportingService(db, runtime_snapshot={"available": True, "running": False, "job_count": 0, "timezone": "UTC", "jobs": {}})

    summary = service.summary(SchedulerReportFilters(days=7))

    assert summary["summary"]["total_events"] == 0
    assert "No scheduler telemetry found for the selected filters" in summary["warnings"]


def test_prune_uses_30_day_default():
    db = _session()
    sched = _schedule(db, "nightly")
    now = datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc)
    telemetry = SchedulerTelemetryRepository(db)
    telemetry.record_event(schedule_id=sched.id, schedule_name="nightly", event_state="completed", status="PASSED", created_at=now - timedelta(days=31))
    telemetry.record_event(schedule_id=sched.id, schedule_name="nightly", event_state="completed", status="PASSED", created_at=now - timedelta(days=3))

    result = SchedulerReportingService(db).prune(now=now)

    assert result == {"retention_days": 30, "deleted": 1}
