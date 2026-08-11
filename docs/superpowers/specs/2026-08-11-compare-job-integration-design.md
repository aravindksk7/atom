# Wiring Compare into Job, Job Catalog, and Job Selections

Date: 2026-08-11

## Problem

The Compare tab (`frontend/partials/tab-compare.html`, `frontend/features/compare.js`,
`api/routes/compare.py`, `api/services/compare_service.py`) already supports BO report,
reconciliation, and multi-file diff comparisons, but only as a standalone, fully ad-hoc
surface. A user configuring or reviewing a saved Job (in the Job Catalog or its edit
modal) or a Job Selection has no way to launch a compare using that job's own saved
configuration, or to compare two of its own past runs, without leaving to the Compare
tab and re-entering everything by hand.

The only existing bridge between these areas is Job Selections' "History" modal
(`tab-launch.html:1308-1333`), which lets a user pick two runs from a selection's run
history and jump into the Compare tab's Mismatch Diff sub-tab
(`compareSelectedRuns()`, `launch.js:1174-1188`). That bridge is hardcoded to
mismatch-diff and does not generalize to BO report, reconciliation, or multi-file
compare.

## Scope

Add Compare entry points to three surfaces, each gated to the job types a compare type
actually applies to (`bo_report`, and `reconciliation` — including its `multi_file`
source mode):

1. **Job Catalog row** — a Compare button alongside the existing Gate/Edit/Del actions,
   for ad-hoc (prefilled from saved config) or run-vs-run compare.
2. **New/Edit Job modal** — an inline Compare section that compares using the
   in-progress (unsaved) form state, so a user can validate a job's compare behavior
   before saving.
3. **Job Selections** — extend the existing History→Compare bridge so, in addition to
   mismatch-diff, a user can launch BO/reconciliation/multi-file compare between two
   picked runs for a chosen job within that selection.

Job rows whose `job_type` is not `bo_report` or `reconciliation` (e.g. `bo_job`,
`ds_job`, `automic_job`, `dbt_artifact`, `freshness`, `schema_snapshot`, `profile`,
`cross_job_assertion`, `api_reconciliation`) show no Compare action — there is no
compare type to launch them into.

Both ad-hoc (prefilled from saved/in-progress config, source-agnostic) and run-vs-run
(reference a past run as a compare source) modes are in scope for all three compare
types, including multi-file — see "Multi-file run-reference" below for why that
needed new backend work.

## Backend changes

### BO report: run-reference source mode

A `bo_report` job's live pull already persists exactly one artifact per run
(`RunExecutor._build_case_bo_live_reconciliation` → `_persist_run_data_artifact` →
`TestResult.data_artifact_path`), because it is a single-source job. This makes a past
BO report run directly reusable as a Compare source.

- `SourceConfig` (`api/schemas.py:798-824`) gains `source_type: Literal["live", "path",
  "upload", "api", "run"]` and a `run_id: str | None` field. The validator requires
  `run_id` (and reuses the existing `config_id`-less path) when `source_type == "run"`.
  `doc_id`/`report_id` are not required for `"run"` — the job name identifies which
  `TestResult` to read.
- `CompareService.run_bo_comparison` (`compare_service.py:139`) resolves a `"run"`
  source by looking up `TestResult` where `run_id` and `query_name` (the job name)
  match, then reads `data_artifact_path` as a frame — the same resolution
  `_load_recon_source`'s `stored_run_id` branch already does
  (`compare_service.py:599-614`), factored into a small shared helper
  (`resolve_result_artifact(repo, run_id, job_name) -> DataFrame | None`) usable by
  both BO and recon-file resolution.
