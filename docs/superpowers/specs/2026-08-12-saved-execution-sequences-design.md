# Saved Execution Sequences — Design Spec

**Date:** 2026-08-12
**Status:** Approved
**Supersedes in part:** `2026-06-26-execution-sequence-scheduler-design.md` (the linear step
loop and forced-sequential-on-hold rule defined there are replaced in Phase 2)

## Problem

An execution sequence today is not a thing you can save. It lives inline inside a
`JobSelectionVersion.job_sequence` JSON array, so:

- The same sequence cannot be shared by two selections or two schedules — it must be
  retyped, and the copies drift.
- Dependencies live on the **job** (`SavedJob.params.depends_on`), not on the sequence,
  so one job cannot run in two different orders in two different pipelines. Those
  dependencies are also only *validated* against the linear order
  (`RunExecutor._validate_dependencies`), never executed.
- Execution is a strict linear list. Independent branches cannot run concurrently, a
  held step blocks the entire run, and there is no way to express "run this cleanup
  step only if the upstream step failed".
- There is no retry, no per-step failure policy, and no way to gate a whole pipeline on
  a time window or on an upstream run having succeeded.

## Solution

Introduce **Execution Sequence** as a first-class, named, versioned entity with its own
steps, its own sequence-scoped dependency graph, and its own conditions. Job Selections
and Schedules reference a sequence instead of carrying steps inline. Execution becomes a
true DAG: a coordinator loop schedules every step whose dependencies are satisfied, with
per-edge trigger rules, per-step retry and failure policy, and sequence-level
preconditions.

Delivered in four independently shippable phases (§7). Existing inline sequences keep
working unchanged throughout.

---

## 1. Data Model

### New tables

Both mirror the existing `JobSelection` / `JobSelectionVersion` pattern
(`etl_framework/repository/models.py`), so repository code, versioning semantics, and
archival behaviour match what the codebase already does.

**`execution_sequences`** — identity and metadata only.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `name` | String(255) | not null, unique, indexed |
| `description` | Text | not null, default `""` |
| `tags` | JSON | not null, default `[]` |
| `archived` | Boolean | not null, default `false` |
| `created_at` | DateTime(tz) | not null |
| `updated_at` | DateTime(tz) | not null, onupdate |

**`execution_sequence_versions`** — immutable snapshots.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `sequence_id` | Integer FK → `execution_sequences.id` ON DELETE CASCADE | not null, indexed |
| `version_number` | Integer | not null; unique together with `sequence_id` |
| `steps_json` | JSON | not null, default `[]`; list of `SequenceStepRef` |
| `preconditions_json` | JSON | nullable; a single `SequencePrecondition` |
| `defaults_json` | JSON | not null, default `{}`; see below |
| `created_at` | DateTime(tz) | not null |

A version is never mutated. Editing a sequence's steps creates
`version_number + 1`. `PATCH /api/sequences/{id}` touches metadata only.

`defaults_json` accepts exactly four optional keys — `source_env`, `target_env`,
`config_id`, and a partial `run_settings` object. A sequence is environment-agnostic: any
value the caller (ad-hoc launch, selection, or schedule) supplies **wins** over the
sequence default, and defaults are only consulted for keys the caller left unset. This is
what lets one sequence run against dev and prod without duplication.

### Step schema

New Pydantic model in `api/schemas.py`, distinct from the existing `SequenceStep`, which
remains for inline/legacy sequences:

```python
class SequenceStepRef(BaseModel):
    step_id: str                     # stable slug, unique within the sequence
    job_name: str
    depends_on: list[str] = []       # step_ids, never job names
    trigger_rule: Literal["all_success", "all_done", "any_success", "all_failed"] = "all_success"
    hold_after: bool = False
    condition: StepCondition | None = None      # existing schema, unchanged
    wait_seconds: int = 0
    max_retries: int | None = None              # None = inherit RunSettings.max_retries
    retry_delay_seconds: float | None = None    # None = inherit RunSettings.retry_delay_seconds
    on_failure: Literal["stop", "continue", "skip_downstream"] = "skip_downstream"
```

`step_id` is deliberately separate from `job_name`. A DAG may run the same job more than
once — a reconciliation before a load and again after it — so `job_name` is not a unique
key and cannot anchor an edge. The existing linear list sidesteps this by using list
position, which a graph has no equivalent of.

`step_id` is auto-slugged from `job_name` on creation in the UI and remains editable.
Uniqueness within a version is enforced at save time.

### Preconditions

