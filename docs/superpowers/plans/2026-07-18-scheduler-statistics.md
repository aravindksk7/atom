# Scheduler Statistics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build scheduler statistics reporting in the web UI, HTTP API, CLI, and CI/CD gate mode.

**Architecture:** Add a focused stats service that combines `scheduled_runs`, recent scheduled `test_runs`, and a live APScheduler runtime snapshot. The API, CLI, and Alpine UI all consume the same stats shape so report and gate behavior stays consistent.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, APScheduler, Alpine.js, Tailwind, pytest.

## Global Constraints

- Default execution-history window is 30 days.
- Accepted `days` range is `1..365`.
- Default CLI mode is report-only and exits `0` when stats are computed successfully.
- CI gate behavior is opt-in through `--fail-on-stopped` and `--min-success-rate`.
- Do not add a new scheduler event table in this version.
- Keep the existing Tailwind/Alpine visual language.
- Do not commit unless the user explicitly requests it.

---

## File Structure

- Create `api/services/scheduler_stats.py`: pure aggregation and gate-evaluation logic shared by API and CLI.
- Modify `api/services/scheduler.py`: add read-only runtime snapshot helpers without changing scheduling behavior.
- Modify `api/routes/schedules.py`: add `GET /api/schedules/stats` before dynamic schedule-id routes.
- Modify `etl_framework/runner/cli.py`: add `--scheduler-stats`, `--days`, `--fail-on-stopped`, and `--min-success-rate` flags.
- Modify `frontend/features/launch.js`: add stats state, load/refresh method, and formatting helpers.
- Modify `frontend/index.html` and `frontend/partials/tab-launch.html`: render scheduler stats summary and per-schedule details.
- Modify or create tests under `tests/unit/test_scheduler.py`, `tests/unit/test_scheduler_stats.py`, and `tests/unit/test_runner_cli.py`.

---

### Task 1: Runtime Snapshot and Stats Service

**Files:**
- Create: `api/services/scheduler_stats.py`
- Modify: `api/services/scheduler.py`
- Test: `tests/unit/test_scheduler_stats.py`

**Interfaces:**
- Consumes: `etl_framework.repository.models.ScheduledRun`, `etl_framework.repository.models.TestRun`, SQLAlchemy `Session`.
- Produces: `api.services.scheduler.get_scheduler_runtime_snapshot() -> dict`.
- Produces: `api.services.scheduler_stats.GateOptions` dataclass.
- Produces: `api.services.scheduler_stats.build_scheduler_stats(db: Session, days: int = 30, now: datetime | None = None, runtime_snapshot: dict | None = None, gate_options: GateOptions | None = None) -> dict`.

- [ ] **Step 1: Write failing stats service tests**

Create `tests/unit/test_scheduler_stats.py`:

```python
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
    db.add(TestRun(
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
    ))
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_scheduler_stats.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'api.services.scheduler_stats'` or missing exported names.

- [ ] **Step 3: Add runtime snapshot helper**

Modify `api/services/scheduler.py` after `_job_id`:

```python
def _parse_schedule_id_from_job_id(job_id: str) -> int | None:
    prefix = "etl_schedule_"
    if not job_id.startswith(prefix):
        return None
    try:
        return int(job_id[len(prefix):])
    except ValueError:
        return None


def _iso_or_none(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def get_scheduler_runtime_snapshot() -> dict:
    """Return read-only APScheduler state for status and statistics reports."""
    if not _APSCHEDULER_AVAILABLE:
        return {
            "available": False,
            "running": False,
            "job_count": 0,
            "timezone": "UTC",
            "jobs": {},
        }
    if _scheduler is None:
        return {
            "available": True,
            "running": False,
            "job_count": 0,
            "timezone": "UTC",
            "jobs": {},
        }
    jobs = {}
    for job in _scheduler.get_jobs():
        schedule_id = _parse_schedule_id_from_job_id(job.id)
        if schedule_id is None:
            continue
        jobs[schedule_id] = {
            "job_id": job.id,
            "next_run_at": _iso_or_none(job.next_run_time),
        }
    timezone_value = getattr(_scheduler, "timezone", None)
    return {
        "available": True,
        "running": bool(getattr(_scheduler, "running", False)),
        "job_count": len(jobs),
        "timezone": str(timezone_value or "UTC"),
        "jobs": jobs,
    }
```

- [ ] **Step 4: Add stats service implementation**

