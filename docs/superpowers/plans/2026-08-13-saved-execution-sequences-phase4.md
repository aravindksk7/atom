# Saved Execution Sequences — Phase 4 Implementation Plan (Preconditions)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a saved sequence declare gates that decide whether it should run at all — a time window, allowed weekdays, and a recent successful run of a named job — and enforce them before a run is created.

**Architecture:** One `sequence_preconditions.check()` function, called by the two things that start a sequence: selection launch and the scheduler. It takes an injected `now` and timezone so every rule is testable without freezing the clock. A failed gate means **no run row at all** — launch returns 422 naming the gate, the scheduler records its existing `skipped` telemetry event. `DagExecutor` is not touched.

**Tech Stack:** Python 3.14, SQLAlchemy 2.x, `zoneinfo`, pytest, Alpine.js, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-12-saved-execution-sequences-design.md` (as amended by `d1de1ad`)

**Depends on:** Phases 1–3, all committed. Full suite green at 2084 passed / 18 skipped.

**Completes the feature.** After this, `phase1_unsupported` has nothing left to gate and is deleted.

---

## Background For The Implementer

### Where preconditions are evaluated, and why

The spec originally had `RunExecutor` evaluate preconditions and end the run `CANCELLED`. That was changed (commit `d1de1ad`) because a weekday-gated nightly schedule would manufacture a cancelled run every weekend, and an ad-hoc launch outside its window would return `202 Accepted` for a run that dies on arrival.

So: **gates run before a run is created.** Two call sites, one shared function:

| Caller | On a failed gate |
|---|---|
| `POST /api/selections/{id}/launch` | 422, body names the failing gate. No run row. |
| Scheduler `_run_schedule` | `record_scheduler_event(..., "skipped", "CANCELLED", ...)`. No run row, no `_execute_run`. |

The scheduler already has this exact shape for a disabled schedule and a missing selection version — copy those, don't invent a new one.

### The three gates

From `SequencePrecondition` (`api/schemas.py`, added in Phase 1, never yet enforced):

- `time_window {start, end}` — `"HH:MM"` strings, evaluated in the **app timezone** (`SettingsRepository.get_timezone()`, the same source APScheduler uses). An `end` earlier than `start` wraps past midnight.
- `weekdays [0..6]` — 0 is Monday, matching `datetime.weekday()`. Evaluated in the app timezone too, since "Monday" means Monday where the user is.
- `require_run_success {job_name, within_hours}` — satisfied when a `TestResult` for that `query_name` with status in `{PASSED, SLOW}` has an `executed_at` inside the window. Looks across all runs, not just this sequence's.

`TestResult.query_name` holds the job name (`etl_framework/repository/models.py:179`) and `executed_at` is the per-job timestamp (`:188`). No join to `TestRun` is needed.

### Timezone handling, carefully

`SettingsRepository.get_timezone()` returns a string like `"Europe/London"`. Use `zoneinfo.ZoneInfo`. An unknown or empty value must fall back to UTC rather than raising — a bad settings row should not make every launch explode.

`executed_at` comes back **naive** from SQLite but **aware** from Postgres. Do not compare the two directly or you get `TypeError: can't compare offset-naive and offset-aware datetimes`. Normalise in Python with the `_naive_utc` helper in Task 1, and fetch the newest matching row rather than filtering by timestamp in SQL, which sidesteps dialect differences entirely.

### Verification rules

- Raw `python -m pytest`, never `rtk`.
- Playwright via `rtk proxy npx playwright test`.
- `tests/unit/test_executor_characterization.py` is still the Phase 2 behaviour gate and still has exactly one commit (`7793e1b`). Phase 4 does not touch the executor, so it must stay untouched.

---

## File Structure

**Create**

| File | Responsibility |
|---|---|
| `api/services/sequence_preconditions.py` | The three gates. Injected `now` and timezone; one DB query. |
| `tests/unit/test_sequence_preconditions.py` | Gate logic, including midnight wrap and DST-safe comparisons. |
| `tests/unit/test_precondition_enforcement.py` | Launch 422 and scheduler skip. |

**Modify**

| File | Change |
|---|---|
| `api/routes/selections.py` | Check gates before creating the run. |
| `api/services/scheduler.py` | Check gates before creating the run; record `skipped`. |
| `api/services/sequence_validation.py` | Delete `phase1_unsupported`. |
| `api/routes/sequences.py` | Drop its two `phase1_unsupported` call sites. |
| `frontend/features/sequences.js` | Precondition state, load, and save. |
| `frontend/partials/tab-sequences.html` | Preconditions panel. |
| `frontend/help-content.js` | Precondition guidance. |

---

## Task 1: The precondition checker

