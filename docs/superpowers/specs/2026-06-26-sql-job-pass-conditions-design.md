# SQL Job Pass Conditions

**Date:** 2026-06-26
**Status:** Approved

## Problem

Users need to define explicit pass/fail conditions on SQL reconciliation jobs. Currently:

- **DQ rules** (`JobDefinition.rules`) check the *source dataframe* for data quality (null checks, regex, row count bounds, etc.) and fail the job if violated.
- **Step conditions** (`StepCondition` on `SequenceStep`) gate whether the *next step in a run sequence* proceeds, based on the previous step's reconciliation outcome.

Neither covers the case where a user wants to declare: "this job only passes if its reconciliation result meets these criteria" — conditions that are stored with the job definition and evaluated every time the job runs.

In addition, the step-level conditions are limited to `require_status` and `max_mismatch_count` (total mismatches). Users need finer-grained step gates: per-mismatch-type limits and row count thresholds.

## Goals

1. Let users declare **pass conditions on a job definition** — evaluated post-reconciliation, causing the job to fail with traceable violations if not met.
2. **Extend step-level conditions** (set at launch time per step) with the same granular fields.
3. Support four condition types: row count thresholds, per-type mismatch limits, required result status, and custom SQL assertion.

## Non-Goals

- No changes to existing DQ rules — they continue to operate on the source dataframe.
- No changes to the DB schema — `pass_condition` is serialized into the existing `params` JSON column alongside `rules` and `depends_on`.
- `pass_sql` is not available on step conditions — step conditions evaluate a `ReconciliationResult` in memory; running a new SQL query per step gate is out of scope.

---

## Data Model (`api/schemas.py`)

### New: `PassCondition`

```python
class PassCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_row_count: int | None = None
    max_row_count: int | None = None
    max_value_mismatches: int | None = None
    max_missing_in_target: int | None = None
    max_missing_in_source: int | None = None
    require_status: list[str] = Field(default_factory=list)
    pass_sql: str | None = None
    pass_sql_mode: Literal["rows_mean_pass", "rows_mean_fail"] = "rows_mean_pass"
```

All fields are optional. An empty `PassCondition` is a no-op. Fields not supplied are not checked.

**`pass_sql` semantics:**
- Executed against the source engine after reconciliation.
- `rows_mean_pass` (default): the query must return ≥ 1 row for the condition to pass. Use for "assert good state exists."
- `rows_mean_fail`: any rows returned means failure. Use for "assert no bad rows exist."
- If the SQL raises an exception it is treated as a violation.

### Change: `JobDefinition`

Add one field:

```python
pass_condition: PassCondition | None = None
```

No change to existing validator logic. No required fields.

### Change: `StepCondition`

Existing fields stay. New granular fields added:

```python
class StepCondition(BaseModel):
    # existing
    require_status: list[str] = Field(default_factory=lambda: ["PASSED"])
    max_mismatch_count: int | None = None      # total of all mismatch types
    # new
    min_row_count: int | None = None
    max_row_count: int | None = None
    max_value_mismatches: int | None = None
    max_missing_in_target: int | None = None
    max_missing_in_source: int | None = None
```

`max_mismatch_count` (total) and the new per-type fields can coexist. Both are checked; either failing blocks the next step.

---

## Backend (`api/services/run_executor.py`)

### New method: `_apply_pass_condition()`

Called in `run_job()` after `engine.reconcile()` and `_apply_dq_rules()`:

```python
result = engine.reconcile(...)
if job.rules:
    result = self._apply_dq_rules(result, job, source_engine)
if job.pass_condition:
    result = self._apply_pass_condition(result, job, source_engine)
return result
```

The method collects all violations as strings, converts each to a `MismatchRecord` with `mismatch_type="pass_condition_violation"`, appends them to `result.mismatches`, increments `value_mismatch_count`, and sets `status = FAILED`. This is the same pattern used by `_apply_dq_rules()`, so violations appear in the mismatch detail view alongside DQ violations.

