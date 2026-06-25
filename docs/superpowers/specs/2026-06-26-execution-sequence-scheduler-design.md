# Execution Sequence Scheduler — Design Spec

**Date:** 2026-06-26  
**Status:** Approved

## Problem

The Execution Sequence (`job_sequence`) fires all jobs in a single batch. There is no way to:
- Pause the sequence after a specific job and wait for human review before proceeding
- Gate the next job on the outcome of the previous one (status, mismatch count)
- Insert a time delay between steps

## Solution

Enrich `job_sequence` from `list[str]` to `list[str | SequenceStep]`. Plain strings remain valid and are treated as steps with all defaults, preserving backward compatibility. A new `run_steps` DB table tracks the lifecycle of each step in an active run. The executor becomes a step-by-step loop that respects holds, conditions, and time delays.

---

## 1. Data Model

### `SequenceStep` (Pydantic schema)

```python
class StepCondition(BaseModel):
    require_status: list[str] = ["PASSED"]
    # Statuses that allow the next step to proceed. e.g. ["PASSED", "SLOW"].
    # If the completed job's status is not in this list the sequence is cancelled automatically.
    max_mismatch_count: int | None = None
    # If set and the completed job's total mismatch count exceeds this value,
    # the sequence is cancelled automatically.

class SequenceStep(BaseModel):
    job_name: str
    hold_after: bool = False
    # When True: after this job completes, the sequence pauses and waits for
    # a human to explicitly release it before the next step runs.
    condition: StepCondition | None = None
    # Condition evaluated against this step's result before the NEXT step starts.
    # Evaluated after a hold is released (if hold_after is also True).
    wait_seconds: int = 0
    # Additional delay (in seconds) inserted before THIS step starts,
    # after any hold release and condition check on the previous step.
```

### `run_steps` table (new)

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `run_id` | varchar FK | → `test_runs.run_id` |
| `job_name` | varchar | |
| `step_index` | integer | 0-based position in sequence |
| `status` | varchar | See lifecycle below |
| `hold_after` | boolean | copied from SequenceStep at trigger time |
| `condition` | json | serialized StepCondition, nullable |
| `wait_seconds` | integer | default 0 |
| `held_at` | timestamp | set when status transitions to HELD |
| `released_at` | timestamp | set on release |
| `released_by` | varchar | required on release |
| `release_note` | varchar | required on release |
| `release_action` | varchar | `approve` \| `skip` \| `cancel` |

**Step status lifecycle:**

```
PENDING → RUNNING → PASSED | FAILED | SLOW | ERROR
                          ↓ (if hold_after=True)
                        HELD
                          ↓ (on release)
              APPROVED | SKIPPED | CANCELLED
```

Remaining steps in the sequence are set to `CANCELLED` when:
- A release action of `cancel` is chosen
- A condition gate fails automatically

### `Schedule` model

`job_sequence` column stays JSON. No Alembic column type change is needed — it already stores a JSON array. Existing rows with `["job_a", "job_b"]` deserialize correctly via the union type. New rows can store `[{"job_name": "job_a", "hold_after": true, ...}]`.

---

## 2. API

### Schema changes

`RunTrigger` (in `api/schemas.py`):
```python
job_sequence: list[str | SequenceStep]  # was list[str]
```

`ScheduleCreate` (in `api/routes/schedules.py`):
```python
job_sequence: list[str | SequenceStep]  # was list[str]
```

Both schemas coerce plain strings to `SequenceStep` with defaults at validation time.

### New endpoints (in `api/routes/runs.py`)

**List steps for a run:**
```
GET /api/runs/{run_id}/steps
Response: list[RunStepOut]
```

`RunStepOut` fields: `id`, `run_id`, `job_name`, `step_index`, `status`, `hold_after`, `condition`, `wait_seconds`, `held_at`, `released_at`, `released_by`, `release_note`, `release_action`.

**Release a held step:**
```
POST /api/runs/{run_id}/steps/{step_index}/release
Body:
  action: "approve" | "skip" | "cancel"  (required)
  note: str                               (required, non-empty)
  released_by: str                        (required, non-empty)

Response: RunStepOut (updated step)
Errors:
  404 — run or step not found
  409 — step is not in HELD status
  422 — missing required fields
```

Any authenticated user may call this endpoint. The call is audit-logged.

### SSE stream extension

`GET /api/runs/{run_id}/stream` event payload gains:
```json
{
  "current_step": "orders_reconciliation",
  "held_step": "customers_reconciliation"
}
```
`held_step` is `null` when no step is currently held.

