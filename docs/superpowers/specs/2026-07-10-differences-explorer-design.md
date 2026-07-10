# Differences Explorer & Report Interactivity — Design

**Date:** 2026-07-10
**Status:** Approved

## Problem

The generated HTML report (`etl_framework/reporting/templates/report.html.j2`)
already has solid interactivity: filter toolbar, live search, char-level diff
highlighting, column heatmap, mismatch-type donut, all-columns sortable table,
keyboard navigation, expand/collapse, copy-to-clipboard. But it is a static
file embedding only the stored `MismatchDetail` rows for a run (capped per
test by `mismatch_row_limit`). There is no way to search/filter/paginate over
mismatches from the live app with server-side query support, and no
run-level insights view (top offenders, accepted-vs-open) outside the
per-report embed.

[2026-07-09-complete-stats-full-export-design.md](2026-07-09-complete-stats-full-export-design.md)
already solved *complete per-column stats* and *full-differences file export*
(CSV/Parquet, hybrid fast/async path). This spec builds the missing piece on
top of that: a live, searchable, paginated browsing UI over stored mismatch
data, plus small linking/insight additions to the static report. It does not
change the export/download infrastructure.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Surface(s) | Both: new frontend tab + static report gets deep-links and a small insight addition |
| Full differences beyond stored cap | Stay a file download (existing export job); no new query backend over export artifacts |
| Live search | Server-side (new query params on the existing mismatches endpoint) |
| New insight types | None beyond existing heatmap/donut, plus an Accepted vs Open breakdown |
| New UI placement | Dedicated top-level nav tab, "Differences" |

## Design

### 1. Backend — searchable/paginated mismatches endpoint

Extend `GET /runs/{run_id}/results/{result_id}/mismatches`
(`api/routes/runs.py:551`, `RunRepository.list_mismatches`
`etl_framework/repository/repository.py:373`):

New query params, all optional:
- `search` — case-insensitive substring match against `key_values` (JSON,
  stringified), `column_name`, `source_value`, `target_value`.
- `column` — exact match on `column_name`.
- `mismatch_type` — exact match (`value_diff`, `missing_in_target`,
  `missing_in_source`).
- `accepted` — `true` / `false` / omitted (=all).
- `sort` — `id` (default), `column`, `mismatch_type`; always stable via `id`
  tiebreaker.

Response shape changes from a bare list to:
```json
{"items": [MismatchOut, ...], "total": 1234, "stored_complete": true}
```
`stored_complete` reuses the existing `stored_rows_are_complete` logic
(`api/services/difference_export.py:186`) scoped to this one result, so the
UI knows whether "load full export" should be offered. This is a breaking
response-shape change; the frontend is the only known consumer, updated in
the same change.

### 2. Backend — run-level insights endpoint

New `GET /runs/{run_id}/mismatches/insights`:
- Aggregates across all of the run's results using each result's
  `mismatch_summary` (already the source of truth per the prior spec) — no
  new full-table scan of `MismatchDetail` needed for the column/type
  breakdowns.
- Returns: top offending columns (name, count) across the whole run, mismatch
  type totals, and accepted-vs-open counts (accepted counts require a
  `COUNT... WHERE accepted=true` grouped query against `MismatchDetail`,
  since acceptance isn't part of `mismatch_summary`).
- Per-test breakdown list (query_name, total_issues, stored row count) so the
  UI can flag which tests are truncated without a separate round-trip.

### 3. Frontend — new "Differences" nav tab

Added to the `tabs` array in `frontend/index.html`/`app.js` alongside
Reports/Compare/Logs, `id: 'differences'`.

- Run + test picker (reuse existing run/result selectors already used by the
  compare/mismatch-diff views).
- Insights cards at top: top-columns bar (port of `buildHeatmap()` from
  `report.html.j2`), type donut (port of `buildDonut()`), new accepted-vs-open
  stat card. These three renderers move into a shared JS module
  (`frontend/report-charts.js`) so the report template and the app both call
  the same code instead of duplicating the SVG-building logic.
- Filter toolbar mirrors the report's (`#filter-toolbar` pattern): test
  select, column select, type select, search box — but every change re-queries
  the backend (debounced ~250ms) instead of filtering DOM rows.
- Table is paginated (default page size 100, "Load more" or numbered pager —
  implementation detail left to the plan) rather than virtualized, backed by
  `total` from the endpoint.
- When `stored_complete` is false for the selected test, show a banner:
  "Showing N of M stored rows — run a full differences export for the rest,"
  with a button that reuses the existing export-job flow
  (`POST /runs/{run_id}/exports` → poll → download).
- Char-level diff rendering for source/target values reuses the existing
  `charDiff`/`renderSrc`/`renderTgt` logic, also moved into the shared module.

### 4. Static report additions (`report.html.j2`)

Minimal, additive — the embedded experience stays fast and offline-capable:
- New "Accepted vs Open" mini stat card next to the existing
  Passed/Failed/Mismatches/Duration row, computed from `mm.accepted` over the
  embedded mismatch rows (client-side, same as today's other stats).
- Where a test's mismatch section is truncated (`total_mismatches >
  shown_mismatches`), add a link next to the existing "download full
  differences export" note: `Open in Differences Explorer ↗`, linking to
  `<app-url>/#/differences?run={{ suite.run_id }}&result={{ result.id }}`.
  The report doesn't know the frontend's base URL at generation time, so this
  is a relative link assuming the report is opened from within the app shell
  (already the case today via the Report/Metrics/Logs sub-tabs); when opened
  standalone the link simply won't resolve, which is acceptable since the
  export-download note remains as the primary fallback.

### 5. Data flow summary

```
Differences tab -> GET /runs/{id}/mismatches/insights (on run/test select)
                 -> GET /runs/{id}/results/{rid}/mismatches?search=&column=&type=&accepted=&page=
                 -> (if stored_complete=false) existing export job endpoints
Report template -> embeds stored rows as today (unchanged data path)
                 -> links out to Differences tab when truncated
```

### 6. Error handling

- Unknown run/result → 404 (existing pattern, unchanged).
- Invalid `sort`/`mismatch_type`/`accepted` values → 422 via FastAPI enum
  validation, not silently ignored.
- Insights endpoint on a run with zero results → empty aggregates, not an
  error.
- Frontend: failed insights/mismatches fetch shows an inline error state in
  the tab, doesn't block the rest of the app.

### 7. Testing

- **Backend:** pytest for the extended mismatches endpoint — each filter
  param individually and combined, pagination boundaries (`total`, last
  page), `stored_complete` true/false cases, sort stability. Insights
  endpoint: aggregation correctness across multiple results, accepted/open
  counts, empty-run case.
- **Frontend:** manual dev-server + Playwright click-through — search/filter/
  pagination in the new tab, accepted-breakdown card renders, truncation
  banner + export-job button flow, deep link from report opens the tab with
  the right run/result preselected.
- Tests dir is gitignored in this repo — new test files need `git add -f`.

## Out of scope

- Querying/searching over export-job artifacts (stays file download).
- Flaky-column-across-runs detection (explicitly deferred).
- Changing `mismatch_row_limit` or the export job architecture from the prior
  spec.
- New chart/insight types beyond top-columns, type donut, accepted-vs-open.
