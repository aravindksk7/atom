# Compare as a Saveable, Launchable, Schedulable Job

Date: 2026-08-12

## Problem

The Compare tab runs three tabular comparisons — BO Report
(`runBOComparison()` → `POST /api/compare/bo-report`), Reconciliation files
(`runFileCompare()` → `POST /api/compare/recon-file`), and Multi-File
(`runMultiFileCompare()` → `POST /api/compare/multi-file`). All three are
ad-hoc only: the request body lives in the browser, `CompareService` creates
its own `TestRun`, writes `TestResult` rows, and sets the run's terminal
status itself (`compare_service.py:139`, `:492`, `:721`). Nothing is
persisted that could be re-run later.

Scheduling in this codebase has one path: `Schedule` → `JobSelection` version
→ `job_sequence` (job *names*) → `_execute_run` → `RunExecutor._build_case(job)`,
which dispatches on `job_type` (`run_executor.py:464-521`). "Schedulable"
therefore means "is a `SavedJob` row whose `job_type` `RunExecutor` knows".
A compare request is none of those things — no name, no row, no `job_type` —
so a user who wants the same comparison every night has to re-enter the whole
form by hand, every night.

Compare templates do not close this gap: they are `localStorage` only
(`frontend/app.js:1109-1152`) and never reach the server.

## Goal

A comparison configured once in the Compare tab can be saved as a job, then
launched manually from the Job Catalog *or* fired by cron through a
selection + schedule, producing a run indistinguishable from any other job
run in Reports.

## What already exists

Some compare-shaped work is already a job, which bounds the scope:

| Existing job | Covers |
|---|---|
| `reconciliation` + `source_mode=multi_file` (`run_executor.py:657`) | Full multi-file reconciliation, including s3/sftp sources the ad-hoc Compare tab rejects |
| `reconciliation` + `source_mode=bo_live` (`run_executor.py:584`) | BO live pull vs one local target file — one cell of the BO compare source matrix |
| `reconciliation` + file params (`run_executor.py:551`) | File A vs file B, path sources only |
| `bo_report` (`run_executor.py:1639`) | BO pull only, no comparison, always PASSED |

Multi-file is therefore already schedulable and *more* capable as a job than
as an ad-hoc compare. BO and recon-file are only partially covered — neither
supports the full live/path/api source matrix, and neither carries the
Compare tab's per-comparison tuning.

## Design

### New `job_type: "compare"`

Its params are the **serialized compare request body**, not a translated shape:

```jsonc
{
  "job_type": "compare",
  "name": "nightly_sales_bo_vs_prod",
  "key_columns": ["region", "product"],   // read-only mirror, for Job Catalog display
  "params": {
    "compare_type": "bo" | "recon_file",
    "request": { /* verbatim BOCompareRequest or ReconFileCompareRequest body */ }
  }
}
```

`_build_case_compare` does `BOCompareRequest.model_validate(params["request"])`
and hands the model to the same core the HTTP endpoint calls. This means no
translation layer, exact round-trip fidelity with the ad-hoc run the job came
from, and a Save-as-Job button that posts the identical body the Compare tab
already builds. `AdvancedCompareOptions` rides along inside `request`, so
per-comparison tuning (column tolerances, datetime tolerance,
case-insensitive and whitespace-normalized columns, `sample_frac`, duckdb
backend, parallel columns) is stored per job — `RunSettings` is per *run* and
carries none of those fields.

Multi-file is deliberately **not** part of this job type. Its Save-as-Job
emits a plain `reconciliation` + `source_mode=multi_file` job, reusing the
executor path that already works rather than splitting multi-file across two
implementations.

### Validation

On job create/update, in both `JobDefinition`'s model validator
(`api/schemas.py:489`) and `job_validation.py`:

- `params.compare_type` present and one of `bo` / `recon_file`
- `params.request` parses as the matching request model
- reject `source_type` ∈ {`upload`, `run`} on either BO side; reject
  `stored_run_id` / `stored_run_id_b` / `file_a_content_b64` /
  `file_b_content_b64` for `recon_file`. The error names the offending side.
- `key_columns` / `exclude_columns` are mirrored from `params.request` up to
  the top-level job fields on save, so the Job Catalog and job list render a
  compare job like every other job

Uploads and past-run references are rejected rather than persisted because
neither survives as a repeatable schedule source: upload bytes live only in
the request, and run artifacts are removed by the existing `UPLOAD_ROOT`
retention sweep. A schedule pointed at either would fail silently later
instead of loudly at save time.

Saveable source types are therefore `live`, `path`, and `api` for BO, and
file paths for recon-file.

Not supported in this version: `rules`, `pass_condition`, and `depends_on` on
a compare job. Those are applied by `_run_reconciliation_job`, which the
compare path does not go through. The validator emits a warning-severity
issue (not an error) when they are set on a compare job.

### Backend: split compare cores from run bookkeeping

Each `run_*` method keeps its exact signature and behavior — the three HTTP
endpoints are untouched — but its middle is extracted into a pure core that
returns a result instead of writing one:

```python
# pure: load sources, build engine, reconcile, return
def compare_bo(self, req: BOCompareRequest, run_id: str | None) -> ReconciliationResult
def compare_recon_file(self, req: ReconFileCompareRequest, run_id: str | None) -> ReconciliationResult

# existing wrappers, now thin: status → core → persist → status
def run_bo_comparison(self, req, run_id) -> None
def run_recon_file_compare(self, req, run_id) -> None
```

### The HTML report shape

`run_recon_file_compare` has two internal shapes. Tabular vs tabular
(`compare_service.py:392`) already produces exactly one
`ReconciliationResult`. The HTML-report / run-stats shape
(`compare_service.py:517-580`) writes **N** results, one per test name in the
report — which does not fit `RunExecutor`'s one-case-one-result contract.

The per-test comparison becomes a shared
`_compare_report_stats(stats_a, stats_b) -> list[ReconciliationResult]` with
two deliberately different consumers:

- the ad-hoc endpoint persists all N, exactly as today — no change to the
  Compare tab's rendering or to existing e2e tests
- the job path folds them with a new
  `aggregate_stat_results(job_name, results) -> ReconciliationResult`,
  keeping per-test detail under `mismatch_summary["report_tests"]` and
  reporting FAILED if any test failed

The asymmetry is intentional. Converting the endpoint to a single aggregated
result would break the current Compare tab recon view and its tests for no
benefit to this feature.

### Dispatch

`RunExecutor._build_case` gains one branch alongside the existing ones:

```python
if job.job_type == "compare":
    return self._build_case_compare(job)
```

`_build_case_compare` constructs `CompareService(self._db, ConfigRepository(self._db))`,
validates `params["request"]` into its model, calls the matching core with
the live run's `run_id`, and returns
`dataclasses.replace(result, query_name=job.name)`.

The `query_name` override is load-bearing: Reports, cross-job assertions, and
job-scoped result lookup (`RunRepository.list_results_for_job`) all key on
`query_name == job.name`, while the compare cores set `query_name` to
`label_a`.

Passing the run's own `run_id` into the core means a BO live pull persists
its downloaded artifact and stored API responses under that run, so a later
run-reference compare can point at it — the same behavior the ad-hoc endpoint
already has.

`source_env` / `target_env` on the result stay the comparison's own `label_a`
/ `label_b` rather than the run's environments. "Source A vs Production
Report" is more informative in Reports than "dev vs prod" for a comparison
that has no environments.

### Credentials

A compare job's `live` source resolves through its own `config_id` via
`ConfigRepository`, exactly as the endpoint does. The job is self-contained:
it runs in any selection without that selection needing to carry a BO config.

### Scheduler prerequisite

`scheduler.py:140` builds its `RunTrigger` without `config_id`, so a
scheduled run never receives the selection's Saved Config and no live job
gets credentials. Selection *launch* already does this correctly
(`selections.py:223`, added in 9d7d70a).

The fix — `config_id=version.config_id` on the scheduler's trigger — is the
first task of this work and lands as its own commit. Without it, "schedulable
BO compare" does not work end to end.

### Frontend

Each sub-tab's payload construction is extracted from its `run*` method into
a builder — `_buildBOComparePayload()`, `_buildReconFilePayload()`,
`_buildMultiFilePayload()` — used by both Run and Save, so there is no second
source of truth for what a compare request looks like.

A "Save as Job" button sits beside each Run button and opens a small dialog
for name, description, and tags. On confirm:

- BO / recon-file → `POST /api/jobs` with
  `{job_type: "compare", params: {compare_type, request: <payload>}}`
- multi-file → `POST /api/jobs` with
  `{job_type: "reconciliation", params: {source_mode: "multi_file", file_mapping, ...}}`

A client-side pre-check mirrors the server validator so an upload or
past-run source is caught before the round trip; the server validator remains
authoritative. Success toasts a link to the Job Catalog.

Adding the saved job to a selection and attaching a schedule uses the
existing Launch-tab flows unchanged — reusing them is the reason for landing
on the job model at all.

The `compare` option is added to the job-type dropdown
(`tab-launch.html:342-351`) for visibility in the Job Catalog; creating a
compare job from scratch there is not supported in this version (see Out of
scope).

## Testing

**Unit**

- `compare` job validator: each rejected source type on each side, both
  `compare_type` values, `key_columns` mirroring, warning issues for
  `rules` / `pass_condition` / `depends_on`
- `compare_bo` and `compare_recon_file` cores return a `ReconciliationResult`
  and touch no run status
- `aggregate_stat_results`: all-passed, one-failed, per-test detail preserved
- `_build_case_compare`: dispatch, `query_name` override, `run_id` passed
  through to the core
- scheduler passes `config_id` from the selection version into its trigger
- regression: the three compare endpoints behave identically after the
  core/wrapper split, including the endpoint's N-result HTML behavior

**e2e**

- save a BO compare as a job from the Compare tab, find it in the Job
  Catalog, launch it through a selection, see its run in Reports
- save rejection: an upload source shows the naming error and creates no job

## Out of scope

- SQL compare, Column Stats, and Mismatch Diff as jobs
- Creating or editing a compare job's A/B sources inside the New/Edit Job
  modal — this version saves from the Compare tab; changing a compare job
  means re-saving from there
- Baseline promotion (`/set-baseline`) for compare jobs
- Multi-file gaining a `compare` job type — it keeps its existing
  `reconciliation` + `source_mode=multi_file` path
