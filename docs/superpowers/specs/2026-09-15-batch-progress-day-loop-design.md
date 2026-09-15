# Batch Run Progress — Job Sequence, Day, and Loop Visibility — Design

**Goal:** For a multi-day batch run (`RunBatch`), show — through both the API and the web GUI — which job-sequence iterations ("loops") are complete, currently running, or still to be executed, and which business day each loop corresponds to. Within any given loop, show the underlying job sequence's own progress (which jobs in that day's run are done / running / pending), not just the loop's overall pass/fail status.

**Tech stack:** Python (FastAPI, SQLAlchemy), Alpine.js frontend, pytest / Playwright.

**Context:** `RunBatch` (introduced in [2026-09-14-repeat-sequence-execution-business-date-design.md](2026-09-14-repeat-sequence-execution-business-date-design.md)) already runs a job sequence once per computed business date, `iterations` times, stepping by `step_days` and skipping/shifting weekends per `weekend_policy`. Each iteration's `TestRun` already has full job-sequence step tracking (`RunStep`, exposed via `GET /api/runs/{run_id}/steps` and rendered in the Monitor tab's step timeline). What's missing is the loop-level view: which day/iteration each member run belongs to, and a persistent, expandable way to see all of that together. Today `RunBatchMemberOut` only carries `run_id/status/started_at/completed_at`, has no entries for iterations that haven't launched yet, and the only GUI surface for it (`batchProgress` in `frontend/features/launch.js`) is in-memory only, lost on navigation or reload.

---

## 1. API changes

### 1.1 `RunBatchMemberOut` — add loop and day

```python
class RunBatchMemberOut(BaseModel):
    run_id: str | None = None        # None for an iteration not yet launched
    status: str                       # existing TestRun status, or "PENDING" if not yet launched
    started_at: datetime | None = None
    completed_at: datetime | None = None
    iteration_index: int              # 1-based loop number, matches batch.current_iteration semantics
    business_date: str                # ISO date, the value substituted for batch.variable_name that iteration
```

### 1.2 `batch_out()` — compute the full iteration list, not just launched ones

`api/services/batch_launch.py` already imports `step_dates` and calls it inside the background `run_batch()` loop. `batch_out()` gains the same call, so every response is self-consistent with no new DB columns or migration:

```python
def batch_out(db: Session, batch) -> RunBatchOut:
    runs = RunBatchRepository(db).member_runs(batch.batch_id)   # launched-so-far, ordered by TestRun.id
    dates = step_dates(
        date.fromisoformat(batch.start_value), batch.iterations, batch.step_days, batch.weekend_policy,
    )
    members = [
        RunBatchMemberOut(
            run_id=r.run_id, status=r.status, started_at=r.started_at, completed_at=r.completed_at,
            iteration_index=i + 1, business_date=dates[i].isoformat(),
        )
        for i, r in enumerate(runs)
    ]
    # Iterations not yet launched: synthetic PENDING rows so the full N-loop
    # picture (done / running / to-be-executed) is visible in one response.
    for i in range(len(runs), batch.iterations):
        members.append(RunBatchMemberOut(
            run_id=None, status="PENDING", iteration_index=i + 1, business_date=dates[i].isoformat(),
        ))
    return RunBatchOut(..., runs=members)
```

This relies on `RunBatchRepository.member_runs()` being ordered by `TestRun.id` ascending (already true), which matches launch order, which matches iteration order — `run_batch()`'s control flow only ever appends one run per iteration in sequence and stops the whole batch on a launch failure, so there is no gap/reorder case to handle.

### 1.3 Job-sequence detail per loop — no new endpoint

The existing `GET /api/runs/{run_id}/steps` already returns that run's `RunStep` list (job name, step index, status). A loop row's `run_id` (once launched) is sufficient for the frontend to fetch this lazily on expand. A `PENDING` (not-yet-launched) loop has no `run_id` and therefore no steps to show — the GUI renders it as a plain "not started" row with no expand affordance.

### 1.4 Backward compatibility

`RunBatchMemberOut.run_id` becomes optional (was implicitly always-present). Existing consumers that only read `status`/`started_at`/`completed_at` are unaffected; any caller that assumed `run_id` is always a string needs a null check — grep confirms the only current consumer is `frontend/features/launch.js`'s `batchProgress`, which is being updated as part of this change (1.2 below).

---

## 2. GUI changes

### 2.1 Loop list replaces the scalar progress line

