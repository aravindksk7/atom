# Execution Sequence Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add manual hold and condition-based gating to the Execution Sequence so jobs pause after completion and wait for human approval before the next step runs.

**Architecture:** `job_sequence: list[str]` widens to `list[str | SequenceStep]`. A new `run_steps` table materializes each step's lifecycle (`PENDING → RUNNING → HELD → APPROVED|SKIPPED|CANCELLED`). `RunExecutor.execute()` becomes a sequential step loop that checks conditions, sleeps wait delays, and polls the DB when a step is held. Two new API endpoints handle step listing and hold release. The frontend Monitor tab shows a step timeline with an inline release form driven by the existing SSE stream.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Pydantic v2, Alpine.js, SQLite (dev) / Postgres (prod), APScheduler

---

## File Map

| File | Change |
|---|---|
| `etl_framework/repository/models.py` | Add `RunStep` ORM model; add `steps` relationship to `TestRun` |
| `etl_framework/repository/database.py` | Add `run_steps` table to `_ensure_compare_columns` bootstrap |
| `etl_framework/repository/repository.py` | Add `RunStepRepository` class |
| `api/schemas.py` | Add `StepCondition`, `SequenceStep`, `RunStepOut`, `RunStepReleaseRequest`; widen `RunTrigger.job_sequence` |
| `api/services/run_executor.py` | Replace batch `TestRunner.run()` with step loop; add condition gate and hold polling |
| `api/services/notifier.py` | Add `run.held` event; add `notify_held()` helper |
| `api/routes/runs.py` | Add `GET /runs/{run_id}/steps` and `POST /runs/{run_id}/steps/{step_index}/release`; extend SSE payload |
| `api/routes/schedules.py` | Widen `ScheduleCreate.job_sequence` type |
| `frontend/app.js` | Step settings state, release form state, API calls, SSE handler extension |
| `frontend/index.html` | Step settings panel (Launch tab), step timeline + release form (Monitor tab), HELD webhook checkbox |
| `tests/unit/test_run_steps.py` | New — all tests for this feature |

---

## Task 1: `RunStep` ORM model + DB bootstrap

**Files:**
- Modify: `etl_framework/repository/models.py`
- Modify: `etl_framework/repository/database.py`
- Test: `tests/unit/test_run_steps.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_run_steps.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models as _models  # registers all ORM models
from etl_framework.repository.models import RunStep, TestRun


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_run_step_model_columns():
    db = _session()
    run = TestRun(run_id="run-s1", status="PENDING", source_env="dev", target_env="prod")
    db.add(run)
    db.commit()

    step = RunStep(
        run_id="run-s1",
        job_name="orders",
        step_index=0,
        status="PENDING",
        hold_after=True,
        condition={"require_status": ["PASSED"], "max_mismatch_count": 5},
        wait_seconds=10,
    )
    db.add(step)
    db.commit()
    db.refresh(step)

    assert step.id is not None
    assert step.status == "PENDING"
    assert step.hold_after is True
    assert step.condition["max_mismatch_count"] == 5
    assert step.wait_seconds == 10
    assert step.held_at is None
    assert step.release_action is None
```

- [ ] **Step 2: Run test to see it fail**

```
pytest tests/unit/test_run_steps.py::test_run_step_model_columns -v
```
Expected: `ImportError` or `AttributeError` on `RunStep`

- [ ] **Step 3: Add `RunStep` to `etl_framework/repository/models.py`**

Add this block after the `AuditEvent` class (end of file). Also add `steps` relationship to `TestRun`:

```python
# In TestRun class, after the `results` relationship:
steps = relationship("RunStep", back_populates="run",
                     cascade="all, delete-orphan", lazy="select",
                     order_by="RunStep.step_index")
```

New class at end of file:
```python
class RunStep(Base):
    __tablename__ = "run_steps"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(
        String(36),
        ForeignKey("test_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_name = Column(String(255), nullable=False)
    step_index = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="PENDING")
    hold_after = Column(Boolean, nullable=False, default=False)
    condition = Column(JSON, nullable=True)
    wait_seconds = Column(Integer, nullable=False, default=0)
    held_at = Column(DateTime(timezone=True), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    released_by = Column(String(255), nullable=True)
    release_note = Column(Text, nullable=True)
    release_action = Column(String(20), nullable=True)

    run = relationship("TestRun", back_populates="steps")
```

- [ ] **Step 4: Add `run_steps` table to `_ensure_compare_columns` in `etl_framework/repository/database.py`**

Add this block inside the `with bind.begin() as conn:` block, after the audit_events section:

```python
        # --- Execution Sequence Scheduler: run_steps table ---
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS run_steps ("
            "id INTEGER PRIMARY KEY, "
            "run_id VARCHAR(36) REFERENCES test_runs(run_id) ON DELETE CASCADE, "
            "job_name VARCHAR(255) NOT NULL, "
            "step_index INTEGER NOT NULL, "
            "status VARCHAR(20) NOT NULL DEFAULT 'PENDING', "
            "hold_after BOOLEAN NOT NULL DEFAULT 0, "
            "condition JSON, "
            "wait_seconds INTEGER NOT NULL DEFAULT 0, "
            "held_at DATETIME, "
            "released_at DATETIME, "
            "released_by VARCHAR(255), "
            "release_note TEXT, "
            "release_action VARCHAR(20))"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_run_steps_run_id ON run_steps (run_id)"
        ))
```

- [ ] **Step 5: Run test to verify it passes**

```
pytest tests/unit/test_run_steps.py::test_run_step_model_columns -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/repository/database.py tests/unit/test_run_steps.py
git commit -m "feat(steps): add RunStep ORM model and run_steps DB table"
```

---

## Task 2: `RunStepRepository`

**Files:**
- Modify: `etl_framework/repository/repository.py`
- Test: `tests/unit/test_run_steps.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_run_steps.py`:

```python
from datetime import datetime, timezone
from etl_framework.repository.repository import RunStepRepository, RunRepository
from api.schemas import SequenceStep, StepCondition


def _make_run(db: Session, run_id: str) -> None:
    RunRepository(db).create_run(run_id, "dev", "prod", {})


def test_materialize_steps_creates_rows():
    db = _session()
    _make_run(db, "run-m1")
    repo = RunStepRepository(db)
    steps = [
        SequenceStep(job_name="orders", hold_after=True,
                     condition=StepCondition(require_status=["PASSED"]),
                     wait_seconds=5),
        SequenceStep(job_name="customers"),
    ]
    rows = repo.materialize_steps("run-m1", steps)
    assert len(rows) == 2
    assert rows[0].job_name == "orders"
    assert rows[0].step_index == 0
    assert rows[0].hold_after is True
    assert rows[0].condition == {"require_status": ["PASSED"], "max_mismatch_count": None}
    assert rows[0].wait_seconds == 5
    assert rows[1].step_index == 1
    assert rows[1].hold_after is False


def test_update_status_and_get_step():
    db = _session()
    _make_run(db, "run-m2")
    repo = RunStepRepository(db)
    repo.materialize_steps("run-m2", [SequenceStep(job_name="orders")])

    updated = repo.update_status("run-m2", 0, "RUNNING")
    assert updated.status == "RUNNING"

    step = repo.get_step("run-m2", 0)
    assert step.status == "RUNNING"


def test_release_step_approve():
    db = _session()
    _make_run(db, "run-m3")
    repo = RunStepRepository(db)
    repo.materialize_steps("run-m3", [SequenceStep(job_name="orders", hold_after=True)])

    now = datetime.now(timezone.utc)
    repo.update_status("run-m3", 0, "HELD", held_at=now)

    released = repo.release_step("run-m3", 0, "approve", "Looks good", "alice")
    assert released.status == "APPROVED"
    assert released.release_action == "approve"
    assert released.release_note == "Looks good"
    assert released.released_by == "alice"


def test_release_step_returns_none_when_not_held():
    db = _session()
    _make_run(db, "run-m4")
    repo = RunStepRepository(db)
    repo.materialize_steps("run-m4", [SequenceStep(job_name="orders")])
    # status is PENDING, not HELD
    result = repo.release_step("run-m4", 0, "approve", "note", "alice")
    assert result is None


def test_cancel_remaining_steps():
    db = _session()
    _make_run(db, "run-m5")
    repo = RunStepRepository(db)
    repo.materialize_steps("run-m5", [
        SequenceStep(job_name="a"),
        SequenceStep(job_name="b"),
        SequenceStep(job_name="c"),
    ])
    repo.update_status("run-m5", 0, "PASSED")
    repo.cancel_remaining("run-m5", from_index=1)

    steps = repo.list_steps("run-m5")
    assert steps[0].status == "PASSED"
    assert steps[1].status == "CANCELLED"
    assert steps[2].status == "CANCELLED"
```

