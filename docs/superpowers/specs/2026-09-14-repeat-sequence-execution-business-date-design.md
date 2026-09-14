# Repeat Sequence Execution With Incrementing Runtime Variable — Design

**Goal:** Run a sequence (or selection) N times back-to-back — or on a recurring schedule for N future firings — with a chosen date-type custom variable (e.g. `business_date`, wired through to `$G_BUSINESS_DATE` on `ds_job` steps) advanced by a configurable step each time, with a choice of how weekends affect the date and the iteration count. Two surfaces share one date-stepping core: an **ad-hoc batch launch** (fires now) and a **Schedule extension** (fires on the existing cron cadence, auto-disabling after N firings).

**Tech stack:** Python (FastAPI, SQLAlchemy, APScheduler), Alpine.js frontend, pytest / Playwright.

---

## 1. Why this reuses the existing variable pipeline unchanged

Custom variables already support a `date` type resolved once per run via `resolve_variables(db, config_id, overrides)` (`api/services/variable_resolution.py`), and every launch path (`RunTrigger`, `SequenceLaunchRequest`, `JobSelectionLaunchRequest`) already accepts `variable_overrides: dict[str, str]` that flows straight into it. `resolve_date_expression` passes a literal `YYYY-MM-DD` through unchanged (only `today`/`today±N` get evaluated) — so a batch iteration just needs to compute a concrete ISO date and hand it in as `variable_overrides={variable_name: computed_date}`. **No changes to `variable_resolution.py` or `substitute_in_job` are needed.** This also means the chosen variable must already exist as a `CustomVariable` with `var_type="date"` — the batch feature is deliberately not introducing a second way to define variables.

## 2. Shared calendar helper

New module `api/services/business_calendar.py`:

```python
def step_dates(start: date, count: int, step_days: int, weekend_policy: Literal["skip", "shift", "ignore"]) -> list[date]:
    """Returns exactly `count` dates (except see note below for 'skip')."""
```

- `ignore`: `[start + i*step_days for i in range(count)]` — every calendar day counts, weekends included.
- `shift`: compute `start + i*step_days` for each `i`, then if it lands on Saturday/Sunday, advance it to the next Monday. Always returns exactly `count` dates.
- `skip`: walk forward one `step_days` at a time from `start`, only keeping dates that aren't Saturday/Sunday, until `count` dates are collected. Weekend dates are consumed by the walk but don't count toward `count` — so "10 iterations, skip weekends" always means 10 actual runs, not 10 calendar slots minus weekends.

Unit-testable in isolation (no DB), covering month/year rollover and a `start` that itself falls on a weekend.

## 3. Ad-hoc batch launch

### 3.1 Data model — new table

```python
class RunBatch(Base):
    __tablename__ = "run_batches"

    id = Column(Integer, primary_key=True, index=True)
    sequence_id = Column(Integer, nullable=True, index=True)
    sequence_version = Column(Integer, nullable=True)
    selection_id = Column(Integer, nullable=True, index=True)
    selection_version = Column(Integer, nullable=True)
    variable_name = Column(String(100), nullable=False)
    start_value = Column(String(20), nullable=False)   # ISO date
    step_days = Column(Integer, nullable=False, default=1)
    iterations = Column(Integer, nullable=False)
    weekend_policy = Column(String(10), nullable=False)  # skip | shift | ignore
    stop_on_failure = Column(Boolean, nullable=False, default=False)
    status = Column(String(20), nullable=False, default="RUNNING")  # RUNNING | COMPLETED | STOPPED
    completed_iterations = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
```

`test_runs` gains `run_batch_id = Column(Integer, nullable=True, index=True)` so a batch's member runs are queryable (`WHERE run_batch_id = ...`) and the existing runs list/detail views need no structural change — a batch run is a completely normal `TestRun`.

### 3.2 API

`SequenceBatchLaunchRequest` = `SequenceLaunchRequest` (unchanged) + :

```python
class BatchOptions(BaseModel):
    variable_name: str
    start_value: date | None = None   # None = today
    step_days: int = Field(default=1, ge=1)
    iterations: int = Field(ge=1, le=200)
    weekend_policy: Literal["skip", "shift", "ignore"] = "skip"
    stop_on_failure: bool = False

class SequenceBatchLaunchRequest(SequenceLaunchRequest):
    batch: BatchOptions
```

