# Playwright E2E Suite — Design

**Date:** 2026-07-14
**Status:** Approved

## Purpose

The frontend (`frontend/index.html` + `frontend/app.js`, ~9,600 lines, Alpine.js SPA over a FastAPI backend) has no browser-driven end-to-end coverage today — only Python-level API/integration tests (`tests/integration/test_api_frontend_smoke.py` etc.) and unit tests. This adds a comprehensive Playwright suite covering every tab's core functionality plus negative/edge cases, so UI regressions are caught before manual QA.

## App shape (as discovered)

- Single-page app, tab-switching via Alpine `currentView`, tabs (`app.js` ~line 104): `config`, `jobs` (labelled "Launch"), `monitor`, `history`, `adapters`, `reports`, `differences`, `compare`, `contracts`, `logs`, `help`.
- Auth is bearer-token based, not a username/password form: an admin token is created once (bootstrap, when `GET /api/auth/setup-status` reports `initialized: false`), then subsequent sessions paste/store the raw token in `sessionStorage['etl_token']`. `require_admin` gates admin-only routes (403 for non-admin tokens); missing/invalid tokens get 401.
- Backend entrypoint: `python -m uvicorn api.main:app --host 127.0.0.1 --port 8000`.
- DB is swappable via `ETL_DATABASE_URL` env var (`etl_framework/repository/database.py`), defaulting to a local sqlite file — this lets tests run against a throwaway DB without touching `etl_framework.db`.

## Architecture

