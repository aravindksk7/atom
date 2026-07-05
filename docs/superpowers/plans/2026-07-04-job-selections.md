# Job Selections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users save a named, versioned list of jobs (a "Job Selection") once, then launch it on-demand against a single environment or attach it to a cron schedule, and compare any two of its historical runs later by pairing them into the existing mismatch-diff view.

**Architecture:** Two new tables (`job_selections`, `job_selection_versions`) hold the reusable job list independent of environment. `TestRun` gains nullable `selection_id`/`selection_version` columns so any run — ad-hoc or scheduled — can be traced back to the selection that produced it. `ScheduledRun` is refactored to reference a selection + pinned version instead of embedding its own job list. No new comparison logic is added: pairing two runs from a selection's history reuses the existing `/api/compare/mismatch-diff` endpoint.

**Tech Stack:** Python, SQLAlchemy ORM, FastAPI, Pydantic v2, APScheduler, Alpine.js, SQLite (dev) / SQL Server (prod), pytest

**Spec:** `docs/superpowers/specs/2026-07-04-job-selections-design.md`

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `etl_framework/repository/models.py` | Add `JobSelection`, `JobSelectionVersion` ORM models; add `selection_id`/`selection_version` columns to `TestRun` and `ScheduledRun` |
| Modify | `etl_framework/repository/database.py` | Add `CREATE TABLE`/`ALTER TABLE` DDL for new tables/columns; add one-time backfill of legacy `ScheduledRun` rows into selections |
| Modify | `etl_framework/repository/repository.py` | Add `JobSelectionRepository`; extend `RunRepository.create_run` with `selection_id`/`selection_version` params |
| Modify | `api/schemas.py` | Add `JobSelectionCreate`, `JobSelectionUpdate`, `JobSelectionOut`, `JobSelectionDetailOut`, `JobSelectionVersionOut`, `JobSelectionLaunchRequest` |
| Create | `api/routes/selections.py` | `/api/selections` CRUD, version detail, run history, launch |
| Modify | `api/main.py` | Register `selections.router` |
| Modify | `api/routes/schedules.py` | `ScheduleCreate`/`ScheduleOut` reference `selection_id`+`selection_version`; resolve/validate on create & update |
| Modify | `api/services/scheduler.py` | `_run_schedule` resolves the pinned selection version and creates the `TestRun` row (fixing a latent gap where scheduled runs never called `create_run`) before executing |
| Create | `tests/unit/test_job_selections_repository.py` | Model + repository unit tests: CRUD, versioning, archive-blocked-by-schedule |
| Create | `tests/unit/test_selections_routes.py` | Route tests: CRUD, launch (default/explicit version), single-env validation error |
| Create | `tests/unit/test_schedules_selection_refactor.py` | Schedule create/update resolving selection version, version pinning, legacy backfill |
| Create | `tests/integration/test_selection_compare_workflow.py` | End-to-end: launch selection twice against two environments, pair the runs, confirm mismatch-diff works |
| Modify | `frontend/app.js` | New Alpine state + methods for the Job Selections sub-tab, launch modal, run history/compare pairing, schedule modal update |
| Modify | `frontend/index.html` | New "Job Selections" sub-tab markup, launch modal, run history panel, schedule modal update |

---

## Task 1: Data Model

**Files:**
- Modify: `etl_framework/repository/models.py`

- [ ] **Step 1: Add `JobSelection` and `JobSelectionVersion` models**

In `etl_framework/repository/models.py`, add after the `SavedJob` class (after line 44, before `class TestRun(Base):`):

```python
# ---------------------------------------------------------------------------
# Job Selections
# ---------------------------------------------------------------------------

class JobSelection(Base):
    __tablename__ = "job_selections"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=False, default="")
    tags = Column(JSON, nullable=False, default=list)
    archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    versions = relationship(
        "JobSelectionVersion", back_populates="selection",
        cascade="all, delete-orphan", lazy="select",
        order_by="JobSelectionVersion.version_number",
    )


class JobSelectionVersion(Base):
    __tablename__ = "job_selection_versions"

    id = Column(Integer, primary_key=True, index=True)
    selection_id = Column(Integer, ForeignKey("job_selections.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    job_sequence = Column(JSON, nullable=False, default=list)
    run_settings_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    selection = relationship("JobSelection", back_populates="versions")
```

- [ ] **Step 2: Add `selection_id`/`selection_version` to `TestRun`**

In the `TestRun` class (models.py:47-72), add two new columns right after `cancel_requested` (line 66):

```python
    cancel_requested = Column(Boolean, default=False, nullable=False)
    selection_id = Column(Integer, nullable=True, index=True)
    selection_version = Column(Integer, nullable=True)
```

- [ ] **Step 3: Add `selection_id`/`selection_version` to `ScheduledRun`**

In the `ScheduledRun` class (models.py:215-229), add two new columns right after `created_at` (line 228):

```python
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    selection_id = Column(Integer, nullable=True, index=True)
    selection_version = Column(Integer, nullable=True)
```

Leave the existing `job_sequence`/`run_settings_json` columns on `ScheduledRun` in place — they stop being populated by new code (Task 7) but are not dropped, since SQLite can't cheaply drop a `NOT NULL` column without a table rebuild. SQLAlchemy's `default=list`/`default=dict` on those columns fills them automatically when new code no longer sets them explicitly.

- [ ] **Step 4: Verify models import cleanly**

Run: `python -c "from etl_framework.repository import models; print(models.JobSelection, models.JobSelectionVersion)"`
Expected: prints the two classes with no import errors.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/repository/models.py
git commit -m "feat: add JobSelection/JobSelectionVersion models and selection columns"
```

---

## Task 2: Database Bootstrap

**Files:**
- Modify: `etl_framework/repository/database.py`

- [ ] **Step 1: Capture `scheduled_runs` columns alongside the existing column snapshots**

In `_ensure_compare_columns`, replace:

```python
    test_run_cols = {col["name"] for col in inspector.get_columns("test_runs")}
    test_result_cols = {col["name"] for col in inspector.get_columns("test_results")}
    mismatch_cols = {col["name"] for col in inspector.get_columns("mismatch_details")}
```

with:

```python
    test_run_cols = {col["name"] for col in inspector.get_columns("test_runs")}
    test_result_cols = {col["name"] for col in inspector.get_columns("test_results")}
    mismatch_cols = {col["name"] for col in inspector.get_columns("mismatch_details")}
    scheduled_run_cols = (
        {col["name"] for col in inspector.get_columns("scheduled_runs")}
        if "scheduled_runs" in tables else set()
    )
```

- [ ] **Step 2: Add DDL for the new tables and columns**

At the end of the `with bind.begin() as conn:` block (after the `contract_breaches` block, which is the last statement in the function), add:

```python
        # --- Job Selections ---
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS job_selections ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name VARCHAR(255) NOT NULL UNIQUE, "
            "description TEXT NOT NULL DEFAULT '', "
            "tags JSON, "
            "archived BOOLEAN NOT NULL DEFAULT 0, "
            "created_at DATETIME, "
            "updated_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_job_selections_name ON job_selections (name)"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS job_selection_versions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "selection_id INTEGER NOT NULL REFERENCES job_selections(id) ON DELETE CASCADE, "
            "version_number INTEGER NOT NULL, "
            "job_sequence JSON, "
            "run_settings_json JSON, "
            "created_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_job_selection_versions_selection_id "
            "ON job_selection_versions (selection_id)"
        ))

        if "selection_id" not in test_run_cols:
            conn.execute(text("ALTER TABLE test_runs ADD COLUMN selection_id INTEGER"))
        if "selection_version" not in test_run_cols:
            conn.execute(text("ALTER TABLE test_runs ADD COLUMN selection_version INTEGER"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_test_runs_selection_id ON test_runs (selection_id)"
        ))

        if scheduled_run_cols:
            if "selection_id" not in scheduled_run_cols:
                conn.execute(text("ALTER TABLE scheduled_runs ADD COLUMN selection_id INTEGER"))
            if "selection_version" not in scheduled_run_cols:
                conn.execute(text("ALTER TABLE scheduled_runs ADD COLUMN selection_version INTEGER"))
