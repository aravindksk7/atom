# Load All Differences Inline in the Generated HTML Report — Design

**Date:** 2026-07-11
**Status:** Approved

## Problem

The generated static HTML report (`etl_framework/reporting/templates/report.html.j2`)
renders each test's `result.mismatches` directly. That list is exactly what was
stored at compare time, capped by `mismatch_row_limit` (default 5000,
`api/schemas.py:649`). When a test has more mismatches than the cap, the report
already tells the user this (truncation note) and offers two ways out:

- a plain-text pointer to "download the full differences export"
- a client-side link (`data-differences-link`) into the app's Differences
  Explorer tab

Neither shows the remaining differences **inside the report itself**. Users
want a "load all" option that loads the complete set into the report's own
tables, without leaving the page or opening a separate file.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| What "load all" does | An inline button fetches/recomputes the full set and renders it in the report's own table (not just a link-out or a file download) |
| Data source | Reuse the existing run-scoped async export job (the same one behind CSV/Parquet full-export downloads) rather than inventing a new recompute path |
| Format | Add a `json` output format to that job so the report can consume structured rows directly, instead of parsing CSV in the browser |
| Button scope | Both: one button per truncated test (loads just that test's rows into that test's table) and one global button (loads everything for the whole run at once) |
| Offline/standalone handling | Not a concern — the report is only ever exercised for this feature from within the running app (blob: URL, shares the app's origin and `sessionStorage` token per the existing Differences Explorer design). No credential-entry UI, no special-casing for a downloaded/offline `.html` file. |

## Why this is safe to build as an inline network call

`report.html.j2` currently has zero network calls — it's fully self-contained
so a downloaded report still opens standalone. This feature is the first to
add one. That's acceptable *only* because of how the report is already loaded
into the browser: the existing Differences Explorer work established that the
report is opened via a `blob:` URL created by the app (`apiBlob()` +
`URL.createObjectURL`), which inherits the creating document's origin —
meaning `window.location.origin` and `sessionStorage` are the app's, not an
opaque `blob:` origin. The "load all" button relies on that same fact to read
`sessionStorage.getItem('etl_token')` and call the API directly, exactly like
`frontend/app.js` does. If a user downloads the `.html` file and opens it
later outside the app, the button will simply fail its fetch (no session, no
reachable API) — per the decision above, that failure mode is out of scope
and is not specially detected or messaged.

## Design

### 1. Backend — add a `json` export format

`api/services/difference_export.py`:

- `DifferenceWriter.__init__`: add a branch for `fmt == "json"` that opens the
  path for text writing (no CSV/Parquet-specific setup needed).
- `DifferenceWriter.write`: for `json`, write one `json.dumps(normalized)` per
  line (newline-delimited JSON / NDJSON) — this keeps the existing
  streaming/batched-write property (no need to buffer the full array or
  rewrite a closing bracket), consistent with how the CSV writer streams row
  by row.
- `DifferenceWriter.close`: no special handling needed for `json` beyond
  closing the file handle (already covered by the existing `self._file is not
  None` close at the end of the method).
- `validate_difference_format`: accept `"json"` alongside `"csv"` and
  `"parquet"`.
- `media_type_for`: return `"application/x-ndjson"` for `"json"`.
- File extension for the artifact/download filename: `.jsonl`.
- No changes to `DIFFERENCE_FIELDS`, job creation/dedup/status/failure
  handling, or the `/runs/{run_id}/differences/download` fast/slow-path
  decision — `json` slots into the existing hybrid architecture exactly like
  `parquet` did.

This intentionally reverses the "XLSX/JSON full exports" exclusion from the
2026-07-09 design — that exclusion was about not adding *user-facing download
formats* without a concrete need; this adds `json` specifically so the report
can consume structured data programmatically, not as a new download button
for end users (though nothing prevents also using it that way later).

### 2. Report — global "load all" button

In `report.html.j2`, near the existing `#mismatches-header` (Expand
All/Collapse All buttons, ~line 357-361):

- Compute (in the template, via a Jinja `namespace` flag set while looping
  `suite.reconciliation_results`) whether any result is truncated:
  `result.total_issues > (result.mismatches | length)` true for at least one
  result — covering both the "some stored" and "zero stored" truncation cases
  already distinguished in the template (lines 364 and 428).
- If true, render a "Load all differences (entire run)" button.
- Click handler kicks off (or reuses) the run-level `json` export job and,
  once complete, distributes rows to every truncated test's table by
  `test_name`, updating each test's truncation note to "Showing all N of N".

### 3. Report — per-test "load all" button

Both truncation paragraphs (~line 374 and ~line 432) get a third inline
action next to the existing download-mention and the Differences Explorer
link: a "Load all for this test" button, tagged with `data-run` and
`data-result` (same attributes the existing Differences Explorer link
already carries).

Click handler: same job (whole-run recompute — there's no cheaper
per-test-only recompute path in the existing architecture), but once the job
completes, only that test's rows (filtered by `test_name` from the NDJSON
payload) get injected into that one table.

