# Matrix Compare: Save as Job

Date: 2026-08-30

## Problem

`docs/superpowers/specs/2026-08-12-compare-as-schedulable-job-design.md` added
`job_type: "compare"` with `compare_type` of `"bo"` or `"recon_file"`, giving
those two Compare sub-tabs a "Save as Job" button. The Matrix sub-tab
(`tab-compare.html:1561`, `runMatrixCompare()` → `POST /api/compare/matrix`)
was added later and never got one — `matrixCompareResult` is ad-hoc only,
exactly the gap the 08-12 doc described for BO/recon-file before that work.

Recon file-vs-file mode already has "Save as Job"
(`compare-file-save-job-btn`) and Dual-Environment recon mode intentionally
does not (it launches existing jobs from a selection rather than building
one — different shape, out of scope here). Matrix is the only real gap.

## Goal

Save a Matrix comparison as a job, edit it from the Job Catalog, launch it
manually or on a schedule, and have its Full HTML Report show real diff
rows — the same contract BO and recon-file compare jobs already have.

## Design

### Third `compare_type`

```jsonc
{
  "job_type": "compare",
  "params": {
    "compare_type": "matrix",
    "request": { /* verbatim MatrixCompareRequest body */ }
  }
}
```

`_buildMatrixComparePayload()` (`compare.js:1032`) already builds exactly
this request shape for the Run button — it is reused unmodified for Save as
Job, no new builder needed.

### Non-repeatable sources

Matrix has no `run`/`upload` mode pills like BO — its `file` source type can
still carry an uploaded file (`file_b64` set, `file_path` empty), which is
non-repeatable the same way a BO upload is. That's the only rejection case,
on either side:

> `compare job Source {A|B} is an upload - a job that re-runs needs a file
> path or another repeatable source.`

`sql`, `aws_athena`, `sap_bo`, and `api` source types are all
config/query-driven and already repeatable as-is.

### Backend (four existing bo/recon_file branches, each gains a matrix arm)

1. **`api/schemas.py`** `JobDefinition` validator — extend the
   `compare_type not in (...)` tuple to include `"matrix"`; on match,
   `MatrixCompareRequest.model_validate(request)` and reject an upload
   source per side.
2. **`etl_framework/runner/job_validation.py`** — mirrors (1), this
   codebase's existing duplication pattern for job validation.
3. **`api/services/run_executor.py`** `_build_case_compare` — add
   `elif compare_type == "matrix": result = service.compare_matrix(MatrixCompareRequest.model_validate(request))`.
   `compare_matrix` (`compare_service.py:961`) is already a pure core (no
   run-bookkeeping side effects) — no core/wrapper split needed, unlike the
   08-12 work required for BO/recon-file.
4. **`api/services/difference_export.py`** `_write_compare_job` — add a
   matrix branch: extract both sources via `extract_data_source` (same call
   `compare_matrix` itself makes) and feed `_write_tabular_differences`,
   which already infers keys when `key_columns` is empty. Without this, a
   matrix job's "Full HTML Report" silently exports zero difference rows —
   the exact bug class `26-compare-save-as-job.spec.ts`'s fourth test
   already regression-guards for recon_file.

### Frontend

1. **`tab-compare.html`** — a `compare-matrix-save-job-btn` beside
   `btn-run-matrix-compare` (~line 1717-1721), calling
   `openSaveCompareAsJob('matrix')`. The save-job modal is generic and
   already works for any compare type.
2. **`compare.js`** `_compareJobBody()` — branch for `'matrix'` building
   `{ job_type: "compare", params: { compare_type: "matrix", request: this._buildMatrixComparePayload() } }`.
   `_assertCompareJobSourcesAreRepeatable` gains a `matrix` branch checking
   both sides for `source_type === 'file' && file_b64 && !file_path`.
3. **`launch.js`** — `_compareSubTabForJob` maps
   `compare_type === 'matrix'` → `'matrix'`; `openCompareForJob` gains a
   matrix prefill branch. Hydration is simpler than BO's: Matrix's UI type
   strings (`sql`/`file`/`aws_athena`/`sap_bo`/`api`) already equal
   `DataSourceSpec.source_type` values directly (no `live`/`path`/`upload`
   indirection), so a new `_hydrateMatrixSourceFromConfig(cfg)` in
   `compare.js` is a near-identity mapping back into `matrixSourceA`/`B`
   shape.

## Testing

**Unit**
- `job_validation.py` / `api/schemas.py` matrix branch: accepts
  path/sql/athena/api/sap_bo sources, rejects an upload source on each side
  with the correct side letter in the message.
- `_write_compare_job` matrix branch produces non-empty diff rows for a
  known source/target pair.
- `_build_case_compare` matrix dispatch returns a `ReconciliationResult`
  with `query_name` overridden to the job name.

**E2E** (`tests/e2e/33-compare-matrix-save-as-job.spec.ts`, mirrors
`26-compare-save-as-job.spec.ts`'s structure and fixtures) — no live-backend
flag; file-path sources only, matching Matrix's simplest repeatable case:
- save a path-vs-path Matrix job, confirm it appears in the Job Catalog,
  launch it via the API, confirm the run isn't `ERROR`.
- refuse to save a Matrix compare whose Source B is an upload; error names
  Source B.
- edit a saved Matrix job: key/exclude columns round-trip through the Job
  Catalog edit flow and persist correctly, functionally proven by a
  triggered run's mismatch counts.
- Full HTML Report download for a Matrix job contains real
  `data-mismatch` rows (not empty), same regression shape as the
  recon_file test.

## Out of scope

- Live SQL/Athena/SAP BO/API source coverage in the new e2e spec (file-only
  is sufficient to prove the save/edit/launch/report contract; the source
  extraction path itself is already covered by the ad-hoc Matrix Run e2e
  tests).
- Dual-Environment recon mode gaining Save as Job (different shape: it
  launches existing jobs from a selection, not a comparison config it owns).
- Any change to SQL compare, Column Stats, or Mismatch Diff sub-tabs
  (still out of scope per the 08-12 design).
