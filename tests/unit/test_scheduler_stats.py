from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.models import ScheduledRun, TestRun


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_schedule(db: Session, name: str, enabled: bool = True) -> ScheduledRun:
    sched = ScheduledRun(
        name=name,
        cron_expr="0 6 * * *",
        job_sequence=[name],
        source_env="dev",
        target_env="prod",
        enabled=enabled,
    )
    db.add(sched)
    db.commit()
    db.refresh(sched)
    return sched


def _add_run(
    db: Session,
    *,
    run_id: str,
    selection_id: int,
    status: str,
    started_at: datetime,
    duration_seconds: int = 60,
    passed: int = 0,
    failed: int = 0,
    error: int = 0,
) -> None:
    db.add(
        TestRun(
            run_id=run_id,
            status=status,
            started_at=started_at,
            completed_at=started_at + timedelta(seconds=duration_seconds),
            source_env="dev",
            target_env="prod",
            total_tests=passed + failed + error,
            passed=passed,
            failed=failed,
            error=error,
            run_type="scheduled",
            selection_id=selection_id,
        )
    )
    db.commit()


def test_scheduler_stats_empty_database_reports_zeroes():
    from api.services.scheduler_stats import build_scheduler_stats

    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    db = _session()

    stats = build_scheduler_stats(
        db,
        now=now,
        runtime_snapshot={"available": True, "running": False, "job_count": 0, "timezone": "UTC", "jobs": {}},
    )

    assert stats["window_days"] == 30
    assert stats["summary"]["total_schedules"] == 0
    assert stats["summary"]["runs_triggered"] == 0
    assert stats["summary"]["success_rate"] is None
    assert stats["scheduler"]["running"] is False


def test_scheduler_stats_aggregates_recent_runs_by_schedule_selection():
    from api.services.scheduler_stats import build_scheduler_stats

    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    db = _session()
    nightly = _add_schedule(db, "nightly", enabled=True)
    disabled = _add_schedule(db, "disabled", enabled=False)
    _add_run(db, run_id="r1", selection_id=nightly.selection_id or nightly.id, status="PASSED", started_at=now - timedelta(days=1), passed=3)
    _add_run(db, run_id="r2", selection_id=nightly.selection_id or nightly.id, status="FAILED", started_at=now - timedelta(days=2), failed=1)
    _add_run(db, run_id="old", selection_id=nightly.selection_id or nightly.id, status="PASSED", started_at=now - timedelta(days=45), passed=1)

    stats = build_scheduler_stats(
        db,
        days=30,
        now=now,
        runtime_snapshot={
            "available": True,
            "running": True,
            "job_count": 1,
            "timezone": "UTC",
            "jobs": {nightly.id: {"next_run_at": "2026-07-19T06:00:00Z"}},
        },
    )

    assert stats["summary"]["total_schedules"] == 2
    assert stats["summary"]["enabled_schedules"] == 1
    assert stats["summary"]["disabled_schedules"] == 1
    assert stats["summary"]["runs_triggered"] == 2
    assert stats["summary"]["passed"] == 1
    assert stats["summary"]["failed"] == 1
    assert stats["summary"]["success_rate"] == 50.0
    by_name = {item["name"]: item for item in stats["schedules"]}
    assert by_name["nightly"]["registered"] is True
    assert by_name["nightly"]["next_run_at"] == "2026-07-19T06:00:00Z"
    assert by_name["nightly"]["last_status"] == "PASSED"
    assert by_name["disabled"]["runs_triggered"] == 0


def test_scheduler_stats_gate_options_report_failures():
    from api.services.scheduler_stats import GateOptions, build_scheduler_stats

    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    db = _session()
    sched = _add_schedule(db, "nightly", enabled=True)
    _add_run(db, run_id="r1", selection_id=sched.selection_id or sched.id, status="FAILED", started_at=now - timedelta(days=1), failed=1)

    stats = build_scheduler_stats(
        db,
        now=now,
        runtime_snapshot={"available": True, "running": False, "job_count": 0, "timezone": "UTC", "jobs": {}},
        gate_options=GateOptions(fail_on_stopped=True, min_success_rate=95.0),
    )

    assert stats["gate"]["status"] == "failed"
    assert stats["gate"]["exit_code"] == 1
    assert "scheduler is not running" in stats["gate"]["reasons"]
    assert "success rate 0.0 is below 95.0" in stats["gate"]["reasons"]


def test_scheduler_stats_counts_cancelled_blocked_and_error_outcomes():
    from api.services.scheduler_stats import build_scheduler_stats

    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    db = _session()
    sched = _add_schedule(db, "nightly", enabled=True)
    selection_id = sched.selection_id or sched.id
    _add_run(db, run_id="passed", selection_id=selection_id, status="PASSED", started_at=now - timedelta(hours=5), passed=1)
    _add_run(db, run_id="error", selection_id=selection_id, status="ERROR", started_at=now - timedelta(hours=4), error=1)
    _add_run(db, run_id="cancelled", selection_id=selection_id, status="CANCELLED", started_at=now - timedelta(hours=3))
    _add_run(db, run_id="blocked", selection_id=selection_id, status="BLOCKED", started_at=now - timedelta(hours=2))

    stats = build_scheduler_stats(
        db,
        now=now,
        runtime_snapshot={"available": True, "running": True, "job_count": 0, "timezone": "UTC", "jobs": {}},
    )

    assert stats["summary"]["runs_triggered"] == 4
    assert stats["summary"]["passed"] == 1
    assert stats["summary"]["error"] == 1
    assert stats["summary"]["cancelled"] == 1
    assert stats["summary"]["blocked"] == 1
    assert stats["summary"]["success_rate"] == 25.0
    schedule = stats["schedules"][0]
    assert schedule["last_status"] == "BLOCKED"
    assert schedule["registered"] is False


def test_scheduler_stats_rejects_invalid_days():
    from api.services.scheduler_stats import build_scheduler_stats

    db = _session()

    try:
        build_scheduler_stats(db, days=366)
    except ValueError as exc:
        assert str(exc) == "days must be between 1 and 365"
    else:
        raise AssertionError("expected invalid days to raise ValueError")


def test_scheduler_stats_min_success_rate_fails_when_no_runs():
    from api.services.scheduler_stats import GateOptions, build_scheduler_stats

    db = _session()
    _add_schedule(db, "nightly", enabled=True)

    stats = build_scheduler_stats(
        db,
        runtime_snapshot={"available": True, "running": True, "job_count": 1, "timezone": "UTC", "jobs": {}},
        gate_options=GateOptions(min_success_rate=95.0),
    )

    assert stats["gate"]["status"] == "failed"
    assert stats["gate"]["exit_code"] == 1
    assert "success rate is unavailable, below 95.0" in stats["gate"]["reasons"]