Create `api/services/scheduler_stats.py`:

```python
"""Scheduler statistics aggregation for API, UI, CLI, and CI gates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from api.services.scheduler import get_scheduler_runtime_snapshot
from etl_framework.repository.models import ScheduledRun, TestRun


OUTCOME_KEYS = ("passed", "failed", "error", "cancelled", "blocked")


@dataclass(frozen=True)
class GateOptions:
    fail_on_stopped: bool = False
    min_success_rate: float | None = None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    value = _utc(value)
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _duration_seconds(run: TestRun) -> float | None:
    started = _utc(run.started_at)
    completed = _utc(run.completed_at)
    if started is None or completed is None:
        return None
    seconds = (completed - started).total_seconds()
    return seconds if seconds >= 0 else None


def _empty_counts() -> dict[str, Any]:
    return {
        "runs_triggered": 0,
        "passed": 0,
        "failed": 0,
        "error": 0,
        "cancelled": 0,
        "blocked": 0,
        "durations": [],
        "last_run_at": None,
        "last_status": None,
    }


def _status_key(status: str | None) -> str | None:
    normalized = (status or "").upper()
    if normalized in {"PASSED", "COMPLETED", "SLOW"}:
        return "passed"
    if normalized == "FAILED":
        return "failed"
    if normalized == "ERROR":
        return "error"
    if normalized == "CANCELLED":
        return "cancelled"
    if normalized == "BLOCKED":
        return "blocked"
    return None


def _success_rate(counts: dict[str, Any]) -> float | None:
    total = counts["runs_triggered"]
    if total == 0:
        return None
    return round((counts["passed"] / total) * 100, 2)


def _average_duration(counts: dict[str, Any]) -> float | None:
    durations = counts["durations"]
    if not durations:
        return None
    return round(sum(durations) / len(durations), 2)


def _public_counts(counts: dict[str, Any]) -> dict[str, Any]:
    return {
        "runs_triggered": counts["runs_triggered"],
        "passed": counts["passed"],
        "failed": counts["failed"],
        "error": counts["error"],
        "cancelled": counts["cancelled"],
        "blocked": counts["blocked"],
        "success_rate": _success_rate(counts),
        "average_duration_seconds": _average_duration(counts),
    }


def _schedule_key(schedule: ScheduledRun) -> int:
    return schedule.selection_id or schedule.id


def _evaluate_gate(summary: dict[str, Any], scheduler: dict[str, Any], options: GateOptions | None) -> dict[str, Any]:
    reasons: list[str] = []
    if options and options.fail_on_stopped and not scheduler.get("running"):
        reasons.append("scheduler is not running")
    if options and options.min_success_rate is not None:
        rate = summary.get("success_rate")
        if rate is None:
            reasons.append(f"success rate is unavailable, below {options.min_success_rate}")
        elif rate < options.min_success_rate:
            reasons.append(f"success rate {rate} is below {options.min_success_rate}")
    return {
        "status": "failed" if reasons else "passed",
        "exit_code": 1 if reasons else 0,
        "reasons": reasons,
    }


def build_scheduler_stats(
    db: Session,
    days: int = 30,
    now: datetime | None = None,
    runtime_snapshot: dict | None = None,
    gate_options: GateOptions | None = None,
) -> dict[str, Any]:
    if days < 1 or days > 365:
        raise ValueError("days must be between 1 and 365")
    generated_at = _utc(now) or datetime.now(timezone.utc)
    since = generated_at - timedelta(days=days)
    scheduler = runtime_snapshot or get_scheduler_runtime_snapshot()
    scheduler.setdefault("jobs", {})

    schedules = db.query(ScheduledRun).order_by(ScheduledRun.name).all()
    counts_by_key = {_schedule_key(schedule): _empty_counts() for schedule in schedules}
    runs = (
        db.query(TestRun)
        .filter(TestRun.run_type == "scheduled")
        .filter(TestRun.started_at >= since)
        .all()
    )
    for run in runs:
        key = run.selection_id
        if key not in counts_by_key:
            continue
        counts = counts_by_key[key]
        counts["runs_triggered"] += 1
        outcome = _status_key(run.status)
        if outcome:
            counts[outcome] += 1
        duration = _duration_seconds(run)
        if duration is not None:
            counts["durations"].append(duration)
        started = _utc(run.started_at)
        last = _utc(counts["last_run_at"])
        if started is not None and (last is None or started > last):
            counts["last_run_at"] = started
            counts["last_status"] = run.status

    summary_counts = _empty_counts()
    schedule_payloads = []
    runtime_jobs = scheduler.get("jobs", {})
    for schedule in schedules:
        counts = counts_by_key[_schedule_key(schedule)]
        public = _public_counts(counts)
        runtime_job = runtime_jobs.get(schedule.id) or runtime_jobs.get(str(schedule.id)) or {}
        registered = bool(runtime_job) if schedule.enabled else False
        for key in ("runs_triggered", *OUTCOME_KEYS):
            summary_counts[key] += public[key]
        summary_counts["durations"].extend(counts["durations"])
        schedule_payloads.append({
            "id": schedule.id,
            "name": schedule.name,
            "enabled": schedule.enabled,
            "cron_expr": schedule.cron_expr,
            "registered": registered,
            "next_run_at": runtime_job.get("next_run_at") or _iso(schedule.next_run_at),
            "last_run_at": _iso(counts["last_run_at"] or schedule.last_run_at),
            "last_status": counts["last_status"],
            **public,
        })

    summary = {
        "total_schedules": len(schedules),
        "enabled_schedules": sum(1 for schedule in schedules if schedule.enabled),
        "disabled_schedules": sum(1 for schedule in schedules if not schedule.enabled),
        **_public_counts(summary_counts),
    }
    return {
        "window_days": days,
        "generated_at": _iso(generated_at),
        "scheduler": {
            "available": bool(scheduler.get("available")),
            "running": bool(scheduler.get("running")),
            "job_count": int(scheduler.get("job_count") or 0),
            "timezone": scheduler.get("timezone") or "UTC",
        },
        "summary": summary,
        "schedules": schedule_payloads,
        "gate": _evaluate_gate(summary, scheduler, gate_options),
    }
```