`POST /api/sequences/{sequence_id}/launch-batch` and `POST /api/selections/{selection_id}/launch-batch` (mirrors the existing `.../launch` endpoints, same 404/422 validation for the target and its preconditions — preconditions are checked **once**, before the batch starts, not re-checked per iteration, since a batch is one explicit user action).

Reuses the *existing* single-launch code path: `launch_sequence`'s body (`api/routes/sequences.py:221`) is factored into a `_do_launch_sequence(db, sequence_id, body, request) -> str` helper the plain `/launch` route calls with `background_tasks=None`-equivalent semantics (returns the run_id synchronously, execution is still dispatched the same way); `/launch-batch` calls it once per computed date, overriding only `variable_overrides[batch.variable_name]`. Same refactor applies symmetrically to `launch_selection`.

`POST /api/run-batches/{batch_id}/cancel` → sets `status="STOPPED"`; the in-flight background loop checks this before starting its next iteration (already-started run finishes normally, matching how run cancel already only affects future steps, not the current one).

`GET /api/run-batches/{batch_id}` → batch row + its member runs (id, status, variable value used, started/completed).

### 3.3 Execution

`api/services/batch_launch.py`:

```python
def run_batch(batch_id: int) -> None:
    """Dispatched via background_tasks; owns its own DB session per iteration,
    same pattern as _run_schedule in scheduler.py."""
```

