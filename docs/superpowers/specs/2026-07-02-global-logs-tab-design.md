# Global Logs Tab — Design Spec

**Date:** 2026-07-02
**Status:** Approved

## Problem

The app has no way to see "what is happening" for troubleshooting unless you already know a specific `run_id` and open its Reports → Logs sub-tab. There's no server-wide log view. Worse, even that per-run view would miss the exact class of error that motivated this feature: unhandled exception tracebacks (e.g. `sqlalchemy.exc.OperationalError`) are logged by uvicorn's own logger, which writes to a separate stream (console/stderr) that never reaches `logs/etl_framework.log` — the only file the app's log tooling reads.

## Decision

Three independent, additive changes:

1. Attach the app's existing logging handlers to uvicorn's logger namespaces too, so uvicorn's tracebacks and lifecycle messages land in the same `logs/etl_framework.log` file as everything else.
2. Add a new top-level `GET /api/logs` endpoint that exposes the existing `log_parser` filtering logic without requiring a `run_id`.
3. Add a top-level "Logs" nav tab that polls this endpoint every 5 seconds and reuses the existing per-run log viewer UI (search box, level chips, highlighted matches) already built for the Reports tab.

The existing `/api/runs/{run_id}/logs` endpoint and its Reports-tab UI are untouched — this spec only adds a global view alongside it.

## Architecture

### Backend: logging unification (`etl_framework/utils/logging.py`)

In `configure_logging()`, after building `stream_handler` and `file_handler`:

- For each of `logging.getLogger("uvicorn")`, `"uvicorn.error"`, `"uvicorn.access"`: clear existing handlers (uvicorn installs its own colorized console handler by default) and attach `stream_handler` + `file_handler`.
- `RunContextFilter` still applies uniformly; uvicorn's own records simply get an empty `run_id` field, consistent with how non-run background events (scheduler ticks, startup) already render.
- This runs once at startup via the existing `on_startup()` hook in `api/main.py` — no dependency on how or with what flags the process is launched.

**Known limitation, not addressed here:** if uvicorn is ever run with `--workers N > 1`, multiple processes writing to one `RotatingFileHandler` isn't inherently rotation-safe. This risk already exists today for the `etl_framework` logger and is not introduced or worsened by this change.

### Backend: new endpoint (`api/routes/logs.py`)

```python
@router.get("")
def get_logs(
    run_id: str = "",
    q: str = "",
    level: str = "",
    limit: int = 500,
):
    log_path = Path("logs") / "etl_framework.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = filter_log_events(text, run_id=run_id, query=q, level=level, limit=limit)
    return {
        "run_id": run_id,
        "query": q,
        "level": level,
        "total_lines": len(text.splitlines()),
        "total_events": len(parse_log_events(text)),
        "matched_lines": len(lines),
        "lines": lines,
    }
```

Mounted in `api/main.py` as `app.include_router(logs_routes.router, prefix="/api/logs")`, following the existing router registration pattern.

- Reuses `parse_log_events`/`filter_log_events` from `api/services/log_parser.py` unchanged — no parser changes needed. It already handles non-piped uvicorn-style lines (e.g. `"INFO:     Application startup complete."`) via `detect_log_level`'s `startswith` check, and groups traceback continuation lines into the event they belong to.
- `run_id=""` (default) means no run filtering — same behavior `filter_log_events` already implements (`if run_l and run_l not in body_l: continue`).
- No auth beyond the existing global `BearerTokenMiddleware` — any authenticated token (admin or standard) can call this, matching the per-run logs route's access level.
- 404 when the log file doesn't exist yet (fresh install / logs dir wiped), matching the existing per-run route's behavior.

### Frontend: new state (app.js)

