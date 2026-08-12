# Saved Execution Sequences — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an Execution Sequence a saved, named, versioned entity with its own steps and dependency graph, referenceable from Job Selections and Schedules, executed in topological order by the existing linear executor.

**Architecture:** Two new tables (`execution_sequences`, `execution_sequence_versions`) mirroring the existing `JobSelection`/`JobSelectionVersion` pattern. A pure validation module handles cycle/dependency checks with no DB access. A single resolver turns a `SequenceRef` into a topologically-ordered step list, which is downgraded to today's `SequenceStep` shape so `RunExecutor` is untouched. Selections and Schedules gain an alternative reference target. Phase 1 hard-rejects fields whose behaviour ships in Phases 2–4, so a saved sequence never promises what the executor cannot do.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.x (SQLite by default), Pydantic v2, pytest, Alpine.js frontend built from partials by `scripts/build-html.js`, Playwright for E2E.

**Spec:** `docs/superpowers/specs/2026-08-12-saved-execution-sequences-design.md`

---

## Background For The Implementer

Things about this codebase you must know before starting:

- **There is no Alembic.** Schema changes work in two parts: new *tables* appear automatically from `Base.metadata.create_all(bind=engine)` in `etl_framework/repository/database.py:28`, and new *columns* on existing tables need a hand-written `ensure_column(conn, table, column, "ALTER TABLE ...")` line inside `_ensure_compare_columns()` in that same file. Read `etl_framework/repository/migrations.py` — `ensure_column` is a no-op when the column already exists.
- **The schedule ORM model is called `ScheduledRun`**, table `scheduled_runs` (`etl_framework/repository/models.py:284`). The API calls it "Schedule". Don't rename anything.
- **The frontend `index.html` is generated.** Never edit it by hand. Edit `frontend/index.template.html` and `frontend/partials/*.html`, then run `npm run build:html`. CI runs `git diff --exit-code frontend/index.html` and fails if you forgot.
- **`scripts/build-html.js` asserts that the number of `<!-- INCLUDE: -->` markers equals the number of `.html` files in `frontend/partials/`.** Adding a partial without adding its marker fails the build loudly.
- **The frontend is one Alpine component assembled from feature slices.** Each `frontend/features/*.js` exposes a `global.ETL_FEATURE_<NAME>` factory returning a plain object; `frontend/app.js:1386` merges them all in `FEATURE_SLICES`. A new feature needs three edits: the file, the `<script>` tag in `index.template.html`, and the entry in `FEATURE_SLICES`.
- **Do not verify test results through `rtk`** — it serves a cached summary. Always run raw `python -m pytest`. For Playwright use `rtk proxy npx playwright test`, because plain `rtk` mangles the reporter output.

### Phase 1 scope decisions

These are decisions this plan makes that the spec leaves at design level:

1. A sequence version must contain **at least one step**. Empty drafts are rejected with 422. Simpler than modelling a draft state nobody asked for.
2. Topological ordering is **deterministic**: Kahn's algorithm, and within each ready level, steps are emitted in their declared order. The same saved sequence always produces the same run order.
3. Phase-gated fields (`trigger_rule`, `max_retries`, `retry_delay_seconds`, `on_failure`, `preconditions`) are stored in the schema from day one but **rejected with 422 unless left at their defaults**. This is checked in one function so Phases 2–4 delete one line each to open a field up.
4. Ad-hoc launch via `RunTrigger.sequence_ref` (spec §3) is **deliberately deferred to Phase 2**. Phase 1 reaches sequences through a selection or a schedule only. `POST /api/runs` is not touched, which keeps the busiest endpoint in the app out of this phase entirely.

---

## File Structure

**Create**

| File | Responsibility |
|---|---|
| `api/services/sequence_validation.py` | Pure DAG validation + topological sort. No DB, no HTTP. |
| `api/services/sequence_resolver.py` | `SequenceRef` → `ResolvedSequence`. The only place that knows how sequences are stored. |
| `api/services/job_env_validation.py` | `_validate_env_requirements` moved out of the selections route module. |
| `etl_framework/repository/sequence_repository.py` | `ExecutionSequenceRepository`. New file because `repository.py` is already ~1100 lines. |
| `api/routes/sequences.py` | `/api/sequences` CRUD, validate, usage. |
| `frontend/features/sequences.js` | Sequences tab Alpine slice. |
| `frontend/partials/tab-sequences.html` | Sequences tab markup. |

**Modify**

| File | Change |
|---|---|
| `etl_framework/repository/models.py` | Add two models; add `sequence_ref` to `JobSelectionVersion`; add `sequence_id`/`sequence_version` to `ScheduledRun`. |
| `etl_framework/repository/database.py` | `ensure_column` lines for the three new columns. |
| `api/schemas.py` | `SequenceStepRef`, `SequenceRef`, precondition models, sequence CRUD schemas. |
| `api/routes/selections.py` | Re-export env validation; accept `sequence_ref`. |
| `api/routes/schedules.py` | Accept a sequence target. |
| `api/services/scheduler.py` | `_run_schedule` branches on target type. |
| `api/main.py` | Register the router. |
| `frontend/index.template.html` | INCLUDE marker + `<script>` tag. |
| `frontend/app.js` | Tab entry + `FEATURE_SLICES` entry. |
| `frontend/partials/tab-launch.html`, `frontend/features/launch.js` | Schedule-modal target radio; selection sequence toggle. |
| `frontend/help-content.js` | Sequences help section. |

---

## Task 1: DAG validation and topological sort

Pure functions, no DB. Everything else depends on this, so it goes first.

**Files:**
- Create: `api/services/sequence_validation.py`
- Test: `tests/unit/test_sequence_validation.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sequence_validation.py`:

```python
"""Pure DAG validation for saved execution sequences."""
from __future__ import annotations

import pytest


def _step(step_id, job_name="orders_recon", depends_on=None, **kw):
    from api.schemas import SequenceStepRef
    return SequenceStepRef(
        step_id=step_id, job_name=job_name, depends_on=depends_on or [], **kw
    )


JOBS = {"orders_recon", "customers_recon", "load_orders"}


def test_valid_chain_reports_no_errors():
    from api.services.sequence_validation import validate_steps
    steps = [_step("a"), _step("b", depends_on=["a"])]
    assert validate_steps(steps, JOBS) == []


def test_empty_sequence_is_rejected():
    from api.services.sequence_validation import validate_steps
    errors = validate_steps([], JOBS)
    assert len(errors) == 1
    assert errors[0]["field"] == "steps"


def test_duplicate_step_id_is_reported():
    from api.services.sequence_validation import validate_steps
    errors = validate_steps([_step("a"), _step("a")], JOBS)
    assert any(e["field"] == "step_id" and "Duplicate" in e["message"] for e in errors)


def test_unknown_job_name_is_reported():
    from api.services.sequence_validation import validate_steps
    errors = validate_steps([_step("a", job_name="nope")], JOBS)
    assert any(e["field"] == "job_name" and e["step_id"] == "a" for e in errors)


def test_unknown_dependency_is_reported():
    from api.services.sequence_validation import validate_steps
    errors = validate_steps([_step("a", depends_on=["ghost"])], JOBS)
    assert any(e["field"] == "depends_on" and "ghost" in e["message"] for e in errors)


def test_self_dependency_is_reported():
    from api.services.sequence_validation import validate_steps
    errors = validate_steps([_step("a", depends_on=["a"])], JOBS)
    assert any("itself" in e["message"] for e in errors)


def test_cycle_is_reported():
    from api.services.sequence_validation import validate_steps
    steps = [_step("a", depends_on=["b"]), _step("b", depends_on=["a"])]
    errors = validate_steps(steps, JOBS)
    assert any(e["field"] == "depends_on" and "cycle" in e["message"].lower() for e in errors)


def test_same_job_may_appear_under_two_step_ids():
    from api.services.sequence_validation import validate_steps
    steps = [
        _step("recon_before", job_name="orders_recon"),
        _step("load", job_name="load_orders", depends_on=["recon_before"]),
        _step("recon_after", job_name="orders_recon", depends_on=["load"]),
    ]
    assert validate_steps(steps, JOBS) == []


def test_topological_order_is_declaration_stable():
    from api.services.sequence_validation import topological_order
    # 'b' and 'c' are both ready at level 2; declared order must decide.
    steps = [_step("a"), _step("c", depends_on=["a"]), _step("b", depends_on=["a"])]
    assert topological_order(steps) == ["a", "c", "b"]


def test_topological_order_places_parents_first():
    from api.services.sequence_validation import topological_order
    steps = [_step("late", depends_on=["early"]), _step("early")]
    assert topological_order(steps) == ["early", "late"]


def test_topological_order_raises_on_cycle():
    from api.services.sequence_validation import SequenceCycleError, topological_order
    steps = [_step("a", depends_on=["b"]), _step("b", depends_on=["a"])]
    with pytest.raises(SequenceCycleError) as exc:
        topological_order(steps)
    assert set(exc.value.step_ids) == {"a", "b"}


def test_phase1_rejects_non_default_trigger_rule():
    from api.services.sequence_validation import phase1_unsupported
    errors = phase1_unsupported([_step("a", trigger_rule="all_done")], None)
    assert any(e["field"] == "trigger_rule" for e in errors)


def test_phase1_rejects_retry_and_on_failure():
    from api.services.sequence_validation import phase1_unsupported
    errors = phase1_unsupported(
        [_step("a", max_retries=2, on_failure="stop")], None
    )
    assert {e["field"] for e in errors} == {"max_retries", "on_failure"}


def test_phase1_rejects_preconditions():
    from api.schemas import SequencePrecondition
    from api.services.sequence_validation import phase1_unsupported
    errors = phase1_unsupported([_step("a")], SequencePrecondition(weekdays=[0]))
    assert any(e["field"] == "preconditions" for e in errors)


def test_phase1_allows_defaults():
    from api.services.sequence_validation import phase1_unsupported
    assert phase1_unsupported([_step("a", hold_after=True, wait_seconds=5)], None) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_sequence_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.services.sequence_validation'` (and `ImportError` for `SequenceStepRef`, which Task 4 adds; that is expected at this point — see Step 3).

Because `SequenceStepRef` does not exist yet either, **do Task 4 Step 3 now** (add the schema models), then return here. The schema is a data declaration with no dependencies, so pulling it forward costs nothing and keeps this task's tests runnable.

- [ ] **Step 3: Write the implementation**

Create `api/services/sequence_validation.py`:

```python
"""Pure validation and ordering for saved execution sequences.

No database access and no HTTP concerns live here so the rules can be unit
tested directly and reused by both the CRUD routes and the resolver.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # pragma: no cover
    from api.schemas import SequencePrecondition, SequenceStepRef


class SequenceCycleError(Exception):
    """Raised when a dependency graph cannot be topologically ordered."""

    def __init__(self, step_ids: list[str]) -> None:
        self.step_ids = step_ids
        super().__init__(f"Dependency cycle among steps: {', '.join(sorted(step_ids))}")


def _issue(step_id: str | None, field: str, message: str) -> dict:
    return {"step_id": step_id, "field": field, "message": message}


def topological_order(steps: list["SequenceStepRef"]) -> list[str]:
    """Return step_ids parents-first.

    Kahn's algorithm, emitting each ready level in declared order so the same
    saved sequence always produces the same run order.
    """
    position = {s.step_id: i for i, s in enumerate(steps)}
    remaining = {s.step_id: set(s.depends_on) for s in steps}
    ordered: list[str] = []
    while remaining:
        ready = sorted(
            (sid for sid, deps in remaining.items() if not deps),
            key=lambda sid: position[sid],
        )
        if not ready:
            raise SequenceCycleError(list(remaining))
        for sid in ready:
            ordered.append(sid)
            del remaining[sid]
        for deps in remaining.values():
            deps.difference_update(ready)
    return ordered


def validate_steps(
    steps: list["SequenceStepRef"], known_job_names: Iterable[str]
) -> list[dict]:
    """Return a list of issues; an empty list means the sequence is valid."""
    known = set(known_job_names)
    errors: list[dict] = []

    if not steps:
        return [_issue(None, "steps", "A sequence must contain at least one step")]

    seen: set[str] = set()
    for step in steps:
        if step.step_id in seen:
            errors.append(_issue(step.step_id, "step_id", f"Duplicate step_id '{step.step_id}'"))
        seen.add(step.step_id)

    for step in steps:
        if step.job_name not in known:
            errors.append(
                _issue(step.step_id, "job_name", f"Unknown or disabled job '{step.job_name}'")
            )
        for dep in step.depends_on:
            if dep == step.step_id:
                errors.append(
                    _issue(step.step_id, "depends_on", f"Step '{step.step_id}' cannot depend on itself")
                )
            elif dep not in seen:
                errors.append(
                    _issue(step.step_id, "depends_on", f"Step '{step.step_id}' depends on unknown step '{dep}'")
                )

    if not errors:
        try:
            topological_order(steps)
        except SequenceCycleError as exc:
            errors.append(_issue(None, "depends_on", str(exc).replace("Dependency cycle", "Dependency cycle detected")))

    return errors


# --- Phase gating -----------------------------------------------------------
# Phase 1 stores the full step shape but only executes what the existing linear
# executor supports. Each later phase deletes its block from this function.

def phase1_unsupported(
    steps: list["SequenceStepRef"], preconditions: "SequencePrecondition | None"
) -> list[dict]:
    errors: list[dict] = []
    for step in steps:
        if step.trigger_rule != "all_success":
            errors.append(_issue(step.step_id, "trigger_rule",
                                 "Trigger rules other than 'all_success' arrive in Phase 2"))
        if step.max_retries is not None:
            errors.append(_issue(step.step_id, "max_retries", "Per-step retry arrives in Phase 3"))
        if step.retry_delay_seconds is not None:
            errors.append(_issue(step.step_id, "retry_delay_seconds", "Per-step retry arrives in Phase 3"))
        if step.on_failure != "skip_downstream":
            errors.append(_issue(step.step_id, "on_failure", "Failure policy arrives in Phase 3"))
    if preconditions is not None:
        errors.append(_issue(None, "preconditions", "Sequence preconditions arrive in Phase 4"))
    return errors
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_sequence_validation.py -v`
Expected: PASS — 14 passed.