**Files:**
- Create: `api/services/sequence_preconditions.py`
- Test: `tests/unit/test_sequence_preconditions.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sequence_preconditions.py`:

```python
"""The three sequence precondition gates."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import (
    RequireRunSuccess, SequencePrecondition, TimeWindow,
)
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.models import TestResult


def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _check(db, precondition, now, tz="UTC"):
    from api.services.sequence_preconditions import check
    return check(db, precondition, now=now, timezone_name=tz)


# --- nothing to check -------------------------------------------------------

def test_none_passes():
    assert _check(_db(), None, _at(2026, 8, 13, 12)).ok is True


def test_empty_precondition_passes():
    assert _check(_db(), SequencePrecondition(), _at(2026, 8, 13, 12)).ok is True


# --- time_window ------------------------------------------------------------

def test_inside_the_window_passes():
    p = SequencePrecondition(time_window=TimeWindow(start="09:00", end="17:00"))
    assert _check(_db(), p, _at(2026, 8, 13, 12)).ok is True


def test_before_the_window_fails():
    p = SequencePrecondition(time_window=TimeWindow(start="09:00", end="17:00"))
    result = _check(_db(), p, _at(2026, 8, 13, 8))
    assert result.ok is False
    assert result.gate == "time_window"
    assert "09:00" in result.reason


def test_after_the_window_fails():
    p = SequencePrecondition(time_window=TimeWindow(start="09:00", end="17:00"))
    assert _check(_db(), p, _at(2026, 8, 13, 18)).ok is False


def test_window_boundaries_are_inclusive():
    p = SequencePrecondition(time_window=TimeWindow(start="09:00", end="17:00"))
    assert _check(_db(), p, _at(2026, 8, 13, 9, 0)).ok is True
    assert _check(_db(), p, _at(2026, 8, 13, 17, 0)).ok is True


def test_window_wrapping_past_midnight():
    p = SequencePrecondition(time_window=TimeWindow(start="22:00", end="04:00"))
    assert _check(_db(), p, _at(2026, 8, 13, 23)).ok is True    # late evening
    assert _check(_db(), p, _at(2026, 8, 13, 2)).ok is True     # small hours
    assert _check(_db(), p, _at(2026, 8, 13, 12)).ok is False   # midday


def test_window_is_evaluated_in_the_app_timezone():
    p = SequencePrecondition(time_window=TimeWindow(start="09:00", end="17:00"))
    # 08:00 UTC is 09:00 in Berlin (CEST, +02:00 in August) -- inside there,
    # outside in UTC.
    assert _check(_db(), p, _at(2026, 8, 13, 8), tz="UTC").ok is False
    assert _check(_db(), p, _at(2026, 8, 13, 8), tz="Europe/Berlin").ok is True


def test_unknown_timezone_falls_back_to_utc():
    p = SequencePrecondition(time_window=TimeWindow(start="09:00", end="17:00"))
    assert _check(_db(), p, _at(2026, 8, 13, 12), tz="Not/AZone").ok is True


# --- weekdays ---------------------------------------------------------------

def test_allowed_weekday_passes():
    # 2026-08-13 is a Thursday -> weekday() == 3
    p = SequencePrecondition(weekdays=[0, 1, 2, 3, 4])
    assert _check(_db(), p, _at(2026, 8, 13, 12)).ok is True


def test_disallowed_weekday_fails():
    p = SequencePrecondition(weekdays=[5, 6])
    result = _check(_db(), p, _at(2026, 8, 13, 12))
    assert result.ok is False
    assert result.gate == "weekdays"
    assert "Thursday" in result.reason


def test_weekday_uses_the_app_timezone():
    # 23:30 UTC Thursday is already Friday in Tokyo (+09:00).
    p = SequencePrecondition(weekdays=[4])          # Friday only
    assert _check(_db(), p, _at(2026, 8, 13, 23, 30), tz="UTC").ok is False
    assert _check(_db(), p, _at(2026, 8, 13, 23, 30), tz="Asia/Tokyo").ok is True


# --- require_run_success ----------------------------------------------------

def _result(db, name, status, executed_at):
    db.add(TestResult(
        run_id="r-1", query_name=name, status=status,
        duration_seconds=0.0, source_row_count=0, target_row_count=0,
        value_mismatch_count=0, missing_in_target_count=0,
        missing_in_source_count=0, executed_at=executed_at,
    ))
    db.commit()


def test_recent_passed_run_satisfies_the_gate():
    db = _db()
    now = _at(2026, 8, 13, 12)
    _result(db, "upstream", "PASSED", now - timedelta(hours=2))
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="upstream", within_hours=6))
    assert _check(db, p, now).ok is True


def test_slow_also_counts_as_success():
    db = _db()
    now = _at(2026, 8, 13, 12)
    _result(db, "upstream", "SLOW", now - timedelta(hours=1))
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="upstream", within_hours=6))
    assert _check(db, p, now).ok is True


def test_too_old_a_run_fails():
    db = _db()
    now = _at(2026, 8, 13, 12)
    _result(db, "upstream", "PASSED", now - timedelta(hours=30))
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="upstream", within_hours=6))
    result = _check(db, p, now)
    assert result.ok is False
    assert result.gate == "require_run_success"
    assert "upstream" in result.reason


def test_a_failed_run_does_not_satisfy_the_gate():
    db = _db()
    now = _at(2026, 8, 13, 12)
    _result(db, "upstream", "FAILED", now - timedelta(hours=1))
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="upstream", within_hours=6))
    assert _check(db, p, now).ok is False


def test_no_run_at_all_fails():
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="never_ran", within_hours=6))
    result = _check(_db(), p, _at(2026, 8, 13, 12))
    assert result.ok is False
    assert "never" in result.reason.lower()


def test_the_newest_run_decides():
    db = _db()
    now = _at(2026, 8, 13, 12)
    _result(db, "upstream", "PASSED", now - timedelta(hours=5))
    _result(db, "upstream", "FAILED", now - timedelta(hours=1))
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="upstream", within_hours=6))
    # The most recent attempt failed, so the gate is not satisfied even though
    # an older one passed inside the window.
    assert _check(db, p, now).ok is False


def test_naive_timestamps_compare_correctly():
    # SQLite hands back naive datetimes; the checker must not blow up.
    db = _db()
    now = _at(2026, 8, 13, 12)
    _result(db, "upstream", "PASSED", datetime(2026, 8, 13, 11))   # naive
    p = SequencePrecondition(require_run_success=RequireRunSuccess(
        job_name="upstream", within_hours=6))
    assert _check(db, p, now).ok is True


# --- ordering ---------------------------------------------------------------

def test_the_first_failing_gate_is_reported():
    p = SequencePrecondition(
        time_window=TimeWindow(start="09:00", end="17:00"),
        weekdays=[5, 6],
    )
    # Both gates fail at 08:00 on a Thursday; time_window is checked first.
    assert _check(_db(), p, _at(2026, 8, 13, 8)).gate == "time_window"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_sequence_preconditions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.services.sequence_preconditions'`.