```python
class SequencePrecondition(BaseModel):
    time_window: TimeWindow | None = None        # {start: "HH:MM", end: "HH:MM"}
    weekdays: list[int] | None = None            # 0 = Monday .. 6 = Sunday
    require_run_success: RequireRunSuccess | None = None  # {job_name: str, within_hours: int}
```

`time_window` and `weekdays` are evaluated in the application timezone
(`SettingsRepository.get_timezone()`, the same source APScheduler already uses). A
`time_window` whose `end` is earlier than its `start` wraps past midnight.

`require_run_success` is satisfied when a `TestResult` for `job_name` with a status in
`{PASSED, SLOW}` exists on a run completed within the last `within_hours` hours. It looks
across all runs, not only runs of this sequence.

There is deliberately **no** file-existence precondition. The repo already has a
`freshness` job type; "the file arrived" is expressed as step 1 of the DAG with the rest
of the graph depending on it. That keeps the check visible in `run_steps` with a real
status and duration, instead of hiding it in an invisible gate, and removes a subsystem.

### References

```python
class SequenceRef(BaseModel):
    sequence_id: int
    sequence_version: int | None = None   # None = always resolve to latest
```

- **`JobSelectionVersion`** gains a nullable `sequence_ref` JSON column. Exactly one of
  `job_sequence` (inline, legacy) or `sequence_ref` may be set on a version.
- **`Schedule`** gains nullable `sequence_id` (Integer) and `sequence_version` (Integer);
  `selection_id` becomes nullable. Exactly one of `selection_id` / `sequence_id` must be
  set. Enforced by a Pydantic validator on the request schema and by a guard in
  `ScheduleRepository` so direct repository callers cannot create an invalid row.

A pinned `sequence_version` gives a schedule reproducibility. A null version lets a
selection float to the latest.

### Run snapshot

At trigger time the reference resolves to a concrete version, which is flattened into
the run's `config_snapshot`:

- `config_snapshot["job_sequence"]` — the resolved step list, in the shape history,
  reporting, and difference export already read
- `config_snapshot["sequence"]` — `{id, name, version}` provenance

Runs created before this feature, and runs from inline sequences, keep exactly the shape
they have today.

### Validation on save

All of the following return **422** with a per-step error list:

- duplicate `step_id` within a version
- `depends_on` naming an unknown `step_id`
- `job_name` not matching an enabled `SavedJob`
- any cycle in the dependency graph
- `weekdays` outside `0..6`, or a malformed `time_window`

Cycle and unknown-step checks run again defensively at resolve time.

---

## 2. Execution Semantics

### `run_steps` additions

| Column | Type | Notes |
|---|---|---|
| `step_id` | String(255) | nullable for legacy rows |
| `depends_on` | JSON | nullable; list of step_ids |
| `trigger_rule` | String(20) | not null, default `all_success` |
| `attempt` | Integer | not null, default 0 |
| `max_retries` | Integer | nullable; null = inherit run setting |
| `on_failure` | String(20) | not null, default `skip_downstream` |

`step_index` is retained and now holds the topological display position, so Monitor,
history, and the existing release-by-index endpoint keep working.

### Step statuses

Existing: `PENDING`, `RUNNING`, `PASSED`, `FAILED`, `SLOW`, `ERROR`, `HELD`, `APPROVED`,
`SKIPPED`, `CANCELLED`.

Added: **`BLOCKED`** — the step never ran because an upstream trigger rule or condition
gate said so. Distinct from `CANCELLED` (human cancel, or `on_failure: stop`) and from
`SKIPPED` (a human `skip` release action).

Note that `BLOCKED` already exists as a **run** status, set today when a condition gate stops a
linear sequence. The two levels coexist: a step is `BLOCKED` when its own gate refused it, and a
run is `BLOCKED` when blocking is the most severe thing that happened to it (see aggregation
below).

### Coordinator loop

A single coordinator thread owns scheduling. Jobs execute in a `ThreadPoolExecutor`
bounded by `RunSettings.max_workers`. When the ready-set holds exactly one step, that
step runs inline with no pool involvement — which is the path every chain-shaped
(legacy) sequence takes, end to end.

```
resolve ref -> version -> steps
validate DAG (cycles, unknown deps, unknown jobs) -> fail fast
materialize run_steps rows as PENDING
evaluate preconditions -> on failure: run CANCELLED with reason, no step runs

loop until nothing pending and nothing in flight:
    if cancel_requested:
        stop scheduling, drain in-flight, mark remainder CANCELLED, exit
    for each step whose parents are all terminal:
        evaluate trigger_rule over parent statuses   -> unsatisfied: BLOCKED
        evaluate parents' StepCondition gates        -> failed:      BLOCKED
        honour wait_seconds, then submit
    on step completion:
        record terminal status and attempt
        if retryable and attempt < max_retries: requeue after retry_delay_seconds
        else apply on_failure
        if hold_after: mark HELD, set held_at, fire run.held webhook, move to waiting-set
    re-check waiting-set for releases
    recompute ready-set
complete run with aggregated status
```

