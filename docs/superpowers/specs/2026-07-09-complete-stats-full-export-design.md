# Complete Reconciliation Statistics & Full Differences Export — Design

**Date:** 2026-07-09
**Status:** Approved
**Branch:** agent/xml-json-file-compare

## Problem

Web reporting today shows only partial statistics and partial differences:

- Web UI "Top stored detail patterns" shows only the top 5 mismatch patterns
  (`frontend/index.html` ~line 2044); the mismatch-diff view silently slices to
  the first 50 rows (~line 4271).
- The generated HTML report caps "Top Columns by Mismatch Count" at 10
  (`etl_framework/reporting/templates/report.html.j2` ~line 597) with no
  complete alternative.
- Downloads (`GET /runs/{run_id}/mismatches/download`, CSV/XLSX/HTML) export
  only the mismatch detail rows stored in the DB, which are capped per test by
  `mismatch_row_limit` (default 5000). When a compare finds more differences
  than the cap, the export is silently incomplete.
- `ReconciliationEngine` leaves `mismatch_summary` as `None` on its fallback
  path (`etl_framework/reconciliation/engine.py` ~line 176), so full
  per-column counts are not always available.

Users need: complete per-column statistics on every reporting surface, and a
way to download **all** differences (not just the stored subset) for offline
investigation.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Surfaces | Web UI run/compare views, generated HTML report, and exports |
| Cap handling | Recompute on export when stored rows are incomplete |
| Uploaded (base64) sources | Persist uploads server-side at run time so recompute is always possible |
| Full-export formats | CSV and Parquet (XLSX excluded — 1M-row sheet limit); existing stored-row CSV/XLSX/HTML downloads unchanged |
| Stats presentation | All columns in a sortable/filterable table; charts stay top-10 as a visual summary |
| Export architecture | Hybrid: instant stream when stored rows are already complete, async recompute job otherwise |

## Design

### 1. Data layer — per-column statistics everywhere

- `TestResult.mismatch_summary` (JSON column, already persisted) is the single
  source of truth for full per-column mismatch counts.
- Fix the `ReconciliationEngine` fallback path so `mismatch_summary` is
  populated on **every** compare path: pandas, polars, duckdb backends,
  parallel column mode, and the non-backend fallback. Contents per column:
  full value-mismatch count (not capped by `mismatch_row_limit`), plus
  compared-row count so match % can be derived. Row-level counts
  (`missing_in_target_count`, `missing_in_source_count`) stay on the result.
- API: extend the existing run/result payloads (and the payload the compare
  tab consumes) with the per-column stats derived from `mismatch_summary`:
  `column`, `mismatch_count`, `compared_rows`, `match_pct`.

### 2. Web UI

- Replace the "Top stored detail patterns" top-5 block with a full **sortable,
  filterable, scrollable table** of all columns (name, mismatch count,
  match %), fed by `mismatch_summary` totals — not by loaded detail rows.
- Show the same table in the compare tab's expanded result panel.
- Mismatch-diff view: replace the hard `slice(0, 50)` on new/resolved/persistent
  lists with load-more pagination; show total counts.
- Existing charts remain top-10 as a visual summary; the table is the complete
  view.

### 3. Generated HTML report

- Keep the top-10 "Top Columns by Mismatch Count" chart.
- Add an "All Columns" sortable table beneath it, rendered from
  `mismatch_summary` totals for every test.
- Show a truncation banner whenever stored detail rows < total mismatch count
  ("N of M rows shown; download full export for all differences").

### 4. Upload persistence (recompute prerequisite)

- When a compare run is launched with base64 file content, persist the raw
  bytes to `reports/uploads/{run_id}/<original-name>` before comparing.
- Store a sanitized copy of the compare request in the existing
  `TestRun.config_snapshot` JSON column: base64 payloads replaced by the
  persisted file path; secrets (config credentials) never stored — only the
  config id reference.
- New setting `upload_retention_days` (default 30). A cleanup task deletes
  expired upload directories; exports for runs whose uploads were cleaned fall
  back to the failure path in §5.

