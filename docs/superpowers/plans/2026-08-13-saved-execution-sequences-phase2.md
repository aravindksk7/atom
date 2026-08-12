# Saved Execution Sequences — Phase 2 Implementation Plan (DAG Executor)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the linear step loop with a DAG coordinator so independent branches run concurrently, trigger rules decide each edge, and a held step no longer blocks the whole run.

**Architecture:** One executor, not two. A linear sequence is normalised into a chain DAG, so every run takes the same code path and legacy chains keep running exactly as they do today. The scheduling loop moves into a new `DagExecutor` that takes an injected step-runner, clock, and sleep, making it unit-testable without touching a database or running a real job. `RunExecutor` keeps everything it already does — building cases, persisting results, aggregating the run — and hands scheduling to the coordinator.

**Tech Stack:** Python 3.14, SQLAlchemy 2.x, `concurrent.futures.ThreadPoolExecutor`, pytest, Alpine.js, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-12-saved-execution-sequences-design.md` (as amended by commit `54ef5c7`)

**Depends on:** Phase 1, which is implemented and passing (68 tests) but **not yet committed**. Commit it before starting — see Task 0.

---

## Background For The Implementer

Read this before touching anything. Several things about the existing executor differ from what you might assume.

### The current linear loop

`RunExecutor.execute()` at `api/services/run_executor.py:199` does all of this in one `for` loop over steps:

1. Evaluates `seq_step.condition` **against the previous step's result**, and only when `i > 0` (line 227). A failed gate calls `step_repo.cancel_remaining(...)` and sets the **run** status to `BLOCKED`.
2. Checks `is_cancel_requested` before and after each step.
3. Sleeps `wait_seconds` with cancel checking.
4. Runs exactly one job through `TestRunner(max_workers=1)` (line 253).
5. On `hold_after`, sets step status `HELD`, fires the `run.held` webhook, then **blocks** in `_poll_for_release` until released or timed out.

### Things that are easy to get wrong

- **Condition direction.** A step's `condition` says what it needs **from its parents**. It is not that step's own success criteria. The DAG generalises this by requiring **every** parent to satisfy the child's condition. This was confirmed against the code and the spec was amended to match — do not invert it.
- **`BLOCKED` is already a run status** (line 291), set when a gate stops the run. Phase 2 adds `BLOCKED` as a *step* status too. Both exist. Run-status precedence becomes `ERROR > FAILED > CANCELLED > BLOCKED > PASSED`.
- **Hold timeout already exists.** `HOLD_TIMEOUT_SECONDS` (line 55, default 86400) auto-cancels a held step. Preserve it per step; do not remove it.
- **A released step loses its job outcome.** `release_step` overwrites `run_steps.status` with `APPROVED`/`SKIPPED`/`CANCELLED` (`repository.py:1360-1361`). The underlying job status is gone from the row. The coordinator must therefore keep each step's job outcome **in memory** and evaluate trigger rules from that, never by re-reading the row.
- **`tests/integration/test_hold_polling.py` monkeypatches `_re_module.HOLD_POLL_INTERVAL_SECONDS = 1`** at import time. Keep the module-level constant so that keeps working.
- **Do not verify with `rtk`** — it serves a cached summary. Use raw `python -m pytest`. For Playwright use `rtk proxy npx playwright test`.

### The one-executor decision

The brainstorm explicitly rejected keeping a linear path alongside a DAG path, because retry, holds, cancel, and conditions would each need implementing twice and would drift. Instead:

> A plain `list[SequenceStep]` is normalised into a chain DAG — step *i* depends on step *i-1*. Ready-set size is then always 1, so it executes inline with no thread pool, in the same order, with the same condition semantics.

Task 1's characterization tests are the gate that proves this. They are written against today's executor **before** any change and must pass **unmodified** after the swap.

### Phase gating

Phase 1 added `phase1_unsupported()` in `api/services/sequence_validation.py`, rejecting non-default `trigger_rule`, `max_retries`, `retry_delay_seconds`, `on_failure`, and any `preconditions`. Phase 2 opens **`trigger_rule` only**. Retry and `on_failure` stay closed until Phase 3; preconditions until Phase 4. Since `on_failure` stays pinned at its default, Phase 2 implements `skip_downstream` propagation and nothing else.

---

## File Structure

**Create**

| File | Responsibility |
|---|---|
| `api/services/sequence_conditions.py` | Pure condition and trigger-rule evaluation. No DB, no threads. |
| `api/services/dag_executor.py` | The scheduling coordinator. Injected step-runner, clock, and sleep. |
| `tests/unit/test_executor_characterization.py` | Golden tests over the linear loop. The swap gate. |
| `tests/unit/test_sequence_conditions.py` | Trigger-rule truth table. |
| `tests/unit/test_dag_executor.py` | Coordinator behaviour with a fake step-runner. |
| `tests/integration/test_dag_branch_hold.py` | A held branch while another branch runs on. |

**Modify**

| File | Change |
|---|---|
| `etl_framework/repository/models.py` | `RunStep` gains six columns. |
| `etl_framework/repository/database.py` | `ensure_column` shims for them. |
| `etl_framework/repository/repository.py` | `RunStepRepository`: materialize from `SequenceStepRef`, lookup by `step_id`, block descendants. |
| `api/services/run_executor.py` | Normalise to a chain DAG; delegate scheduling; extract `_check_condition`. |
| `api/services/sequence_validation.py` | Open `trigger_rule`. |
| `api/services/sequence_resolver.py` | Deprecate `as_linear_steps` in favour of passing real steps. |
| `api/routes/selections.py`, `api/routes/schedules.py`, `api/services/scheduler.py` | Pass DAG steps through. |
| `api/schemas.py` | `RunStepOut` gains the new fields. |
| `api/routes/runs.py` | SSE `steps` payload; release-by-step-id route. |
| `frontend/features/monitor.js`, `frontend/partials/tab-monitor.html` | Step timeline by level, `BLOCKED` badge. |

---

## Task 0: Commit Phase 1

Phase 1 is implemented and green but sitting uncommitted. Everything below builds on it, and you must not mix Phase 1 and Phase 2 changes in one commit.

- [ ] **Step 1: Confirm Phase 1 is green**

Run: `python -m pytest tests/unit/test_sequence_validation.py tests/unit/test_sequence_schemas.py tests/unit/test_sequence_repository.py tests/unit/test_sequence_resolver.py tests/unit/test_sequences_routes.py tests/unit/test_selections_sequence_ref.py tests/unit/test_schedules_sequence_target.py tests/integration/test_sequence_workflow.py -q`

Expected: `68 passed`.

- [ ] **Step 2: Confirm the generated HTML is in sync**

Run: `npm run build:html && git diff --exit-code frontend/index.html`
Expected: exit code 0, no output.

- [ ] **Step 3: Commit only the Phase 1 files**

```bash
git add api/routes/sequences.py api/services/sequence_validation.py api/services/sequence_resolver.py \
        api/services/job_env_validation.py etl_framework/repository/sequence_repository.py \
        frontend/features/sequences.js frontend/partials/tab-sequences.html \
        tests/unit/test_sequence_validation.py tests/unit/test_sequence_schemas.py \
        tests/unit/test_sequence_repository.py tests/unit/test_sequence_resolver.py \
        tests/unit/test_sequences_routes.py tests/unit/test_selections_sequence_ref.py \
        tests/unit/test_schedules_sequence_target.py tests/integration/test_sequence_workflow.py \
        tests/e2e/17-sequences.spec.ts \
        api/main.py api/routes/schedules.py api/routes/selections.py api/schemas.py \
        api/services/scheduler.py etl_framework/repository/database.py \
        etl_framework/repository/models.py etl_framework/repository/repository.py \
        frontend/app.js frontend/features/launch.js frontend/help-content.js \
        frontend/index.html frontend/index.template.html frontend/partials/tab-launch.html
git commit -m "feat: saved execution sequences (Phase 1)"
```

Leave `README.md`, `tests/e2e/11-help.spec.ts`, and the untracked scratch directories alone unless they are genuinely part of Phase 1 — check `git diff README.md` before deciding.

---

## Task 1: Characterization tests — the swap gate

No production code in this task. These tests capture what the linear executor does **today**, in enough detail that a behaviour change cannot slip through. They must pass unmodified after Task 9.

**Files:**
- Create: `tests/unit/test_executor_characterization.py`

- [ ] **Step 1: Write the golden tests**

Create `tests/unit/test_executor_characterization.py`:

```python
"""Golden tests locking in the linear executor's observable behaviour.

Written BEFORE the DAG rewrite and re-run unmodified after it. If a test in
this file needs editing to pass, the rewrite changed behaviour -- stop and
work out whether that change was intended.
"""
from __future__ import annotations

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


def _job(db, name, *, matching=True):
    """A reconciliation job that passes (matching) or fails (mismatched)."""
    JobRepository(db).create({
        "name": name, "description": "", "tags": [],
        "job_type": "reconciliation", "query": f"SELECT * FROM {name}",
        "key_columns": ["id"], "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {
            "source_rows": [{"id": 1, "amount": 10.0}],
            "target_rows": [{"id": 1, "amount": 10.0 if matching else 9.0}],
        },
        "enabled": True,
    })


def _execute(db, run_id, steps):
    RunExecutor(
        db=db, run_id=run_id, source_env="dev", target_env="prod",
        job_sequence=steps, run_settings=RunSettings(metrics_enabled=False),
    ).execute()


def _step_statuses(db, run_id):
    return [(s.step_index, s.job_name, s.status) for s in RunStepRepository(db).list_steps(run_id)]