Held steps sit in a **waiting-set polled each tick**, not a blocking sleep. This is the
principal payoff of the DAG rewrite: while one branch waits for human review,
independent branches continue running. The rule in the 2026-06-26 spec that forced
`execution_mode` to `sequential` whenever any step had `hold_after=True` is **deleted**.

Poll cadence reuses the existing `HOLD_POLL_INTERVAL_SECONDS` (default 5).

### Trigger rules

Evaluated over the terminal statuses of a step's parents:

| Rule | Fires when |
|---|---|
| `all_success` (default) | every parent reached a terminal status accepted by **this step's** `condition.require_status`, defaulting to `["PASSED"]` when this step has no `condition` |
| `all_done` | every parent is terminal, whatever the outcome |
| `any_success` | at least one parent succeeded |
| `all_failed` | every parent failed — the alerting / cleanup branch |

`SKIPPED` counts as **done** but **not success**. A step with no parents is always ready.

**Condition direction.** A step's `condition` states what that step requires **of its parents**,
checked before it runs. It does not describe the step's own success. This matches the existing
linear executor, where `seq_step.condition` is evaluated against the preceding step's result
(`api/services/run_executor.py`), so no saved sequence or selection changes meaning. The DAG
generalises it in the only sensible way: the condition is evaluated against **every** parent's
result and all must pass.

### `on_failure` vs `trigger_rule`

These govern different scopes and never overlap. `trigger_rule` governs a single
**edge**; `on_failure` governs the **run**:

| `on_failure` | Effect |
|---|---|
| `stop` | Abort the run. Stop scheduling, drain in-flight, mark the remainder `CANCELLED`. |
| `continue` | Run keeps scheduling. Each descendant is decided purely by its own trigger rule. |
| `skip_downstream` (default) | Force every transitive descendant `BLOCKED`, overriding their trigger rules. |

### Retry

A step is retried only when its terminal status is in the inherited
`RunSettings.retry_on` (`error`, `timeout`). `FAILED` — a genuine data mismatch — is
never retried; re-running a mismatch cannot change the outcome and only burns the run
window. Each attempt increments `run_steps.attempt`; the final row records the last
attempt's status.

`max_retries` and `retry_delay_seconds` fall back to the run-level values when null.

### Release actions in a DAG

| Action | Effect |
|---|---|
| `approve` | Step marked `APPROVED`; descendants become eligible. |
| `skip` | Step marked `SKIPPED`; descendants re-evaluate under their trigger rules (skip is done-but-not-success). |
| `cancel` | Abort the whole run, as `on_failure: stop`. |

Release still requires `note` and `released_by`, and is audit-logged.

### Aggregate run status

Precedence: `CANCELLED` > `BLOCKED` > `ERROR` > `FAILED` > `SLOW` > `PASSED`.

`BLOCKED` outranking `ERROR` and `FAILED` looks wrong at first glance and is deliberate: it is
what the linear executor does today. A gate only refuses a step *because* an upstream step
failed, so a blocked run almost always contains a failure too — reporting it as `FAILED` would
hide the fact that the rest of the pipeline never ran, which is the more important thing to
know. Changing this would also silently reclassify historical runs in existing reports.

`BLOCKED` steps are **excluded** from the pass/fail/slow/error counts and reported separately in
the summary, so a deliberately-unfired `all_failed` alert branch does not turn a clean run red.
A run is only `BLOCKED` when at least one step was blocked **and** no step errored or failed.

### SSE and webhooks

`GET /api/runs/{run_id}/stream` keeps `current_step` and `held_step` unchanged and adds:

```json
{ "steps": [{"step_id": "load_orders", "status": "RUNNING", "attempt": 1}] }
```

`run.held` fires as it does today. No new event types.

`execution_mode: "sequential"` now simply pins `max_workers` to 1; the graph is still
walked topologically.

---

## 3. API

### New router — `api/routes/sequences.py`

```
GET    /api/sequences                    list (q, tags, archived)
POST   /api/sequences                    create; also writes version 1
GET    /api/sequences/{id}               metadata + latest version
PATCH  /api/sequences/{id}               metadata only (name, description, tags, archived)
DELETE /api/sequences/{id}               409 if referenced; archiving is the normal path
GET    /api/sequences/{id}/versions      list versions
POST   /api/sequences/{id}/versions      create a new immutable version
GET    /api/sequences/{id}/versions/{n}  one version
GET    /api/sequences/{id}/usage         referencing selections and schedules
POST   /api/sequences/validate           dry-run DAG validation, no persistence
```