```

- [ ] **Step 3: Add the one-time legacy-schedule backfill function**

At the end of `database.py`, after `_ensure_compare_columns`, add:

```python
def _backfill_schedule_selections(bind) -> None:
    """One-time backfill: give every pre-existing ScheduledRun row a JobSelection.

    Idempotent — only touches rows where selection_id is still NULL, so this
    is a no-op once every schedule has been migrated or created fresh.
    """
    if bind.dialect.name != "sqlite":
        return
    inspector = inspect(bind)
    if "scheduled_runs" not in set(inspector.get_table_names()):
        return
    cols = {col["name"] for col in inspector.get_columns("scheduled_runs")}
    if "selection_id" not in cols:
        return

    from sqlalchemy.orm import Session
    from etl_framework.repository.models import ScheduledRun, JobSelection, JobSelectionVersion

    with Session(bind) as db:
        legacy = db.query(ScheduledRun).filter(ScheduledRun.selection_id.is_(None)).all()
        if not legacy:
            return
        for sched in legacy:
            selection = JobSelection(
                name=f"{sched.name} (migrated)",
                description="Auto-created from a pre-existing schedule.",
            )
            db.add(selection)
            db.flush()
            db.add(JobSelectionVersion(
                selection_id=selection.id,
                version_number=1,
                job_sequence=sched.job_sequence or [],
                run_settings_json=sched.run_settings_json or {},
            ))
            sched.selection_id = selection.id
            sched.selection_version = 1
        db.commit()
```

- [ ] **Step 4: Call the backfill from `init_db`**

Change:

```python
def init_db() -> None:
    from etl_framework.repository import models  # noqa: F401 — registers all ORM models
    from etl_framework.repository import contract_models  # noqa: F401 — registers contract ORM models
    Base.metadata.create_all(bind=engine)
    _ensure_compare_columns(engine)
```

to:

```python
def init_db() -> None:
    from etl_framework.repository import models  # noqa: F401 — registers all ORM models
    from etl_framework.repository import contract_models  # noqa: F401 — registers contract ORM models
    Base.metadata.create_all(bind=engine)
    _ensure_compare_columns(engine)
    _backfill_schedule_selections(engine)
```

- [ ] **Step 5: Verify bootstrap runs cleanly**

Run: `python -c "from etl_framework.repository.database import init_db; init_db(); print('ok')"`
Expected: prints `ok` with no exceptions (uses the default `sqlite:///./etl_framework.db` unless `ETL_DATABASE_URL` is set).

- [ ] **Step 6: Commit**

```bash
git add etl_framework/repository/database.py
git commit -m "feat: bootstrap job_selections tables and backfill legacy schedules"
```

---

## Task 3: Repository Layer

**Files:**
- Modify: `etl_framework/repository/repository.py`
- Test: `tests/unit/test_job_selections_repository.py`

- [ ] **Step 1: Write the failing repository tests**

Create `tests/unit/test_job_selections_repository.py`:

```python
"""Tests for JobSelectionRepository and the selection-aware RunRepository.create_run."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    JobSelectionRepository, RunRepository, ScheduleRepository,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_create_makes_version_1():
    db = _session()
    repo = JobSelectionRepository(db)
    sel = repo.create(
        name="nightly-set", description="desc", tags=["daily"],
        job_sequence=[{"job_name": "orders"}], run_settings={"execution_mode": "parallel"},
    )
    assert sel.id is not None
    latest = repo.latest_version(sel.id)
    assert latest.version_number == 1
    assert latest.job_sequence == [{"job_name": "orders"}]


def test_create_new_version_increments_and_keeps_old():
    db = _session()
    repo = JobSelectionRepository(db)
    sel = repo.create(name="s", description="", tags=[], job_sequence=["a"], run_settings={})
    v2 = repo.create_new_version(sel.id, job_sequence=["a", "b"], run_settings=None)
    assert v2.version_number == 2
    v1 = repo.get_version(sel.id, 1)
    assert v1.job_sequence == ["a"]
    assert repo.get_version(sel.id, 2).job_sequence == ["a", "b"]


def test_update_metadata_does_not_create_new_version():
    db = _session()
    repo = JobSelectionRepository(db)
    sel = repo.create(name="s", description="", tags=[], job_sequence=["a"], run_settings={})
    repo.update_metadata(sel.id, name="renamed")
    assert repo.latest_version(sel.id).version_number == 1
    assert repo.get(sel.id).name == "renamed"


def test_archive_blocked_by_enabled_schedule():
    db = _session()
    repo = JobSelectionRepository(db)
    sel = repo.create(name="s", description="", tags=[], job_sequence=["a"], run_settings={})
    ScheduleRepository(db).create({
        "name": "sched", "cron_expr": "0 6 * * *",
        "selection_id": sel.id, "selection_version": 1,
        "source_env": "dev", "target_env": "prod", "enabled": True,
    })
    assert repo.active_schedule_count(sel.id) == 1
    with pytest.raises(ValueError):
        repo.archive_or_raise(sel.id)


def test_archive_succeeds_when_no_active_schedule():
    db = _session()
    repo = JobSelectionRepository(db)
    sel = repo.create(name="s", description="", tags=[], job_sequence=["a"], run_settings={})
    repo.archive_or_raise(sel.id)
    assert repo.get(sel.id).archived is True


def test_runs_for_selection_filters_by_selection_id():
    db = _session()
    repo = JobSelectionRepository(db)
    sel = repo.create(name="s", description="", tags=[], job_sequence=["a"], run_settings={})
    run_repo = RunRepository(db)
    run_repo.create_run(run_id="r1", source_env="dev", target_env="",
                         selection_id=sel.id, selection_version=1)
    run_repo.create_run(run_id="r2", source_env="qa", target_env="")
    runs = repo.runs_for_selection(sel.id)
    assert [r.run_id for r in runs] == ["r1"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_job_selections_repository.py -v`
Expected: `ImportError: cannot import name 'JobSelectionRepository'` (or `NameError`/collection error) — the repository class doesn't exist yet.

- [ ] **Step 3: Add `JobSelectionRepository` and extend `RunRepository.create_run`**

In `etl_framework/repository/repository.py`, update the models import at the top (line 6-10) to include the new models:

```python
from etl_framework.repository.models import (
    SavedConfig, SavedJob, TestRun, TestResult, MismatchDetail,
    ApiToken, NotificationHook, NotificationDelivery, ScheduledRun, JobLineageEdge, AuditEvent,
    RunStep, JobSelection, JobSelectionVersion, TERMINAL_STATUSES,
)
```

Extend `RunRepository.create_run` (repository.py:118-139) to accept the two new optional params:

```python
    def create_run(
        self,
        run_id: str,
        source_env: str,
        target_env: str,
        config_snapshot: dict | None = None,
        run_type: str = "reconciliation",
        pair_id: str | None = None,
        selection_id: int | None = None,
        selection_version: int | None = None,
    ) -> TestRun:
        run = TestRun(
            run_id=run_id,
            status="PENDING",
            source_env=source_env,
            target_env=target_env,
            config_snapshot=config_snapshot,
            run_type=run_type,
            pair_id=pair_id,
            selection_id=selection_id,
            selection_version=selection_version,
        )
        self._db.add(run)
        self._db.commit()
        self._db.refresh(run)
        return run
```

Add `JobSelectionRepository` right after `JobRepository` (after line 111, before `class RunRepository:`):

