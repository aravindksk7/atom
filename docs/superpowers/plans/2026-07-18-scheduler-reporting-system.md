# Scheduler Reporting System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a scheduler reporting system with telemetry-backed API, dashboard, and CLI reporting for scheduled jobs.

**Architecture:** Add separate scheduler telemetry storage and a shared `SchedulerReportingService` that powers FastAPI routes, the Alpine.js dashboard tab, and CLI output. Existing scheduler orchestration remains intact except for narrow best-effort listener calls that never change job execution semantics.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, SQLite, APScheduler, Alpine.js, Chart.js, pytest, Playwright, Node HTML build scripts.

## Global Constraints

- Existing core scheduling behavior must remain unchanged except for narrow, best-effort telemetry listener calls.
- The reporting system must share one underlying reporting engine across UI, API, and CLI surfaces.
- Dashboard location: new dedicated Scheduler Reports tab.
- Dashboard management level: full management actions: enable/disable, edit cron, delete, and run-now.
- Telemetry retention: 30 days by default.
- Reporting commands must support summaries, filters, and JSON/CSV/text output without launching the web server.
- Telemetry capture must be best-effort and must never fail, retry, skip, or otherwise change a scheduled job.
- Reuse existing database/session/repository conventions.
- Reuse existing frontend module and partial conventions.
- Do not rewrite APScheduler orchestration.

---

## File Structure

### Backend Data And Repository

- Modify `etl_framework/repository/models.py`
  - Add `SchedulerTelemetryEvent` ORM model.
  - Add indexes for schedule id, event state, normalized status, exit code, started timestamp, and created timestamp.
- Modify `etl_framework/repository/database.py`
  - Ensure the new table and indexes are created for existing SQLite databases.
- Modify `etl_framework/repository/repository.py`
  - Add `SchedulerTelemetryRepository` with record, query, latest-by-schedule, and prune methods.

### Backend Reporting And Telemetry

- Create `api/services/scheduler_reporting.py`
  - Define `SchedulerReportFilters` dataclass.
  - Define `SchedulerReportingService` with `summary`, `grid`, `timeline`, `metrics`, `export_rows`, and `prune`.
- Create `api/services/scheduler_telemetry.py`
  - Define best-effort listener helpers used by scheduler orchestration.
- Modify `api/services/scheduler.py`
  - Add listener calls around `_run_schedule` start, skip, and terminal outcomes.
  - Do not change scheduler trigger setup, locking, run orchestration, or `_execute_run` behavior.
- Create `api/routes/scheduler_reports.py`
  - Add summary, grid, timeline, metrics, export, and prune endpoints.
- Modify `api/main.py`
  - Include `scheduler_reports.router` under `/api/scheduler-reports`.

### CLI

- Modify `etl_framework/runner/cli.py`
  - Add scheduler reporting arguments and output formatting.
  - Call `SchedulerReportingService` through the existing local session factory pattern.

### Frontend

- Create `frontend/features/scheduler-reports.js`
  - Alpine feature slice for filters, polling, charts, exports, and schedule management actions.
- Create `frontend/partials/tab-scheduler-reports.html`
  - Dedicated Scheduler Reports tab markup.
- Modify `frontend/app.js`
  - Add navigation tab and merge `ETL_FEATURE_SCHEDULER_REPORTS()` into `FEATURE_SLICES`.
- Modify `frontend/index.template.html`
  - Add include marker for `partials/tab-scheduler-reports.html`.
- Modify `frontend/index.html`
  - Regenerate with `npm run build:html`.
- Modify `frontend/styles.css`
  - Add focused scheduler report layout, timeline, status badge, and responsive rules only if existing utility classes are insufficient.

### Tests

- Create `tests/unit/test_scheduler_telemetry_repository.py`
- Create `tests/unit/test_scheduler_reporting_service.py`
- Create `tests/unit/test_scheduler_reports_routes.py`
- Create `tests/unit/test_scheduler_telemetry_listener.py`
- Create `tests/unit/test_scheduler_report_cli.py`
- Create or modify `tests/integration/test_api_frontend_smoke.py` for the dashboard tab smoke check.
- Add Playwright coverage in existing e2e style only if the repository's current e2e suite is passing locally.

---

### Task 1: Telemetry ORM, Migration, And Repository

**Files:**
- Modify: `etl_framework/repository/models.py`
- Modify: `etl_framework/repository/database.py`
- Modify: `etl_framework/repository/repository.py`
- Test: `tests/unit/test_scheduler_telemetry_repository.py`

**Interfaces:**
- Produces: ORM class `SchedulerTelemetryEvent`.
- Produces: repository class `SchedulerTelemetryRepository`.
- Produces: `SchedulerTelemetryRepository.record_event(...) -> SchedulerTelemetryEvent`.
- Produces: `SchedulerTelemetryRepository.query_events(filters: SchedulerTelemetryQuery | None = None) -> list[SchedulerTelemetryEvent]`.
- Produces: `SchedulerTelemetryRepository.latest_by_schedule() -> dict[int, SchedulerTelemetryEvent]`.
- Produces: `SchedulerTelemetryRepository.prune_older_than(cutoff: datetime) -> int`.

- [ ] **Step 1: Write failing repository tests**

Create `tests/unit/test_scheduler_telemetry_repository.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/unit/test_scheduler_telemetry_repository.py -v
```

Expected: FAIL with `ImportError` or `AttributeError` for `SchedulerTelemetryRepository` / `SchedulerTelemetryQuery` / `SchedulerTelemetryEvent`.

- [ ] **Step 3: Add telemetry model**

In `etl_framework/repository/models.py`, add imports if missing:

```python
from sqlalchemy import Index
```

Add this model near `ScheduledRun` so scheduler-related tables stay together:

```python
class SchedulerTelemetryEvent(Base):
    __tablename__ = "scheduler_telemetry_events"

    id = Column(Integer, primary_key=True, index=True)
    schedule_id = Column(Integer, ForeignKey("scheduled_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    schedule_name = Column(String(255), nullable=False, index=True)
    job_name = Column(String(255), nullable=True, index=True)
    selection_id = Column(Integer, nullable=True, index=True)
    selection_version = Column(Integer, nullable=True)
    run_id = Column(String(36), nullable=True, index=True)
    event_state = Column(String(32), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    exit_code = Column(Integer, nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True, index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_summary = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)

    schedule = relationship("ScheduledRun")


Index("ix_scheduler_telemetry_schedule_created", SchedulerTelemetryEvent.schedule_id, SchedulerTelemetryEvent.created_at)
Index("ix_scheduler_telemetry_status_created", SchedulerTelemetryEvent.status, SchedulerTelemetryEvent.created_at)
Index("ix_scheduler_telemetry_state_created", SchedulerTelemetryEvent.event_state, SchedulerTelemetryEvent.created_at)
```

- [ ] **Step 4: Add SQLite compatibility migration**

In `etl_framework/repository/database.py`, extend `_ensure_compare_columns()` inside the existing `with bind.begin() as conn:` block with:

```python
        ensure_table(conn, "scheduler_telemetry_events",
            "CREATE TABLE IF NOT EXISTS scheduler_telemetry_events ("
            "id INTEGER PRIMARY KEY, "
            "schedule_id INTEGER REFERENCES scheduled_runs(id) ON DELETE SET NULL, "
            "schedule_name VARCHAR(255) NOT NULL, "
            "job_name VARCHAR(255), "
            "selection_id INTEGER, "
            "selection_version INTEGER, "
            "run_id VARCHAR(36), "
            "event_state VARCHAR(32) NOT NULL, "
            "status VARCHAR(32) NOT NULL, "
            "exit_code INTEGER, "
            "started_at DATETIME, "
            "finished_at DATETIME, "
            "duration_ms INTEGER, "
            "error_summary TEXT, "
            "metadata_json JSON, "
            "created_at DATETIME NOT NULL)"
        )
        ensure_index(conn, "ix_scheduler_telemetry_events_schedule_id", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_schedule_id ON scheduler_telemetry_events (schedule_id)")
        ensure_index(conn, "ix_scheduler_telemetry_events_schedule_name", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_schedule_name ON scheduler_telemetry_events (schedule_name)")
        ensure_index(conn, "ix_scheduler_telemetry_events_job_name", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_job_name ON scheduler_telemetry_events (job_name)")
        ensure_index(conn, "ix_scheduler_telemetry_events_selection_id", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_selection_id ON scheduler_telemetry_events (selection_id)")
        ensure_index(conn, "ix_scheduler_telemetry_events_run_id", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_run_id ON scheduler_telemetry_events (run_id)")
        ensure_index(conn, "ix_scheduler_telemetry_events_event_state", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_event_state ON scheduler_telemetry_events (event_state)")
        ensure_index(conn, "ix_scheduler_telemetry_events_status", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_status ON scheduler_telemetry_events (status)")
        ensure_index(conn, "ix_scheduler_telemetry_events_exit_code", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_exit_code ON scheduler_telemetry_events (exit_code)")
        ensure_index(conn, "ix_scheduler_telemetry_events_started_at", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_started_at ON scheduler_telemetry_events (started_at)")
        ensure_index(conn, "ix_scheduler_telemetry_events_created_at", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_created_at ON scheduler_telemetry_events (created_at)")
        ensure_index(conn, "ix_scheduler_telemetry_schedule_created", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_schedule_created ON scheduler_telemetry_events (schedule_id, created_at)")
        ensure_index(conn, "ix_scheduler_telemetry_status_created", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_status_created ON scheduler_telemetry_events (status, created_at)")
        ensure_index(conn, "ix_scheduler_telemetry_state_created", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_state_created ON scheduler_telemetry_events (event_state, created_at)")
```

- [ ] **Step 5: Add telemetry repository**

In `etl_framework/repository/repository.py`, extend the model import list with `SchedulerTelemetryEvent`, then add:

```python
from dataclasses import dataclass
```

Add this near `ScheduleRepository`:

```python
@dataclass(frozen=True)
class SchedulerTelemetryQuery:
    from_dt: datetime | None = None
    to_dt: datetime | None = None
    schedule_id: int | None = None
    job: str | None = None
    status: str | None = None
    exit_code: int | None = None


class SchedulerTelemetryRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def record_event(
        self,
        *,
        schedule_id: int | None,
        schedule_name: str,
        event_state: str,
        status: str,
        job_name: str | None = None,
        selection_id: int | None = None,
        selection_version: int | None = None,
        run_id: str | None = None,
        exit_code: int | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        duration_ms: int | None = None,
        error_summary: str | None = None,
        metadata_json: dict | None = None,
        created_at: datetime | None = None,
    ) -> SchedulerTelemetryEvent:
        event = SchedulerTelemetryEvent(
            schedule_id=schedule_id,
            schedule_name=schedule_name,
            job_name=job_name,
            selection_id=selection_id,
            selection_version=selection_version,
            run_id=run_id,
            event_state=event_state.lower(),
            status=status.upper(),
            exit_code=exit_code,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            error_summary=error_summary,
            metadata_json=metadata_json or {},
            created_at=created_at or datetime.now(timezone.utc),
        )
        self._db.add(event)
        self._db.commit()
        self._db.refresh(event)
        return event

    def query_events(self, filters: SchedulerTelemetryQuery | None = None) -> list[SchedulerTelemetryEvent]:
        filters = filters or SchedulerTelemetryQuery()
        q = self._db.query(SchedulerTelemetryEvent)
        if filters.from_dt is not None:
            q = q.filter(SchedulerTelemetryEvent.created_at >= filters.from_dt)
        if filters.to_dt is not None:
            q = q.filter(SchedulerTelemetryEvent.created_at <= filters.to_dt)
        if filters.schedule_id is not None:
            q = q.filter(SchedulerTelemetryEvent.schedule_id == filters.schedule_id)
        if filters.job:
            pattern = f"%{filters.job.lower()}%"
            q = q.filter(func.lower(SchedulerTelemetryEvent.schedule_name).like(pattern))
        if filters.status:
            q = q.filter(SchedulerTelemetryEvent.status == filters.status.upper())
        if filters.exit_code is not None:
            q = q.filter(SchedulerTelemetryEvent.exit_code == filters.exit_code)
        return q.order_by(SchedulerTelemetryEvent.created_at.asc(), SchedulerTelemetryEvent.id.asc()).all()

    def latest_by_schedule(self) -> dict[int, SchedulerTelemetryEvent]:
        rows = self._db.query(SchedulerTelemetryEvent).order_by(
            SchedulerTelemetryEvent.schedule_id.asc(),
            SchedulerTelemetryEvent.created_at.desc(),
            SchedulerTelemetryEvent.id.desc(),
        ).all()
        latest: dict[int, SchedulerTelemetryEvent] = {}
        for row in rows:
            if row.schedule_id is not None and row.schedule_id not in latest:
                latest[row.schedule_id] = row
        return latest

    def prune_older_than(self, cutoff: datetime) -> int:
        deleted = self._db.query(SchedulerTelemetryEvent).filter(
            SchedulerTelemetryEvent.created_at < cutoff
        ).delete(synchronize_session=False)
        self._db.commit()
        return int(deleted or 0)
```

- [ ] **Step 6: Run repository tests**

Run:

```powershell
python -m pytest tests/unit/test_scheduler_telemetry_repository.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit telemetry storage**

```powershell
git add etl_framework/repository/models.py etl_framework/repository/database.py etl_framework/repository/repository.py tests/unit/test_scheduler_telemetry_repository.py
git commit -m "feat(scheduler): add telemetry storage"
```

---

### Task 2: Shared Scheduler Reporting Service

**Files:**
- Create: `api/services/scheduler_reporting.py`
- Test: `tests/unit/test_scheduler_reporting_service.py`

**Interfaces:**
- Consumes: `SchedulerTelemetryRepository`, `SchedulerTelemetryQuery`, `ScheduleRepository`.
- Produces: `SchedulerReportFilters` dataclass.
- Produces: `SchedulerReportingService.summary(filters: SchedulerReportFilters) -> dict`.
- Produces: `SchedulerReportingService.grid(filters: SchedulerReportFilters) -> dict`.
- Produces: `SchedulerReportingService.timeline(filters: SchedulerReportFilters) -> dict`.
- Produces: `SchedulerReportingService.metrics(filters: SchedulerReportFilters) -> dict`.
- Produces: `SchedulerReportingService.export_rows(filters: SchedulerReportFilters) -> list[dict]`.
- Produces: `SchedulerReportingService.prune(retention_days: int = 30, now: datetime | None = None) -> dict`.

- [ ] **Step 1: Write failing service tests**

Create `tests/unit/test_scheduler_reporting_service.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/unit/test_scheduler_reporting_service.py -v
```

Expected: FAIL with missing `api.services.scheduler_reporting`.

- [ ] **Step 3: Implement reporting service**

Create `api/services/scheduler_reporting.py`:

```python
from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from api.services.scheduler import get_scheduler_runtime_snapshot
from etl_framework.repository.models import SchedulerTelemetryEvent
from etl_framework.repository.repository import (
    ScheduleRepository,
    SchedulerTelemetryQuery,
    SchedulerTelemetryRepository,
)

