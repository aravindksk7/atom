# Saved Execution Sequences — Phase 3 Implementation Plan (Retry & Failure Policy)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each step its own retry policy and failure policy, and make `DagExecutor` the single owner of retry across every job type.

**Architecture:** Retry runs on the **worker thread**, inside `_run_one`, so a retry delay never stalls the coordinator or the branches running beside it. The ad-hoc retry wrapper buried in `_build_case_reconciliation` is deleted, so one mechanism covers all job types, honours `retry_on`, and records the attempt count. `on_failure` gains three genuinely distinct behaviours, with `continue` meaning continue-on-error.

**Tech Stack:** Python 3.14, SQLAlchemy 2.x, `concurrent.futures`, pytest, Alpine.js, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-12-saved-execution-sequences-design.md` (as amended by `ddb97eb`)

**Depends on:** Phases 1 and 2, both implemented and committed (`b15fa49`, `7793e1b`, `58a8568`). Full suite green at 2062 passed / 18 skipped.

---

## Background For The Implementer

### What Phase 2 left you

`DagExecutor` (`api/services/dag_executor.py`) is a clean coordinator: injected `run_step`, `clock`, `sleep`, and `step_repo`. `_run_one` runs on a worker thread; `_finish` and everything else runs on the coordinator thread. **Keep that split.** Anything that sleeps belongs in `_run_one`.

Phase 2 added two things beyond its plan, both of which you must preserve:
- `blocked_step_status` — lets the caller report blocked steps under a different status for backward compatibility.
- A cancel check after `wait_seconds` in `_run_one`.

### The two conflicts this phase resolves

**1. Retry already exists, badly.** `api/services/run_executor.py:576-590` wraps *only* reconciliation jobs in a retry loop that catches exceptions, backs off exponentially, records nothing, and ignores `RunSettings.retry_on`. Phase 3 **deletes it**. Since run-level `max_retries` defaults to 0, a default run behaves identically before and after.

**2. `on_failure`'s three values described two behaviours.** The spec's original `continue` ("descendants decided purely by their trigger rule") is already what the default does — `tests/unit/test_dag_executor.py::test_all_done_runs_even_after_a_failed_parent` proves an `all_done` branch survives a failed parent with `on_failure` at its default. The spec was amended. Final semantics:

| `on_failure` | Effect |
|---|---|
| `stop` | Abort the run: stop scheduling, drain in-flight, mark the remainder `CANCELLED`. |
| `skip_downstream` (default) | Exactly today's behaviour. Trigger rules decide downstream; the failure counts toward run status. |
| `continue` | Same scheduling as the default, but this step's failure is **excluded from run-status aggregation**. |

Because the default keeps its current meaning, no stored sequence changes behaviour and no Phase 2 test needs editing.

### `retry_on` and the missing TIMEOUT status

`RunSettings.retry_on` is `list[Literal["error", "timeout"]]`, defaulting to `["error"]`. The runner's `TestStatus` enum has only `PASSED`, `FAILED`, `SLOW`, `ERROR` — there is no `TIMEOUT`. So:

- `"error"` → retries the `ERROR` status.
- `"timeout"` → maps to nothing today. Accept it, match no status, and leave a comment. A timeout currently surfaces as `ERROR` anyway.
- `FAILED` is **never** retried. It means a real data mismatch, and re-running cannot change it.
- `SLOW` is never retried. It passed, just slowly.

### Verification rules

- Raw `python -m pytest`, never `rtk` — it serves a cached summary.
- Playwright via `rtk proxy npx playwright test`.
- `tests/unit/test_executor_characterization.py` is still the behaviour gate from Phase 2, and still has exactly one commit. It must stay that way.

---

## File Structure

**Modify**

| File | Change |
|---|---|
| `api/services/dag_executor.py` | Retry loop in `_run_one`; `on_failure` handling in `_finish`; `tolerated_states` on `DagOutcome`. |
| `api/services/run_executor.py` | Delete the reconciliation retry wrapper; pass run-level retry defaults; exclude tolerated states from aggregation. |
| `api/services/sequence_validation.py` | Open `max_retries`, `retry_delay_seconds`, `on_failure`. |
| `frontend/partials/tab-sequences.html` | Trigger-rule select (Phase 2 gap) plus retry and failure-policy fields. |
| `frontend/features/sequences.js` | Defaults for the new step fields. |
| `frontend/help-content.js` | Retry and failure-policy guidance. |

**Test**

| File | Covers |
|---|---|
| `tests/unit/test_dag_retry.py` | Retry eligibility, attempt counting, delays, cancel-during-retry. |
| `tests/unit/test_dag_on_failure.py` | `stop`, `continue`, and default behaviour. |
| `tests/unit/test_sequence_validation.py` | Gate opening (modify existing). |
| `tests/unit/test_sequences_routes.py` | Route-level acceptance (append). |
| `tests/integration/test_dag_retry_flow.py` | Retry through a real run, attempt persisted. |

---

## Task 1: Retry in the coordinator

**Files:**
- Modify: `api/services/dag_executor.py`
- Test: `tests/unit/test_dag_retry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_dag_retry.py`:

```python
"""Step-level retry inside DagExecutor."""
from __future__ import annotations