### 5. Full-differences export — hybrid

**Fast path (complete in DB):**
`GET /runs/{run_id}/differences/download?format=csv|parquet`
When every result's `total_issues` ≤ its stored mismatch-detail row count, the
stored rows ARE the complete set — stream them straight from the DB in the
requested format. No recompute.

**Slow path (stored rows incomplete):**
The download endpoint responds `202` with `{"requires_export_job": true}`;
the client then:

1. `POST /runs/{run_id}/exports` `{format: "csv"|"parquet"}` — creates an
   export job row and schedules a FastAPI `BackgroundTasks` worker (same
   pattern as compare runs).
2. Worker rebuilds both sources from `config_snapshot`:
   - SQL compares → re-run stored queries against the referenced configs
   - file-path sources → re-read from disk
   - persisted uploads → re-read from `reports/uploads/{run_id}/`
   - live BO / API sources → refetch (data may have drifted since the run —
     export carries a `recomputed_at` + drift warning in its metadata)
3. Worker re-runs the reconciliation with a **streaming mismatch writer**:
   mismatch records are appended to the on-disk artifact (CSV writer or
   pyarrow Parquet writer, batched) instead of accumulating in memory, so
   arbitrarily large diff sets cannot OOM the server.
4. Artifact written under `reports/exports/{run_id}/`.
5. `GET /runs/{run_id}/exports/{export_id}` — status (PENDING/RUNNING/
   COMPLETED/FAILED + row count + friendly error).
6. `GET /runs/{run_id}/exports/{export_id}/download` — serves the artifact.

**Failure handling:** if a source can't be rebuilt (uploads cleaned, config
deleted, DB unreachable), the job ends FAILED with a friendly message and the
UI offers the stored-rows download (existing endpoint) as a fallback, clearly
labelled as partial.

**Export columns:** test_name, key_values, column_name, source_value,
target_value, mismatch_type, delta, relative_delta.

**UI:** the compare tab and run report view get "Download all differences"
buttons (CSV, Parquet). When the async path is needed, the button starts the
job and shows inline progress (poll status), then flips to a download link.

**Dependency:** `pyarrow` added to requirements for Parquet.

### 6. Formats summary

| Download | Content | Formats | Status |
|---|---|---|---|
| `/runs/{id}/mismatches/download` | stored detail rows | CSV, XLSX, HTML | unchanged |
| `/runs/{id}/export` | per-test result summary | CSV | unchanged |
| `/runs/{id}/differences/download` + export jobs | **all** differences | CSV, Parquet | new |

### 7. Error handling

- Missing run / result → 404 (existing pattern).
- Recompute source unreachable → export job FAILED, friendly `error_message`,
  UI fallback to partial stored-rows download.
- Parquet requested without pyarrow installed → 500 with actionable message
  (mirror of the existing beautifulsoup4 pattern).
- Concurrent export requests for the same run+format reuse the in-flight job.

### 8. Testing

- **Engine:** `mismatch_summary` populated with full counts on all backends
  (pandas/polars/duckdb), parallel mode, and fallback path; counts exceed
  `mismatch_row_limit` when real mismatches do.
- **Hybrid decision:** stored-complete → fast path; stored-truncated → 202 /
  job path.
- **Writers:** CSV and Parquet golden-file tests, streaming batches, large-set
  memory bound (batch flushes).
- **Upload persistence:** bytes persisted at launch, `config_snapshot`
  sanitized (no b64, no secrets), retention cleanup deletes expired dirs.
- **Recompute:** source resolution for SQL / file path / persisted upload;
  missing-source → FAILED job with friendly error.
- **API:** endpoint contracts, job lifecycle, 404s, concurrent-job reuse.
- Tests dir is gitignored — new test files need `git add -f`.

## Out of scope

- Changing `mismatch_row_limit` default.
- XLSX/JSON full exports.
- Distributed job queue (BackgroundTasks is sufficient; single-process).
