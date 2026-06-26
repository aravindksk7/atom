# Metrics & Logs UI — Design Spec

**Date:** 2026-06-26
**Status:** Approved

## Problem

The Metrics and Logs buttons on the History page opened a new browser tab via `openRunTab()`, producing a blob URL of server-rendered HTML. Two compounding issues:

1. The server-rendered logs page has a `<form method="get">` search bar — form submissions are plain browser navigations that carry no `Authorization` header, so every search returns `{"detail":"Missing or invalid Authorization header"}`.
2. Opening a standalone HTML blob means any client-side API calls in that blob have no access to the session token stored in `sessionStorage` (different origin).

## Decision

Keep everything in-app. Clicking Metrics or Logs on a run navigates to the Reports tab and auto-loads that run's data into the correct sub-tab. No new tab is opened. Auth is handled automatically by the existing `api()` function.

Log search is implemented entirely client-side: all events are loaded once as JSON, then filtered in the browser as the user types. This gives instant results with no per-keystroke API calls.

## Architecture

### New state (app.js)

| Field | Type | Purpose |
|---|---|---|
| `allLogEvents` | `array` | All log event objects for the current run, loaded once |
| `allLogEventsLoading` | `bool` | Spinner flag while fetching |
| `logFilterQuery` | `string` | Live text search string |
| `logFilterLevel` | `string` | Active level chip: `''` \| `'ERROR'` \| `'WARN'` \| `'INFO'` \| `'DEBUG'` |

### New methods (app.js)

**`navigateToRunArtifact(runId, view)`**
Sets `currentView = 'reports'`, resets report state, sets `reportRunId = runId`, sets `reportView = view`, marks `reportLoaded = true`, then calls `loadRunMetrics()` (if view is `'metrics'`) or `loadAllLogEvents()` (if view is `'logs'`). Called by the Metrics and Logs buttons in the History tab.

**`loadAllLogEvents()`**
Calls `api('GET', /api/runs/{id}/logs?format=json&limit=5000&scope=run)`, stores result in `allLogEvents`. Sets `allLogEventsLoading` during the call. On error, shows a toast and leaves `allLogEvents` empty.

**`filteredLogEvents()`**
Pure filter over `allLogEvents`. Applies `logFilterLevel` first (exact match on `event.level`), then `logFilterQuery` (case-insensitive substring match on `event.text`). Returns the filtered array. Called inline from `x-for` in the template.

**`highlightMatch(text, query)`**
HTML-escapes `text`, then wraps each match of `query` in `<mark class="log-highlight">`. Returns safe HTML string. Used with `x-html` binding in log rows.

**`resetReportArtifacts()` — updated**
Also clears `allLogEvents`, `logFilterQuery`, `logFilterLevel` on run change.

### Updated methods (app.js)

**`loadReport()`**
When `reportView` is `'report'`: fetches blob and sets `reportBlobUrl` (unchanged).
When `reportView` is `'metrics'`: calls `loadRunMetrics()`.
When `reportView` is `'logs'`: calls `loadAllLogEvents()`.

**`switchReportView(view)`**
If switching to `'logs'` and `allLogEvents` is empty (not yet loaded), calls `loadAllLogEvents()`. Otherwise filtering is instant.

## UI Changes (index.html)

### History tab — run detail panel

Replace the two `<button @click="openRunTab(...)">` buttons for Metrics and Logs with buttons that call `navigateToRunArtifact()`:

```html
<!-- before -->
<button @click="openRunTab(selectedRun.run_id, 'metrics')">Metrics</button>
<button @click="openRunTab(selectedRun.run_id, 'logs')">Logs</button>

<!-- after -->
<button @click="navigateToRunArtifact(selectedRun.run_id, 'metrics')">Metrics</button>
<button @click="navigateToRunArtifact(selectedRun.run_id, 'logs')">Logs</button>
```

### Reports tab — Logs sub-tab

Replace the existing search toolbar (keyword input + level select + scope select + limit input + Search button) with:

- A single text input bound to `logFilterQuery` — live filter, no button
- Five level chips (ALL / ERROR / WARN / INFO / DEBUG) that toggle `logFilterLevel`
- A match count line: `filteredLogEvents().length + ' / ' + allLogEvents.length + ' events'`
- Loading state card while `allLogEventsLoading` is true
- Empty-state card when `filteredLogEvents().length === 0`

Log rows rendered with `x-for="line in filteredLogEvents()"`:

| Column | Content |
|---|---|
| Left border | Coloured by level (red=ERROR, amber=WARN, blue=INFO, purple=DEBUG) |
| Line number | `line.number`, monospace, muted |
| Level badge | `line.level` in coloured pill |
| Message | `x-html="highlightMatch(line.text, logFilterQuery)"` |

Remove the existing "Open Log GUI" button from the toolbar (it used `openRunTab` to open a new tab, which is replaced by in-app navigation).

### Reports tab — Metrics sub-tab

No data-fetch changes. Table row enhancements:

- Row background: `rgba(251,113,133,.06)` for FAILED, `rgba(251,191,36,.04)` for SLOW, transparent for PASSED
- Duration cell: amber text when test status is SLOW
- Issues cell: red bold text when `issues > 0`, green when `0`
- New rightmost column: mini progress bar (green for PASSED, red for FAILED, amber for SLOW), 80 px wide, 4 px tall

Remove the "Open Themed Metrics" and "Raw JSON" buttons from the metrics toolbar (both used `openRunTab`).

## CSS additions (styles.css or inline)

```css
.log-highlight {
  background: rgba(251, 191, 36, 0.28);
  color: #fcd34d;
  border-radius: 2px;
  padding: 0 2px;
}
.level-chip {
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid rgba(148,163,184,.4);
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  background: rgba(255,255,255,.05);
  color: var(--text-muted);
  transition: background .15s, border-color .15s;
}
.level-chip.active-ERROR { border-color: rgba(251,113,133,.6); color: #fda4af; background: rgba(251,113,133,.12); }
.level-chip.active-WARN  { border-color: rgba(251,191,36,.6);  color: #fcd34d; background: rgba(251,191,36,.12); }
.level-chip.active-INFO  { border-color: rgba(59,130,246,.6);  color: #93c5fd; background: rgba(59,130,246,.12); }
.level-chip.active-DEBUG { border-color: rgba(139,92,246,.6);  color: #c4b5fd; background: rgba(139,92,246,.12); }
```

## What Does Not Change

- `openReportTab()` and `openRunTab()` remain for the Report sub-tab blob approach and any other callers.
- `loadRunLogs()` (server-side, JSON) remains — still used when the Report tab is loaded with view=logs via `loadReport()` → `switchReportView()` path.
- The Report sub-tab (HTML blob in iframe) is unchanged.
- `BearerTokenMiddleware`, API routes, and backend rendering functions are unchanged.

## Out of Scope

- Pagination of log events (5000-line cap is the limit for now)
- Export of filtered log results
- Saved log filter presets