from api.schemas import SequenceStepRef


class FakeStepRepo:
    def __init__(self, releases=None):
        self.status = {}
        self.calls = []
        self.attempts = {}
        self.releases = releases or {}

    def set_status(self, step_id, status, **kw):
        self.status[step_id] = status
        self.calls.append((step_id, status))
        if "attempt" in kw:
            self.attempts[step_id] = kw["attempt"]

    def get_release(self, step_id):
        return self.releases.get(step_id)


class _Result:
    def __init__(self, status="PASSED"):
        self.status = status
        self.value_mismatch_count = 0
        self.missing_in_target_count = 0
        self.missing_in_source_count = 0
        self.source_row_count = 10


def _scripted(statuses_by_step):
    """A run_step that returns a scripted status sequence per step_id."""
    calls = {}

    def run_step(step):
        from api.services.dag_executor import StepOutcome
        n = calls.get(step.step_id, 0)
        calls[step.step_id] = n + 1
        script = statuses_by_step[step.step_id]
        status = script[min(n, len(script) - 1)]
        return StepOutcome(status=status, result=_Result(status), state=f"{step.step_id}-{n}")

    run_step.calls = calls
    return run_step


def _executor(steps, run_step, **kw):
    from api.services.dag_executor import DagExecutor
    repo = kw.pop("step_repo", None) or FakeStepRepo()
    slept = []
    ex = DagExecutor(
        steps=steps,
        step_repo=repo,
        run_step=run_step,
        is_cancel_requested=kw.pop("is_cancel_requested", lambda: False),
        max_workers=kw.pop("max_workers", 4),
        sleep=kw.pop("sleep", slept.append),
        clock=kw.pop("clock", lambda: 0.0),
        **kw,
    )
    return ex, repo, slept