- [ ] **Step 5: Commit**

```bash
git add api/services/sequence_validation.py tests/unit/test_sequence_validation.py api/schemas.py
git commit -m "feat: add DAG validation and topological sort for execution sequences"
```

---

## Task 2: ORM models and column migrations

**Files:**
- Modify: `etl_framework/repository/models.py` (after `JobSelectionVersion`, around line 81; and `ScheduledRun` at line 284)
- Modify: `etl_framework/repository/database.py` (inside `_ensure_compare_columns`, near the existing `job_selection_versions` line at 330)
- Test: `tests/unit/test_sequence_repository.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_sequence_repository.py`:

```python
"""ExecutionSequence ORM models and repository."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def db():
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_new_tables_are_created(db):
    tables = set(inspect(db.get_bind()).get_table_names())
    assert {"execution_sequences", "execution_sequence_versions"} <= tables


def test_job_selection_version_has_sequence_ref_column(db):
    cols = {c["name"] for c in inspect(db.get_bind()).get_columns("job_selection_versions")}
    assert "sequence_ref" in cols


def test_scheduled_run_has_sequence_columns(db):
    cols = {c["name"] for c in inspect(db.get_bind()).get_columns("scheduled_runs")}
    assert {"sequence_id", "sequence_version"} <= cols
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/test_sequence_repository.py -v`
Expected: FAIL — `AssertionError` on `test_new_tables_are_created`.

- [ ] **Step 3: Add the ORM models**

In `etl_framework/repository/models.py`, insert immediately after the `JobSelectionVersion` class (which ends at line 81 with `selection = relationship(...)`):

```python
# ---------------------------------------------------------------------------
# Execution Sequences
# ---------------------------------------------------------------------------

class ExecutionSequence(Base):
    __tablename__ = "execution_sequences"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=False, default="")
    tags = Column(JSON, nullable=False, default=list)
    archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    versions = relationship(
        "ExecutionSequenceVersion", back_populates="sequence",
        cascade="all, delete-orphan", lazy="select",
        order_by="ExecutionSequenceVersion.version_number",
    )


class ExecutionSequenceVersion(Base):
    __tablename__ = "execution_sequence_versions"

    id = Column(Integer, primary_key=True, index=True)
    sequence_id = Column(Integer, ForeignKey("execution_sequences.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    steps_json = Column(JSON, nullable=False, default=list)
    preconditions_json = Column(JSON, nullable=True)
    defaults_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    sequence = relationship("ExecutionSequence", back_populates="versions")
```

In the same file, add one column to `JobSelectionVersion` (after `config_id` on line 78):

```python
    sequence_ref = Column(JSON, nullable=True)
```

And two columns to `ScheduledRun` (after `selection_version` on line 299):

```python
    sequence_id = Column(Integer, nullable=True, index=True)
    sequence_version = Column(Integer, nullable=True)
```

- [ ] **Step 4: Add the migration shims**

In `etl_framework/repository/database.py`, inside `_ensure_compare_columns`, directly below the existing `job_selection_versions`/`config_id` line (line 330):

```python
        ensure_column(conn, "job_selection_versions", "sequence_ref",
                      "ALTER TABLE job_selection_versions ADD COLUMN sequence_ref JSON")
        if "scheduled_runs" in tables:
            ensure_column(conn, "scheduled_runs", "sequence_id",
                          "ALTER TABLE scheduled_runs ADD COLUMN sequence_id INTEGER")
            ensure_column(conn, "scheduled_runs", "sequence_version",
                          "ALTER TABLE scheduled_runs ADD COLUMN sequence_version INTEGER")
```

The two new tables need no shim — `Base.metadata.create_all` creates them.

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/unit/test_sequence_repository.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 6: Verify nothing else broke**

Run: `python -m pytest tests/unit -q`
Expected: PASS — same count as before your change, no new failures.

- [ ] **Step 7: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/repository/database.py tests/unit/test_sequence_repository.py
git commit -m "feat: add execution sequence tables and reference columns"
```

---

## Task 3: ExecutionSequenceRepository

**Files:**
- Create: `etl_framework/repository/sequence_repository.py`
- Test: `tests/unit/test_sequence_repository.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_sequence_repository.py`:

```python
STEPS_V1 = [{"step_id": "a", "job_name": "orders_recon", "depends_on": []}]
STEPS_V2 = [
    {"step_id": "a", "job_name": "orders_recon", "depends_on": []},
    {"step_id": "b", "job_name": "load_orders", "depends_on": ["a"]},
]


def _repo(db):
    from etl_framework.repository.sequence_repository import ExecutionSequenceRepository
    return ExecutionSequenceRepository(db)


def test_create_writes_version_one(db):
    seq = _repo(db).create(name="nightly", description="d", tags=["t"], steps=STEPS_V1)
    assert seq.id is not None
    version = _repo(db).latest_version(seq.id)
    assert version.version_number == 1
    assert version.steps_json == STEPS_V1


def test_create_new_version_increments(db):
    seq = _repo(db).create(name="nightly", description="", tags=[], steps=STEPS_V1)
    version = _repo(db).create_new_version(seq.id, steps=STEPS_V2)
    assert version.version_number == 2
    assert _repo(db).latest_version(seq.id).steps_json == STEPS_V2
    assert _repo(db).get_version(seq.id, 1).steps_json == STEPS_V1


def test_get_by_name(db):
    _repo(db).create(name="nightly", description="", tags=[], steps=STEPS_V1)
    assert _repo(db).get_by_name("nightly") is not None
    assert _repo(db).get_by_name("missing") is None


def test_list_hides_archived_by_default(db):
    seq = _repo(db).create(name="nightly", description="", tags=[], steps=STEPS_V1)
    _repo(db).archive_or_raise(seq.id)
    assert _repo(db).list() == []
    assert len(_repo(db).list(include_archived=True)) == 1


def test_archive_raises_when_an_enabled_schedule_references_it(db):
    from etl_framework.repository.models import ScheduledRun
    seq = _repo(db).create(name="nightly", description="", tags=[], steps=STEPS_V1)
    db.add(ScheduledRun(name="s1", cron_expr="0 1 * * *", sequence_id=seq.id, enabled=True))
    db.commit()
    with pytest.raises(ValueError, match="enabled schedule"):
        _repo(db).archive_or_raise(seq.id)


def test_usage_lists_referencing_schedules_and_selections(db):
    from etl_framework.repository.models import JobSelection, JobSelectionVersion, ScheduledRun
    seq = _repo(db).create(name="nightly", description="", tags=[], steps=STEPS_V1)
    db.add(ScheduledRun(name="s1", cron_expr="0 1 * * *", sequence_id=seq.id,
                        sequence_version=1, enabled=True))
    selection = JobSelection(name="sel", description="", tags=[])
    db.add(selection)
    db.flush()
    db.add(JobSelectionVersion(
        selection_id=selection.id, version_number=1, job_sequence=[],
        run_settings_json={}, sequence_ref={"sequence_id": seq.id, "sequence_version": None},
    ))
    db.commit()

    usage = _repo(db).usage(seq.id)
    assert [s["name"] for s in usage["schedules"]] == ["s1"]
    assert [s["name"] for s in usage["selections"]] == ["sel"]


def test_update_metadata_changes_name_and_archived(db):
    seq = _repo(db).create(name="nightly", description="", tags=[], steps=STEPS_V1)
    updated = _repo(db).update_metadata(seq.id, name="renamed", archived=True)
    assert updated.name == "renamed"
    assert updated.archived is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_sequence_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl_framework.repository.sequence_repository'`.

- [ ] **Step 3: Write the implementation**

Create `etl_framework/repository/sequence_repository.py`:

```python
"""Persistence for saved execution sequences.

Lives in its own module rather than repository.py, which is already large.
Mirrors JobSelectionRepository so the two read the same way.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from etl_framework.repository.models import (
    ExecutionSequence,
    ExecutionSequenceVersion,
    JobSelection,
    JobSelectionVersion,
    ScheduledRun,
)


class ExecutionSequenceRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    # --- reads --------------------------------------------------------------

    def get(self, sequence_id: int) -> ExecutionSequence | None:
        return self._db.get(ExecutionSequence, sequence_id)

    def get_by_name(self, name: str) -> ExecutionSequence | None:
        return self._db.query(ExecutionSequence).filter_by(name=name).first()

    def list(self, include_archived: bool = False) -> list[ExecutionSequence]:
        q = self._db.query(ExecutionSequence)
        if not include_archived:
            q = q.filter(ExecutionSequence.archived.is_(False))
        return q.order_by(ExecutionSequence.name).all()

    def latest_version(self, sequence_id: int) -> ExecutionSequenceVersion | None:
        return (
            self._db.query(ExecutionSequenceVersion)
            .filter_by(sequence_id=sequence_id)
            .order_by(ExecutionSequenceVersion.version_number.desc())
            .first()
        )

    def get_version(self, sequence_id: int, version_number: int) -> ExecutionSequenceVersion | None:
        return (
            self._db.query(ExecutionSequenceVersion)
            .filter_by(sequence_id=sequence_id, version_number=version_number)
            .first()
        )

    # --- writes -------------------------------------------------------------

    def create(
        self, name: str, description: str, tags: list[str], steps: list,
        preconditions: dict | None = None, defaults: dict | None = None,
    ) -> ExecutionSequence:
        sequence = ExecutionSequence(name=name, description=description, tags=tags or [])
        self._db.add(sequence)
        self._db.flush()
        self._db.add(ExecutionSequenceVersion(
            sequence_id=sequence.id, version_number=1, steps_json=steps or [],
            preconditions_json=preconditions, defaults_json=defaults or {},
        ))
        self._db.commit()
        self._db.refresh(sequence)
        return sequence

    def create_new_version(
        self, sequence_id: int, steps: list,
        preconditions: dict | None = None, defaults: dict | None = None,
    ) -> ExecutionSequenceVersion | None:
        sequence = self.get(sequence_id)
        if sequence is None:
            return None
        current = self.latest_version(sequence_id)
        version = ExecutionSequenceVersion(
            sequence_id=sequence_id,
            version_number=(current.version_number + 1 if current else 1),
            steps_json=steps or [],
            preconditions_json=preconditions,
            defaults_json=defaults if defaults is not None else (current.defaults_json if current else {}),
        )
        self._db.add(version)
        sequence.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(version)
        return version

    def update_metadata(
        self, sequence_id: int, name: str | None = None, description: str | None = None,
        tags: list[str] | None = None, archived: bool | None = None,
    ) -> ExecutionSequence | None:
        sequence = self.get(sequence_id)
        if sequence is None:
            return None
        if name is not None:
            sequence.name = name
        if description is not None:
            sequence.description = description
        if tags is not None:
            sequence.tags = tags
        if archived is not None:
            sequence.archived = archived
        sequence.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(sequence)
        return sequence

    def archive_or_raise(self, sequence_id: int) -> ExecutionSequence | None:
        sequence = self.get(sequence_id)
        if sequence is None:
            return None
        if self.active_schedule_count(sequence_id) > 0:
            raise ValueError("Cannot archive: an enabled schedule still references this sequence")
        sequence.archived = True
        self._db.commit()
        self._db.refresh(sequence)
        return sequence

    # --- usage --------------------------------------------------------------

    def active_schedule_count(self, sequence_id: int) -> int:
        return (
            self._db.query(ScheduledRun)
            .filter(ScheduledRun.sequence_id == sequence_id, ScheduledRun.enabled.is_(True))
            .count()
        )

    def usage(self, sequence_id: int) -> dict:
        """Who references this sequence.

        Schedules resolve through an indexed column. Selections keep their
        reference inside a JSON column, so that side is a scan -- acceptable at
        this table size and only used by the UI and the archive guard.
        """
        schedules = [
            {"id": s.id, "name": s.name, "version": s.sequence_version}
            for s in self._db.query(ScheduledRun)
            .filter(ScheduledRun.sequence_id == sequence_id)
            .order_by(ScheduledRun.name)
            .all()
        ]

        selections: list[dict] = []
        rows = (
            self._db.query(JobSelectionVersion, JobSelection)
            .join(JobSelection, JobSelection.id == JobSelectionVersion.selection_id)
            .filter(JobSelectionVersion.sequence_ref.isnot(None))
            .all()
        )
        seen: set[int] = set()
        for version, selection in rows:
            ref = version.sequence_ref or {}
            if ref.get("sequence_id") != sequence_id or selection.id in seen:
                continue
            seen.add(selection.id)
            selections.append({
                "id": selection.id, "name": selection.name,
                "version": ref.get("sequence_version"),
            })
        selections.sort(key=lambda s: s["name"])

        return {"schedules": schedules, "selections": selections}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_sequence_repository.py -v`
Expected: PASS — 10 passed.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/repository/sequence_repository.py tests/unit/test_sequence_repository.py
git commit -m "feat: add ExecutionSequenceRepository with versioning and usage lookup"
```

---

## Task 4: Pydantic schemas

If you pulled Step 3 forward during Task 1, tick Steps 1–3 and only run the verification.

**Files:**
- Modify: `api/schemas.py` (insert after `SequenceStep`, which ends at line 180)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_sequence_schemas.py`:

```python
"""Schema shape for saved execution sequences."""
from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_step_ref_defaults():
    from api.schemas import SequenceStepRef
    step = SequenceStepRef(step_id="a", job_name="orders_recon")
    assert step.depends_on == []
    assert step.trigger_rule == "all_success"
    assert step.on_failure == "skip_downstream"
    assert step.max_retries is None
    assert step.hold_after is False
    assert step.wait_seconds == 0


def test_step_ref_rejects_blank_step_id():
    from api.schemas import SequenceStepRef
    with pytest.raises(ValidationError):
        SequenceStepRef(step_id="", job_name="orders_recon")


def test_sequence_ref_version_defaults_to_latest():
    from api.schemas import SequenceRef
    assert SequenceRef(sequence_id=1).sequence_version is None


def test_defaults_rejects_unknown_keys():
    from api.schemas import SequenceDefaults
    with pytest.raises(ValidationError):
        SequenceDefaults(nonsense=1)


def test_precondition_rejects_out_of_range_weekday():
    from api.schemas import SequencePrecondition
    with pytest.raises(ValidationError):
        SequencePrecondition(weekdays=[7])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/test_sequence_schemas.py -v`
Expected: FAIL — `ImportError: cannot import name 'SequenceStepRef' from 'api.schemas'`.

- [ ] **Step 3: Write the schemas**

In `api/schemas.py`, insert after the `SequenceStep` class (ends line 180):

```python
# ---------------------------------------------------------------------------
# Saved execution sequences
# ---------------------------------------------------------------------------

class SequenceStepRef(BaseModel):
    """A step inside a saved sequence.

    step_id is separate from job_name because a DAG may run the same job more
    than once (a reconciliation before a load and again after it), so job_name
    cannot anchor a dependency edge.
    """
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=255)
    job_name: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    trigger_rule: Literal["all_success", "all_done", "any_success", "all_failed"] = "all_success"
    hold_after: bool = False
    condition: StepCondition | None = None
    wait_seconds: int = Field(default=0, ge=0)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    retry_delay_seconds: float | None = Field(default=None, ge=0)
    on_failure: Literal["stop", "continue", "skip_downstream"] = "skip_downstream"


class TimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class RequireRunSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str = Field(min_length=1)
    within_hours: int = Field(gt=0)


class SequencePrecondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_window: TimeWindow | None = None
    weekdays: list[int] | None = None
    require_run_success: RequireRunSuccess | None = None

    @field_validator("weekdays")
    @classmethod
    def check_weekdays(cls, v: list[int] | None) -> list[int] | None:
        if v is not None and any(d < 0 or d > 6 for d in v):
            raise ValueError("weekdays entries must be between 0 (Monday) and 6 (Sunday)")
        return v


class SequenceDefaults(BaseModel):
    """Optional defaults a caller may override. A sequence is env-agnostic."""
    model_config = ConfigDict(extra="forbid")

    source_env: str | None = None
    target_env: str | None = None
    config_id: int | None = None
    run_settings: dict[str, Any] = Field(default_factory=dict)


class SequenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence_id: int
    sequence_version: int | None = None   # None resolves to the latest version


class SequenceValidationIssue(BaseModel):
    step_id: str | None = None
    field: str
    message: str


class ExecutionSequenceVersionOut(BaseModel):
    version_number: int
    steps: list[SequenceStepRef]
    preconditions: SequencePrecondition | None = None
    defaults: SequenceDefaults = Field(default_factory=SequenceDefaults)
    created_at: datetime


class ExecutionSequenceCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    steps: list[SequenceStepRef] = Field(default_factory=list)
    preconditions: SequencePrecondition | None = None
    defaults: SequenceDefaults = Field(default_factory=SequenceDefaults)


class ExecutionSequenceUpdate(BaseModel):
    """Metadata only. Steps change by creating a new version."""
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    archived: bool | None = None


class ExecutionSequenceVersionCreate(BaseModel):
    steps: list[SequenceStepRef]
    preconditions: SequencePrecondition | None = None
    defaults: SequenceDefaults | None = None


class ExecutionSequenceOut(BaseModel):
    id: int
    name: str
    description: str
    tags: list[str]
    archived: bool
    latest_version: int
    step_count: int
    created_at: datetime
    updated_at: datetime


class ExecutionSequenceDetailOut(ExecutionSequenceOut):
    versions: list[ExecutionSequenceVersionOut]


class SequenceValidateRequest(BaseModel):
    steps: list[SequenceStepRef]
    preconditions: SequencePrecondition | None = None


class SequenceValidateResponse(BaseModel):
    ok: bool
    errors: list[SequenceValidationIssue] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)


class SequenceUsageRef(BaseModel):
    id: int
    name: str
    version: int | None = None


class SequenceUsageOut(BaseModel):
    selections: list[SequenceUsageRef] = Field(default_factory=list)
    schedules: list[SequenceUsageRef] = Field(default_factory=list)
```

If `field_validator` is not already imported at the top of `api/schemas.py`, add it to the existing `from pydantic import ...` line.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/test_sequence_schemas.py tests/unit/test_sequence_validation.py -v`
Expected: PASS — 19 passed.

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_sequence_schemas.py
git commit -m "feat: add pydantic schemas for saved execution sequences"
```

---

## Task 5: Sequence resolver

Turns a reference into an ordered, executable step list. This is the only module that knows how sequences are stored.

**Files:**
- Create: `api/services/sequence_resolver.py`
- Test: `tests/unit/test_sequence_resolver.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sequence_resolver.py`:

```python
"""Resolving a SequenceRef into an ordered, executable sequence."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def db():
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import JobRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for name in ("orders_recon", "load_orders"):
            JobRepository(session).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [], "params": {},
                "enabled": True,
            })
        yield session


BRANCHED = [
    {"step_id": "load", "job_name": "load_orders", "depends_on": []},
    {"step_id": "recon_b", "job_name": "orders_recon", "depends_on": ["load"]},
    {"step_id": "recon_a", "job_name": "orders_recon", "depends_on": ["load"]},
]


def _make(db, steps=None, defaults=None):
    from etl_framework.repository.sequence_repository import ExecutionSequenceRepository
    return ExecutionSequenceRepository(db).create(
        name="nightly", description="", tags=[], steps=steps or BRANCHED,
        defaults=defaults or {},
    )


def test_resolve_latest_returns_topological_order(db):
    from api.schemas import SequenceRef
    from api.services.sequence_resolver import resolve
    seq = _make(db)
    resolved = resolve(db, SequenceRef(sequence_id=seq.id))
    assert [s.step_id for s in resolved.steps] == ["load", "recon_b", "recon_a"]
    assert resolved.version_number == 1
    assert resolved.sequence_name == "nightly"


def test_resolve_pins_an_explicit_version(db):
    from api.schemas import SequenceRef
    from api.services.sequence_resolver import resolve
    from etl_framework.repository.sequence_repository import ExecutionSequenceRepository
    seq = _make(db, steps=[{"step_id": "a", "job_name": "orders_recon", "depends_on": []}])
    ExecutionSequenceRepository(db).create_new_version(seq.id, steps=BRANCHED)
    resolved = resolve(db, SequenceRef(sequence_id=seq.id, sequence_version=1))
    assert [s.step_id for s in resolved.steps] == ["a"]


def test_resolve_raises_for_missing_sequence(db):
    from api.schemas import SequenceRef
    from api.services.sequence_resolver import SequenceResolutionError, resolve
    with pytest.raises(SequenceResolutionError, match="not found"):
        resolve(db, SequenceRef(sequence_id=999))


def test_resolve_raises_for_missing_version(db):
    from api.schemas import SequenceRef
    from api.services.sequence_resolver import SequenceResolutionError, resolve
    seq = _make(db)
    with pytest.raises(SequenceResolutionError, match="version"):
        resolve(db, SequenceRef(sequence_id=seq.id, sequence_version=7))


def test_resolve_raises_naming_every_missing_job(db):
    from api.schemas import SequenceRef
    from api.services.sequence_resolver import SequenceResolutionError, resolve
    seq = _make(db, steps=[
        {"step_id": "a", "job_name": "ghost_one", "depends_on": []},
        {"step_id": "b", "job_name": "ghost_two", "depends_on": ["a"]},
    ])
    with pytest.raises(SequenceResolutionError) as exc:
        resolve(db, SequenceRef(sequence_id=seq.id))
    assert "ghost_one" in str(exc.value)
    assert "ghost_two" in str(exc.value)


def test_as_linear_steps_drops_dag_only_fields(db):
    from api.schemas import SequenceRef, SequenceStep
    from api.services.sequence_resolver import resolve
    seq = _make(db, steps=[
        {"step_id": "a", "job_name": "orders_recon", "depends_on": [],
         "hold_after": True, "wait_seconds": 3,
         "condition": {"require_status": ["PASSED"], "max_mismatch_count": 5}},
    ])
    linear = resolve(db, SequenceRef(sequence_id=seq.id)).as_linear_steps()
    assert all(isinstance(s, SequenceStep) for s in linear)
    assert linear[0].job_name == "orders_recon"
    assert linear[0].hold_after is True
    assert linear[0].wait_seconds == 3
    assert linear[0].condition.max_mismatch_count == 5


def test_defaults_are_returned(db):
    from api.schemas import SequenceRef
    from api.services.sequence_resolver import resolve
    seq = _make(db, defaults={"source_env": "dev", "config_id": 4})
    resolved = resolve(db, SequenceRef(sequence_id=seq.id))
    assert resolved.defaults.source_env == "dev"
    assert resolved.defaults.config_id == 4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_sequence_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.services.sequence_resolver'`.

- [ ] **Step 3: Write the implementation**

Create `api/services/sequence_resolver.py`:

```python
"""The single place that turns a SequenceRef into something runnable.

Every caller -- ad-hoc launch, selection launch, and the scheduler -- goes
through resolve(), so nothing else in the codebase learns how sequences are
stored or ordered.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from api.schemas import (
    SequenceDefaults,
    SequencePrecondition,
    SequenceRef,
    SequenceStep,
    SequenceStepRef,
)
from api.services.sequence_validation import SequenceCycleError, topological_order
from etl_framework.repository.repository import JobRepository
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository


class SequenceResolutionError(Exception):
    """A saved sequence cannot be turned into a runnable step list."""


@dataclass(frozen=True)
class ResolvedSequence:
    sequence_id: int
    sequence_name: str
    version_number: int
    steps: list[SequenceStepRef]          # topologically ordered, parents first
    preconditions: SequencePrecondition | None
    defaults: SequenceDefaults

    def as_linear_steps(self) -> list[SequenceStep]:
        """Downgrade to the shape the existing linear executor consumes.

        DAG-only fields are dropped on purpose: Phase 1 rejects any non-default
        value for them at save time, so nothing is silently lost here.
        """
        return [
            SequenceStep(
                job_name=step.job_name,
                hold_after=step.hold_after,
                condition=step.condition,
                wait_seconds=step.wait_seconds,
            )
            for step in self.steps
        ]

    def snapshot_meta(self) -> dict:
        return {
            "id": self.sequence_id,
            "name": self.sequence_name,
            "version": self.version_number,
        }


def resolve(db: Session, ref: SequenceRef) -> ResolvedSequence:
    repo = ExecutionSequenceRepository(db)
    sequence = repo.get(ref.sequence_id)
    if sequence is None:
        raise SequenceResolutionError(f"Execution sequence {ref.sequence_id} not found")

    version = (
        repo.get_version(ref.sequence_id, ref.sequence_version)
        if ref.sequence_version is not None
        else repo.latest_version(ref.sequence_id)
    )
    if version is None:
        raise SequenceResolutionError(
            f"Execution sequence '{sequence.name}' has no version "
            f"{ref.sequence_version if ref.sequence_version is not None else '(latest)'}"
        )

    steps = [SequenceStepRef.model_validate(s) for s in (version.steps_json or [])]
    if not steps:
        raise SequenceResolutionError(
            f"Execution sequence '{sequence.name}' v{version.version_number} has no steps"
        )

    # Fail fast and completely: a job deleted or disabled after the sequence was
    # saved must never produce a half-executed run.
    known = {j.name for j in JobRepository(db).list() if j.enabled}
    missing = sorted({s.job_name for s in steps if s.job_name not in known})
    if missing:
        raise SequenceResolutionError(
            f"Execution sequence '{sequence.name}' v{version.version_number} references "
            f"unknown or disabled jobs: {', '.join(missing)}"
        )

    try:
        order = topological_order(steps)
    except SequenceCycleError as exc:
        raise SequenceResolutionError(
            f"Execution sequence '{sequence.name}' v{version.version_number}: {exc}"
        ) from exc

    by_id = {s.step_id: s for s in steps}
    preconditions = (
        SequencePrecondition.model_validate(version.preconditions_json)
        if version.preconditions_json else None
    )

    return ResolvedSequence(
        sequence_id=sequence.id,
        sequence_name=sequence.name,
        version_number=version.version_number,
        steps=[by_id[sid] for sid in order],
        preconditions=preconditions,
        defaults=SequenceDefaults.model_validate(version.defaults_json or {}),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_sequence_resolver.py -v`
Expected: PASS — 7 passed.

- [ ] **Step 5: Commit**

```bash
git add api/services/sequence_resolver.py tests/unit/test_sequence_resolver.py
git commit -m "feat: add sequence resolver with topological ordering and fail-fast job checks"
```

---

## Task 6: Extract job env validation

A small refactor that must land before three modules import from a route module.

**Files:**
- Create: `api/services/job_env_validation.py`
- Modify: `api/routes/selections.py:25-27` and `:168-189`

- [ ] **Step 1: Create the new module**

Create `api/services/job_env_validation.py`:

```python
"""Single-vs-dual environment requirements for a job sequence.

Moved out of api/routes/selections.py so the selections route, the schedules
route, and the sequences route can all use it without importing from a route
module.
"""
from __future__ import annotations

from fastapi import HTTPException

# Job types whose execution only touches one environment (per the approved
# design spec); everything else needs a target_env to compare against.
SINGLE_ENV_JOB_TYPES = {
    "bo_report", "freshness", "profile", "automic_job",
    "dbt_artifact", "schema_snapshot", "bo_job", "ds_job",
}


def job_name_of(step) -> str:
    if isinstance(step, dict):
        return step.get("job_name", "")
    if hasattr(step, "job_name"):
        return step.job_name
    return str(step)


def validate_env_requirements(job_sequence: list, jobs_by_name: dict, target_env: str) -> None:
    if target_env:
        return
    for step in job_sequence:
        job_name = job_name_of(step)
        job = jobs_by_name.get(job_name)
        if job is not None and job.job_type not in SINGLE_ENV_JOB_TYPES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Job '{job_name}' (type '{job.job_type}') requires a target_env; "
                    "only single-environment job types can run with target_env omitted"
                ),
            )
```

- [ ] **Step 2: Replace the originals with re-exports**

In `api/routes/selections.py`, delete the `_SINGLE_ENV_JOB_TYPES` constant (lines 25-27) and both the `_job_name_of` and `_validate_env_requirements` functions (lines 168-189), then add near the other imports:

```python
from api.services.job_env_validation import (
    SINGLE_ENV_JOB_TYPES as _SINGLE_ENV_JOB_TYPES,  # noqa: F401 — back-compat re-export
    job_name_of as _job_name_of,  # noqa: F401 — back-compat re-export
    validate_env_requirements as _validate_env_requirements,
)
```

`api/routes/schedules.py:11` already imports `_validate_env_requirements` from this module and keeps working unchanged.

- [ ] **Step 3: Run the affected suites to verify nothing broke**

Run: `python -m pytest tests/unit/test_selections_routes.py tests/unit/test_schedules_selection_refactor.py -v`
Expected: PASS — same counts as before your change.

- [ ] **Step 4: Commit**

```bash
git add api/services/job_env_validation.py api/routes/selections.py
git commit -m "refactor: move job env validation out of the selections route module"
```

---

## Task 7: Sequences CRUD router

**Files:**
- Create: `api/routes/sequences.py`
- Modify: `api/main.py` (import near line 15, `include_router` near line 73)
- Test: `tests/unit/test_sequences_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sequences_routes.py`:

```python
"""Tests for /api/sequences CRUD, validation, and usage endpoints."""
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
    from etl_framework.repository.repository import JobRepository, TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        for name in ("orders_recon", "load_orders"):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [], "params": {},
                "enabled": True,
            })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


CHAIN = [
    {"step_id": "load", "job_name": "load_orders", "depends_on": []},
    {"step_id": "recon", "job_name": "orders_recon", "depends_on": ["load"]},
]


def _create(client, name="nightly", steps=None):
    return client.post("/api/sequences", json={
        "name": name, "description": "d", "tags": ["t"], "steps": steps or CHAIN,
    })


def test_create_returns_201_with_version_one(client):
    resp = _create(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["latest_version"] == 1
    assert body["step_count"] == 2


def test_create_rejects_duplicate_name(client):
    _create(client)
    assert _create(client).status_code == 409


def test_create_rejects_cycle_with_422(client):
    resp = _create(client, steps=[
        {"step_id": "a", "job_name": "orders_recon", "depends_on": ["b"]},
        {"step_id": "b", "job_name": "load_orders", "depends_on": ["a"]},
    ])
    assert resp.status_code == 422
    assert any("cycle" in e["message"].lower() for e in resp.json()["detail"])


def test_create_rejects_unknown_job(client):
    resp = _create(client, steps=[{"step_id": "a", "job_name": "ghost", "depends_on": []}])
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "job_name"


def test_create_rejects_empty_steps(client):
    resp = _create(client, steps=[])
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "steps"


def test_create_rejects_phase2_trigger_rule(client):
    resp = _create(client, steps=[
        {"step_id": "a", "job_name": "orders_recon", "depends_on": [], "trigger_rule": "all_done"},
    ])
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "trigger_rule"


def test_create_rejects_phase4_preconditions(client):
    resp = client.post("/api/sequences", json={
        "name": "gated", "steps": CHAIN, "preconditions": {"weekdays": [0]},
    })
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "preconditions"


def test_get_returns_detail_with_versions(client):
    seq_id = _create(client).json()["id"]
    body = client.get(f"/api/sequences/{seq_id}").json()
    assert len(body["versions"]) == 1
    assert [s["step_id"] for s in body["versions"][0]["steps"]] == ["load", "recon"]


def test_list_excludes_archived(client):
    seq_id = _create(client).json()["id"]
    assert len(client.get("/api/sequences").json()) == 1
    assert client.delete(f"/api/sequences/{seq_id}").status_code == 204
    assert client.get("/api/sequences").json() == []
    assert len(client.get("/api/sequences?include_archived=true").json()) == 1


def test_patch_updates_metadata_only(client):
    seq_id = _create(client).json()["id"]
    body = client.patch(f"/api/sequences/{seq_id}", json={"name": "renamed"}).json()
    assert body["name"] == "renamed"
    assert body["latest_version"] == 1


def test_post_version_increments_and_validates(client):
    seq_id = _create(client).json()["id"]
    resp = client.post(f"/api/sequences/{seq_id}/versions", json={
        "steps": [{"step_id": "solo", "job_name": "orders_recon", "depends_on": []}],
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["version_number"] == 2
    assert client.get(f"/api/sequences/{seq_id}/versions/1").json()["version_number"] == 1


def test_get_missing_version_returns_404(client):
    seq_id = _create(client).json()["id"]
    assert client.get(f"/api/sequences/{seq_id}/versions/9").status_code == 404


def test_validate_endpoint_reports_order_when_valid(client):
    resp = client.post("/api/sequences/validate", json={"steps": CHAIN})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "errors": [], "order": ["load", "recon"]}


def test_validate_endpoint_reports_errors_without_persisting(client):
    resp = client.post("/api/sequences/validate", json={
        "steps": [{"step_id": "a", "job_name": "ghost", "depends_on": []}],
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert client.get("/api/sequences").json() == []


def test_usage_is_empty_for_a_fresh_sequence(client):
    seq_id = _create(client).json()["id"]
    assert client.get(f"/api/sequences/{seq_id}/usage").json() == {
        "selections": [], "schedules": [],
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_sequences_routes.py -v`
Expected: FAIL — all tests 404, because the router is not registered yet.

- [ ] **Step 3: Write the router**

Create `api/routes/sequences.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import (
    ExecutionSequenceCreate,
    ExecutionSequenceDetailOut,
    ExecutionSequenceOut,
    ExecutionSequenceUpdate,
    ExecutionSequenceVersionCreate,
    ExecutionSequenceVersionOut,
    SequenceUsageOut,
    SequenceValidateRequest,
    SequenceValidateResponse,
)
from api.services.audit_service import AuditService
from api.services.sequence_validation import (
    SequenceCycleError,
    phase1_unsupported,
    topological_order,
    validate_steps,
)
from etl_framework.repository.repository import JobRepository
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository

router = APIRouter(tags=["sequences"])


def _known_job_names(db: Session) -> set[str]:
    return {j.name for j in JobRepository(db).list() if j.enabled}


def _check_or_422(db: Session, steps, preconditions) -> None:
    errors = validate_steps(steps, _known_job_names(db)) + phase1_unsupported(steps, preconditions)
    if errors:
        raise HTTPException(status_code=422, detail=errors)


def _dump(models) -> list:
    return [m.model_dump() for m in models]


def _version_out(version) -> ExecutionSequenceVersionOut:
    return ExecutionSequenceVersionOut(
        version_number=version.version_number,
        steps=version.steps_json or [],
        preconditions=version.preconditions_json,
        defaults=version.defaults_json or {},
        created_at=version.created_at,
    )


def _sequence_out(sequence) -> ExecutionSequenceOut:
    latest = sequence.versions[-1] if sequence.versions else None
    return ExecutionSequenceOut(
        id=sequence.id,
        name=sequence.name,
        description=sequence.description,
        tags=sequence.tags or [],
        archived=sequence.archived,
        latest_version=latest.version_number if latest else 0,
        step_count=len(latest.steps_json or []) if latest else 0,
        created_at=sequence.created_at,
        updated_at=sequence.updated_at,
    )


def _detail_out(sequence) -> ExecutionSequenceDetailOut:
    return ExecutionSequenceDetailOut(
        **_sequence_out(sequence).model_dump(),
        versions=[_version_out(v) for v in sequence.versions],
    )


def _get_or_404(db: Session, sequence_id: int):
    sequence = ExecutionSequenceRepository(db).get(sequence_id)
    if sequence is None:
        raise HTTPException(status_code=404, detail="Execution sequence not found")
    return sequence


@router.get("", response_model=list[ExecutionSequenceOut])
def list_sequences(
    include_archived: bool = Query(False), db: Session = Depends(get_session)
):
    return [
        _sequence_out(s)
        for s in ExecutionSequenceRepository(db).list(include_archived=include_archived)
    ]


@router.post("", response_model=ExecutionSequenceOut, status_code=201)
def create_sequence(
    body: ExecutionSequenceCreate, request: Request, db: Session = Depends(get_session)
):
    repo = ExecutionSequenceRepository(db)
    if repo.get_by_name(body.name) is not None:
        raise HTTPException(
            status_code=409, detail="An execution sequence with this name already exists"
        )
    _check_or_422(db, body.steps, body.preconditions)
    sequence = repo.create(
        name=body.name, description=body.description, tags=body.tags,
        steps=_dump(body.steps),
        preconditions=body.preconditions.model_dump() if body.preconditions else None,
        defaults=body.defaults.model_dump(),
    )
    AuditService(db).log(
        request, "sequence.created", "execution_sequence", sequence.id,
        {"name": sequence.name, "step_count": len(body.steps)},
    )
    return _sequence_out(sequence)


# Registered before /{sequence_id} so "validate" is never read as an id.
@router.post("/validate", response_model=SequenceValidateResponse)
def validate_sequence(body: SequenceValidateRequest, db: Session = Depends(get_session)):
    errors = validate_steps(body.steps, _known_job_names(db)) + phase1_unsupported(
        body.steps, body.preconditions
    )
    if errors:
        return SequenceValidateResponse(ok=False, errors=errors, order=[])
    try:
        order = topological_order(body.steps)
    except SequenceCycleError as exc:  # pragma: no cover — validate_steps catches this first
        return SequenceValidateResponse(
            ok=False, errors=[{"step_id": None, "field": "depends_on", "message": str(exc)}], order=[]
        )
    return SequenceValidateResponse(ok=True, errors=[], order=order)


@router.get("/{sequence_id}", response_model=ExecutionSequenceDetailOut)
def get_sequence(sequence_id: int, db: Session = Depends(get_session)):
    return _detail_out(_get_or_404(db, sequence_id))


@router.patch("/{sequence_id}", response_model=ExecutionSequenceDetailOut)
def update_sequence(
    sequence_id: int, body: ExecutionSequenceUpdate, request: Request,
    db: Session = Depends(get_session),
):
    repo = ExecutionSequenceRepository(db)
    _get_or_404(db, sequence_id)
    if body.name is not None:
        clash = repo.get_by_name(body.name)
        if clash is not None and clash.id != sequence_id:
            raise HTTPException(
                status_code=409, detail="An execution sequence with this name already exists"
            )
    sequence = repo.update_metadata(
        sequence_id, name=body.name, description=body.description,
        tags=body.tags, archived=body.archived,
    )
    AuditService(db).log(
        request, "sequence.updated", "execution_sequence", sequence_id, {"name": sequence.name}
    )
    return _detail_out(sequence)


@router.delete("/{sequence_id}", status_code=204)
def archive_sequence(sequence_id: int, request: Request, db: Session = Depends(get_session)):
    _get_or_404(db, sequence_id)
    try:
        ExecutionSequenceRepository(db).archive_or_raise(sequence_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    AuditService(db).log(request, "sequence.archived", "execution_sequence", sequence_id)


@router.get("/{sequence_id}/versions", response_model=list[ExecutionSequenceVersionOut])
def list_sequence_versions(sequence_id: int, db: Session = Depends(get_session)):
    return [_version_out(v) for v in _get_or_404(db, sequence_id).versions]


@router.post("/{sequence_id}/versions", response_model=ExecutionSequenceVersionOut, status_code=201)
def create_sequence_version(
    sequence_id: int, body: ExecutionSequenceVersionCreate, request: Request,
    db: Session = Depends(get_session),
):
    _get_or_404(db, sequence_id)
    _check_or_422(db, body.steps, body.preconditions)
    version = ExecutionSequenceRepository(db).create_new_version(
        sequence_id, steps=_dump(body.steps),
        preconditions=body.preconditions.model_dump() if body.preconditions else None,
        defaults=body.defaults.model_dump() if body.defaults is not None else None,
    )
    AuditService(db).log(
        request, "sequence.version_created", "execution_sequence", sequence_id,
        {"version": version.version_number},
    )
    return _version_out(version)


@router.get("/{sequence_id}/versions/{version_number}", response_model=ExecutionSequenceVersionOut)
def get_sequence_version(
    sequence_id: int, version_number: int, db: Session = Depends(get_session)
):
    _get_or_404(db, sequence_id)
    version = ExecutionSequenceRepository(db).get_version(sequence_id, version_number)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return _version_out(version)


@router.get("/{sequence_id}/usage", response_model=SequenceUsageOut)
def get_sequence_usage(sequence_id: int, db: Session = Depends(get_session)):
    _get_or_404(db, sequence_id)
    return SequenceUsageOut(**ExecutionSequenceRepository(db).usage(sequence_id))
```

