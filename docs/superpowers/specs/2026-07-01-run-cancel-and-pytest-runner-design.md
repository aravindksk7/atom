# Run Cancellation & Pytest Suite Runner — Design Spec

**Date:** 2026-07-01
**Status:** Approved

---

## Problem

Two gaps in the current system:

1. **ETL runs cannot be cancelled externally.** Once a `RunExecutor` starts, the only cancellation path is a "cancel" release action on a `HELD` step. There is no API endpoint to kill an actively running job sequence.

2. **Pytest test suite runs are invisible.** Running `pytest` is a local CLI operation — no status, no progress, no way to cancel from the app. There is demand for pytest runs to appear in the existing runs system with live streaming and a cancel option.

---

## Goals

- Add `POST /runs/{run_id}/cancel` to request cooperative cancellation of any active ETL run.
- Add `POST /runs/test-suite` to trigger a pytest subprocess run that appears in the existing runs table with live progress via the existing SSE stream.
- Cancel works for both ETL and pytest runs through the same endpoint.
- No changes to the SSE stream endpoint or the runs table schema beyond one new column.

---

## Non-Goals

- Hard/immediate kill of a mid-step ETL job. Cancellation is cooperative: the current step finishes, then the run stops.
- Parallel pytest execution (`pytest-xdist`).
- Pytest run history persistence beyond what the existing `Run` model already stores.

---

## Architecture

### Shared cancel mechanism

A single `cancel_requested` boolean column on the `Run` model acts as the cancellation signal. Both the ETL executor and the pytest executor poll this column between units of work.

```
POST /runs/{run_id}/cancel
        │
        ▼
RunRepository.request_cancel()   ← sets cancel_requested = True in DB
        │
        ▼
Executor loop checks is_cancel_requested() after each step/test
        │
        ▼
cancel_remaining() + status → CANCELLED
```

### Pytest runner

A new `PytestRunExecutor` class is a standalone executor (not a subclass of `RunExecutor`) with the same `execute()` call convention used by `BackgroundTasks`. It runs in the background thread, writes progress to the DB, and reuses the existing SSE stream endpoint for live updates.

---

## DB Change

**One new column** on the `runs` table:

```python
# etl_framework/repository/models.py  (Run model)
cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
```

Default `False` — safe for existing rows with no migration risk.

---

## Repository Changes

**`RunRepository`** gains two methods:

```python
def request_cancel(self, run_id: str) -> bool:
    """Set cancel_requested = True. Returns False if run not found or already terminal."""
    run = self.get_run(run_id)
    if run is None or run.status in _TERMINAL_STATUSES:
        return False
    run.cancel_requested = True
    self._db.commit()
    return True

def is_cancel_requested(self, run_id: str) -> bool:
    """Re-fetches from DB to bypass identity-map cache."""
    self._db.expire_all()
    run = self.get_run(run_id)
    return bool(run and run.cancel_requested)
```

`_TERMINAL_STATUSES = {"PASSED", "FAILED", "SLOW", "ERROR", "COMPLETED", "CANCELLED"}` — currently defined as `_TERMINAL` in `api/routes/runs.py`. Will be extracted to `etl_framework/repository/models.py` and imported from there by both the routes and the repository methods.

---

## API Changes

### Cancel endpoint

```
POST /runs/{run_id}/cancel
```

**Response 202:**
```json
{ "run_id": "...", "cancel_requested": true }
```

- Returns 404 if run not found.
- Returns 202 with `cancel_requested: false` if run is already in a terminal state (idempotent — not an error).
- Does not wait for the run to actually stop; the caller polls `/runs/{run_id}/status` or the SSE stream.

### Pytest trigger endpoint

```
POST /runs/test-suite
```

**Request body (`TestSuiteTrigger`):**
```json
{
  "pytest_args": ["tests/unit/", "-k", "test_runner"]
}
```

**Response 202** — same `RunStatusOut` shape as an ETL run trigger:
```json
{ "run_id": "...", "status": "PENDING", "run_type": "test_suite" }
```

The run immediately appears in the runs table and the SSE stream works without any frontend changes.

---

## ETL Executor Change

**File:** `api/services/run_executor.py`

One check inserted in `RunExecutor.execute()` after each step's outcome is written:

```python
# after: step_repo.update_status(self._run_id, i, job_outcome)
if self._run_repo.is_cancel_requested(self._run_id):
    step_repo.cancel_remaining(self._run_id, from_index=i + 1)
    cancelled = True
    break
```

The existing `cancelled` branch already handles writing `CANCELLED` status and firing webhooks — no further changes needed there.

---

## Pytest Executor

**New file:** `api/services/pytest_runner.py`

```python
class PytestRunExecutor:
    def __init__(self, db: Session, run_id: str, pytest_args: list[str]) -> None: ...

    def execute(self) -> None:
        self._run_repo.update_run_status(self._run_id, "RUNNING", ...)
        cmd = ["python", "-m", "pytest", "--tb=short", "-v", "--no-header"] + self._pytest_args
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        for line in process.stdout:
            self._parse_line(line)
            if self._run_repo.is_cancel_requested(self._run_id):
                process.terminate()
                self._run_repo.update_run_status(self._run_id, "CANCELLED", ...)
                return

        exit_code = process.wait()
        self._finalize(exit_code)
```

### Output parsing

Pytest verbose output format:

```
collected 42 items
tests/unit/test_foo.py::test_bar PASSED    [ 12%]
tests/unit/test_baz.py::test_qux FAILED   [ 25%]
```

| Pattern | Action |
|---------|--------|
| `collected N items` | Write `total_tests = N` to DB |
| Line ends with `PASSED` | Increment `passed` |
| Line ends with `FAILED` | Increment `failed` |
| Line ends with `ERROR` | Increment `error` |

DB writes are batched — update once every 5 parsed test lines to avoid hammering the DB on large suites.

### Terminal status mapping

| Subprocess exit code | Run status |
|----------------------|------------|
| 0 (all tests pass) | `PASSED` |
| 1 (tests ran, some failed) | `COMPLETED` |
| Cancelled | `CANCELLED` |
| 2, 3, 4, 5 (collection error, usage error, etc.) | `ERROR` |

`COMPLETED` is used for "suite ran to completion but had failures" to distinguish from ETL `FAILED` semantics.

---

## Tests

### `tests/unit/test_run_cancel.py`

- `POST /runs/{run_id}/cancel` → 202, flag set in DB
- Cancel on non-existent run → 404
- Cancel on terminal run → 202, `cancel_requested: false` (idempotent)
- `RunExecutor` stops after current step when flag is set (mocked repo, verify `cancel_remaining` called with correct `from_index`)

### `tests/unit/test_pytest_runner.py`

- `collected N items` line sets `total_tests = N`
- `PASSED`/`FAILED`/`ERROR` lines increment correct counters
- Exit 0 → `PASSED`, exit 1 → `COMPLETED`, exit 2 → `ERROR`
- `process.terminate()` called and status set to `CANCELLED` when `cancel_requested` is true

### `tests/integration/test_cancel_flow.py`

Mirrors the `test_hold_polling.py` pattern (shared temp-file SQLite DB, two threads):

1. Start `RunExecutor` in a thread with a 3-step sequence (each step is a fast mock job)
2. Wait for first step to reach `RUNNING`
3. Fire `request_cancel()` from the main thread
4. Wait for run to terminate
5. Assert:
   - Run status is `CANCELLED`
   - Steps after the cancelled point have status `CANCELLED`
   - The step that was running when cancel fired has a terminal status (not `RUNNING`)

---

## File Map

| File | Change |
|------|--------|
| `etl_framework/repository/models.py` | Add `cancel_requested` column to `Run` |
| `etl_framework/repository/repository.py` | Add `request_cancel()`, `is_cancel_requested()` to `RunRepository` |
| `api/schemas.py` | Add `TestSuiteTrigger` schema; extract `_TERMINAL_STATUSES` constant |
| `api/services/run_executor.py` | Add cancel check after each step |
| `api/services/pytest_runner.py` | **New** — `PytestRunExecutor` |
| `api/routes/runs.py` | Add `POST /{run_id}/cancel` and `POST /test-suite` endpoints |
| `tests/unit/test_run_cancel.py` | **New** |
| `tests/unit/test_pytest_runner.py` | **New** |
| `tests/integration/test_cancel_flow.py` | **New** |
