# Full HTML Report Download — Design

## Problem

HTML report download (Compare tab "⬇ HTML Report" button, and viewing any run's report)
only renders mismatch rows already persisted in `mismatch_details`. That table is
hard-capped at comparison time by `mismatch_row_limit` (1000 for job/scheduled runs,
5000 for ad-hoc Compare-tab runs — `etl_framework/reconciliation/backends/pandas_backend.py`).

Confirmed against the local dev DB (`etl_framework.db`):

```
run 3d99791e: 9,074 total mismatches, only 5,000 stored
run 7f1d943a: 9,074 total mismatches, only 1,000 stored
run 2ae20088: 112,911 total mismatches, only 5,000 stored
```

The report template does have a "Load all differences" button
(`report.html.j2` `loadAllDifferencesGlobal`/`loadAllDifferencesForSection`) that fetches
the rest via `/api/runs/{id}/exports`, but it reads its auth token from
`sessionStorage.getItem('etl_token')` and calls `fetch(window.location.origin + ...)`.
Once the file is downloaded and opened standalone (double-click, `file://`, air-gapped
machine — this project explicitly supports air-gapped use per its font-loading
history), sessionStorage is empty and the origin is wrong, so the fetch fails. A
downloaded report is permanently stuck at the row-limit subset — there is no way to get
a self-contained file with the entire comparison.

Separately: the report's existing client-side filter/search/nav toolbar
(`#filter-search`, `filter-test`/`filter-col`/`filter-type` selects, donut/heatmap)
already operates over whatever `<tr data-mismatch>` rows are in the DOM, with no
server dependency. It was never the bottleneck — the truncated row set was. Once the
full row set is present, this toolbar works over it automatically.

## Goal

Add a "Download Full HTML Report" action (History tab, run detail) that produces a
single, self-contained HTML file containing the entire comparison — no live API calls
needed after download — searchable/filterable offline via the existing toolbar.

## Non-goals

- Not touching the existing capped "Report" (view) or Compare tab "⬇ HTML Report"
  button — those stay fast/small/default as-is.
- Not changing `mismatch_row_limit` or DB storage behavior.
- Not building new interactivity — the existing filter/search/nav/donut/heatmap JS is
  reused unchanged (aside from the debounce fix below).

## Design

### UI flow (History tab, run detail)

1. New button "Download Full HTML Report" next to existing Export CSV / All
   differences CSV / All differences Parquet buttons.
2. Click → `GET /api/runs/{run_id}/differences/summary` → `{total_issues, stored_rows}`
   (thin wrapper around existing `stored_completeness_summary()`).
3. Confirm dialog: plain `window.confirm('This run has {total_issues} total
   mismatches (~{estimate} MB estimated). Continue?')` — same pattern already used
   for every other destructive/heavy action in this codebase (delete run, delete job,
   revoke token, etc. — see `frontend/features/history.js`, `config.js`, `launch.js`,
   `contracts.js`). Estimate uses a fixed ~1.8KB/row constant (measured from the
   5000-row/8.86MB sample above) — good enough for a heads-up, not meant to be exact.
4. On Continue: reuses the existing async export-job pattern (`DifferenceExportJob`,
   same `POST /exports` → poll `GET /exports/{id}` → `GET /exports/{id}/download` flow
   already used by "All differences" CSV/Parquet), with `format="html"`. Same busy/label
   UI reuse as `isDifferenceExportBusy`/`differenceExportLabel`.
5. On job completion, frontend downloads the artifact via
   `triggerDownload`, same as other exports.

### Backend

- `api/services/difference_export.py`:
  - New `write_full_html_report(db, run, path) -> int` (returns row count):
    - If `stored_rows_are_complete(db, run)`: query `MismatchDetail` directly, grouped
      by `test_result_id` — no recompute needed.
    - Else: call existing `write_recomputed_differences(db, run, "json", tmp_path)`
      unchanged (same recompute-from-source logic, same "sources may have drifted"
      caveat already accepted for CSV/Parquet), then read the JSONL back and group by
      `test_name`.
    - Build a snapshot for rendering: reuse `build_run_report_snapshot(run)` for the
      suite-level/result-level metadata, then replace each result's mismatch list with
      the full grouped set (bypassing the template's normal per-section slice).
    - Render `report.html.j2` with a new `full_export=True` flag, write to `path`.
  - `run_difference_export_job`: add an `html` branch that calls
    `write_full_html_report` instead of going through `DifferenceWriter`
    (`DifferenceWriter`/`DIFFERENCE_FIELDS` stay flat-tabular-only — an HTML document
    doesn't fit that abstraction, so this is a parallel path, not a new `DifferenceWriter`
    format).
  - `validate_difference_format` / the `/exports` route: accept `"html"` as a valid
    format value for job creation, status, and download (content-type `text/html`,
    filename `report_{run_id}_full.html`).
- `api/routes/runs.py`: new `GET /{run_id}/differences/summary` returning
  `stored_completeness_summary(db, run)`.
- `etl_framework/reporting/templates/report.html.j2`:
  - `full_export` flag: when true, skip the `MAX_MISMATCH_DISPLAY` slice per section
    (render every mismatch), hide "Load all differences" buttons/status spans for
    sections that are already complete (nothing left to load), and skip the global
    "Load all differences (entire run)" button entirely.
  - Debounce `#filter-search`'s `oninput` handler (~200ms) so typing stays smooth at
    tens of thousands of rows. Applies to the template generally (harmless at small
    scale), not gated on `full_export`.
- `etl_framework/reporting/generator.py`: `ReportGenerator.generate()` needs no change
  — it already just does `template.render(suite=suite_result)`; the caller
  (`write_full_html_report`) passes `full_export=True` into the render context directly
  rather than going through `ReportGenerator`, since `ReportGenerator.generate()` writes
  to a fixed `report_{run_id}.html` path and this needs a distinct export-job artifact
  path instead.

### Frontend

- `frontend/features/history.js`: new `downloadFullHtmlReport(runId)` — fetch
  summary, `window.confirm(...)`, then same export-job poll loop as
  `downloadAllDifferences` but for `format: 'html'`.
- `frontend/partials/tab-history.html`: new button in the run-detail action row.

### Error handling

Job `FAILED` status surfaces the same friendly error toast already used for CSV/Parquet
export failures (`_friendly_export_error`) — no new error class.

### Testing

- Unit: `write_full_html_report` — grouping correctness (stored-complete path and
  recompute path), rendered output contains all rows (spot-check a row past the
  original 100/1000/5000 cutoff), no "Load all" buttons/truncation banners present when
  `full_export=True`.
- Route test: `GET /differences/summary` returns correct counts.
- Route test: `/exports` with `format=html` end-to-end (create → poll → download),
  content-type and filename correct.
- E2E (`tests/e2e/06-reports.spec.ts` or `08b-compare-reconciliation.spec.ts`): click
  button → confirm dialog shows count → confirm → poll → download fires; downloaded
  file's search box finds a mismatch beyond the original truncation cutoff.

### Implementation notes (2026-07-18)

Implemented per the plan (`docs/superpowers/plans/2026-07-18-full-html-report-download.md`)
with these deviations:

- **Task 1 prerequisite fix** (not in the original spec): `_write_sql_compare` /
  `_write_recon_file_compare` used `"sql_comparison"`/`"recon_file"` as `test_name`
  fallbacks while the real compare success path uses `req.label_a or "file_a"` --
  aligned to `"file_a"` so recomputed rows group with their `TestResult`.
- **Template `data-key` fix** (discovered during Task 3): `{{ mm.key_values | tojson }}`
  inside a double-quoted attribute truncated at the first `"` because Jinja's `tojson`
  leaves double quotes unescaped (safe for single-quoted attributes only). Now
  `| tojson | forceescape`, which fixes key-value search and accept/reject row keying
  for server-rendered rows in *all* reports, not just full ones.
- **E2E content verification** (`tests/e2e/04-history.spec.ts`, not `06-reports.spec.ts`):
  reading the browser's downloaded file EPERMs on Windows (AV scan lock on the
  Playwright artifact), so the test asserts the download event + filename from the UI
  click, then verifies content by streaming the same COMPLETED export-job artifact
  through the API.