- [ ] **Step 3: Write the checker**

Create `api/services/sequence_preconditions.py`:

```python
"""Gates that decide whether a saved sequence should run at all.

Evaluated BEFORE a run is created, by both selection launch and the scheduler.
A failed gate means nothing happened -- no run row, no cancelled history entry.

`now` and `timezone_name` are injected so every rule is testable without
freezing the clock or touching app settings.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from etl_framework.repository.models import TestResult

if TYPE_CHECKING:  # pragma: no cover
    from api.schemas import SequencePrecondition

# A job that reached either of these is considered to have succeeded. SLOW
# passed its comparison, it just took longer than the threshold.
SUCCESS_STATUSES = ("PASSED", "SLOW")

_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class PreconditionResult:
    ok: bool
    gate: str | None = None
    reason: str | None = None


def _zone(timezone_name: str | None) -> ZoneInfo:
    """The app timezone, falling back to UTC rather than raising.

    A malformed settings row must not make every launch fail.
    """
    if not timezone_name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def _naive_utc(value: datetime) -> datetime:
    """Normalise to naive UTC so SQLite and Postgres timestamps compare.

    SQLite returns naive datetimes, Postgres returns aware ones; comparing the
    two directly raises TypeError.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _minutes(hhmm: str) -> int:
    hours, _, minutes = hhmm.partition(":")
    return int(hours) * 60 + int(minutes)


def _check_time_window(window, local: datetime) -> PreconditionResult | None:
    start, end = _minutes(window.start), _minutes(window.end)
    current = local.hour * 60 + local.minute
    inside = start <= current <= end if start <= end else (current >= start or current <= end)
    if inside:
        return None
    return PreconditionResult(
        ok=False, gate="time_window",
        reason=(
            f"Outside the allowed window {window.start}-{window.end} "
            f"({local.strftime('%H:%M')} local time)"
        ),
    )


def _check_weekdays(weekdays: list[int], local: datetime) -> PreconditionResult | None:
    if local.weekday() in weekdays:
        return None
    allowed = ", ".join(_DAY_NAMES[d] for d in sorted(weekdays)) or "no days"
    return PreconditionResult(
        ok=False, gate="weekdays",
        reason=f"{_DAY_NAMES[local.weekday()]} is not an allowed day (allowed: {allowed})",
    )


def _check_require_run_success(db: Session, rule, now: datetime) -> PreconditionResult | None:
    """The most recent run of the named job must have succeeded, recently.

    Fetches the newest matching row and compares in Python rather than
    filtering on the timestamp in SQL, which keeps it free of dialect-specific
    timezone behaviour.
    """
    latest = (
        db.query(TestResult)
        .filter(TestResult.query_name == rule.job_name)
        .filter(TestResult.executed_at.isnot(None))
        .order_by(TestResult.executed_at.desc())
        .first()
    )
    if latest is None:
        return PreconditionResult(
            ok=False, gate="require_run_success",
            reason=f"Job '{rule.job_name}' has never run",
        )

    cutoff = _naive_utc(now) - timedelta(hours=rule.within_hours)
    executed = _naive_utc(latest.executed_at)
    if executed < cutoff:
        return PreconditionResult(
            ok=False, gate="require_run_success",
            reason=(
                f"Job '{rule.job_name}' last ran at {executed:%Y-%m-%d %H:%M} UTC, "
                f"outside the last {rule.within_hours}h"
            ),
        )
    if latest.status not in SUCCESS_STATUSES:
        return PreconditionResult(
            ok=False, gate="require_run_success",
            reason=f"Job '{rule.job_name}' last finished {latest.status}, not a success",
        )
    return None


def check(
    db: Session,
    preconditions: "SequencePrecondition | None",
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> PreconditionResult:
    """Evaluate every gate, reporting the first failure."""
    if preconditions is None:
        return PreconditionResult(ok=True)

    now = now or datetime.now(timezone.utc)
    local = now.astimezone(_zone(timezone_name))

    if preconditions.time_window is not None:
        failure = _check_time_window(preconditions.time_window, local)
        if failure is not None:
            return failure

    if preconditions.weekdays is not None:
        failure = _check_weekdays(preconditions.weekdays, local)
        if failure is not None:
            return failure

    if preconditions.require_run_success is not None:
        failure = _check_require_run_success(db, preconditions.require_run_success, now)
        if failure is not None:
            return failure

    return PreconditionResult(ok=True)


def check_for_session(db: Session, preconditions: "SequencePrecondition | None") -> PreconditionResult:
    """check() with the app timezone read from settings. The production entry point."""
    from etl_framework.repository.repository import SettingsRepository

    try:
        timezone_name = SettingsRepository(db).get_timezone()
    except Exception:
        timezone_name = "UTC"
    return check(db, preconditions, timezone_name=timezone_name)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/unit/test_sequence_preconditions.py -v`