- [ ] **Step 4: Register the router**

In `api/main.py`, add to the route imports near line 15:

```python
from api.routes import sequences as sequences_routes
```

And after the selections line (line 73):

```python
app.include_router(sequences_routes.router, prefix="/api/sequences")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_sequences_routes.py -v`
Expected: PASS — 15 passed.

- [ ] **Step 6: Commit**

```bash
git add api/routes/sequences.py api/main.py tests/unit/test_sequences_routes.py
git commit -m "feat: add /api/sequences CRUD, validation, and usage endpoints"
```

---

## Task 8: Wire sequences into Job Selections

**Files:**
- Modify: `api/schemas.py` (`JobSelectionCreate`, `JobSelectionUpdate`, `JobSelectionVersionOut`)
- Modify: `etl_framework/repository/repository.py:175-190` (`create`) and `:236-262` (`create_new_version`)
- Modify: `api/routes/selections.py`
- Test: `tests/unit/test_selections_sequence_ref.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_selections_sequence_ref.py`:

```python
"""Job Selections referencing a saved execution sequence."""
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
    from etl_framework.repository.repository import JobRepository, TokenRepository

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
        for name in ("orders_recon", "load_orders"):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [], "params": {},
                "enabled": True,
            })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _sequence(client):
    return client.post("/api/sequences", json={
        "name": "nightly",
        "steps": [
            {"step_id": "load", "job_name": "load_orders", "depends_on": []},
            {"step_id": "recon", "job_name": "orders_recon", "depends_on": ["load"]},
        ],
    }).json()["id"]


def test_selection_can_reference_a_sequence(client):
    seq_id = _sequence(client)
    resp = client.post("/api/selections", json={
        "name": "sel", "sequence_ref": {"sequence_id": seq_id},
    })
    assert resp.status_code == 201, resp.text
    detail = client.get(f"/api/selections/{resp.json()['id']}").json()
    assert detail["versions"][0]["sequence_ref"] == {"sequence_id": seq_id, "sequence_version": None}


def test_selection_rejects_both_inline_and_ref(client):
    seq_id = _sequence(client)
    resp = client.post("/api/selections", json={
        "name": "sel", "job_sequence": ["orders_recon"],
        "sequence_ref": {"sequence_id": seq_id},
    })
    assert resp.status_code == 422


def test_selection_rejects_unknown_sequence(client):
    resp = client.post("/api/selections", json={
        "name": "sel", "sequence_ref": {"sequence_id": 999},
    })
    assert resp.status_code == 404


def test_launch_resolves_the_sequence_in_topological_order(client):
    seq_id = _sequence(client)
    sel_id = client.post("/api/selections", json={
        "name": "sel", "sequence_ref": {"sequence_id": seq_id},
    }).json()["id"]

    resp = client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 202, resp.text

    run = client.get(f"/api/runs/{resp.json()['run_id']}").json()
    snapshot = run["config_snapshot"]
    assert [s["job_name"] for s in snapshot["job_sequence"]] == ["load_orders", "orders_recon"]
    assert snapshot["sequence"] == {"id": seq_id, "name": "nightly", "version": 1}


def test_inline_selections_still_work(client):
    resp = client.post("/api/selections", json={"name": "sel", "job_sequence": ["orders_recon"]})
    assert resp.status_code == 201
    detail = client.get(f"/api/selections/{resp.json()['id']}").json()
    assert detail["versions"][0]["sequence_ref"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_selections_sequence_ref.py -v`
Expected: FAIL — the first test 422s because `sequence_ref` is not a known field.

- [ ] **Step 3: Extend the selection schemas**

In `api/schemas.py`, add `sequence_ref` to three models and a mutual-exclusion validator. Replace `JobSelectionVersionOut`, `JobSelectionCreate`, and `JobSelectionUpdate` (lines 627-652) with:

```python
class JobSelectionVersionOut(BaseModel):
    version_number: int
    job_sequence: list[str | SequenceStep]
    run_settings: RunSettings
    config_id: int | None = None
    sequence_ref: SequenceRef | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class JobSelectionCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    job_sequence: list[str | SequenceStep] = Field(default_factory=list)
    run_settings: RunSettings = Field(default_factory=RunSettings)
    config_id: int | None = None
    sequence_ref: SequenceRef | None = None

    @model_validator(mode="after")
    def check_one_source(self) -> "JobSelectionCreate":
        if self.sequence_ref is not None and self.job_sequence:
            raise ValueError(
                "Provide either job_sequence or sequence_ref, not both"
            )
        return self


class JobSelectionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    job_sequence: list[str | SequenceStep] | None = None
    run_settings: RunSettings | None = None
    config_id: int | None = None
    sequence_ref: SequenceRef | None = None

    @model_validator(mode="after")
    def check_one_source(self) -> "JobSelectionUpdate":
        if self.sequence_ref is not None and self.job_sequence:
            raise ValueError(
                "Provide either job_sequence or sequence_ref, not both"
            )
        return self
```

- [ ] **Step 4: Extend the repository**

In `etl_framework/repository/repository.py`, change `JobSelectionRepository.create` (line 175) to accept and persist the reference:

```python
    def create(
        self, name: str, description: str, tags: list[str],
        job_sequence: list, run_settings: dict, config_id: int | None = None,
        sequence_ref: dict | None = None,
    ) -> JobSelection:
        selection = JobSelection(name=name, description=description, tags=tags or [])
        self._db.add(selection)
        self._db.flush()
        self._db.add(JobSelectionVersion(
            selection_id=selection.id, version_number=1,
            job_sequence=job_sequence or [], run_settings_json=run_settings or {},
            config_id=config_id, sequence_ref=sequence_ref,
        ))
        self._db.commit()
        self._db.refresh(selection)
        return selection
```

And in `create_new_version` (line 236), add the parameter and carry it forward:

```python
    def create_new_version(
        self, selection_id: int, job_sequence: list | None, run_settings: dict | None,
        config_id: int | None = _UNSET, sequence_ref: dict | None = _UNSET,
    ) -> JobSelectionVersion | None:
```

then inside the `JobSelectionVersion(...)` construction add:

```python
            sequence_ref=(
                sequence_ref if sequence_ref is not _UNSET
                else (current.sequence_ref if current else None)
            ),
```

- [ ] **Step 5: Update the selections route**

In `api/routes/selections.py`:

Add imports:

```python
from api.schemas import SequenceRef  # add to the existing api.schemas import block
from api.services.sequence_resolver import SequenceResolutionError, resolve as resolve_sequence
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository
```

Add a guard helper next to `_validate_config_id_or_404`:

```python
def _validate_sequence_ref_or_404(ref: SequenceRef | None, db: Session) -> dict | None:
    if ref is None:
        return None
    repo = ExecutionSequenceRepository(db)
    if repo.get(ref.sequence_id) is None:
        raise HTTPException(status_code=404, detail="Execution sequence not found")
    if ref.sequence_version is not None and repo.get_version(ref.sequence_id, ref.sequence_version) is None:
        raise HTTPException(status_code=404, detail="Execution sequence version not found")
    return ref.model_dump()
```

Extend `_version_out` to pass the reference through:

```python
def _version_out(version) -> JobSelectionVersionOut:
    return JobSelectionVersionOut(
        version_number=version.version_number,
        job_sequence=version.job_sequence or [],
        run_settings=version.run_settings_json or {},
        config_id=version.config_id,
        sequence_ref=version.sequence_ref,
        created_at=version.created_at,
    )
```

In `create_selection`, after `_validate_config_id_or_404(...)`:

```python
    sequence_ref = _validate_sequence_ref_or_404(body.sequence_ref, db)
```

and pass `sequence_ref=sequence_ref` to `repo.create(...)`.

In `update_selection`, add `sequence_ref` to the change detection and pass it through:

```python
    sequence_ref_set = "sequence_ref" in body.model_fields_set
    if body.job_sequence is not None or body.run_settings is not None or config_id_set or sequence_ref_set:
        ...
        if sequence_ref_set:
            version_kwargs["sequence_ref"] = _validate_sequence_ref_or_404(body.sequence_ref, db)
```

In `launch_selection`, resolve before building the trigger. Replace the block from `jobs_by_name = ...` down to the `config_snapshot["run_settings"] = ...` line with:

```python
    resolved = None
    if version.sequence_ref:
        try:
            resolved = resolve_sequence(db, SequenceRef.model_validate(version.sequence_ref))
        except SequenceResolutionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job_sequence = resolved.as_linear_steps()
    else:
        job_sequence = version.job_sequence or []

    jobs_by_name = {j.name: j for j in JobRepository(db).list()}
    _validate_env_requirements(job_sequence, jobs_by_name, body.target_env)

    trigger = RunTrigger(
        source_env=body.source_env,
        target_env=body.target_env,
        source_connection=body.source_connection,
        target_connection=body.target_connection,
        job_sequence=job_sequence,
        # The selection remembers its own config (saved on the selection so
        # launching doesn't require re-picking one every time); an explicit
        # config_id on the launch request overrides it for a one-off run.
        config_id=body.config_id if body.config_id is not None else version.config_id,
        config_data=body.config_data,
        run_settings=version.run_settings_json or {},
    )

    run_id = str(uuid.uuid4())
    ordered_jobs = trigger.job_sequence
    config_snapshot = _snapshot_from_trigger(trigger, db)
    config_snapshot["job_sequence"] = _dump_job_sequence(ordered_jobs)
    config_snapshot["run_settings"] = trigger.run_settings.model_dump()
    if resolved is not None:
        config_snapshot["sequence"] = resolved.snapshot_meta()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_selections_sequence_ref.py tests/unit/test_selections_routes.py -v`
Expected: PASS — 5 new tests pass and the existing selections suite is unchanged.

- [ ] **Step 7: Commit**

```bash
git add api/schemas.py api/routes/selections.py etl_framework/repository/repository.py tests/unit/test_selections_sequence_ref.py
git commit -m "feat: let job selections reference a saved execution sequence"
```

---

## Task 9: Wire sequences into Schedules

**Files:**
- Modify: `api/routes/schedules.py`
- Modify: `api/services/scheduler.py:80-176` (`_run_schedule`)
- Test: `tests/unit/test_schedules_sequence_target.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_schedules_sequence_target.py`:

```python
"""Schedules targeting a saved execution sequence instead of a selection."""
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
    from etl_framework.repository.repository import JobRepository, TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        for name in ("orders_recon", "load_orders"):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [], "params": {},
                "enabled": True,
            })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _sequence(client, name="nightly"):
    return client.post("/api/sequences", json={
        "name": name,
        "steps": [
            {"step_id": "load", "job_name": "load_orders", "depends_on": []},
            {"step_id": "recon", "job_name": "orders_recon", "depends_on": ["load"]},
        ],
    }).json()["id"]


def _payload(**kw):
    body = {"name": "nightly-sched", "cron_expr": "0 1 * * *",
            "source_env": "dev", "target_env": "prod"}
    body.update(kw)
    return body


def test_schedule_can_target_a_sequence(client):
    seq_id = _sequence(client)
    resp = client.post("/api/schedules", json=_payload(sequence_id=seq_id))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sequence_id"] == seq_id
    assert body["sequence_version"] == 1     # null pins to latest at save time
    assert body["selection_id"] is None


def test_schedule_pins_an_explicit_version(client):
    seq_id = _sequence(client)
    body = client.post("/api/schedules", json=_payload(
        sequence_id=seq_id, sequence_version=1)).json()
    assert body["sequence_version"] == 1


def test_schedule_rejects_both_targets(client):
    seq_id = _sequence(client)
    resp = client.post("/api/schedules", json=_payload(sequence_id=seq_id, selection_id=1))
    assert resp.status_code == 422


def test_schedule_rejects_neither_target(client):
    assert client.post("/api/schedules", json=_payload()).status_code == 422


def test_schedule_rejects_unknown_sequence(client):
    assert client.post("/api/schedules", json=_payload(sequence_id=999)).status_code == 404


def test_schedule_rejects_unknown_sequence_version(client):
    seq_id = _sequence(client)
    resp = client.post("/api/schedules", json=_payload(sequence_id=seq_id, sequence_version=9))
    assert resp.status_code == 404


def test_archiving_a_scheduled_sequence_is_blocked(client):
    seq_id = _sequence(client)
    client.post("/api/schedules", json=_payload(sequence_id=seq_id))
    assert client.delete(f"/api/sequences/{seq_id}").status_code == 409


def test_usage_reports_the_schedule(client):
    seq_id = _sequence(client)
    client.post("/api/schedules", json=_payload(sequence_id=seq_id))
    usage = client.get(f"/api/sequences/{seq_id}/usage").json()
    assert [s["name"] for s in usage["schedules"]] == ["nightly-sched"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_schedules_sequence_target.py -v`
Expected: FAIL — the first test 422s because `selection_id` is still required.

- [ ] **Step 3: Update the schedules route**

In `api/routes/schedules.py`, replace `ScheduleCreate`, `ScheduleOut`, and `_resolve_and_validate` with:

```python
class ScheduleCreate(BaseModel):
    name: str
    cron_expr: str
    selection_id: int | None = None
    selection_version: int | None = None
    sequence_id: int | None = None
    sequence_version: int | None = None
    source_env: str
    target_env: str = ""
    enabled: bool = True

    @field_validator("cron_expr")
    @classmethod
    def check_cron(cls, v: str) -> str:
        return _validate_cron(v)

    @model_validator(mode="after")
    def check_one_target(self) -> "ScheduleCreate":
        if (self.selection_id is None) == (self.sequence_id is None):
            raise ValueError("Provide exactly one of selection_id or sequence_id")
        return self


class ScheduleOut(BaseModel):
    id: int
    name: str
    cron_expr: str
    selection_id: int | None
    selection_version: int | None
    sequence_id: int | None
    sequence_version: int | None
    source_env: str
    target_env: str
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    model_config = {"from_attributes": True}


def _resolve_sequence_version(db: Session, sequence_id: int, version: int | None) -> int:
    """Resolve and pin. A schedule always stores a concrete version so a later
    edit to the sequence cannot silently change what the schedule runs."""
    repo = ExecutionSequenceRepository(db)
    if repo.get(sequence_id) is None:
        raise HTTPException(status_code=404, detail="Execution sequence not found")
    if version is None:
        latest = repo.latest_version(sequence_id)
        if latest is None:
            raise HTTPException(status_code=422, detail="Execution sequence has no versions")
        return latest.version_number
    if repo.get_version(sequence_id, version) is None:
        raise HTTPException(status_code=404, detail="Execution sequence version not found")
    return version


def _resolve_and_validate(db: Session, body: "ScheduleCreate") -> tuple[str, int]:
    """Resolve the target version and enforce the same single/dual-env job-type
    check used by ad-hoc launches, so a schedule can't be saved pointing at a
    target that structurally needs a target_env it doesn't have.

    Returns (target_kind, version_number) where target_kind is
    "selection" or "sequence".
    """
    jobs_by_name = {j.name: j for j in JobRepository(db).list()}

    if body.sequence_id is not None:
        version_number = _resolve_sequence_version(db, body.sequence_id, body.sequence_version)
        try:
            resolved = resolve_sequence(
                db, SequenceRef(sequence_id=body.sequence_id, sequence_version=version_number)
            )
        except SequenceResolutionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _validate_env_requirements(resolved.as_linear_steps(), jobs_by_name, body.target_env)
        return "sequence", version_number

    version_number = _resolve_selection_version(db, body.selection_id, body.selection_version)
    version = JobSelectionRepository(db).get_version(body.selection_id, version_number)
    _validate_env_requirements(version.job_sequence or [], jobs_by_name, body.target_env)
    return "selection", version_number
```

Add the imports this needs at the top of the file:

```python
from pydantic import BaseModel, field_validator, model_validator

from api.schemas import SequenceRef
from api.services.sequence_resolver import SequenceResolutionError, resolve as resolve_sequence
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository
```

There are exactly two call sites of `_resolve_and_validate`, both currently the single line
`data["selection_version"] = _resolve_and_validate(db, body)`: `create_schedule` at
`api/routes/schedules.py:105` and `update_schedule` at `:120`. Replace that line in **both**
with this block:

```python
    kind, version_number = _resolve_and_validate(db, body)
    if kind == "sequence":
        data["sequence_version"] = version_number
        data["selection_id"] = None
        data["selection_version"] = None
    else:
        data["selection_version"] = version_number
        data["sequence_id"] = None
        data["sequence_version"] = None
```

In `create_schedule` the block goes after the existing `data = body.model_dump()` on line 104;
in `update_schedule` after the same call on line 119.

- [ ] **Step 4: Update the scheduler**

In `api/services/scheduler.py`, inside `_run_schedule`, replace the block that fetches the selection version and builds the trigger (lines 108-150) with:

```python
        from api.schemas import SequenceRef
        from api.services.sequence_resolver import SequenceResolutionError, resolve as resolve_sequence

        sequence_meta = None
        if sched.sequence_id is not None:
            try:
                resolved = resolve_sequence(
                    db,
                    SequenceRef(sequence_id=sched.sequence_id,
                                sequence_version=sched.sequence_version),
                )
            except SequenceResolutionError as exc:
                logger.error("Schedule '%s' could not resolve its sequence: %s", name, exc)
                record_scheduler_event(
                    db, sched, "skipped", "ERROR", exit_code=1, error_summary=str(exc)
                )
                return
            job_sequence = resolved.as_linear_steps()
            run_settings = resolved.defaults.run_settings or {}
            config_id = resolved.defaults.config_id
            sequence_meta = resolved.snapshot_meta()
            selection_id = None
            selection_version = None
        else:
            sel_repo = JobSelectionRepository(db)
            version = sel_repo.get_version(sched.selection_id, sched.selection_version)
            if version is None:
                logger.error(
                    "Schedule '%s' references missing selection %s v%s; skipping run",
                    name, sched.selection_id, sched.selection_version,
                )
                record_scheduler_event(
                    db, sched, "skipped", "ERROR", exit_code=1,
                    error_summary=(
                        f"Selection version not found: selection {sched.selection_id} "
                        f"v{sched.selection_version}"
                    ),
                )
                return
            job_sequence = version.job_sequence or []
            run_settings = version.run_settings_json or {}
            # Without this, a scheduled run resolves no Saved Config and every
            # live job in it (bo_report, automic_job, compare) runs without
            # credentials. Selection launch already does this -- see
            # api/routes/selections.py's launch handler.
            config_id = version.config_id
            selection_id = sched.selection_id
            selection_version = sched.selection_version

        run_repo = RunRepository(db)
        if selection_id is not None and run_repo.has_active_run_for_selection(selection_id):
            logger.info(
                "Schedule '%s' skipped because selection %s already has an active run",
                name, selection_id,
            )
            record_scheduler_event(
                db, sched, "skipped", "BLOCKED",
                error_summary=f"Selection {selection_id} already has an active run",
            )
            return

        trigger = RunTrigger(
            source_env=sched.source_env,
            target_env=sched.target_env,
            job_sequence=job_sequence,
            run_settings=run_settings,
            config_id=config_id,
        )
```

Then further down, after `config_snapshot["run_settings"] = ...`, add:

```python
        if sequence_meta is not None:
            config_snapshot["sequence"] = sequence_meta
```

and change the `run_repo.create_run(...)` call to pass `selection_id=selection_id, selection_version=selection_version`.

Note that the original `run_repo = RunRepository(db)` and `has_active_run_for_selection` guard moved into the block above — make sure it is not left duplicated below.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_schedules_sequence_target.py tests/unit/test_scheduler.py tests/unit/test_schedules_selection_refactor.py -v`
Expected: PASS — 8 new tests pass, both existing scheduler suites unchanged.

- [ ] **Step 6: Commit**

```bash
git add api/routes/schedules.py api/services/scheduler.py tests/unit/test_schedules_sequence_target.py
git commit -m "feat: let schedules target a saved execution sequence"
```

---

## Task 10: Integration test for the full workflow

**Files:**
- Test: `tests/integration/test_sequence_workflow.py`

- [ ] **Step 1: Write the test**

Create `tests/integration/test_sequence_workflow.py`:

```python
"""End-to-end workflow: build a sequence, attach it, run it."""
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
    from etl_framework.repository.repository import JobRepository, TokenRepository

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
        for name in ("extract", "load", "verify"):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [], "params": {},
                "enabled": True,
            })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


DIAMOND = [
    {"step_id": "extract", "job_name": "extract", "depends_on": []},
    {"step_id": "load_b", "job_name": "load", "depends_on": ["extract"]},
    {"step_id": "load_a", "job_name": "load", "depends_on": ["extract"]},
    {"step_id": "verify", "job_name": "verify", "depends_on": ["load_a", "load_b"]},
]


def test_build_attach_and_launch(client):
    # 1. Validate before saving.
    check = client.post("/api/sequences/validate", json={"steps": DIAMOND}).json()
    assert check["ok"] is True
    assert check["order"] == ["extract", "load_b", "load_a", "verify"]

    # 2. Save it.
    seq_id = client.post("/api/sequences", json={"name": "etl", "steps": DIAMOND}).json()["id"]

    # 3. Attach to a selection and launch.
    sel_id = client.post("/api/selections", json={
        "name": "etl-sel", "sequence_ref": {"sequence_id": seq_id},
    }).json()["id"]
    run_id = client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    }).json()["run_id"]

    snapshot = client.get(f"/api/runs/{run_id}").json()["config_snapshot"]
    assert [s["job_name"] for s in snapshot["job_sequence"]] == ["extract", "load", "load", "verify"]
    assert snapshot["sequence"]["name"] == "etl"

    # 4. Attach to a schedule too.
    sched = client.post("/api/schedules", json={
        "name": "etl-nightly", "cron_expr": "0 2 * * *",
        "sequence_id": seq_id, "source_env": "dev", "target_env": "prod",
    })
    assert sched.status_code == 201

    # 5. Usage reports both consumers.
    usage = client.get(f"/api/sequences/{seq_id}/usage").json()
    assert [s["name"] for s in usage["selections"]] == ["etl-sel"]
    assert [s["name"] for s in usage["schedules"]] == ["etl-nightly"]


def test_new_version_does_not_disturb_a_pinned_schedule(client):
    seq_id = client.post("/api/sequences", json={"name": "etl", "steps": DIAMOND}).json()["id"]
    client.post("/api/schedules", json={
        "name": "etl-nightly", "cron_expr": "0 2 * * *",
        "sequence_id": seq_id, "source_env": "dev", "target_env": "prod",
    })
    client.post(f"/api/sequences/{seq_id}/versions", json={
        "steps": [{"step_id": "solo", "job_name": "verify", "depends_on": []}],
    })
    schedules = client.get("/api/schedules").json()
    assert schedules[0]["sequence_version"] == 1


def test_disabling_a_job_breaks_resolution_with_a_clear_error(client):
    seq_id = client.post("/api/sequences", json={"name": "etl", "steps": DIAMOND}).json()["id"]
    sel_id = client.post("/api/selections", json={
        "name": "etl-sel", "sequence_ref": {"sequence_id": seq_id},
    }).json()["id"]

    jobs = client.get("/api/jobs").json()
    load = next(j for j in jobs if j["name"] == "load")
    client.put(f"/api/jobs/{load['name']}", json={**load, "enabled": False})

    resp = client.post(f"/api/selections/{sel_id}/launch", json={
        "source_env": "dev", "target_env": "prod",
    })
    assert resp.status_code == 422
    assert "load" in resp.json()["detail"]
```

The disable call uses `PUT /api/jobs/{name}` with a full `JobDefinition` body — see `api/routes/jobs.py:242`. That is why the test round-trips the job object rather than sending a partial patch.

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/integration/test_sequence_workflow.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_sequence_workflow.py
git commit -m "test: add integration coverage for the sequence build-attach-launch workflow"
```

---

## Task 11: Sequences tab — Alpine feature slice

**Files:**
- Create: `frontend/features/sequences.js`
- Modify: `frontend/app.js:140-169` (tabs) and `:1386` (FEATURE_SLICES)

- [ ] **Step 1: Write the feature slice**

Create `frontend/features/sequences.js`:

```javascript
(function (global) {
  'use strict';
  // Sequences feature slice (Sequences tab: saved execution sequence CRUD,
  // step/dependency editing, live DAG validation). Merged into the Alpine
  // component via the FEATURE_SLICES reduce in app.js.
  global.ETL_FEATURE_SEQUENCES = function () {
    return {
      // ===== STATE =====
      sequences: [],
      sequencesLoading: false,
      selectedSequence: null,          // detail payload from GET /api/sequences/{id}
      sequenceUsage: { selections: [], schedules: [] },
      sequenceEditorOpen: false,
      sequenceEditorMode: 'create',    // 'create' | 'version'
      sequenceMeta: { name: '', description: '', tags_raw: '' },
      sequenceSteps: [],               // array of SequenceStepRef-shaped objects
      sequenceIssues: [],              // [{step_id, field, message}]
      sequenceOrder: [],               // topological step_id order when valid
      sequenceSaving: false,

      // ===== DERIVED =====
      get sequenceIsValid() {
        return this.sequenceSteps.length > 0 && this.sequenceIssues.length === 0;
      },

      sequenceIssuesFor(stepId) {
        return this.sequenceIssues.filter((i) => i.step_id === stepId);
      },

      get sequenceGlobalIssues() {
        return this.sequenceIssues.filter((i) => !i.step_id);
      },

      // Steps grouped by dependency depth, for the read-only graph preview.
      get sequenceLevels() {
        const depth = {};
        const byId = {};
        for (const s of this.sequenceSteps) byId[s.step_id] = s;
        const resolveDepth = (id, seen) => {
          if (depth[id] !== undefined) return depth[id];
          if (seen.has(id)) return 0;              // cycle: validation reports it
          seen.add(id);
          const step = byId[id];
          const parents = (step && step.depends_on) || [];
          const value = parents.length
            ? 1 + Math.max(...parents.map((p) => (byId[p] ? resolveDepth(p, seen) : 0)))
            : 0;
          depth[id] = value;
          return value;
        };
        const levels = [];
        for (const s of this.sequenceSteps) {
          const d = resolveDepth(s.step_id, new Set());
          (levels[d] = levels[d] || []).push(s);
        }
        return levels.map((steps, index) => ({ index, steps: steps || [] }));
      },

      // ===== LOADING =====
      async loadSequences() {
        this.sequencesLoading = true;
        try {
          this.sequences = await api('GET', '/api/sequences');
        } catch { this.sequences = []; }
        this.sequencesLoading = false;
      },

      async selectSequence(sequence) {
        try {
          this.selectedSequence = await api('GET', `/api/sequences/${sequence.id}`);
          this.sequenceUsage = await api('GET', `/api/sequences/${sequence.id}/usage`);
        } catch {
          this.selectedSequence = null;
          this.sequenceUsage = { selections: [], schedules: [] };
        }
      },

      // ===== EDITING =====
      newSequenceStep() {
        return {
          step_id: '', job_name: '', depends_on: [],
          hold_after: false, wait_seconds: 0, condition: null,
        };
      },

      slugifyStepId(name) {
        return String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
      },

      openSequenceCreate() {
        this.sequenceEditorMode = 'create';
        this.sequenceMeta = { name: '', description: '', tags_raw: '' };
        this.sequenceSteps = [this.newSequenceStep()];
        this.sequenceIssues = [];
        this.sequenceOrder = [];
        this.sequenceEditorOpen = true;
      },

      openSequenceVersionEditor() {
        if (!this.selectedSequence) return;
        const latest = this.selectedSequence.versions[this.selectedSequence.versions.length - 1];
        this.sequenceEditorMode = 'version';
        this.sequenceMeta = {
          name: this.selectedSequence.name,
          description: this.selectedSequence.description,
          tags_raw: (this.selectedSequence.tags || []).join(', '),
        };
        this.sequenceSteps = JSON.parse(JSON.stringify(latest ? latest.steps : []));
        this.sequenceIssues = [];
        this.sequenceOrder = [];
        this.sequenceEditorOpen = true;
      },

      addSequenceStep() {
        this.sequenceSteps.push(this.newSequenceStep());
        this.validateSequenceSteps();
      },

      removeSequenceStep(index) {
        const removed = this.sequenceSteps[index];
        this.sequenceSteps.splice(index, 1);
        // Drop any edges that pointed at the removed step so the user is not
        // left staring at an "unknown step" error they did not cause.
        for (const step of this.sequenceSteps) {
          step.depends_on = (step.depends_on || []).filter((d) => d !== removed.step_id);
        }
        this.validateSequenceSteps();
      },

      onSequenceJobPicked(step) {
        if (!step.step_id) step.step_id = this.slugifyStepId(step.job_name);
        this.validateSequenceSteps();
      },

      // Candidate parents: every other step that already has an id.
      sequenceParentOptions(step) {
        return this.sequenceSteps
          .filter((s) => s !== step && s.step_id)
          .map((s) => s.step_id);
      },

      toggleSequenceDependency(step, parentId) {
        step.depends_on = step.depends_on || [];
        const at = step.depends_on.indexOf(parentId);
        if (at === -1) step.depends_on.push(parentId);
        else step.depends_on.splice(at, 1);
        this.validateSequenceSteps();
      },

      async validateSequenceSteps() {
        try {
          const result = await api('POST', '/api/sequences/validate', {
            steps: this.sequenceSteps,
          });
          this.sequenceIssues = result.errors || [];
          this.sequenceOrder = result.order || [];
        } catch {
          this.sequenceIssues = [];
          this.sequenceOrder = [];
        }
      },

      // ===== SAVING =====
      async saveSequence() {
        if (!this.sequenceIsValid) return;
        this.sequenceSaving = true;
        const tags = this.sequenceMeta.tags_raw
          .split(',').map((t) => t.trim()).filter(Boolean);
        try {
          if (this.sequenceEditorMode === 'create') {
            const created = await api('POST', '/api/sequences', {
              name: this.sequenceMeta.name,
              description: this.sequenceMeta.description,
              tags,
              steps: this.sequenceSteps,
            });
            await this.loadSequences();
            await this.selectSequence(created);
          } else {
            await api('POST', `/api/sequences/${this.selectedSequence.id}/versions`, {
              steps: this.sequenceSteps,
            });
            await this.loadSequences();
            await this.selectSequence(this.selectedSequence);
          }
          this.sequenceEditorOpen = false;
        } catch (err) {
          const detail = err && err.detail;
          this.sequenceIssues = Array.isArray(detail)
            ? detail
            : [{ step_id: null, field: 'steps', message: String((detail && detail.message) || err) }];
        }
        this.sequenceSaving = false;
      },

      async archiveSequence(sequence) {
        if (!confirm(`Archive sequence "${sequence.name}"?`)) return;
        try {
          await api('DELETE', `/api/sequences/${sequence.id}`);
          if (this.selectedSequence && this.selectedSequence.id === sequence.id) {
            this.selectedSequence = null;
          }
          await this.loadSequences();
        } catch (err) {
          alert((err && err.detail) || 'Could not archive this sequence.');
        }
      },
    };
  };
})(window);
```

- [ ] **Step 2: Register the tab and the slice**

In `frontend/app.js`, add a tab entry to the `tabs` array (line 140), directly after the `jobs` entry so it sits in the `execution` group:

```javascript
      { id: 'sequences', label: 'Sequences', group: 'execution',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"></circle><circle cx="18" cy="18" r="3"></circle><path d="M9 6h6a3 3 0 0 1 3 3v6"></path></svg>' },
```

And add the slice to `FEATURE_SLICES` on line 1386:

```javascript
  const FEATURE_SLICES = [ETL_FEATURE_COMPARE(), ETL_FEATURE_CONFIG(), ETL_FEATURE_LAUNCH(), ETL_FEATURE_MONITOR(), ETL_FEATURE_HISTORY(), ETL_FEATURE_ADAPTERS(), ETL_FEATURE_AWS(), ETL_FEATURE_REPORTS(), ETL_FEATURE_DIFFERENCES(), ETL_FEATURE_CONTRACTS(), ETL_FEATURE_SCHEDULER_REPORTS(), ETL_FEATURE_LOGS(), ETL_FEATURE_SEQUENCES()];
```

- [ ] **Step 3: Commit**

```bash
git add frontend/features/sequences.js frontend/app.js
git commit -m "feat: add sequences Alpine feature slice"
```

---

## Task 12: Sequences tab markup

**Files:**
- Create: `frontend/partials/tab-sequences.html`
- Modify: `frontend/index.template.html` (INCLUDE marker near line 105, `<script>` tag near line 6756 of the generated output)

- [ ] **Step 1: Write the partial**

Create `frontend/partials/tab-sequences.html`:

```html
<template x-if="currentView === 'sequences'"><div data-testid="sequences-panel">
  <div class="section-header">
    <div>
      <div class="section-title">Execution Sequences</div>
      <div class="section-sub">Saved, versioned job pipelines with their own dependencies. Attach one to a job selection or a schedule.</div>
    </div>
    <button class="btn btn-primary" data-testid="sequence-new-btn" @click="openSequenceCreate()">New sequence</button>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
    <!-- List -->
    <div class="card">
      <div class="card-title">Saved sequences</div>
      <div x-show="sequencesLoading" class="text-sm text-muted">Loading…</div>
      <div x-show="!sequencesLoading && sequences.length === 0" class="text-sm text-muted">
        No sequences yet.
      </div>
      <ul class="space-y-2" data-testid="sequence-list">
        <template x-for="seq in sequences" :key="seq.id">
          <li class="flex items-center justify-between gap-2 p-2 rounded hover:bg-subtle cursor-pointer"
              :data-testid="'sequence-row-' + seq.name"
              @click="selectSequence(seq)">
            <div>
              <div class="font-medium" x-text="seq.name"></div>
              <div class="text-xs text-muted">
                <span x-text="'v' + seq.latest_version"></span> ·
                <span x-text="seq.step_count + ' steps'"></span>
              </div>
            </div>
            <button class="btn btn-ghost btn-xs" :data-testid="'sequence-archive-' + seq.name"
                    @click.stop="archiveSequence(seq)">Archive</button>
          </li>
        </template>
      </ul>
    </div>

    <!-- Detail -->
    <div class="card lg:col-span-2" x-show="selectedSequence" data-testid="sequence-detail">
      <div class="flex items-center justify-between">
        <div class="card-title" x-text="selectedSequence && selectedSequence.name"></div>
        <button class="btn btn-secondary btn-sm" data-testid="sequence-edit-btn"
                @click="openSequenceVersionEditor()">Edit as new version</button>
      </div>
      <div class="text-sm text-muted mb-3" x-text="selectedSequence && selectedSequence.description"></div>

      <table class="data-table" aria-label="sequences table 1">
        <thead>
          <tr><th>Step</th><th>Job</th><th>Runs after</th></tr>
        </thead>
        <tbody>
          <template x-if="selectedSequence">
            <template x-for="step in selectedSequence.versions[selectedSequence.versions.length - 1].steps"
                      :key="step.step_id">
              <tr>
                <td x-text="step.step_id"></td>
                <td x-text="step.job_name"></td>
                <td x-text="(step.depends_on || []).join(', ') || '—'"></td>
              </tr>
            </template>
          </template>
        </tbody>
      </table>

      <div class="mt-4 text-sm" data-testid="sequence-usage">
        <div class="font-medium">Used by</div>
        <div class="text-muted">
          <span x-text="sequenceUsage.selections.length + ' selection(s)'"></span> ·
          <span x-text="sequenceUsage.schedules.length + ' schedule(s)'"></span>
        </div>
      </div>
    </div>
  </div>

  <!-- Editor -->
  <div class="modal" x-show="sequenceEditorOpen" x-cloak data-testid="sequence-editor">
    <div class="modal-body max-w-4xl">
      <div class="modal-title"
           x-text="sequenceEditorMode === 'create' ? 'New execution sequence' : 'New version'"></div>

      <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
        <label class="field">
          <span class="field-label">Name</span>
          <input class="field-input" data-testid="sequence-name-input"
                 x-model="sequenceMeta.name" :disabled="sequenceEditorMode === 'version'"
                 aria-label="sequence name" />
        </label>
        <label class="field">
          <span class="field-label">Description</span>
          <input class="field-input" x-model="sequenceMeta.description" aria-label="sequence description" />
        </label>
        <label class="field">
          <span class="field-label">Tags (comma separated)</span>
          <input class="field-input" x-model="sequenceMeta.tags_raw" aria-label="sequence tags" />
        </label>
      </div>

      <div class="space-y-3" data-testid="sequence-step-editor">
        <template x-for="(step, index) in sequenceSteps" :key="index">
          <div class="card card-compact">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
              <label class="field">
                <span class="field-label">Job</span>
                <select class="field-input" :data-testid="'sequence-step-job-' + index"
                        x-model="step.job_name" @change="onSequenceJobPicked(step)"
                        aria-label="step job">
                  <option value="">Select a job…</option>
                  <template x-for="job in jobs" :key="job.name">
                    <option :value="job.name" x-text="job.name"></option>
                  </template>
                </select>
              </label>
              <label class="field">
                <span class="field-label">Step id</span>
                <input class="field-input" :data-testid="'sequence-step-id-' + index"
                       x-model="step.step_id" @input="validateSequenceSteps()"
                       aria-label="step id" />
              </label>
              <div class="field">
                <span class="field-label">Runs after</span>
                <div class="flex flex-wrap gap-2" :data-testid="'sequence-step-deps-' + index">
                  <template x-for="parent in sequenceParentOptions(step)" :key="parent">
                    <label class="chip cursor-pointer">
                      <input type="checkbox" :checked="(step.depends_on || []).includes(parent)"
                             @change="toggleSequenceDependency(step, parent)"
                             :aria-label="'depends on ' + parent" />
                      <span x-text="parent"></span>
                    </label>
                  </template>
                  <span class="text-xs text-muted" x-show="sequenceParentOptions(step).length === 0">
                    No other steps yet
                  </span>
                </div>
              </div>
            </div>

            <details class="mt-2">
              <summary class="text-sm cursor-pointer">Advanced</summary>
              <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
                <label class="field-inline">
                  <input type="checkbox" x-model="step.hold_after" aria-label="hold after this step" />
                  <span>Hold after this step</span>
                </label>
                <label class="field">
                  <span class="field-label">Wait before this step (seconds)</span>
                  <input type="number" min="0" class="field-input" x-model.number="step.wait_seconds"
                         aria-label="wait seconds" />
                </label>
              </div>
            </details>

            <template x-for="issue in sequenceIssuesFor(step.step_id)" :key="issue.field + issue.message">
              <div class="text-xs text-danger mt-1" x-text="issue.message"></div>
            </template>

            <div class="mt-2 text-right">
              <button class="btn btn-ghost btn-xs" :data-testid="'sequence-step-remove-' + index"
                      @click="removeSequenceStep(index)">Remove step</button>
            </div>
          </div>
        </template>
      </div>

      <button class="btn btn-secondary btn-sm mt-3" data-testid="sequence-add-step"
              @click="addSequenceStep()">Add step</button>

      <template x-for="issue in sequenceGlobalIssues" :key="issue.field + issue.message">
        <div class="text-sm text-danger mt-2" data-testid="sequence-global-error" x-text="issue.message"></div>
      </template>

      <!-- Read-only graph preview, laid out by dependency depth. -->
      <div class="mt-4" x-show="sequenceOrder.length" data-testid="sequence-graph-preview">
        <div class="field-label">Execution order preview</div>
        <div class="flex flex-col gap-2">
          <template x-for="level in sequenceLevels" :key="level.index">
            <div class="flex items-center gap-2">
              <span class="text-xs text-muted w-16" x-text="'level ' + (level.index + 1)"></span>
              <div class="flex flex-wrap gap-2">
                <template x-for="step in level.steps" :key="step.step_id">
                  <span class="chip" x-text="step.step_id"></span>
                </template>
              </div>
            </div>
          </template>
        </div>
      </div>

      <div class="modal-actions">
        <button class="btn btn-ghost" @click="sequenceEditorOpen = false">Cancel</button>
        <button class="btn btn-primary" data-testid="sequence-save-btn"
                :disabled="!sequenceIsValid || sequenceSaving"
                @click="saveSequence()">Save</button>
      </div>
    </div>
  </div>
</div></template>
```

If any CSS class used here does not exist in `frontend/styles.css`, substitute the nearest equivalent already used by `frontend/partials/tab-contracts.html` — match the existing visual language rather than inventing classes.

- [ ] **Step 2: Add the INCLUDE marker and script tag**

In `frontend/index.template.html`, add after the `tab-launch.html` marker (line 105):

```html
<!-- INCLUDE: partials/tab-sequences.html -->
```

And add the script tag alongside the other feature scripts, after `features/launch.js`:

```html
    <script src="features/sequences.js"></script>
```

