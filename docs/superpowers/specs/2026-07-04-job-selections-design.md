# Job Selections — Design Spec

**Date:** 2026-07-04
**Status:** Approved

## Problem

Today, launching a job sequence (ad-hoc or scheduled) requires re-picking the job list from scratch every time — there is no way to save a named, reusable list of jobs. Separately, `ScheduledRun` bakes a fixed `source_env`/`target_env` pair in at creation time, so there's no way to:

- Run the same set of jobs against just **one** environment now, and compare it against a run from a **different** environment launched later.
- Save a job selection once and reuse it across multiple ad-hoc launches and schedules, targeting different environments each time.
- Know which version of a job list a given historical run or schedule actually used, once that list has been edited.

## Solution

Introduce a new, environment-agnostic **Job Selection** entity: a named, versioned list of jobs (`job_sequence` + `run_settings`) that is chosen independently of environment and cron. Selections are launched on-demand against a single environment (or an environment pair, if the contained job types require it), and `ScheduledRun` is refactored to reference a selection (pinned to a specific version) instead of embedding its own job list. Comparison of two runs launched from the same selection at different times/environments reuses the existing `/api/compare/mismatch-diff` endpoint unchanged — no new comparison logic is introduced.

---

## 1. Data Model

### `JobSelection` (new table)

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `name` | varchar | unique |
| `description` | varchar | nullable |
| `tags` | json | list[str], nullable |
| `archived` | boolean | default false; soft-delete |
| `created_at` | timestamp | |
| `updated_at` | timestamp | bumped whenever a new version is created |

### `JobSelectionVersion` (new table)

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `selection_id` | integer FK | → `job_selections.id` |
| `version_number` | integer | 1-based, increments per selection |
| `job_sequence` | json | `list[str \| SequenceStep]`, same shape as today's `RunTrigger.job_sequence` |
| `run_settings_json` | json | same shape as today's per-run settings |
| `created_at` | timestamp | |

A `JobSelection`'s **latest version** is `max(version_number)` among its `JobSelectionVersion` rows. Editing `job_sequence` or `run_settings_json` always creates a new `JobSelectionVersion` row rather than mutating an existing one; editing only `name`/`description`/`tags` on `JobSelection` does not create a new version.

### `TestRun` (existing table — additive change)

Two new nullable columns:

| Column | Type | Notes |
|---|---|---|
| `selection_id` | integer FK | nullable; set when the run was launched from a selection |
| `selection_version` | integer | nullable; the version number used |

Ad-hoc runs not launched from a selection leave both columns null, matching today's behavior unchanged. These columns are what let the UI later ask "show me all runs launched from selection X" without touching `config_snapshot`.

### `ScheduledRun` (existing table — refactor)

- **Remove:** the embedded `job_sequence` and `run_settings_json` columns.
- **Add:** `selection_id` (FK, required) and `selection_version` (integer, required — pinned at schedule-creation/edit time, not floating).
- `source_env` and `target_env` are unchanged, except `target_env` becomes **nullable**, to support scheduling a selection containing only single-environment job types (e.g. `bo_report`, `freshness`, `profile`) against one environment.

### Migration

A one-time Alembic data migration:
1. For each existing `ScheduledRun` row, create a `JobSelection` named `"<schedule name> (migrated)"` with a `JobSelectionVersion` (version 1) populated from that row's current `job_sequence`/`run_settings_json`.
2. Rewrite the `ScheduledRun` row to reference `selection_id` = the new selection's id, `selection_version` = 1.
3. Drop the old `job_sequence`/`run_settings_json` columns from `scheduled_runs` after backfill.

---

## 2. API

New router `api/routes/selections.py`, mounted at `/api/selections`.

```
POST   /api/selections
  Body: { name, description?, tags?, job_sequence, run_settings? }
  Creates JobSelection + JobSelectionVersion(version_number=1).

GET    /api/selections
  Lists selections with latest version's job count and tags.

GET    /api/selections/{id}
  Full detail: metadata + all versions (summary: version_number, job count, created_at).

GET    /api/selections/{id}/versions/{version_number}
  Full job_sequence + run_settings for one specific version.

PUT    /api/selections/{id}
  Body may include job_sequence/run_settings (→ creates one new version
  combining whatever of those two fields were provided, defaulting any
  omitted field to the current latest version's value)
  and/or name/description/tags (→ updated in place, no new version).

DELETE /api/selections/{id}
  Archives (sets archived=true). 409 if any non-disabled ScheduledRun
  still references this selection.

GET    /api/selections/{id}/runs
  Lists TestRuns where selection_id == {id}, across all environments
  and time, newest first. Used to populate the compare-pairing UI.

POST   /api/selections/{id}/launch
  Body: { source_env, target_env?, version? }  (version defaults to latest)
  Resolves job_sequence/run_settings from the specified JobSelectionVersion,
  then reuses the existing run-trigger creation path (same internal function
  used by POST /api/runs) to create a TestRun, setting selection_id and
  selection_version on it.
```