def test_passing_chain_marks_every_step_passed():
    db = _session()
    RunRepository(db).create_run("c-1", "dev", "prod", {})
    _job(db, "a")
    _job(db, "b")
    _execute(db, "c-1", ["a", "b"])

    assert _step_statuses(db, "c-1") == [(0, "a", "PASSED"), (1, "b", "PASSED")]
    run = RunRepository(db).get_run("c-1")
    assert (run.status, run.total_tests, run.passed, run.failed) == ("PASSED", 2, 2, 0)


def test_steps_run_in_declared_order():
    db = _session()
    RunRepository(db).create_run("c-2", "dev", "prod", {})
    _job(db, "first")
    _job(db, "second")
    _execute(db, "c-2", ["second", "first"])

    assert [s[1] for s in _step_statuses(db, "c-2")] == ["second", "first"]


def test_failing_step_does_not_stop_an_ungated_chain():
    db = _session()
    RunRepository(db).create_run("c-3", "dev", "prod", {})
    _job(db, "bad", matching=False)
    _job(db, "good")
    _execute(db, "c-3", ["bad", "good"])

    assert _step_statuses(db, "c-3") == [(0, "bad", "FAILED"), (1, "good", "PASSED")]
    run = RunRepository(db).get_run("c-3")
    assert (run.status, run.passed, run.failed) == ("FAILED", 1, 1)


def test_failed_condition_gate_blocks_the_run_and_cancels_the_rest():
    db = _session()
    RunRepository(db).create_run("c-4", "dev", "prod", {})
    _job(db, "bad", matching=False)
    _job(db, "gated")
    _job(db, "after")
    _execute(db, "c-4", [
        SequenceStep(job_name="bad"),
        SequenceStep(job_name="gated", condition=StepCondition(require_status=["PASSED"])),
        SequenceStep(job_name="after"),
    ])

    assert _step_statuses(db, "c-4") == [
        (0, "bad", "FAILED"), (1, "gated", "CANCELLED"), (2, "after", "CANCELLED"),
    ]
    assert RunRepository(db).get_run("c-4").status == "BLOCKED"


def test_satisfied_condition_gate_lets_the_run_continue():
    db = _session()
    RunRepository(db).create_run("c-5", "dev", "prod", {})
    _job(db, "ok")
    _job(db, "gated")
    _execute(db, "c-5", [
        SequenceStep(job_name="ok"),
        SequenceStep(job_name="gated", condition=StepCondition(require_status=["PASSED"])),
    ])

    assert _step_statuses(db, "c-5") == [(0, "ok", "PASSED"), (1, "gated", "PASSED")]
    assert RunRepository(db).get_run("c-5").status == "PASSED"


def test_condition_on_the_first_step_is_ignored():
    # i > 0 guard at run_executor.py:227 -- a condition on step 0 has no
    # predecessor to check, and today it is skipped rather than failing.
    db = _session()
    RunRepository(db).create_run("c-6", "dev", "prod", {})
    _job(db, "only")
    _execute(db, "c-6", [
        SequenceStep(job_name="only", condition=StepCondition(require_status=["NOPE"])),
    ])

    assert _step_statuses(db, "c-6") == [(0, "only", "PASSED")]
    assert RunRepository(db).get_run("c-6").status == "PASSED"


def test_unknown_job_marks_the_step_error_and_continues():
    db = _session()
    RunRepository(db).create_run("c-7", "dev", "prod", {})
    _job(db, "real")
    _execute(db, "c-7", ["ghost", "real"])

    assert _step_statuses(db, "c-7") == [(0, "ghost", "ERROR"), (1, "real", "PASSED")]


def test_cancel_requested_before_the_run_cancels_every_step():
    db = _session()
    RunRepository(db).create_run("c-8", "dev", "prod", {})
    _job(db, "a")
    _job(db, "b")
    RunRepository(db).request_cancel("c-8")
    _execute(db, "c-8", ["a", "b"])

    assert _step_statuses(db, "c-8") == [(0, "a", "CANCELLED"), (1, "b", "CANCELLED")]
    assert RunRepository(db).get_run("c-8").status == "CANCELLED"


def test_materialized_steps_carry_hold_condition_and_wait():
    db = _session()
    RunRepository(db).create_run("c-9", "dev", "prod", {})
    _job(db, "a")
    _job(db, "b")
    _execute(db, "c-9", [
        SequenceStep(job_name="a"),
        SequenceStep(job_name="b", wait_seconds=0,
                     condition=StepCondition(require_status=["PASSED"], max_mismatch_count=3)),
    ])

    steps = RunStepRepository(db).list_steps("c-9")
    assert steps[0].hold_after is False
    assert steps[1].condition["max_mismatch_count"] == 3
    assert steps[1].wait_seconds == 0


def test_string_dict_and_model_steps_all_normalize():
    db = _session()
    RunRepository(db).create_run("c-10", "dev", "prod", {})
    for name in ("s", "d", "m"):
        _job(db, name)
    _execute(db, "c-10", ["s", {"job_name": "d"}, SequenceStep(job_name="m")])

    assert [x[1] for x in _step_statuses(db, "c-10")] == ["s", "d", "m"]
    assert RunRepository(db).get_run("c-10").total_tests == 3
```

`RunRepository.request_cancel` is at `etl_framework/repository/repository.py:373`.

Note what `test_failed_condition_gate_blocks_the_run_and_cancels_the_rest` locks in: the run
reports `BLOCKED` even though a step `FAILED`. Blocking outranks failing. That is today's
behaviour and Phase 2 preserves it — see the precedence note in spec §2.

- [ ] **Step 2: Run them against the CURRENT executor**

Run: `python -m pytest tests/unit/test_executor_characterization.py -v`
Expected: **all PASS**. These describe existing behaviour, so any failure here means the test is wrong, not the code. Fix the test until it passes against today's executor — that is the whole point.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_executor_characterization.py
git commit -m "test: characterize the linear executor before the DAG rewrite"
```

---

## Task 2: run_steps columns

Add every column Phases 2 and 3 need in one migration so Phase 3 needs no schema change.

**Files:**
- Modify: `etl_framework/repository/models.py` (`RunStep`, line 364)
- Modify: `etl_framework/repository/database.py` (inside `_ensure_compare_columns`)
- Test: `tests/unit/test_dag_run_steps.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dag_run_steps.py`:

```python
"""run_steps columns backing DAG execution."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401


def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_run_steps_has_dag_columns():
    cols = {c["name"] for c in inspect(_db().get_bind()).get_columns("run_steps")}
    assert {"step_id", "depends_on", "trigger_rule", "attempt", "max_retries", "on_failure"} <= cols
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/test_dag_run_steps.py -v`
Expected: FAIL — `AssertionError`.

- [ ] **Step 3: Add the columns**

In `etl_framework/repository/models.py`, add to `RunStep` after `wait_seconds` (line 379):

```python
    step_id = Column(String(255), nullable=True, index=True)
    depends_on = Column(JSON, nullable=True)
    trigger_rule = Column(String(20), nullable=False, default="all_success")
    attempt = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=True)
    on_failure = Column(String(20), nullable=False, default="skip_downstream")
```

In `etl_framework/repository/database.py`, inside `_ensure_compare_columns`, next to the other `run_steps` shims (add them if none exist yet):

```python
        if "run_steps" in tables:
            ensure_column(conn, "run_steps", "step_id",
                          "ALTER TABLE run_steps ADD COLUMN step_id VARCHAR(255)")
            ensure_column(conn, "run_steps", "depends_on",
                          "ALTER TABLE run_steps ADD COLUMN depends_on JSON")
            ensure_column(conn, "run_steps", "trigger_rule",
                          "ALTER TABLE run_steps ADD COLUMN trigger_rule VARCHAR(20) NOT NULL DEFAULT 'all_success'")
            ensure_column(conn, "run_steps", "attempt",
                          "ALTER TABLE run_steps ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0")
            ensure_column(conn, "run_steps", "max_retries",
                          "ALTER TABLE run_steps ADD COLUMN max_retries INTEGER")
            ensure_column(conn, "run_steps", "on_failure",
                          "ALTER TABLE run_steps ADD COLUMN on_failure VARCHAR(20) NOT NULL DEFAULT 'skip_downstream'")
```

`tables` is already computed at the top of that function — reuse it rather than re-inspecting.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/unit/test_dag_run_steps.py tests/unit/test_executor_characterization.py -v`
Expected: PASS — new test passes, all 10 characterization tests still pass.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/repository/database.py tests/unit/test_dag_run_steps.py
git commit -m "feat: add DAG columns to run_steps"
```

---

## Task 3: Pure condition and trigger-rule evaluation

**Files:**
- Create: `api/services/sequence_conditions.py`
- Modify: `api/services/run_executor.py:331-353` (`_check_condition` delegates)
- Test: `tests/unit/test_sequence_conditions.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sequence_conditions.py`:

```python
"""Trigger-rule and parent-condition evaluation."""
from __future__ import annotations

import pytest


class _Result:
    """Minimal stand-in for ReconciliationResult."""
    def __init__(self, status="PASSED", value=0, missing_t=0, missing_s=0, rows=100):
        self.status = status
        self.value_mismatch_count = value
        self.missing_in_target_count = missing_t
        self.missing_in_source_count = missing_s
        self.source_row_count = rows


def _outcome(status="PASSED", **kw):
    from api.services.sequence_conditions import ParentOutcome
    return ParentOutcome(status=status, result=_Result(status=status, **kw))


# --- evaluate_condition -----------------------------------------------------

def test_evaluate_condition_accepts_matching_status():
    from api.schemas import StepCondition
    from api.services.sequence_conditions import evaluate_condition
    assert evaluate_condition(StepCondition(require_status=["PASSED"]), _Result()) is True


def test_evaluate_condition_rejects_other_status():
    from api.schemas import StepCondition
    from api.services.sequence_conditions import evaluate_condition
    assert evaluate_condition(StepCondition(require_status=["PASSED"]), _Result("FAILED")) is False


def test_evaluate_condition_enforces_max_mismatch_count():
    from api.schemas import StepCondition
    from api.services.sequence_conditions import evaluate_condition
    cond = StepCondition(require_status=["PASSED"], max_mismatch_count=2)
    assert evaluate_condition(cond, _Result(value=1, missing_t=1)) is True
    assert evaluate_condition(cond, _Result(value=2, missing_t=1)) is False


def test_evaluate_condition_enforces_row_bounds():
    from api.schemas import StepCondition
    from api.services.sequence_conditions import evaluate_condition
    assert evaluate_condition(StepCondition(min_row_count=50), _Result(rows=10)) is False
    assert evaluate_condition(StepCondition(max_row_count=5), _Result(rows=10)) is False


# --- parent_satisfies -------------------------------------------------------

def test_parent_satisfies_defaults_to_passed_when_no_condition():
    from api.services.sequence_conditions import parent_satisfies
    assert parent_satisfies(None, _outcome("PASSED")) is True
    assert parent_satisfies(None, _outcome("FAILED")) is False


def test_parent_satisfies_treats_skipped_as_not_success():
    from api.services.sequence_conditions import parent_satisfies
    assert parent_satisfies(None, _outcome("SKIPPED")) is False


def test_parent_satisfies_with_no_result_falls_back_to_status():
    from api.schemas import StepCondition
    from api.services.sequence_conditions import ParentOutcome, parent_satisfies
    outcome = ParentOutcome(status="PASSED", result=None)
    assert parent_satisfies(StepCondition(require_status=["PASSED"], max_mismatch_count=0), outcome) is True


# --- trigger_fires ----------------------------------------------------------

@pytest.mark.parametrize("satisfied,expected", [
    ([], True), ([True], True), ([True, True], True),
    ([True, False], False), ([False], False),
])
def test_all_success(satisfied, expected):
    from api.services.sequence_conditions import trigger_fires
    assert trigger_fires("all_success", satisfied) is expected


@pytest.mark.parametrize("satisfied", [[], [True], [False], [True, False]])
def test_all_done_always_fires(satisfied):
    from api.services.sequence_conditions import trigger_fires
    assert trigger_fires("all_done", satisfied) is True


@pytest.mark.parametrize("satisfied,expected", [
    ([], True), ([True, False], True), ([False, False], False),
])
def test_any_success(satisfied, expected):
    from api.services.sequence_conditions import trigger_fires
    assert trigger_fires("any_success", satisfied) is expected


@pytest.mark.parametrize("satisfied,expected", [
    ([], False), ([False], True), ([False, False], True), ([True, False], False),
])
def test_all_failed(satisfied, expected):
    from api.services.sequence_conditions import trigger_fires
    assert trigger_fires("all_failed", satisfied) is expected
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_sequence_conditions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.services.sequence_conditions'`.

- [ ] **Step 3: Write the module**

Create `api/services/sequence_conditions.py`:

```python
"""Pure evaluation of step conditions and DAG trigger rules.

Extracted from RunExecutor so the DAG coordinator can use it without pulling in
the executor, a database, or a thread pool.

Direction matters: a step's `condition` states what that step requires OF ITS
PARENTS. It is not the step's own success criteria. In a DAG the condition is
evaluated against every parent, and all must satisfy it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from api.schemas import StepCondition

DEFAULT_REQUIRE_STATUS = ("PASSED",)


@dataclass(frozen=True)
class ParentOutcome:
    """What a finished step produced, as the coordinator remembers it.

    `status` is the job's own outcome (PASSED/FAILED/SLOW/ERROR/SKIPPED) and NOT
    the run_steps row status, which release_step overwrites with APPROVED etc.
    """
    status: str
    result: Any | None = None


def _status_of(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def evaluate_condition(condition: "StepCondition", result) -> bool:
    """Does `result` satisfy `condition`? Mirrors RunExecutor._check_condition."""
    status = _status_of(result.status)
    if condition.require_status and status not in condition.require_status:
        return False
    if condition.max_mismatch_count is not None:
        total = (
            result.value_mismatch_count
            + result.missing_in_target_count
            + result.missing_in_source_count
        )
        if total > condition.max_mismatch_count:
            return False
    if condition.min_row_count is not None and result.source_row_count < condition.min_row_count:
        return False
    if condition.max_row_count is not None and result.source_row_count > condition.max_row_count:
        return False
    if condition.max_value_mismatches is not None and result.value_mismatch_count > condition.max_value_mismatches:
        return False
    if condition.max_missing_in_target is not None and result.missing_in_target_count > condition.max_missing_in_target:
        return False
    if condition.max_missing_in_source is not None and result.missing_in_source_count > condition.max_missing_in_source:
        return False
    return True


def parent_satisfies(condition: "StepCondition | None", outcome: ParentOutcome) -> bool:
    """Does this parent meet the child's entry requirement?

    With no condition the requirement is simply that the parent PASSED. When the
    parent produced no result object (an ERROR, or a skipped hold) only the
    status is checked, since the numeric gates have nothing to read.
    """
    required = tuple(condition.require_status) if (condition and condition.require_status) else DEFAULT_REQUIRE_STATUS
    if outcome.status not in required:
        return False
    if condition is None or outcome.result is None:
        return True
    return evaluate_condition(condition, outcome.result)


def trigger_fires(rule: str, satisfied: list[bool]) -> bool:
    """Should a step run, given whether each parent satisfied its condition?

    A step with no parents always fires, except under `all_failed`, which needs
    at least one parent to have failed in order to mean anything.
    """
    if rule == "all_done":
        return True
    if rule == "any_success":
        return True if not satisfied else any(satisfied)
    if rule == "all_failed":
        return bool(satisfied) and not any(satisfied)
    return all(satisfied)   # all_success, the default
```

- [ ] **Step 4: Delegate from RunExecutor**

Replace the body of `RunExecutor._check_condition` (`api/services/run_executor.py:331-353`) with a delegation, keeping the method so existing callers and tests are untouched:

```python
    def _check_condition(self, condition: StepCondition, prev_result: ReconciliationResult) -> bool:
        from api.services.sequence_conditions import evaluate_condition
        return evaluate_condition(condition, prev_result)
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/unit/test_sequence_conditions.py tests/unit/test_executor_characterization.py tests/unit/test_pass_condition_executor.py -v`
Expected: PASS — the new truth table plus both existing suites.

- [ ] **Step 6: Commit**

```bash
git add api/services/sequence_conditions.py api/services/run_executor.py tests/unit/test_sequence_conditions.py
git commit -m "refactor: extract condition evaluation and add trigger-rule rules"
```

---

## Task 4: Normalise a plain sequence into a chain DAG

Still executed by the existing linear loop. This isolates the normalisation change from the scheduling change, so if the characterization tests break you know exactly which of the two did it.

**Files:**
- Modify: `api/services/run_executor.py:315-324` (`_resolve_sequence_steps`)
- Test: `tests/unit/test_chain_normalization.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_chain_normalization.py`:

```python
"""A plain sequence becomes an explicit chain DAG."""
from __future__ import annotations

from api.schemas import SequenceStep, SequenceStepRef, StepCondition
from api.services.run_executor import normalize_to_dag


def test_strings_become_a_chain():
    steps = normalize_to_dag(["a", "b", "c"])
    assert [s.step_id for s in steps] == ["step_0", "step_1", "step_2"]
    assert [s.depends_on for s in steps] == [[], ["step_0"], ["step_1"]]
    assert [s.job_name for s in steps] == ["a", "b", "c"]


def test_dicts_and_models_normalize_too():
    steps = normalize_to_dag([{"job_name": "a"}, SequenceStep(job_name="b")])
    assert [s.job_name for s in steps] == ["a", "b"]
    assert steps[1].depends_on == ["step_0"]


def test_hold_condition_and_wait_are_preserved():
    steps = normalize_to_dag([
        SequenceStep(job_name="a"),
        SequenceStep(job_name="b", hold_after=True, wait_seconds=7,
                     condition=StepCondition(require_status=["PASSED"], max_mismatch_count=2)),
    ])
    assert steps[1].hold_after is True
    assert steps[1].wait_seconds == 7
    assert steps[1].condition.max_mismatch_count == 2


def test_step_refs_pass_through_untouched():
    given = [
        SequenceStepRef(step_id="root", job_name="a"),
        SequenceStepRef(step_id="left", job_name="b", depends_on=["root"]),
        SequenceStepRef(step_id="right", job_name="c", depends_on=["root"]),
    ]
    steps = normalize_to_dag(given)
    assert [s.step_id for s in steps] == ["root", "left", "right"]
    assert steps[2].depends_on == ["root"]


def test_defaults_are_all_success_and_skip_downstream():
    step = normalize_to_dag(["a"])[0]
    assert step.trigger_rule == "all_success"
    assert step.on_failure == "skip_downstream"
    assert step.max_retries is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_chain_normalization.py -v`
Expected: FAIL — `ImportError: cannot import name 'normalize_to_dag'`.

- [ ] **Step 3: Implement**