Expected: PASS — 21 passed.

- [ ] **Step 5: Commit**

```bash
git add api/services/sequence_preconditions.py tests/unit/test_sequence_preconditions.py
git commit -m "feat: add sequence precondition gates"
```

---

## Task 2: Enforce at launch and in the scheduler

**Files:**
- Modify: `api/routes/selections.py` (launch handler, around line 210)
- Modify: `api/services/scheduler.py` (`_run_schedule`, around line 111)
- Test: `tests/unit/test_precondition_enforcement.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_precondition_enforcement.py`:

```python
"""Preconditions gate run creation at both entry points."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def engine():
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def client(monkeypatch, engine):
    from api.main import app
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import JobRepository, TokenRepository

    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.routes.selections._execute_run", lambda *a, **k: None)

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        JobRepository(db).create({
            "name": "orders_recon", "description": "", "tags": [],
            "job_type": "reconciliation", "query": "SELECT 1",
            "key_columns": ["id"], "exclude_columns": [], "params": {},
            "enabled": True,
        })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


STEPS = [{"step_id": "a", "job_name": "orders_recon", "depends_on": []}]


def _sequence_with(client, preconditions, name="gated"):
    resp = client.post("/api/sequences", json={
        "name": name, "steps": STEPS, "preconditions": preconditions,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _selection_for(client, seq_id, name="sel"):
    return client.post("/api/selections", json={
        "name": name, "sequence_ref": {"sequence_id": seq_id},
    }).json()["id"]


def test_launch_is_refused_when_a_gate_fails(client):
    # No job has ever run, so require_run_success cannot be satisfied.
    seq_id = _sequence_with(client, {
        "require_run_success": {"job_name": "never_ran", "within_hours": 6},
    })
    sel_id = _selection_for(client, seq_id)

    resp = client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 422
    assert "never_ran" in resp.json()["detail"]


def test_no_run_row_is_created_when_a_gate_fails(client):
    seq_id = _sequence_with(client, {
        "require_run_success": {"job_name": "never_ran", "within_hours": 6},
    })
    sel_id = _selection_for(client, seq_id)
    client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    })

    assert client.get(f"/api/selections/{sel_id}/runs").json() == []


def test_launch_proceeds_when_every_gate_passes(client):
    seq_id = _sequence_with(client, {"weekdays": [0, 1, 2, 3, 4, 5, 6]})
    sel_id = _selection_for(client, seq_id)

    resp = client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 202, resp.text


def test_a_selection_without_a_sequence_is_unaffected(client):
    sel_id = client.post("/api/selections", json={
        "name": "plain", "job_sequence": ["orders_recon"],
    }).json()["id"]

    resp = client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 202, resp.text


def test_scheduler_records_skipped_and_creates_no_run(client, engine, monkeypatch):
    from etl_framework.repository.repository import RunRepository
    from etl_framework.repository.models import SchedulerTelemetryEvent

    seq_id = _sequence_with(client, {
        "require_run_success": {"job_name": "never_ran", "within_hours": 6},
    })
    sched = client.post("/api/schedules", json={
        "name": "gated-nightly", "cron_expr": "0 2 * * *",
        "sequence_id": seq_id, "source_env": "dev", "target_env": "prod",
    })
    assert sched.status_code == 201, sched.text
    schedule_id = sched.json()["id"]

    executed = []
    monkeypatch.setattr("api.routes.runs._execute_run", lambda *a, **k: executed.append(a))

    from api.services.scheduler import _run_schedule
    _run_schedule(schedule_id, "gated-nightly")

    with Session(engine) as db:
        events = db.query(SchedulerTelemetryEvent).all()
        runs = db.query(TestRun).all()

    assert executed == []
    assert runs == []
    skips = [e for e in events if e.event_state == "skipped"]
    assert len(skips) == 1
    assert "never_ran" in (skips[0].error_summary or "")
```

