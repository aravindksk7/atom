# Scheduler / Run-Executor Gap Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the seven correctness/robustness gaps found in the run-orchestration investigation: stale `prev_result` in condition gates, condition-break masquerading as normal completion, unbounded hold polling, coarse cancel granularity, cross-process schedule double-fire, duplicated chunk loader (with an either-side-exhausted truncation bug), and dead topo-sort code replaced by real dependency-order validation. Adds a memory guardrail (`max_compare_rows`) as scoped mitigation for full-frame memory usage.

**Architecture:** All changes stay inside the existing synchronous-loop design — no queue, no new infra. `RunExecutor.execute()` loop gains a `blocked` outcome and per-iteration cancel checks; `_poll_for_release` gains timeout + cancel awareness; the APScheduler wrapper gains an active-run DB guard; the chunk loader moves to `etl_framework/reconciliation/chunker.py` and is shared by engine + compare service.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy (sqlite in-memory for tests), APScheduler, pandas, pytest.

**Out of scope (deliberate):** true streaming/chunk-wise compare (design change; `max_compare_rows` guardrail instead), mid-compare cancellation (interrupting a running pandas merge), DuckDB backend in RunExecutor, frontend BLOCKED badge styling (status renders as plain text already).

**Test conventions in this repo:** unit tests build an in-memory sqlite session via a local `_session()` helper (see `tests/unit/test_run_executor.py:13-20`), create runs with `RunRepository(db).create_run(...)`, jobs with `JobRepository(db).create({...})`, and drive `RunExecutor(...).execute()` directly. Follow that pattern exactly. Run tests with `python -m pytest <path> -v` from `c:\atom`.

---

### Task 1: Condition gate evaluates the *immediate* predecessor; gate-break sets run status BLOCKED

**Problem:** `prev_result` is only reassigned when a step yields a result ([api/services/run_executor.py:221-224](../../api/services/run_executor.py)). A step that errors without producing a `ReconciliationResult` leaves `prev_result` pointing at an older step, so the next step's condition gate evaluates the wrong step — or is skipped entirely (`prev_result is None` skips the gate). Also, a condition-gate break exits with `cancelled=False`, so the run completes via `_complete_run` as if nothing was skipped.

**Fix:** Always reassign `prev_result` each iteration (`None` when the step produced no result). A condition on step `i>0` with `prev_result is None` **blocks** (conservative). Gate-break sets new run status `BLOCKED`.

**Files:**
- Modify: `api/services/run_executor.py` (execute loop, ~lines 194-255)
- Test: `tests/unit/test_run_executor_gates.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_run_executor_gates.py`:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import RunSettings, SequenceStep, StepCondition
from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    JobRepository,
    RunRepository,
    RunStepRepository,
)


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_job(db, name, source_rows, target_rows):
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
        "params": {"source_rows": source_rows, "target_rows": target_rows},
        "enabled": True,
    })


def test_condition_blocks_after_resultless_error_step():
    """Step A errors without a result (schema policy error). Step B's condition
    must NOT be evaluated against a stale result or skipped — it must block."""
    db = _session()
    RunRepository(db).create_run("run-g1", "dev", "prod", {})
    # schema mismatch + policy=error -> case raises -> ERROR state, no result
    _make_job(db, "job_a", [{"id": 1, "amount": 1.0}], [{"id": 1}])
    _make_job(db, "job_b", [{"id": 1, "v": 1}], [{"id": 1, "v": 1}])

    RunExecutor(
        db=db,
        run_id="run-g1",
        source_env="dev",
        target_env="prod",
        job_sequence=[
            SequenceStep(job_name="job_a"),
            SequenceStep(job_name="job_b",
                         condition=StepCondition(require_status=["PASSED"])),
        ],
        run_settings=RunSettings(schema_mismatch_policy="error",
                                 metrics_enabled=False),
    ).execute()

    steps = RunStepRepository(db).list_steps("run-g1")
    assert steps[0].status == "ERROR"
    assert steps[1].status == "CANCELLED"
    run = RunRepository(db).get_run("run-g1")
    assert run.status == "BLOCKED"