In `api/services/run_executor.py`, add a module-level function near the other helpers (above `class RunExecutor`), and import `SequenceStepRef` at the top alongside the existing `SequenceStep` import:

```python
def normalize_to_dag(job_sequence: list) -> list["SequenceStepRef"]:
    """Turn any accepted sequence shape into an explicit list of DAG steps.

    A plain list has no dependency information, so it becomes a chain: step i
    depends on step i-1. That reproduces linear order exactly -- the ready set
    never holds more than one step -- while letting one code path serve both
    plain sequences and real saved DAGs.
    """
    from api.schemas import SequenceStepRef

    normalized: list[SequenceStepRef] = []
    previous_id: str | None = None
    for i, item in enumerate(job_sequence):
        if isinstance(item, SequenceStepRef):
            normalized.append(item)
            continue
        if isinstance(item, str):
            step = SequenceStep(job_name=item)
        elif isinstance(item, dict):
            step = SequenceStep(**item)
        else:
            step = item
        step_id = f"step_{i}"
        normalized.append(SequenceStepRef(
            step_id=step_id,
            job_name=step.job_name,
            depends_on=[previous_id] if previous_id is not None else [],
            hold_after=step.hold_after,
            condition=step.condition,
            wait_seconds=step.wait_seconds,
        ))
        previous_id = step_id
    return normalized
```

Note the early `continue` for a `SequenceStepRef`: real DAG steps carry their own `depends_on` and must never be re-chained. Because of that, a list mixing `SequenceStepRef` with plain entries is not supported — callers pass one shape or the other.

Now change `_resolve_sequence_steps` to use it while still returning what the linear loop expects:

```python
    def _resolve_sequence_steps(self) -> list[SequenceStep]:
        self._dag_steps = normalize_to_dag(self._job_sequence)
        return [
            SequenceStep(
                job_name=s.job_name, hold_after=s.hold_after,
                condition=s.condition, wait_seconds=s.wait_seconds,
            )
            for s in self._dag_steps
        ]
```

Initialise `self._dag_steps: list = []` in `RunExecutor.__init__` so the attribute always exists.

- [ ] **Step 4: Run the gate**

Run: `python -m pytest tests/unit/test_chain_normalization.py tests/unit/test_executor_characterization.py -v`
Expected: PASS — 5 new tests, and **all 10 characterization tests still passing unmodified**. If any characterization test fails here, normalisation changed behaviour; fix it before going further.

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_chain_normalization.py
git commit -m "refactor: normalize any job sequence into an explicit chain DAG"
```

---

## Task 5: DagExecutor — scheduling core

The coordinator, with no knowledge of jobs, reconciliation, or HTTP. Everything it needs is injected, so it is tested with a fake step-runner and a fake clock.

**Files:**
- Create: `api/services/dag_executor.py`
- Test: `tests/unit/test_dag_executor.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_dag_executor.py`:

```python
"""DagExecutor scheduling, with a fake step-runner and no database."""
from __future__ import annotations

import threading

from api.schemas import SequenceStepRef, StepCondition


class FakeStepRepo:
    """Records status writes the way RunStepRepository would persist them."""

    def __init__(self, releases=None):
        self.status = {}
        self.calls = []
        # step_id -> action, returned once the coordinator polls for a release.
        self.releases = releases or {}

    def set_status(self, step_id, status, **kw):
        self.status[step_id] = status
        self.calls.append((step_id, status))

    def get_release(self, step_id):
        return self.releases.get(step_id)


class _Result:
    def __init__(self, status="PASSED", value=0, rows=100):
        self.status = status
        self.value_mismatch_count = value
        self.missing_in_target_count = 0
        self.missing_in_source_count = 0
        self.source_row_count = rows


def _executor(steps, run_step, **kw):
    from api.services.dag_executor import DagExecutor
    repo = kw.pop("step_repo", None) or FakeStepRepo()
    ex = DagExecutor(
        steps=steps,
        step_repo=repo,
        run_step=run_step,
        is_cancel_requested=kw.pop("is_cancel_requested", lambda: False),
        max_workers=kw.pop("max_workers", 4),
        sleep=kw.pop("sleep", lambda s: None),
        clock=kw.pop("clock", lambda: 0.0),
        **kw,
    )
    return ex, repo


def _ok(status="PASSED"):
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        return StepOutcome(status=status, result=_Result(status), state=None)
    return run_step


def test_chain_runs_in_order():
    order = []

    def run_step(step):
        from api.services.dag_executor import StepOutcome
        order.append(step.step_id)
        return StepOutcome(status="PASSED", result=_Result(), state=None)

    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
        SequenceStepRef(step_id="c", job_name="jc", depends_on=["b"]),
    ]
    ex, repo = _executor(steps, run_step)
    outcome = ex.run()

    assert order == ["a", "b", "c"]
    assert outcome.cancelled is False and outcome.blocked is False
    assert repo.status == {"a": "PASSED", "b": "PASSED", "c": "PASSED"}


def test_chain_never_uses_the_thread_pool():
    threads = set()

    def run_step(step):
        from api.services.dag_executor import StepOutcome
        threads.add(threading.current_thread().name)
        return StepOutcome(status="PASSED", result=_Result(), state=None)

    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    ex, _ = _executor(steps, run_step)
    ex.run()

    # A chain has a ready-set of one, so everything runs on the caller's thread.
    assert threads == {threading.current_thread().name}


def test_independent_branches_both_run():
    seen = []

    def run_step(step):
        from api.services.dag_executor import StepOutcome
        seen.append(step.step_id)
        return StepOutcome(status="PASSED", result=_Result(), state=None)

    steps = [
        SequenceStepRef(step_id="root", job_name="j0"),
        SequenceStepRef(step_id="left", job_name="j1", depends_on=["root"]),
        SequenceStepRef(step_id="right", job_name="j2", depends_on=["root"]),
        SequenceStepRef(step_id="join", job_name="j3", depends_on=["left", "right"]),
    ]
    ex, repo = _executor(steps, run_step)
    ex.run()

    assert seen[0] == "root"
    assert seen[-1] == "join"
    assert set(seen[1:3]) == {"left", "right"}
    assert repo.status["join"] == "PASSED"


def test_failed_parent_blocks_its_descendants_only():
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        status = "FAILED" if step.step_id == "left" else "PASSED"
        return StepOutcome(status=status, result=_Result(status), state=None)

    steps = [
        SequenceStepRef(step_id="root", job_name="j0"),
        SequenceStepRef(step_id="left", job_name="j1", depends_on=["root"]),
        SequenceStepRef(step_id="left_child", job_name="j2", depends_on=["left"]),
        SequenceStepRef(step_id="right", job_name="j3", depends_on=["root"]),
    ]
    ex, repo = _executor(steps, run_step)
    outcome = ex.run()

    assert repo.status["left"] == "FAILED"
    assert repo.status["left_child"] == "BLOCKED"
    assert repo.status["right"] == "PASSED"       # unrelated branch is untouched
    assert outcome.blocked is True


def test_blocking_propagates_through_the_whole_subtree():
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        status = "FAILED" if step.step_id == "a" else "PASSED"
        return StepOutcome(status=status, result=_Result(status), state=None)

    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
        SequenceStepRef(step_id="c", job_name="jc", depends_on=["b"]),
    ]
    ex, repo = _executor(steps, run_step)
    ex.run()

    assert repo.status == {"a": "FAILED", "b": "BLOCKED", "c": "BLOCKED"}


def test_all_done_runs_even_after_a_failed_parent():
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        status = "FAILED" if step.step_id == "a" else "PASSED"
        return StepOutcome(status=status, result=_Result(status), state=None)

    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="cleanup", job_name="jc", depends_on=["a"], trigger_rule="all_done"),
    ]
    ex, repo = _executor(steps, run_step)
    ex.run()

    assert repo.status["cleanup"] == "PASSED"


def test_all_failed_only_fires_when_every_parent_failed():
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        status = "FAILED" if step.step_id in {"a", "b"} else "PASSED"
        return StepOutcome(status=status, result=_Result(status), state=None)

    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="b", job_name="jb"),
        SequenceStepRef(step_id="alert", job_name="jc", depends_on=["a", "b"], trigger_rule="all_failed"),
    ]
    ex, repo = _executor(steps, run_step)
    ex.run()

    assert repo.status["alert"] == "PASSED"


def test_child_condition_is_checked_against_every_parent():
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        value = 10 if step.step_id == "b" else 0
        return StepOutcome(status="PASSED", result=_Result("PASSED", value=value), state=None)

    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="b", job_name="jb"),
        SequenceStepRef(
            step_id="c", job_name="jc", depends_on=["a", "b"],
            condition=StepCondition(require_status=["PASSED"], max_mismatch_count=5),
        ),
    ]
    ex, repo = _executor(steps, run_step)
    ex.run()

    # 'a' satisfies the gate but 'b' has 10 mismatches, so 'c' is blocked.
    assert repo.status["c"] == "BLOCKED"


def test_wait_seconds_is_honoured_before_the_step():
    slept = []
    steps = [SequenceStepRef(step_id="a", job_name="ja", wait_seconds=3)]
    ex, _ = _executor(steps, _ok(), sleep=lambda s: slept.append(s))
    ex.run()
    assert 3 in slept


def test_cancel_requested_cancels_pending_steps():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    ex, repo = _executor(steps, _ok(), is_cancel_requested=lambda: True)
    outcome = ex.run()

    assert outcome.cancelled is True
    assert repo.status == {"a": "CANCELLED", "b": "CANCELLED"}