### Webhook events

`HELD` is added as a valid event type in the notification system. Payload:
```json
{
  "run_id": "...",
  "event": "HELD",
  "held_step": "customers_reconciliation",
  "step_index": 1,
  "release_url": "/api/runs/{run_id}/steps/1/release"
}
```

---

## 3. Execution Flow

### `RunExecutor.execute()` — new step loop

Replace the single `TestRunner.run(cases)` call with a sequential step loop:

```
materialize run_steps rows (PENDING) for all steps in sequence
for each step in order:
    1. evaluate previous step's condition (if any)
       → if fails: mark remaining steps CANCELLED, end run
    2. if wait_seconds > 0: sleep
    3. mark step RUNNING, run the job via TestRunner (single job)
    4. mark step with job result status (PASSED/FAILED/SLOW/ERROR)
    5. if hold_after=True:
       a. mark step HELD, set held_at, fire HELD webhook
       b. poll run_steps row every HOLD_POLL_INTERVAL_SECONDS (default: 5)
          until status != HELD
       c. if release_action == "cancel": mark remaining steps CANCELLED, end run
       d. if release_action == "skip": note skip, continue to next step
       e. if release_action == "approve": continue to next step
complete run with aggregated status
```

### Condition evaluation

Evaluated automatically before step N+1 starts (after any hold on step N is resolved):

- `require_status`: step N's job status must be in the list. If not → sequence cancelled.
- `max_mismatch_count`: step N's `value_mismatch_count + missing_in_target_count + missing_in_source_count` must be ≤ this value. If not → sequence cancelled.

Condition failure produces a run status of `CANCELLED` with an error message identifying which condition failed and on which step.

### Execution mode constraint

If any step in the resolved sequence has `hold_after=True` or a non-null `condition`, `execution_mode` is forced to `sequential` for that run. A warning is recorded in the run's config snapshot. Parallel mode cannot interleave held steps.

### Hold polling

The executor polls via a simple DB read. `HOLD_POLL_INTERVAL_SECONDS` defaults to 5, configurable via env var. No long-lived connections or async primitives are required — the executor thread sleeps between polls.

---

## 4. UI

### Launch tab — Jobs sub-tab

Each job in the selected sequence gets an expandable "Step settings" panel (collapsed by default):

- **Hold after this job** (checkbox)
- **Proceed only if status** (multi-select: PASSED, SLOW, FAILED, ERROR — defaults to PASSED only)
- **Max mismatch count** (numeric input, blank = no limit)
- **Wait before next job** (integer seconds, default 0)

When all steps use defaults, the payload sent to `POST /api/runs` remains `list[str]` for simplicity. If any step has non-default settings, all steps are sent as `list[SequenceStep]`.

### Schedules sub-tab

The schedule modal replaces the `job_sequence_raw` comma-separated text field with the same per-job step settings panel used in the Launch tab. Existing schedules with plain string sequences load as steps with all defaults and are editable without data loss.

### Monitor tab

The existing progress bar and current-job indicator are extended with a **step timeline** — a vertical list of steps showing status badges. When a step is `HELD`:

- The row shows an amber "HELD" badge with elapsed hold time (updated via the SSE stream)
- An inline release form appears below the held step:
  - **Action** dropdown: Approve / Skip / Cancel run
  - **Note** text input (required)
  - **Released by** text input (required)
  - **Release** button (disabled until both fields are filled)
- On successful release, the UI optimistically clears the form; the SSE stream drives the next status update

### Webhook UI

The existing webhook event selector gains `HELD` as a selectable event type. No other UI changes needed.

---

## 5. Backward Compatibility

| Scenario | Behaviour |
|---|---|
| Existing schedule with `["job_a", "job_b"]` | Parsed as two SequenceSteps with all defaults. Runs unchanged. |
| Existing `POST /api/runs` with `job_sequence: ["a", "b"]` | Accepted unchanged. |
| Run with no steps having `hold_after=True` | `run_steps` table is still populated (provides step-level progress visibility) but no holds occur. |
| `execution_mode: "parallel"` with holds | Forced to sequential; warning logged. |

---

## 6. Out of Scope

- Role-based or named-approver hold release (any authenticated user with a note is sufficient)
- Partial job execution / checkpointing within a single job
- Resuming a CANCELLED run
- Hold timeout (auto-cancel if not released within N hours) — can be added later