- [ ] **Step 5: Run stats service tests**

Run: `python -m pytest tests/unit/test_scheduler_stats.py -v`

Expected: PASS for all tests in `tests/unit/test_scheduler_stats.py`.

---

### Task 2: HTTP API Endpoint

**Files:**
- Modify: `api/routes/schedules.py`
- Test: `tests/unit/test_scheduler.py`

**Interfaces:**
- Consumes: `build_scheduler_stats(db: Session, days: int = 30) -> dict` from Task 1.
- Produces: `GET /api/schedules/stats?days=30` endpoint returning the stats payload.

- [ ] **Step 1: Add failing route tests**

Append to `tests/unit/test_scheduler.py`:

```python
def test_schedule_stats_route_returns_payload(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from api.dependencies import get_session

    db = _session()

    def override_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get("/api/schedules/stats?days=30")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["window_days"] == 30
    assert "scheduler" in body
    assert "summary" in body
    assert "schedules" in body


def test_schedule_stats_route_validates_days():
    from fastapi.testclient import TestClient
    from api.main import app

    client = TestClient(app)
    response = client.get("/api/schedules/stats?days=0")

    assert response.status_code == 422
```

- [ ] **Step 2: Run route tests to verify they fail**

Run: `python -m pytest tests/unit/test_scheduler.py::test_schedule_stats_route_returns_payload tests/unit/test_scheduler.py::test_schedule_stats_route_validates_days -v`

Expected: FAIL with a 404 for `/api/schedules/stats`.

- [ ] **Step 3: Add API route**

Modify imports in `api/routes/schedules.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
```

Add after `_validate_selection` and before `@router.get("", response_model=list[ScheduleOut])`:

```python
@router.get("/stats")
def scheduler_stats(
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    db: Session = Depends(get_session),
):
    from api.services.scheduler_stats import build_scheduler_stats

    return build_scheduler_stats(db, days=days)
```

- [ ] **Step 4: Run route tests**

Run: `python -m pytest tests/unit/test_scheduler.py::test_schedule_stats_route_returns_payload tests/unit/test_scheduler.py::test_schedule_stats_route_validates_days -v`

Expected: PASS for both route tests.

---

### Task 3: CLI Report and Gate Mode

**Files:**
- Modify: `etl_framework/runner/cli.py`
- Test: `tests/unit/test_runner_cli.py`

**Interfaces:**
- Consumes: `GateOptions` and `build_scheduler_stats` from Task 1.
- Produces: CLI flags `--scheduler-stats`, `--days`, `--fail-on-stopped`, `--min-success-rate`.