Loop, one iteration at a time (never parallel — mirrors the scheduler's one-selection-one-active-run discipline and keeps resource usage predictable):

1. Compute this iteration's date from `business_calendar.step_dates`.
2. Re-check `status != "STOPPED"` (cancel check) before launching.
3. Call the shared single-launch helper with `variable_overrides={variable_name: date.isoformat()}`, get back a `run_id`, stamp `run_batch_id` on it.
4. Poll `RunRepository.get_run(run_id).status` until terminal (same poll interval as `HOLD_POLL_INTERVAL_SECONDS`), then bump `completed_iterations`.
5. If terminal status is failure-like (`ERROR`, `BLOCKED`, or `FAILED`/nonzero `failed`) and `stop_on_failure`, set `status="STOPPED"` and end the loop.
6. After all iterations (or a stop), set `status="COMPLETED"` or leave `"STOPPED"`.

### 3.4 Frontend

Launch tab (`frontend/features/launch.js` + `tab-launch.html`): a "Repeat execution" toggle next to the existing single-launch button. When on: a variable picker (only `CustomVariable`s with `var_type="date"`), start date (defaults to today), iterations count, weekend-policy select, stop-on-failure checkbox. Submitting posts to `launch-batch` instead of `launch` and opens a batch progress panel (poll `GET /api/run-batches/{id}` every few seconds: "3 / 10 complete — 2026-09-17 running", per-iteration status list, Cancel button).

## 4. Schedule extension

### 4.1 Data model — new columns on `scheduled_runs`

```python
batch_variable_name = Column(String(100), nullable=True)
batch_start_value = Column(String(20), nullable=True)    # ISO date; None = "today" at first firing
batch_step_days = Column(Integer, nullable=False, default=1)
batch_max_firings = Column(Integer, nullable=True)       # None = unlimited (no auto-disable)
batch_weekend_policy = Column(String(10), nullable=True) # skip | shift | ignore
batch_next_value = Column(String(20), nullable=True)     # cursor: candidate date for the NEXT firing
firings_completed = Column(Integer, nullable=False, default=0)  # count of firings that actually RAN
```

`batch_next_value` is a cursor, not something re-derived from `firings_completed` each time — see §4.3 for why: firings_completed alone can't reconstruct "where we are" once a `skip` has happened, because a skipped firing consumes a calendar slot without producing a run.

All nullable/defaulted — an existing schedule with none of these set behaves exactly as today (no variable override injected, no auto-disable). This is independent of the existing `SequencePrecondition.weekdays` gate: that decides whether the cron fires *at all* on a given calendar day; `batch_weekend_policy` decides what happens to the *date value* it injects, regardless of what day it's actually running on.

### 4.2 `ScheduleCreate`/`ScheduleOut` (`api/routes/schedules.py`)

Gain the same fields (all optional). `check_one_target`-style validator: whenever `batch_variable_name` is set, `batch_weekend_policy` is **required** (no silent default — matches the ad-hoc batch's explicit `weekend_policy`); `batch_max_firings`/`batch_step_days` remain optional (unlimited / daily by default). `batch_variable_name` must name an existing `CustomVariable` with `var_type="date"` (validated at create/update time, same 422 style as the existing cron validation).

### 4.3 `_run_schedule()` (`api/services/scheduler.py`)

Why a plain `start + step_days * firings_completed` formula doesn't work: `firings_completed` only counts firings that actually **ran**. A `skip`-policy firing that lands on a weekend consumes a calendar slot but produces no run, so it must never be counted — but the *next* firing still needs to try the *next* candidate date, not recompute the same one. Deriving the date purely from `firings_completed` would make a skipped Saturday's candidate reappear on Sunday's firing (same offset, same date, skipped again — stuck). A persisted cursor (`batch_next_value`) avoids this: it advances by `step_days` on *every* firing attempt, run or skipped, independent of the run counter.

Right before building `trigger = RunTrigger(...)`:

```python
variable_overrides = {}
if sched.batch_variable_name:
    candidate = date.fromisoformat(sched.batch_next_value or sched.batch_start_value or date.today().isoformat())
    policy = sched.batch_weekend_policy  # required whenever batch_variable_name is set (§4.2)
    next_cursor = candidate + timedelta(days=sched.batch_step_days)

    if policy == "skip" and candidate.weekday() >= 5:
        repo.update(schedule_id, {"batch_next_value": next_cursor.isoformat()})  # advance cursor, don't run
        record_scheduler_event(db, sched, "skipped", "CANCELLED", error_summary=f"{candidate} is a weekend")
        return   # firings_completed NOT incremented — matches ad-hoc "skip" semantics

    computed = candidate
    if policy == "shift" and computed.weekday() >= 5:
        computed += timedelta(days=7 - computed.weekday())  # forward to Monday
    variable_overrides = {sched.batch_variable_name: computed.isoformat()}
```

`trigger = RunTrigger(..., variable_overrides=variable_overrides)` (currently always `{}` for schedules — this is the one behavioral gap this feature closes). After a successful run, alongside the existing `repo.touch(schedule_id, last_run_at=...)` call: `repo.update(schedule_id, {"batch_next_value": next_cursor.isoformat(), "firings_completed": sched.firings_completed + 1})` (`ScheduleRepository.update` already takes a plain field dict — no signature change needed). If `batch_max_firings` is set and the new count reaches it, also include `"enabled": False` in that same `update()` call and call `scheduler.remove_job(schedule_id)` (auto-disable, same effect as a user toggling the schedule off).

`batch_next_value` starts unset; the first firing falls back to `batch_start_value` (or today), matching the ad-hoc batch's own default.

### 4.4 Frontend

Schedule create/edit form gains the same batch fields as the ad-hoc launch panel (shown when a sequence/selection target with a date-type custom variable is available), plus, once `batch_max_firings` is set, a "will auto-disable after N runs (currently at M)" line reading `firings_completed`/`batch_max_firings`.

## 5. Error handling

- `batch.iterations` capped at 200 (422 above that) — a runaway batch shouldn't be able to queue thousands of runs by typo.
- `variable_name` not found, or found but not `var_type="date"` → 422 at batch-launch/schedule-save time, not discovered mid-batch.
- A single iteration erroring (job/config problem) behaves exactly like any other run failure; the batch's `stop_on_failure` flag is the only thing deciding whether the loop continues.
- Batch launch requested while the target's precondition gate fails → 422 up front (same as a normal `/launch` 422), no batch row created.

## 6. Testing

- `tests/unit/test_business_calendar.py`: `step_dates` for all three policies, including weekend-start, month/year rollover, `step_days > 1`.
- `tests/unit/test_batch_launch.py`: sequential iteration order, `stop_on_failure` halting the loop, cancel mid-batch, `run_batch_id` stamped on member runs.
- `tests/unit/test_sequences_routes.py` / `test_selections_routes.py`: new `launch-batch` route — happy path, 422s (bad variable, iterations cap, precondition failure).
- `tests/unit/test_scheduler_batch.py`: `_run_schedule` computes the right date per firing, `skip` policy doesn't increment `firings_completed`, auto-disable fires exactly at `batch_max_firings`.
- `tests/e2e/`: Launch tab batch UI — configure a 3-iteration batch, observe 3 runs complete with distinct dates in their `config_snapshot["variables"]`.

## 7. Out of scope (explicitly deferred)

- Parallel/concurrent batch iterations (v1 is strictly sequential).
- A batch stepping more than one variable at once (only one date variable per batch).
- Editing a running batch's remaining iteration count/policy mid-flight (cancel-and-relaunch only).
- Non-date step types (e.g. incrementing a numeric `batch_id` variable) — this feature is scoped to date-type variables and business-day semantics specifically.