def test_held_step_releases_and_unblocks_its_child():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", hold_after=True),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    repo = FakeStepRepo(releases={"a": "approve"})
    ex, _ = _executor(steps, _ok(), step_repo=repo)
    ex.run()

    assert repo.status["a"] == "APPROVED"
    assert repo.status["b"] == "PASSED"


def test_skip_release_makes_the_child_block_under_all_success():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", hold_after=True),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    repo = FakeStepRepo(releases={"a": "skip"})
    ex, _ = _executor(steps, _ok(), step_repo=repo)
    ex.run()

    # SKIPPED is done-but-not-success, so all_success refuses the child.
    assert repo.status["a"] == "SKIPPED"
    assert repo.status["b"] == "BLOCKED"


def test_skip_release_still_satisfies_all_done():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", hold_after=True),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"], trigger_rule="all_done"),
    ]
    repo = FakeStepRepo(releases={"a": "skip"})
    ex, _ = _executor(steps, _ok(), step_repo=repo)
    ex.run()

    assert repo.status["b"] == "PASSED"


def test_cancel_release_cancels_the_run():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", hold_after=True),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    repo = FakeStepRepo(releases={"a": "cancel"})
    ex, _ = _executor(steps, _ok(), step_repo=repo)
    outcome = ex.run()

    assert repo.status["a"] == "CANCELLED"
    assert outcome.cancelled is True


def test_hold_timeout_auto_cancels_the_step():
    # Preserved behaviour: HOLD_TIMEOUT_SECONDS auto-cancels a stuck hold.
    ticks = iter([0.0, 0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 100.0])

    steps = [SequenceStepRef(step_id="a", job_name="ja", hold_after=True)]
    repo = FakeStepRepo()          # never released
    ex, _ = _executor(
        steps, _ok(), step_repo=repo,
        clock=lambda: next(ticks, 100.0), hold_timeout=10.0,
    )
    outcome = ex.run()

    assert repo.status["a"] == "CANCELLED"
    assert outcome.cancelled is True


def test_outcome_collects_states_for_aggregation():
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        return StepOutcome(status="PASSED", result=_Result(), state=f"state-{step.step_id}")

    steps = [SequenceStepRef(step_id="a", job_name="ja"), SequenceStepRef(step_id="b", job_name="jb")]
    ex, _ = _executor(steps, run_step)
    outcome = ex.run()

    assert set(outcome.states) == {"state-a", "state-b"}
    assert len(outcome.results) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_dag_executor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.services.dag_executor'`.

- [ ] **Step 3: Write the coordinator**

Create `api/services/dag_executor.py`:

```python
"""The DAG scheduling coordinator.

Owns only scheduling: which step is ready, what blocks what, when a hold is
released, when to stop. It knows nothing about jobs, reconciliation, or HTTP --
running a step is an injected callable, and so are the clock and sleep, which
keeps the whole thing unit-testable without a database or a real job.

A chain is just a DAG whose ready-set never exceeds one step, so it runs inline
on the caller's thread and never touches the pool.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable

from api.services.sequence_conditions import ParentOutcome, parent_satisfies, trigger_fires

# Statuses a step can reach without ever having run.
NON_RUNNING_STATUSES = frozenset({"BLOCKED", "CANCELLED"})


@dataclass(frozen=True)
class StepOutcome:
    """What running one step produced."""
    status: str                 # PASSED | FAILED | SLOW | ERROR
    result: Any | None = None   # ReconciliationResult, when there is one
    state: Any | None = None    # TestCaseState, for run aggregation


@dataclass
class DagOutcome:
    states: list = field(default_factory=list)
    results: list = field(default_factory=list)
    cancelled: bool = False
    blocked: bool = False


class DagExecutor:
    def __init__(
        self,
        steps: list,
        step_repo,
        run_step: Callable[[Any], StepOutcome],
        is_cancel_requested: Callable[[], bool],
        on_held: Callable[[Any], None] | None = None,
        max_workers: int = 1,
        hold_poll_interval: float = 5.0,
        hold_timeout: float = 86400.0,
        expire_all: Callable[[], None] | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        import time as _time

        self._steps = list(steps)
        self._by_id = {s.step_id: s for s in self._steps}
        self._repo = step_repo
        self._run_step = run_step
        self._is_cancelled = is_cancel_requested
        self._on_held = on_held
        self._max_workers = max(1, int(max_workers))
        self._hold_poll = hold_poll_interval
        self._hold_timeout = hold_timeout
        self._expire_all = expire_all or (lambda: None)
        self._clock = clock or _time.monotonic
        self._sleep = sleep or _time.sleep

        self._children: dict[str, list[str]] = {s.step_id: [] for s in self._steps}
        for step in self._steps:
            for parent in step.depends_on:
                if parent in self._children:
                    self._children[parent].append(step.step_id)

        self._pending: list[str] = [s.step_id for s in self._steps]
        self._outcomes: dict[str, ParentOutcome] = {}
        self._final: dict[str, str] = {}
        self._held: dict[str, float] = {}          # step_id -> held since (clock)
        self._outcome = DagOutcome()

    # --- public -------------------------------------------------------------

    def run(self) -> DagOutcome:
        pool: ThreadPoolExecutor | None = None
        in_flight: dict = {}
        try:
            while self._pending or in_flight or self._held:
                if self._is_cancelled():
                    self._drain(in_flight)
                    self._cancel_pending()
                    self._outcome.cancelled = True
                    return self._outcome

                self._poll_holds()
                ready = self._ready_steps()

                if not ready and not in_flight:
                    if self._held:
                        self._sleep(self._hold_poll)
                        continue
                    break   # nothing ready, nothing running, nothing held

                # A single ready step with nothing in flight runs inline. That is
                # every chain, and it keeps legacy sequences off the pool entirely.
                if len(ready) == 1 and not in_flight:
                    self._finish(ready[0], self._execute(ready[0]))
                    continue

                for step_id in ready:
                    if pool is None:
                        pool = ThreadPoolExecutor(max_workers=self._max_workers)
                    self._pending.remove(step_id)
                    self._mark(step_id, "RUNNING")
                    in_flight[pool.submit(self._run_one, step_id)] = step_id

                if in_flight:
                    done, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
                    for future in done:
                        step_id = in_flight.pop(future)
                        self._finish(step_id, future.result())
            return self._outcome
        finally:
            if pool is not None:
                pool.shutdown(wait=True)

    # --- scheduling ---------------------------------------------------------

    def _ready_steps(self) -> list[str]:
        """Pending steps whose parents are all resolved, in declared order."""
        ready = []
        for step_id in list(self._pending):
            step = self._by_id[step_id]
            if not all(p in self._final for p in step.depends_on):
                continue
            if any(p in self._held for p in step.depends_on):
                continue
            if self._decide(step_id):
                ready.append(step_id)
        return ready

    def _decide(self, step_id: str) -> bool:
        """True if the step should run; False if it was just marked BLOCKED.

        A parent that never ran (BLOCKED or CANCELLED) has no outcome to judge,
        so it blocks its children outright regardless of their trigger rule.
        """
        step = self._by_id[step_id]
        for parent in step.depends_on:
            if self._final.get(parent) in NON_RUNNING_STATUSES:
                self._block(step_id)
                return False

        satisfied = [
            parent_satisfies(step.condition, self._outcomes[p])
            for p in step.depends_on
            if p in self._outcomes
        ]
        if not trigger_fires(step.trigger_rule, satisfied):
            self._block(step_id)
            return False
        return True

    def _execute(self, step_id: str):
        self._pending.remove(step_id)
        self._mark(step_id, "RUNNING")
        return self._run_one(step_id)

    def _run_one(self, step_id: str) -> StepOutcome:
        step = self._by_id[step_id]
        if step.wait_seconds:
            self._sleep(step.wait_seconds)
        return self._run_step(step)

    def _finish(self, step_id: str, outcome: StepOutcome) -> None:
        step = self._by_id[step_id]
        self._outcomes[step_id] = ParentOutcome(status=outcome.status, result=outcome.result)
        if outcome.state is not None:
            self._outcome.states.append(outcome.state)
        if outcome.result is not None:
            self._outcome.results.append(outcome.result)

        if step.hold_after:
            self._mark(step_id, "HELD")
            self._held[step_id] = self._clock()
            if self._on_held is not None:
                self._on_held(step)
            return

        self._settle(step_id, outcome.status)

    def _settle(self, step_id: str, status: str) -> None:
        self._final[step_id] = status
        self._mark(step_id, status)

    # --- holds --------------------------------------------------------------

    def _poll_holds(self) -> None:
        if not self._held:
            return
        self._expire_all()
        now = self._clock()
        for step_id, since in list(self._held.items()):
            action = self._repo.get_release(step_id)
            if action is None:
                if self._hold_timeout > 0 and (now - since) >= self._hold_timeout:
                    action = "cancel"
                else:
                    continue
            del self._held[step_id]
            if action == "cancel":
                self._settle(step_id, "CANCELLED")
                self._outcome.cancelled = True
            elif action == "skip":
                self._outcomes[step_id] = ParentOutcome(status="SKIPPED", result=None)
                self._settle(step_id, "SKIPPED")
            else:
                self._settle(step_id, "APPROVED")

    # --- terminal handling --------------------------------------------------

    def _block(self, step_id: str) -> None:
        """Mark a step BLOCKED and propagate to its whole subtree."""
        stack = [step_id]
        while stack:
            current = stack.pop()
            if current not in self._pending:
                continue
            self._pending.remove(current)
            self._final[current] = "BLOCKED"
            self._mark(current, "BLOCKED")
            self._outcome.blocked = True
            stack.extend(self._children.get(current, []))

    def _cancel_pending(self) -> None:
        for step_id in list(self._pending):
            self._pending.remove(step_id)
            self._settle(step_id, "CANCELLED")
        for step_id in list(self._held):
            del self._held[step_id]
            self._settle(step_id, "CANCELLED")

    def _drain(self, in_flight: dict) -> None:
        for future in list(in_flight):
            step_id = in_flight.pop(future)
            try:
                self._finish(step_id, future.result())
            except Exception:
                self._settle(step_id, "ERROR")

    def _mark(self, step_id: str, status: str) -> None:
        self._repo.set_status(step_id, status)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/unit/test_dag_executor.py -v`