- [ ] **Step 1: Add failing CLI tests**

If `tests/unit/test_runner_cli.py` does not exist, create it. Add:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_scheduler_stats_cli_json_report_exits_zero(monkeypatch, capsys):
    from etl_framework.runner import cli

    factory = _session_factory()
    monkeypatch.setattr(cli, "_stats_session_factory", factory)

    code = cli.main(["--scheduler-stats", "--output", "json"])

    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["window_days"] == 30
    assert body["gate"]["exit_code"] == 0


def test_scheduler_stats_cli_gate_returns_nonzero(monkeypatch, capsys):
    from etl_framework.runner import cli

    factory = _session_factory()
    monkeypatch.setattr(cli, "_stats_session_factory", factory)
    monkeypatch.setattr(
        "api.services.scheduler_stats.get_scheduler_runtime_snapshot",
        lambda: {"available": True, "running": False, "job_count": 0, "timezone": "UTC", "jobs": {}},
    )

    code = cli.main(["--scheduler-stats", "--fail-on-stopped", "--output", "json"])

    assert code == 1
    body = json.loads(capsys.readouterr().out)
    assert body["gate"]["status"] == "failed"
```

- [ ] **Step 2: Run CLI tests to verify they fail**

Run: `python -m pytest tests/unit/test_runner_cli.py -v`

Expected: FAIL because `_stats_session_factory` or `--scheduler-stats` does not exist.

- [ ] **Step 3: Add CLI stats helpers and flags**

Modify `etl_framework/runner/cli.py`.

Add after `_gate_session_factory = None`:

```python
_stats_session_factory = None  # test seam; resolved lazily in _scheduler_stats_exit_code
```

Add after `_gate_exit_code`:

```python
def _default_stats_session_factory():
    from etl_framework.repository.database import SessionLocal, init_db
    init_db()
    return SessionLocal


def _print_scheduler_stats_text(stats: dict) -> None:
    summary = stats["summary"]
    scheduler = stats["scheduler"]
    gate = stats["gate"]
    state = "running" if scheduler["running"] else "stopped"
    if not scheduler["available"]:
        state = "unavailable"
    print(f"Scheduler: {state} jobs={scheduler['job_count']} timezone={scheduler['timezone']}")
    print(
        f"Window: {stats['window_days']} days schedules={summary['total_schedules']} "
        f"enabled={summary['enabled_schedules']} runs={summary['runs_triggered']}"
    )
    print(
        f"Outcomes: passed={summary['passed']} failed={summary['failed']} "
        f"error={summary['error']} cancelled={summary['cancelled']} blocked={summary['blocked']}"
    )
    print(
        f"Success rate: {summary['success_rate'] if summary['success_rate'] is not None else 'n/a'} "
        f"avg_duration_seconds={summary['average_duration_seconds'] if summary['average_duration_seconds'] is not None else 'n/a'}"
    )
    print(f"Gate: {gate['status']} exit={gate['exit_code']}")
    for reason in gate["reasons"]:
        print(f"- {reason}")


def _scheduler_stats_exit_code(args) -> int:
    from api.services.scheduler_stats import GateOptions, build_scheduler_stats

    factory = _stats_session_factory or _default_stats_session_factory()
    session = factory()
    try:
        stats = build_scheduler_stats(
            session,
            days=args.days,
            gate_options=GateOptions(
                fail_on_stopped=args.fail_on_stopped,
                min_success_rate=args.min_success_rate,
            ),
        )
        if args.output == "json":
            print(json.dumps(stats, default=str))
        else:
            _print_scheduler_stats_text(stats)
        return int(stats["gate"]["exit_code"])
    except Exception as exc:
        if args.output == "json":
            print(json.dumps({"error": str(exc), "exit_code": 1}))
        else:
            print(f"ERROR scheduler stats: {exc}")
        return 1
    finally:
        session.close()
```

Add parser arguments before `return parser`:

```python
    parser.add_argument("--scheduler-stats", action="store_true", help="Report scheduler execution and runtime statistics, then stop")
    parser.add_argument("--days", type=int, default=30, help="Scheduler stats lookback window in days, 1..365")
    parser.add_argument("--fail-on-stopped", action="store_true", help="Scheduler stats gate: fail when scheduler is unavailable or stopped")
    parser.add_argument("--min-success-rate", type=float, default=None, help="Scheduler stats gate: fail when aggregate success rate is below this percentage")
