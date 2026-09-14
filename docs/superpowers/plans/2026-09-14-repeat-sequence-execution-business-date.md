# Repeat Sequence Execution Business Date Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a sequence or selection repeatedly with a date custom variable advanced for each iteration, and optionally apply the same date cursor to scheduled firings.

**Architecture:** Add a shared calendar helper, persist batch metadata, and reuse existing single-launch paths with per-iteration `variable_overrides`. Scheduled runs gain an independent persisted cursor so `skip` weekend policy advances calendar slots without incrementing completed firings.

**Tech Stack:** Python, FastAPI, SQLAlchemy, APScheduler, Alpine.js, pytest, Playwright.

## Global Constraints

- Date variables must already exist as `CustomVariable` rows with `var_type="date"`.
- Literal ISO `YYYY-MM-DD` values flow through existing `resolve_variables` unchanged.
- Ad-hoc batch iterations are sequential, not parallel.
- `batch.iterations` is capped at 200.
- Schedule `skip` policy advances `batch_next_value` without incrementing `firings_completed`.

---

### Task 1: Business Calendar Helper

**Files:**
- Create: `api/services/business_calendar.py`
- Test: `tests/unit/test_business_calendar.py`

**Interfaces:**
- Produces: `step_dates(start: date, count: int, step_days: int, weekend_policy: Literal["skip", "shift", "ignore"]) -> list[date]`.

- [ ] Write failing tests for `ignore`, `shift`, `skip`, weekend start, rollover, and `step_days > 1`.
- [ ] Run `python -m pytest tests/unit/test_business_calendar.py -q` and confirm failures.
- [ ] Implement `step_dates` with validation for count and step days.
- [ ] Re-run `python -m pytest tests/unit/test_business_calendar.py -q`.

### Task 2: Batch Persistence And Schemas

**Files:**
- Modify: `etl_framework/repository/models.py`
- Modify: `etl_framework/repository/database.py`
- Modify: `etl_framework/repository/repository.py`
- Modify: `api/schemas.py`
- Test: `tests/unit/test_repository.py`

**Interfaces:**
- Produces: `RunBatch` ORM model, `TestRun.run_batch_id`, `RunBatchRepository`, `BatchOptions`, `SequenceBatchLaunchRequest`, `JobSelectionBatchLaunchRequest`, `RunBatchOut`.

- [ ] Add failing repository tests for creating a batch, listing member runs, stopping a batch, and stamping member run IDs.
- [ ] Run `python -m pytest tests/unit/test_repository.py -q` and confirm failures.
- [ ] Add ORM model, relationships where useful, and SQLite migration shims.
- [ ] Add repository methods: `create`, `get`, `stop`, `increment_completed`, `complete`, `member_runs`, `set_run_batch_id`.
- [ ] Add Pydantic schemas for batch request and response.
- [ ] Re-run `python -m pytest tests/unit/test_repository.py -q`.

### Task 3: Reusable Launch Helpers And Batch Routes

**Files:**
- Modify: `api/routes/sequences.py`
- Modify: `api/routes/selections.py`
- Create: `api/routes/run_batches.py`
- Modify: `api/main.py`
- Create: `api/services/batch_launch.py`
- Test: `tests/unit/test_sequences_routes.py`
- Test: `tests/unit/test_selections_routes.py`
- Test: `tests/unit/test_batch_launch.py`

**Interfaces:**
- Produces: `_do_launch_sequence(...) -> str`, `_do_launch_selection(...) -> str`, `POST /api/sequences/{id}/launch-batch`, `POST /api/selections/{id}/launch-batch`, `GET /api/run-batches/{id}`, `POST /api/run-batches/{id}/cancel`.

- [ ] Add failing route tests for happy paths and 422s: invalid variable, non-date variable, too many iterations, precondition failure.
- [ ] Add failing batch service tests for sequential order, stop-on-failure, cancel before next iteration, and `run_batch_id` stamping.
- [ ] Extract single-launch helper functions without changing existing `/launch` behavior.
- [ ] Implement date-variable validation through `CustomVariableRepository`.
- [ ] Implement `run_batch(batch_id)` loop with terminal polling.
- [ ] Add run-batch API router and include it in `api/main.py`.
- [ ] Re-run targeted route and batch tests.

### Task 4: Scheduled Batch Cursor

**Files:**
- Modify: `etl_framework/repository/models.py`
- Modify: `etl_framework/repository/database.py`
- Modify: `api/routes/schedules.py`
- Modify: `api/services/scheduler.py`
- Test: `tests/unit/test_scheduler_batch.py`
- Test: existing schedule route tests.

**Interfaces:**
- Produces: scheduled columns `batch_variable_name`, `batch_start_value`, `batch_step_days`, `batch_max_firings`, `batch_weekend_policy`, `batch_next_value`, `firings_completed`.

- [ ] Add failing tests for schedule save validation and `_run_schedule` cursor behavior.
- [ ] Add ORM columns and SQLite migration shims.
- [ ] Extend `ScheduleCreate` and `ScheduleOut` with batch fields.
- [ ] Validate date variable and required weekend policy when scheduling batch mode.
- [ ] Inject computed `variable_overrides` into scheduled `RunTrigger`.
- [ ] Advance cursor on every firing attempt; increment firings only after actual run.
- [ ] Auto-disable and remove scheduler job once `batch_max_firings` is reached.
- [ ] Re-run scheduler batch tests.

### Task 5: Batch UI

**Files:**
- Modify: `frontend/features/launch.js`
- Modify: `frontend/partials/tab-launch.html`
- Modify: `frontend/features/monitor.js` or existing schedule feature module if schedule form lives there.
- Modify: relevant schedule partial.

**Interfaces:**
- Consumes: `/api/variables`, `/api/*/launch-batch`, `/api/run-batches/{id}`, schedule batch fields.

- [ ] Add repeat-execution toggle to the launch tab.
- [ ] Show only date-type custom variables in the picker.
- [ ] Submit to `launch-batch` and render batch progress polling.
- [ ] Add cancel action for batches.
- [ ] Add schedule batch fields and auto-disable summary text.

### Task 6: Verification

**Files:**
- No production edits.

- [ ] Run `python -m pytest tests/unit/test_business_calendar.py tests/unit/test_batch_launch.py tests/unit/test_sequences_routes.py tests/unit/test_selections_routes.py tests/unit/test_scheduler_batch.py tests/unit/test_repository.py -q`.
- [ ] Run relevant e2e smoke if local frontend dependencies are available.
- [ ] Inspect changed files for unrelated modifications.