Add `TestRun` and `SchedulerTelemetryEvent` to the imports at the top of the file:

```python
from etl_framework.repository.models import SchedulerTelemetryEvent, TestRun
```

and drop the now-redundant in-function imports from that test.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_precondition_enforcement.py -v`
Expected: FAIL — the launch returns 202 rather than 422, because nothing checks the gates yet.

- [ ] **Step 3: Enforce at selection launch**

In `api/routes/selections.py`, add the import:

```python
from api.services.sequence_preconditions import check_for_session as check_preconditions
```

In the launch handler, immediately after the sequence resolves (the block that sets `job_sequence` and `dag_steps` around line 216), add:

```python
        gate = check_preconditions(db, resolved.preconditions)
        if not gate.ok:
            raise HTTPException(status_code=422, detail=gate.reason)
```

Put it before `RunRepository(db).create_run(...)` — the whole point is that no run row exists when a gate refuses. Selections with an inline `job_sequence` have no `resolved`, so they skip this entirely.

- [ ] **Step 4: Enforce in the scheduler**

In `api/services/scheduler.py`, inside `_run_schedule`'s sequence branch, after `resolved` is obtained and `sequence_meta` is set (around line 129), add:

```python
            from api.services.sequence_preconditions import check_for_session

            gate = check_for_session(db, resolved.preconditions)
            if not gate.ok:
                logger.info("Schedule '%s' skipped: %s", name, gate.reason)
                record_scheduler_event(
                    db, sched, "skipped", "CANCELLED",
                    error_summary=f"Precondition not met: {gate.reason}",
                )
                return
```

This mirrors the existing disabled-schedule and missing-version skips a few lines above — same event state, same early `return`, no run created.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/unit/test_precondition_enforcement.py tests/unit/test_selections_sequence_ref.py tests/unit/test_schedules_sequence_target.py tests/unit/test_scheduler.py tests/integration/test_sequence_workflow.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/routes/selections.py api/services/scheduler.py tests/unit/test_precondition_enforcement.py
git commit -m "feat: gate run creation on sequence preconditions"
```

---

## Task 3: Remove the phase gate

Nothing is left to gate. `phase1_unsupported` goes.

**Files:**
- Modify: `api/services/sequence_validation.py`
- Modify: `api/routes/sequences.py`
- Modify: `tests/unit/test_sequence_validation.py`
- Modify: `tests/unit/test_sequences_routes.py`

- [ ] **Step 1: Update the tests**

In `tests/unit/test_sequence_validation.py`, delete `test_retry_and_on_failure_are_allowed_from_phase3` and `test_preconditions_are_still_gated`, and delete the `test_phase1_allows_defaults` test if it is still present. They all exercise a function that is about to stop existing.

In `tests/unit/test_sequences_routes.py`, replace `test_create_still_rejects_preconditions` with:

```python
def test_create_accepts_preconditions(client):
    resp = client.post("/api/sequences", json={
        "name": "gated", "steps": CHAIN,
        "preconditions": {
            "time_window": {"start": "01:00", "end": "05:00"},
            "weekdays": [0, 1, 2, 3, 4],
        },
    })
    assert resp.status_code == 201, resp.text

    detail = client.get(f"/api/sequences/{resp.json()['id']}").json()
    stored = detail["versions"][0]["preconditions"]
    assert stored["time_window"] == {"start": "01:00", "end": "05:00"}
    assert stored["weekdays"] == [0, 1, 2, 3, 4]


def test_validate_endpoint_accepts_preconditions(client):
    resp = client.post("/api/sequences/validate", json={
        "steps": CHAIN, "preconditions": {"weekdays": [0]},
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_sequences_routes.py -v`
Expected: FAIL — `test_create_accepts_preconditions` gets 422.

- [ ] **Step 3: Delete the function and its call sites**

In `api/services/sequence_validation.py`, delete `phase1_unsupported` entirely, along with the `SequencePrecondition` entry in the `TYPE_CHECKING` import if nothing else uses it.

In `api/routes/sequences.py`:

- Drop `phase1_unsupported` from the `from api.services.sequence_validation import ...` block.
- Simplify `_check_or_422`:

```python
def _check_or_422(db: Session, steps, preconditions) -> None:
    errors = validate_steps(steps, _known_job_names(db))
    if errors:
        raise HTTPException(status_code=422, detail=errors)
```

- In `validate_sequence`, change the errors line to:

```python
    errors = validate_steps(body.steps, _known_job_names(db))
```

`_check_or_422` keeps its now-unused `preconditions` parameter so both call sites stay as they are; delete it only if you also update both callers in the same edit.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/unit/test_sequence_validation.py tests/unit/test_sequences_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/sequence_validation.py api/routes/sequences.py tests/unit/test_sequence_validation.py tests/unit/test_sequences_routes.py
git commit -m "feat: allow preconditions to be saved and retire the phase gate"
```

---

## Task 4: Preconditions panel in the editor

**Files:**
- Modify: `frontend/features/sequences.js`
- Modify: `frontend/partials/tab-sequences.html`

- [ ] **Step 1: Add state and conversion helpers**

In `frontend/features/sequences.js`, add to the returned object's state:

```javascript
      // Preconditions are edited as a flat form and converted on save, because
      // the API wants absent gates to be null rather than empty objects.
      sequencePreconditions: {
        time_window_enabled: false, time_window_start: '', time_window_end: '',
        weekdays_enabled: false, weekdays: [],
        require_run_enabled: false, require_run_job: '', require_run_hours: 24,
      },
      weekdayOptions: [
        { value: 0, label: 'Mon' }, { value: 1, label: 'Tue' }, { value: 2, label: 'Wed' },
        { value: 3, label: 'Thu' }, { value: 4, label: 'Fri' }, { value: 5, label: 'Sat' },
        { value: 6, label: 'Sun' },
      ],
```

and these methods:

```javascript
      emptyPreconditions() {
        return {
          time_window_enabled: false, time_window_start: '', time_window_end: '',
          weekdays_enabled: false, weekdays: [],
          require_run_enabled: false, require_run_job: '', require_run_hours: 24,
        };
      },

      loadPreconditionsForm(stored) {
        const form = this.emptyPreconditions();
        if (!stored) return form;
        if (stored.time_window) {
          form.time_window_enabled = true;
          form.time_window_start = stored.time_window.start;
          form.time_window_end = stored.time_window.end;
        }
        if (stored.weekdays) {
          form.weekdays_enabled = true;
          form.weekdays = [...stored.weekdays];
        }
        if (stored.require_run_success) {
          form.require_run_enabled = true;
          form.require_run_job = stored.require_run_success.job_name;
          form.require_run_hours = stored.require_run_success.within_hours;
        }
        return form;
      },

      buildPreconditionsPayload() {
        const f = this.sequencePreconditions;
        const payload = {};
        if (f.time_window_enabled && f.time_window_start && f.time_window_end) {
          payload.time_window = { start: f.time_window_start, end: f.time_window_end };
        }
        if (f.weekdays_enabled && f.weekdays.length) {
          payload.weekdays = [...f.weekdays].sort((a, b) => a - b);
        }
        if (f.require_run_enabled && f.require_run_job) {
          payload.require_run_success = {
            job_name: f.require_run_job,
            within_hours: Number(f.require_run_hours) || 1,
          };
        }
        // No gates enabled means no preconditions at all, which the API
        // expresses as null -- an empty object would be a meaningless gate set.
        return Object.keys(payload).length ? payload : null;
      },

      toggleWeekday(day) {
        const at = this.sequencePreconditions.weekdays.indexOf(day);
        if (at === -1) this.sequencePreconditions.weekdays.push(day);
        else this.sequencePreconditions.weekdays.splice(at, 1);
      },