`frontend/partials/tab-launch.html`'s `batch-progress-panel` currently renders one line: `"${completed} / ${iterations} complete - ${current_value}"`. This becomes a list, one row per `RunBatchOut.runs[]` entry:

- Loop number (`iteration_index`), business date (`business_date`), status badge — reusing `stepStatusBadgeClass`-style mapping already used for `TestRun`/`RunStep` statuses, plus a `PENDING`-for-not-yet-launched treatment (grey, no timestamps).
- A chevron/expand toggle on rows that have a `run_id`. Expanding calls the existing `loadRunSteps(run_id)` (already in `runStepsCache`) and renders the same job-sequence badge list the Monitor tab uses — no new rendering logic, just reuse.
- The batch-level summary (`completed / iterations`, overall `status`, Cancel button) stays as a header above the list, unchanged.

### 2.2 Persistence across reload/navigation

`batchProgress`/`batchPollingTimer` today are pure Alpine component state — gone on reload. This change adds:

- On successful batch launch, push `batch_id` onto a small `localStorage` list (`etl_recent_batches`, capped at, say, 10 entries, newest first).
- On Launch-tab mount, if the URL has `?batch_id=...`, or if `etl_recent_batches` has an entry whose batch is not yet terminal, call `GET /api/run-batches/{batch_id}` to rehydrate `batchProgress` and resume `pollBatch(batchId)` — same function used today after a live trigger, just also invoked on mount.
- A small "recent batches" picker (dropdown or chip list) sourced from `etl_recent_batches`, letting the user switch which batch's progress panel is showing without re-triggering one. Terminal batches (`COMPLETED`/`STOPPED`/`FAILED`) are still selectable (read-only progress view) but stop polling.

No new list-batches API endpoint — deliberately out of scope; the picker is seeded from what this browser has launched, not a server-side batch registry. (If a cross-session/cross-device batch browser turns out to be needed later, that's a `GET /api/run-batches` list endpoint — a natural, additive follow-up, not part of this change.)

### 2.3 Out of scope

- Single-run (non-batch) sequence progress: unchanged, already covered by the Monitor tab's step timeline.
- A dedicated top-level "Batch Progress" nav view: explicitly deferred in favor of extending the existing Launch-tab panel.
- Editing/re-triggering a specific failed loop from this panel: not requested; batch-level `stop_on_failure` and `POST /api/run-batches/{batch_id}/cancel` already exist and are unchanged.

---

## 3. Data flow

1. User launches a batch (existing flow, unchanged) → `batch_id` returned → pushed to `localStorage`, `pollBatch(batch_id)` starts.
2. Each `pollBatch` tick: `GET /api/run-batches/{batch_id}` → `batch_out()` computes the full `iterations`-length `runs[]` (launched + synthetic pending) → panel re-renders the loop list.
3. User expands a launched loop row → `GET /api/runs/{run_id}/steps` (existing, cached in `runStepsCache`) → job-sequence badges render under that row.
4. User reloads the page or returns later → mount hook reads `?batch_id=` or `etl_recent_batches` → re-fetches `GET /api/run-batches/{batch_id}` → panel and polling resume exactly as in step 2.

## 4. Error handling

- `GET /api/run-batches/{batch_id}` returns 404 (batch purged, or bad `localStorage`/URL value) → panel shows "Batch not found — it may have been deleted" instead of a blank/erroring panel, and that entry is dropped from `etl_recent_batches`.
- `GET /api/runs/{run_id}/steps` fails on expand → matches existing `loadRunSteps` behavior (catches to `[]`), row shows "steps unavailable" rather than an empty silent gap.
- A `PENDING` loop has no `run_id` — the GUI must not attempt to call `/steps` for it (guard on `run_id != null`, not just `status !== 'PENDING'`, so a future status value doesn't accidentally trigger a bad call).

## 5. Testing

- `api/services/batch_launch.py`: unit test for `batch_out()`'s zip-and-pad logic — asserts `iteration_index`/`business_date` line up correctly for a partially-completed batch (some launched, some pending), and that a `stop_on_failure` batch that stopped early still reports the remaining iterations as `PENDING` (not silently dropped).
- Playwright: extend the existing batch-launch e2e spec to assert the loop list renders with correct day/loop numbers and statuses, that expanding a launched loop shows its job steps, and that reloading the page (or navigating away and back) with a still-running batch re-shows its progress without re-triggering a launch.