```

Add in `main()` after the `--gate-run` block:

```python
    if args.scheduler_stats:
        if args.days < 1 or args.days > 365:
            parser.error("--days must be between 1 and 365")
        if args.min_success_rate is not None and (args.min_success_rate < 0 or args.min_success_rate > 100):
            parser.error("--min-success-rate must be between 0 and 100")
        return _scheduler_stats_exit_code(args)
```

- [ ] **Step 4: Run CLI tests**

Run: `python -m pytest tests/unit/test_runner_cli.py -v`

Expected: PASS for scheduler stats CLI tests.

---

### Task 4: Web UI State and Rendering

**Files:**
- Modify: `frontend/features/launch.js`
- Modify: `frontend/index.html`
- Modify: `frontend/partials/tab-launch.html`

**Interfaces:**
- Consumes: `GET /api/schedules/stats?days=30` from Task 2.
- Produces: visible Scheduler Statistics summary in the Schedules sub-tab.

- [ ] **Step 1: Add UI state and methods**

Modify `frontend/features/launch.js` near the schedule state block:

```javascript
    schedulerStats: null,
    schedulerStatsLoading: false,
    schedulerStatsError: '',
```

Replace `loadSchedules()` with:

```javascript
    async loadSchedules() {
      try { this.schedules = await api('GET', '/api/schedules'); } catch {}
      await this.loadSchedulerStats();
    },

    async loadSchedulerStats() {
      this.schedulerStatsLoading = true;
      this.schedulerStatsError = '';
      try {
        this.schedulerStats = await api('GET', '/api/schedules/stats?days=30');
      } catch (e) {
        this.schedulerStatsError = e.message || 'Unable to load scheduler statistics';
      } finally {
        this.schedulerStatsLoading = false;
      }
    },

    formatSchedulerPercent(value) {
      return value === null || value === undefined ? 'n/a' : `${Number(value).toFixed(2)}%`;
    },

    formatSchedulerDuration(value) {
      if (value === null || value === undefined) return 'n/a';
      if (value < 60) return `${Number(value).toFixed(1)}s`;
      return `${(Number(value) / 60).toFixed(1)}m`;
    },

    schedulerStateLabel() {
      const scheduler = this.schedulerStats?.scheduler;
      if (!scheduler) return 'Loading';
      if (!scheduler.available) return 'Unavailable';
      return scheduler.running ? 'Running' : 'Stopped';
    },
```

In `saveSchedule()`, `deleteSchedule()`, and `runScheduleNow()`, keep existing `await this.loadSchedules();` or add `await this.loadSchedulerStats();` after manual trigger so stats refreshes. For `runScheduleNow()`, replace `setTimeout(() => this.loadRuns(), 1000);` with:

```javascript
        setTimeout(() => { this.loadRuns(); this.loadSchedulerStats(); }, 1000);
```

- [ ] **Step 2: Add HTML summary block to both schedule templates**

In both `frontend/index.html` and `frontend/partials/tab-launch.html`, find `<!-- ── Schedules sub-tab ── -->` and insert this block immediately inside `<div x-show="launchSubTab === 'schedules'">` before the heading row:

```html
    <div class="section-card mb-4">
      <div class="flex items-start justify-between gap-4 mb-3">
        <div>
          <h3 class="text-base font-semibold text-slate-900">Scheduler Statistics</h3>
          <p class="text-sm text-muted">Execution and runtime health for the last 30 days.</p>
        </div>
        <button @click="loadSchedulerStats()" class="btn-secondary btn-sm" :disabled="schedulerStatsLoading">Refresh</button>
      </div>
      <template x-if="schedulerStatsError">
        <div class="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2" x-text="schedulerStatsError"></div>
      </template>
      <template x-if="schedulerStats">
        <div>
          <div class="grid grid-cols-1 md:grid-cols-5 gap-3 mb-4">
            <div class="bg-slate-50 border border-slate-200 rounded-xl p-3">
              <div class="text-xs text-muted uppercase tracking-wide">Scheduler</div>
              <div class="text-lg font-semibold" x-text="schedulerStateLabel()"></div>
            </div>
            <div class="bg-slate-50 border border-slate-200 rounded-xl p-3">
              <div class="text-xs text-muted uppercase tracking-wide">Enabled</div>
              <div class="text-lg font-semibold" x-text="schedulerStats.summary.enabled_schedules"></div>
            </div>
            <div class="bg-slate-50 border border-slate-200 rounded-xl p-3">
              <div class="text-xs text-muted uppercase tracking-wide">Runs</div>
              <div class="text-lg font-semibold" x-text="schedulerStats.summary.runs_triggered"></div>
            </div>
            <div class="bg-slate-50 border border-slate-200 rounded-xl p-3">
              <div class="text-xs text-muted uppercase tracking-wide">Success Rate</div>
              <div class="text-lg font-semibold" x-text="formatSchedulerPercent(schedulerStats.summary.success_rate)"></div>
            </div>
            <div class="bg-slate-50 border border-slate-200 rounded-xl p-3">
              <div class="text-xs text-muted uppercase tracking-wide">Avg Duration</div>
              <div class="text-lg font-semibold" x-text="formatSchedulerDuration(schedulerStats.summary.average_duration_seconds)"></div>
            </div>
          </div>
          <div class="text-xs text-muted" x-text="`Generated ${formatDateTime(schedulerStats.generated_at)} | APScheduler jobs ${schedulerStats.scheduler.job_count}`"></div>
        </div>
      </template>
    </div>