```python
class JobSelectionRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(
        self, name: str, description: str, tags: list[str],
        job_sequence: list, run_settings: dict,
    ) -> JobSelection:
        selection = JobSelection(name=name, description=description, tags=tags or [])
        self._db.add(selection)
        self._db.flush()
        self._db.add(JobSelectionVersion(
            selection_id=selection.id, version_number=1,
            job_sequence=job_sequence or [], run_settings_json=run_settings or {},
        ))
        self._db.commit()
        self._db.refresh(selection)
        return selection

    def get(self, selection_id: int) -> JobSelection | None:
        return self._db.get(JobSelection, selection_id)

    def get_by_name(self, name: str) -> JobSelection | None:
        return self._db.query(JobSelection).filter_by(name=name).first()

    def list(self, include_archived: bool = False) -> list[JobSelection]:
        q = self._db.query(JobSelection)
        if not include_archived:
            q = q.filter(JobSelection.archived.is_(False))
        return q.order_by(JobSelection.name).all()

    def latest_version(self, selection_id: int) -> JobSelectionVersion | None:
        return (
            self._db.query(JobSelectionVersion)
            .filter_by(selection_id=selection_id)
            .order_by(JobSelectionVersion.version_number.desc())
            .first()
        )

    def get_version(self, selection_id: int, version_number: int) -> JobSelectionVersion | None:
        return (
            self._db.query(JobSelectionVersion)
            .filter_by(selection_id=selection_id, version_number=version_number)
            .first()
        )

    def update_metadata(
        self, selection_id: int, name: str | None = None,
        description: str | None = None, tags: list[str] | None = None,
    ) -> JobSelection | None:
        from datetime import datetime, timezone
        selection = self.get(selection_id)
        if selection is None:
            return None
        if name is not None:
            selection.name = name
        if description is not None:
            selection.description = description
        if tags is not None:
            selection.tags = tags
        selection.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(selection)
        return selection

    def create_new_version(
        self, selection_id: int, job_sequence: list | None, run_settings: dict | None,
    ) -> JobSelectionVersion | None:
        from datetime import datetime, timezone
        selection = self.get(selection_id)
        if selection is None:
            return None
        current = self.latest_version(selection_id)
        version = JobSelectionVersion(
            selection_id=selection_id,
            version_number=(current.version_number + 1 if current else 1),
            job_sequence=(
                job_sequence if job_sequence is not None
                else (current.job_sequence if current else [])
            ),
            run_settings_json=(
                run_settings if run_settings is not None
                else (current.run_settings_json if current else {})
            ),
        )
        self._db.add(version)
        selection.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(version)
        return version

    def active_schedule_count(self, selection_id: int) -> int:
        return (
            self._db.query(ScheduledRun)
            .filter(ScheduledRun.selection_id == selection_id, ScheduledRun.enabled.is_(True))
            .count()
        )

    def archive_or_raise(self, selection_id: int) -> JobSelection:
        if self.active_schedule_count(selection_id) > 0:
            raise ValueError("Cannot archive: an enabled schedule still references this selection")
        selection = self.get(selection_id)
        selection.archived = True
        self._db.commit()
        self._db.refresh(selection)
        return selection

    def runs_for_selection(self, selection_id: int, limit: int = 100) -> list[TestRun]:
        return (
            self._db.query(TestRun)
            .filter(TestRun.selection_id == selection_id)
            .order_by(TestRun.id.desc())
            .limit(limit)
            .all()
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_job_selections_repository.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/repository/repository.py tests/unit/test_job_selections_repository.py
git commit -m "feat: add JobSelectionRepository and selection-aware run creation"
```

---

## Task 4: Pydantic Schemas

**Files:**
- Modify: `api/schemas.py`

- [ ] **Step 1: Add the Job Selection schemas**

In `api/schemas.py`, add after the `JobDefinition` class and its validator (after line 360, before the `# --- Adapter / SAP BO / Automic schemas ---` comment on line 363):

```python
# ---------------------------------------------------------------------------
# Job Selections
# ---------------------------------------------------------------------------

class JobSelectionVersionOut(BaseModel):
    version_number: int
    job_sequence: list[str | SequenceStep]
    run_settings: RunSettings
    created_at: datetime

    model_config = {"from_attributes": True}


class JobSelectionCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    job_sequence: list[str | SequenceStep] = Field(default_factory=list)
    run_settings: RunSettings = Field(default_factory=RunSettings)


class JobSelectionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    job_sequence: list[str | SequenceStep] | None = None
    run_settings: RunSettings | None = None


class JobSelectionOut(BaseModel):
    id: int
    name: str
    description: str
    tags: list[str]
    archived: bool
    latest_version: int
    job_count: int
    created_at: datetime
    updated_at: datetime


class JobSelectionDetailOut(JobSelectionOut):
    versions: list[JobSelectionVersionOut]


class JobSelectionLaunchRequest(BaseModel):
    source_env: str
    target_env: str = ""
    source_connection: str | None = None
    target_connection: str | None = None
    config_id: int | None = None
    config_data: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None
```

Note: `JobSelectionVersionOut.run_settings` reads from the ORM's `run_settings_json` dict column; the route layer (Task 5) constructs it explicitly rather than relying on `from_attributes` field-name matching, since the column name differs from the schema field name.

- [ ] **Step 2: Verify schemas import cleanly**

Run: `python -c "from api.schemas import JobSelectionCreate, JobSelectionOut, JobSelectionDetailOut, JobSelectionLaunchRequest; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add api/schemas.py
git commit -m "feat: add Job Selection Pydantic schemas"
```

---

## Task 5: Selections Router

**Files:**
- Create: `api/routes/selections.py`
- Modify: `api/main.py`

- [ ] **Step 1: Create the router**

Create `api/routes/selections.py`:

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import (
    JobSelectionCreate,
    JobSelectionDetailOut,
    JobSelectionLaunchRequest,
    JobSelectionOut,
    JobSelectionUpdate,
    JobSelectionVersionOut,
    RunStatusOut,
    RunTrigger,
)
from api.routes.runs import _execute_run, _snapshot_from_trigger
from api.services.audit_service import AuditService
from etl_framework.repository.repository import JobRepository, JobSelectionRepository, RunRepository

router = APIRouter(tags=["selections"])

# Job types whose execution only touches one environment (per the approved
# design spec); everything else needs a target_env to compare against.
_SINGLE_ENV_JOB_TYPES = {"bo_report", "freshness", "profile", "automic_job", "dbt_artifact", "schema_snapshot"}


def _selection_out(selection) -> JobSelectionOut:
    latest = selection.versions[-1] if selection.versions else None
    return JobSelectionOut(
        id=selection.id,
        name=selection.name,
        description=selection.description,
        tags=selection.tags or [],
        archived=selection.archived,
        latest_version=latest.version_number if latest else 0,
        job_count=len(latest.job_sequence) if latest else 0,
        created_at=selection.created_at,
        updated_at=selection.updated_at,
    )


def _version_out(version) -> JobSelectionVersionOut:
    return JobSelectionVersionOut(
        version_number=version.version_number,
        job_sequence=version.job_sequence or [],
        run_settings=version.run_settings_json or {},
        created_at=version.created_at,
    )


def _detail_out(selection) -> JobSelectionDetailOut:
    base = _selection_out(selection)
    return JobSelectionDetailOut(
        **base.model_dump(),
        versions=[_version_out(v) for v in selection.versions],
    )


def _dump_job_sequence(job_sequence) -> list:
    return [s.model_dump() if hasattr(s, "model_dump") else s for s in job_sequence]


@router.get("", response_model=list[JobSelectionOut])
def list_selections(db: Session = Depends(get_session)):
    return [_selection_out(s) for s in JobSelectionRepository(db).list()]


@router.post("", response_model=JobSelectionOut, status_code=201)
def create_selection(body: JobSelectionCreate, request: Request, db: Session = Depends(get_session)):
    repo = JobSelectionRepository(db)
    if repo.get_by_name(body.name) is not None:
        raise HTTPException(status_code=409, detail="A job selection with this name already exists")
    job_sequence = _dump_job_sequence(body.job_sequence)
    selection = repo.create(
        name=body.name, description=body.description, tags=body.tags,
        job_sequence=job_sequence, run_settings=body.run_settings.model_dump(),
    )
    AuditService(db).log(
        request, "selection.created", "job_selection", selection.id,
        {"name": selection.name, "job_count": len(job_sequence)},
    )
    return _selection_out(selection)


@router.get("/{selection_id}", response_model=JobSelectionDetailOut)
def get_selection(selection_id: int, db: Session = Depends(get_session)):
    selection = JobSelectionRepository(db).get(selection_id)
    if selection is None:
        raise HTTPException(status_code=404, detail="Job selection not found")
    return _detail_out(selection)


@router.get("/{selection_id}/versions/{version_number}", response_model=JobSelectionVersionOut)
def get_selection_version(selection_id: int, version_number: int, db: Session = Depends(get_session)):
    repo = JobSelectionRepository(db)
    if repo.get(selection_id) is None:
        raise HTTPException(status_code=404, detail="Job selection not found")
    version = repo.get_version(selection_id, version_number)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return _version_out(version)


@router.put("/{selection_id}", response_model=JobSelectionDetailOut)
def update_selection(
    selection_id: int, body: JobSelectionUpdate, request: Request, db: Session = Depends(get_session)
):
    repo = JobSelectionRepository(db)
    selection = repo.get(selection_id)
    if selection is None:
        raise HTTPException(status_code=404, detail="Job selection not found")

    repo.update_metadata(selection_id, name=body.name, description=body.description, tags=body.tags)

    if body.job_sequence is not None or body.run_settings is not None:
        job_sequence = _dump_job_sequence(body.job_sequence) if body.job_sequence is not None else None
        run_settings = body.run_settings.model_dump() if body.run_settings is not None else None
        repo.create_new_version(selection_id, job_sequence, run_settings)

    db.refresh(selection)
    AuditService(db).log(request, "selection.updated", "job_selection", selection_id, {"name": selection.name})
    return _detail_out(selection)