- [ ] **Step 2: Run tests to see them fail**

```
pytest tests/unit/test_run_steps.py -k "materialize or update_status or release_step or cancel_remaining" -v
```
Expected: `ImportError` on `RunStepRepository`

- [ ] **Step 3: Add `RunStepRepository` to `etl_framework/repository/repository.py`**

First add the import at the top of the file alongside existing model imports:
```python
from etl_framework.repository.models import (
    SavedConfig, SavedJob, TestRun, TestResult, MismatchDetail,
    ApiToken, NotificationHook, NotificationDelivery, ScheduledRun,
    JobLineageEdge, AuditEvent, RunStep,
)
```

Then append this class at the end of `repository.py`:

```python
class RunStepRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def materialize_steps(self, run_id: str, steps: list) -> list[RunStep]:
        rows: list[RunStep] = []
        for i, step in enumerate(steps):
            cond = step.condition.model_dump() if step.condition is not None else None
            row = RunStep(
                run_id=run_id,
                job_name=step.job_name,
                step_index=i,
                status="PENDING",
                hold_after=step.hold_after,
                condition=cond,
                wait_seconds=step.wait_seconds,
            )
            self._db.add(row)
            rows.append(row)
        self._db.commit()
        for row in rows:
            self._db.refresh(row)
        return rows

    def get_step(self, run_id: str, step_index: int) -> RunStep | None:
        return (
            self._db.query(RunStep)
            .filter(RunStep.run_id == run_id, RunStep.step_index == step_index)
            .first()
        )

    def list_steps(self, run_id: str) -> list[RunStep]:
        return (
            self._db.query(RunStep)
            .filter(RunStep.run_id == run_id)
            .order_by(RunStep.step_index)
            .all()
        )

    def update_status(
        self, run_id: str, step_index: int, status: str, **kwargs
    ) -> RunStep | None:
        step = self.get_step(run_id, step_index)
        if step is None:
            return None
        step.status = status
        for k, v in kwargs.items():
            setattr(step, k, v)
        self._db.commit()
        self._db.refresh(step)
        return step

    def release_step(
        self,
        run_id: str,
        step_index: int,
        action: str,
        note: str,
        released_by: str,
    ) -> RunStep | None:
        step = self.get_step(run_id, step_index)
        if step is None or step.status != "HELD":
            return None
        step.status = action.upper()  # APPROVED | SKIPPED | CANCELLED
        step.release_action = action
        step.release_note = note
        step.released_by = released_by
        step.released_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(step)
        return step

    def cancel_remaining(self, run_id: str, from_index: int) -> None:
        (
            self._db.query(RunStep)
            .filter(
                RunStep.run_id == run_id,
                RunStep.step_index >= from_index,
                RunStep.status == "PENDING",
            )
            .update({"status": "CANCELLED"})
        )
        self._db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_run_steps.py -k "materialize or update_status or release_step or cancel_remaining" -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/repository/repository.py tests/unit/test_run_steps.py
git commit -m "feat(steps): add RunStepRepository with materialize, release, cancel"
```

---

## Task 3: Pydantic schemas

**Files:**
- Modify: `api/schemas.py`
- Test: `tests/unit/test_run_steps.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_run_steps.py`:

```python
from api.schemas import SequenceStep, StepCondition, RunTrigger, RunStepReleaseRequest


def test_sequence_step_coerces_from_string():
    # SequenceStep with defaults
    step = SequenceStep(job_name="orders")
    assert step.hold_after is False
    assert step.condition is None
    assert step.wait_seconds == 0


def test_run_trigger_accepts_mixed_job_sequence():
    payload = {
        "source_env": "dev",
        "target_env": "prod",
        "job_sequence": [
            "orders",
            {"job_name": "customers", "hold_after": True,
             "condition": {"require_status": ["PASSED"]}, "wait_seconds": 30},
        ],
    }
    trigger = RunTrigger(**payload)
    assert len(trigger.job_sequence) == 2
    assert isinstance(trigger.job_sequence[0], SequenceStep)
    assert trigger.job_sequence[0].job_name == "orders"
    assert trigger.job_sequence[0].hold_after is False
    assert isinstance(trigger.job_sequence[1], SequenceStep)
    assert trigger.job_sequence[1].hold_after is True
    assert trigger.job_sequence[1].wait_seconds == 30


def test_run_step_release_request_requires_note_and_by():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RunStepReleaseRequest(action="approve", note="", released_by="alice")
    with pytest.raises(ValidationError):
        RunStepReleaseRequest(action="approve", note="ok", released_by="")
    valid = RunStepReleaseRequest(action="approve", note="ok", released_by="alice")
    assert valid.action == "approve"
```

Add `import pytest` at the top of `tests/unit/test_run_steps.py`.

- [ ] **Step 2: Run tests to see them fail**

```
pytest tests/unit/test_run_steps.py -k "coerce or mixed or release_request" -v
```
Expected: `ImportError` on new schema classes

- [ ] **Step 3: Add new schemas to `api/schemas.py`**

Add these classes after the `DQRule` class and before `RunTrigger`:

```python
class StepCondition(BaseModel):
    require_status: list[str] = Field(default_factory=lambda: ["PASSED"])
    max_mismatch_count: int | None = None


class SequenceStep(BaseModel):
    job_name: str
    hold_after: bool = False
    condition: StepCondition | None = None
    wait_seconds: int = Field(default=0, ge=0)


class RunStepOut(BaseModel):
    id: int
    run_id: str
    job_name: str
    step_index: int
    status: str
    hold_after: bool
    condition: dict[str, Any] | None = None
    wait_seconds: int
    held_at: datetime | None = None
    released_at: datetime | None = None
    released_by: str | None = None
    release_note: str | None = None
    release_action: str | None = None

    model_config = {"from_attributes": True}


class RunStepReleaseRequest(BaseModel):
    action: Literal["approve", "skip", "cancel"]
    note: str = Field(min_length=1)
    released_by: str = Field(min_length=1)
```