def test_error_is_retried_up_to_max_retries():
    run_step = _scripted({"a": ["ERROR", "ERROR", "PASSED"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=2)]
    ex, repo, _ = _executor(steps, run_step)
    ex.run()

    assert run_step.calls["a"] == 3
    assert repo.status["a"] == "PASSED"


def test_retry_stops_at_the_limit_and_keeps_the_last_status():
    run_step = _scripted({"a": ["ERROR"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=2)]
    ex, repo, _ = _executor(steps, run_step)
    ex.run()

    assert run_step.calls["a"] == 3        # initial + 2 retries
    assert repo.status["a"] == "ERROR"


def test_failed_is_never_retried():
    run_step = _scripted({"a": ["FAILED"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=5)]
    ex, repo, _ = _executor(steps, run_step)
    ex.run()

    assert run_step.calls["a"] == 1
    assert repo.status["a"] == "FAILED"


def test_slow_is_never_retried():
    run_step = _scripted({"a": ["SLOW"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=3)]
    ex, repo, _ = _executor(steps, run_step)
    ex.run()

    assert run_step.calls["a"] == 1


def test_no_retry_when_max_retries_is_zero():
    run_step = _scripted({"a": ["ERROR"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=0)]
    ex, _, _ = _executor(steps, run_step)
    ex.run()

    assert run_step.calls["a"] == 1


def test_null_max_retries_inherits_the_run_default():
    run_step = _scripted({"a": ["ERROR"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja")]     # max_retries is None
    ex, _, _ = _executor(steps, run_step, default_max_retries=2)
    ex.run()

    assert run_step.calls["a"] == 3


def test_step_max_retries_overrides_the_run_default():
    run_step = _scripted({"a": ["ERROR"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=0)]
    ex, _, _ = _executor(steps, run_step, default_max_retries=5)
    ex.run()

    assert run_step.calls["a"] == 1


def test_retry_delay_is_slept_between_attempts():
    run_step = _scripted({"a": ["ERROR", "PASSED"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=1, retry_delay_seconds=7)]
    ex, _, slept = _executor(steps, run_step)
    ex.run()

    assert 7 in slept


def test_retry_delay_inherits_the_run_default():
    run_step = _scripted({"a": ["ERROR", "PASSED"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=1)]
    ex, _, slept = _executor(steps, run_step, default_retry_delay_seconds=4)
    ex.run()

    assert 4 in slept


def test_attempt_number_is_persisted():
    run_step = _scripted({"a": ["ERROR", "ERROR", "PASSED"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=2)]
    ex, repo, _ = _executor(steps, run_step)
    ex.run()

    # Three executions -> the final row records attempt 3.
    assert repo.attempts["a"] == 3


def test_retry_on_without_error_disables_retry():
    run_step = _scripted({"a": ["ERROR"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=3)]
    ex, _, _ = _executor(steps, run_step, retry_on=["timeout"])
    ex.run()

    # 'timeout' matches no status today, so ERROR is not retryable.
    assert run_step.calls["a"] == 1


def test_cancel_during_retry_delay_stops_retrying():
    cancelled = {"value": False}
    run_step = _scripted({"a": ["ERROR"]})

    def sleeper(_seconds):
        cancelled["value"] = True      # cancel arrives while we wait

    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=5)]
    ex, _, _ = _executor(
        steps, run_step,
        sleep=sleeper,
        is_cancel_requested=lambda: cancelled["value"],
    )
    ex.run()

    assert run_step.calls["a"] == 1


def test_retry_does_not_stall_an_independent_branch():
    run_step = _scripted({"slow": ["ERROR", "PASSED"], "fast": ["PASSED"]})
    steps = [
        SequenceStepRef(step_id="slow", job_name="j1", max_retries=1, retry_delay_seconds=5),
        SequenceStepRef(step_id="fast", job_name="j2"),
    ]
    ex, repo, _ = _executor(steps, run_step)
    ex.run()

    # Both are roots, so they run concurrently -- the retry sleeps on its own
    # worker thread and never blocks the coordinator.
    assert repo.status["slow"] == "PASSED"
    assert repo.status["fast"] == "PASSED"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_dag_retry.py -v`
Expected: FAIL — `test_error_is_retried_up_to_max_retries` sees 1 call, and the `default_max_retries` tests raise `TypeError` for an unexpected keyword argument.

- [ ] **Step 3: Implement retry**

In `api/services/dag_executor.py`, add a module-level mapping below `NON_RUNNING_STATUSES`:

```python
# RunSettings.retry_on tokens mapped to runner statuses. There is no TIMEOUT in
# TestStatus today -- a timeout surfaces as ERROR -- so "timeout" matches nothing
# and is accepted only so the setting stays forward-compatible.
RETRYABLE_STATUS_BY_TOKEN = {"error": frozenset({"ERROR"}), "timeout": frozenset()}
```

Add three constructor parameters, after `hold_timeout`:

```python
        default_max_retries: int = 0,
        default_retry_delay_seconds: float = 0.0,
        retry_on: list[str] | None = None,
```

and store them in `__init__`:

```python
        self._default_max_retries = default_max_retries
        self._default_retry_delay = default_retry_delay_seconds
        self._retryable: frozenset[str] = frozenset().union(
            *(RETRYABLE_STATUS_BY_TOKEN.get(token, frozenset())
              for token in (retry_on if retry_on is not None else ["error"]))
        )
        self._attempts: dict[str, int] = {}
```

Add two helpers in the scheduling section:

```python
    def _retry_budget(self, step) -> tuple[int, float]:
        """Per-step retry settings, falling back to the run-level defaults."""
        limit = step.max_retries if step.max_retries is not None else self._default_max_retries
        delay = (
            step.retry_delay_seconds
            if step.retry_delay_seconds is not None
            else self._default_retry_delay
        )
        return max(0, int(limit or 0)), float(delay or 0.0)

    def _should_retry(self, status: str, attempt: int, limit: int) -> bool:
        # FAILED is a real data mismatch and SLOW already passed -- neither is
        # retryable however generous the budget.
        return status in self._retryable and attempt <= limit
```

Replace `_run_one` with a retrying version. **It stays on the worker thread**, which is what keeps a retry delay from stalling the coordinator:

```python
    def _run_one(self, step_id: str) -> StepOutcome:
        step = self._by_id[step_id]
        if step.wait_seconds:
            self._sleep(step.wait_seconds)
            if self._is_cancelled():
                self._outcome.cancelled = True
                return StepOutcome(status="CANCELLED", result=None, state=None)

        limit, delay = self._retry_budget(step)
        attempt = 0
        while True:
            outcome = self._run_step(step)
            attempt += 1
            self._attempts[step_id] = attempt
            if not self._should_retry(outcome.status, attempt, limit):
                return outcome
            if delay:
                self._sleep(delay)
            if self._is_cancelled():
                self._outcome.cancelled = True
                return outcome
```

Finally, persist the attempt count. Change `_settle` so the final write carries it:

```python
    def _settle(self, step_id: str, status: str) -> None:
        self._final[step_id] = status
        attempt = self._attempts.get(step_id)
        if attempt is not None:
            self._repo.set_status(step_id, status, attempt=attempt)
        else:
            self._mark(step_id, status)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/unit/test_dag_retry.py tests/unit/test_dag_executor.py tests/unit/test_executor_characterization.py -v`
Expected: PASS — 13 new tests, all Phase 2 coordinator tests, and all 10 characterization tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add api/services/dag_executor.py tests/unit/test_dag_retry.py
git commit -m "feat: retry steps on the worker thread with per-step budgets"
```

---

## Task 2: Retire the reconciliation retry wrapper

**Files:**
- Modify: `api/services/run_executor.py:576-590` and `_build_dag_executor` (line 335)
- Test: `tests/integration/test_dag_retry_flow.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_dag_retry_flow.py`:

```python
"""Retry through a real run, with the attempt count persisted."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import RunSettings, SequenceStepRef
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    JobRepository, RunRepository, RunStepRepository,
)


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _erroring_job(db, name):
    """Mismatched source/target columns, which raise under an 'error' policy.

    This is the same recipe tests/unit/test_run_executor.py:375 uses to provoke
    an ERROR status -- source has col_a, target has col_b, and the run settings
    below set schema_mismatch_policy="error".
    """
    JobRepository(db).create({
        "name": name, "description": "", "tags": [],
        "job_type": "reconciliation", "query": f"SELECT * FROM {name}",
        "key_columns": ["id"], "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {
            "source_rows": [{"id": 1, "col_a": "x"}],
            "target_rows": [{"id": 1, "col_b": "x"}],
        },
        "enabled": True,
    })


_ERROR_SETTINGS = RunSettings(schema_mismatch_policy="error", metrics_enabled=False)


def test_step_retry_records_attempts_and_final_error():
    db = _session()
    RunRepository(db).create_run("retry-1", "dev", "prod", {})
    _erroring_job(db, "flaky")

    from api.services.run_executor import RunExecutor
    RunExecutor(
        db=db, run_id="retry-1", source_env="dev", target_env="prod",
        job_sequence=[SequenceStepRef(
            step_id="a", job_name="flaky", max_retries=2, retry_delay_seconds=0,
        )],
        run_settings=_ERROR_SETTINGS,
    ).execute()

    step = RunStepRepository(db).get_step_by_step_id("retry-1", "a")
    assert step.status == "ERROR"
    assert step.attempt == 3          # initial + 2 retries


def test_default_run_does_not_retry():
    db = _session()
    RunRepository(db).create_run("retry-2", "dev", "prod", {})
    _erroring_job(db, "flaky")

    from api.services.run_executor import RunExecutor
    RunExecutor(
        db=db, run_id="retry-2", source_env="dev", target_env="prod",
        job_sequence=["flaky"],
        run_settings=_ERROR_SETTINGS,
    ).execute()

    step = RunStepRepository(db).list_steps("retry-2")[0]
    assert step.status == "ERROR"
    assert step.attempt == 1          # run-level max_retries defaults to 0
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/integration/test_dag_retry_flow.py -v`
Expected: FAIL — `attempt` is 1 in the first test, because the run-level defaults are not wired through yet.

- [ ] **Step 3: Delete the wrapper**

In `api/services/run_executor.py`, in `_build_case_reconciliation`, delete the whole retry block (lines 576-590) and return the plain job:

```python
        return run_job
```

That removes `max_retries`, `retry_delay`, and the nested `run_with_retry`. Retry is now the coordinator's job, uniformly, for every job type.

- [ ] **Step 4: Wire the run-level defaults in**

In `_build_dag_executor` (line 335), add three arguments to the `DagExecutor(...)` call:

```python
            default_max_retries=self._settings.max_retries,
            default_retry_delay_seconds=self._settings.retry_delay_seconds,
            retry_on=list(self._settings.retry_on or []),
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/integration/test_dag_retry_flow.py tests/unit/test_run_executor.py tests/unit/test_executor_characterization.py tests/unit/test_dag_retry_trends.py -v`
Expected: PASS.

If a reconciliation test was relying on the deleted exponential backoff, it will surface here. Read it before changing anything: the wrapper retried on *exception*, whereas the coordinator retries on `ERROR` **status**, and a raised exception becomes an `ERROR` state — so the behaviour should carry over. Only the exponential backoff (`delay * 2**attempt`) is gone, replaced by a flat delay.

- [ ] **Step 6: Commit**

```bash
git add api/services/run_executor.py tests/integration/test_dag_retry_flow.py
git commit -m "refactor: make DagExecutor the only retry mechanism"
```

---

## Task 3: `on_failure` — stop and continue

**Files:**
- Modify: `api/services/dag_executor.py`
- Modify: `api/services/run_executor.py` (aggregation)
- Test: `tests/unit/test_dag_on_failure.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_dag_on_failure.py`:

```python
"""on_failure policy: stop, continue, and the default."""
from __future__ import annotations

from api.schemas import SequenceStepRef
from tests.unit.test_dag_retry import FakeStepRepo, _Result, _executor


def _status_map(mapping):
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        status = mapping.get(step.step_id, "PASSED")
        return StepOutcome(status=status, result=_Result(status), state=f"state-{step.step_id}")
    return run_step


# --- stop -------------------------------------------------------------------

def test_stop_aborts_the_run():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", on_failure="stop"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    ex, repo, _ = _executor(steps, _status_map({"a": "FAILED"}))
    outcome = ex.run()

    assert outcome.cancelled is True
    assert repo.status["b"] == "CANCELLED"


def test_stop_also_cancels_an_unrelated_branch():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", on_failure="stop"),
        SequenceStepRef(step_id="other", job_name="jo", depends_on=["a"]),
    ]
    ex, repo, _ = _executor(steps, _status_map({"a": "ERROR"}))
    outcome = ex.run()

    assert outcome.cancelled is True
    assert repo.status["other"] == "CANCELLED"


def test_stop_does_nothing_when_the_step_succeeds():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", on_failure="stop"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    ex, repo, _ = _executor(steps, _status_map({}))
    outcome = ex.run()

    assert outcome.cancelled is False
    assert repo.status["b"] == "PASSED"


# --- continue ---------------------------------------------------------------

def test_continue_excludes_the_failure_from_aggregation():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", on_failure="continue"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"], trigger_rule="all_done"),
    ]
    ex, repo, _ = _executor(steps, _status_map({"a": "FAILED"}))
    outcome = ex.run()

    assert repo.status["a"] == "FAILED"           # the step still reads as failed
    assert outcome.tolerated_states == ["state-a"]
    assert outcome.states == ["state-b"]          # only 'b' counts toward run status


def test_continue_does_not_change_scheduling():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", on_failure="continue"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    ex, repo, _ = _executor(steps, _status_map({"a": "FAILED"}))
    ex.run()

    # all_success still refuses a failed parent -- continue is about the score,
    # not about scheduling.
    assert repo.status["b"] == "BLOCKED"


def test_continue_leaves_a_successful_step_counted():
    steps = [SequenceStepRef(step_id="a", job_name="ja", on_failure="continue")]
    ex, _, _ = _executor(steps, _status_map({}))
    outcome = ex.run()

    assert outcome.states == ["state-a"]
    assert outcome.tolerated_states == []


# --- default ----------------------------------------------------------------

def test_default_counts_the_failure_and_keeps_going():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="cleanup", job_name="jc", depends_on=["a"], trigger_rule="all_done"),
    ]
    ex, repo, _ = _executor(steps, _status_map({"a": "FAILED"}))
    outcome = ex.run()

    assert repo.status["cleanup"] == "PASSED"
    assert outcome.tolerated_states == []
    assert set(outcome.states) == {"state-a", "state-cleanup"}
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_dag_on_failure.py -v`
Expected: FAIL — `AttributeError: 'DagOutcome' object has no attribute 'tolerated_states'`.

- [ ] **Step 3: Implement**

In `api/services/dag_executor.py`, add a failure-status set beside the other constants:

```python
# Statuses that count as a step having failed, for on_failure purposes.
FAILURE_STATUSES = frozenset({"FAILED", "ERROR"})
```

Add the field to `DagOutcome`:

```python
    tolerated_states: list = field(default_factory=list)
```

Then handle the policy in `_finish`. Replace its state-collection lines with:

```python
    def _finish(self, step_id: str, outcome: StepOutcome) -> None:
        step = self._by_id[step_id]
        self._outcomes[step_id] = ParentOutcome(status=outcome.status, result=outcome.result)

        failed = outcome.status in FAILURE_STATUSES
        if outcome.state is not None:
            # continue-on-error: the step still reports its own status, but its
            # failure is kept out of the run's pass/fail arithmetic.
            if failed and step.on_failure == "continue":
                self._outcome.tolerated_states.append(outcome.state)
            else:
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

        if failed and step.on_failure == "stop":
            self._outcome.cancelled = True
```

The `run()` loop already re-checks `self._outcome.cancelled` at the top of each iteration (Phase 2, line 90), so setting the flag is enough to abort — pending steps get `CANCELLED` through `_cancel_pending`.

- [ ] **Step 4: Exclude tolerated states from run aggregation**

In `api/services/run_executor.py`, the blocked branch and the success branch both count from `outcome.states`. Since `DagExecutor` now keeps tolerated failures out of that list, the pass/fail arithmetic is already correct — but `total_tests` should still reflect every step that ran.

**Line numbers have shifted** — Task 2 deleted the retry wrapper — so find these by content, not by line:

Search for `total_tests=len(outcome.states),` inside the `elif outcome.blocked:` branch and change it to:

```python
                        total_tests=len(outcome.states) + len(outcome.tolerated_states),
```

Search for `self._complete_run(outcome.states)` and change it to:

```python
                    self._complete_run(outcome.states, outcome.tolerated_states)
```

Then find `def _complete_run(self, states:` and update the signature:

```python
    def _complete_run(self, states: list[TestCaseState], tolerated: list | None = None) -> None:
        tolerated = tolerated or []
        passed = sum(1 for state in states if state.status == TestStatus.PASSED)
        failed = sum(1 for state in states if state.status == TestStatus.FAILED)
        slow = sum(1 for state in states if state.status == TestStatus.SLOW)
        error = sum(1 for state in states if state.status == TestStatus.ERROR)
```

and its `update_run_status` call:

```python
            total_tests=len(states) + len(tolerated),
```

Leave the rest of `_complete_run` alone — `_check_contracts(states)` should keep seeing only counted states, since a tolerated failure is explicitly not a breach.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/unit/test_dag_on_failure.py tests/unit/test_dag_executor.py tests/unit/test_dag_retry.py tests/unit/test_executor_characterization.py -v`
Expected: PASS — 7 new tests plus everything from Phase 2 and Task 1.

- [ ] **Step 6: Commit**

```bash
git add api/services/dag_executor.py api/services/run_executor.py tests/unit/test_dag_on_failure.py
git commit -m "feat: implement on_failure stop and continue-on-error policies"
```

---

## Task 4: Open the validation gate

**Files:**
- Modify: `api/services/sequence_validation.py:93-106`
- Modify: `tests/unit/test_sequence_validation.py`
- Modify: `tests/unit/test_sequences_routes.py`

- [ ] **Step 1: Update the unit tests**

In `tests/unit/test_sequence_validation.py`, replace `test_phase1_rejects_retry_and_on_failure` with:

```python
def test_retry_and_on_failure_are_allowed_from_phase3():
    from api.services.sequence_validation import phase1_unsupported
    steps = [_step("a", max_retries=2, retry_delay_seconds=1.5, on_failure="stop")]
    assert phase1_unsupported(steps, None) == []


def test_preconditions_are_still_gated():
    from api.schemas import SequencePrecondition
    from api.services.sequence_validation import phase1_unsupported
    errors = phase1_unsupported([_step("a")], SequencePrecondition(weekdays=[0]))
    assert [e["field"] for e in errors] == ["preconditions"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_sequence_validation.py -v`
Expected: FAIL — `test_retry_and_on_failure_are_allowed_from_phase3` still finds three issues.

- [ ] **Step 3: Remove the gates**

In `api/services/sequence_validation.py`, `phase1_unsupported` reduces to the preconditions check:

```python
def phase1_unsupported(
    steps: list["SequenceStepRef"], preconditions: "SequencePrecondition | None"
) -> list[dict]:
    """Fields the executor cannot honour yet.

    Trigger rules opened in Phase 2; retry and failure policy in Phase 3. Only
    sequence preconditions remain, and they arrive in Phase 4.
    """
    if preconditions is not None:
        return [_issue(None, "preconditions", "Sequence preconditions arrive in Phase 4")]
    return []
```

The `steps` parameter is now unused but stays in the signature — Phase 4 removes the function entirely, and churning every call site twice is pointless.

- [ ] **Step 4: Add route coverage**

In `tests/unit/test_sequences_routes.py`, replace `test_create_still_rejects_retry_and_on_failure` with:

```python
def test_create_accepts_retry_and_failure_policy(client):
    resp = _create(client, steps=[
        {"step_id": "a", "job_name": "orders_recon", "depends_on": [],
         "max_retries": 3, "retry_delay_seconds": 10, "on_failure": "continue"},
    ])
    assert resp.status_code == 201, resp.text


def test_create_still_rejects_preconditions(client):
    resp = client.post("/api/sequences", json={
        "name": "gated", "steps": CHAIN, "preconditions": {"weekdays": [0]},
    })
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "preconditions"
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/unit/test_sequence_validation.py tests/unit/test_sequences_routes.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/services/sequence_validation.py tests/unit/test_sequence_validation.py tests/unit/test_sequences_routes.py
git commit -m "feat: allow retry and failure policy to be saved"
```

---

## Task 5: Sequence editor controls

Two gaps close here. `trigger_rule` was opened by Phase 2's API but never exposed in the editor, so it is API-only today. Retry and failure policy are new.

**Files:**
- Modify: `frontend/features/sequences.js` (`newSequenceStep`)
- Modify: `frontend/partials/tab-sequences.html` (step row and Advanced panel, around line 137)

- [ ] **Step 1: Extend the step template**

In `frontend/features/sequences.js`, update `newSequenceStep()`:

```javascript
      newSequenceStep() {
        return {
          step_id: '', job_name: '', depends_on: [],
          trigger_rule: 'all_success',
          hold_after: false, wait_seconds: 0, condition: null,
          max_retries: null, retry_delay_seconds: null,
          on_failure: 'skip_downstream',
        };
      },
```

Nulls matter: they are what makes a step inherit the run-level retry settings. Do not default them to 0.

- [ ] **Step 2: Add the trigger-rule select**

In `frontend/partials/tab-sequences.html`, inside the step row's three-column grid (next to "Runs after"), add:

```html
<label class="field" x-show="(step.depends_on || []).length > 0">
  <span class="field-label">Run this step when</span>
  <select class="field-input" :data-testid="'sequence-step-trigger-' + index"
          x-model="step.trigger_rule" @change="validateSequenceSteps()"
          aria-label="trigger rule">
    <option value="all_success">All parents succeeded</option>
    <option value="all_done">All parents finished, whatever the outcome</option>
    <option value="any_success">At least one parent succeeded</option>
    <option value="all_failed">All parents failed</option>
  </select>
</label>
```

Hiding it for a root step is deliberate — a step with no parents has nothing to trigger on.

- [ ] **Step 3: Add retry and failure policy to the Advanced panel**

Inside the existing `<details>` Advanced block (line 137), after the hold and wait fields:

```html
<label class="field">
  <span class="field-label">Retries on error</span>
  <input type="number" min="0" max="10" class="field-input"
         :data-testid="'sequence-step-retries-' + index"
         x-model.number="step.max_retries" placeholder="Inherit from run settings"
         aria-label="max retries" />
</label>
<label class="field">
  <span class="field-label">Delay between retries (seconds)</span>
  <input type="number" min="0" class="field-input"
         x-model.number="step.retry_delay_seconds" placeholder="Inherit from run settings"
         aria-label="retry delay seconds" />
</label>
<label class="field">
  <span class="field-label">If this step fails</span>
  <select class="field-input" :data-testid="'sequence-step-onfailure-' + index"
          x-model="step.on_failure" aria-label="on failure">
    <option value="skip_downstream">Carry on — dependent steps decide for themselves</option>
    <option value="continue">Carry on and don't count this failure against the run</option>
    <option value="stop">Stop the whole run</option>
  </select>
</label>
<p class="text-xs text-muted">
  Only errors are retried. A failed data comparison is a real result, so re-running it cannot change the outcome.
</p>
```

- [ ] **Step 4: Rebuild and verify**

Run: `npm run build:html && python -m pytest tests/integration/test_api_frontend_smoke.py -v`
Expected: build succeeds; smoke test PASSES.

- [ ] **Step 5: Normalise empty retry inputs to null**

`x-model.number` yields `''` or `NaN` for a cleared number input, and neither survives the `int | None` schema. Add this to `saveSequence()` in `frontend/features/sequences.js`, before the payload is built:

```javascript
        // A blank retry box means "inherit the run settings", which the API
        // expresses as null -- '' and NaN both fail schema validation.
        for (const step of this.sequenceSteps) {
          for (const key of ['max_retries', 'retry_delay_seconds']) {
            const value = step[key];
            if (value === '' || value === undefined || Number.isNaN(value)) step[key] = null;
          }
        }
```

Then check the round trip by hand: create a sequence with two steps, set the second to `all_done` with 2 retries and `continue`, save, reload the page, reopen it. The values must come back, and a blank retry box must still be blank rather than `0`.

- [ ] **Step 6: Commit**

```bash
git add frontend/features/sequences.js frontend/partials/tab-sequences.html frontend/index.html
git commit -m "feat: expose trigger rule, retry, and failure policy in the sequence editor"
```

---

## Task 6: Help content

**Files:**
- Modify: `frontend/help-content.js`

- [ ] **Step 1: Extend the Sequences section**

Add these steps to the existing `sequences` section's `steps[]` array, following the same `{title, text, where, tip, warn}` shape already used there:

```javascript
      {
        title: 'Choose when a step runs',
        text: 'A step with dependencies has a "Run this step when" setting. The default waits for every parent to succeed. "All parents finished" runs regardless of outcome, which is how you build a cleanup step. "All parents failed" is how you build an alert branch that only fires when things went wrong.',
        where: 'Sequences tab → step row',
      },
      {
        title: 'Retry transient errors',
        text: 'Set "Retries on error" to re-run a step that errored. Leave it blank to inherit the run settings. Only errors are retried — a failed data comparison is a real result, and running it again cannot change it.',
        tip: 'Retries happen on the step\'s own thread, so a waiting retry never holds up other branches.',
      },
      {
        title: 'Decide what a failure means',
        text: '"If this step fails" controls the blast radius. The default lets dependent steps decide for themselves through their own trigger rules. "Don\'t count this failure" lets a step fail without turning the whole run red. "Stop the whole run" cancels everything still to come.',
        warn: 'Stopping cancels every branch, including ones that had nothing to do with the failed step.',
      },
```

- [ ] **Step 2: Verify**

Run: `npm run build:html && python -m pytest tests/unit -k help -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/help-content.js frontend/index.html
git commit -m "docs: document trigger rules, retry, and failure policy"
```

---

## Task 7: Full verification

- [ ] **Step 1: The behaviour gate**

Run: `python -m pytest tests/unit/test_executor_characterization.py -v`
Expected: 10 passed.

Run: `git log --oneline -- tests/unit/test_executor_characterization.py`
Expected: still exactly one commit (`7793e1b`). More than one means the gate was edited — justify every change or revert it.

- [ ] **Step 2: Whole Python suite**

Run: `python -m pytest tests/unit tests/integration -q`
Expected: PASS. The Phase 3 baseline is 2062 passed / 18 skipped, plus roughly 24 new tests — no failures, no errors.

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

## Phase 3 Done — What Ships

Every step can retry on error with its own budget, or inherit the run's. Retries run on the step's own worker thread, so a waiting retry never holds up a parallel branch, and the attempt count lands in `run_steps` and shows as "try 2/3" in Monitor. Retry now works the same way for every job type, because the reconciliation-only wrapper is gone.

`on_failure` gives three real choices: let dependents decide (the default, unchanged), let a step fail without scoring against the run, or stop everything. The sequence editor finally exposes trigger rules, which shipped API-only in Phase 2.

Phase 4 is preconditions — `time_window`, `weekdays`, `require_run_success` — evaluated once before a run starts, plus the editor panel and the scheduler telemetry path for precondition skips. `phase1_unsupported` disappears with it.