TERMINAL_SUCCESS = {"PASSED", "COMPLETED"}
TERMINAL_FAILURE = {"FAILED", "ERROR", "CANCELLED", "BLOCKED"}


@dataclass(frozen=True)
class SchedulerReportFilters:
    from_dt: datetime | None = None
    to_dt: datetime | None = None
    days: int | None = 7
    schedule_id: int | None = None
    job: str | None = None
    status: str | None = None
    exit_code: int | None = None

    def resolved(self, now: datetime | None = None) -> "SchedulerReportFilters":
        now = now or datetime.now(timezone.utc)
        if self.from_dt is None and self.days is not None:
            return SchedulerReportFilters(
                from_dt=now - timedelta(days=self.days),
                to_dt=self.to_dt or now,
                days=self.days,
                schedule_id=self.schedule_id,
                job=self.job,
                status=self.status,
                exit_code=self.exit_code,
            )
        return self

    def telemetry_query(self, now: datetime | None = None) -> SchedulerTelemetryQuery:
        resolved = self.resolved(now=now)
        return SchedulerTelemetryQuery(
            from_dt=resolved.from_dt,
            to_dt=resolved.to_dt,
            schedule_id=resolved.schedule_id,
            job=resolved.job,
            status=resolved.status,
            exit_code=resolved.exit_code,
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _duration_seconds(duration_ms: int | None) -> float | None:
    return round(duration_ms / 1000, 3) if duration_ms is not None else None


class SchedulerReportingService:
    def __init__(self, db: Session, runtime_snapshot: dict | None = None) -> None:
        self._db = db
        self._runtime_snapshot = runtime_snapshot

    def _runtime(self) -> dict:
        return self._runtime_snapshot if self._runtime_snapshot is not None else get_scheduler_runtime_snapshot()

    def _events(self, filters: SchedulerReportFilters) -> list[SchedulerTelemetryEvent]:
        return SchedulerTelemetryRepository(self._db).query_events(filters.telemetry_query())

    def _warnings(self, events: list[SchedulerTelemetryEvent]) -> list[str]:
        warnings: list[str] = []
        if not events:
            warnings.append("No scheduler telemetry found for the selected filters")
        runtime = self._runtime()
        if not runtime.get("available", False):
            warnings.append("Scheduler runtime is unavailable")
        elif not runtime.get("running", False):
            warnings.append("Scheduler runtime is not running")
        return warnings

    def summary(self, filters: SchedulerReportFilters) -> dict:
        start = time.perf_counter()
        events = self._events(filters)
        counts = {"passed": 0, "failed": 0, "error": 0, "cancelled": 0, "blocked": 0}
        durations = [event.duration_ms for event in events if event.duration_ms is not None]
        for event in events:
            status = (event.status or "").upper()
            if status in TERMINAL_SUCCESS:
                counts["passed"] += 1
            elif status == "ERROR":
                counts["error"] += 1
            elif status == "CANCELLED":
                counts["cancelled"] += 1
            elif status == "BLOCKED":
                counts["blocked"] += 1
            elif status == "FAILED":
                counts["failed"] += 1
        total_terminal = sum(counts.values())
        success_rate = round((counts["passed"] / total_terminal) * 100, 1) if total_terminal else None
        query_ms = round((time.perf_counter() - start) * 1000, 3)
        return {
            "filters": self._filters_payload(filters),
            "generated_at": _iso(datetime.now(timezone.utc)),
            "scheduler": self._runtime(),
            "summary": {
                "total_events": len(events),
                "success_rate": success_rate,
                "avg_duration_seconds": _duration_seconds(int(sum(durations) / len(durations))) if durations else None,
                **counts,
            },
            "performance": {"report_query_ms": query_ms},
            "warnings": self._warnings(events),
        }

    def grid(self, filters: SchedulerReportFilters) -> dict:
        events = self._events(filters)
        latest = SchedulerTelemetryRepository(self._db).latest_by_schedule()
        runtime = self._runtime()
        runtime_jobs = runtime.get("jobs", {}) or {}
        rows = []
        for schedule in ScheduleRepository(self._db).list():
            if filters.schedule_id is not None and schedule.id != filters.schedule_id:
                continue
            if filters.job and filters.job.lower() not in schedule.name.lower():
                continue
            last = latest.get(schedule.id)
            next_run_at = (runtime_jobs.get(schedule.id) or runtime_jobs.get(str(schedule.id)) or {}).get("next_run_at")
            rows.append({
                "schedule_id": schedule.id,
                "schedule_name": schedule.name,
                "enabled": bool(schedule.enabled),
                "cron_expr": schedule.cron_expr,
                "source_env": schedule.source_env,
                "target_env": schedule.target_env,
                "selection_id": schedule.selection_id,
                "selection_version": schedule.selection_version,
                "next_run_at": next_run_at or _iso(schedule.next_run_at),
                "last_run_at": _iso(schedule.last_run_at),
                "last_status": last.status if last else None,
                "last_event_state": last.event_state if last else None,
                "last_duration_seconds": _duration_seconds(last.duration_ms if last else None),
                "last_exit_code": last.exit_code if last else None,
                "last_error_summary": last.error_summary if last else None,
            })
        return {"rows": rows, "warnings": self._warnings(events)}

    def timeline(self, filters: SchedulerReportFilters) -> dict:
        segments = []
        for event in self._events(filters):
            if event.started_at is None:
                continue
            segments.append({
                "schedule_id": event.schedule_id,
                "schedule_name": event.schedule_name,
                "run_id": event.run_id,
                "status": event.status,
                "event_state": event.event_state,
                "started_at": _iso(event.started_at),
                "finished_at": _iso(event.finished_at),
                "duration_seconds": _duration_seconds(event.duration_ms),
                "exit_code": event.exit_code,
                "error_summary": event.error_summary,
            })
        return {"segments": segments, "warnings": self._warnings(self._events(filters))}

    def metrics(self, filters: SchedulerReportFilters) -> dict:
        events = self._events(filters)
        outcomes: dict[str, int] = {}
        durations = []
        for event in events:
            outcomes[event.status] = outcomes.get(event.status, 0) + 1
            if event.duration_ms is not None:
                durations.append(event.duration_ms)
        durations.sort()
        p95 = durations[int((len(durations) - 1) * 0.95)] if durations else None
        return {
            "outcomes": [{"status": status, "count": count} for status, count in sorted(outcomes.items())],
            "runtime": {
                "count": len(durations),
                "avg_seconds": _duration_seconds(int(sum(durations) / len(durations))) if durations else None,
                "p95_seconds": _duration_seconds(p95),
            },
            "warnings": self._warnings(events),
        }

    def export_rows(self, filters: SchedulerReportFilters) -> list[dict]:
        return [
            {
                "schedule_id": event.schedule_id,
                "schedule_name": event.schedule_name,
                "job_name": event.job_name,
                "selection_id": event.selection_id,
                "selection_version": event.selection_version,
                "run_id": event.run_id,
                "event_state": event.event_state,
                "status": event.status,
                "exit_code": event.exit_code,
                "started_at": _iso(event.started_at),
                "finished_at": _iso(event.finished_at),
                "duration_seconds": _duration_seconds(event.duration_ms),
                "error_summary": event.error_summary,
                "created_at": _iso(event.created_at),
            }
            for event in self._events(filters)
        ]

    def export_csv(self, filters: SchedulerReportFilters) -> str:
        rows = self.export_rows(filters)
        output = io.StringIO()
        fieldnames = [
            "schedule_id", "schedule_name", "job_name", "selection_id", "selection_version",
            "run_id", "event_state", "status", "exit_code", "started_at", "finished_at",
            "duration_seconds", "error_summary", "created_at",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()

    def prune(self, retention_days: int = 30, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        deleted = SchedulerTelemetryRepository(self._db).prune_older_than(now - timedelta(days=retention_days))
        return {"retention_days": retention_days, "deleted": deleted}

    def _filters_payload(self, filters: SchedulerReportFilters) -> dict:
        resolved = filters.resolved()
        return {
            "from": _iso(resolved.from_dt),
            "to": _iso(resolved.to_dt),
            "days": resolved.days,
            "schedule_id": resolved.schedule_id,
            "job": resolved.job,
            "status": resolved.status,
            "exit_code": resolved.exit_code,
        }
```

- [ ] **Step 4: Run service tests**

```powershell
python -m pytest tests/unit/test_scheduler_reporting_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit reporting service**

```powershell
git add api/services/scheduler_reporting.py tests/unit/test_scheduler_reporting_service.py
git commit -m "feat(scheduler): add reporting service"
```

---

### Task 3: Best-Effort Scheduler Telemetry Listener

**Files:**
- Create: `api/services/scheduler_telemetry.py`
- Modify: `api/services/scheduler.py`
- Test: `tests/unit/test_scheduler_telemetry_listener.py`
- Regression Test: `tests/unit/test_scheduler.py`

**Interfaces:**
- Consumes: `SchedulerTelemetryRepository.record_event(...)`.
- Produces: `record_scheduler_event(db: Session, schedule: ScheduledRun | None, event_state: str, status: str, ...) -> None`.
- Produces: `record_scheduler_event_best_effort(session_factory, schedule_id: int, schedule_name: str, event_state: str, status: str, ...) -> None`.

- [ ] **Step 1: Write failing listener tests**

Create `tests/unit/test_scheduler_telemetry_listener.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
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
    sched = ScheduledRun(name="nightly", cron_expr="0 2 * * *", selection_id=7, selection_version=3, source_env="dev", target_env="prod")
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


def test_best_effort_swallows_listener_database_errors(monkeypatch):
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
```

- [ ] **Step 2: Run listener tests to verify they fail**

```powershell
python -m pytest tests/unit/test_scheduler_telemetry_listener.py -v
```

Expected: FAIL with missing `api.services.scheduler_telemetry`.

- [ ] **Step 3: Implement telemetry listener helpers**

Create `api/services/scheduler_telemetry.py`:

```python
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
    SchedulerTelemetryRepository(db).record_event(
        schedule_id=schedule.id if schedule is not None else schedule_id,
        schedule_name=schedule.name if schedule is not None else (schedule_name or "unknown"),
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
```

- [ ] **Step 4: Add non-invasive listener calls to scheduler**

In `api/services/scheduler.py`, import monotonic timing near top:

```python
import time
```

Inside `_run_schedule`, after imports, add:

```python
    from api.services.scheduler_telemetry import record_scheduler_event
```

Inside `_run_schedule`, after `sched` is loaded and before missing/enabled returns, add skipped telemetry for disabled/missing states using the same existing `db` session:

```python
        if sched is None:
            record_scheduler_event(db, None, "missed", "ERROR", schedule_id=schedule_id, schedule_name=name, error_summary="Schedule not found")
            return
        if not sched.enabled:
            record_scheduler_event(db, sched, "skipped", "CANCELLED", error_summary="Schedule disabled")
            return
```

Immediately before generating `run_id`, add:

```python
        started_at = datetime.now(timezone.utc)
        started_perf = time.perf_counter()
```

Immediately after `run_id = str(_uuid.uuid4())`, add:

```python
        record_scheduler_event(db, sched, "started", "RUNNING", run_id=run_id, started_at=started_at)
```

Immediately after `_execute_run(...)`, add:

```python
        finished_at = datetime.now(timezone.utc)
        duration_ms = int((time.perf_counter() - started_perf) * 1000)
        run = run_repo.get_run(run_id)
        terminal_status = (run.status if run is not None else "COMPLETED") or "COMPLETED"
        exit_code = 0 if terminal_status in {"PASSED", "COMPLETED"} else 1
        record_scheduler_event(
            db,
            sched,
            "completed" if exit_code == 0 else "failed",
            terminal_status,
            run_id=run_id,
            exit_code=exit_code,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            error_summary=getattr(run, "error_message", None) if run is not None else None,
        )
```

In the `except Exception as exc:` block, before logging or after logging, add a separate best-effort session call because the current session may be in an error state:

```python
        from etl_framework.repository.database import SessionLocal as _TelemetrySessionLocal
        from api.services.scheduler_telemetry import record_scheduler_event_best_effort
        record_scheduler_event_best_effort(
            _TelemetrySessionLocal,
            schedule_id=schedule_id,
            schedule_name=name,
            event_state="failed",
            status="ERROR",
            exit_code=1,
            finished_at=datetime.now(timezone.utc),
            error_summary=str(exc),
        )
```

Do not remove the existing `logger.exception(...)` or `db.close()` behavior.

- [ ] **Step 5: Run listener and scheduler regression tests**

```powershell
python -m pytest tests/unit/test_scheduler_telemetry_listener.py tests/unit/test_scheduler.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit telemetry listener**

```powershell
git add api/services/scheduler_telemetry.py api/services/scheduler.py tests/unit/test_scheduler_telemetry_listener.py
git commit -m "feat(scheduler): record execution telemetry"
```

---

### Task 4: Scheduler Report API Routes And Exports

**Files:**
- Create: `api/routes/scheduler_reports.py`
- Modify: `api/main.py`
- Test: `tests/unit/test_scheduler_reports_routes.py`

**Interfaces:**
- Consumes: `SchedulerReportingService`.
- Produces: `GET /api/scheduler-reports/summary`.
- Produces: `GET /api/scheduler-reports/grid`.
- Produces: `GET /api/scheduler-reports/timeline`.
- Produces: `GET /api/scheduler-reports/metrics`.
- Produces: `GET /api/scheduler-reports/export?format=json|csv`.
- Produces: `POST /api/scheduler-reports/prune`.

- [ ] **Step 1: Write failing route tests**

Create `tests/unit/test_scheduler_reports_routes.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.models import ScheduledRun
from etl_framework.repository.repository import SchedulerTelemetryRepository, TokenRepository


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test", is_admin=True)
        sched = ScheduledRun(name="nightly", cron_expr="0 2 * * *", selection_id=1, selection_version=1, source_env="dev", target_env="prod")
        db.add(sched)
        db.commit()
        db.refresh(sched)
        SchedulerTelemetryRepository(db).record_event(
            schedule_id=sched.id,
            schedule_name="nightly",
            event_state="completed",
            status="PASSED",
            exit_code=0,
            started_at=datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc),
        )
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


def test_summary_endpoint_returns_scheduler_report(client):
    resp = client.get("/api/scheduler-reports/summary", params={"days": 30})
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total_events"] == 1
    assert body["summary"]["passed"] == 1


def test_export_endpoint_returns_csv(client):
    resp = client.get("/api/scheduler-reports/export", params={"format": "csv", "days": 30})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "schedule_name" in resp.text
    assert "nightly" in resp.text


def test_rejects_invalid_date_range(client):
    resp = client.get("/api/scheduler-reports/summary", params={"from": "2026-07-20T00:00:00+00:00", "to": "2026-07-01T00:00:00+00:00"})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run route tests to verify they fail**

```powershell
python -m pytest tests/unit/test_scheduler_reports_routes.py -v
```

Expected: FAIL with 404 for `/api/scheduler-reports/summary`.

- [ ] **Step 3: Implement scheduler report routes**

Create `api/routes/scheduler_reports.py`:

```python
from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.services.scheduler_reporting import SchedulerReportFilters, SchedulerReportingService

router = APIRouter(tags=["scheduler-reports"])


def _filters(
    from_dt: Annotated[datetime | None, Query(alias="from")] = None,
    to_dt: Annotated[datetime | None, Query(alias="to")] = None,
    days: int | None = Query(7, ge=1, le=365),
    schedule_id: int | None = Query(None, ge=1),
    job: str | None = None,
    status: str | None = None,
    exit_code: int | None = None,
) -> SchedulerReportFilters:
    if from_dt is not None and to_dt is not None and from_dt > to_dt:
        raise HTTPException(status_code=422, detail="from must be earlier than to")
    return SchedulerReportFilters(
        from_dt=from_dt,
        to_dt=to_dt,
        days=days,
        schedule_id=schedule_id,
        job=job,
        status=status,
        exit_code=exit_code,
    )


@router.get("/summary")
def summary(filters: SchedulerReportFilters = Depends(_filters), db: Session = Depends(get_session)):
    return SchedulerReportingService(db).summary(filters)


@router.get("/grid")
def grid(filters: SchedulerReportFilters = Depends(_filters), db: Session = Depends(get_session)):
    return SchedulerReportingService(db).grid(filters)


@router.get("/timeline")
def timeline(filters: SchedulerReportFilters = Depends(_filters), db: Session = Depends(get_session)):
    return SchedulerReportingService(db).timeline(filters)


@router.get("/metrics")
def metrics(filters: SchedulerReportFilters = Depends(_filters), db: Session = Depends(get_session)):
    return SchedulerReportingService(db).metrics(filters)


@router.get("/export")
def export_report(
    format: str = Query("json", pattern="^(json|csv)$"),
    filters: SchedulerReportFilters = Depends(_filters),
    db: Session = Depends(get_session),
):
    service = SchedulerReportingService(db)
    if format == "csv":
        return Response(
            content=service.export_csv(filters),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=scheduler-report.csv"},
        )
    return Response(
        content=json.dumps({"rows": service.export_rows(filters)}, default=str),
        media_type="application/json",
    )


@router.post("/prune")
def prune(retention_days: int = Query(30, ge=1, le=365), db: Session = Depends(get_session)):
    return SchedulerReportingService(db).prune(retention_days=retention_days)
```

- [ ] **Step 4: Register route in app**

Modify `api/main.py` imports:

```python
from api.routes import scheduler_reports as scheduler_reports_routes
```

Add with existing router includes:

```python
app.include_router(scheduler_reports_routes.router, prefix="/api/scheduler-reports")
```

- [ ] **Step 5: Run route tests**

```powershell
python -m pytest tests/unit/test_scheduler_reports_routes.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit report routes**

```powershell
git add api/routes/scheduler_reports.py api/main.py tests/unit/test_scheduler_reports_routes.py
git commit -m "feat(api): add scheduler report routes"
```

---

### Task 5: Scheduler Report CLI

**Files:**
- Modify: `etl_framework/runner/cli.py`
- Test: `tests/unit/test_scheduler_report_cli.py`

**Interfaces:**
- Consumes: `SchedulerReportingService` and `SchedulerReportFilters`.
- Produces: CLI flags `--scheduler-report`, `--summary`, `--from`, `--to`, `--job`, `--status`, `--exit-code`, `--format text|json|csv`, and `--report-output`.
- Preserves: existing `--scheduler-stats` and `--gate-run` behavior.

- [ ] **Step 1: Write failing CLI tests**

Create `tests/unit/test_scheduler_report_cli.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.models import ScheduledRun
from etl_framework.repository.repository import SchedulerTelemetryRepository
from etl_framework.runner import cli as cli_module


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with Session(engine) as db:
        sched = ScheduledRun(name="nightly", cron_expr="0 2 * * *", selection_id=1, selection_version=1, source_env="dev", target_env="prod")
        db.add(sched)
        db.commit()
        db.refresh(sched)
        SchedulerTelemetryRepository(db).record_event(
            schedule_id=sched.id,
            schedule_name="nightly",
            event_state="failed",
            status="FAILED",
            exit_code=1,
            started_at=datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc),
        )
    return SessionLocal


def test_scheduler_report_summary_text(monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "_gate_session_factory", _session_factory())

    code = cli_module.main(["--scheduler-report", "--summary", "--days", "30"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Scheduler Report" in out
    assert "failed=1" in out


def test_scheduler_report_json(monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "_gate_session_factory", _session_factory())

    code = cli_module.main(["--scheduler-report", "--format", "json", "--status", "failed", "--days", "30"])

    out = capsys.readouterr().out
    assert code == 0
    assert '"rows"' in out
    assert '"schedule_name": "nightly"' in out


def test_scheduler_report_csv_report_output_file(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "_gate_session_factory", _session_factory())
    output = tmp_path / "report.csv"

    code = cli_module.main(["--scheduler-report", "--format", "csv", "--report-output", str(output), "--days", "30"])

    assert code == 0
    text = output.read_text(encoding="utf-8")
    assert "schedule_name" in text
    assert "nightly" in text
```

- [ ] **Step 2: Run CLI tests to verify they fail**

```powershell
python -m pytest tests/unit/test_scheduler_report_cli.py -v
```

Expected: FAIL because `--scheduler-report` is unrecognized.

- [ ] **Step 3: Add CLI parser flags**

In `etl_framework/runner/cli.py`, add imports:

```python
from datetime import datetime
```

Add parser arguments near existing scheduler stats flags:

```python
    parser.add_argument("--scheduler-report", action="store_true", help="Print or export scheduler reporting data, then stop")
    parser.add_argument("--summary", action="store_true", help="Scheduler report: print summary instead of row export")
    parser.add_argument("--from", dest="from_dt", default=None, help="Scheduler report start timestamp, ISO-8601")
    parser.add_argument("--to", dest="to_dt", default=None, help="Scheduler report end timestamp, ISO-8601")
    parser.add_argument("--job", default=None, help="Scheduler report job or schedule name filter")
    parser.add_argument("--status", default=None, help="Scheduler report status filter")
    parser.add_argument("--exit-code", type=int, default=None, help="Scheduler report exit code filter")
    parser.add_argument("--format", choices=["text", "json", "csv"], default="text", help="Output format")
    parser.add_argument("--report-output", default=None, help="Write scheduler report output to file")
```

The existing CLI already uses the generic output flag for text/json command output, so use `--report-output` for scheduler report file writes.

- [ ] **Step 4: Add CLI report helpers**

In `etl_framework/runner/cli.py`, add:

```python
def _parse_report_dt(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _scheduler_report_exit_code(args) -> int:
    from api.services.scheduler_reporting import SchedulerReportFilters, SchedulerReportingService

    session_factory = _gate_session_factory or _default_gate_session_factory()
    db = session_factory()
    try:
        filters = SchedulerReportFilters(
            from_dt=_parse_report_dt(args.from_dt),
            to_dt=_parse_report_dt(args.to_dt),
            days=args.days,
            job=args.job,
            status=args.status,
            exit_code=args.exit_code,
        )
        service = SchedulerReportingService(db)
        if args.format == "json":
            payload = service.summary(filters) if args.summary else {"rows": service.export_rows(filters)}
            output = json.dumps(payload, default=str)
        elif args.format == "csv":
            output = service.export_csv(filters)
        else:
            summary = service.summary(filters)
            counts = summary["summary"]
            output = (
                "Scheduler Report\n"
                f"Window days: {summary['filters']['days']}\n"
                f"Events: {counts['total_events']}\n"
                f"Outcomes: passed={counts['passed']} failed={counts['failed']} "
                f"error={counts['error']} cancelled={counts['cancelled']} blocked={counts['blocked']}\n"
                f"Success rate: {counts['success_rate']}\n"
            )
        if args.report_output:
            Path(args.report_output).write_text(output, encoding="utf-8")
        else:
            print(output)
        return 0
    except Exception as exc:
        if args.format == "json":
            print(json.dumps({"error": str(exc), "exit_code": 1}))
        else:
            print(f"ERROR scheduler report: {exc}")
        return 1
    finally:
        db.close()
```

- [ ] **Step 5: Wire CLI early exit**

After existing validation of `args.days`, add:

```python
    if args.scheduler_report:
        return _scheduler_report_exit_code(args)
```

Update the config-required validation so `--scheduler-report` is exempt:

```python
    if not args.gate_run and not args.scheduler_stats and not args.scheduler_report and (not args.config or not args.source_env or not args.target_env):
```

- [ ] **Step 6: Run CLI tests**

```powershell
python -m pytest tests/unit/test_scheduler_report_cli.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit CLI reporting**

```powershell
git add etl_framework/runner/cli.py tests/unit/test_scheduler_report_cli.py
git commit -m "feat(cli): add scheduler report command"
```

---

### Task 6: Dashboard Feature Slice And Tab Markup

**Files:**
- Create: `frontend/features/scheduler-reports.js`
- Create: `frontend/partials/tab-scheduler-reports.html`
- Modify: `frontend/app.js`
- Modify: `frontend/index.template.html`
- Modify: `frontend/index.html`
- Modify: `frontend/styles.css`
- Test: `tests/integration/test_api_frontend_smoke.py`

**Interfaces:**
- Consumes: `/api/scheduler-reports/summary`, `/grid`, `/timeline`, `/metrics`, `/export`.
- Consumes: existing `/api/schedules` and `/api/schedules/{id}/run-now` routes for management.
- Produces: Alpine slice `ETL_FEATURE_SCHEDULER_REPORTS()`.
- Produces: tab id `scheduler-reports`.

- [ ] **Step 1: Add frontend smoke test for nav and tab content**

Modify `tests/integration/test_api_frontend_smoke.py` by adding:

```python
def test_scheduler_reports_tab_is_present_in_frontend(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "Scheduler Reports" in html
    assert "data-testid=\"nav-tab-scheduler-reports\"" in html or "nav-tab-' + tab.id" in html
    assert "data-testid=\"scheduler-reports-tab\"" in html
```

If the existing smoke fixture does not expose `client`, follow the fixture pattern already in that file and add the assertions to the existing frontend HTML smoke test.

- [ ] **Step 2: Run smoke test to verify it fails**

```powershell
python -m pytest tests/integration/test_api_frontend_smoke.py -v
```

Expected: FAIL because the Scheduler Reports tab is not present.

- [ ] **Step 3: Create scheduler reports feature slice**

Create `frontend/features/scheduler-reports.js`:

```javascript
(function (global) {
  'use strict';
  global.ETL_FEATURE_SCHEDULER_REPORTS = function () {
    return {
      schedulerReportFilters: { days: 30, job: '', status: '', exitCode: '' },
      schedulerReportSummary: null,
      schedulerReportGrid: [],
      schedulerReportTimeline: [],
      schedulerReportMetrics: null,
      schedulerReportWarnings: [],
      schedulerReportLoading: false,
      schedulerReportError: '',
      schedulerReportPollTimer: null,
      schedulerReportEditing: null,

      schedulerReportParams() {
        const params = new URLSearchParams();
        params.set('days', String(this.schedulerReportFilters.days || 30));
        if (this.schedulerReportFilters.job) params.set('job', this.schedulerReportFilters.job);
        if (this.schedulerReportFilters.status) params.set('status', this.schedulerReportFilters.status);
        if (this.schedulerReportFilters.exitCode !== '') params.set('exit_code', String(this.schedulerReportFilters.exitCode));
        return params.toString();
      },

      async loadSchedulerReports() {
        this.schedulerReportLoading = true;
        this.schedulerReportError = '';
        const qs = this.schedulerReportParams();
        try {
          const [summary, grid, timeline, metrics] = await Promise.all([
            api('GET', `/api/scheduler-reports/summary?${qs}`),
            api('GET', `/api/scheduler-reports/grid?${qs}`),
            api('GET', `/api/scheduler-reports/timeline?${qs}`),
            api('GET', `/api/scheduler-reports/metrics?${qs}`),
          ]);
          this.schedulerReportSummary = summary;
          this.schedulerReportGrid = grid.rows || [];
          this.schedulerReportTimeline = timeline.segments || [];
          this.schedulerReportMetrics = metrics;
          this.schedulerReportWarnings = [...(summary.warnings || []), ...(grid.warnings || [])].filter((v, i, a) => a.indexOf(v) === i);
          this.$nextTick(() => this.renderSchedulerReportCharts());
        } catch (e) {
          this.schedulerReportError = e.message || 'Failed to load scheduler reports';
          this.toast('error', 'Scheduler reports unavailable', this.schedulerReportError);
        } finally {
          this.schedulerReportLoading = false;
        }
      },

      startSchedulerReportPolling() {
        this.stopSchedulerReportPolling();
        this.loadSchedulerReports();
        this.schedulerReportPollTimer = setInterval(() => {
          if (this.currentView === 'scheduler-reports') this.loadSchedulerReports();
        }, 15000);
      },

      stopSchedulerReportPolling() {
        if (this.schedulerReportPollTimer) clearInterval(this.schedulerReportPollTimer);
        this.schedulerReportPollTimer = null;
      },

      schedulerStatusClass(status) {
        const value = String(status || '').toUpperCase();
        if (['PASSED', 'COMPLETED', 'RUNNING'].includes(value)) return 'status-pill status-pill-success';
        if (value === 'FAILED' || value === 'ERROR') return 'status-pill status-pill-danger';
        if (value === 'CANCELLED' || value === 'BLOCKED') return 'status-pill status-pill-warning';
        return 'status-pill';
      },

      async schedulerReportRunNow(row) {
        await api('POST', `/api/schedules/${row.schedule_id}/run-now`);
        this.toast('success', 'Schedule triggered', row.schedule_name);
        await this.loadSchedulerReports();
      },

      async schedulerReportToggle(row) {
        await api('PUT', `/api/schedules/${row.schedule_id}`, { enabled: !row.enabled });
        this.toast('success', row.enabled ? 'Schedule disabled' : 'Schedule enabled', row.schedule_name);
        await this.loadSchedulerReports();
      },

      schedulerReportEdit(row) {
        this.schedulerReportEditing = { ...row, cron_expr: row.cron_expr || '' };
      },

      async schedulerReportSaveEdit() {
        const edit = this.schedulerReportEditing;
        if (!edit) return;
        await api('PUT', `/api/schedules/${edit.schedule_id}`, { cron_expr: edit.cron_expr, enabled: edit.enabled });
        this.schedulerReportEditing = null;
        this.toast('success', 'Schedule updated', edit.schedule_name);
        await this.loadSchedulerReports();
      },

      async schedulerReportDelete(row) {
        if (!confirm(`Delete schedule ${row.schedule_name}?`)) return;
        await api('DELETE', `/api/schedules/${row.schedule_id}`);
        this.toast('success', 'Schedule deleted', row.schedule_name);
        await this.loadSchedulerReports();
      },

      schedulerReportExportUrl(format) {
        return `/api/scheduler-reports/export?format=${format}&${this.schedulerReportParams()}`;
      },

      renderSchedulerReportCharts() {
        if (!global.Chart || !this.schedulerReportMetrics) return;
        const canvas = document.getElementById('scheduler-report-outcomes-chart');
        if (!canvas) return;
        if (this.schedulerReportOutcomeChart) this.schedulerReportOutcomeChart.destroy();
        const outcomes = this.schedulerReportMetrics.outcomes || [];
        this.schedulerReportOutcomeChart = new Chart(canvas, {
          type: 'doughnut',
          data: {
            labels: outcomes.map(o => o.status),
            datasets: [{ data: outcomes.map(o => o.count), backgroundColor: ['#10b981', '#ef4444', '#f59e0b', '#64748b'] }],
          },
          options: { responsive: true, plugins: { legend: { position: 'bottom' } } },
        });
      },
    };
  };
})(window);
```

- [ ] **Step 4: Create tab partial**

Create `frontend/partials/tab-scheduler-reports.html`:

```html
<div x-show="currentView === 'scheduler-reports'" x-cloak data-testid="scheduler-reports-tab">
  <div class="section-header">
    <div>
      <div class="section-title">Scheduler Reports</div>
      <div class="section-sub">Monitor scheduled jobs, inspect timelines, analyze outcomes, export telemetry, and manage schedules.</div>
    </div>
    <div class="flex gap-2">
      <a class="btn-secondary" :href="schedulerReportExportUrl('json')" download="scheduler-report.json">Export JSON</a>
      <a class="btn-secondary" :href="schedulerReportExportUrl('csv')" download="scheduler-report.csv">Export CSV</a>
      <button class="btn-primary" @click="loadSchedulerReports()" :disabled="schedulerReportLoading">Refresh</button>
    </div>
  </div>

  <div class="card scheduler-report-filters mb-4">
    <select class="field-input field-select" x-model.number="schedulerReportFilters.days" @change="loadSchedulerReports()">
      <option :value="7">Last 7 days</option>
      <option :value="30">Last 30 days</option>
      <option :value="90">Last 90 days</option>
    </select>
    <input class="field-input" x-model.debounce.300ms="schedulerReportFilters.job" @input="loadSchedulerReports()" placeholder="Filter by job or schedule" />
    <select class="field-input field-select" x-model="schedulerReportFilters.status" @change="loadSchedulerReports()">
      <option value="">All statuses</option>
      <option value="PASSED">Passed</option>
      <option value="FAILED">Failed</option>
      <option value="ERROR">Error</option>
      <option value="CANCELLED">Cancelled</option>
      <option value="BLOCKED">Blocked</option>
    </select>
    <input class="field-input" type="number" x-model="schedulerReportFilters.exitCode" @input.debounce.300ms="loadSchedulerReports()" placeholder="Exit code" />
  </div>

  <template x-if="schedulerReportError">
    <div class="alert-error mb-4" x-text="schedulerReportError"></div>
  </template>

  <template x-if="schedulerReportWarnings.length">
    <div class="alert-warn mb-4">
      <template x-for="warning in schedulerReportWarnings" :key="warning"><div x-text="warning"></div></template>
    </div>
  </template>

  <div class="scheduler-report-cards mb-4" x-show="schedulerReportSummary">
    <div class="metric-card metric-slate"><div class="metric-label">Scheduler</div><div class="metric-value" x-text="schedulerReportSummary?.scheduler?.running ? 'Running' : 'Stopped'"></div></div>
    <div class="metric-card metric-emerald"><div class="metric-label">Success Rate</div><div class="metric-value" x-text="(schedulerReportSummary?.summary?.success_rate ?? 'N/A') + (schedulerReportSummary?.summary?.success_rate == null ? '' : '%')"></div></div>
    <div class="metric-card metric-rose"><div class="metric-label">Failures</div><div class="metric-value" x-text="(schedulerReportSummary?.summary?.failed || 0) + (schedulerReportSummary?.summary?.error || 0)"></div></div>
    <div class="metric-card metric-indigo"><div class="metric-label">Query Time</div><div class="metric-value" x-text="(schedulerReportSummary?.performance?.report_query_ms || 0) + ' ms'"></div></div>
  </div>

  <div class="card mb-4">
    <div class="font-semibold mb-3">Live Status Grid</div>
    <div class="table-scroll">
      <table class="data-table">
        <thead><tr><th>Schedule</th><th>Status</th><th>Enabled</th><th>Cron</th><th>Next Run</th><th>Duration</th><th>Exit</th><th>Actions</th></tr></thead>
        <tbody>
          <template x-for="row in schedulerReportGrid" :key="row.schedule_id">
            <tr>
              <td x-text="row.schedule_name"></td>
              <td><span :class="schedulerStatusClass(row.last_status)" x-text="row.last_status || 'No telemetry'"></span></td>
              <td x-text="row.enabled ? 'Yes' : 'No'"></td>
              <td class="font-mono text-xs" x-text="row.cron_expr"></td>
              <td x-text="row.next_run_at || 'Not scheduled'"></td>
              <td x-text="row.last_duration_seconds == null ? 'N/A' : row.last_duration_seconds + 's'"></td>
              <td x-text="row.last_exit_code == null ? 'N/A' : row.last_exit_code"></td>
              <td class="flex gap-1 flex-wrap">
                <button class="btn-secondary btn-sm" @click="schedulerReportRunNow(row)">Run</button>
                <button class="btn-secondary btn-sm" @click="schedulerReportToggle(row)" x-text="row.enabled ? 'Disable' : 'Enable'"></button>
                <button class="btn-secondary btn-sm" @click="schedulerReportEdit(row)">Edit</button>
                <button class="btn-danger btn-sm" @click="schedulerReportDelete(row)">Delete</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>

  <div class="scheduler-report-panels">
    <div class="card">
      <div class="font-semibold mb-3">Execution Timeline</div>
      <div class="scheduler-timeline">
        <template x-for="segment in schedulerReportTimeline" :key="segment.schedule_name + '-' + segment.started_at + '-' + segment.status">
          <div class="scheduler-timeline-row">
            <div class="scheduler-timeline-label" x-text="segment.schedule_name"></div>
            <div class="scheduler-timeline-bar" :class="schedulerStatusClass(segment.status)" :title="segment.status + ' · ' + (segment.duration_seconds || 0) + 's'" x-text="segment.status"></div>
          </div>
        </template>
      </div>
    </div>
    <div class="card">
      <div class="font-semibold mb-3">Outcome Analytics</div>
      <canvas id="scheduler-report-outcomes-chart" height="180"></canvas>
    </div>
  </div>

  <div x-show="schedulerReportEditing" x-cloak class="modal-backdrop" @click.self="schedulerReportEditing = null">
    <div class="modal-box" role="dialog" aria-modal="true">
      <div class="drawer-header"><div class="drawer-title">Edit Schedule</div><button class="drawer-close" @click="schedulerReportEditing = null">x</button></div>
      <label class="field-label">Cron expression</label>
      <input class="field-input mb-3" x-model="schedulerReportEditing.cron_expr" />
      <label class="inline-flex items-center gap-2 mb-4"><input type="checkbox" x-model="schedulerReportEditing.enabled" /> Enabled</label>
      <div class="flex gap-2 justify-end"><button class="btn-secondary" @click="schedulerReportEditing = null">Cancel</button><button class="btn-primary" @click="schedulerReportSaveEdit()">Save</button></div>
    </div>
  </div>
</div>
```

- [ ] **Step 5: Wire navigation and feature merge**

In `frontend/app.js`, add a tab object to `tabs`:

```javascript
      { id: 'scheduler-reports', label: 'Scheduler Reports', group: 'observe',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"></path><path d="M7 14l3-3 3 2 5-7"></path></svg>' },
```

In `onTabEnter(id)`, after `this.currentView = id;`, add:

```javascript
      if (id === 'scheduler-reports') this.startSchedulerReportPolling();
      else this.stopSchedulerReportPolling();
```

In `FEATURE_SLICES`, add `ETL_FEATURE_SCHEDULER_REPORTS()` before `ETL_FEATURE_LOGS()`:

```javascript
ETL_FEATURE_SCHEDULER_REPORTS()
```

- [ ] **Step 6: Add script and partial include**

In `frontend/index.template.html`, add the tab include near Monitor/History or Reports:

```html
<!-- ====================================================================
     TAB - SCHEDULER REPORTS
     ==================================================================== -->
<!-- INCLUDE: partials/tab-scheduler-reports.html -->
```

Add script include for the new feature in the built template area where feature scripts are loaded. If scripts are only in `frontend/index.html`, add there before `app.js`:

```html
<script src="features/scheduler-reports.js"></script>
```

If `index.template.html` owns scripts, add it there and regenerate.

- [ ] **Step 7: Add minimal responsive styles**

Append to `frontend/styles.css`:

```css
.scheduler-report-filters,
.scheduler-report-cards,
.scheduler-report-panels {
  display: grid;
  gap: 1rem;
}

.scheduler-report-filters {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.scheduler-report-cards {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.scheduler-report-panels {
  grid-template-columns: minmax(0, 1.4fr) minmax(280px, .6fr);
}

.scheduler-timeline {
  display: grid;
  gap: .75rem;
  overflow-x: auto;
}

.scheduler-timeline-row {
  display: grid;
  grid-template-columns: 160px minmax(180px, 1fr);
  gap: .75rem;
  align-items: center;
}

.scheduler-timeline-label {
  font-size: .8rem;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scheduler-timeline-bar {
  border-radius: 999px;
  padding: .35rem .75rem;
  font-size: .75rem;
  min-width: 96px;
  text-align: center;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: .2rem .55rem;
  font-size: .75rem;
  background: rgba(100, 116, 139, .14);
  color: var(--text-muted);
}

.status-pill-success { background: rgba(16, 185, 129, .14); color: var(--emerald); }
.status-pill-danger { background: rgba(239, 68, 68, .14); color: var(--rose); }
.status-pill-warning { background: rgba(245, 158, 11, .14); color: var(--amber); }

@media (max-width: 900px) {
  .scheduler-report-filters,
  .scheduler-report-cards,
  .scheduler-report-panels {
    grid-template-columns: 1fr;
  }

  .scheduler-timeline-row {
    grid-template-columns: 120px minmax(180px, 1fr);
  }
}
```

- [ ] **Step 8: Rebuild frontend HTML**

```powershell
npm run build:html
```

Expected: `Built C:\atom\frontend\index.html from ...` with include count matching partial count.

- [ ] **Step 9: Run frontend smoke test**

```powershell
python -m pytest tests/integration/test_api_frontend_smoke.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit dashboard tab**

```powershell
git add frontend/features/scheduler-reports.js frontend/partials/tab-scheduler-reports.html frontend/app.js frontend/index.template.html frontend/index.html frontend/styles.css tests/integration/test_api_frontend_smoke.py
git commit -m "feat(frontend): add scheduler reports dashboard"
```

---

### Task 7: End-To-End Verification And Documentation Update

**Files:**
- Modify: `README.md`
- Test: relevant backend, API, CLI, frontend tests from previous tasks

**Interfaces:**
- Produces: user documentation for dashboard and CLI usage.
- Confirms: no scheduler regression.

- [ ] **Step 1: Add README feature notes**

In `README.md`, add a short bullet near the scheduler/statistics features:

```markdown
- **Scheduler Reporting System** — use the Scheduler Reports dashboard tab for live status, Gantt-style execution timelines, success/runtime analytics, JSON/CSV exports, and schedule management actions. Use `python -m etl_framework.runner.cli --scheduler-report --summary` for low-overhead terminal health checks without launching the web server.
```

Add CLI examples near existing CLI documentation:

```markdown
### Scheduler Reports CLI

```powershell
python -m etl_framework.runner.cli --scheduler-report --summary --days 30
python -m etl_framework.runner.cli --scheduler-report --status failed --exit-code 1 --format json --days 30
python -m etl_framework.runner.cli --scheduler-report --format csv --report-output scheduler-report.csv --days 30
```
```

- [ ] **Step 2: Run focused backend tests**

```powershell
python -m pytest tests/unit/test_scheduler_telemetry_repository.py tests/unit/test_scheduler_reporting_service.py tests/unit/test_scheduler_telemetry_listener.py tests/unit/test_scheduler_reports_routes.py tests/unit/test_scheduler_report_cli.py tests/unit/test_scheduler.py tests/unit/test_scheduler_stats.py -v
```

Expected: PASS.

- [ ] **Step 3: Run frontend build and smoke**

```powershell
npm run build:html
python -m pytest tests/integration/test_api_frontend_smoke.py -v
```

Expected: both PASS.

- [ ] **Step 4: Run broader scheduler/API regression slice**

```powershell
python -m pytest tests/unit/test_api.py tests/unit/test_run_executor.py tests/unit/test_run_executor_gates.py tests/unit/test_schedules_selection_refactor.py -v
```

Expected: PASS.

- [ ] **Step 5: Inspect working tree and diff**

```powershell
git status --short
git diff -- README.md
```

Expected: only intended README changes are uncommitted at this point.

- [ ] **Step 6: Commit docs and verification update**

```powershell
git add README.md
git commit -m "docs: document scheduler reporting"
```

---

## Final Verification

After all tasks are complete, run:

```powershell
python -m pytest tests/unit/test_scheduler_telemetry_repository.py tests/unit/test_scheduler_reporting_service.py tests/unit/test_scheduler_telemetry_listener.py tests/unit/test_scheduler_reports_routes.py tests/unit/test_scheduler_report_cli.py tests/unit/test_scheduler.py tests/unit/test_scheduler_stats.py tests/unit/test_api.py tests/unit/test_run_executor.py tests/unit/test_run_executor_gates.py tests/unit/test_schedules_selection_refactor.py tests/integration/test_api_frontend_smoke.py -v
npm run build:html
```

Expected:

- All listed pytest tests pass.
- `npm run build:html` succeeds and regenerates `frontend/index.html` from template and partials.
- `git status --short` shows no unintended files staged or modified except pre-existing unrelated workspace dirt.

## Plan Self-Review

- Spec coverage: telemetry storage, listener isolation, shared service, API, CLI, dashboard, management actions, exports, 30-day retention, error warnings, responsive UI, and regression testing are each covered by a task.
- Placeholder scan: the plan contains no unresolved placeholder phrases or inconsistent interface names.
- Type consistency: `SchedulerReportFilters`, `SchedulerReportingService`, `SchedulerTelemetryRepository`, and route/CLI consumers use consistent names and payload shapes across tasks.