Then update `RunTrigger.job_sequence` (replace the existing line and add a validator):

```python
class RunTrigger(BaseModel):
    source_env: str
    target_env: str
    job_names: list[str] = Field(default_factory=list)
    job_sequence: list[str | SequenceStep] = Field(default_factory=list)
    config_id: int | None = None
    config_data: dict[str, Any] = Field(default_factory=dict)
    run_settings: RunSettings = Field(default_factory=RunSettings)

    @model_validator(mode="after")
    def normalize_job_sequence(self) -> "RunTrigger":
        # Legacy job_names → job_sequence
        if not self.job_sequence and self.job_names:
            self.job_sequence = list(self.job_names)
        # Coerce all entries to SequenceStep
        coerced: list[SequenceStep] = []
        for item in self.job_sequence:
            if isinstance(item, str):
                coerced.append(SequenceStep(job_name=item))
            elif isinstance(item, dict):
                coerced.append(SequenceStep(**item))
            else:
                coerced.append(item)
        self.job_sequence = coerced
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_run_steps.py -k "coerce or mixed or release_request" -v
```
Expected: all PASS

- [ ] **Step 5: Ensure existing API tests still pass**

```
pytest tests/unit/test_api.py tests/unit/test_new_schemas.py -v
```
Expected: all PASS (the union type is backward-compatible)

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py tests/unit/test_run_steps.py
git commit -m "feat(steps): add SequenceStep, StepCondition, RunStepOut schemas"
```

---

## Task 4: `RunExecutor` — step-by-step loop

**Files:**
- Modify: `api/services/run_executor.py`
- Test: `tests/unit/test_run_steps.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_run_steps.py`:

```python
from api.services.run_executor import RunExecutor
from api.schemas import RunSettings, SequenceStep, StepCondition
from etl_framework.repository.repository import RunRepository, JobRepository, RunStepRepository


def _create_job(db: Session, name: str, source_rows=None, target_rows=None) -> None:
    JobRepository(db).create({
        "name": name,
        "description": name,
        "tags": [],
        "job_type": "reconciliation",
        "query": f"SELECT * FROM {name}",
        "key_columns": ["id"],
        "exclude_columns": [],
        "source_env": None,
        "target_env": None,
        "params": {
            "source_rows": source_rows or [{"id": 1, "value": "ok"}],
            "target_rows": target_rows or [{"id": 1, "value": "ok"}],
        },
        "enabled": True,
    })


