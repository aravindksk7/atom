# App-wide Timezone Setting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one admin-configurable, server-side IANA timezone setting that (a) drives how every timestamp is displayed across the dashboard and generated HTML reports, and (b) drives when cron-scheduled runs actually fire, replacing the currently hardcoded `"UTC"` in both places.

**Architecture:** A new single-row `app_settings` table (via a `SettingsRepository`) is the one source of truth. `GET/PUT /api/settings` exposes it (PUT gated to admin tokens, matching the existing `tokens.py` pattern). The scheduler reads it when building each `CronTrigger` and re-adds all jobs when it changes. The report generator's existing `to_local` Jinja filter is extended to accept it. The frontend fetches it once at startup and threads it through the existing `fmtDate()` helper (its only call path, 18 sites in `index.html`) via `Intl.DateTimeFormat`.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite), Alpine.js frontend, APScheduler, Python stdlib `zoneinfo`.

**Reference spec:** `docs/superpowers/specs/2026-07-08-app-timezone-setting-design.md`

---

## Task 1: `AppSettings` model + DB migration shim

**Files:**
- Modify: `etl_framework/repository/models.py` (add class after `ScheduledRun`, ~line 270)
- Modify: `etl_framework/repository/database.py` (add `CREATE TABLE IF NOT EXISTS` + seed row inside `_ensure_compare_columns`, ~line 320)

- [ ] **Step 1: Add the `AppSettings` model**

In `etl_framework/repository/models.py`, insert this new section immediately after the `ScheduledRun` class (which ends right before the `# Audit log` comment block, around line 270):

```python
# ---------------------------------------------------------------------------
# App-wide settings
# ---------------------------------------------------------------------------

class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)
    timezone = Column(String(64), nullable=False, default="UTC")
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
```

- [ ] **Step 2: Add the SQLite backward-compat shim**

In `etl_framework/repository/database.py`, inside `_ensure_compare_columns`, find the block that ends with:

```python
        if scheduled_run_cols:
            if "selection_id" not in scheduled_run_cols:
                conn.execute(text("ALTER TABLE scheduled_runs ADD COLUMN selection_id INTEGER"))
            if "selection_version" not in scheduled_run_cols:
                conn.execute(text("ALTER TABLE scheduled_runs ADD COLUMN selection_version INTEGER"))
```

Immediately after it (still inside the `with bind.begin() as conn:` block, still inside `_ensure_compare_columns`), add:

```python

        # --- App-wide settings (single row) ---
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS app_settings ("
            "id INTEGER PRIMARY KEY, "
            "timezone VARCHAR(64) NOT NULL DEFAULT 'UTC', "
            "updated_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT OR IGNORE INTO app_settings (id, timezone) VALUES (1, 'UTC')"
        ))
```

- [ ] **Step 3: Verify the app still starts cleanly against the real dev DB**