No changes to `/api/compare/*`. The `/api/selections/{id}/runs` list feeds run_ids into the existing `/api/compare/mismatch-diff` endpoint from the frontend, unchanged.

### `ScheduleCreate`/`ScheduleOut` (in `api/routes/schedules.py`)

Replace `job_sequence`/`run_settings_json` fields with `selection_id` (required) and `selection_version` (required, defaults to the selection's latest version number at creation time — the value is then stored, not re-resolved). `target_env` becomes optional.

---

## 3. Scheduling Execution

`api/services/scheduler.py`'s APScheduler callback, at fire time:
1. Loads the `ScheduledRun`'s `selection_id` + `selection_version` (pinned).
2. Loads that exact `JobSelectionVersion`'s `job_sequence`/`run_settings`.
3. Calls the same internal launch function used by `/api/selections/{id}/launch`, passing the schedule's `source_env`/`target_env`.

Because the version is pinned, editing a selection after a schedule is created does not change what that schedule runs. To pick up a new version, the schedule itself must be edited to reference it.

**Validation caveat:** making `target_env` nullable means a schedule (or ad-hoc launch) could reference a selection containing a job type that structurally requires two environments (e.g. `reconciliation`) while only one environment was supplied. This must surface as a clear validation error at launch time (existing per-job-type environment requirement checks already exist in the job execution path; this design only requires the error message clearly name the offending job and job type — no new validation engine is introduced).

---

## 4. Comparison Workflow

No new comparison logic. The frontend's selection detail view calls `GET /api/selections/{id}/runs`, lets the user check exactly two runs (which may be against different environments and launched at different times), and navigates to the existing Compare → Mismatch Diff sub-tab pre-filled with those two `run_id`s, hitting `/api/compare/mismatch-diff` exactly as it works today for any two runs.

---

## 5. Frontend

`frontend/index.html` + `app.js` (Alpine.js), new "Job Selections" sub-tab alongside the existing Jobs/Schedules sub-tabs under the Launch tab:

- **Create/edit selection:** reuses the existing job-picker pattern already used for ad-hoc launches — `selectedJobs[]`, `toggleJobWithShift()`, `selectAllJobs()`/`selectNoneJobs()`, and the search filter (`jobSearchQuery`/`filteredJobList`) — persisting the result server-side via `POST`/`PUT /api/selections` instead of discarding it after one run.
- **Selection detail view:** shows version history (version number, created_at, job count) and the run list from `GET /api/selections/{id}/runs`, each row showing environment, launch time, and status. Two checkboxes + a "Compare" button navigate to Compare → Mismatch Diff pre-filled with the two selected `run_id`s.
- **Launch modal:** environment picker(s) (source, optional target) + version picker (default: latest) → `POST /api/selections/{id}/launch`.
- **Schedule modal:** the existing inline job-sequence builder is replaced with a "Job Selection" + version picker; `target_env` becomes optional in the form.

---

## 6. Testing Plan

Following the existing e2e coverage pattern (see `test: add end-to-end coverage for api source_type through /compare/column-stats`):

- `JobSelection`/`JobSelectionVersion` CRUD: create, edit job_sequence (creates version 2, version 1 retained), edit metadata only (no new version), archive (blocked while a non-disabled schedule references it).
- Launch flow: `POST /selections/{id}/launch` creates a `TestRun` with `selection_id`/`selection_version` set; defaults to latest version when `version` omitted.
- Version pinning: create a schedule pinned to version 1, edit the selection to create version 2, confirm the schedule still fires version 1's job list on its next run.
- Single-env schedule: a schedule with `target_env=null` referencing a selection containing only single-env-compatible job types succeeds; the same with a `reconciliation` job type produces a clear validation error naming the job.
- Migration: a fixture `ScheduledRun` with an embedded `job_sequence` migrates into a `JobSelection` + version 1 and continues to fire an equivalent run afterward.
- End-to-end: launch a selection against environment A, launch again against environment B, call `GET /api/selections/{id}/runs`, pick the two returned `run_id`s, and confirm `/api/compare/mismatch-diff` returns the expected diff — mirroring the existing column-stats e2e fixture style.

---

## 7. Out of Scope

- Fan-out launches (one launch targeting multiple environments at once) — each launch targets one environment (pair), per the "one environment per launch" decision.
- Auto-pairing or baseline designation for comparison — pairing is always a manual pick of two runs from the selection's run history.
- A dedicated, selection-aware comparison view (e.g. a per-job pass/fail matrix) — comparison reuses the existing generic mismatch-diff endpoint/UI as-is.
- Role-based permissions on who may create/edit/launch selections.
- Hard-deleting a `JobSelection` or any of its versions (archive/soft-delete only).