### 4. Report — shared inline JS (vanilla, no framework)

Added to the report's existing `<script>` block, alongside
`computeAcceptedStats()` / `wireDifferencesLinks()`:

- `loadAllDifferences(runId, resultIdOrNull, buttonEl)`:
  1. Guard against double-clicks (disable button, show "Preparing…").
  2. `POST /api/runs/{runId}/exports` with `{format: "json"}` and
     `Authorization: Bearer <sessionStorage token>` — reuse if a job is
     already in flight for this run+format (server-side dedup already
     handles this; the report doesn't need its own client-side lock beyond
     disabling the button it was clicked from).
  3. Poll `GET /api/runs/{runId}/exports/{export_id}` every 2s, up to 240
     attempts (mirrors `pollDifferenceExport` in `frontend/app.js:2668`).
  4. On `COMPLETED`, `fetch` the NDJSON artifact from
     `GET /api/runs/{runId}/exports/{export_id}/download`, split on
     newlines, `JSON.parse` each line.
  5. Filter rows by `test_name` (all of them for the global button; one
     `query_name` for a per-test button) and build `<tr data-mismatch ...>`
     elements matching the exact attribute set the server already renders
     (`data-test`, `data-column`, `data-type`, `data-key`, `data-src`,
     `data-tgt`, `data-accepted` — `accepted` state comes back as whatever
     was true at export time; the export doesn't carry `accepted_by`/`note`,
     so injected rows won't render the green "Accepted by … on …" detail row
     that server-rendered accepted rows show, just the base row with
     `data-accepted="true"`).
  6. Replace each affected table's `<tbody>` content for that test (clearing
     any partial/stale rows first) and update its truncation paragraph to
     "Showing all N of N — <button disabled>Loaded</button>".
  7. Call `populateColFilter()`, `buildHeatmap()`, `buildDonut()`,
     `computeAcceptedStats()`, `buildNavList()`, and re-run whatever
     column/search filter (`applyDiff()`/`filterByCol()`) is currently
     active, since all of those scan `document.querySelectorAll('tr[data-mismatch]')`
     freshly each time and would otherwise miss the injected rows.
  8. On `FAILED`, show the job's `error_message` inline next to the button
     and change its label to "Retry" (re-enables the same click handler).
  9. On timeout (240 attempts / 8 minutes), show a generic timeout message
     with a "Retry" option.

No Alpine/Chart.js dependency added — this stays consistent with the report's
existing hand-rolled vanilla-JS style (per the 2026-07-10 design's rationale
for not sharing a JS module between the app and the report).

### 5. Error handling

- Network/auth failure (no session, unreachable API): the `fetch`/`POST`
  throws; caught and shown as "Failed to load all differences: <message>"
  next to the button. No special detection of "standalone report" — this is
  the same failure a raw fetch error produces, and per the decision above
  that's an acceptable, unhandled edge case.
- Export job FAILED (source unreachable, upload cleaned, etc.): show the
  job's existing friendly `error_message`.
- Malformed/partial NDJSON line (shouldn't happen, but the writer streams):
  skip unparseable lines defensively rather than aborting the whole render.

### 6. Testing

- `DifferenceWriter` unit test: writing rows with `fmt="json"` produces one
  valid JSON object per line, each containing exactly `DIFFERENCE_FIELDS`.
- `validate_difference_format("json")` returns `"json"`; still rejects
  anything else.
- `media_type_for("json")` returns `"application/x-ndjson"`.
- Template smoke-render (same pattern as the 2026-07-09/07-10 designs' Jinja
  smoke test) with a result where `total_issues > len(mismatches)`, asserting
  both the global and per-test "Load all" buttons are present in the output;
  and a second render with no truncated results asserting the global button
  is absent.
- Manual browser verification (per this project's `verify`/`run` skills):
  trigger a run with a low `mismatch_row_limit` so a test is truncated, open
  its report, click both the per-test and global "Load all" buttons, confirm
  rows are injected, counts/charts/filters update, and accepted-row styling
  and search/column filters still work correctly against the injected rows.
- Tests dir is gitignored — new test files need `git add -f`.

## Out of scope

- Changing `mismatch_row_limit` default or behavior.
- Handling the report as a standalone/offline artifact for this feature —
  "load all" only works when opened from within the running app's session.
- New XLSX full-export format.
- Changes to the app's Differences Explorer tab, its pagination, or its
  existing CSV/Parquet "download all differences" UI — those are untouched.
- Rendering `accepted_by`/`accepted_at`/`accepted_note` detail rows for
  injected mismatches (the export payload doesn't carry them); injected rows
  only reflect accepted/open state via the badge, not the full acceptance
  detail row.