```

- [ ] **Step 3: Add per-schedule indicators to both schedule card templates**

In both `frontend/index.html` and `frontend/partials/tab-launch.html`, inside the schedule card body after the existing cron/source/target text and before the action buttons, add:

```html
            <template x-if="schedulerStats">
              <div class="mt-3 grid grid-cols-1 md:grid-cols-4 gap-2 text-xs text-slate-600">
                <template x-for="stat in schedulerStats.schedules.filter(item => item.id === sched.id)" :key="stat.id">
                  <div class="contents">
                    <div class="bg-white border border-slate-200 rounded-lg px-2 py-1">Next: <span x-text="stat.next_run_at ? formatDateTime(stat.next_run_at) : 'n/a'"></span></div>
                    <div class="bg-white border border-slate-200 rounded-lg px-2 py-1">Last: <span x-text="stat.last_status || 'No recent runs'"></span></div>
                    <div class="bg-white border border-slate-200 rounded-lg px-2 py-1">Rate: <span x-text="formatSchedulerPercent(stat.success_rate)"></span></div>
                    <div class="bg-white border border-slate-200 rounded-lg px-2 py-1">Registered: <span x-text="stat.registered ? 'yes' : 'no'"></span></div>
                  </div>
                </template>
              </div>
            </template>
```

- [ ] **Step 4: Run lightweight verification**

Run: `npm run build:html`

Expected: command exits `0` and regenerates HTML without template errors.

---

### Task 5: Final Verification and Documentation Notes

**Files:**
- Modify: `README.md` only if it has an existing scheduler/CLI feature list location that should mention the new command.

**Interfaces:**
- Consumes: completed Tasks 1-4.
- Produces: verified scheduler statistics feature across API, CLI, and web build.

- [ ] **Step 1: Run focused unit tests**

Run: `python -m pytest tests/unit/test_scheduler_stats.py tests/unit/test_scheduler.py tests/unit/test_runner_cli.py -v`

Expected: PASS for all scheduler stats, route, and CLI tests.

- [ ] **Step 2: Run frontend build check**

Run: `npm run build:html`

Expected: exits `0`.

- [ ] **Step 3: Run CLI smoke checks**

Run: `python -m etl_framework.runner.cli --scheduler-stats --output json`

Expected: exits `0` and prints JSON containing `window_days`, `scheduler`, `summary`, `schedules`, and `gate`.

Run: `python -m etl_framework.runner.cli --scheduler-stats --days 30 --output text`

Expected: exits `0` and prints text lines starting with `Scheduler:`, `Window:`, `Outcomes:`, `Success rate:`, and `Gate:`.

- [ ] **Step 4: Inspect git diff**

Run: `git diff -- api/services/scheduler.py api/services/scheduler_stats.py api/routes/schedules.py etl_framework/runner/cli.py frontend/features/launch.js frontend/index.html frontend/partials/tab-launch.html tests/unit/test_scheduler_stats.py tests/unit/test_scheduler.py tests/unit/test_runner_cli.py docs/superpowers/specs/2026-07-18-scheduler-statistics-design.md docs/superpowers/plans/2026-07-18-scheduler-statistics.md`

Expected: diff only contains scheduler statistics feature work and the approved spec/plan files.