Run: `.venv/Scripts/python.exe -c "from etl_framework.repository.database import init_db; init_db(); print('ok')"`
Expected: prints `ok` with no exceptions (this exercises `init_db()` -> `Base.metadata.create_all` -> `_ensure_compare_columns` against the actual `etl_framework.db` SQLite file, confirming the new table + seed row don't collide with existing data).

- [ ] **Step 4: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/repository/database.py
git commit -m "feat(db): add app_settings table for app-wide timezone"
```

---

## Task 2: `SettingsRepository`

**Files:**
- Modify: `etl_framework/repository/repository.py` (add `AppSettings` to the models import at the top; add `SettingsRepository` class at the end of the file)
- Create: `tests/unit/test_settings_repository.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_settings_repository.py`:

```python
"""Tests for SettingsRepository (app-wide timezone setting)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import SettingsRepository


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_get_timezone_defaults_to_utc_on_fresh_db():
    db = _session()
    assert SettingsRepository(db).get_timezone() == "UTC"


def test_set_timezone_persists_and_round_trips():
    db = _session()
    repo = SettingsRepository(db)
    repo.set_timezone("America/New_York")
    assert repo.get_timezone() == "America/New_York"


def test_set_timezone_rejects_unknown_zone():
    db = _session()
    with pytest.raises(ValueError):
        SettingsRepository(db).set_timezone("Not/AZone")


def test_set_timezone_updates_updated_at():
    db = _session()
    repo = SettingsRepository(db)
    row = repo.set_timezone("Europe/London")
    assert row.updated_at is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_settings_repository.py -v`
Expected: FAIL — `ImportError: cannot import name 'SettingsRepository'`

- [ ] **Step 3: Add `AppSettings` to the repository.py models import**

In `etl_framework/repository/repository.py`, the import block at the top currently reads:

```python
from etl_framework.repository.models import (
    SavedConfig, SavedJob, TestRun, TestResult, MismatchDetail,
    ApiToken, NotificationHook, NotificationDelivery, ScheduledRun, JobLineageEdge, AuditEvent,
    RunStep, JobSelection, JobSelectionVersion, TERMINAL_STATUSES,
)
```

Change it to:

```python
from etl_framework.repository.models import (
    SavedConfig, SavedJob, TestRun, TestResult, MismatchDetail,
    ApiToken, NotificationHook, NotificationDelivery, ScheduledRun, JobLineageEdge, AuditEvent,
    RunStep, JobSelection, JobSelectionVersion, AppSettings, TERMINAL_STATUSES,
)
```

- [ ] **Step 4: Implement `SettingsRepository`**

Append to the end of `etl_framework/repository/repository.py` (the file currently ends with `SchemaSnapshotRepository.get_history`):

```python


# ---------------------------------------------------------------------------
# App-wide settings repository
# ---------------------------------------------------------------------------

class SettingsRepository:
    """Single-row app-wide settings (id=1). Currently holds just the display/schedule timezone."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def _get_or_create(self) -> AppSettings:
        row = self._db.get(AppSettings, 1)
        if row is None:
            row = AppSettings(id=1, timezone="UTC")
            self._db.add(row)
            self._db.commit()
            self._db.refresh(row)
        return row

    def get_timezone(self) -> str:
        return self._get_or_create().timezone

    def set_timezone(self, tz_name: str) -> AppSettings:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(tz_name)
        except Exception as exc:
            raise ValueError(f"Unknown timezone: {tz_name}") from exc
        row = self._get_or_create()
        row.timezone = tz_name
        row.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(row)
        return row
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_settings_repository.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add etl_framework/repository/repository.py tests/unit/test_settings_repository.py
git commit -m "feat(db): add SettingsRepository for app-wide timezone"
```

---

## Task 3: `GET`/`PUT /api/settings` route

**Files:**
- Create: `api/routes/settings.py`
- Modify: `api/main.py` (register router)
- Create: `tests/test_settings_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_settings_routes.py`:

```python
"""Integration tests for GET/PUT /api/settings routes."""
import etl_framework.repository.models  # noqa: F401 — registers ORM models with Base
import etl_framework.repository.database as _db_module
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
from api.main import app
from api.dependencies import get_session
from fastapi.testclient import TestClient

_admin_token = None
_regular_token = None


@pytest.fixture(scope="module")
def settings_client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)
    previous_session_local = _db_module.SessionLocal
    previous_overrides = dict(app.dependency_overrides)
    _db_module.SessionLocal = testing_session
    app.dependency_overrides.clear()

    def override_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        _db_module.SessionLocal = previous_session_local
        engine.dispose()


def test_1_bootstrap_creates_admin_token(settings_client):
    global _admin_token
    resp = settings_client.post("/api/tokens", json={"name": "bootstrap-admin"})
    assert resp.status_code == 201
    _admin_token = resp.json()["raw_token"]


