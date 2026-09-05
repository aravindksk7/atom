from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.models import ScheduledRun
from etl_framework.repository.repository import SchedulerTelemetryRepository
from etl_framework.runner import cli as cli_module


def FAILED_EVENT_START() -> datetime:
    """Start of the seeded FAILED event: yesterday, safely inside any lookback."""
    return (datetime.now(timezone.utc) - timedelta(days=1)).replace(
        hour=5, minute=0, second=0, microsecond=0
    )


def PASSED_EVENT_START() -> datetime:
    """Start of the seeded PASSED event: the day before the FAILED one."""
    return FAILED_EVENT_START() - timedelta(days=1)


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with Session(engine) as db:
        sched = ScheduledRun(
            name="nightly",
            cron_expr="0 2 * * *",
            selection_id=1,
            selection_version=1,
            source_env="dev",
            target_env="prod",
        )
        db.add(sched)
        db.commit()
        db.refresh(sched)
        repo = SchedulerTelemetryRepository(db)
        # Anchored to "now" rather than fixed calendar dates: the report CLI filters on
        # a rolling lookback window, so hardcoded dates age out and fail with time.
        failed_started = FAILED_EVENT_START()
        passed_started = PASSED_EVENT_START()
        repo.record_event(
            schedule_id=sched.id,
            schedule_name="nightly",
            job_name="warehouse-refresh",
            event_state="failed",
            status="FAILED",
            exit_code=1,
            started_at=failed_started,
            finished_at=failed_started + timedelta(minutes=4),
            duration_ms=240000,
            run_id="run-1",
            error_summary="source timeout",
            created_at=failed_started + timedelta(minutes=4),
        )
        repo.record_event(
            schedule_id=sched.id,
            schedule_name="nightly",
            job_name="warehouse-refresh",
            event_state="completed",
            status="PASSED",
            exit_code=0,
            started_at=passed_started,
            finished_at=passed_started + timedelta(minutes=3),
            duration_ms=180000,
            run_id="run-0",
            created_at=passed_started + timedelta(minutes=3),
        )
    return SessionLocal


def test_scheduler_report_text_summary_filters(monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "_report_session_factory", _session_factory())

    code = cli_module.main([
        "--scheduler-report",
        "--summary",
        "--status", "failed",
        "--job", "nightly",
        "--exit-code", "1",
        # Bracket the seeded FAILED event without naming fixed calendar dates.
        "--from", (FAILED_EVENT_START() - timedelta(hours=5)).isoformat(),
        "--to", (FAILED_EVENT_START() + timedelta(hours=19)).isoformat(),
    ])

    out = capsys.readouterr().out
    assert code == 0
    assert "Scheduler report" in out
    assert "Events: total=1" in out
    assert "failed=1" in out
    assert "Success rate: 0.0" in out


def test_scheduler_report_json_export_rows(monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "_report_session_factory", _session_factory())

    code = cli_module.main(["--scheduler-report", "--format", "json", "--status", "failed", "--days", "30"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["summary"]["summary"]["failed"] == 1
    assert payload["rows"][0]["schedule_name"] == "nightly"
    assert payload["rows"][0]["status"] == "FAILED"
    assert payload["rows"][0]["exit_code"] == 1


def test_scheduler_report_csv_writes_report_output(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli_module, "_report_session_factory", _session_factory())
    report_path = tmp_path / "scheduler-report.csv"

    code = cli_module.main([
        "--scheduler-report",
        "--format", "csv",
        "--report-output", str(report_path),
        "--status", "failed",
    ])

    assert code == 0
    assert capsys.readouterr().out == ""
    rows = list(csv.DictReader(io.StringIO(report_path.read_text(encoding="utf-8"))))
    assert len(rows) == 1
    assert rows[0]["schedule_name"] == "nightly"
    assert rows[0]["status"] == "FAILED"
    assert rows[0]["exit_code"] == "1"