- Add `@playwright/test` as a devDependency; add `playwright.config.ts` at repo root with `testDir: './tests/e2e'`.
- `webServer` block starts uvicorn with `ETL_DATABASE_URL` pointed at a temp sqlite file (e.g. `os.tmpdir()/atom-e2e/<run-id>.db`); a `globalSetup` script deletes/recreates that path before the run starts, so every full run begins from an empty DB (schema created by the app's own startup migrations).
- **Isolation model**: one throwaway DB is shared by the whole Playwright run (restarting uvicorn per spec file is impractical — slow, and reruns startup migrations each time). Each spec file namespaces the data it creates (job/adapter/contract names prefixed `e2e-<spec-slug>-<timestamp>`) and cleans up what it created in `test.afterAll` via direct API calls. This keeps specs independent and rerunnable without polluting real data or depending on tab execution order.
- Tests run against real backend logic (no request mocking) — this is an integration-style E2E suite, matching how `test_api_frontend_smoke.py` already validates the served HTML.

## Spec files

One file per tab/concern; each is an independently completable, parallelizable unit.

| File | Covers | Negative / edge cases |
|---|---|---|
| `00-auth-setup.spec.ts` | Bootstrap first admin token, paste/connect token, disconnect | Malformed token, revoked/wrong token (401), non-admin token on admin-only action (403), unauthenticated API call |
| `01-config.spec.ts` | Validate Configuration, Run Health Check, DB password field | Bad DB credentials, missing required fields |
| `02-launch-jobs.spec.ts` | Add Job wizard (basic/schema/execution sub-tabs), job CRUD, Execution Sequence, Comparison Backend, Pass-with-actions | Missing required fields, duplicate job name, invalid schema JSON |
| `03-monitor.spec.ts` | Trigger run, live status polling, cancel run | Trigger with nothing selected, cancel an already-finished run |
| `04-history.spec.ts` | Run history list, `historySubTab` filters, pagination | Filter combination yielding empty state |
| `05-adapters.spec.ts` | Adapter CRUD, connectivity test | Bad credentials, unreachable host |
| `06-reports.spec.ts` | View/download report, rejected-mismatches HTML report | Report request for a nonexistent run |
| `07-differences.spec.ts` | Differences Explorer search/pagination/insights, bulk-decide bar + modal, mismatch drawer accept/reject | Bulk-decide with zero rows selected, missing decision reason |
| `08a-compare-bo-report.spec.ts` | Compare → BO Report sub-tab (see breakdown below) | Missing source, upload wrong file type, bad path, tolerance/range validation |
| `08b-compare-reconciliation.spec.ts` | Compare → Reconciliation sub-tab: Quick Compare Mode, Dual Environment, Run/File vs Report | Launch with no jobs selected, no source selected, empty past-pairs state, zero-match diff filter |
| `08c-compare-sql.spec.ts` | Compare → SQL sub-tab | Empty query, malformed SQL, missing config |
| `08d-compare-colstats.spec.ts` | Compare → Column Stats sub-tab | Missing required source fields, invalid/non-numeric doc or report ID, negative row-count tolerance |
| `08e-compare-mismatch-diff.spec.ts` | Compare → Mismatch Diff sub-tab | Invalid/nonexistent run UUID, Run A == Run B, query filter matching nothing |
| `08f-compare-templates.spec.ts` | Compare templates bar (Load/Save, shared across all 5 sub-tabs) | Save with empty name, load dropdown with no custom templates |
| `09-contracts.spec.ts` | Contract CRUD | Validation errors on save |
| `10-logs.spec.ts` | Global Logs tab auto-refresh, search/level filter | — |
| `11-help.spec.ts` | Help tab renders | — |
| `12-cross-cutting.spec.ts` | Offline indicator (`apiOk`), unknown route/deep-link, XSS-safe rendering of user-entered text (job names, mismatch reasons), session expiry mid-action | — |

## Compare tab — detailed breakdown

The Compare tab (`currentView === 'compare'`) has 5 sub-tabs (`compareSubTab`) plus a shared template bar above them. This is the most complex tab in the app, so it gets one spec file per sub-tab instead of being folded into a single file.

**Shared template bar** (`08f`) — sits above all 5 sub-tabs:
- Load template `<select>` with "Built-in" (`predefinedCompareTemplates`) and "My Templates" (`compareTemplates`) optgroups; selecting one calls `loadCompareTemplate()` and populates the active sub-tab's fields
- "Save Template" toggles a panel with a name input + Save/Cancel
- Negative: Save with empty name; My Templates optgroup absent when `compareTemplates` is empty

**08a — BO Report** (`compareSubTab === 'bo'`):
- Source A/B, each independently switchable between 4 modes: Live (cascading Config → Document → Report selects, via `loadCompareBODocuments`/`loadCompareBOReports`), Path (raw file path input), Upload (multi-format file input: csv/xlsx/xls/json/xml/tsv/txt), API (Config → Endpoint select via `configApiEndpointNames`)
- Key columns / Exclude columns text inputs
- Advanced Options accordion: Backend (pandas/polars/duckdb), Float Tolerance, Datetime Tolerance, Mismatch Row Limit, Sample Fraction (0.01–1), Per-column tolerances, Case-insensitive columns, Whitespace-normalize columns, Parallel column comparison checkbox
- "Save as Baseline when complete" checkbox; "⇄ Swap Sides" button (verify A/B contents actually swap)
- Run BO Compare → loading state → result card: status badge, test-count chips, results table with per-row checkbox selection and "View" → opens mismatch drawer
- Result actions: 📊 Chart toggle (chart-by column/sourceValue/targetValue select, canvas render), Export Settings, Download All Differences (CSV/Parquet, each shows busy/disabled state mid-export), "Open in Reports →" (navigates to Reports tab with matching `reportRunId`)
- Negative: Run with no source selected on either side; Live mode with doc/report left unselected; Upload wrong extension (rejected by `accept` filter — verify via `browser_file_upload` with a disallowed type is not offered/handled); Path pointing at a nonexistent file (compare fails, error surfaced); tolerance/limit fields at their boundaries (sample fraction 0 or >1, mismatch row limit <1)

**08b — Reconciliation** (`compareSubTab === 'recon'`):
- Quick Compare Mode checkbox (auto-selects last successful run as Source A when enabled)
- Mode switch: **Dual Environment** vs **Run/File vs Report**
- *Dual Environment*: Config A/B selects, source/target env label inputs, multi-select job list, "Launch Dual-Env Run" → pair result (improved/regressed/unchanged chips + per-test delta table); "Past Dual-Env Pairs" list with Refresh and per-row "Load"
- *Run/File vs Report*: Source A/B each switchable between Stored Run (select from `sortRunsForDisplay(runs)`), Server Path, Upload; key/exclude columns + same Advanced Options set as BO; "Compare Files" → result card with CSV/XLSX/HTML download buttons, download-all-differences, "Open in Reports"; per-result row expands into: column statistics table (sortable by column/mismatches/compared/match%, with a filter input), diff filter bar (type/column/search), diff table (source/target values with "…more" expand for long cells, delta, mismatch type), "Load next 100 rows" pagination
- Negative: Launch dual-env with zero jobs selected; Compare Files with no source chosen on either side; Upload wrong file type; Refresh past pairs with none existing (empty-state message, not an error); clicking a row to expand when its status is PASSED (must not expand — only non-PASSED rows are clickable); diff filter search with no matches (table renders empty, count reflects 0); "Load more" hidden when `hasMore` is false

**08c — SQL** (`compareSubTab === 'sql'`):
- Config A/B selects; Connection select only rendered when `sqlConfigAConnections()`/`B()` returns entries (verify it's absent for single-connection configs and defaults correctly)
- SQL Query textarea A/B, key/exclude columns, same Advanced Options set
- Run SQL Compare → result card mirrors the Reconciliation file-diff result: status/pass/fail chips, per-row expandable diff with column stats + filter bar + diff table + pagination, download-all-differences, Open in Reports
- Negative: Run with an empty query on either side; malformed SQL (backend error surfaced, not a silent failure); no config selected; query returning identical result sets both sides (PASSED, row not expandable)

**08d — Column Stats** (`compareSubTab === 'colstats'`):
- Source A/B independently switchable between Upload / Live BO (Config select + free-text Document ID + Report ID — note these are raw inputs here, not cascading selects like the BO tab) / File path / API endpoint
- Query/Report name, Float Tolerance, Row Count Tolerance
- "Compute Column Stats" → result: "No drift" message when `has_diffs` is false, else a drift table (column, metric, source value, target value, delta)
- Negative: Compute with required source fields empty; non-numeric Document/Report ID in Live mode; Upload wrong extension; negative Row Count Tolerance

**08e — Mismatch Diff** (`compareSubTab === 'mmdiff'`):
- Run A (baseline) / Run B (current) UUID inputs with optional labels; optional query-name filter
- "Run Mismatch Diff" → summary chips (new/resolved/persistent counts) + regressions badge; three independently-paginated tables (New Regressions, Resolved, Persistent) each with a "Load more" button driven by `mismatchDiffVisible`
- Negative: invalid/nonexistent run UUID on either side (error surfaced, not a blank success state); Run A identical to Run B (expect 0 new/0 resolved, everything persistent — or whatever the backend's actual documented behavior is, confirmed during implementation); query-name filter matching nothing (all three lists empty, no regressions badge, no crash)

## Shared fixtures

- `tests/e2e/fixtures.ts`: a Playwright fixture that bootstraps (or reuses, if already initialized) an admin token once per worker and injects it into `sessionStorage` before each test, so most specs start already authenticated. `00-auth-setup.spec.ts` is the exception — it explicitly drives the unauthenticated/bootstrap flow itself.
- `tests/e2e/api-helpers.ts`: thin wrappers over the backend REST API (using the bootstrapped token) for setup/teardown data a spec needs but isn't itself testing (e.g. `02-launch-jobs` needing a pre-existing adapter to attach a job to).

## Error handling / non-goals

- No mocking of network responses — failures are induced by real invalid input (bad credentials, malformed JSON) rather than intercepted routes, keeping the suite honest about actual backend behavior.
- Not covering: cross-browser matrix (Chromium only), visual/screenshot regression, load/performance testing, mobile viewport — out of scope for this pass.

## Testing strategy for the suite itself

- `npx playwright test` runs the full suite headless in CI; `--ui`/`--headed` for local debugging.
- Each spec file's `afterAll` deletes the jobs/adapters/contracts/tokens it created, verified by re-fetching the relevant list endpoint and asserting the namespaced items are gone — so a partial run failure doesn't leave orphaned state for the next run.