If no violations: result is returned unchanged.

The `require_status` check in `PassCondition` runs on the status *after* DQ rules have been applied, not the raw reconciliation status. If DQ rules already flipped the job to `FAILED`, a `require_status: ["PASSED"]` pass condition will also see `FAILED` and add its own violation.

### Extended: `_check_condition()`

The five new `StepCondition` fields are checked against `prev_result` after the existing checks. A `False` return cancels remaining steps (existing behaviour). No change to the cancellation flow.

---

## API (`api/routes/jobs.py`)

`pass_condition` is stored in the `params` JSON column, extracted/injected by the two private helpers:

**`_job_to_data()`** — add to the `params` dict before write:
```python
if job.pass_condition:
    params["pass_condition"] = job.pass_condition.model_dump(exclude_none=True)
```

**`_job_to_schema()`** — pop from params and reconstruct before returning:
```python
pass_condition_raw = params.pop("pass_condition", None)
pass_condition = PassCondition.model_validate(pass_condition_raw) if pass_condition_raw else None
# … pass pass_condition=pass_condition to JobDefinition(…)
```

No route handlers change. `GET /api/jobs`, `POST /api/jobs`, `PUT /api/jobs/{name}`, and `POST /api/jobs/import` all work through these helpers.

---

## Frontend

### Job modal — new "Conditions" tab

Added to `jobModalTabs`:
```js
{ id: 'conditions', label: 'Conditions' }
```

Tab content: a set of labeled numeric inputs and one text input for `require_status`, visible for all job types. `pass_sql` textarea and `pass_sql_mode` select are only shown when `jobModal.job_type === 'reconciliation'`.

New `jobModal` fields (initialised in both `openNewJobModal()` and `openEditJobModal()`):

| Field | Type | Default |
|---|---|---|
| `pass_min_row_count` | string | `''` |
| `pass_max_row_count` | string | `''` |
| `pass_max_value_mismatches` | string | `''` |
| `pass_max_missing_in_target` | string | `''` |
| `pass_max_missing_in_source` | string | `''` |
| `pass_require_status` | string | `''` (comma-sep) |
| `pass_sql` | string | `''` |
| `pass_sql_mode` | string | `'rows_mean_pass'` |

`openEditJobModal()` reads `job.pass_condition` and pre-fills these fields when editing an existing job.

`saveJob()` assembles a `pass_condition` object (omitting blank fields) and includes it in the request body as `pass_condition: object | null`.

### Step settings panel — new fields

`getStepCfg()` default object extended with five new fields:
```js
min_row_count: '', max_row_count: '',
max_value_mismatches: '', max_missing_in_target: '', max_missing_in_source: '',
```

Five numeric inputs added to the expandable step settings panel in the launch tab, beneath the existing `require_status` input.

`_buildJobSequence()` includes the new fields in the `condition` object when non-empty, alongside the existing `require_status` and `max_mismatch_count`.

---

## Error Surface

Condition violations appear as `MismatchRecord` rows with `mismatch_type = "pass_condition_violation"` in the mismatch detail view. The `key_values` field contains the human-readable violation string (e.g. `{"pass_condition": "row_count 0 < min 1"}`). This makes them visible in the existing history/detail UI without any new UI work.

---

## Files Changed

| File | Change |
|---|---|
| `api/schemas.py` | Add `PassCondition`; extend `JobDefinition` and `StepCondition` |
| `api/services/run_executor.py` | Add `_apply_pass_condition()`; extend `_check_condition()` |
| `api/routes/jobs.py` | Extend `_job_to_data()` and `_job_to_schema()` |
| `frontend/app.js` | Add Conditions tab fields; extend step settings; update `saveJob()`, `openNewJobModal()`, `openEditJobModal()`, `getStepCfg()`, `_buildJobSequence()` |
| `frontend/index.html` | Add Conditions tab panel; add five new step settings inputs |