def test_step_loop_materializes_run_steps():
    db = _session()
    RunRepository(db).create_run("run-e1", "dev", "prod", {})
    _create_job(db, "orders")
    _create_job(db, "customers")

    RunExecutor(
        db=db,
        run_id="run-e1",
        source_env="dev",
        target_env="prod",
        job_sequence=[SequenceStep(job_name="orders"), SequenceStep(job_name="customers")],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    steps = RunStepRepository(db).list_steps("run-e1")
    assert len(steps) == 2
    assert steps[0].job_name == "orders"
    assert steps[0].status in {"PASSED", "FAILED", "ERROR"}
    assert steps[1].job_name == "customers"
    assert steps[1].status in {"PASSED", "FAILED", "ERROR"}


def test_step_loop_condition_cancels_on_failure():
    db = _session()
    RunRepository(db).create_run("run-e2", "dev", "prod", {})
    # orders will FAIL (mismatch)
    _create_job(db, "orders",
                source_rows=[{"id": 1, "v": 1}],
                target_rows=[{"id": 1, "v": 2}])
    _create_job(db, "customers")

    RunExecutor(
        db=db,
        run_id="run-e2",
        source_env="dev",
        target_env="prod",
        job_sequence=[
            SequenceStep(job_name="orders"),
            SequenceStep(
                job_name="customers",
                condition=StepCondition(require_status=["PASSED"]),
            ),
        ],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    steps = RunStepRepository(db).list_steps("run-e2")
    assert steps[0].status == "FAILED"
    assert steps[1].status == "CANCELLED"

    run = RunRepository(db).get_run("run-e2")
    assert run.status == "CANCELLED"


def test_step_loop_condition_max_mismatch_cancels():
    db = _session()
    RunRepository(db).create_run("run-e3", "dev", "prod", {})
    # orders will have 1 mismatch
    _create_job(db, "orders",
                source_rows=[{"id": 1, "v": 1}],
                target_rows=[{"id": 1, "v": 2}])
    _create_job(db, "customers")

    RunExecutor(
        db=db,
        run_id="run-e3",
        source_env="dev",
        target_env="prod",
        job_sequence=[
            SequenceStep(job_name="orders"),
            SequenceStep(
                job_name="customers",
                condition=StepCondition(require_status=["PASSED", "FAILED"],
                                        max_mismatch_count=0),
            ),
        ],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    steps = RunStepRepository(db).list_steps("run-e3")
    assert steps[1].status == "CANCELLED"
```

- [ ] **Step 2: Run tests to see them fail**

```
pytest tests/unit/test_run_steps.py -k "step_loop" -v
```
Expected: FAIL (executor still does batch execution)

- [ ] **Step 3: Update `RunExecutor.__init__` signature and `execute()` method in `api/services/run_executor.py`**

Change the constructor's `job_sequence` type annotation:
```python
# was: job_sequence: list[str],
job_sequence: list,  # list[str | dict | SequenceStep]
```

Replace the `execute()` method:
```python
def execute(self) -> None:
    from api.schemas import SequenceStep as _Step
    from etl_framework.repository.repository import RunStepRepository

    with span("api.run_executor.execute", {"run_id": self._run_id}):
        set_run_id(self._run_id)
        steps = self._resolve_sequence_steps()
        started_at = datetime.now(timezone.utc)
        self._run_repo.update_run_status(
            self._run_id,
            "RUNNING",
            started_at=started_at,
            total_tests=len(steps),
        )
        step_repo = RunStepRepository(self._db)
        step_repo.materialize_steps(self._run_id, steps)

        try:
            self._apply_health_gate()
            self._execute_steps(steps, step_repo)
        except Exception as exc:
            self._run_repo.update_run_status(
                self._run_id,
                "ERROR",
                completed_at=datetime.now(timezone.utc),
                error=1,
            )
            self._persist_error("<run>", exc)
        finally:
            set_run_id("")
```

Add `_resolve_sequence_steps()` method:
```python
def _resolve_sequence_steps(self) -> list:
    from api.schemas import SequenceStep as _Step
    resolved = []
    for item in self._job_sequence:
        if isinstance(item, str):
            resolved.append(_Step(job_name=item))
        elif isinstance(item, dict):
            resolved.append(_Step(**item))
        else:
            resolved.append(item)
    return resolved
```

Add `_execute_steps()` method:
```python
def _execute_steps(self, steps: list, step_repo) -> None:
    import time
    import os

    poll_interval = int(os.environ.get("HOLD_POLL_INTERVAL_SECONDS", "5"))
    jobs_by_name = {j.name: self._job_to_definition(j) for j in self._job_repo.list()}
    jobs_by_name.update(
        {j.name: j for j in _SEED_JOBS if j.name not in jobs_by_name}
    )

    all_states: list = []
    prev_state = None

    for i, step in enumerate(steps):
        # 1. Condition gate on previous step's result
        if i > 0 and step.condition is not None and prev_state is not None:
            if not self._condition_passes(step.condition, prev_state):
                step_repo.cancel_remaining(self._run_id, i)
                self._complete_run(all_states, cancelled=True)
                return

        # 2. Time delay before this step
        if step.wait_seconds > 0:
            time.sleep(step.wait_seconds)

        # 3. Run the job
        job_def = jobs_by_name.get(step.job_name)
        if job_def is None:
            step_repo.update_status(self._run_id, i, "ERROR")
            self._persist_error(step.job_name, Exception(f"Job '{step.job_name}' not found"))
            step_repo.cancel_remaining(self._run_id, i + 1)
            self._complete_run(all_states, cancelled=True)
            return

        step_repo.update_status(self._run_id, i, "RUNNING")
        states = TestRunner(max_workers=1).run([(job_def.name, self._build_case(job_def))])
        state = states[0]
        all_states.append(state)

        results = self._persist_states([state])
        if results:
            self._write_metrics(results)

        step_status = state.status.value if hasattr(state.status, "value") else str(state.status)
        step_repo.update_status(self._run_id, i, step_status)
        prev_state = state

        # 4. Hold after this step if configured
        if step.hold_after:
            step_repo.update_status(
                self._run_id, i, "HELD", held_at=datetime.now(timezone.utc)
            )
            self._fire_held_event(step.job_name, i)
            # Poll for release
            while True:
                time.sleep(poll_interval)
                self._db.expire_all()
                current = step_repo.get_step(self._run_id, i)
                if current is None or current.status != "HELD":
                    break
            current = step_repo.get_step(self._run_id, i)
            if current and current.release_action == "cancel":
                step_repo.cancel_remaining(self._run_id, i + 1)
                self._complete_run(all_states, cancelled=True)
                return
            # approve or skip — continue to next step

    self._complete_run(all_states)
```

Add `_condition_passes()` method:
```python
def _condition_passes(self, condition, state) -> bool:
    from etl_framework.reconciliation.models import ReconciliationResult

    status_str = state.status.value if hasattr(state.status, "value") else str(state.status)
    if condition.require_status and status_str not in condition.require_status:
        return False
    if condition.max_mismatch_count is not None and isinstance(
        state.result, ReconciliationResult
    ):
        total = (
            (state.result.value_mismatch_count or 0)
            + (state.result.missing_in_target_count or 0)
            + (state.result.missing_in_source_count or 0)
        )
        if total > condition.max_mismatch_count:
            return False
    return True
```

Add `_fire_held_event()` stub (notifier wired in Task 6):
```python
def _fire_held_event(self, job_name: str, step_index: int) -> None:
    try:
        from api.services.notifier import notify_held
        from etl_framework.repository.repository import NotificationRepository
        hooks = NotificationRepository(self._db).list_enabled_for_event("HELD")
        notify_held(self._run_id, job_name, step_index, hooks=hooks, db_session=self._db)
    except Exception:
        pass
```

Update `_complete_run()` to accept `cancelled=False`:
```python
def _complete_run(self, states: list, cancelled: bool = False) -> None:
    if cancelled:
        self._run_repo.update_run_status(
            self._run_id,
            "CANCELLED",
            completed_at=datetime.now(timezone.utc),
            total_tests=len(states),
            passed=sum(1 for s in states if s.status == TestStatus.PASSED),
            failed=sum(1 for s in states if s.status == TestStatus.FAILED),
            slow=sum(1 for s in states if s.status == TestStatus.SLOW),
            error=sum(1 for s in states if s.status == TestStatus.ERROR),
        )
        return
    # ... existing logic unchanged below ...
```

- [ ] **Step 4: Run step loop tests**

```
pytest tests/unit/test_run_steps.py -k "step_loop" -v
```
Expected: all PASS

- [ ] **Step 5: Verify existing executor tests still pass**

```
pytest tests/unit/test_run_executor.py -v
```
Expected: all PASS (plain `list[str]` sequences still work via `_resolve_sequence_steps`)

- [ ] **Step 6: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_steps.py
git commit -m "feat(steps): replace batch execution with per-step loop and condition gating"
```

---

## Task 5: Hold polling integration test

**Files:**
- Test: `tests/unit/test_run_steps.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_run_steps.py`:

```python
import threading


def test_hold_and_approve_continues_sequence():
    db = _session()
    RunRepository(db).create_run("run-h1", "dev", "prod", {})
    _create_job(db, "orders")
    _create_job(db, "customers")

    # Release the hold after 0.1s from a background thread
    def _release_after_delay():
        import time
        time.sleep(0.1)
        step_repo = RunStepRepository(db)
        # Poll until the step is HELD
        for _ in range(20):
            step = step_repo.get_step("run-h1", 0)
            if step and step.status == "HELD":
                break
            time.sleep(0.05)
        step_repo.release_step("run-h1", 0, "approve", "all good", "alice")

    # Reduce poll interval for the test
    import os
    os.environ["HOLD_POLL_INTERVAL_SECONDS"] = "0"

    releaser = threading.Thread(target=_release_after_delay, daemon=True)
    releaser.start()

    RunExecutor(
        db=db,
        run_id="run-h1",
        source_env="dev",
        target_env="prod",
        job_sequence=[
            SequenceStep(job_name="orders", hold_after=True),
            SequenceStep(job_name="customers"),
        ],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    releaser.join(timeout=5)
    steps = RunStepRepository(db).list_steps("run-h1")
    assert steps[0].status == "APPROVED"
    assert steps[1].status in {"PASSED", "FAILED", "ERROR"}

    run = RunRepository(db).get_run("run-h1")
    assert run.status != "CANCELLED"


def test_hold_and_cancel_stops_sequence():
    db = _session()
    RunRepository(db).create_run("run-h2", "dev", "prod", {})
    _create_job(db, "orders")
    _create_job(db, "customers")

    import os
    os.environ["HOLD_POLL_INTERVAL_SECONDS"] = "0"

    def _cancel_after_delay():
        import time
        time.sleep(0.1)
        step_repo = RunStepRepository(db)
        for _ in range(20):
            step = step_repo.get_step("run-h2", 0)
            if step and step.status == "HELD":
                break
            time.sleep(0.05)
        step_repo.release_step("run-h2", 0, "cancel", "stop here", "bob")

    canceller = threading.Thread(target=_cancel_after_delay, daemon=True)
    canceller.start()

    RunExecutor(
        db=db,
        run_id="run-h2",
        source_env="dev",
        target_env="prod",
        job_sequence=[
            SequenceStep(job_name="orders", hold_after=True),
            SequenceStep(job_name="customers"),
        ],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    canceller.join(timeout=5)
    steps = RunStepRepository(db).list_steps("run-h2")
    assert steps[0].status == "CANCELLED"
    assert steps[1].status == "CANCELLED"

    run = RunRepository(db).get_run("run-h2")
    assert run.status == "CANCELLED"
```

- [ ] **Step 2: Run tests**

```
pytest tests/unit/test_run_steps.py -k "hold_and" -v
```
Expected: both PASS (the `HOLD_POLL_INTERVAL_SECONDS=0` makes polling tight)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_run_steps.py
git commit -m "test(steps): add hold polling integration tests"
```

---

## Task 6: `run.held` webhook event

**Files:**
- Modify: `api/services/notifier.py`
- Test: `tests/unit/test_run_steps.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_run_steps.py`:

```python
from api.services.notifier import notify_held, EVENTS


def test_held_in_events_set():
    assert "run.held" in EVENTS


def test_notify_held_calls_hooks(monkeypatch):
    fired = []

    def fake_post(url, payload, secret):
        fired.append((url, payload))
        from api.services.notifier import DeliveryResult
        return DeliveryResult(True, 200)

    monkeypatch.setattr("api.services.notifier._post", fake_post)

    from unittest.mock import MagicMock
    hook = MagicMock()
    hook.enabled = True
    hook.events = ["run.held"]
    hook.url = "http://example.com/webhook"
    hook.secret = None

    notify_held("run-w1", "orders", 0, hooks=[hook], db_session=None)
    assert len(fired) == 1
    assert fired[0][1]["event"] == "run.held"
    assert fired[0][1]["held_step"] == "orders"
    assert fired[0][1]["step_index"] == 0
```

- [ ] **Step 2: Run test to see it fail**

```
pytest tests/unit/test_run_steps.py -k "held_in_events or notify_held" -v
```
Expected: `ImportError` on `notify_held`

- [ ] **Step 3: Update `api/services/notifier.py`**

Add `"run.held"` to the `EVENTS` set:
```python
EVENTS = {
    "run.passed",
    "run.failed",
    "run.slow",
    "run.error",
    "run.completed",
    "run.held",
}
```

Add `notify_held()` function after the `notify()` function:
```python
def notify_held(
    run_id: str,
    held_step: str,
    step_index: int,
    hooks: list | None = None,
    db_session: "Session | None" = None,
) -> None:
    """Fire run.held webhook for a step that is waiting for human approval."""
    if not hooks:
        return

    payload = {
        "run_id": run_id,
        "event": "run.held",
        "held_step": held_step,
        "step_index": step_index,
        "held_at": datetime.now(timezone.utc).isoformat(),
        "release_url": f"/api/runs/{run_id}/steps/{step_index}/release",
    }

    delivery_repo = None
    if db_session is not None:
        from etl_framework.repository.repository import NotificationDeliveryRepository
        delivery_repo = NotificationDeliveryRepository(db_session)

    for hook in hooks:
        if not hook.enabled:
            continue
        if "run.held" not in (hook.events or []):
            continue

        delivery_id = None
        if delivery_repo:
            delivery_attempt = delivery_repo.create_delivery_attempt(
                hook_id=hook.id, run_id=run_id, event="run.held"
            )
            delivery_id = delivery_attempt.id

        target = _post_and_track if delivery_id is not None else _post
        args = (
            (hook.url, payload, hook.secret, delivery_id)
            if delivery_id is not None
            else (hook.url, payload, hook.secret)
        )
        threading.Thread(target=target, args=args, daemon=True).start()
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_run_steps.py -k "held_in_events or notify_held" -v
```
Expected: both PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/notifier.py tests/unit/test_run_steps.py
git commit -m "feat(steps): add run.held webhook event and notify_held helper"
```

---

## Task 7: Step API endpoints

**Files:**
- Modify: `api/routes/runs.py`
- Test: `tests/unit/test_run_steps.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_run_steps.py`:

```python
from fastapi.testclient import TestClient
from api.main import app


def _app_session_override(db: Session):
    from api.dependencies import get_session
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


def test_get_steps_endpoint():
    db = _session()
    RunRepository(db).create_run("run-api1", "dev", "prod", {})
    RunStepRepository(db).materialize_steps("run-api1", [
        SequenceStep(job_name="orders"),
        SequenceStep(job_name="customers", hold_after=True),
    ])
    client = _app_session_override(db)

    resp = client.get("/api/runs/run-api1/steps",
                      headers={"Authorization": "Bearer test"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["job_name"] == "orders"
    assert data[1]["hold_after"] is True


def test_release_endpoint_approve():
    db = _session()
    RunRepository(db).create_run("run-api2", "dev", "prod", {})
    step_repo = RunStepRepository(db)
    step_repo.materialize_steps("run-api2", [SequenceStep(job_name="orders", hold_after=True)])
    step_repo.update_status("run-api2", 0, "HELD",
                            held_at=datetime.now(timezone.utc))
    client = _app_session_override(db)

    resp = client.post(
        "/api/runs/run-api2/steps/0/release",
        json={"action": "approve", "note": "all clear", "released_by": "alice"},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "APPROVED"
    assert data["released_by"] == "alice"


def test_release_endpoint_409_when_not_held():
    db = _session()
    RunRepository(db).create_run("run-api3", "dev", "prod", {})
    RunStepRepository(db).materialize_steps(
        "run-api3", [SequenceStep(job_name="orders")]
    )
    client = _app_session_override(db)

    resp = client.post(
        "/api/runs/run-api3/steps/0/release",
        json={"action": "approve", "note": "ok", "released_by": "alice"},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 409
```

- [ ] **Step 2: Run tests to see them fail**

```
pytest tests/unit/test_run_steps.py -k "get_steps or release_endpoint" -v
```
Expected: 404 responses (endpoints don't exist yet)

- [ ] **Step 3: Add step endpoints to `api/routes/runs.py`**

Add these imports at the top of the runs imports block:
```python
from api.schemas import (
    ...,  # existing imports
    RunStepOut,
    RunStepReleaseRequest,
)
from etl_framework.repository.repository import RunStepRepository
```

Add these two endpoints after the `get_run_progress` endpoint:

```python
@router.get("/{run_id}/steps", response_model=list[RunStepOut])
def list_run_steps(run_id: str, db: Session = Depends(get_session)):
    if RunRepository(db).get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunStepRepository(db).list_steps(run_id)


@router.post("/{run_id}/steps/{step_index}/release", response_model=RunStepOut)
def release_run_step(
    run_id: str,
    step_index: int,
    body: RunStepReleaseRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    if RunRepository(db).get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    step_repo = RunStepRepository(db)
    if step_repo.get_step(run_id, step_index) is None:
        raise HTTPException(status_code=404, detail="Step not found")
    released = step_repo.release_step(
        run_id, step_index, body.action, body.note, body.released_by
    )
    if released is None:
        raise HTTPException(status_code=409, detail="Step is not in HELD status")
    AuditService(db).log(
        request,
        f"step.{body.action}",
        "run_step",
        f"{run_id}/{step_index}",
        {"job_name": released.job_name, "note": body.note, "by": body.released_by},
        actor=body.released_by,
    )
    return released
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_run_steps.py -k "get_steps or release_endpoint" -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add api/routes/runs.py tests/unit/test_run_steps.py
git commit -m "feat(steps): add GET /steps and POST /steps/{index}/release endpoints"
```

---

## Task 8: SSE stream extension

**Files:**
- Modify: `api/routes/runs.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_run_steps.py`:

```python
def test_sse_payload_includes_held_step():
    db = _session()
    RunRepository(db).create_run("run-sse1", "dev", "prod", {})
    step_repo = RunStepRepository(db)
    step_repo.materialize_steps("run-sse1", [
        SequenceStep(job_name="orders", hold_after=True),
        SequenceStep(job_name="customers"),
    ])
    step_repo.update_status("run-sse1", 0, "HELD",
                            held_at=datetime.now(timezone.utc))
    client = _app_session_override(db)

    # Use GET /progress endpoint to verify the new fields (simpler than SSE streaming)
    resp = client.get("/api/runs/run-sse1/progress",
                      headers={"Authorization": "Bearer test"})
    assert resp.status_code == 200
    data = resp.json()
    assert "held_step" in data
    assert data["held_step"] == "orders"
```

- [ ] **Step 2: Run test to see it fail**

```
pytest tests/unit/test_run_steps.py::test_sse_payload_includes_held_step -v
```
Expected: FAIL — `held_step` not in response

- [ ] **Step 3: Update `RunProgressOut` in `api/schemas.py`**

Add two fields to `RunProgressOut`:
```python
class RunProgressOut(BaseModel):
    run_id: str
    status: str
    total_tests: int = 0
    completed_tests: int = 0
    current_job: str | None = None
    percent_complete: int = Field(default=0, ge=0, le=100)
    held_step: str | None = None        # job_name of the currently held step
    current_step: str | None = None     # job_name of the currently running step
```

- [ ] **Step 4: Update `get_run_progress` in `api/routes/runs.py`**

```python
@router.get("/{run_id}/progress", response_model=RunProgressOut)
def get_run_progress(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    total = run.total_tests or 0
    completed = repo.count_completed_results(run_id)
    percent = int(completed / total * 100) if total > 0 else 0

    step_repo = RunStepRepository(db)
    steps = step_repo.list_steps(run_id)
    held_step = next((s.job_name for s in steps if s.status == "HELD"), None)
    current_step = next((s.job_name for s in steps if s.status == "RUNNING"), None)

    return RunProgressOut(
        run_id=run.run_id,
        status=run.status,
        total_tests=total,
        completed_tests=completed,
        current_job=repo.get_current_job(run_id),
        percent_complete=min(percent, 100),
        held_step=held_step,
        current_step=current_step,
    )
```

Also extend the SSE `events()` generator to include the same fields in its payload dict:
```python
# Inside the stream_run events() async generator, update the payload dict:
payload = {
    "run_id": run.run_id,
    "status": run.status,
    "total_tests": total,
    "completed_tests": completed,
    "current_job": repo.get_current_job(run_id),
    "percent_complete": min(percent, 100),
    "held_step": next(
        (s.job_name for s in RunStepRepository(db).list_steps(run_id) if s.status == "HELD"),
        None,
    ),
    "current_step": next(
        (s.job_name for s in RunStepRepository(db).list_steps(run_id) if s.status == "RUNNING"),
        None,
    ),
}
```

- [ ] **Step 5: Run test**

```
pytest tests/unit/test_run_steps.py::test_sse_payload_includes_held_step -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py api/routes/runs.py tests/unit/test_run_steps.py
git commit -m "feat(steps): extend progress and SSE with held_step and current_step fields"
```

---

## Task 9: `ScheduleCreate` schema update

**Files:**
- Modify: `api/routes/schedules.py`
- Test: `tests/unit/test_run_steps.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_run_steps.py`:

```python
from api.routes.schedules import ScheduleCreate


def test_schedule_create_accepts_sequence_steps():
    body = ScheduleCreate(
        name="nightly",
        cron_expr="0 2 * * *",
        job_sequence=[
            "orders",
            {"job_name": "customers", "hold_after": True,
             "condition": {"require_status": ["PASSED"]}, "wait_seconds": 60},
        ],
        source_env="dev",
        target_env="prod",
    )
    assert len(body.job_sequence) == 2
    assert isinstance(body.job_sequence[0], SequenceStep)
    assert body.job_sequence[0].job_name == "orders"
    assert body.job_sequence[1].hold_after is True
```

- [ ] **Step 2: Run test to see it fail**

```
pytest tests/unit/test_run_steps.py::test_schedule_create_accepts_sequence_steps -v
```
Expected: FAIL — `job_sequence` is still `list[str]`

- [ ] **Step 3: Update `ScheduleCreate` in `api/routes/schedules.py`**

Add the import at the top:
```python
from api.schemas import SequenceStep, StepCondition
```

Update the field and add a validator:
```python
class ScheduleCreate(BaseModel):
    name: str
    cron_expr: str
    job_sequence: list[str | SequenceStep] = Field(default_factory=list)
    source_env: str
    target_env: str
    run_settings_json: dict = {}
    enabled: bool = True

    @field_validator("cron_expr")
    @classmethod
    def check_cron(cls, v: str) -> str:
        return _validate_cron(v)

    @model_validator(mode="after")
    def coerce_job_sequence(self) -> "ScheduleCreate":
        coerced: list[SequenceStep] = []
        for item in self.job_sequence:
            if isinstance(item, str):
                coerced.append(SequenceStep(job_name=item))
            elif isinstance(item, dict):
                coerced.append(SequenceStep(**item))
            else:
                coerced.append(item)
        self.job_sequence = coerced
        return self
```

Also add `model_validator` to the import:
```python
from pydantic import BaseModel, Field, field_validator, model_validator
```

Update `ScheduleOut.job_sequence` to `list[dict]` so it serializes correctly:
```python
class ScheduleOut(BaseModel):
    id: int
    name: str
    cron_expr: str
    job_sequence: list[dict]   # was list[str]
    ...
```

- [ ] **Step 4: Update `_run_schedule` in `api/services/scheduler.py`**

The `sched.job_sequence` is now stored as `list[dict | str]`. The executor accepts this natively. No change needed — `_execute_run` passes it through to `RunExecutor` which calls `_resolve_sequence_steps()`.

- [ ] **Step 5: Run test**

```
pytest tests/unit/test_run_steps.py::test_schedule_create_accepts_sequence_steps -v
```
Expected: PASS

- [ ] **Step 6: Run scheduler tests**

```
pytest tests/unit/test_scheduler.py -v
```
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add api/routes/schedules.py tests/unit/test_run_steps.py
git commit -m "feat(steps): widen ScheduleCreate.job_sequence to accept SequenceStep objects"
```

---

## Task 10: Frontend — Launch tab step settings panel

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`

- [ ] **Step 1: Add step settings state to the `jobs` section of `app()` in `frontend/app.js`**

Find the section where `selectedJobs` is declared (around the `jobs` variable). Add:

```javascript
// Step settings per-job (keyed by job name)
stepSettings: {},

// Initialize default step settings for a job
_defaultStepSettings() {
  return { hold_after: false, require_status: ['PASSED'], max_mismatch_count: '', wait_seconds: 0 };
},

// Toggle job selection — initialize step settings when adding
toggleJob(name) {
  const idx = this.selectedJobs.indexOf(name);
  if (idx === -1) {
    this.selectedJobs.push(name);
    if (!this.stepSettings[name]) {
      this.stepSettings[name] = this._defaultStepSettings();
    }
  } else {
    this.selectedJobs.splice(idx, 1);
  }
},
```

- [ ] **Step 2: Update `launchRun()` to build `SequenceStep` payload in `frontend/app.js`**

Find the `launchRun()` method and replace the `job_sequence: [...this.selectedJobs]` line:

```javascript
const hasStepConfig = this.selectedJobs.some(name => {
  const s = this.stepSettings[name];
  return s && (s.hold_after || s.wait_seconds > 0 || s.max_mismatch_count !== '');
});

const jobSequence = hasStepConfig
  ? this.selectedJobs.map(name => {
      const s = this.stepSettings[name] || this._defaultStepSettings();
      const step = { job_name: name };
      if (s.hold_after) step.hold_after = true;
      if (s.wait_seconds > 0) step.wait_seconds = parseInt(s.wait_seconds, 10);
      const hasCondition = s.require_status.length > 0 || s.max_mismatch_count !== '';
      if (hasCondition) {
        step.condition = { require_status: s.require_status };
        if (s.max_mismatch_count !== '') {
          step.condition.max_mismatch_count = parseInt(s.max_mismatch_count, 10);
        }
      }
      return step;
    })
  : [...this.selectedJobs];

const run = await api('POST', '/api/runs', {
  source_env: this.launchSettings.source_env,
  target_env: this.launchSettings.target_env,
  job_sequence: jobSequence,
  config_id: cfg ? cfg.id : null,
  run_settings: this._runSettingsPayload(),
  config_data: cfg ? cfg.config_data : {},
});
```

- [ ] **Step 3: Add step settings panel to the job list in `frontend/index.html`**

Find the job list rendering in the Launch → Jobs sub-tab. After the job name/checkbox row, add the collapsible settings panel. Find the `<template x-for="job in filteredJobs">` section and add below the existing job row content:

```html
<!-- Step settings panel -->
<div x-show="selectedJobs.includes(job.name)" class="ml-6 mt-1 mb-2 p-3 bg-slate-50 border border-slate-200 rounded text-xs space-y-2">
  <div class="flex items-center gap-3">
    <label class="flex items-center gap-1 cursor-pointer">
      <input type="checkbox" :checked="(stepSettings[job.name] || {}).hold_after"
             @change="(stepSettings[job.name] = stepSettings[job.name] || _defaultStepSettings()).hold_after = $event.target.checked" />
      <span class="text-slate-600">Hold after this job</span>
    </label>
  </div>
  <div class="flex items-center gap-3 flex-wrap">
    <span class="text-slate-500">Proceed only if status:</span>
    <template x-for="st in ['PASSED','SLOW','FAILED','ERROR']" :key="st">
      <label class="flex items-center gap-1 cursor-pointer">
        <input type="checkbox"
               :checked="((stepSettings[job.name] || _defaultStepSettings()).require_status || []).includes(st)"
               @change="() => {
                 const s = stepSettings[job.name] = stepSettings[job.name] || _defaultStepSettings();
                 const idx = s.require_status.indexOf(st);
                 if ($event.target.checked && idx === -1) s.require_status.push(st);
                 else if (!$event.target.checked && idx !== -1) s.require_status.splice(idx, 1);
               }" />
        <span x-text="st" class="text-slate-600"></span>
      </label>
    </template>
  </div>
  <div class="flex items-center gap-3">
    <label class="text-slate-500">Max mismatch count:</label>
    <input type="number" min="0"
           :value="(stepSettings[job.name] || {}).max_mismatch_count || ''"
           @input="(stepSettings[job.name] = stepSettings[job.name] || _defaultStepSettings()).max_mismatch_count = $event.target.value"
           class="field-input w-24 text-xs py-0.5" placeholder="any" />
  </div>
  <div class="flex items-center gap-3">
    <label class="text-slate-500">Wait before next job (s):</label>
    <input type="number" min="0"
           :value="(stepSettings[job.name] || {}).wait_seconds || 0"
           @input="(stepSettings[job.name] = stepSettings[job.name] || _defaultStepSettings()).wait_seconds = parseInt($event.target.value) || 0"
           class="field-input w-20 text-xs py-0.5" />
  </div>
</div>
```

- [ ] **Step 4: Verify the Launch tab renders without JS errors**

Run the dev server and open the Launch → Jobs tab. Select a job and confirm the step settings panel appears. Deselect and confirm it hides.

```
python -m uvicorn api.main:app --reload
# Open http://localhost:8000 in a browser
```

- [ ] **Step 5: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat(steps): add per-job step settings panel in Launch tab"
```

---

## Task 11: Frontend — Monitor tab step timeline + release form

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`

- [ ] **Step 1: Add step state and release logic to `app()` in `frontend/app.js`**

In the Monitor section, add:

```javascript
// Run steps
runSteps: [],
releaseForm: { action: 'approve', note: '', released_by: '', loading: false, error: '' },

async loadRunSteps(run_id) {
  try {
    this.runSteps = await api('GET', `/api/runs/${run_id}/steps`);
  } catch (e) {
    this.runSteps = [];
  }
},

async releaseStep(run_id, step_index) {
  this.releaseForm.loading = true;
  this.releaseForm.error = '';
  try {
    await api('POST', `/api/runs/${run_id}/steps/${step_index}/release`, {
      action: this.releaseForm.action,
      note: this.releaseForm.note,
      released_by: this.releaseForm.released_by,
    });
    this.releaseForm.note = '';
    this.releaseForm.released_by = '';
    await this.loadRunSteps(run_id);
  } catch (e) {
    this.releaseForm.error = e.message || 'Release failed';
  } finally {
    this.releaseForm.loading = false;
  }
},
```

In the SSE handler (where the `progress` event is handled), add a call to refresh steps when `held_step` is present:

```javascript
// Inside the SSE onmessage handler for 'progress' events:
if (data.held_step) {
  this.loadRunSteps(this.activeRunId);
}
```

Also call `loadRunSteps` when a run is selected in the Monitor tab.

- [ ] **Step 2: Add step timeline to Monitor tab in `frontend/index.html`**

Find the Monitor tab content area. After the existing progress bar / current-job display, add:

```html
<!-- Step timeline -->
<template x-if="runSteps.length > 0">
  <div class="mt-4 space-y-1">
    <h4 class="text-xs font-semibold text-slate-500 uppercase tracking-wide">Steps</h4>
    <template x-for="step in runSteps" :key="step.step_index">
      <div class="flex flex-col rounded border p-2 text-xs"
           :class="{
             'border-amber-300 bg-amber-50': step.status === 'HELD',
             'border-slate-200 bg-white': step.status !== 'HELD'
           }">
        <div class="flex items-center justify-between">
          <span class="font-medium text-slate-700" x-text="step.job_name"></span>
          <span class="badge text-xs"
                :class="{
                  'badge-amber': step.status === 'HELD',
                  'badge-green': step.status === 'PASSED' || step.status === 'APPROVED',
                  'badge-red': step.status === 'FAILED' || step.status === 'CANCELLED',
                  'badge-blue': step.status === 'RUNNING',
                  'badge-gray': step.status === 'PENDING' || step.status === 'SKIPPED'
                }"
                x-text="step.status"></span>
        </div>

        <!-- Release form for HELD steps -->
        <template x-if="step.status === 'HELD'">
          <div class="mt-2 space-y-2 border-t border-amber-200 pt-2">
            <p class="text-amber-700 text-xs">Waiting for approval before next step runs.</p>
            <div class="flex items-center gap-2">
              <label class="text-slate-500">Action:</label>
              <select x-model="releaseForm.action" class="field-input text-xs py-0.5">
                <option value="approve">Approve — continue</option>
                <option value="skip">Skip — skip result, continue</option>
                <option value="cancel">Cancel run</option>
              </select>
            </div>
            <div>
              <input x-model="releaseForm.note" class="field-input text-xs py-0.5 w-full"
                     placeholder="Note (required)" />
            </div>
            <div>
              <input x-model="releaseForm.released_by" class="field-input text-xs py-0.5 w-full"
                     placeholder="Your name (required)" />
            </div>
            <p x-show="releaseForm.error" x-text="releaseForm.error" class="text-red-600 text-xs"></p>
            <button
              :disabled="!releaseForm.note.trim() || !releaseForm.released_by.trim() || releaseForm.loading"
              @click="releaseStep(activeRunId, step.step_index)"
              class="btn-primary text-xs py-1 px-3">
              <span x-show="!releaseForm.loading">Release</span>
              <span x-show="releaseForm.loading">Releasing…</span>
            </button>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>
```

- [ ] **Step 3: Verify Monitor tab renders correctly**

Run the app, trigger a run with a `hold_after` step, and verify:
1. The step timeline appears
2. The HELD step shows the amber release form
3. Filling note + name and clicking Release updates the step status

```
python -m uvicorn api.main:app --reload
```

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat(steps): add step timeline and hold release form in Monitor tab"
```

---

## Task 12: Frontend — Schedules modal step config

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`

- [ ] **Step 1: Update schedule modal state in `frontend/app.js`**

Find where `scheduleModal` is initialized. Replace `job_sequence_raw: ''` with `schedule_steps`:

```javascript
openScheduleModal() {
  this.scheduleModal = {
    name: '', cron_expr: '0 6 * * *',
    source_env: 'dev', target_env: 'prod',
    schedule_steps: [],   // was: job_sequence_raw: ''
    enabled: true,
  };
  this.scheduleModalEditing = false;
},

editScheduleModal(sched) {
  this.scheduleModal = {
    id: sched.id,
    name: sched.name,
    cron_expr: sched.cron_expr,
    source_env: sched.source_env,
    target_env: sched.target_env,
    schedule_steps: (sched.job_sequence || []).map(s =>
      typeof s === 'string'
        ? { job_name: s, hold_after: false, wait_seconds: 0, require_status: ['PASSED'], max_mismatch_count: '' }
        : { job_name: s.job_name, hold_after: s.hold_after || false,
            wait_seconds: s.wait_seconds || 0,
            require_status: (s.condition && s.condition.require_status) || ['PASSED'],
            max_mismatch_count: (s.condition && s.condition.max_mismatch_count != null)
              ? String(s.condition.max_mismatch_count) : '' }
    ),
    enabled: sched.enabled,
  };
  this.scheduleModalEditing = true;
},
```

Update `saveSchedule()` to build `job_sequence` from `schedule_steps`:

```javascript
const job_sequence = (this.scheduleModal.schedule_steps || []).map(s => {
  const step = { job_name: s.job_name };
  if (s.hold_after) step.hold_after = true;
  if (s.wait_seconds > 0) step.wait_seconds = parseInt(s.wait_seconds, 10);
  const hasCondition = (s.require_status && s.require_status.length > 0) || s.max_mismatch_count !== '';
  if (hasCondition) {
    step.condition = { require_status: s.require_status || ['PASSED'] };
    if (s.max_mismatch_count !== '') {
      step.condition.max_mismatch_count = parseInt(s.max_mismatch_count, 10);
    }
  }
  return step;
});
```

Add helpers to add/remove steps in the modal:

```javascript
addScheduleStep() {
  this.scheduleModal.schedule_steps.push({
    job_name: '', hold_after: false, wait_seconds: 0,
    require_status: ['PASSED'], max_mismatch_count: ''
  });
},
removeScheduleStep(idx) {
  this.scheduleModal.schedule_steps.splice(idx, 1);
},
```

- [ ] **Step 2: Update the schedule modal UI in `frontend/index.html`**

Replace the `job_sequence_raw` text input in the schedule modal with a per-step builder:

```html
<!-- Job sequence steps -->
<div>
  <label class="field-label">Job Sequence</label>
  <div class="space-y-2 mt-1">
    <template x-for="(step, idx) in scheduleModal.schedule_steps" :key="idx">
      <div class="border border-slate-200 rounded p-2 space-y-1 text-xs bg-slate-50">
        <div class="flex items-center gap-2">
          <input x-model="step.job_name" class="field-input flex-1 text-xs py-0.5" placeholder="Job name" />
          <button @click="removeScheduleStep(idx)" class="text-red-500 hover:text-red-700 text-sm leading-none">&times;</button>
        </div>
        <div class="flex items-center gap-3 flex-wrap">
          <label class="flex items-center gap-1 cursor-pointer">
            <input type="checkbox" x-model="step.hold_after" />
            <span class="text-slate-600">Hold after</span>
          </label>
          <span class="text-slate-400">|</span>
          <span class="text-slate-500">Wait (s):</span>
          <input type="number" x-model.number="step.wait_seconds" min="0"
                 class="field-input w-16 text-xs py-0.5" />
          <span class="text-slate-400">|</span>
          <span class="text-slate-500">Max mismatches:</span>
          <input type="number" x-model="step.max_mismatch_count" min="0"
                 class="field-input w-20 text-xs py-0.5" placeholder="any" />
        </div>
      </div>
    </template>
  </div>
  <button @click="addScheduleStep()" class="mt-2 text-xs text-indigo-600 hover:underline">+ Add step</button>
</div>
```

- [ ] **Step 3: Verify schedule modal works end-to-end**

Open the Schedules sub-tab, create a schedule with multiple steps (some with holds), save it, reopen it, and verify the step config round-trips correctly.

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat(steps): update schedules modal to support per-step hold and condition config"
```

---

## Task 13: Frontend — HELD webhook event checkbox

**Files:**
- Modify: `frontend/index.html`

- [ ] **Step 1: Find the webhook event selector in `frontend/index.html`**

Search for the existing event checkboxes (PASSED, FAILED, ERROR etc.) in the webhook creation/edit modal.

- [ ] **Step 2: Add the HELD checkbox alongside the existing ones**

```html
<label class="flex items-center gap-2 cursor-pointer">
  <input type="checkbox"
         :checked="(hookModal.events || []).includes('run.held')"
         @change="() => {
           const ev = 'run.held';
           const idx = (hookModal.events = hookModal.events || []).indexOf(ev);
           if ($event.target.checked && idx === -1) hookModal.events.push(ev);
           else if (!$event.target.checked && idx !== -1) hookModal.events.splice(idx, 1);
         }" />
  <span class="text-slate-600 text-sm">HELD <span class="text-slate-400 text-xs">(step waiting for approval)</span></span>
</label>
```

- [ ] **Step 3: Verify webhook modal shows the new checkbox**

Open the Notifications/Webhooks section, create or edit a webhook, and confirm `HELD` appears as a selectable event.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html
git commit -m "feat(steps): add HELD event to webhook event selector"
```

---

## Task 14: Full regression check

- [ ] **Step 1: Run the full test suite**

```
pytest tests/ -v --tb=short
```
Expected: all existing tests pass; new tests in `test_run_steps.py` pass

- [ ] **Step 2: Smoke test the app manually**

```
python -m uvicorn api.main:app --reload
```

1. Create two jobs in the Jobs tab
2. Select both, set hold_after on the first, click Run
3. Open Monitor tab — confirm first step shows HELD badge and release form
4. Fill note + name, click Approve — confirm sequence continues and second job runs
5. Trigger another run, cancel at the hold — confirm second step shows CANCELLED

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: execution sequence scheduler with manual holds and condition gating"
```
