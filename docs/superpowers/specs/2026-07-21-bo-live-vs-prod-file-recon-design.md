# SAP BO live-QA vs prod-file reconciliation — design

## Problem

SAP BO reports are pulled live from QA. Reconciliation against prod uses an
already-downloaded prod file (no live prod access). The ad-hoc Compare tab
(BO Report) already supports this per-side (Source A=Live, Source B=Upload).
The gap is in **scheduled/saved jobs** (`SavedJob` / `JobDefinition`): there is
no mode to pull one side live from SAP BO and diff it against a static prod
file inside a saved, schedulable job. This also blocks the Reconcile
(dual-env launch) tab, since it only launches job sequences — if no job type
supports this mix, the tab can't either.

## Approach

Extend the existing `reconciliation` job type with a third `source_mode`,
rather than adding a new job type.

Today `reconciliation` jobs support `source_mode`: `sql` | `files`. The
`files` mode already has generic machinery for loading tabular sources
(`_load_job_file_frame`, `_has_file_source`, `_job_file_value`) and diffing
them via `FrameEngine` + `_run_reconciliation_job`, including base64-upload
storage inside `SavedJob.params` (a JSON column — persists with the job, no
separate storage system).

Add `source_mode = "bo_live"`:

- **Source**: pulled live on every run via `BORestClient`, reusing the exact
  live-pull code already in `RunExecutor._build_case_bo_report`
  (`config_id` / `doc_id` / `report_id` / `format` from `job.params`).
- **Target**: a file, using the existing generic file-param contract
  (`target_file_path` or `target_file_content_b64` + `target_file_name`) —
  the same fields `files` mode already uses. Upload happens once when the
  job is created/edited; the content is stored in `SavedJob.params` and
  reused on every scheduled run (matches "per-run upload only" for ad-hoc
  compares, but persists for scheduled jobs since it lives in the job
  definition).

This keeps one diffing code path (`_run_reconciliation_job` + `FrameEngine`)
and only varies where the source dataframe comes from. It also means the
Reconcile tab needs no changes: once a job supports `bo_live`, including it
in a job sequence launched from that tab "just works."

## Components

1. **`api/schemas.py` — `JobDefinition.validate_reconciliation_contract`**
   Add a `bo_live` branch under `job_type == "reconciliation"`:
   - Require BO params: `report_id` (doc id) and `bo_report_id` (report id),
     matching the existing `bo_report` job type's param naming (kept as-is
     for consistency, despite the confusing names).
   - Require a target file via `_has_job_file_source(self.params, "target")`
     / `_validate_job_file_source(self.params, "target")` (same helpers
     `files` mode uses).
   - `key_columns` stays optional, same as `files` mode (RunExecutor infers
     a shared ID column or falls back to positional matching).

2. **`api/services/run_executor.py`**
   - `_uses_file_sources` (or the `_build_case` dispatch directly) gains a
     check for `job.params.get("source_mode") == "bo_live"`.
   - New `_build_case_bo_live_recon(job)`:
     - Guarded by `self._settings.use_live_connections`, like `bo_report`,
       `automic_job`, `api_reconciliation`.
     - Pulls the source dataframe live via `BORestClient` (same
       authenticate/download_report/logout/read_tabular sequence as
       `_build_case_bo_report`).
     - Loads the target dataframe via the existing
       `_load_job_file_frame(job, "target")`.
     - Resolves key columns via `resolve_key_columns` (same as
       `_build_case_file_reconciliation`).
     - Diffs via `_run_reconciliation_job` using `FrameEngine` for both
       sides.
     - Sets `target_file_name` on the result via `_job_file_name(job,
       "target")` (source has no file name — it's a live pull).

3. **`frontend/partials/tab-launch.html` — job editor**
   - Add `"Live BO Report"` as a third option in the "Input Source" select
     (`jobModal.source_mode`), shown for `job_type === 'reconciliation'`
     only (not `freshness`/`schema_snapshot`/`profile` — those stay
     `sql`/`files`).
   - When `source_mode === 'bo_live'`: show the BO doc/report/format fields
     (reuse the existing `bo_report` job type's fields: BO Document ID, BO
     Report/Page ID, Format) plus a BO config selector (reuse pattern from
     BO Report compare tab's config select).
   - Target file input: reuse the existing Target File Path field, and add
     an Upload toggle next to it (mirrors the Live/Path/Upload pill pattern
     already used in the ad-hoc BO Report compare tab) so the user can
     either point at a server path or upload a file that gets stored as
     `target_file_content_b64` in the job's params.

4. **Provenance / run history**
   - No new fields needed. `ReconciliationResult.source_file_name` stays
     `None` (live pull) and `target_file_name` is populated from the
     uploaded/path file name — same fields `files` mode already populates,
     already surfaced in run history/reports.

5. **BO Report ad-hoc Compare tab**
   - No functional change (already supports Live + Upload independently per
     side). Add a short inline hint clarifying that Live = pull now, Upload
     = use an already-downloaded file (e.g. a prod snapshot), since this
     capability wasn't discoverable.

## Error handling

- Missing target file for a `bo_live` job: same `ValueError` message pattern
  as `files` mode — `"file-backed reconciliation jobs require source and
  target files"` (adapted: `bo_live` jobs require a target file — source is
  always live).
- Missing BO params (`report_id`/`bo_report_id`): same pattern as the
  existing `bo_report` validator — `"require 'report_id' in params"`.
- Live BO pull failures (auth, network, missing report): surface through
  the existing `_add_error_result` / run ERROR status path, unchanged.

## Testing

- Unit: `JobDefinition` validator — `bo_live` requires BO params and a
  target file; rejects when either missing.
- Unit: `RunExecutor._build_case_bo_live_recon` — mock `BORestClient`,
  assert it diffs the live-pulled frame against a file-loaded frame and
  produces a `ReconciliationResult` with the correct `target_file_name`.
- E2E: extend `tests/e2e/08b-compare-reconciliation.spec.ts` (or a new spec)
  covering: create a job with `source_mode=bo_live`, save it, verify the
  target file (path or upload) round-trips, and a scheduled/launched run
  produces a real diff (not the previous `bo_report` type's always-PASSED
  smoke check).

## Out of scope

- No new "prod snapshot library" UI — uploads stay per-job (this job's
  `params`), not a shared/reusable file store across jobs.
- No changes to the Reconcile (dual-env launch) tab UI — it inherits this
  capability automatically once the job type supports it.
- No changes to the `bo_report` job type's existing (smoke-check) behavior —
  `bo_live` is additive, reached only via the new `source_mode` value.