Expected: PASS — 17 passed.

- [ ] **Step 5: Commit**

```bash
git add api/services/dag_executor.py tests/unit/test_dag_executor.py
git commit -m "feat: add DagExecutor scheduling coordinator"
```

---

## Task 6: RunStepRepository adapter

The coordinator talks in `step_id`; the table is still keyed by `step_index`. This adapter bridges them and is the only place that knows both.

**Files:**
- Modify: `etl_framework/repository/repository.py:1297-1381` (`RunStepRepository`)
- Test: `tests/unit/test_dag_run_steps.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dag_run_steps.py`:

```python
from api.schemas import SequenceStepRef
from etl_framework.repository.repository import RunRepository, RunStepRepository

DIAMOND = [
    SequenceStepRef(step_id="root", job_name="j0"),
    SequenceStepRef(step_id="left", job_name="j1", depends_on=["root"]),
    SequenceStepRef(step_id="right", job_name="j2", depends_on=["root"], trigger_rule="all_done"),
]


def test_materialize_persists_dag_fields():
    db = _db()
    RunRepository(db).create_run("r-1", "dev", "prod", {})
    RunStepRepository(db).materialize_steps("r-1", DIAMOND)

    rows = RunStepRepository(db).list_steps("r-1")
    assert [r.step_id for r in rows] == ["root", "left", "right"]
    assert [r.step_index for r in rows] == [0, 1, 2]
    assert rows[1].depends_on == ["root"]
    assert rows[2].trigger_rule == "all_done"


def test_set_status_by_step_id():
    db = _db()
    RunRepository(db).create_run("r-2", "dev", "prod", {})
    repo = RunStepRepository(db)
    repo.materialize_steps("r-2", DIAMOND)
    repo.set_status_by_step_id("r-2", "left", "BLOCKED")

    assert repo.get_step_by_step_id("r-2", "left").status == "BLOCKED"


def test_get_release_returns_none_while_held():
    db = _db()
    RunRepository(db).create_run("r-3", "dev", "prod", {})
    repo = RunStepRepository(db)
    repo.materialize_steps("r-3", DIAMOND)
    repo.set_status_by_step_id("r-3", "root", "HELD")

    assert repo.get_release_by_step_id("r-3", "root") is None


def test_get_release_returns_the_action_after_release():
    db = _db()
    RunRepository(db).create_run("r-4", "dev", "prod", {})
    repo = RunStepRepository(db)
    repo.materialize_steps("r-4", DIAMOND)
    repo.set_status_by_step_id("r-4", "root", "HELD")
    repo.release_step("r-4", 0, "skip", "not needed", "alice")

    assert repo.get_release_by_step_id("r-4", "root") == "skip"


def test_legacy_string_steps_still_materialize():
    # Plain SequenceStep objects (no step_id) must keep working.
    from api.schemas import SequenceStep
    db = _db()
    RunRepository(db).create_run("r-5", "dev", "prod", {})
    RunStepRepository(db).materialize_steps("r-5", [SequenceStep(job_name="a")])

    row = RunStepRepository(db).list_steps("r-5")[0]
    assert (row.job_name, row.step_index, row.trigger_rule) == ("a", 0, "all_success")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_dag_run_steps.py -v`
Expected: FAIL — `test_materialize_persists_dag_fields` finds `step_id` is `None`.

- [ ] **Step 3: Extend the repository**

In `etl_framework/repository/repository.py`, replace `RunStepRepository.materialize_steps` (line 1301) with a version that persists DAG fields when present:

```python
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
                # DAG fields are absent on plain SequenceStep, which is still a
                # valid input shape -- fall back to chain-equivalent defaults.
                step_id=getattr(step, "step_id", None),
                depends_on=list(getattr(step, "depends_on", []) or []),
                trigger_rule=getattr(step, "trigger_rule", "all_success"),
                max_retries=getattr(step, "max_retries", None),
                on_failure=getattr(step, "on_failure", "skip_downstream"),
            )
            self._db.add(row)
            rows.append(row)
        self._db.commit()
        for row in rows:
            self._db.refresh(row)
        return rows
```

Then add these methods to the same class, after `get_step`:

```python
    def get_step_by_step_id(self, run_id: str, step_id: str) -> RunStep | None:
        return (
            self._db.query(RunStep)
            .filter(RunStep.run_id == run_id, RunStep.step_id == step_id)
            .first()
        )

    def set_status_by_step_id(self, run_id: str, step_id: str, status: str, **kwargs) -> RunStep | None:
        step = self.get_step_by_step_id(run_id, step_id)
        if step is None:
            return None
        return self.update_status(run_id, step.step_index, status, **kwargs)

    def get_release_by_step_id(self, run_id: str, step_id: str) -> str | None:
        """The release action for a held step, or None while it is still held."""
        step = self.get_step_by_step_id(run_id, step_id)
        if step is None:
            return "approve"
        if step.status == "HELD":
            return None
        return step.release_action or "approve"
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/unit/test_dag_run_steps.py tests/unit/test_executor_characterization.py -v`
Expected: PASS — 6 new tests plus all 10 characterization tests.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/repository/repository.py tests/unit/test_dag_run_steps.py
git commit -m "feat: address run steps by step_id and persist DAG fields"
```

---

## Task 7: The swap — RunExecutor delegates to DagExecutor

The moment of truth. The characterization tests are the gate.

**Files:**
- Modify: `api/services/run_executor.py:199-313` (`execute`)

- [ ] **Step 1: Replace the loop with delegation**

Replace the body of `RunExecutor.execute()` from the `try:` on line 212 down to (but not including) the `except Exception as exc:` on line 304 with:

```python
            try:
                self._apply_health_gate()
                jobs_index = self._build_jobs_index()
                self._validate_dependencies(steps, jobs_index)
                step_repo = RunStepRepository(self._db)
                step_repo.materialize_steps(self._run_id, self._dag_steps)

                outcome = self._build_dag_executor(step_repo, jobs_index).run()

                if outcome.cancelled:
                    self._run_repo.update_run_status(
                        self._run_id, "CANCELLED",
                        completed_at=datetime.now(timezone.utc),
                    )
                    self._fire_webhooks("CANCELLED")
                elif outcome.blocked:
                    self._write_metrics(outcome.results)
                    self._run_repo.update_run_status(
                        self._run_id, "BLOCKED",
                        completed_at=datetime.now(timezone.utc),
                        total_tests=len(outcome.states),
                        passed=sum(1 for s in outcome.states if s.status == TestStatus.PASSED),
                        failed=sum(1 for s in outcome.states if s.status == TestStatus.FAILED),
                        slow=sum(1 for s in outcome.states if s.status == TestStatus.SLOW),
                        error=sum(1 for s in outcome.states if s.status == TestStatus.ERROR),
                    )
                    self._fire_webhooks("BLOCKED")
                else:
                    self._write_metrics(outcome.results)
                    self._complete_run(outcome.states)
```

Then add these three methods to `RunExecutor`, next to `_poll_for_release`:

```python
    def _build_dag_executor(self, step_repo: RunStepRepository, jobs_index: dict):
        from api.services.dag_executor import DagExecutor

        return DagExecutor(
            steps=self._dag_steps,
            step_repo=_StepRepoAdapter(step_repo, self._run_id),
            run_step=lambda step: self._run_dag_step(step, jobs_index),
            is_cancel_requested=lambda: self._run_repo.is_cancel_requested(self._run_id),
            on_held=lambda step: self._on_step_held(step_repo, step),
            # Sequential mode pins the pool to one worker; the graph is still
            # walked topologically.
            max_workers=1 if self._settings.execution_mode == "sequential" else self._settings.max_workers,
            hold_poll_interval=HOLD_POLL_INTERVAL_SECONDS,
            hold_timeout=HOLD_TIMEOUT_SECONDS,
            expire_all=self._db.expire_all,
        )

    def _run_dag_step(self, step, jobs_index: dict):
        from api.services.dag_executor import StepOutcome

        job_def = jobs_index.get(step.job_name)
        if job_def is None:
            return StepOutcome(status="ERROR", result=None, state=None)

        case_fn = self._build_case(job_def)
        state = TestRunner(max_workers=1).run([(job_def.name, case_fn)])[0]
        results = self._persist_states([state])
        status = state.status.value if hasattr(state.status, "value") else str(state.status)
        return StepOutcome(status=status, result=results[0] if results else None, state=state)

    def _on_step_held(self, step_repo: RunStepRepository, step) -> None:
        row = step_repo.get_step_by_step_id(self._run_id, step.step_id)
        index = row.step_index if row is not None else 0
        step_repo.update_status(
            self._run_id, index, "HELD", held_at=datetime.now(timezone.utc),
        )
        self._fire_held_webhook(index, step.job_name)