- [ ] **Step 3: Rebuild the generated HTML**

Run: `npm run build:html`
Expected: `Built .../frontend/index.html from .../frontend/index.template.html + 16 partials`

The partial count must have gone from 15 to 16. If the build throws `INCLUDE marker count ... does not match`, you missed the marker.

- [ ] **Step 4: Verify the frontend smoke test still passes**

Run: `python -m pytest tests/integration/test_api_frontend_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/partials/tab-sequences.html frontend/index.template.html frontend/index.html
git commit -m "feat: add sequences tab markup with dependency editor and order preview"
```

---

## Task 13: Attach a sequence from the selection and schedule forms

**Files:**
- Modify: `frontend/features/launch.js`
- Modify: `frontend/partials/tab-launch.html`

- [ ] **Step 1: Add state and loading to the launch slice**

In `frontend/features/launch.js`, add to the returned object's state:

```javascript
      // Saved execution sequences available to attach to a selection or schedule.
      availableSequences: [],
      selectionSourceMode: 'inline',     // 'inline' | 'sequence'
      selectionSequenceRef: { sequence_id: null, sequence_version: null },
      scheduleTargetMode: 'selection',   // 'selection' | 'sequence'
      scheduleSequenceRef: { sequence_id: null, sequence_version: null },
```

and add a loader method:

```javascript
    async loadAvailableSequences() {
      try {
        this.availableSequences = await api('GET', '/api/sequences');
      } catch { this.availableSequences = []; }
    },

    sequenceVersionOptions(sequenceId) {
      const seq = this.availableSequences.find((s) => s.id === Number(sequenceId));
      if (!seq) return [];
      return Array.from({ length: seq.latest_version }, (_, i) => i + 1).reverse();
    },
```

Then call it from `loadJobSelections()` (`frontend/features/launch.js:1045`), so the picker is populated whenever the selections list is:

```javascript
    async loadJobSelections() {
      try { this.jobSelections = await api('GET', '/api/selections'); } catch {}
      await this.loadAvailableSequences();
      // ... rest of the existing method body stays unchanged
    },
```

The `jobs` array the step-editor `<select>` iterates is already on this slice (`frontend/features/launch.js:12`), populated by `loadJobs()` at `:111`. No extra wiring needed for it.

- [ ] **Step 2: Send the reference when saving**

In `saveSelection()` (`frontend/features/launch.js:1084`), which builds a `body` from `this.selectionModal` and then calls `PUT /api/selections/{id}` or `POST /api/selections` at `:1096-1098`, branch on the mode when assembling that body:

```javascript
      const payload = {
        name: this.selectionModal.name,
        description: this.selectionModal.description,
        tags: this.selectionModal.tags,
      };
      if (this.selectionSourceMode === 'sequence') {
        payload.sequence_ref = {
          sequence_id: Number(this.selectionSequenceRef.sequence_id),
          sequence_version: this.selectionSequenceRef.sequence_version
            ? Number(this.selectionSequenceRef.sequence_version)
            : null,
        };
      } else {
        payload.job_sequence = this.selectionJobSequence;
        payload.run_settings = this.runSettings;
        payload.config_id = this.selectionConfigId;
      }
```

In `saveSchedule()` (`frontend/features/launch.js:997`), branch the same way:

```javascript
      const payload = {
        name: this.scheduleModal.name,
        cron_expr: this.scheduleModal.cron_expr,
        source_env: this.scheduleModal.source_env,
        target_env: this.scheduleModal.target_env,
        enabled: this.scheduleModal.enabled,
      };
      if (this.scheduleTargetMode === 'sequence') {
        payload.sequence_id = Number(this.scheduleSequenceRef.sequence_id);
        payload.sequence_version = this.scheduleSequenceRef.sequence_version
          ? Number(this.scheduleSequenceRef.sequence_version)
          : null;
      } else {
        payload.selection_id = Number(this.scheduleModal.selection_id);
        payload.selection_version = this.scheduleModal.selection_version || null;
      }
```

- [ ] **Step 3: Add the pickers to the markup**

In `frontend/partials/tab-launch.html`, add above the existing selection job-sequence editor:

```html
<div class="field" data-testid="selection-source-mode">
  <span class="field-label">Jobs come from</span>
  <label class="field-inline">
    <input type="radio" value="inline" x-model="selectionSourceMode"
           @change="loadAvailableSequences()" aria-label="inline job list" />
    <span>This selection's own job list</span>
  </label>
  <label class="field-inline">
    <input type="radio" value="sequence" x-model="selectionSourceMode"
           @change="loadAvailableSequences()" aria-label="saved execution sequence" />
    <span>A saved execution sequence</span>
  </label>
</div>

<div class="grid grid-cols-1 md:grid-cols-2 gap-3" x-show="selectionSourceMode === 'sequence'">
  <label class="field">
    <span class="field-label">Sequence</span>
    <select class="field-input" data-testid="selection-sequence-picker"
            x-model="selectionSequenceRef.sequence_id" aria-label="sequence">
      <option :value="null">Select a sequence…</option>
      <template x-for="seq in availableSequences" :key="seq.id">
        <option :value="seq.id" x-text="seq.name"></option>
      </template>
    </select>
  </label>
  <label class="field">
    <span class="field-label">Version</span>
    <select class="field-input" data-testid="selection-sequence-version"
            x-model="selectionSequenceRef.sequence_version" aria-label="sequence version">
      <option :value="null">Always latest</option>
      <template x-for="v in sequenceVersionOptions(selectionSequenceRef.sequence_id)" :key="v">
        <option :value="v" x-text="'v' + v"></option>
      </template>
    </select>
  </label>
</div>
```

And in the schedule modal, above the existing selection picker:

```html
<div class="field" data-testid="schedule-target-mode">
  <span class="field-label">Run</span>
  <label class="field-inline">
    <input type="radio" value="selection" x-model="scheduleTargetMode" aria-label="a job selection" />
    <span>A job selection</span>
  </label>
  <label class="field-inline">
    <input type="radio" value="sequence" x-model="scheduleTargetMode"
           @change="loadAvailableSequences()" aria-label="an execution sequence" />
    <span>An execution sequence</span>
  </label>
</div>

<div class="grid grid-cols-1 md:grid-cols-2 gap-3" x-show="scheduleTargetMode === 'sequence'">
  <label class="field">
    <span class="field-label">Sequence</span>
    <select class="field-input" data-testid="schedule-sequence-picker"
            x-model="scheduleSequenceRef.sequence_id" aria-label="schedule sequence">
      <option :value="null">Select a sequence…</option>
      <template x-for="seq in availableSequences" :key="seq.id">
        <option :value="seq.id" x-text="seq.name"></option>
      </template>
    </select>
  </label>
  <label class="field">
    <span class="field-label">Version</span>
    <select class="field-input" data-testid="schedule-sequence-version"
            x-model="scheduleSequenceRef.sequence_version" aria-label="schedule sequence version">
      <option :value="null">Latest at save time</option>
      <template x-for="v in sequenceVersionOptions(scheduleSequenceRef.sequence_id)" :key="v">
        <option :value="v" x-text="'v' + v"></option>
      </template>
    </select>
  </label>
</div>

<div x-show="scheduleTargetMode === 'selection'">
  <!-- existing selection picker markup stays here, unchanged -->
</div>
```

- [ ] **Step 4: Rebuild and verify**

Run: `npm run build:html && python -m pytest tests/integration/test_api_frontend_smoke.py -v`
Expected: build succeeds with 16 partials; smoke test PASSES.

- [ ] **Step 5: Commit**

```bash
git add frontend/features/launch.js frontend/partials/tab-launch.html frontend/index.html
git commit -m "feat: attach a saved sequence from the selection and schedule forms"
```

---

## Task 14: Help content

**Files:**
- Modify: `frontend/help-content.js`

- [ ] **Step 1: Add the section**

Read the existing structure in `frontend/help-content.js` first — each section has `id`, `title`, `intro`, and `steps[]`, where a step supports `title`, `text`, `where`, `tip`, and `warn`. Add a section following that exact shape:

```javascript
  {
    id: 'sequences',
    title: 'Execution Sequences',
    intro: 'A saved execution sequence is a named, versioned pipeline of jobs with its own dependencies. Build it once, then attach it to as many job selections and schedules as you like.',
    steps: [
      {
        title: 'Create a sequence',
        text: 'Open the Sequences tab and choose "New sequence". Add a step for each job you want to run.',
        where: 'Sequences tab → New sequence',
      },
      {
        title: 'Give each step an id',
        text: 'The step id is filled in from the job name, but you can change it. Ids matter because the same job can appear more than once in a pipeline — a reconciliation before a load and again after it — so the job name alone cannot identify a step.',
        tip: 'Keep ids short and descriptive: recon_before, load, recon_after.',
      },
      {
        title: 'Declare dependencies',
        text: 'Use "Runs after" to pick which steps must finish before this one starts. Steps with no dependencies start first. The order preview shows the levels the sequence will run in.',
        warn: 'A circular dependency is rejected when you save. The editor flags it as you type.',
      },
      {
        title: 'Save a new version',
        text: 'Editing a saved sequence always creates a new version. Older versions are never changed, so a schedule pinned to version 1 keeps running version 1.',
        where: 'Sequences tab → Edit as new version',
      },
      {
        title: 'Attach it',
        text: 'In a job selection, switch "Jobs come from" to "A saved execution sequence". In a schedule, switch "Run" to "An execution sequence". A selection can follow the latest version; a schedule always pins the version it was saved with.',
        where: 'Launch tab → Selections and Schedules',
      },
    ],
  },
```

- [ ] **Step 2: Verify the help tests still pass**

Run: `python -m pytest tests/unit -k help -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/help-content.js
git commit -m "docs: add Sequences help section"
```

---

## Task 15: E2E coverage

**Files:**
- Test: `tests/e2e/17-sequences.spec.ts`

- [ ] **Step 1: Read an existing spec for the local conventions**

Read `tests/e2e/16-scheduler-stats.spec.ts` first. Copy its imports, its auth/context helper, and its fixture-seeding approach exactly — this repo has a specific `authedContext` helper and a seeding pattern you must not reinvent.

- [ ] **Step 2: Write the spec**

Create `tests/e2e/17-sequences.spec.ts`, using the helpers you just read:

```typescript
import { expect, test } from '@playwright/test';
// Import the same auth/context helpers 16-scheduler-stats.spec.ts uses.

test.describe('Execution sequences', () => {
  test('build a two-branch sequence and attach it to a schedule', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await expect(page.getByTestId('sequences-panel')).toBeVisible();

    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill('e2e-pipeline');

    // Step 1 — the root.
    await page.getByTestId('sequence-step-job-0').selectOption({ index: 1 });
    await expect(page.getByTestId('sequence-step-id-0')).not.toHaveValue('');

    // Step 2 — depends on step 1.
    await page.getByTestId('sequence-add-step').click();
    await page.getByTestId('sequence-step-job-1').selectOption({ index: 1 });
    await page.getByTestId('sequence-step-id-1').fill('second');
    await page.getByTestId('sequence-step-deps-1').getByRole('checkbox').first().check();

    await expect(page.getByTestId('sequence-graph-preview')).toBeVisible();
    await page.getByTestId('sequence-save-btn').click();

    await expect(page.getByTestId('sequence-row-e2e-pipeline')).toBeVisible();
  });

  test('a cycle is rejected before saving', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill('e2e-cycle');

    await page.getByTestId('sequence-step-job-0').selectOption({ index: 1 });
    await page.getByTestId('sequence-step-id-0').fill('a');
    await page.getByTestId('sequence-add-step').click();
    await page.getByTestId('sequence-step-job-1').selectOption({ index: 1 });
    await page.getByTestId('sequence-step-id-1').fill('b');

    // b depends on a, then a depends on b — a cycle.
    await page.getByTestId('sequence-step-deps-1').getByRole('checkbox').first().check();
    await page.getByTestId('sequence-step-deps-0').getByRole('checkbox').first().check();

    await expect(page.getByTestId('sequence-global-error')).toContainText(/cycle/i);
    await expect(page.getByTestId('sequence-save-btn')).toBeDisabled();
  });
});
```

- [ ] **Step 3: Run the spec**

Run: `rtk proxy npx playwright test tests/e2e/17-sequences.spec.ts`

Plain `rtk` forces a JSON reporter and truncates the output, so always use `rtk proxy` for Playwright in this repo.

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/17-sequences.spec.ts
git commit -m "test: add e2e coverage for building and validating sequences"
```

---

## Task 16: Full verification

- [ ] **Step 1: Run the whole Python suite**

Run: `python -m pytest tests/unit tests/integration -q`
Expected: PASS with no failures and no errors. Do not use `rtk` — it serves a cached summary that can report stale results.

- [ ] **Step 2: Confirm the generated HTML is in sync**

Run: `npm run build:html && git diff --exit-code frontend/index.html`
Expected: no output, exit code 0. Any diff means `frontend/index.html` was committed stale and CI will fail.

- [ ] **Step 3: Run the E2E suite**

Run: `rtk proxy npx playwright test`
Expected: PASS, including the two new sequence specs.

- [ ] **Step 4: Commit anything outstanding**

```bash
git status
```

Expected: clean tree. If `frontend/index.html` changed in Step 2, commit it:

```bash
git add frontend/index.html
git commit -m "chore: rebuild frontend/index.html"
```

---

## Phase 1 Done — What Ships

A user can build a named execution sequence with dependencies, validate it live, save versions of it, attach it to a job selection or directly to a schedule, and run it. Execution goes through the existing linear executor in topological order with no concurrency, so nothing about today's run behaviour changes.

Phase 2 (`DagExecutor`) is unblocked and starts with characterization tests over the current linear loop, as the spec requires.