def test_2_create_regular_token(settings_client):
    global _regular_token
    resp = settings_client.post(
        "/api/tokens",
        json={"name": "regular", "is_admin": False},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 201
    _regular_token = resp.json()["raw_token"]


def test_3_get_settings_defaults_to_utc(settings_client):
    resp = settings_client.get("/api/settings", headers={"Authorization": f"Bearer {_regular_token}"})
    assert resp.status_code == 200
    assert resp.json() == {"timezone": "UTC"}


def test_4_put_settings_requires_admin(settings_client):
    resp = settings_client.put(
        "/api/settings",
        json={"timezone": "America/New_York"},
        headers={"Authorization": f"Bearer {_regular_token}"},
    )
    assert resp.status_code == 403


def test_5_put_settings_rejects_invalid_zone(settings_client):
    resp = settings_client.put(
        "/api/settings",
        json={"timezone": "Not/AZone"},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 422


def test_6_put_settings_persists_as_admin(settings_client):
    resp = settings_client.put(
        "/api/settings",
        json={"timezone": "America/New_York"},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"timezone": "America/New_York"}

    get_resp = settings_client.get("/api/settings", headers={"Authorization": f"Bearer {_regular_token}"})
    assert get_resp.json() == {"timezone": "America/New_York"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_settings_routes.py -v`
Expected: FAIL — `404` on `/api/settings` (route doesn't exist yet), or connection/import errors.

- [ ] **Step 3: Implement the route**

Create `api/routes/settings.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.dependencies import get_session, require_admin
from api.services.audit_service import AuditService
from etl_framework.repository.repository import SettingsRepository

router = APIRouter(tags=["settings"])


class SettingsOut(BaseModel):
    timezone: str


class SettingsUpdate(BaseModel):
    timezone: str


@router.get("", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_session)):
    return SettingsOut(timezone=SettingsRepository(db).get_timezone())


@router.put("", response_model=SettingsOut, dependencies=[Depends(require_admin)])
def update_settings(body: SettingsUpdate, request: Request, db: Session = Depends(get_session)):
    try:
        row = SettingsRepository(db).set_timezone(body.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    from api.services import scheduler as _sched_svc
    _sched_svc.refresh_all_timezones()

    AuditService(db).log(request, "settings.timezone_changed", "settings", 1, {"timezone": row.timezone})
    return SettingsOut(timezone=row.timezone)
```

- [ ] **Step 4: Register the router in `api/main.py`**

Change the import block:

```python
from api.routes import tokens, notifications, schedules, lineage as lineage_routes
```

to:

```python
from api.routes import tokens, notifications, schedules, lineage as lineage_routes
from api.routes import settings as settings_routes
```

And add, next to the other `include_router` calls (near `app.include_router(schedules.router, prefix="/api/schedules")`):

```python
app.include_router(settings_routes.router, prefix="/api/settings")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_settings_routes.py -v`
Expected: 6 passed

Note: `test_6_put_settings_persists_as_admin` also exercises `refresh_all_timezones()` (called inside the route). At this point in the plan that function doesn't exist yet on `api.services.scheduler` — Task 4 adds it. If this test fails with `AttributeError: module 'api.services.scheduler' has no attribute 'refresh_all_timezones'`, that's expected until Task 4 is done; do Task 4 immediately after this step before considering Task 3 complete, then re-run this test file to confirm all 6 pass together.

- [ ] **Step 6: Commit**

```bash
git add api/routes/settings.py api/main.py tests/test_settings_routes.py
git commit -m "feat(api): add GET/PUT /api/settings for app-wide timezone"
```

---

## Task 4: Scheduler uses the configured timezone

**Files:**
- Modify: `api/services/scheduler.py`
- Modify: `tests/unit/test_scheduler.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_scheduler.py`:

```python
# ---------------------------------------------------------------------------
# App-wide timezone integration
# ---------------------------------------------------------------------------

def test_add_job_uses_current_app_timezone(monkeypatch):
    pytest.importorskip("apscheduler")
    from api.services import scheduler as svc

    added = {}

    class FakeScheduler:
        def add_job(self, func, trigger=None, id=None, args=None,
                     replace_existing=None, misfire_grace_time=None):
            added["trigger"] = trigger
            added["id"] = id

    monkeypatch.setattr(svc, "_scheduler", FakeScheduler())
    monkeypatch.setattr(svc, "_current_timezone", lambda: "America/New_York")

    class FakeSched:
        id = 1
        name = "nightly"
        cron_expr = "0 9 * * *"

    svc._add_job(FakeSched())
    assert added["id"] == "etl_schedule_1"
    assert str(added["trigger"].timezone) == "America/New_York"


def test_refresh_all_timezones_readds_enabled_schedules(monkeypatch):
    pytest.importorskip("apscheduler")
    from api.services import scheduler as svc
    from etl_framework.repository.database import Base
    import etl_framework.repository.database as _db_module

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)
    previous = _db_module.SessionLocal
    _db_module.SessionLocal = testing_session
    try:
        db = testing_session()
        ScheduleRepository(db).create(_sched_data(name="nightly", enabled=True))
        ScheduleRepository(db).create(_sched_data(name="off", enabled=False))
        db.close()

        calls = []
        monkeypatch.setattr(svc, "_add_job", lambda sched: calls.append(sched.name))
        monkeypatch.setattr(svc, "_scheduler", object())  # truthy; only _add_job matters here
        svc.refresh_all_timezones()
        assert calls == ["nightly"]
    finally:
        _db_module.SessionLocal = previous


def test_refresh_all_timezones_noop_when_not_started():
    from api.services import scheduler as svc
    svc._scheduler = None
    svc.refresh_all_timezones()  # must not raise
```

Add the missing imports at the top of `tests/unit/test_scheduler.py` (the file already imports `create_engine`, `Session`, `StaticPool`, `Base`, `ScheduleRepository` — add `sessionmaker`, since the existing `_session()` helper uses `Session(engine)` directly rather than a sessionmaker):

```python
from sqlalchemy.orm import Session, sessionmaker
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_scheduler.py -v -k timezone`
Expected: FAIL — `AttributeError: module 'api.services.scheduler' has no attribute '_current_timezone'` (and `refresh_all_timezones`).

- [ ] **Step 3: Implement `_current_timezone` and wire it into `_add_job`**

In `api/services/scheduler.py`, add this function (place it right before `_add_job`):

```python
def _current_timezone() -> str:
    from etl_framework.repository.database import SessionLocal
    from etl_framework.repository.repository import SettingsRepository
    db = SessionLocal()
    try:
        return SettingsRepository(db).get_timezone()
    finally:
        db.close()
```

Change `_add_job` from:

```python
def _add_job(sched) -> None:
    if _scheduler is None:
        return
    try:
        trigger = CronTrigger.from_crontab(sched.cron_expr, timezone="UTC")
        _scheduler.add_job(
            _run_schedule,
            trigger=trigger,
            id=_job_id(sched.id),
            args=[sched.id, sched.name],
            replace_existing=True,
            misfire_grace_time=300,
        )
    except Exception as exc:
        logger.warning("Failed to schedule '%s': %s", sched.name, exc)
```

to:

```python
def _add_job(sched) -> None:
    if _scheduler is None:
        return
    try:
        trigger = CronTrigger.from_crontab(sched.cron_expr, timezone=_current_timezone())
        _scheduler.add_job(
            _run_schedule,
            trigger=trigger,
            id=_job_id(sched.id),
            args=[sched.id, sched.name],
            replace_existing=True,
            misfire_grace_time=300,
        )
    except Exception as exc:
        logger.warning("Failed to schedule '%s': %s", sched.name, exc)
```

- [ ] **Step 4: Add `refresh_all_timezones()`**

Add this function after `reload_job` (right before `is_available`):

```python
def refresh_all_timezones() -> None:
    """Re-add every enabled schedule so its CronTrigger picks up the current app timezone."""
    if _scheduler is None:
        return
    from etl_framework.repository.database import SessionLocal
    from etl_framework.repository.repository import ScheduleRepository
    db = SessionLocal()
    try:
        for sched in ScheduleRepository(db).list_enabled():
            _add_job(sched)
    finally:
        db.close()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_scheduler.py -v`
Expected: all pass (existing tests + 3 new ones)

- [ ] **Step 6: Re-run Task 3's route tests now that `refresh_all_timezones` exists**

Run: `.venv/Scripts/python.exe -m pytest tests/test_settings_routes.py -v`
Expected: 6 passed

- [ ] **Step 7: Commit**

```bash
git add api/services/scheduler.py tests/unit/test_scheduler.py
git commit -m "feat(scheduler): use app-configured timezone for cron triggers"
```

---

## Task 5: HTML report uses the configured timezone

**Files:**
- Modify: `etl_framework/reporting/generator.py`
- Modify: `api/services/artifact_service.py`
- Modify: `tests/unit/test_reporting_generator.py`
- Modify: `test_artifact_service.py` (repo root)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_reporting_generator.py`:

```python


def test_to_local_with_tz_name_converts_to_that_zone():
    from zoneinfo import ZoneInfo
    utc_dt = datetime(2026, 7, 1, 18, 30, 0, tzinfo=timezone.utc)
    result = to_local(utc_dt, "America/New_York")
    expected = utc_dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M %Z")
    assert result == expected


def test_to_local_none_tz_name_falls_back_to_system_local():
    utc_dt = datetime(2026, 7, 1, 18, 30, 0, tzinfo=timezone.utc)
    assert to_local(utc_dt, None) == to_local(utc_dt)


def test_report_generator_binds_configured_timezone_to_filter():
    from etl_framework.reporting.generator import ReportGenerator
    from zoneinfo import ZoneInfo
    gen = ReportGenerator(output_dir="./reports", timezone="America/New_York")
    filt = gen._jinja_env.filters["to_local"]
    utc_dt = datetime(2026, 7, 1, 18, 30, 0, tzinfo=timezone.utc)
    expected = utc_dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M %Z")
    assert filt(utc_dt) == expected
```

Append to `test_artifact_service.py` (repo root):

```python

from unittest.mock import patch


@patch("api.services.artifact_service._current_app_timezone", return_value="America/New_York")
@patch("api.services.artifact_service.ReportGenerator")
def test_generate_report_passes_configured_timezone(MockGenerator, mock_tz):
    mock_repo = MagicMock()
    mock_repo.get_run.return_value = MagicMock(run_id="run-123")
    mock_generator_instance = MockGenerator.return_value
    mock_generator_instance.generate.return_value = "/tmp/reports/report_run-123.html"

    service = ArtifactService(repository=mock_repo, report_dir="/tmp/reports")
    service.generate_html_report("run-123")

    MockGenerator.assert_called_once_with(output_dir="/tmp/reports", timezone="America/New_York")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_reporting_generator.py test_artifact_service.py -v`
Expected: FAIL — `TypeError: to_local() takes 1 positional argument but 2 were given`, `TypeError: __init__() got an unexpected keyword argument 'timezone'`, and `AttributeError`/import error for `_current_app_timezone`.

- [ ] **Step 3: Update `to_local` and `ReportGenerator` in `etl_framework/reporting/generator.py`**

Replace:

```python
def to_local(value):
    """Jinja filter: render an aware UTC datetime as local wall-clock time with a zone abbreviation."""
    if value is None:
        return ""
    return value.astimezone().strftime("%Y-%m-%d %H:%M %Z")
```

with:

```python
def to_local(value, tz_name: str | None = None):
    """Jinja filter: render an aware UTC datetime as local wall-clock time with a zone abbreviation.

    With no tz_name, converts to the server process's OS-local timezone (original behavior).
    With tz_name, converts to that IANA zone instead (the app-wide configured timezone).
    """
    if value is None:
        return ""
    if tz_name:
        from zoneinfo import ZoneInfo
        return value.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M %Z")
    return value.astimezone().strftime("%Y-%m-%d %H:%M %Z")
```

Replace the `ReportGenerator.__init__`:

```python
    def __init__(
        self,
        output_dir: str = DEFAULT_OUTPUT_DIR,
        max_mismatch_display: int = MAX_MISMATCH_DISPLAY,
    ):
        self._output_dir = Path(output_dir)
        self._max_mismatch_display = max_mismatch_display
        
        template_dir = Path(__file__).parent / "templates"
        loader = FileSystemLoader(template_dir)
        self._jinja_env = Environment(loader=loader, autoescape=True)
        self._jinja_env.filters["to_local"] = to_local
```

with:

```python
    def __init__(
        self,
        output_dir: str = DEFAULT_OUTPUT_DIR,
        max_mismatch_display: int = MAX_MISMATCH_DISPLAY,
        timezone: str | None = None,
    ):
        self._output_dir = Path(output_dir)
        self._max_mismatch_display = max_mismatch_display
        self._timezone = timezone

        template_dir = Path(__file__).parent / "templates"
        loader = FileSystemLoader(template_dir)
        self._jinja_env = Environment(loader=loader, autoescape=True)
        self._jinja_env.filters["to_local"] = lambda v: to_local(v, self._timezone)
```

- [ ] **Step 4: Wire the configured timezone into `ArtifactService`**

In `api/services/artifact_service.py`, replace the full file with:

```python
import os
from fastapi import HTTPException
from etl_framework.repository.base import AbstractTestRunRepository
from etl_framework.reporting.generator import ReportGenerator
from api.services.run_report import build_run_report_snapshot
import logging

logger = logging.getLogger("api.services.artifact_service")


def _current_app_timezone() -> str:
    from etl_framework.repository.database import SessionLocal
    from etl_framework.repository.repository import SettingsRepository
    db = SessionLocal()
    try:
        return SettingsRepository(db).get_timezone()
    finally:
        db.close()


class ArtifactService:
    def __init__(self, repository: AbstractTestRunRepository, report_dir: str = "./reports"):
        self._repository = repository
        self._report_dir = report_dir

    def generate_html_report(self, run_id: str) -> str:
        run = self._repository.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found.")

        try:
            generator = ReportGenerator(output_dir=self._report_dir, timezone=_current_app_timezone())
            report_path = generator.generate(build_run_report_snapshot(run, include_mismatches=True))
            return report_path
        except Exception as e:
            logger.error(f"Failed to generate HTML report for {run_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="HTML Report generation failed.")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_reporting_generator.py tests/unit/test_report_template.py test_artifact_service.py -v`
Expected: all pass (the pre-existing `test_accepted_at_rendered_via_to_local_filter` in `test_report_template.py` and the two original `test_artifact_service.py` tests must still pass unchanged — they exercise the default/no-timezone-argument path)

- [ ] **Step 6: Commit**

```bash
git add etl_framework/reporting/generator.py api/services/artifact_service.py tests/unit/test_reporting_generator.py test_artifact_service.py
git commit -m "feat(reports): render HTML report timestamps in the configured app timezone"
```

---

## Task 6: Frontend — fetch the setting and rewrite `fmtDate()`

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Add timezone state**

In `frontend/app.js`, find this block (around line 519-524):

```js
    acceptForms: {},

    // -----------------------------------------------------------
    // Security – API tokens
    // -----------------------------------------------------------
    tokens: [],
```

Insert a new block between them:

```js
    acceptForms: {},

    // -----------------------------------------------------------
    // Regional — app-wide timezone
    // -----------------------------------------------------------
    appTimezone: 'UTC',
    timezoneOpen: false,
    timezoneDraft: 'UTC',
    timezoneSaving: false,
    timezoneOptions: [
      'UTC',
      'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
      'America/Anchorage', 'America/Sao_Paulo', 'America/Mexico_City', 'America/Toronto',
      'Europe/London', 'Europe/Dublin', 'Europe/Paris', 'Europe/Berlin', 'Europe/Madrid',
      'Europe/Rome', 'Europe/Amsterdam', 'Europe/Moscow', 'Europe/Istanbul',
      'Asia/Kolkata', 'Asia/Dubai', 'Asia/Karachi', 'Asia/Dhaka', 'Asia/Bangkok',
      'Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Singapore', 'Asia/Tokyo', 'Asia/Seoul',
      'Australia/Sydney', 'Australia/Perth', 'Pacific/Auckland',
    ],

    // -----------------------------------------------------------
    // Security – API tokens
    // -----------------------------------------------------------
    tokens: [],
```

- [ ] **Step 2: Add `loadTimezoneSetting()` and `saveTimezoneSetting()` methods**

Find `async loadHooks() {` (around line 3067):

```js
    async loadHooks() {
      try { this.hooks = await api('GET', '/api/notifications'); } catch {}
    },
```

Insert immediately before it:

```js
    async loadTimezoneSetting() {
      try {
        const resp = await api('GET', '/api/settings');
        this.appTimezone = resp.timezone || 'UTC';
        this.timezoneDraft = this.appTimezone;
      } catch {}
    },

    async saveTimezoneSetting() {
      this.timezoneSaving = true;
      try {
        const resp = await api('PUT', '/api/settings', { timezone: this.timezoneDraft });
        this.appTimezone = resp.timezone;
        this.toast('success', 'Timezone updated', `All timestamps now shown in ${resp.timezone}`);
      } catch (e) {
        this.toast('error', 'Failed to update timezone', e.message || '');
      } finally {
        this.timezoneSaving = false;
      }
    },

    async loadHooks() {
      try { this.hooks = await api('GET', '/api/notifications'); } catch {}
    },
```

- [ ] **Step 3: Call `loadTimezoneSetting()` from `init()`**

Find (around line 638-645):

```js
      if (this.storedToken) {
        const tokenValid = await this.resolveActiveTokenName({ verify: true, clearInvalid: true });
        if (tokenValid) {
          await this.loadAll();
          this.loadTokens();
          this.loadHooks();
          this.loadSchedules();
        }
      }
```

Change to:

```js
      if (this.storedToken) {
        const tokenValid = await this.resolveActiveTokenName({ verify: true, clearInvalid: true });
        if (tokenValid) {
          await this.loadAll();
          this.loadTokens();
          this.loadHooks();
          this.loadSchedules();
          this.loadTimezoneSetting();
        }
      }
```

- [ ] **Step 4: Rewrite `fmtDate()` to use the configured timezone**

Find (around line 3520):

```js
    fmtDate(iso) {
      if (!iso) return '—';
      // Treat bare ISO strings (no timezone suffix) as UTC so toLocale* shows local time correctly
      const ts = /[Zz]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + 'Z';
      const d = new Date(ts);
      return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    },
```

Replace with:

```js
    fmtDate(iso) {
      if (!iso) return '—';
      // Treat bare ISO strings (no timezone suffix) as UTC so conversion below is correct
      const ts = /[Zz]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + 'Z';
      const d = new Date(ts);
      if (isNaN(d.getTime())) return '—';
      try {
        return new Intl.DateTimeFormat([], {
          timeZone: this.appTimezone || 'UTC',
          year: 'numeric', month: 'numeric', day: 'numeric',
          hour: '2-digit', minute: '2-digit',
        }).format(d);
      } catch {
        // Unknown/unsupported timeZone value — fall back to browser-local rather than throwing
        return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }
    },
```

- [ ] **Step 5: Manually verify in the browser**

1. Start the server: `.venv/Scripts/python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload`
2. Open `http://127.0.0.1:8000/`, set up/paste an admin API token if prompted.
3. Open the browser devtools console and run `document.querySelector('[x-data]').__x.$data.appTimezone` (or just check the History tab timestamps) — confirm it reads `"UTC"` by default and dates render correctly (no `"Invalid Date"` or `"—"` on rows that have a real timestamp).
4. In the console, run `fetch('/api/settings').then(r=>r.json()).then(console.log)` with your token — confirm it returns `{"timezone": "UTC"}`.

- [ ] **Step 6: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): render timestamps in the app-configured timezone"
```

---

## Task 7: Frontend — timezone settings card (admin-editable)

**Files:**
- Modify: `frontend/index.html`

- [ ] **Step 1: Add the collapsible card**

In `frontend/index.html`, find the closing of the Security card and the opening of the Notifications card (around line 290-293):

```html
  </div>

  <!-- Notifications – Webhook Hooks -->
  <div class="card mt-4">
```

Insert a new card between them:

```html
  </div>

  <!-- Regional – App-wide Timezone -->
  <div class="card mt-4">
    <div class="flex items-center justify-between cursor-pointer" @click="timezoneOpen = !timezoneOpen">
      <div class="font-semibold text-slate-700">🌐 Regional — Timezone</div>
      <span class="text-muted text-sm" x-text="timezoneOpen ? '▲ collapse' : '▼ expand'"></span>
    </div>
    <template x-if="timezoneOpen">
      <div class="mt-3 space-y-3">
        <p class="text-muted text-sm">All timestamps across the dashboard, run history, and generated reports are shown in this timezone. Scheduled runs also fire according to it — a cron expression of <code>0 9 * * *</code> means 9am in this zone.</p>
        <template x-if="!activeTokenIsAdmin">
          <div class="text-sm">Current: <span class="font-medium" x-text="appTimezone"></span> <span class="text-muted text-xs">(administrator access required to change)</span></div>
        </template>
        <template x-if="activeTokenIsAdmin">
          <div class="flex items-center gap-2">
            <select x-model="timezoneDraft" class="field-input field-select flex-1">
              <template x-for="tz in timezoneOptions" :key="tz">
                <option :value="tz" x-text="tz"></option>
              </template>
            </select>
            <button @click="saveTimezoneSetting()" :disabled="timezoneSaving || timezoneDraft === appTimezone" class="btn-primary btn-sm">
              <span x-show="!timezoneSaving">Save</span>
              <span x-show="timezoneSaving">Saving…</span>
            </button>
          </div>
        </template>
      </div>
    </template>
  </div>

  <!-- Notifications – Webhook Hooks -->
  <div class="card mt-4">
```

- [ ] **Step 2: Add the timezone note to the schedule modal's cron helper text**

Find (around line 1639):

```html
        <div><label class="field-label">Cron Expression *</label><input x-model="scheduleModal.cron_expr" class="field-input font-mono" placeholder="0 6 * * *" /><div class="text-xs text-muted mt-1">Format: min hour dom month dow — e.g. <code>0 6 * * *</code> = 6am daily</div></div>
```

Replace with:

```html
        <div><label class="field-label">Cron Expression *</label><input x-model="scheduleModal.cron_expr" class="field-input font-mono" placeholder="0 6 * * *" /><div class="text-xs text-muted mt-1">Format: min hour dom month dow — e.g. <code>0 6 * * *</code> = 6am daily (times in <span x-text="appTimezone"></span>)</div></div>
```

- [ ] **Step 3: Manually verify in the browser**

1. Start the server: `.venv/Scripts/python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload`
2. Open `http://127.0.0.1:8000/`, authenticate with an admin token.
3. Go to the Config tab, expand "🌐 Regional — Timezone". Confirm it shows "UTC" and a dropdown (since you're an admin).
4. Change the dropdown to e.g. `America/New_York`, click Save. Confirm a success toast appears and the History/Monitor tab timestamps immediately shift by the UTC offset.
5. Reload the page — confirm the setting persisted (still shows `America/New_York`, timestamps still shifted).
6. Open the "+ New Schedule" modal (Launch tab → Schedules sub-tab) — confirm the cron helper text now reads "...(times in America/New_York)".
7. Paste a non-admin token (or revoke admin) — confirm the timezone card becomes read-only (no dropdown/Save button).
8. Set the timezone back to `UTC` to leave the environment clean for other manual testing.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): add admin-editable app-wide timezone setting UI"
```

---

## Task 8: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ test_artifact_service.py test_exceptions.py -q`
Expected: all pass, no regressions in unrelated suites (particularly `tests/unit/test_scheduler.py`, `tests/test_token_routes.py`, `tests/unit/test_reporting_generator.py`, `tests/unit/test_report_template.py`, `tests/integration/test_api_frontend_smoke.py`).

- [ ] **Step 2: Confirm no leftover debug state**

Run: `git status`
Expected: only the files touched in Tasks 1-7 are modified/added; no stray `__pycache__` or `.db` changes beyond what's already gitignored.

- [ ] **Step 3: Final commit if anything was missed**

```bash
git add -A
git status
# If clean (nothing to commit), skip. Otherwise commit any stragglers with a descriptive message.
```