@router.delete("/{selection_id}", status_code=204)
def archive_selection(selection_id: int, request: Request, db: Session = Depends(get_session)):
    repo = JobSelectionRepository(db)
    if repo.get(selection_id) is None:
        raise HTTPException(status_code=404, detail="Job selection not found")
    try:
        repo.archive_or_raise(selection_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    AuditService(db).log(request, "selection.archived", "job_selection", selection_id)


@router.get("/{selection_id}/runs", response_model=list[RunStatusOut])
def list_selection_runs(selection_id: int, db: Session = Depends(get_session)):
    repo = JobSelectionRepository(db)
    if repo.get(selection_id) is None:
        raise HTTPException(status_code=404, detail="Job selection not found")
    return [
        RunStatusOut(
            run_id=r.run_id, status=r.status, started_at=r.started_at,
            completed_at=r.completed_at, total_tests=r.total_tests,
            passed=r.passed, failed=r.failed, slow=r.slow, error=r.error,
            run_type=r.run_type, pair_id=r.pair_id,
        )
        for r in repo.runs_for_selection(selection_id)
    ]


def _job_name_of(step) -> str:
    if isinstance(step, dict):
        return step.get("job_name", "")
    if hasattr(step, "job_name"):
        return step.job_name
    return str(step)


def _validate_env_requirements(job_sequence: list, jobs_by_name: dict, target_env: str) -> None:
    if target_env:
        return
    for step in job_sequence:
        job_name = _job_name_of(step)
        job = jobs_by_name.get(job_name)
        if job is not None and job.job_type not in _SINGLE_ENV_JOB_TYPES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Job '{job_name}' (type '{job.job_type}') requires a target_env; "
                    "only single-environment job types can run with target_env omitted"
                ),
            )