```

- [ ] **Step 2: Load and save them**

In `openSequenceCreate()`, add:

```javascript
        this.sequencePreconditions = this.emptyPreconditions();
```

In `openSequenceVersionEditor()`, after `this.sequenceSteps = ...`, add:

```javascript
        this.sequencePreconditions = this.loadPreconditionsForm(latest ? latest.preconditions : null);
```

In `saveSequence()`, include the payload in **both** branches:

```javascript
          const preconditions = this.buildPreconditionsPayload();
```

then add `preconditions` to the create body and to the version body.

- [ ] **Step 3: Add the panel**

In `frontend/partials/tab-sequences.html`, above the step list (after the name/description/tags grid):

```html
<details class="card card-compact mb-3" data-testid="sequence-preconditions">
  <summary class="text-sm cursor-pointer">Preconditions — when may this sequence run?</summary>
  <p class="text-xs text-muted mt-2">
    Checked once before the sequence starts. If a gate says no, nothing runs and no run is recorded.
  </p>

  <label class="field-inline mt-3">
    <input type="checkbox" x-model="sequencePreconditions.time_window_enabled"
           data-testid="precondition-window-toggle" aria-label="restrict to a time window" />
    <span>Only within a time window</span>
  </label>
  <div class="grid grid-cols-2 gap-3" x-show="sequencePreconditions.time_window_enabled">
    <label class="field">
      <span class="field-label">From</span>
      <input type="time" class="field-input" data-testid="precondition-window-start"
             x-model="sequencePreconditions.time_window_start" aria-label="window start" />
    </label>
    <label class="field">
      <span class="field-label">Until</span>
      <input type="time" class="field-input" data-testid="precondition-window-end"
             x-model="sequencePreconditions.time_window_end" aria-label="window end" />
    </label>
  </div>
  <p class="text-xs text-muted" x-show="sequencePreconditions.time_window_enabled">
    Uses the application timezone. An end time earlier than the start wraps past midnight.
  </p>

  <label class="field-inline mt-3">
    <input type="checkbox" x-model="sequencePreconditions.weekdays_enabled"
           data-testid="precondition-weekdays-toggle" aria-label="restrict to weekdays" />
    <span>Only on certain days</span>
  </label>
  <div class="flex flex-wrap gap-2" x-show="sequencePreconditions.weekdays_enabled"
       data-testid="precondition-weekdays">
    <template x-for="day in weekdayOptions" :key="day.value">
      <label class="chip cursor-pointer">
        <input type="checkbox" :checked="sequencePreconditions.weekdays.includes(day.value)"
               @change="toggleWeekday(day.value)" :aria-label="day.label" />
        <span x-text="day.label"></span>
      </label>
    </template>
  </div>

  <label class="field-inline mt-3">
    <input type="checkbox" x-model="sequencePreconditions.require_run_enabled"
           data-testid="precondition-upstream-toggle" aria-label="require a recent successful job" />
    <span>Only after another job has succeeded recently</span>
  </label>
  <div class="grid grid-cols-2 gap-3" x-show="sequencePreconditions.require_run_enabled">
    <label class="field">
      <span class="field-label">Job</span>
      <select class="field-input" data-testid="precondition-upstream-job"
              x-model="sequencePreconditions.require_run_job" aria-label="upstream job">
        <option value="">Select a job…</option>
        <template x-for="job in jobs" :key="job.name">
          <option :value="job.name" x-text="job.name"></option>
        </template>
      </select>
    </label>
    <label class="field">
      <span class="field-label">Within the last (hours)</span>
      <input type="number" min="1" class="field-input" data-testid="precondition-upstream-hours"
             x-model.number="sequencePreconditions.require_run_hours" aria-label="within hours" />
    </label>
  </div>