```

And add the adapter as a module-level class just above `class RunExecutor`:

```python
class _StepRepoAdapter:
    """Translates the coordinator's step_id vocabulary to run_steps rows."""

    def __init__(self, step_repo: RunStepRepository, run_id: str) -> None:
        self._repo = step_repo
        self._run_id = run_id

    def set_status(self, step_id: str, status: str, **kwargs) -> None:
        self._repo.set_status_by_step_id(self._run_id, step_id, status, **kwargs)

    def get_release(self, step_id: str) -> str | None:
        return self._repo.get_release_by_step_id(self._run_id, step_id)
```

`_poll_for_release` and `_sleep_with_cancel_check` are now unused by `execute()`. **Leave them in place** — `tests/integration/test_hold_polling.py` and other suites may reference them, and deleting code is not what this task is for.

- [ ] **Step 2: Run the gate**

Run: `python -m pytest tests/unit/test_executor_characterization.py -v`

Expected: **all 10 PASS, unmodified.** This is the entire point of Task 1. If a test fails:
- Read what it asserts and what the DAG executor produced.
- Fix `DagExecutor` or the delegation, **not the test**.
- Only change a characterization test if you can state plainly why the old behaviour was wrong and the new one is right — and then say so in the commit message.

- [ ] **Step 3: Run every executor-related suite**

Run: `python -m pytest tests/unit/test_run_executor.py tests/unit/test_run_cancel.py tests/unit/test_pass_condition_executor.py tests/unit/test_run_executor_gates.py tests/unit/test_dag_retry_trends.py tests/integration/test_hold_polling.py tests/integration/test_cancel_flow.py -v`
Expected: PASS.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest tests/unit tests/integration -q`
Expected: PASS with no new failures.

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py
git commit -m "feat: execute every run through the DAG coordinator"
```

---

## Task 8: Pass real DAG steps through from saved sequences

Until now every run is still a chain, because callers hand over `as_linear_steps()`, which drops `depends_on`. This wires the real graph through.

**Files:**
- Modify: `api/services/sequence_resolver.py`
- Modify: `api/routes/selections.py` (launch handler)
- Modify: `api/services/scheduler.py` (`_run_schedule`)
- Test: `tests/integration/test_dag_branch_hold.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_dag_branch_hold.py`:

```python
"""A held branch must not stop an independent branch from finishing."""
from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.schemas import RunSettings, SequenceStepRef
from api.services import run_executor as _re_module
from etl_framework.repository.database import Base, _ensure_compare_columns
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    JobRepository, RunRepository, RunStepRepository,
)

_re_module.HOLD_POLL_INTERVAL_SECONDS = 0.2


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'dag.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(eng)
    _ensure_compare_columns(eng)
    return eng


def _session(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def _seed_jobs(engine):
    db = _session(engine)
    try:
        for name in ("root", "held", "free"):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [],
                "source_env": None, "target_env": None,
                "params": {
                    "source_rows": [{"id": 1, "amount": 1.0}],
                    "target_rows": [{"id": 1, "amount": 1.0}],
                },
                "enabled": True,
            })
    finally:
        db.close()