@router.post("/{selection_id}/launch", response_model=RunStatusOut, status_code=202)
def launch_selection(
    selection_id: int,
    body: JobSelectionLaunchRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_session),
):
    repo = JobSelectionRepository(db)
    selection = repo.get(selection_id)
    if selection is None:
        raise HTTPException(status_code=404, detail="Job selection not found")

    version = (
        repo.get_version(selection_id, body.version) if body.version is not None
        else repo.latest_version(selection_id)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")

    jobs_by_name = {j.name: j for j in JobRepository(db).list()}
    _validate_env_requirements(version.job_sequence or [], jobs_by_name, body.target_env)

    trigger = RunTrigger(
        source_env=body.source_env,
        target_env=body.target_env,
        source_connection=body.source_connection,
        target_connection=body.target_connection,
        job_sequence=version.job_sequence or [],
        config_id=body.config_id,
        config_data=body.config_data,
        run_settings=version.run_settings_json or {},
    )

    run_id = str(uuid.uuid4())
    ordered_jobs = trigger.job_sequence
    config_snapshot = _snapshot_from_trigger(trigger, db)
    config_snapshot["job_sequence"] = _dump_job_sequence(ordered_jobs)
    config_snapshot["run_settings"] = trigger.run_settings.model_dump()

    RunRepository(db).create_run(
        run_id=run_id,
        source_env=trigger.source_env,
        target_env=trigger.target_env,
        config_snapshot=config_snapshot or None,
        selection_id=selection_id,
        selection_version=version.version_number,
    )
    AuditService(db).log(
        request, "selection.launched", "job_selection", selection_id,
        {
            "run_id": run_id, "source_env": trigger.source_env,
            "target_env": trigger.target_env, "version": version.version_number,
        },
    )
    background_tasks.add_task(
        _execute_run, run_id, ordered_jobs, trigger.source_env, trigger.target_env,
        trigger.run_settings, config_snapshot,
    )
    return RunStatusOut(run_id=run_id, status="PENDING")
```

- [ ] **Step 2: Mount the router**

In `api/main.py`, change the import line (line 10):

```python
from api.routes import configs, runs, jobs, health as health_routes, adapters, compare as compare_routes
```

to:

```python
from api.routes import configs, runs, jobs, health as health_routes, adapters, compare as compare_routes
from api.routes import selections as selections_routes
```

And add after `app.include_router(logs_routes.router, prefix="/api/logs")` (line 56):

```python
app.include_router(selections_routes.router, prefix="/api/selections")
```

- [ ] **Step 3: Verify the app starts and the router is mounted**

Run: `python -c "from api.main import app; print([r.path for r in app.routes if 'selections' in r.path])"`
Expected: prints a non-empty list of paths starting with `/api/selections`.

- [ ] **Step 4: Commit**

```bash
git add api/routes/selections.py api/main.py
git commit -m "feat: add /api/selections CRUD, run history, and launch endpoints"
```

---

## Task 6: Route Tests for Selections

**Files:**
- Test: `tests/unit/test_selections_routes.py`

- [ ] **Step 1: Write the tests**

Create `tests/unit/test_selections_routes.py`:

```python
"""Tests for /api/selections CRUD, run history, and launch endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import TokenRepository, JobRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.routes.selections._execute_run", lambda *a, **k: None)

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        JobRepository(db).create({
            "name": "orders_recon", "description": "", "tags": [],
            "job_type": "reconciliation", "query": "SELECT 1", "key_columns": ["id"],
            "exclude_columns": [], "params": {}, "enabled": True,
        })
        JobRepository(db).create({
            "name": "bo_job", "description": "", "tags": [],
            "job_type": "bo_report", "query": "", "key_columns": ["region"],
            "exclude_columns": [], "params": {"report_id": "R1"}, "enabled": True,
        })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _create_selection(client, name="nightly-set", jobs=None):
    resp = client.post("/api/selections", json={
        "name": name, "description": "d", "tags": ["t"],
        "job_sequence": jobs or ["orders_recon"],
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_and_get_selection(client):
    created = _create_selection(client)
    resp = client.get(f"/api/selections/{created['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "nightly-set"
    assert len(data["versions"]) == 1
    assert data["versions"][0]["version_number"] == 1


def test_duplicate_name_rejected(client):
    _create_selection(client)
    resp = client.post("/api/selections", json={"name": "nightly-set", "job_sequence": ["orders_recon"]})
    assert resp.status_code == 409


def test_update_job_sequence_creates_new_version(client):
    created = _create_selection(client)
    resp = client.put(f"/api/selections/{created['id']}", json={"job_sequence": ["orders_recon", "bo_job"]})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["versions"]) == 2
    assert data["versions"][0]["job_sequence"] != data["versions"][1]["job_sequence"]


def test_update_metadata_only_does_not_create_new_version(client):
    created = _create_selection(client)
    resp = client.put(f"/api/selections/{created['id']}", json={"description": "new desc"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["versions"]) == 1
    assert data["description"] == "new desc"


def test_archive_succeeds_with_no_schedules(client):
    created = _create_selection(client)
    resp = client.delete(f"/api/selections/{created['id']}")
    assert resp.status_code == 204


def test_launch_creates_run_with_selection_fields(client):
    created = _create_selection(client)
    resp = client.post(f"/api/selections/{created['id']}/launch", json={"source_env": "dev", "target_env": "qa"})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    runs_resp = client.get(f"/api/selections/{created['id']}/runs")
    assert runs_resp.status_code == 200
    assert [r["run_id"] for r in runs_resp.json()] == [run_id]


def test_launch_single_env_job_type_succeeds_without_target(client):
    created = _create_selection(client, name="bo-only", jobs=["bo_job"])
    resp = client.post(f"/api/selections/{created['id']}/launch", json={"source_env": "dev"})
    assert resp.status_code == 202


def test_launch_dual_env_job_type_without_target_fails_clearly(client):
    created = _create_selection(client, name="recon-only", jobs=["orders_recon"])
    resp = client.post(f"/api/selections/{created['id']}/launch", json={"source_env": "dev"})
    assert resp.status_code == 422
    assert "orders_recon" in resp.json()["detail"]
    assert "target_env" in resp.json()["detail"]
```

- [ ] **Step 2: Run to verify they fail first, then pass**

Run: `pytest tests/unit/test_selections_routes.py -v`
Expected before any bug fixes: all PASS immediately, since Tasks 4-5 already implemented the routes. If any test fails, fix the route/schema code from Task 5 (not the test) until all 8 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_selections_routes.py
git commit -m "test: add coverage for /api/selections CRUD, run history, and launch"
```

---

## Task 7: Schedules Refactor

**Files:**
- Modify: `api/routes/schedules.py`
- Test: `tests/unit/test_schedules_selection_refactor.py`

- [ ] **Step 1: Rewrite `ScheduleCreate`/`ScheduleOut` and the create/update routes**

Replace the full contents of `api/routes/schedules.py` with:

```python
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.routes.selections import _validate_env_requirements
from etl_framework.repository.repository import JobRepository, JobSelectionRepository, ScheduleRepository
import api.services.scheduler as _sched_svc
from api.services.audit_service import AuditService

router = APIRouter(tags=["schedules"])


def _validate_cron(expr: str) -> str:
    try:
        from croniter import croniter
        if not croniter.is_valid(expr):
            raise ValueError("invalid")
    except ImportError:
        pass  # croniter not installed — skip validation
    return expr


class ScheduleCreate(BaseModel):
    name: str
    cron_expr: str
    selection_id: int
    selection_version: int | None = None
    source_env: str
    target_env: str = ""
    enabled: bool = True

    @field_validator("cron_expr")
    @classmethod
    def check_cron(cls, v: str) -> str:
        return _validate_cron(v)


class ScheduleOut(BaseModel):
    id: int
    name: str
    cron_expr: str
    selection_id: int
    selection_version: int
    source_env: str
    target_env: str
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    model_config = {"from_attributes": True}


def _resolve_selection_version(db: Session, selection_id: int, version: int | None) -> int:
    sel_repo = JobSelectionRepository(db)
    if sel_repo.get(selection_id) is None:
        raise HTTPException(status_code=404, detail="Job selection not found")
    if version is None:
        latest = sel_repo.latest_version(selection_id)
        if latest is None:
            raise HTTPException(status_code=422, detail="Job selection has no versions")
        return latest.version_number
    if sel_repo.get_version(selection_id, version) is None:
        raise HTTPException(status_code=404, detail="Selection version not found")
    return version


def _resolve_and_validate(db: Session, body: "ScheduleCreate") -> int:
    """Resolve the target selection_version and enforce the same single/dual-env
    job-type check used by ad-hoc launches, so a schedule can't be saved pointing
    at a selection that structurally needs a target_env it doesn't have."""
    version_number = _resolve_selection_version(db, body.selection_id, body.selection_version)
    version = JobSelectionRepository(db).get_version(body.selection_id, version_number)
    jobs_by_name = {j.name: j for j in JobRepository(db).list()}
    _validate_env_requirements(version.job_sequence or [], jobs_by_name, body.target_env)
    return version_number


@router.get("", response_model=list[ScheduleOut])
def list_schedules(db: Session = Depends(get_session)):
    return ScheduleRepository(db).list()


@router.post("", response_model=ScheduleOut, status_code=201)
def create_schedule(body: ScheduleCreate, request: Request, db: Session = Depends(get_session)):
    repo = ScheduleRepository(db)
    if repo.get_by_name(body.name):
        raise HTTPException(status_code=409, detail="Schedule name already exists")
    data = body.model_dump()
    data["selection_version"] = _resolve_and_validate(db, body)
    sched = repo.create(data)
    _sched_svc.add_job(sched)
    AuditService(db).log(
        request, "schedule.created", "schedule", sched.id,
        {"name": sched.name, "cron_expr": sched.cron_expr, "selection_id": sched.selection_id},
    )
    return sched


@router.put("/{schedule_id}", response_model=ScheduleOut)
def update_schedule(
    schedule_id: int, body: ScheduleCreate, request: Request, db: Session = Depends(get_session)
):
    data = body.model_dump()
    data["selection_version"] = _resolve_and_validate(db, body)
    repo = ScheduleRepository(db)
    sched = repo.update(schedule_id, data)
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    _sched_svc.reload_job(sched)
    AuditService(db).log(
        request, "schedule.updated", "schedule", sched.id,
        {"name": sched.name, "cron_expr": sched.cron_expr, "enabled": sched.enabled},
    )
    return sched


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, request: Request, db: Session = Depends(get_session)):
    if not ScheduleRepository(db).delete(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    _sched_svc.remove_job(schedule_id)
    AuditService(db).log(request, "schedule.deleted", "schedule", schedule_id)


@router.post("/{schedule_id}/run-now", status_code=202)
def run_now(schedule_id: int, request: Request, db: Session = Depends(get_session)):
    sched = ScheduleRepository(db).get(schedule_id)
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    import threading
    from api.services.scheduler import _run_schedule
    threading.Thread(
        target=_run_schedule, args=(sched.id, sched.name), daemon=True
    ).start()
    AuditService(db).log(request, "schedule.run_now", "schedule", schedule_id)
    return {"detail": f"Schedule '{sched.name}' triggered manually"}
```

- [ ] **Step 2: Write tests for the refactored routes**

Create `tests/unit/test_schedules_selection_refactor.py`:

```python
"""Tests for schedules referencing Job Selections (pinned version resolution)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import TokenRepository, JobRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.services.scheduler.add_job", lambda *a, **k: None)
    monkeypatch.setattr("api.services.scheduler.reload_job", lambda *a, **k: None)

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        JobRepository(db).create({
            "name": "orders_recon", "description": "", "tags": [],
            "job_type": "reconciliation", "query": "SELECT 1", "key_columns": ["id"],
            "exclude_columns": [], "params": {}, "enabled": True,
        })
        JobRepository(db).create({
            "name": "bo_job", "description": "", "tags": [],
            "job_type": "bo_report", "query": "", "key_columns": ["region"],
            "exclude_columns": [], "params": {"report_id": "R1"}, "enabled": True,
        })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _create_selection(client, name="s1"):
    resp = client.post("/api/selections", json={"name": name, "job_sequence": ["orders_recon"]})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_schedule_defaults_to_latest_version(client):
    sel = _create_selection(client)
    resp = client.post("/api/schedules", json={
        "name": "nightly", "cron_expr": "0 6 * * *",
        "selection_id": sel["id"], "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["selection_version"] == 1


def test_create_schedule_missing_selection_returns_404(client):
    resp = client.post("/api/schedules", json={
        "name": "nightly", "cron_expr": "0 6 * * *",
        "selection_id": 9999, "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 404


def test_schedule_stays_pinned_after_selection_is_edited(client):
    sel = _create_selection(client)
    sched_resp = client.post("/api/schedules", json={
        "name": "nightly", "cron_expr": "0 6 * * *",
        "selection_id": sel["id"], "source_env": "dev", "target_env": "prod",
    })
    assert sched_resp.json()["selection_version"] == 1

    client.put(f"/api/selections/{sel['id']}", json={"job_sequence": ["orders_recon", "bo_job"]})

    schedules = client.get("/api/schedules").json()
    assert schedules[0]["selection_version"] == 1


def test_target_env_optional_for_single_env_schedule(client):
    resp_sel = client.post("/api/selections", json={"name": "bo-set", "job_sequence": ["bo_job"]})
    sel_id = resp_sel.json()["id"]
    resp = client.post("/api/schedules", json={
        "name": "bo-nightly", "cron_expr": "0 6 * * *",
        "selection_id": sel_id, "source_env": "dev",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["target_env"] == ""


def test_create_schedule_dual_env_job_without_target_fails_clearly(client):
    resp_sel = client.post("/api/selections", json={"name": "recon-set", "job_sequence": ["orders_recon"]})
    sel_id = resp_sel.json()["id"]
    resp = client.post("/api/schedules", json={
        "name": "recon-nightly", "cron_expr": "0 6 * * *",
        "selection_id": sel_id, "source_env": "dev",
    })
    assert resp.status_code == 422
    assert "orders_recon" in resp.json()["detail"]
    assert "target_env" in resp.json()["detail"]
```

- [ ] **Step 3: Run the tests**

Run: `pytest tests/unit/test_schedules_selection_refactor.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add api/routes/schedules.py tests/unit/test_schedules_selection_refactor.py
git commit -m "feat: refactor schedules to reference a pinned Job Selection version"
```

---

## Task 8: Scheduler Refactor

**Files:**
- Modify: `api/services/scheduler.py`
- Modify: `tests/unit/test_repository.py`

- [ ] **Step 1: Rewrite `_run_schedule` to resolve the pinned version and create the `TestRun` row**

In `api/services/scheduler.py`, replace the `_run_schedule` function (lines 24-52) with:

```python
def _run_schedule(schedule_id: int, name: str) -> None:
    """Called by APScheduler; runs inside a daemon thread."""
    import uuid as _uuid
    from etl_framework.repository.database import SessionLocal
    from etl_framework.repository.repository import ScheduleRepository, JobSelectionRepository, RunRepository
    from api.routes.runs import _execute_run, _snapshot_from_trigger
    from api.schemas import RunTrigger

    db = SessionLocal()
    try:
        repo = ScheduleRepository(db)
        sched = repo.get(schedule_id)
        if sched is None or not sched.enabled:
            return

        sel_repo = JobSelectionRepository(db)
        version = sel_repo.get_version(sched.selection_id, sched.selection_version)
        if version is None:
            logger.error(
                "Schedule '%s' references missing selection %s v%s; skipping run",
                name, sched.selection_id, sched.selection_version,
            )
            return

        trigger = RunTrigger(
            source_env=sched.source_env,
            target_env=sched.target_env,
            job_sequence=version.job_sequence or [],
            run_settings=version.run_settings_json or {},
        )
        run_id = str(_uuid.uuid4())
        config_snapshot = _snapshot_from_trigger(trigger, db)
        config_snapshot["job_sequence"] = [
            s.model_dump() if hasattr(s, "model_dump") else s for s in trigger.job_sequence
        ]
        config_snapshot["run_settings"] = trigger.run_settings.model_dump()

        RunRepository(db).create_run(
            run_id=run_id,
            source_env=trigger.source_env,
            target_env=trigger.target_env,
            config_snapshot=config_snapshot,
            selection_id=sched.selection_id,
            selection_version=sched.selection_version,
        )
        _execute_run(
            run_id=run_id,
            job_sequence=trigger.job_sequence,
            source_env=trigger.source_env,
            target_env=trigger.target_env,
            run_settings=trigger.run_settings,
            config_snapshot=config_snapshot,
        )
        repo.touch(schedule_id, last_run_at=datetime.now(timezone.utc))
        logger.info("Scheduled run '%s' started as %s", name, run_id)
    except Exception as exc:
        logger.exception("Scheduled run '%s' failed: %s", name, exc)
    finally:
        db.close()
```

This also fixes a latent gap: the previous implementation called `_execute_run` without first calling `RunRepository.create_run`, so `RunExecutor.execute()`'s `update_run_status` calls had no row to update — scheduled runs never appeared in run history. Creating the row up front is required for this feature anyway (it's what populates `selection_id`/`selection_version` for schedule-triggered runs), and it fixes that gap as a side effect.

- [ ] **Step 2: Update `test_repository.py`'s schedule fixture data**

In `tests/unit/test_repository.py`, the `_sched_data` helper (lines 28-38) builds schedule dicts with `job_sequence`/`run_settings_json`/no `target_env` default of `""`. Since `ScheduleRepository.create`/`update` still accept any key that `hasattr(ScheduledRun, k)` (unaffected by the schema-layer refactor — Task 7 only changed the FastAPI route schemas, not the ORM model or repository), these existing repository-level tests keep passing unchanged. Run them to confirm:

Run: `pytest tests/unit/test_repository.py -v`
Expected: all existing tests PASS with no modification needed (repository layer is unchanged for `ScheduleRepository`).

- [ ] **Step 3: Run the full scheduler test suite**

Run: `pytest tests/unit/test_scheduler.py tests/unit/test_schedules_selection_refactor.py tests/unit/test_repository.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add api/services/scheduler.py
git commit -m "fix: scheduled runs resolve pinned selection version and persist TestRun rows"
```

---

## Task 9: End-to-End Compare Workflow Test

**Files:**
- Test: `tests/integration/test_selection_compare_workflow.py`

- [ ] **Step 1: Write the end-to-end test**

Mirroring the existing `tests/integration/test_api_frontend_smoke.py` style, create `tests/integration/test_selection_compare_workflow.py`:

```python
"""End-to-end: launch a selection twice against two environments, then pair
the resulting runs into the existing mismatch-diff compare endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import TokenRepository, JobRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.routes.selections._execute_run", lambda *a, **k: None)

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        JobRepository(db).create({
            "name": "orders_recon", "description": "", "tags": [],
            "job_type": "reconciliation", "query": "SELECT 1", "key_columns": ["id"],
            "exclude_columns": [], "params": {}, "enabled": True,
        })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def test_launch_twice_and_pair_runs_via_mismatch_diff(client):
    sel = client.post("/api/selections", json={
        "name": "cross-env-set", "job_sequence": ["orders_recon"],
    }).json()

    run_a = client.post(
        f"/api/selections/{sel['id']}/launch",
        json={"source_env": "dev", "target_env": "qa"},
    ).json()["run_id"]
    run_b = client.post(
        f"/api/selections/{sel['id']}/launch",
        json={"source_env": "staging", "target_env": "prod"},
    ).json()["run_id"]

    runs = client.get(f"/api/selections/{sel['id']}/runs").json()
    run_ids = {r["run_id"] for r in runs}
    assert run_ids == {run_a, run_b}

    diff_resp = client.post("/api/compare/mismatch-diff", json={
        "run_id_a": run_a, "run_id_b": run_b,
    })
    assert diff_resp.status_code == 200
    data = diff_resp.json()
    assert "new" in data and "resolved" in data and "persistent" in data
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/integration/test_selection_compare_workflow.py -v`
Expected: PASS. (`_execute_run` is monkeypatched to a no-op since the test only exercises wiring — job execution itself is covered by `tests/unit/test_run_executor.py`.)

- [ ] **Step 3: Run the entire new + touched test surface together**

Run: `pytest tests/unit/test_job_selections_repository.py tests/unit/test_selections_routes.py tests/unit/test_schedules_selection_refactor.py tests/unit/test_repository.py tests/unit/test_scheduler.py tests/integration/test_selection_compare_workflow.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_selection_compare_workflow.py
git commit -m "test: add end-to-end coverage for launching a selection and pairing runs for compare"
```

---

## Task 10: Frontend — Job Selections Tab (List + Create/Edit)

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`

- [ ] **Step 1: Add Alpine state**

In `frontend/app.js`, add new state properties right after `showScheduleModal: false,` / `scheduleModal: {},` (app.js:541-542):

```javascript
    showScheduleModal: false,
    scheduleModal: {},
    jobSelections: [],
    showSelectionModal: false,
    selectionModal: {},
    selectionModalEditing: false,
    selectedSelectionJobNames: [],
    selectionRunsPanel: null,
    selectionRuns: [],
    compareRunIds: [],
```

- [ ] **Step 2: Add the load/save/delete methods**

In `frontend/app.js`, add right after `runScheduleNow` (after line 3218, before the `// BASELINE` section comment):

```javascript
    // ===========================================================
    // JOB SELECTIONS
    // ===========================================================
    async loadJobSelections() {
      try { this.jobSelections = await api('GET', '/api/selections'); } catch {}
    },

    openNewSelectionModal() {
      this.selectionModal = { name: '', description: '', tags: '' };
      this.selectedSelectionJobNames = [];
      this.selectionModalEditing = false;
      this.showSelectionModal = true;
    },

    async openEditSelectionModal(sel) {
      const detail = await api('GET', `/api/selections/${sel.id}`);
      const latest = detail.versions[detail.versions.length - 1];
      this.selectionModal = {
        id: detail.id,
        name: detail.name,
        description: detail.description,
        tags: (detail.tags || []).join(', '),
      };
      this.selectedSelectionJobNames = (latest.job_sequence || []).map(
        s => (typeof s === 'string' ? s : s.job_name)
      );
      this.selectionModalEditing = true;
      this.showSelectionModal = true;
    },

    isSelectionJobChecked(name) {
      return this.selectedSelectionJobNames.includes(name);
    },

    toggleSelectionJob(name) {
      const idx = this.selectedSelectionJobNames.indexOf(name);
      if (idx >= 0) this.selectedSelectionJobNames.splice(idx, 1);
      else this.selectedSelectionJobNames.push(name);
    },

    async saveSelection() {
      const m = this.selectionModal;
      const body = {
        name: m.name,
        description: m.description || '',
        tags: (m.tags || '').split(',').map(s => s.trim()).filter(Boolean),
        job_sequence: this.selectedSelectionJobNames,
      };
      try {
        if (this.selectionModalEditing) {
          await api('PUT', `/api/selections/${m.id}`, body);
        } else {
          await api('POST', '/api/selections', body);
        }
        await this.loadJobSelections();
        this.showSelectionModal = false;
        this.toast('success', this.selectionModalEditing ? 'Selection updated' : 'Selection created', m.name);
      } catch (e) {
        this.toast('error', 'Save failed', e.message);
      }
    },

    async deleteSelection(id) {
      if (!confirm('Archive this job selection?')) return;
      try {
        await api('DELETE', `/api/selections/${id}`);
        await this.loadJobSelections();
        this.toast('success', 'Selection archived');
      } catch (e) {
        this.toast('error', 'Archive failed', e.message);
      }
    },
```

- [ ] **Step 3: Add the "Job Selections" sub-tab button**

In `frontend/index.html`, change the sub-tab button group (index.html:622-625):

```html
      <div class="flex rounded-lg border border-slate-200 overflow-hidden">
        <button @click="launchSubTab='jobs'" :class="launchSubTab==='jobs' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'" class="px-4 py-1.5 text-sm font-medium transition-colors">Jobs</button>
        <button @click="launchSubTab='selections'; loadJobSelections()" :class="launchSubTab==='selections' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'" class="px-4 py-1.5 text-sm font-medium transition-colors">Job Selections</button>
        <button @click="launchSubTab='schedules'; loadSchedules()" :class="launchSubTab==='schedules' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'" class="px-4 py-1.5 text-sm font-medium transition-colors">Schedules</button>
      </div>
```

- [ ] **Step 4: Add the sub-tab body and modal**

In `frontend/index.html`, add a new block right before the Schedules sub-tab (before line 1445 `<div x-show="launchSubTab === 'schedules'">`):

```html
  <!-- ── Job Selections sub-tab ── -->
  <div x-show="launchSubTab === 'selections'">
    <div class="card mb-4">
      <div class="flex items-center justify-between mb-3">
        <div class="font-semibold text-slate-700">Job Selections</div>
        <button @click="openNewSelectionModal()" class="btn-primary btn-sm">+ New Selection</button>
      </div>
      <template x-if="jobSelections.length === 0">
        <div class="empty-state"><div class="empty-state-title">No saved job selections yet.</div></div>
      </template>
      <div class="space-y-1">
        <template x-for="sel in jobSelections" :key="sel.id">
          <div class="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-slate-50 border border-transparent hover:border-slate-200">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="font-medium text-slate-800" x-text="sel.name"></span>
                <span class="badge text-xs" x-text="'v' + sel.latest_version"></span>
                <span class="text-muted text-xs" x-text="sel.job_count + ' jobs'"></span>
              </div>
              <div class="text-muted truncate" x-text="sel.description"></div>
            </div>
            <div class="flex gap-1 flex-shrink-0">
              <button @click="openLaunchSelectionModal(sel)" class="btn-primary btn-sm text-xs">Launch</button>
              <button @click="openSelectionRuns(sel)" class="btn-secondary btn-sm text-xs">History</button>
              <button @click="openEditSelectionModal(sel)" class="btn-secondary btn-sm text-xs">Edit</button>
              <button @click="deleteSelection(sel.id)" class="btn-danger btn-sm text-xs">Archive</button>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>

  <!-- Job Selection Modal -->
  <div x-show="showSelectionModal" x-cloak class="modal-backdrop" @click.self="showSelectionModal = false">
    <div class="modal-box w-full max-w-2xl">
      <div class="modal-header" x-text="selectionModalEditing ? 'Edit Job Selection' : 'New Job Selection'"></div>
      <div class="modal-body space-y-3">
        <div><label class="field-label">Name *</label><input x-model="selectionModal.name" class="field-input" :disabled="selectionModalEditing" placeholder="nightly-set" /></div>
        <div><label class="field-label">Description</label><input x-model="selectionModal.description" class="field-input" /></div>
        <div><label class="field-label">Tags (comma-separated)</label><input x-model="selectionModal.tags" class="field-input" placeholder="daily, orders" /></div>
        <div>
          <label class="field-label">Jobs</label>
          <div class="border rounded-lg max-h-64 overflow-y-auto">
            <template x-for="job in filteredJobList" :key="job.name">
              <label class="flex items-center gap-2 px-3 py-1.5 hover:bg-slate-50 cursor-pointer">
                <input type="checkbox" class="rounded" :checked="isSelectionJobChecked(job.name)" @change="toggleSelectionJob(job.name)" />
                <span class="text-sm" x-text="job.name"></span>
              </label>
            </template>
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button @click="showSelectionModal = false" class="btn-secondary">Cancel</button>
        <button @click="saveSelection()" :disabled="!selectionModal.name || !selectedSelectionJobNames.length" class="btn-primary">Save</button>
      </div>
    </div>
  </div>
```

- [ ] **Step 5: Manually verify in the browser**

Run the app (see project's `run` skill or existing dev-server instructions), navigate to the Jobs tab, click "Job Selections", create a selection with 2+ jobs checked, confirm it appears in the list with `v1` and the correct job count, edit it (add a job), confirm the job count updates and it's still `v1` shown as latest after refetch (the list view always shows the newest version's count).

- [ ] **Step 6: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat: add Job Selections tab with create/edit and job picker"
```

---

## Task 11: Frontend — Launch Modal, Run History, Compare Pairing

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`

- [ ] **Step 1: Add launch-modal and run-history state**

In `frontend/app.js`, extend the state block added in Task 10, Step 1 — replace:

```javascript
    jobSelections: [],
    showSelectionModal: false,
    selectionModal: {},
    selectionModalEditing: false,
    selectedSelectionJobNames: [],
    selectionRunsPanel: null,
    selectionRuns: [],
    compareRunIds: [],
```

with:

```javascript
    jobSelections: [],
    showSelectionModal: false,
    selectionModal: {},
    selectionModalEditing: false,
    selectedSelectionJobNames: [],
    showLaunchSelectionModal: false,
    launchSelectionModal: {},
    showSelectionRunsModal: false,
    selectionRunsPanel: null,
    selectionRuns: [],
    compareRunIds: [],
```

- [ ] **Step 2: Add launch, history, and compare-pairing methods**

In `frontend/app.js`, add right after `deleteSelection` (end of Task 10, Step 2 block):

```javascript
    openLaunchSelectionModal(sel) {
      this.launchSelectionModal = { selection_id: sel.id, source_env: 'dev', target_env: 'prod' };
      this.showLaunchSelectionModal = true;
    },

    async launchSelection() {
      const m = this.launchSelectionModal;
      const body = { source_env: m.source_env, target_env: m.target_env || '' };
      try {
        const run = await api('POST', `/api/selections/${m.selection_id}/launch`, body);
        this.showLaunchSelectionModal = false;
        this.toast('success', 'Launched', `Run ${run.run_id} started`);
        setTimeout(() => this.loadRuns(), 1000);
      } catch (e) {
        this.toast('error', 'Launch failed', e.message);
      }
    },

    async openSelectionRuns(sel) {
      this.selectionRunsPanel = sel;
      this.compareRunIds = [];
      try {
        this.selectionRuns = await api('GET', `/api/selections/${sel.id}/runs`);
      } catch (e) {
        this.selectionRuns = [];
        this.toast('error', 'Could not load run history', e.message);
      }
      this.showSelectionRunsModal = true;
    },

    isCompareRunSelected(runId) {
      return this.compareRunIds.includes(runId);
    },

    toggleCompareRunSelection(runId) {
      const idx = this.compareRunIds.indexOf(runId);
      if (idx >= 0) {
        this.compareRunIds.splice(idx, 1);
      } else {
        if (this.compareRunIds.length >= 2) this.compareRunIds.shift();
        this.compareRunIds.push(runId);
      }
    },

    compareSelectedRuns() {
      if (this.compareRunIds.length !== 2) {
        this.toast('warn', 'Select exactly two runs', 'Pick two runs to compare');
        return;
      }
      this.mismatchDiffRunIdA = this.compareRunIds[0];
      this.mismatchDiffRunIdB = this.compareRunIds[1];
      this.showSelectionRunsModal = false;
      this.currentView = 'compare';
      this.compareSubTab = 'mismatchdiff';
      this.runMismatchDiff();
    },
```

- [ ] **Step 3: Add the Launch and Run History modals**

In `frontend/index.html`, add right after the Job Selection Modal block from Task 10, Step 4:

```html
  <!-- Launch Selection Modal -->
  <div x-show="showLaunchSelectionModal" x-cloak class="modal-backdrop" @click.self="showLaunchSelectionModal = false">
    <div class="modal-box w-full max-w-md">
      <h2 class="text-lg font-bold mb-4">Launch Job Selection</h2>
      <div class="space-y-3">
        <div>
          <label class="field-label">Source Env</label>
          <select x-model="launchSelectionModal.source_env" class="field-input field-select">
            <option value="dev">Dev</option><option value="qa">QA</option>
            <option value="staging">Staging</option><option value="prod">Prod</option>
          </select>
        </div>
        <div>
          <label class="field-label">Target Env (leave blank for single-environment jobs)</label>
          <select x-model="launchSelectionModal.target_env" class="field-input field-select">
            <option value="">— None —</option>
            <option value="dev">Dev</option><option value="qa">QA</option>
            <option value="staging">Staging</option><option value="prod">Prod</option>
          </select>
        </div>
      </div>
      <div class="flex justify-end gap-3 mt-6">
        <button @click="showLaunchSelectionModal = false" class="btn-secondary">Cancel</button>
        <button @click="launchSelection()" class="btn-primary">Launch</button>
      </div>
    </div>
  </div>

  <!-- Selection Run History / Compare-Pairing Modal -->
  <div x-show="showSelectionRunsModal" x-cloak class="modal-backdrop" @click.self="showSelectionRunsModal = false">
    <div class="modal-box w-full max-w-2xl">
      <h2 class="text-lg font-bold mb-4" x-text="selectionRunsPanel ? ('Run History — ' + selectionRunsPanel.name) : 'Run History'"></h2>
      <div>
        <div class="text-muted text-xs mb-2">Pick exactly two runs to compare (any environment, any time).</div>
        <template x-if="selectionRuns.length === 0">
          <div class="empty-state"><div class="empty-state-title">No runs yet for this selection.</div></div>
        </template>
        <div class="space-y-1 max-h-80 overflow-y-auto">
          <template x-for="run in selectionRuns" :key="run.run_id">
            <label class="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-slate-50 cursor-pointer border border-transparent hover:border-slate-200">
              <input type="checkbox" class="rounded" :checked="isCompareRunSelected(run.run_id)" @change="toggleCompareRunSelection(run.run_id)" />
              <span class="font-mono text-xs text-slate-600" x-text="run.run_id"></span>
              <span class="badge text-xs" x-text="run.status"></span>
              <span class="text-muted text-xs" x-text="run.started_at || ''"></span>
            </label>
          </template>
        </div>
      </div>
      <div class="flex justify-end gap-3 mt-6">
        <button @click="showSelectionRunsModal = false" class="btn-secondary">Close</button>
        <button @click="compareSelectedRuns()" :disabled="compareRunIds.length !== 2" class="btn-primary">Compare Selected</button>
      </div>
    </div>
  </div>
```

- [ ] **Step 4: Manually verify in the browser**

Launch a saved selection against `dev`, then again against `qa`. Open "History" for that selection, confirm both runs appear. Check two of them and click "Compare Selected" — confirm it navigates to Compare → Mismatch Diff with both run IDs pre-filled and a result rendered (or a clear "no mismatches" state if the underlying jobs didn't actually diverge).

- [ ] **Step 5: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat: add selection launch modal and run-history compare pairing"
```

---

## Task 12: Frontend — Schedule Modal References a Selection

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`

- [ ] **Step 1: Update schedule modal open/save methods**

In `frontend/app.js`, replace `openNewScheduleModal`, `openEditScheduleModal`, and `saveSchedule` (lines 3149-3197) with:

```javascript
    openNewScheduleModal() {
      this.scheduleModal = {
        name: '', cron_expr: '0 6 * * *',
        source_env: 'dev', target_env: 'prod',
        selection_id: this.jobSelections[0]?.id || '',
        enabled: true,
      };
      this.scheduleModalEditing = false;
      this.showScheduleModal = true;
    },

    openEditScheduleModal(sched) {
      this.scheduleModal = {
        id: sched.id,
        name: sched.name,
        cron_expr: sched.cron_expr,
        source_env: sched.source_env,
        target_env: sched.target_env,
        selection_id: sched.selection_id,
        enabled: sched.enabled,
      };
      this.scheduleModalEditing = true;
      this.showScheduleModal = true;
    },

    async saveSchedule() {
      const m = this.scheduleModal;
      const body = {
        name: m.name,
        cron_expr: m.cron_expr,
        source_env: m.source_env,
        target_env: m.target_env || '',
        selection_id: m.selection_id,
        enabled: m.enabled,
      };
      try {
        if (this.scheduleModalEditing) {
          await api('PUT', `/api/schedules/${m.id}`, body);
        } else {
          await api('POST', '/api/schedules', body);
        }
        await this.loadSchedules();
        this.showScheduleModal = false;
        this.toast('success', this.scheduleModalEditing ? 'Schedule updated' : 'Schedule created', m.name);
      } catch (e) {
        this.toast('error', 'Save failed', e.message);
      }
    },
```

Also, ensure `jobSelections` is loaded whenever the Schedules sub-tab is opened, since the picker needs it. In `frontend/index.html`, change (index.html:624, already touched in Task 10 Step 3 to add the new tab button — re-apply on top of that edit):

```html
        <button @click="launchSubTab='schedules'; loadSchedules(); loadJobSelections()" :class="launchSubTab==='schedules' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'" class="px-4 py-1.5 text-sm font-medium transition-colors">Schedules</button>
```

- [ ] **Step 2: Replace the job-sequence text field with a selection picker**

In `frontend/index.html`, replace the "Jobs (comma-separated...)" field (line 1507):

```html
        <div><label class="field-label">Jobs (comma-separated, in order)</label><input x-model="scheduleModal.job_sequence_raw" class="field-input" placeholder="orders_reconciliation, customers_reconciliation" /></div>
```

with:

```html
        <div>
          <label class="field-label">Job Selection *</label>
          <select x-model="scheduleModal.selection_id" class="field-input field-select">
            <option value="">— Select —</option>
            <template x-for="sel in jobSelections" :key="sel.id">
              <option :value="sel.id" x-text="sel.name + ' (v' + sel.latest_version + ', ' + sel.job_count + ' jobs)'"></option>
            </template>
          </select>
        </div>
```

Also update the Save button's disabled condition (line 1512) to require a selection instead of the removed field:

```html
        <button @click="saveSchedule()" :disabled="!scheduleModal.name || !scheduleModal.cron_expr || !scheduleModal.selection_id" class="btn-primary">Save</button>
```

And relax the Target Env dropdown to allow "no target" (index.html:1501-1505 area) by adding a blank option — change:

```html
            <select x-model="scheduleModal.target_env" class="field-input field-select">
```

to include a leading `<option value="">— None —</option>` immediately inside that `<select>`, matching the pattern already used for the Launch Selection modal's target env dropdown in Task 11.

- [ ] **Step 3: Manually verify in the browser**

Create a Job Selection, then open the Schedules sub-tab, click "+ New Schedule", confirm the Job Selection dropdown lists it with correct version/job-count label, save, and confirm the schedule list shows it correctly. Edit an existing schedule and confirm the previously-selected selection is pre-selected.

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat: schedule modal references a Job Selection instead of an inline job list"
```

---

## Task 13: Final Verification

- [ ] **Step 1: Run the complete backend test suite touched by this feature**

Run: `pytest tests/unit/test_job_selections_repository.py tests/unit/test_selections_routes.py tests/unit/test_schedules_selection_refactor.py tests/unit/test_repository.py tests/unit/test_scheduler.py tests/integration/test_selection_compare_workflow.py -v`
Expected: all PASS, zero failures.

- [ ] **Step 2: Run the full existing test suite to check for regressions**

Run: `pytest tests/ -x -q`
Expected: all PASS (or only pre-existing skips/xfails unrelated to this change, e.g. tests requiring live SQL Server containers).

- [ ] **Step 3: Manually smoke-test the full golden path in the browser**

1. Create a Job Selection with 2+ jobs.
2. Launch it against `dev` only (no target env, if the jobs are single-env-compatible) — confirm it succeeds.
3. Launch it again against `staging`/`prod`.
4. Open its run history, select both runs, click "Compare Selected" — confirm it lands on Mismatch Diff with both run IDs filled in.
5. Create a Schedule referencing the same selection, confirm it appears correctly and "Run Now" produces a new run that also shows up in the selection's run history.
6. Edit the selection's job list — confirm a new version appears, and the existing schedule's pinned version is unaffected until manually updated.

- [ ] **Step 4: Commit any final fixups**

If Step 2 or Step 3 surfaces issues, fix them and commit as `fix: <description>` before considering this plan complete.