| Field | Type | Purpose |
|---|---|---|
| `globalLogEvents` | `array` | All log event objects for the global view, refreshed on each poll |
| `globalLogsLoading` | `bool` | Spinner flag while fetching |
| `globalLogFilterQuery` | `string` | Live text search string |
| `globalLogFilterLevel` | `string` | Active level chip: `''` \| `'ERROR'` \| `'WARNING'` \| `'INFO'` \| `'DEBUG'` |
| `globalLogRunId` | `string` | Optional run-id filter, sent as `run_id` query param |
| `globalLogsPollTimer` | `number \| null` | Handle for the polling `setInterval`, cleared on tab exit |

### Frontend: new methods (app.js)

**`loadGlobalLogs()`**
Calls `api('GET', /api/logs?limit=1000` plus `run_id`/`q`/`level` when set — the endpoint always returns JSON, no `format` param), stores result in `globalLogEvents`. Sets `globalLogsLoading` only on the first load (not on background poll refreshes, to avoid flashing the loading state every 5 seconds). On error, logs to console and skips silently — no toast per failed poll tick, so a transient network blip or server restart doesn't spam the user (the next poll recovers automatically).

**`filteredGlobalLogEvents()`**
Pure filter over `globalLogEvents`, mirroring `filteredLogEvents()`: level chip is an exact match on `event.level`, text query is a case-insensitive substring match on `event.text`.

**`startGlobalLogsPolling()` / `stopGlobalLogsPolling()`**
Called when `currentView` becomes/stops being `'logs'` (via the existing tab-click handler in the nav). Starts/clears a `setInterval(() => this.loadGlobalLogs(), 5000)`, matching the cadence already used by `pollActiveRuns()`. Skips the fetch (but keeps the timer alive) when `document.visibilityState !== 'visible'`, so a backgrounded browser tab doesn't keep polling.

**Scroll-preserving refresh**
Before replacing `globalLogEvents` on a poll tick, check whether the log list container is currently scrolled to its bottom. If so, re-scroll to bottom after the DOM updates (Alpine `$nextTick`); if the user has scrolled up to read older entries, leave their scroll position alone.

## UI Changes (index.html)

### Nav

Add `{ id: 'logs', label: 'Logs' }` to the `tabs` array. Tab click handler gains: `if (tab.id === 'logs') startGlobalLogsPolling(); else stopGlobalLogsPolling();`

### New "Logs" tab section

Structurally a copy of the existing Reports → Logs sub-tab markup, with two differences:

- An additional text input bound to `globalLogRunId` (placeholder: "Filter to a run ID…") above the search bar.
- No "select a run first" empty state — loads immediately on tab open regardless of run context.

Reuses as-is:
- Search input (live, no button) bound to `globalLogFilterQuery`
- Five level chips (ALL / ERROR / WARN / INFO / DEBUG) bound to `globalLogFilterLevel`
- Match count line: `filteredGlobalLogEvents().length + ' / ' + globalLogEvents.length + ' events'`
- Log row rendering: line number, level badge, `x-html="highlightMatch(line.text, globalLogFilterQuery)"`, left-border color by level
- Loading-state and empty-state cards, same styling as the existing Logs sub-tab

No new CSS — reuses `.log-highlight`, `.level-chip`, `.log-entry` and related classes already defined for the Reports tab logs view.

## What Does Not Change

- `/api/runs/{run_id}/logs`, `loadAllLogEvents()`, `filteredLogEvents()`, and the Reports tab's Logs sub-tab are untouched.
- `log_parser.py` (`parse_log_events`, `filter_log_events`, `detect_log_level`) is unchanged.
- `BearerTokenMiddleware` and existing route auth behavior are unchanged.

## Out of Scope

- True live-tail (SSE/WebSocket) — polling every 5s is sufficient per current requirements.
- Reading rotated backup log files (`etl_framework.log.1`, `.2`, ...) — only the active file is read, matching the existing per-run route's behavior.
- Multi-worker-safe rotating file handler — pre-existing risk, not addressed here.
- Export of filtered results, saved filter presets, timestamp range filtering.