def test_condition_break_sets_blocked_not_passed():
    """Step A FAILS (value mismatch); step B requires PASSED. Run must end
    BLOCKED, with A's failure still counted."""
    db = _session()
    RunRepository(db).create_run("run-g2", "dev", "prod", {})
    _make_job(db, "job_a", [{"id": 1, "amount": 10.0}], [{"id": 1, "amount": 9.0}])
    _make_job(db, "job_b", [{"id": 1, "v": 1}], [{"id": 1, "v": 1}])

    RunExecutor(
        db=db,
        run_id="run-g2",
        source_env="dev",
        target_env="prod",
        job_sequence=[
            SequenceStep(job_name="job_a"),
            SequenceStep(job_name="job_b",
                         condition=StepCondition(require_status=["PASSED"])),
        ],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-g2")
    assert run.status == "BLOCKED"
    assert run.failed == 1
    assert run.completed_at is not None
    steps = RunStepRepository(db).list_steps("run-g2")
    assert steps[1].status == "CANCELLED"


def test_condition_on_first_step_is_ignored():
    """A condition on step 0 has nothing to evaluate; step must still run."""
    db = _session()
    RunRepository(db).create_run("run-g3", "dev", "prod", {})
    _make_job(db, "job_a", [{"id": 1, "v": 1}], [{"id": 1, "v": 1}])

    RunExecutor(
        db=db,
        run_id="run-g3",
        source_env="dev",
        target_env="prod",
        job_sequence=[
            SequenceStep(job_name="job_a",
                         condition=StepCondition(require_status=["PASSED"])),
        ],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-g3")
    assert run.status == "PASSED"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/unit/test_run_executor_gates.py -v`
Expected: first two tests FAIL (step 1 runs job_b / run status is PASSED-or-FAILED, not BLOCKED). Third may already pass — fine.

- [ ] **Step 3: Implement the loop changes**

In `api/services/run_executor.py`, inside `execute()`:

Replace (currently ~lines 196-232):

```python
                prev_result: ReconciliationResult | None = None
                cancelled = False
                jobs_index = self._build_jobs_index()

                for i, seq_step in enumerate(steps):
                    # Condition gate: check previous step's outcome before running this step
                    if seq_step.condition is not None and prev_result is not None:
                        if not self._check_condition(seq_step.condition, prev_result):
                            step_repo.cancel_remaining(self._run_id, from_index=i)
                            break
```

with:

```python
                prev_result: ReconciliationResult | None = None
                cancelled = False
                blocked = False
                jobs_index = self._build_jobs_index()

                for i, seq_step in enumerate(steps):
                    # Condition gate: evaluate the immediately preceding step's
                    # outcome. A predecessor that produced no result (ERROR,
                    # unknown job) blocks conservatively.
                    if seq_step.condition is not None and i > 0:
                        if prev_result is None or not self._check_condition(
                            seq_step.condition, prev_result
                        ):
                            step_repo.cancel_remaining(self._run_id, from_index=i)
                            blocked = True
                            break
```

Replace the unknown-job branch (~lines 212-215):

```python
                    job_def = jobs_index.get(seq_step.job_name)
                    if job_def is None:
                        step_repo.update_status(self._run_id, i, "ERROR")
                        continue
```

with:

```python
                    job_def = jobs_index.get(seq_step.job_name)
                    if job_def is None:
                        step_repo.update_status(self._run_id, i, "ERROR")
                        prev_result = None
                        continue
```

Replace the result bookkeeping (~lines 221-224):

```python
                    step_results = self._persist_states([state])
                    if step_results:
                        prev_result = step_results[0]
                        all_results.extend(step_results)
```

with:

```python
                    step_results = self._persist_states([state])
                    prev_result = step_results[0] if step_results else None
                    all_results.extend(step_results)
```

Replace the completion branch (~lines 246-255):

```python
                if cancelled:
                    self._run_repo.update_run_status(
                        self._run_id,
                        "CANCELLED",
                        completed_at=datetime.now(timezone.utc),
                    )
                    self._fire_webhooks("CANCELLED")
                else:
                    self._write_metrics(all_results)
                    self._complete_run(all_states)
```

with:

```python
                if cancelled:
                    self._run_repo.update_run_status(
                        self._run_id,
                        "CANCELLED",
                        completed_at=datetime.now(timezone.utc),
                    )
                    self._fire_webhooks("CANCELLED")
                elif blocked:
                    self._write_metrics(all_results)
                    self._run_repo.update_run_status(
                        self._run_id,
                        "BLOCKED",
                        completed_at=datetime.now(timezone.utc),
                        total_tests=len(all_states),
                        passed=sum(1 for s in all_states if s.status == TestStatus.PASSED),
                        failed=sum(1 for s in all_states if s.status == TestStatus.FAILED),
                        error=sum(1 for s in all_states if s.status == TestStatus.ERROR),
                    )
                    self._fire_webhooks("BLOCKED")
                else:
                    self._write_metrics(all_results)
                    self._complete_run(all_states)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `python -m pytest tests/unit/test_run_executor_gates.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Run full run-executor suite for regressions**

Run: `python -m pytest tests/unit/test_run_executor.py tests/unit/test_run_steps.py tests/unit/test_run_cancel.py -v`
Expected: all PASS. If an existing test asserts the old gate-skip behavior, update it to expect BLOCKED and note it in the commit message.

- [ ] **Step 6: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_executor_gates.py
git commit -m "fix: condition gates evaluate immediate predecessor; gate-break yields BLOCKED run status"
```

---

### Task 2: Cancel responsiveness — check before each step and during wait_seconds

**Problem:** Cancel flag checked only *after* a step completes ([api/services/run_executor.py:229](../../api/services/run_executor.py)). A cancel requested during a long `wait_seconds` sleep or before the first step is ignored until a whole step has run.

**Files:**
- Modify: `api/services/run_executor.py`
- Test: `tests/unit/test_run_executor_gates.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_run_executor_gates.py`:

```python
def test_precancelled_run_executes_no_steps():
    db = _session()
    RunRepository(db).create_run("run-c1", "dev", "prod", {})
    _make_job(db, "job_a", [{"id": 1, "v": 1}], [{"id": 1, "v": 1}])
    repo = RunRepository(db)
    repo.request_cancel("run-c1")

    RunExecutor(
        db=db,
        run_id="run-c1",
        source_env="dev",
        target_env="prod",
        job_sequence=[SequenceStep(job_name="job_a", wait_seconds=0)],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = repo.get_run("run-c1")
    assert run.status == "CANCELLED"
    steps = RunStepRepository(db).list_steps("run-c1")
    assert steps[0].status == "CANCELLED"
    assert run.results == []
```

- [ ] **Step 2: Run test, verify it fails**

Run: `python -m pytest tests/unit/test_run_executor_gates.py::test_precancelled_run_executes_no_steps -v`
Expected: FAIL — job_a runs and run finishes PASSED.

- [ ] **Step 3: Implement**

In `execute()`, immediately after the condition-gate block and before `if seq_step.wait_seconds > 0:`, insert:

```python
                    if self._run_repo.is_cancel_requested(self._run_id):
                        step_repo.cancel_remaining(self._run_id, from_index=i)
                        cancelled = True
                        break
```

Replace `if seq_step.wait_seconds > 0: time.sleep(seq_step.wait_seconds)` with:

```python
                    if seq_step.wait_seconds > 0 and self._sleep_with_cancel_check(
                        seq_step.wait_seconds
                    ):
                        step_repo.cancel_remaining(self._run_id, from_index=i)
                        cancelled = True
                        break
```

Add method to `RunExecutor` (place next to `_poll_for_release`):

```python
    def _sleep_with_cancel_check(self, seconds: float) -> bool:
        """Sleep up to `seconds` in 1s slices, checking the cancel flag between
        slices. Returns True if cancellation was requested."""
        remaining = float(seconds)
        while remaining > 0:
            time.sleep(min(1.0, remaining))
            remaining -= 1.0
            if self._run_repo.is_cancel_requested(self._run_id):
                return True
        return False
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/unit/test_run_executor_gates.py tests/unit/test_run_cancel.py tests/integration/test_cancel_flow.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_executor_gates.py
git commit -m "fix: honor cancel requests before each step and during wait_seconds"
```

---

### Task 3: Hold poll timeout + cancel awareness

**Problem:** `_poll_for_release` ([api/services/run_executor.py:308-314](../../api/services/run_executor.py)) loops forever — a held run pins its worker thread indefinitely, and a run-level cancel request is ignored while held.

**Fix:** `HOLD_TIMEOUT_SECONDS` env (default 86400 = 24h; 0 disables). On timeout: step → CANCELLED with `release_note="hold timed out"`, poll returns `"cancel"` (existing caller already cancels remaining + run). Also check `is_cancel_requested` each poll tick.

**Files:**
- Modify: `api/services/run_executor.py`
- Test: `tests/integration/test_hold_polling.py` (extend — read the file first and reuse its thread/monkeypatch helpers if present)

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_hold_polling.py` (adapt the `_session`/job-creation helpers already in that file; if it defines its own fixtures, use them instead of redefining):

```python
def test_hold_times_out_and_cancels(monkeypatch):
    import api.services.run_executor as rex
    monkeypatch.setattr(rex, "HOLD_POLL_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(rex, "HOLD_TIMEOUT_SECONDS", 0.15)

    db = _session()
    RunRepository(db).create_run("run-ht1", "dev", "prod", {})
    _make_job(db, "job_a", [{"id": 1, "v": 1}], [{"id": 1, "v": 1}])

    RunExecutor(
        db=db,
        run_id="run-ht1",
        source_env="dev",
        target_env="prod",
        job_sequence=[SequenceStep(job_name="job_a", hold_after=True)],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()  # returns because hold times out — no thread needed

    run = RunRepository(db).get_run("run-ht1")
    assert run.status == "CANCELLED"
    step = RunStepRepository(db).get_step("run-ht1", 0)
    assert step.status == "CANCELLED"
    assert step.release_note == "hold timed out"


def test_cancel_request_releases_held_step(monkeypatch):
    import threading
    import api.services.run_executor as rex
    monkeypatch.setattr(rex, "HOLD_POLL_INTERVAL_SECONDS", 0.05)

    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-ht2", "dev", "prod", {})
    _make_job(db, "job_a", [{"id": 1, "v": 1}], [{"id": 1, "v": 1}])

    ex = RunExecutor(
        db=db,
        run_id="run-ht2",
        source_env="dev",
        target_env="prod",
        job_sequence=[SequenceStep(job_name="job_a", hold_after=True)],
        run_settings=RunSettings(metrics_enabled=False),
    )
    t = threading.Thread(target=ex.execute)
    t.start()
    import time as _t
    deadline = _t.monotonic() + 5
    while _t.monotonic() < deadline:
        step = RunStepRepository(db).get_step("run-ht2", 0)
        if step is not None and step.status == "HELD":
            break
        _t.sleep(0.05)
    repo.request_cancel("run-ht2")
    t.join(timeout=5)
    assert not t.is_alive()
    assert repo.get_run("run-ht2").status == "CANCELLED"
```

Note: these tests share one sqlite `StaticPool` session across threads — the existing `test_hold_polling.py` already does this; follow whatever session-sharing approach it uses.

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/integration/test_hold_polling.py -v -k "times_out or releases_held"`
Expected: first FAILS by hanging is possible — pytest-timeout is not guaranteed installed, so `HOLD_TIMEOUT_SECONDS` won't exist yet → `AttributeError` from monkeypatch. That counts as the failing state.

- [ ] **Step 3: Implement**

In `api/services/run_executor.py`, next to the existing constant (line 42):

```python
HOLD_TIMEOUT_SECONDS = float(os.environ.get("HOLD_TIMEOUT_SECONDS", "86400"))
```

Replace `_poll_for_release`:

```python
    def _poll_for_release(self, step_repo: RunStepRepository, step_index: int) -> str:
        waited = 0.0
        while True:
            time.sleep(HOLD_POLL_INTERVAL_SECONDS)
            waited += HOLD_POLL_INTERVAL_SECONDS
            self._db.expire_all()
            if self._run_repo.is_cancel_requested(self._run_id):
                return "cancel"
            step = step_repo.get_step(self._run_id, step_index)
            if step is None or step.status != "HELD":
                return (step.release_action or "approve") if step else "approve"
            if HOLD_TIMEOUT_SECONDS > 0 and waited >= HOLD_TIMEOUT_SECONDS:
                step_repo.update_status(
                    self._run_id, step_index, "CANCELLED",
                    release_action="cancel",
                    release_note="hold timed out",
                    released_at=datetime.now(timezone.utc),
                )
                return "cancel"
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/integration/test_hold_polling.py -v`
Expected: all PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py tests/integration/test_hold_polling.py
git commit -m "fix: hold polling honors HOLD_TIMEOUT_SECONDS and run cancel requests"
```

---

### Task 4: Scheduler overlap guard — skip fire while previous run still active

**Problem:** No cross-process lock; two API processes double-fire the same schedule, and a run outlasting its cron interval can stack with the next fire (`max_instances` guards only in-process, and only implicitly).

**Fix:** (a) explicit `max_instances=1, coalesce=True` on `_scheduler.add_job`; (b) DB guard in `_run_schedule`: skip when a PENDING/RUNNING run exists for the same selection.

**Files:**
- Modify: `etl_framework/repository/repository.py` (`RunRepository`), `api/services/scheduler.py`
- Test: `tests/unit/test_repository.py`, `tests/unit/test_scheduler.py` (extend both; read `tests/unit/test_scheduler.py` first — it exists and shows how `_run_schedule` is currently driven, including how `SessionLocal` is monkeypatched)

- [ ] **Step 1: Write failing repository test**

Append to `tests/unit/test_repository.py` (reuse its existing sqlite `_session`-style helper):

```python
def test_has_active_run_for_selection():
    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-a1", "dev", "prod", {}, selection_id=7, selection_version=1)
    assert repo.has_active_run_for_selection(7) is True     # PENDING counts
    assert repo.has_active_run_for_selection(8) is False

    repo.update_run_status("run-a1", "PASSED")
    assert repo.has_active_run_for_selection(7) is False

    repo.create_run("run-a2", "dev", "prod", {}, selection_id=7, selection_version=1)
    repo.update_run_status("run-a2", "RUNNING")
    assert repo.has_active_run_for_selection(7) is True
```

(Verify `create_run` keyword names against the actual signature in `etl_framework/repository/repository.py` before running — the scheduler calls it with `selection_id=`/`selection_version=`, so they exist.)

- [ ] **Step 2: Run test, verify it fails**

Run: `python -m pytest tests/unit/test_repository.py::test_has_active_run_for_selection -v`
Expected: FAIL with `AttributeError: ... has_active_run_for_selection`.

- [ ] **Step 3: Implement repository method**

In `etl_framework/repository/repository.py`, inside `RunRepository` (next to `is_cancel_requested`):

```python
    def has_active_run_for_selection(self, selection_id: int) -> bool:
        return (
            self._db.query(TestRun)
            .filter(
                TestRun.selection_id == selection_id,
                TestRun.status.in_(("PENDING", "RUNNING")),
            )
            .count()
            > 0
        )
```

(`TestRun` is already imported at the top of the module.)

- [ ] **Step 4: Run test, verify pass, commit**

Run: `python -m pytest tests/unit/test_repository.py -v` — all PASS.

```bash
git add etl_framework/repository/repository.py tests/unit/test_repository.py
git commit -m "feat: RunRepository.has_active_run_for_selection"
```

- [ ] **Step 5: Write failing scheduler test**

Append to `tests/unit/test_scheduler.py`, following that file's existing pattern for driving `_run_schedule` with a monkeypatched `SessionLocal` (copy how existing tests there build schedule + selection rows):

```python
def test_run_schedule_skips_when_previous_run_active(monkeypatch, ...):
    # Arrange (mirror the existing happy-path _run_schedule test's setup):
    #   1. create schedule + selection version rows
    #   2. create a run with the schedule's selection_id, status RUNNING
    #   3. monkeypatch SessionLocal used inside _run_schedule
    # Act: scheduler._run_schedule(schedule_id, "nightly")
    # Assert: run count unchanged (no new run row created)
    ...
```

The `...` above must be filled from the existing happy-path test in the same file — copy its arrangement verbatim, then add the pre-existing RUNNING run and the run-count assertion:

```python
    before = db.query(TestRun).count()
    scheduler._run_schedule(sched.id, sched.name)
    assert db.query(TestRun).count() == before
```

- [ ] **Step 6: Run test, verify it fails**

Run: `python -m pytest tests/unit/test_scheduler.py -v -k skips_when_previous`
Expected: FAIL — a new run row is created.

- [ ] **Step 7: Implement scheduler guard**

In `api/services/scheduler.py` `_run_schedule`, after the selection-version check (line 46's `return`), insert:

```python
        run_repo = RunRepository(db)
        if run_repo.has_active_run_for_selection(sched.selection_id):
            logger.warning(
                "Schedule '%s' skipped: a run for selection %s is still active",
                name, sched.selection_id,
            )
            return
```

and reuse `run_repo` for the existing `RunRepository(db).create_run(...)` call below (replace with `run_repo.create_run(...)`).

In `_add_job`, add explicit overlap controls to `add_job(...)`:

```python
        _scheduler.add_job(
            _run_schedule,
            trigger=trigger,
            id=_job_id(sched.id),
            args=[sched.id, sched.name],
            replace_existing=True,
            misfire_grace_time=300,
            max_instances=1,
            coalesce=True,
        )
```

- [ ] **Step 8: Run tests, verify pass, commit**

Run: `python -m pytest tests/unit/test_scheduler.py tests/unit/test_schedules_selection_refactor.py -v` — all PASS.

```bash
git add api/services/scheduler.py tests/unit/test_scheduler.py
git commit -m "fix: skip scheduled fire while previous run for same selection is active"
```

---

### Task 5: Extract shared chunk loader; fix either-side-exhausted truncation

**Problem:** Two near-identical OFFSET/FETCH loaders: [engine.py:125-146](../../etl_framework/reconciliation/engine.py) and [compare_service.py:27-51](../../api/services/compare_service.py). The engine's interleaved loop also `break`s when *either* side returns a short chunk (line 143), truncating the longer side and undercounting missing rows.

**Fix:** One per-side loader `load_in_chunks` in `etl_framework/reconciliation/chunker.py`; both call sites delegate. Per-side loading inherently fixes the truncation bug.

**Files:**
- Modify: `etl_framework/reconciliation/chunker.py`, `etl_framework/reconciliation/engine.py`, `api/services/compare_service.py`
- Test: `tests/unit/test_chunker.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_chunker.py`:

```python
import re
import pandas as pd

from etl_framework.reconciliation.chunker import load_in_chunks


class _WindowEngine:
    """Fake engine honoring the OFFSET/FETCH pagination emitted by build_chunk_query."""

    def __init__(self, name: str, df: pd.DataFrame) -> None:
        self._env = type("E", (), {"name": name})()
        self._df = df

    def execute_query(self, query: str, params=None) -> pd.DataFrame:
        m = re.search(r"OFFSET\s+(\d+)\s+ROWS\s+FETCH\s+NEXT\s+(\d+)", query, re.I)
        if not m:
            return self._df.copy()
        o, n = int(m.group(1)), int(m.group(2))
        return self._df.iloc[o:o + n].reset_index(drop=True)


def test_load_in_chunks_paginates_fully():
    df = pd.DataFrame({"id": [1, 2, 3, 4, 5], "v": list("abcde")})
    out = load_in_chunks(_WindowEngine("dev", df), "SELECT * FROM t", ["id"], 2)
    assert len(out) == 5
    assert list(out["id"]) == [1, 2, 3, 4, 5]


def test_load_in_chunks_single_read_when_disabled():
    df = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
    assert len(load_in_chunks(_WindowEngine("dev", df), "q", ["id"], 0)) == 2
    assert len(load_in_chunks(_WindowEngine("dev", df), "q", [], 5)) == 2


def test_load_in_chunks_applies_normalize():
    df = pd.DataFrame({"id": [1, 2, 3], "v": [1, 2, 3]})
    out = load_in_chunks(
        _WindowEngine("dev", df), "SELECT * FROM t", ["id"], 2,
        normalize=lambda d: d.assign(v=d["v"] * 10),
    )
    assert list(out["v"]) == [10, 20, 30]


def test_engine_chunked_reconcile_loads_longer_side_fully():
    """Regression: old interleaved loop truncated the longer side when the
    shorter side returned a short chunk."""
    from etl_framework.reconciliation.engine import ReconciliationEngine

    src = pd.DataFrame({"id": [1, 2, 3], "v": [1, 2, 3]})
    tgt = pd.DataFrame({"id": [1, 2, 3, 4, 5], "v": [1, 2, 3, 4, 5]})
    engine = ReconciliationEngine(
        source_engine=_WindowEngine("dev", src),
        target_engine=_WindowEngine("prod", tgt),
        key_columns=["id"],
        chunk_size=2,
        use_hash_precheck=False,
    )
    result = engine.reconcile(query="SELECT * FROM t", query_name="trunc")
    assert result.target_row_count == 5
    assert result.missing_in_source_count == 2
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/unit/test_chunker.py -v`
Expected: new tests FAIL (`ImportError: cannot import name 'load_in_chunks'`; regression test would report `target_row_count == 4` under old code).

- [ ] **Step 3: Implement `load_in_chunks`**

In `etl_framework/reconciliation/chunker.py` (add `import pandas as pd` at top if absent):

```python
def load_in_chunks(
    engine,
    query: str,
    key_columns: list[str],
    chunk_size: int,
    params: dict | None = None,
    normalize=None,
) -> "pd.DataFrame":
    """Load a query fully via OFFSET/FETCH pagination.

    Falls back to a single read when chunk_size is 0 or key_columns are empty
    (ORDER BY keys are required for deterministic pagination). `normalize`,
    when given, is applied to every chunk (and to the single-read frame).
    """
    _n = normalize or (lambda df: df)
    if not chunk_size or not key_columns:
        return _n(engine.execute_query(query, params))
    parts: list[pd.DataFrame] = []
    offset = 0
    while True:
        q = build_chunk_query(query, key_columns, offset, chunk_size)
        chunk = _n(engine.execute_query(q, params))
        if chunk.empty:
            break
        parts.append(chunk)
        if len(chunk) < chunk_size:
            break
        offset += chunk_size
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
```

- [ ] **Step 4: Rewire engine**

In `etl_framework/reconciliation/engine.py`, add `load_in_chunks` to the existing chunker import (line 15). Replace the chunked-loading block (lines 124-146, from `# Chunked loading:` through the `df_target = pd.concat(...)` line) with:

```python
                df_source = load_in_chunks(
                    self._source_engine, query, self._key_columns,
                    self._chunk_size, params, self._normalizer.normalize,
                )
                df_target = load_in_chunks(
                    self._target_engine, query, self._key_columns,
                    self._chunk_size, params, self._normalizer.normalize,
                )
```

- [ ] **Step 5: Rewire compare_service**

In `api/services/compare_service.py`, delete the `_load_in_chunks` function body (lines 27-51) and replace with a delegating wrapper (callers keep working):

```python
from etl_framework.reconciliation.chunker import build_chunk_query, load_in_chunks


def _load_in_chunks(db_engine, query, key_cols, chunk_size):
    return load_in_chunks(db_engine, query, key_cols, chunk_size)
```

(Keep the existing `build_chunk_query` import satisfied; remove it if no longer referenced elsewhere in the file — check with grep first.)

- [ ] **Step 6: Run tests, verify pass**

Run: `python -m pytest tests/unit/test_chunker.py tests/unit/test_reconciliation.py tests/unit/test_compare_api.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add etl_framework/reconciliation/chunker.py etl_framework/reconciliation/engine.py api/services/compare_service.py tests/unit/test_chunker.py
git commit -m "refactor: shared load_in_chunks; fix chunked compare truncating longer side"
```

---

### Task 6: Memory guardrail — `max_compare_rows` run setting

**Problem:** Chunked mode still concatenates everything into RAM; nothing bounds total loaded rows. Streaming compare is out of scope; a hard guardrail turns silent OOM risk into a clear per-job ERROR.

**Files:**
- Modify: `etl_framework/exceptions.py`, `etl_framework/reconciliation/engine.py`, `api/schemas.py` (RunSettings), `api/services/run_executor.py` (`_run_reconciliation_job`)
- Test: `tests/unit/test_reconciliation.py`, `tests/unit/test_run_executor_gates.py` (extend)

- [ ] **Step 1: Write failing engine test**

Append to `tests/unit/test_reconciliation.py` (reuse its fake-engine helpers; the `_WindowEngine` from Task 5 works too):

```python
def test_max_compare_rows_guardrail_raises():
    import pandas as pd
    from etl_framework.exceptions import CompareRowLimitExceeded
    from etl_framework.reconciliation.engine import ReconciliationEngine

    df = pd.DataFrame({"id": range(10), "v": range(10)})
    engine = ReconciliationEngine(
        source_engine=_WindowEngine("dev", df),
        target_engine=_WindowEngine("prod", df),
        key_columns=["id"],
        max_compare_rows=5,
    )
    import pytest
    with pytest.raises(CompareRowLimitExceeded) as exc:
        engine.reconcile(query="SELECT * FROM t", query_name="big")
    assert "max_compare_rows=5" in str(exc.value)
    assert "20" in str(exc.value)  # 10 + 10 rows loaded
```

- [ ] **Step 2: Run test, verify it fails**

Run: `python -m pytest tests/unit/test_reconciliation.py::test_max_compare_rows_guardrail_raises -v`
Expected: FAIL (`ImportError` / `TypeError: unexpected keyword`).

- [ ] **Step 3: Implement**

`etl_framework/exceptions.py` — append:

```python
class CompareRowLimitExceeded(RuntimeError):
    """Total loaded rows exceeded the configured max_compare_rows guardrail."""

    def __init__(self, query_name: str, total_rows: int, limit: int) -> None:
        super().__init__(
            f"Compare aborted for '{query_name}': {total_rows} rows loaded "
            f"exceeds max_compare_rows={limit}"
        )
```

`etl_framework/reconciliation/engine.py`:
- import: `from etl_framework.exceptions import CompareRowLimitExceeded, SchemaValidationError`
- ctor: add parameter `max_compare_rows: int = 0` (after `segment_columns`) and `self._max_compare_rows = max_compare_rows`
- in `reconcile()`, immediately after both `df_source`/`df_target` are loaded (after the chunked/non-chunked if/else, before `self._filter_incremental`):

```python
            if self._max_compare_rows and (
                len(df_source) + len(df_target)
            ) > self._max_compare_rows:
                raise CompareRowLimitExceeded(
                    query_name, len(df_source) + len(df_target), self._max_compare_rows
                )
```

`api/schemas.py` `RunSettings` — add field:

```python
    max_compare_rows: int = Field(default=0, ge=0)
```

`api/services/run_executor.py` `_run_reconciliation_job` — add to the `ReconciliationEngine(...)` kwargs:

```python
            max_compare_rows=self._settings.max_compare_rows,
```

- [ ] **Step 4: Write failing executor-level test, then verify both pass**

Append to `tests/unit/test_run_executor_gates.py`:

```python
def test_max_compare_rows_marks_step_error():
    db = _session()
    RunRepository(db).create_run("run-mr1", "dev", "prod", {})
    _make_job(
        db, "big_job",
        [{"id": i, "v": i} for i in range(6)],
        [{"id": i, "v": i} for i in range(6)],
    )

    RunExecutor(
        db=db,
        run_id="run-mr1",
        source_env="dev",
        target_env="prod",
        job_sequence=[SequenceStep(job_name="big_job")],
        run_settings=RunSettings(metrics_enabled=False, max_compare_rows=5),
    ).execute()

    run = RunRepository(db).get_run("run-mr1")
    assert run.status == "ERROR"
    assert "max_compare_rows=5" in run.results[0].error_message
```

Run: `python -m pytest tests/unit/test_reconciliation.py tests/unit/test_run_executor_gates.py tests/unit/test_new_schemas.py -v`
Expected: all PASS (RunSettings has `extra="forbid"` — new field is legitimate, existing schema tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/exceptions.py etl_framework/reconciliation/engine.py api/schemas.py api/services/run_executor.py tests/unit/test_reconciliation.py tests/unit/test_run_executor_gates.py
git commit -m "feat: max_compare_rows guardrail aborts oversized compares with clear error"
```

---

### Task 7: Replace dead topo-sort with dependency-order validation

**Problem:** `_resolve_jobs` + `_topo_sort` ([api/services/run_executor.py:331-358](../../api/services/run_executor.py)) are never called from `execute()` — `depends_on` is silently ignored at runtime. Dead code with live tests (`tests/unit/test_dag_retry_trends.py:48-86`).

**Fix:** Delete both methods and their tests. Add `_validate_dependencies`: if job B declares `depends_on: [A]` and A appears *after* B in the sequence (both present), fail fast — run ends ERROR with an actionable message. Deps not present in the sequence are ignored (unchanged semantics).

**Files:**
- Modify: `api/services/run_executor.py`, `tests/unit/test_dag_retry_trends.py`
- Test: `tests/unit/test_run_executor_gates.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_run_executor_gates.py`:

```python
def _make_job_with_deps(db, name, depends_on):
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
            "source_rows": [{"id": 1, "v": 1}],
            "target_rows": [{"id": 1, "v": 1}],
            "depends_on": depends_on,
        },
        "enabled": True,
    })


def test_sequence_violating_depends_on_errors_before_any_step():
    db = _session()
    RunRepository(db).create_run("run-d1", "dev", "prod", {})
    _make_job_with_deps(db, "loader", [])
    _make_job_with_deps(db, "reconciler", ["loader"])

    RunExecutor(
        db=db,
        run_id="run-d1",
        source_env="dev",
        target_env="prod",
        job_sequence=[SequenceStep(job_name="reconciler"),
                      SequenceStep(job_name="loader")],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-d1")
    assert run.status == "ERROR"
    assert "depends on 'loader'" in run.results[0].error_message


def test_sequence_respecting_depends_on_passes():
    db = _session()
    RunRepository(db).create_run("run-d2", "dev", "prod", {})
    _make_job_with_deps(db, "loader", [])
    _make_job_with_deps(db, "reconciler", ["loader"])

    RunExecutor(
        db=db,
        run_id="run-d2",
        source_env="dev",
        target_env="prod",
        job_sequence=[SequenceStep(job_name="loader"),
                      SequenceStep(job_name="reconciler")],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    assert RunRepository(db).get_run("run-d2").status == "PASSED"


def test_depends_on_outside_sequence_is_ignored():
    db = _session()
    RunRepository(db).create_run("run-d3", "dev", "prod", {})
    _make_job_with_deps(db, "reconciler", ["not_in_sequence"])

    RunExecutor(
        db=db,
        run_id="run-d3",
        source_env="dev",
        target_env="prod",
        job_sequence=[SequenceStep(job_name="reconciler")],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    assert RunRepository(db).get_run("run-d3").status == "PASSED"
```

- [ ] **Step 2: Run tests, verify first one fails**

Run: `python -m pytest tests/unit/test_run_executor_gates.py -v -k depends`
Expected: `test_sequence_violating_depends_on_errors_before_any_step` FAILS (run PASSES today — depends_on ignored); other two already pass.

- [ ] **Step 3: Implement**

In `api/services/run_executor.py`:

Delete `_resolve_jobs` (lines 331-335) and `_topo_sort` (lines 337-358) entirely.

Add:

```python
    def _validate_dependencies(
        self, steps: list[SequenceStep], jobs_index: dict[str, JobDefinition]
    ) -> None:
        """Fail fast when the user-ordered sequence contradicts depends_on.

        Only dependencies that are themselves part of the sequence are
        enforced; external depends_on entries are informational.
        """
        position = {s.job_name: i for i, s in enumerate(steps)}
        for i, s in enumerate(steps):
            job = jobs_index.get(s.job_name)
            if job is None:
                continue
            for dep in job.depends_on:
                dep_pos = position.get(dep)
                if dep_pos is not None and dep_pos > i:
                    raise ValueError(
                        f"Job '{s.job_name}' depends on '{dep}' but is sequenced "
                        f"before it (position {i} vs {dep_pos}). Reorder the "
                        f"job sequence so dependencies run first."
                    )
```

In `execute()`, right after `jobs_index = self._build_jobs_index()`, add:

```python
                self._validate_dependencies(steps, jobs_index)
```

(The surrounding `try/except` already converts the raise into run status ERROR with `_persist_error("<run>", exc)` — that's what the test asserts.)

In `tests/unit/test_dag_retry_trends.py`: delete the topo-sort section (the header comment at line 29 and the five tests `test_topo_sort_no_deps`, `test_topo_sort_linear_chain`, `test_topo_sort_diamond`, `test_topo_sort_cycle_raises`, `test_topo_sort_external_deps_ignored`, lines ~29-86) and any now-unused imports/helpers those tests used exclusively. Keep the rest of the file (retry/trends tests) untouched.

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/unit/test_run_executor_gates.py tests/unit/test_dag_retry_trends.py -v`
Expected: all PASS; no topo tests collected.

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_dag_retry_trends.py tests/unit/test_run_executor_gates.py
git commit -m "feat: enforce depends_on ordering at launch; remove dead topo-sort code"
```

---

### Task 8: Full-suite verification

- [ ] **Step 1: Run the whole unit + integration suite**

Run: `python -m pytest tests/unit tests/integration -x -q`
Expected: all PASS. Two known interaction points to watch:
- any test asserting a run finishes PASSED/FAILED after a failed condition gate → now BLOCKED (fix the assertion, it encodes the old bug);
- `tests/unit/test_api.py` / `tests/unit/test_runs_extensions.py` may serialize `RunSettings` — the new `max_compare_rows` field is additive with a default, so serialization only changes if a test asserts an exact dict; update such asserts.

- [ ] **Step 2: Commit any test-fix fallout**

```bash
git add -A tests
git commit -m "test: update assertions for BLOCKED status and max_compare_rows field"
```

---

## Self-Review Notes

- **Coverage:** gap→task map: prev_result skew → T1; condition-break status → T1; hold poll unbounded → T3; cancel granularity → T2 (+ cancel-during-hold in T3); scheduler overlap → T4; duplicated chunk loader + truncation → T5; memory → T6 (guardrail; streaming explicitly out of scope); dead topo code → T7.
- **Type consistency:** `_sleep_with_cancel_check(seconds) -> bool` (T2) used only in T2; `load_in_chunks(engine, query, key_columns, chunk_size, params, normalize)` signature identical in T5 tests and both call sites; `CompareRowLimitExceeded(query_name, total_rows, limit)` matches T6 test's message asserts; `has_active_run_for_selection(selection_id) -> bool` matches T4 both tests and scheduler call.
- **Known open ends for the executor:** T4 Step 5 requires copying the existing `_run_schedule` test arrangement from `tests/unit/test_scheduler.py` (file exists, 6.8K — the pattern is there); T3 tests must reuse `test_hold_polling.py`'s session-sharing approach. Both are read-and-mirror instructions against files that verifiably exist, not placeholders for unwritten design.