- Errors: 404 if no matching `TestResult`, 422 if the result has no
  `data_artifact_path` (e.g. the referenced job wasn't actually a single-source pull).

### Reconciliation: no backend change needed

`ReconFileCompareRequest.stored_run_id` (`schemas.py:876-899`) already handles
two-sided reconciliation runs by falling back to a per-test stats dict
(`compare_service.py:606-614`) when `load_row_diffable_frame` finds no single
artifact. This already covers reconciliation run-vs-run; only frontend wiring is
needed (§ Job Catalog / Job Selections below).

### Multi-file: run-reference source mode (new artifact persistence)

A multi-file job (`RunExecutor._build_case_multi_file_reconciliation`,
`run_executor.py:657-802`) is two-sided per file pair — each pair's `source_df` and
`target_df` are read into memory, compared, and discarded
(`_make_pair_case.run_pair`, `run_executor.py:729-770`); nothing is persisted to
disk today, consistent with the existing artifact-storage design's stance that
two-sided pulls don't get a `data_artifact_path` because it would be ambiguous which
side to record (`docs/superpowers/specs/2026-08-03-api-response-artifact-storage-design.md`).

To support multi-file run-vs-run compare, this adds explicit per-pair artifact
persistence:

- In `run_pair()`, after `source_df`/`target_df` are read, write each to
  `UPLOAD_ROOT/<run_id>/<job_name>/pair_<i>/{source,target}.csv` (same root and
  retention sweep as existing run-scoped upload artifacts — no new cleanup code
  needed). Persisted on a best-effort basis (a pair whose own compare errors still
  gets its source/target written, so the failure is inspectable).
- `FileMappingManifestWriter` (already writes
  `logs/file_mapping_manifest_<run_id>_<job_name>.json` with the resolved mapping and
  pairs, `run_executor.py:705-707`) gains a second write, after all pairs finish, that
  records each pair's persisted artifact paths alongside its existing pair metadata.
- `MultiFileCompareRequest` (`schemas.py:902-915`) gains optional `run_id: str | None`
  and `job_name: str | None`, mutually exclusive with `file_mapping` (validator
  enforces exactly one of the two is set). When `run_id`/`job_name` are given,
  `CompareService.run_multi_file_compare` reads the manifest for that run+job and
  builds an explicit-pairs file mapping (`kind: "explicit_pairs"`, pointing at the
  persisted local CSVs) that flows through the existing multi-file compare pipeline
  unchanged.

### New endpoint: job run history

`GET /api/jobs/{name}/runs` (new handler in `api/routes/jobs.py`) — queries
`TestResult` where `query_name == name`, joined to `TestRun`, ordered by
`TestRun.started_at` descending, default limit 20. Returns run_id, status,
started_at, completed_at. This powers the run-vs-run picker on the Job Catalog row,
the Job modal, and is reused (filtered to a selection's own run history, which
already has its own endpoint) conceptually by the Selections bridge.

## Frontend changes

### Shared prefill helper

A new helper, `openCompareForJob(job, opts)` (added to `frontend/features/launch.js`
near the existing `compareSelectedRuns()`), centralizes the navigate-and-drive
pattern already used by that function:

- Determines `compareSubTab` from `job.job_type` / `job.params.source_mode`
  (`bo` for `bo_report`, `multi_file` for `reconciliation` with
  `source_mode: "multi_file"`, `recon` otherwise).
- Builds Source A from either the job's saved `params` (ad-hoc) or
  `{source_type: "run", run_id}` / `{run_id, job_name}` (run-reference), per `opts`.
- Sets `currentView = 'compare'` and the resolved sub-tab, same as
  `compareSelectedRuns()` does today for mismatch-diff.

This helper is the single integration point used by all three surfaces below, so job
type → sub-tab mapping and prefill logic exist in one place.

### Job Catalog row

Compare button added to the row's trailing action cluster
(`tab-launch.html:270-278`), following the existing convention (`btn-secondary btn-sm
text-xs`, `data-testid="'job-row-' + job.name + '-compare-btn'"`), rendered only when
`job.job_type` is `bo_report` or `reconciliation`.

Click opens a small chooser (new lightweight modal or inline popover, consistent with
existing modal patterns in this file): "Compare against" → Live/Path/Upload (ad-hoc,
Source B entered fresh) or "Past run" (fetches `/api/jobs/{name}/runs`, lets the user
pick one as Source B; Source A is always this job's own last-known config or a second
run picker for true run-vs-run). Confirms into `openCompareForJob`.

### New/Edit Job modal

New collapsible "Test Compare" section inside the modal
(`tab-launch.html:285-1075`), shown only for job_type `bo_report`/`reconciliation`.
Unlike the catalog button, this uses the modal's live (possibly unsaved) form field
values directly to build the compare request body — no save required first, so
in-progress edits aren't lost by navigating away. Results render inline in the modal
in a shared result-panel partial (extracted from the existing Compare tab result
markup if not already reusable as its own component — small factoring change,
in-scope since both surfaces need identical rendering).

### Job Selections History bridge

Extends the existing History modal (`tab-launch.html:1308-1333`): once exactly two
runs are picked, a job dropdown appears, scoped to jobs present in both runs (from the
selection's `job_sequence`) and filtered to `bo_report`/`reconciliation` types, plus a
compare-type selector defaulting to that job's own type. Mismatch-diff remains
available as the generic whole-run/per-query option, unchanged. Picking
bo_report/reconciliation/multi_file plus a job routes through `openCompareForJob(job,
{runIdA, runIdB})` instead of the current hardcoded mismatch-diff-only path in
`compareSelectedRuns()`.

## Testing

- **Unit**: `SourceConfig` run-mode validator; `MultiFileCompareRequest`
  run-reference validator (mutual exclusivity with `file_mapping`); BO run-reference
  resolver (success, 404 no result, 422 no artifact); multi-file pair-artifact
  persistence and manifest round-trip; `/api/jobs/{name}/runs` endpoint (ordering,
  limit, empty case).
- **e2e**: extend `tests/e2e/08*-compare-*.spec.ts` to cover the three new entry
  points — Job Catalog Compare button (ad-hoc and run-vs-run), Job modal inline
  Compare, and the extended Job Selections History→Compare flow.

## Out of scope

- Dual-Environment reconciliation launch mode (`tab-compare.html:325-767`'s
  "Dual Environment" sub-mode) is unaffected — it launches fresh full runs across two
  environments and has no per-job prefill concept.
- SQL compare, column stats compare — not part of this request.
- Retention/cleanup for the new per-pair multi-file artifacts reuses the existing
  `UPLOAD_ROOT` sweep (`cleanup_expired_uploads`, startup-triggered); no new cleanup
  code.