</details>
```

- [ ] **Step 4: Rebuild and verify**

Run: `npm run build:html && python -m pytest tests/integration/test_api_frontend_smoke.py -v`
Expected: build succeeds; smoke test PASSES.

- [ ] **Step 5: Check the round trip by hand**

Create a sequence with a 01:00–05:00 window and Mon–Fri, save, reload the page, reopen it. Both gates must come back ticked with their values. Then untick everything, save a new version, and confirm the stored `preconditions` is `null` rather than `{}`.

- [ ] **Step 6: Commit**

```bash
git add frontend/features/sequences.js frontend/partials/tab-sequences.html frontend/index.html
git commit -m "feat: add a preconditions panel to the sequence editor"
```

---

## Task 5: Help content

**Files:**
- Modify: `frontend/help-content.js`

- [ ] **Step 1: Add the step**

Append to the existing `sequences` section's `steps[]`:

```javascript
      {
        title: 'Gate the whole sequence',
        text: 'Preconditions decide whether a sequence may start at all. Restrict it to a time window, to certain days, or to running only after another job has succeeded recently. Times and days use the application timezone, and a window whose end is earlier than its start wraps past midnight.',
        where: 'Sequences tab → Preconditions',
        tip: 'To wait for a file to arrive, add a freshness job as the first step instead — you then see the check in the run with its own status.',
        warn: 'A refused gate means no run is recorded at all. For a schedule, look in Scheduler Reports for the skipped entry and its reason.',
      },
```

- [ ] **Step 2: Verify**

Run: `npm run build:html && python -m pytest tests/unit -k help -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/help-content.js frontend/index.html
git commit -m "docs: document sequence preconditions"
```

---

## Task 6: End-to-end coverage

**Files:**
- Modify: `tests/e2e/17-sequences.spec.ts`

- [ ] **Step 1: Add a spec**

Read the existing file first and reuse its helpers exactly. Append:

```typescript
  test('preconditions round-trip through the editor', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill('e2e-gated');

    await page.getByTestId('sequence-step-job-0').selectOption({ index: 1 });

    await page.getByTestId('sequence-preconditions').click();   // open the details
    await page.getByTestId('precondition-window-toggle').check();
    await page.getByTestId('precondition-window-start').fill('01:00');
    await page.getByTestId('precondition-window-end').fill('05:00');
    await page.getByTestId('precondition-weekdays-toggle').check();
    await page.getByTestId('precondition-weekdays').getByRole('checkbox').first().check();

    await page.getByTestId('sequence-save-btn').click();
    await expect(page.getByTestId('sequence-row-e2e-gated')).toBeVisible();

    // Reopen and confirm the values survived the round trip.
    await page.getByTestId('sequence-row-e2e-gated').click();
    await page.getByTestId('sequence-edit-btn').click();
    await page.getByTestId('sequence-preconditions').click();
    await expect(page.getByTestId('precondition-window-start')).toHaveValue('01:00');
    await expect(page.getByTestId('precondition-window-end')).toHaveValue('05:00');
  });
```

- [ ] **Step 2: Run it**

Run: `rtk proxy npx playwright test tests/e2e/17-sequences.spec.ts`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/17-sequences.spec.ts
git commit -m "test: add e2e coverage for the preconditions panel"
```

---

## Task 7: Full verification

- [ ] **Step 1: The behaviour gate**

Run: `python -m pytest tests/unit/test_executor_characterization.py -v`
Expected: 10 passed.

Run: `git log --oneline -- tests/unit/test_executor_characterization.py`
Expected: still exactly one commit (`7793e1b`). Phase 4 does not touch the executor, so this must be unchanged.

- [ ] **Step 2: Whole Python suite**

Run: `python -m pytest tests/unit tests/integration -q`
Expected: PASS. Baseline entering Phase 4 is 2084 passed / 18 skipped; expect roughly 28 more and a handful fewer from the deleted gate tests.

- [ ] **Step 3: Generated HTML in sync**

Run: `npm run build:html && git diff --exit-code frontend/index.html`
Expected: exit code 0.

- [ ] **Step 4: E2E**

Run: `rtk proxy npx playwright test`
Expected: PASS.

- [ ] **Step 5: Confirm no phase gate survives**

Run: `grep -rn "phase1_unsupported\|arrives in Phase" api/ tests/`
Expected: no matches. Every field the schema declares is now honoured.

- [ ] **Step 6: Clean tree**

Run: `git status`
Expected: clean, or only files you intend to leave uncommitted.

---

## Phase 4 Done — The Feature Is Complete

A sequence can now say when it is allowed to run, and the gate is enforced before anything is created — a refused launch returns 422 naming the gate, a refused schedule records a `skipped` telemetry event, and neither leaves a misleading cancelled run in history.

With `phase1_unsupported` gone, every field `SequenceStepRef` and `SequencePrecondition` declare is honoured by the executor. Across the four phases: sequences are saved, named, versioned, and shared by selections and schedules; they execute as real DAGs with concurrent branches, trigger rules, non-blocking holds, and per-step retry and failure policy; and they can be gated on time, day, and upstream success.

Worth considering next, none of it planned: a "why was this skipped?" surface in Scheduler Reports that reads the precondition telemetry, and the deferred items in spec §9 — cross-sequence dependencies, dynamic fan-out, and resuming a cancelled run from the point of failure.