Create, new-version, and archive are audit-logged through the existing `AuditService`,
matching schedules.

`/usage` is the acknowledged cost of JSON storage: schedules resolve cheaply because
`sequence_id` is a real indexed column, while selections require a JSON scan of
`sequence_ref`. Acceptable at this table size, and it is what backs the `DELETE` guard.

### Single resolution point — `api/services/sequence_resolver.py`

```python
def resolve(db: Session, ref: SequenceRef) -> ResolvedSequence:
    """ResolvedSequence(steps, preconditions, defaults, meta)"""
```

Every caller — ad-hoc launch, selection launch, and the scheduler — resolves through
this one function. A null `sequence_version` resolves to the latest version at that
moment; a pinned version resolves exactly. Nothing else in the codebase learns how
sequences are stored.

### Changed schemas

**Schedules** (`api/routes/schedules.py`) — `ScheduleCreate` and `ScheduleOut` gain
nullable `sequence_id` and `sequence_version`; `selection_id` becomes nullable. A
model validator enforces exactly one target. `_resolve_and_validate` branches on target
type. `api/services/scheduler.py::_run_schedule` builds its `RunTrigger` from either
target, calling the resolver in the sequence case.

**Selections** (`api/routes/selections.py`) — the version-create schema gains
`sequence_ref`, with exactly-one-of validation against inline `job_sequence`. The launch
handler resolves before building the trigger.

**Runs** (`api/routes/runs.py`, `api/schemas.py`) — `RunTrigger` gains
`sequence_ref: SequenceRef | None`. `RunStepOut` gains `step_id`, `depends_on`,
`trigger_rule`, `attempt`, and `on_failure`.

### New release route

```
POST /api/runs/{run_id}/steps/by-id/{step_id}/release
```

Same body and error codes as the existing index-based route, which stays working —
`step_index` remains unique within a run.

### Targeted cleanup

`_validate_env_requirements` currently lives in `api/routes/selections.py` and
`api/routes/schedules.py` imports it from that route module. With sequences as a third
consumer, it moves to `api/services/job_env_validation.py`, with a re-export left in
`selections.py` so existing imports keep working.

---

## 4. UI

New top-level **Sequences** tab: `frontend/partials/tab-sequences.html` plus
`frontend/features/sequences.js`, kept as its own module in line with the frontend
modularization plan already in `docs/superpowers/plans/`.

### Sequence list

Name, description, tags, latest version number, usage count, archived toggle. Search by
name and filter by tag.

### Sequence editor

A vertical step list. Each row carries:

- job picker (enabled `SavedJob`s)
- `step_id` — auto-slugged from the job name, editable, uniqueness validated live
- **Runs after** — multi-select over the other step_ids in this sequence
- trigger-rule select
- a collapsed **Advanced** panel reusing the existing Launch step-settings markup for
  `condition` and `hold_after` and `wait_seconds`, plus `max_retries`,
  `retry_delay_seconds`, and `on_failure`

Validation calls `POST /api/sequences/validate` on change and renders cycle and
duplicate-id errors inline against the offending rows. Saving writes a new version.

A sequence-level **Preconditions** panel above the list holds `time_window`, `weekdays`,
and `require_run_success`.

### Graph preview

Read-only, laid out by topological level as a CSS grid with inline SVG connectors. No
graph library — the project vendors no frontend dependencies, and inline SVG stays
within the existing CSP.

### Selections and schedules

Selections gain a **Use saved sequence** toggle that swaps the inline step list for a
sequence + version picker, where version offers a "latest" option. The schedule modal
gains a target-type radio — Job Selection | Execution Sequence — followed by the
corresponding picker.

### Monitor

The existing step timeline groups steps by topological level, adds a `BLOCKED` badge,
and shows an attempt counter (`try 2/3`) on retried steps. Hold rows and the inline
release form are unchanged.

### Help

`frontend/help-content.js` gains a Sequences section covering step ids, dependencies,
trigger rules, and the retry/failure policy.

---

## 5. Error Handling