def test_independent_branch_completes_while_another_is_held(engine):
    _seed_jobs(engine)
    db = _session(engine)
    RunRepository(db).create_run("dag-1", "dev", "prod", {})
    db.close()

    steps = [
        SequenceStepRef(step_id="root", job_name="root"),
        SequenceStepRef(step_id="held", job_name="held", depends_on=["root"], hold_after=True),
        SequenceStepRef(step_id="free", job_name="free", depends_on=["root"]),
    ]

    def _run():
        from api.services.run_executor import RunExecutor
        session = _session(engine)
        try:
            RunExecutor(
                db=session, run_id="dag-1", source_env="dev", target_env="prod",
                job_sequence=steps,
                run_settings=RunSettings(metrics_enabled=False, max_workers=4),
            ).execute()
        finally:
            session.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    # The free branch must finish while 'held' is still waiting on a human.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        probe = _session(engine)
        try:
            rows = {s.step_id: s.status for s in RunStepRepository(probe).list_steps("dag-1")}
        finally:
            probe.close()
        if rows.get("free") == "PASSED" and rows.get("held") == "HELD":
            break
        time.sleep(0.2)
    else:
        pytest.fail(f"free branch never completed while held; last saw {rows}")

    # Release the hold and let the run finish.
    releaser = _session(engine)
    try:
        row = RunStepRepository(releaser).get_step_by_step_id("dag-1", "held")
        RunStepRepository(releaser).release_step("dag-1", row.step_index, "approve", "ok", "alice")
    finally:
        releaser.close()

    thread.join(timeout=20)
    assert not thread.is_alive()

    final = _session(engine)
    try:
        run = RunRepository(final).get_run("dag-1")
        rows = {s.step_id: s.status for s in RunStepRepository(final).list_steps("dag-1")}
    finally:
        final.close()

    assert rows["free"] == "PASSED"
    assert rows["held"] == "APPROVED"
    assert run.status in {"PASSED", "SLOW"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/integration/test_dag_branch_hold.py -v`

Expected: **PASS**, and that is the point of running it here. This test hands `SequenceStepRef` objects straight to `RunExecutor`, which `normalize_to_dag` passes through untouched, so it exercises the coordinator end-to-end through a real database without depending on Step 3 at all.

Running it now proves the executor half works before you touch the call sites, so if anything breaks in Step 4 you know the plumbing caused it. If it fails here — typically the free branch never completing, or the thread not joining — fix `DagExecutor` before going any further.

- [ ] **Step 3: Stop flattening the graph at the call sites**

In `api/services/sequence_resolver.py`, mark the downgrade as legacy so nobody reaches for it by habit:

```python
    def as_linear_steps(self) -> list[SequenceStep]:
        """DEPRECATED -- returns a chain, discarding depends_on and trigger_rule.

        Kept only for callers that genuinely need the old flat shape (env
        validation, config snapshots). Anything that EXECUTES the sequence must
        use `.steps` so the DAG survives.
        """
```

In `api/routes/selections.py`'s launch handler, keep `as_linear_steps()` for `_validate_env_requirements` and the config snapshot, but execute the real steps. Where the code currently reads:

```python
        job_sequence = resolved.as_linear_steps()
```

change it to:

```python
        job_sequence = resolved.as_linear_steps()   # flat shape, for env validation + snapshot
        dag_steps = resolved.steps                  # real graph, for execution
```

and at the end of the handler pass `dag_steps` to the background task instead of `ordered_jobs`:

```python
    background_tasks.add_task(
        _execute_run, run_id, dag_steps if resolved is not None else ordered_jobs,
        trigger.source_env, trigger.target_env, trigger.run_settings, config_snapshot,
    )
```

Apply the identical change in `api/services/scheduler.py::_run_schedule`: keep `resolved.as_linear_steps()` for the `RunTrigger` and the snapshot, and pass `resolved.steps` to `_execute_run` when `sequence_meta is not None`.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/integration/test_dag_branch_hold.py tests/integration/test_sequence_workflow.py tests/unit/test_selections_sequence_ref.py tests/unit/test_schedules_sequence_target.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/sequence_resolver.py api/routes/selections.py api/services/scheduler.py tests/integration/test_dag_branch_hold.py
git commit -m "feat: execute saved sequences as real DAGs, not flattened chains"
```

---

## Task 9: Open trigger_rule for saving

**Files:**
- Modify: `api/services/sequence_validation.py` (`phase1_unsupported`)
- Test: `tests/unit/test_sequence_validation.py` (modify one test)

- [ ] **Step 1: Update the tests**

In `tests/unit/test_sequence_validation.py`, replace `test_phase1_rejects_non_default_trigger_rule` with:

```python
def test_trigger_rules_are_allowed_from_phase2():
    from api.services.sequence_validation import phase1_unsupported
    for rule in ("all_success", "all_done", "any_success", "all_failed"):
        assert phase1_unsupported([_step("a", trigger_rule=rule)], None) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_sequence_validation.py -v`
Expected: FAIL — `test_trigger_rules_are_allowed_from_phase2` still sees a `trigger_rule` issue.

- [ ] **Step 3: Remove the gate**

In `api/services/sequence_validation.py`, delete this block from `phase1_unsupported`:

```python
        if step.trigger_rule != "all_success":
            errors.append(_issue(step.step_id, "trigger_rule",
                                 "Trigger rules other than 'all_success' arrive in Phase 2"))
```

Update the function's docstring/comment so it reads "Phase 3/4" rather than "Phases 2-4". Retry, `on_failure`, and preconditions stay gated.

- [ ] **Step 4: Add a route-level test**

Append to `tests/unit/test_sequences_routes.py`:

```python
def test_create_accepts_a_trigger_rule(client):
    resp = _create(client, steps=[
        {"step_id": "a", "job_name": "orders_recon", "depends_on": []},
        {"step_id": "cleanup", "job_name": "load_orders", "depends_on": ["a"],
         "trigger_rule": "all_done"},
    ])
    assert resp.status_code == 201, resp.text


def test_create_still_rejects_retry_and_on_failure(client):
    resp = _create(client, steps=[
        {"step_id": "a", "job_name": "orders_recon", "depends_on": [], "max_retries": 3},
    ])
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "max_retries"
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/unit/test_sequence_validation.py tests/unit/test_sequences_routes.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/services/sequence_validation.py tests/unit/test_sequence_validation.py tests/unit/test_sequences_routes.py
git commit -m "feat: allow trigger rules to be saved now the DAG executor honours them"
```

---

## Task 10: API surface — RunStepOut, SSE, release by step_id

**Files:**
- Modify: `api/schemas.py` (`RunStepOut`, line 183)
- Modify: `api/routes/runs.py` (SSE block near line 1289; new release route near line 1807)
- Test: `tests/unit/test_runs_dag_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_runs_dag_api.py`:

```python
"""DAG-aware fields on the runs API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import SequenceStepRef


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import (
        RunRepository, RunStepRepository, TokenRepository,
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        RunRepository(db).create_run("run-dag", "dev", "prod", {})
        RunStepRepository(db).materialize_steps("run-dag", [
            SequenceStepRef(step_id="root", job_name="a"),
            SequenceStepRef(step_id="leaf", job_name="b", depends_on=["root"],
                            trigger_rule="all_done", hold_after=True),
        ])

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def test_steps_endpoint_exposes_dag_fields(client):
    steps = client.get("/api/runs/run-dag/steps").json()
    assert steps[0]["step_id"] == "root"
    assert steps[1]["depends_on"] == ["root"]
    assert steps[1]["trigger_rule"] == "all_done"
    assert steps[1]["attempt"] == 0
    assert steps[1]["on_failure"] == "skip_downstream"


def test_release_by_step_id(client):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunStepRepository
    db = _db_module.SessionLocal()
    try:
        RunStepRepository(db).set_status_by_step_id("run-dag", "leaf", "HELD")
    finally:
        db.close()

    resp = client.post("/api/runs/run-dag/steps/by-id/leaf/release", json={
        "action": "approve", "note": "looks fine", "released_by": "alice",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "APPROVED"


def test_release_by_unknown_step_id_is_404(client):
    resp = client.post("/api/runs/run-dag/steps/by-id/ghost/release", json={
        "action": "approve", "note": "n", "released_by": "alice",
    })
    assert resp.status_code == 404


def test_release_by_step_id_conflicts_when_not_held(client):
    resp = client.post("/api/runs/run-dag/steps/by-id/root/release", json={
        "action": "approve", "note": "n", "released_by": "alice",
    })
    assert resp.status_code == 409
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_runs_dag_api.py -v`
Expected: FAIL — `KeyError: 'step_id'` on the first test.

- [ ] **Step 3: Extend RunStepOut**

In `api/schemas.py`, add to `RunStepOut` (after `wait_seconds`, line 191):

```python
    step_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    trigger_rule: str = "all_success"
    attempt: int = 0
    max_retries: int | None = None
    on_failure: str = "skip_downstream"
```

- [ ] **Step 4: Add the release-by-step-id route**

In `api/routes/runs.py`, directly after the existing `/{run_id}/steps/{step_index}/release` route (line 1807), add:

```python
@router.post(
    "/{run_id}/steps/by-id/{step_id}/release",
    response_model=RunStepOut,
)
def release_run_step_by_id(
    run_id: str,
    step_id: str,
    body: RunStepReleaseRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    """Release a held step by its stable step_id.

    The index-based route stays for backward compatibility; step_index is still
    unique within a run, so both address the same row.
    """
    repo = RunStepRepository(db)
    step = repo.get_step_by_step_id(run_id, step_id)
    if step is None:
        raise HTTPException(status_code=404, detail="Step not found")
    released = repo.release_step(
        run_id, step.step_index, body.action, body.note, body.released_by
    )
    if released is None:
        raise HTTPException(status_code=409, detail="Step is not held")
    AuditService(db).log(
        request, "run.step_released", "run", None,
        {"run_id": run_id, "step_id": step_id, "action": body.action},
    )
    return released
```

Match the imports and the audit-log shape used by the existing index-based release handler — read it first and mirror it rather than assuming.

- [ ] **Step 5: Add steps to the SSE payload**

In `api/routes/runs.py`, in the SSE block near line 1289, keep `current_step` and `held_step` untouched and add a `steps` array to the emitted dict:

```python
                "steps": [
                    {"step_id": s.step_id, "status": s.status, "attempt": s.attempt or 0}
                    for s in steps
                ],
```

`steps` is already in scope from `step_repo.list_steps(run_id)` on line 1292.

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/unit/test_runs_dag_api.py tests/unit/test_runs_extensions.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/schemas.py api/routes/runs.py tests/unit/test_runs_dag_api.py
git commit -m "feat: expose DAG step fields, SSE steps payload, and release by step_id"
```

---

## Task 11: Monitor timeline

**Files:**
- Modify: `frontend/features/monitor.js`
- Modify: `frontend/partials/tab-monitor.html`

Monitor renders steps **per run**, inside a loop over `run`, reading `runStepsCache[run.run_id]` (`frontend/partials/tab-monitor.html:80-84`; the cache is populated by `loadRunSteps(runId)` at `frontend/features/monitor.js:100`). Level grouping must therefore be a **method taking the step array**, not a getter over a single `runSteps` property — there is no such property.

- [ ] **Step 1: Add level grouping to the monitor slice**

In `frontend/features/monitor.js`, add to the returned object:

```javascript
      // Steps grouped by dependency depth, so branches read as parallel rows.
      // Takes the array because Monitor holds one list per run in runStepsCache.
      monitorStepLevels(steps) {
        const list = steps || [];
        const byId = {};
        for (const s of list) if (s.step_id) byId[s.step_id] = s;

        const depthOf = (step, seen) => {
          const parents = (step && step.depends_on) || [];
          if (!parents.length) return 0;
          if (seen.has(step.step_id)) return 0;         // cycle guard; never valid here
          seen.add(step.step_id);
          return 1 + Math.max(...parents.map((p) => (byId[p] ? depthOf(byId[p], seen) : 0)));
        };

        const levels = [];
        for (const step of list) {
          const d = depthOf(step, new Set());
          (levels[d] = levels[d] || []).push(step);
        }
        return levels.map((s, index) => ({ index, steps: s || [] }));
      },

      monitorStepBadgeClass(status) {
        if (status === 'BLOCKED') return 'badge badge-muted';
        if (status === 'HELD') return 'badge badge-warn';
        if (status === 'PASSED' || status === 'APPROVED') return 'badge badge-ok';
        if (status === 'FAILED' || status === 'ERROR') return 'badge badge-danger';
        if (status === 'RUNNING') return 'badge badge-info';
        return 'badge';
      },
```

Use whichever badge class names `frontend/styles.css` actually defines — read the existing status badges in `tab-monitor.html` and reuse those exact names.

- [ ] **Step 2: Add the timeline markup**

In `frontend/partials/tab-monitor.html`, inside the same `<template x-if="runStepsCache[run.run_id] && ...">` block at line 80 and **after** the existing flat step list at line 84, add:

```html
<div class="mt-3" data-testid="monitor-step-timeline">
  <div class="field-label">Step timeline</div>
  <template x-for="level in monitorStepLevels(runStepsCache[run.run_id])" :key="level.index">
    <div class="flex items-start gap-3 py-1">
      <span class="text-xs text-muted w-16 pt-1" x-text="'level ' + (level.index + 1)"></span>
      <div class="flex flex-wrap gap-2">
        <template x-for="step in level.steps" :key="step.step_id || step.step_index">
          <div class="chip flex items-center gap-2"
               :data-testid="'monitor-step-' + (step.step_id || step.step_index)">
            <span x-text="step.step_id || step.job_name"></span>
            <span :class="monitorStepBadgeClass(step.status)" x-text="step.status"></span>
            <span class="text-xs text-muted" x-show="(step.attempt || 0) > 1"
                  x-text="'try ' + step.attempt"></span>
          </div>
        </template>
      </div>
    </div>
  </template>
</div>
```

Keeping the existing flat list means the release button at line 102 and its `loadRunSteps(run.run_id)` handler are untouched.

- [ ] **Step 3: Rebuild and verify**

Run: `npm run build:html && python -m pytest tests/integration/test_api_frontend_smoke.py -v`
Expected: build succeeds; smoke test PASSES.

- [ ] **Step 4: Commit**

```bash
git add frontend/features/monitor.js frontend/partials/tab-monitor.html frontend/index.html
git commit -m "feat: show a level-grouped step timeline with BLOCKED badges in Monitor"
```

---

## Task 12: Full verification

- [ ] **Step 1: The gate, one more time**

Run: `python -m pytest tests/unit/test_executor_characterization.py -v`
Expected: 10 passed, **still unmodified since Task 1**.

Confirm with: `git log --oneline -- tests/unit/test_executor_characterization.py`
Expected: exactly one commit. More than one means the gate was edited — go back and justify every change, or revert it.

- [ ] **Step 2: Whole Python suite**

Run: `python -m pytest tests/unit tests/integration -q`
Expected: PASS, no failures, no errors.

- [ ] **Step 3: Generated HTML in sync**

Run: `npm run build:html && git diff --exit-code frontend/index.html`
Expected: exit code 0.

- [ ] **Step 4: E2E**

Run: `rtk proxy npx playwright test`
Expected: PASS.

- [ ] **Step 5: Clean tree**

Run: `git status`
Expected: clean, or only files you intend to leave uncommitted.

---

## Phase 2 Done — What Ships

Every run goes through one coordinator. Saved sequences with branches run their independent branches concurrently, bounded by `max_workers`. Trigger rules decide each edge, so an `all_failed` branch becomes a working alert path. A held step parks in a waiting set instead of blocking the run, and the branches beside it keep going. A failed step blocks its own subtree and nothing else. Chains — every legacy sequence and every plain `job_sequence` — take a ready-set of one, run inline off the pool, and behave exactly as the characterization tests recorded before any of this existed.

Phase 3 (retry and `on_failure`) needs no schema change: the columns landed in Task 2, and opening the fields is a deletion from `phase1_unsupported` plus retry bookkeeping in `DagExecutor._finish`.