| Situation | Behaviour |
|---|---|
| A `job_name` in the sequence no longer exists or is disabled | Resolve-time failure. The run fails immediately, naming every missing job. No step runs — never a half-executed DAG. |
| A schedule's pinned `sequence_version` is gone | `record_scheduler_event(..., "skipped", "ERROR", ...)`, matching the existing missing-selection-version path in `api/services/scheduler.py`. |
| A sequence is archived while a schedule references it | Scheduler skips with a telemetry event. Archiving does not delete. |
| A precondition fails | Run ends `CANCELLED` with the failing precondition in the error message; scheduler records a `skipped` event. |
| A cycle somehow reaches the executor | Resolve-time validation raises before materializing `run_steps`. |
| `DELETE` on a referenced sequence | 409 listing the referencing selections and schedules. |

---

## 6. Testing

**Unit**
- DAG validation: cycles, duplicate `step_id`, unknown dependency, unknown job
- resolver: pinned version vs latest, missing version, archived sequence
- trigger-rule truth table across all four rules and all parent-status combinations
- `on_failure` propagation, including `skip_downstream` overriding a descendant's
  `all_done` rule
- retry eligibility: `ERROR` retries, `FAILED` does not, inheritance of null values

**Characterization (gate for Phase 2)**
Golden tests over the *current* linear executor — step statuses, `run_steps` rows,
hold/release transitions, cancel drain, SSE payloads — written and passing **before**
the `DagExecutor` swap, then re-run against the new executor unchanged.

**Integration**
- a hold in one branch while an independent branch continues to completion
- cancel drains in-flight steps and marks the remainder `CANCELLED`
- a sequence-targeted schedule fires and produces a run with correct `config_snapshot`
  provenance

**E2E (Playwright)**
Build a two-branch sequence, attach it to a schedule, trigger it, and verify the Monitor
timeline shows both branches and their statuses.

---

## 7. Phases

Each phase is independently shippable.

**P1 — Saved sequences, end to end.** Tables and migration, versioning, CRUD API,
`sequence_resolver`, Sequences UI, and selection + schedule wiring. Execution runs
through the **existing linear executor** in topological order, single-threaded. Users can
build, save, reuse, and schedule sequences with zero executor risk.

`SequenceStepRef` carries its full field set from P1 so the stored shape never changes,
but a field is only accepted once the phase that honours it ships. Until then the API
returns **422** for any non-default `trigger_rule`, `max_retries`, `retry_delay_seconds`,
or `on_failure` (P2/P3), and for any non-null `preconditions_json` (P4). The editor hides
those controls behind the same phase gate. This prevents a saved sequence from silently
promising behaviour the executor does not yet implement.

P1 failure semantics are the linear executor's existing semantics: a step whose
`StepCondition` gate fails cancels the remaining steps. Branch-aware blocking arrives
with the DAG executor in P2.

**P2 — DAG executor.** Characterization tests first, then the `DagExecutor` swap:
parallel branches, trigger rules, `BLOCKED`, non-blocking holds, `run_steps` columns,
SSE `steps` payload, Monitor timeline.

**P3 — Retry and failure policy.** Per-step `max_retries` / `retry_delay_seconds` /
`on_failure`, with run-level inheritance and the attempt counter in the UI.

**P4 — Preconditions.** `time_window`, `weekdays`, `require_run_success`, plus the
editor panel and the scheduler telemetry path for precondition skips.

---

## 8. Backward Compatibility

| Scenario | Behaviour |
|---|---|
| Selection with an inline `job_sequence` | Unchanged. `sequence_ref` is null. |
| Schedule targeting `selection_id` | Unchanged. `sequence_id` is null. |
| `POST /api/runs` with `job_sequence: ["a", "b"]` | Accepted unchanged. |
| Existing `run_steps` rows | New columns are nullable or defaulted; Monitor and history read them as before. |
| Existing release-by-index endpoint | Still works; `step_index` remains unique per run. |
| `SavedJob.params.depends_on` | Still validated for inline sequences by `RunExecutor._validate_dependencies`. Inside a saved sequence, the sequence-scoped `depends_on` is authoritative and the job-level list is ignored. |
| Chain-shaped saved sequences under the DAG executor | Ready-set size is always 1, so they run inline with no pool involvement — same order, same serialization as today. |

---

## 9. Out of Scope

- Visual drag-and-drop DAG canvas (the read-only preview plus dependency pickers is the
  agreed editing model)
- Dynamic fan-out — a step expanding into N parallel instances at runtime
- Cross-sequence dependencies, and nesting one sequence inside another as a step
- Resuming a `CANCELLED` run from the point of failure
- Changing hold-timeout behaviour. It already exists and is **kept as-is**: `HOLD_TIMEOUT_SECONDS`
  (default 86400) auto-cancels a step left held too long. The DAG executor preserves it per step.
- Role-based or named-approver hold release; any authenticated user with a note suffices
- File-existence preconditions, replaced by using the existing `freshness` job type as a
  DAG step
