# Global Logs Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the user a server-wide, auto-refreshing "Logs" tab for troubleshooting, and make sure unhandled exceptions (which today only reach uvicorn's own logger) actually show up in it.

**Architecture:** Unify uvicorn's logger into the app's existing rotating-file handler, add a new `GET /api/logs` endpoint that reuses the existing `log_parser` filtering logic without requiring a `run_id`, and add a top-level nav tab that polls it every 5 seconds — reusing the per-run log viewer UI already built for the Reports tab.

**Tech Stack:** FastAPI, Python stdlib `logging`, SQLAlchemy (test fixtures only), Alpine.js, pytest.

**Spec:** `docs/superpowers/specs/2026-07-02-global-logs-tab-design.md`

---

### Task 1: Regression tests for `log_parser` handling uvicorn-style lines

The unification in Task 2 relies on `parse_log_events`/`filter_log_events` (in `api/services/log_parser.py`) correctly handling lines that don't have the app's own `%(asctime)s | %(levelname)s | ...` pipe-delimited format — e.g. uvicorn's plain `"INFO:     Application startup complete."` and multi-line tracebacks. This has been manually verified to already work; this task locks that behavior in with a test so it can't silently regress. No production code changes in this task.

**Files:**
- Create: `tests/unit/test_log_parser.py`

- [ ] **Step 1: Write the test file**

```python
"""Tests for api/services/log_parser.py, including uvicorn-style log lines
that don't use the app's own pipe-delimited format (see the logging
unification in etl_framework/utils/logging.py, which routes uvicorn's
logger through the same file as the app's own logger)."""
from api.services.log_parser import parse_log_events, filter_log_events


UNIFIED_LOG_SAMPLE = (
    "2026-07-02 06:10:00 | INFO     |  | etl_framework.db | starting up\n"
    "INFO:     Started server process [1234]\n"
    "INFO:     Waiting for application startup.\n"
    "INFO:     Application startup complete.\n"
    "2026-07-02 06:10:05 | ERROR    | abc-123 | api.routes.runs | Exception in ASGI application\n"
    "Traceback (most recent call last):\n"
    '  File "x.py", line 1, in <module>\n'
    "    raise ValueError(\"boom\")\n"
    "ValueError: boom\n"
    "2026-07-02 06:10:10 | INFO     |  | etl_framework.db | done\n"
)


def test_uvicorn_style_lines_each_become_their_own_event():
    events = parse_log_events(UNIFIED_LOG_SAMPLE)
    # 3 app-format lines + 3 uvicorn-style lines + 1 traceback event = 6 (last
    # line has no trailing continuation so 3 app + 3 uvicorn-plain + 1 error = 7... )
    uvicorn_events = [e for e in events if e["text"].startswith("INFO:     ")]
    assert len(uvicorn_events) == 3
    assert all(e["level"] == "INFO" for e in uvicorn_events)


def test_traceback_lines_group_into_the_preceding_error_event():
    events = parse_log_events(UNIFIED_LOG_SAMPLE)
    error_events = [e for e in events if e["level"] == "ERROR"]
    assert len(error_events) == 1
    assert "Traceback (most recent call last):" in error_events[0]["text"]
    assert "ValueError: boom" in error_events[0]["text"]


def test_filter_log_events_by_level_finds_the_traceback_event():
    matches = filter_log_events(UNIFIED_LOG_SAMPLE, level="ERROR")
    assert len(matches) == 1
    assert "ValueError: boom" in matches[0]["text"]


def test_filter_log_events_by_run_id_ignores_uvicorn_lines():
    matches = filter_log_events(UNIFIED_LOG_SAMPLE, run_id="abc-123")
    assert len(matches) == 1
    assert matches[0]["level"] == "ERROR"
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_log_parser.py -v`
Expected: 4 passed. (These document already-correct behavior in `log_parser.py` — no implementation change is needed for this task. If any of these fail, stop and investigate `log_parser.py` before proceeding to Task 2, since Task 2's unification depends on this behavior being correct.)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_log_parser.py
git commit -m "test: lock in log_parser handling of uvicorn-style log lines"
```

---

### Task 2: Route uvicorn's logger through the same rotating file as the app's own logger

**Files:**
- Modify: `etl_framework/utils/logging.py`
- Modify: `tests/unit/test_logging.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_logging.py`:

```python
def test_uvicorn_error_logger_writes_to_the_same_file(tmp_path):
    log_file = str(tmp_path / "unify.log")
    configure_logging(level="INFO", log_file=log_file, log_format="text")
    logging.getLogger("uvicorn.error").error("boom from uvicorn")
    content = (tmp_path / "unify.log").read_text()
    assert "boom from uvicorn" in content


def test_uvicorn_access_logger_writes_to_the_same_file(tmp_path):
    log_file = str(tmp_path / "unify_access.log")
    configure_logging(level="INFO", log_file=log_file, log_format="text")
    logging.getLogger("uvicorn.access").info("127.0.0.1 GET /api/health 200")
    content = (tmp_path / "unify_access.log").read_text()
    assert "127.0.0.1 GET /api/health 200" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_logging.py -v -k unify`
Expected: FAIL — both new tests fail because `configure_logging()` never touches the `uvicorn`/`uvicorn.error`/`uvicorn.access` loggers today, so records emitted on them never reach the file (they propagate to Python's real root logger, which has no handler pointed at our file).

- [ ] **Step 3: Implement the unification**

In `etl_framework/utils/logging.py`, add this block at the end of `configure_logging()` (after `root.addHandler(file_handler)`):

```python
    # Route uvicorn's own logger (unhandled exceptions, request errors,
    # startup/shutdown/reload messages) through the same handlers, so
    # everything lands in one file instead of being silently lost to
    # whatever console the process happens to be attached to.
    for uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.addHandler(stream_handler)
        uvicorn_logger.addHandler(file_handler)
        uvicorn_logger.setLevel(root.level)
```

The full function should now read:

```python
def configure_logging(
    level: str = "INFO",
    log_file: str = "./logs/etl_framework.log",
    log_format: str = "text",
) -> None:
    root = logging.getLogger("etl_framework")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    context_filter = RunContextFilter()
    text_formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.addFilter(context_filter)
    stream_handler.setFormatter(text_formatter)
    root.addHandler(stream_handler)

    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    file_handler.addFilter(context_filter)
    if log_format == "json":
        try:
            from pythonjsonlogger import jsonlogger
            json_formatter = jsonlogger.JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(run_id)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%SZ",
                rename_fields={"levelname": "level", "asctime": "timestamp"},
            )
            file_handler.setFormatter(json_formatter)
        except ImportError:
            file_handler.setFormatter(text_formatter)
    else:
        file_handler.setFormatter(text_formatter)
    root.addHandler(file_handler)

    # Route uvicorn's own logger (unhandled exceptions, request errors,
    # startup/shutdown/reload messages) through the same handlers, so
    # everything lands in one file instead of being silently lost to
    # whatever console the process happens to be attached to.
    for uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.addHandler(stream_handler)
        uvicorn_logger.addHandler(file_handler)
        uvicorn_logger.setLevel(root.level)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_logging.py -v`
Expected: all tests in the file pass (the two new ones plus the five pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/utils/logging.py tests/unit/test_logging.py
git commit -m "fix: route uvicorn's logger through the app's rotating log file"
```

---

### Task 3: Add `GET /api/logs` endpoint

**Files:**
- Create: `api/routes/logs.py`
- Modify: `api/main.py`
- Create: `tests/unit/test_logs_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_logs_routes.py`:

```python
"""Tests for GET /api/logs — the server-wide log view (no run_id required)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository


@pytest.fixture
def client(monkeypatch, tmp_path):
    from api.main import app
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").mkdir()
        yield c


def _write_log(tmp_path, text):
    (tmp_path / "logs" / "etl_framework.log").write_text(text, encoding="utf-8")


def test_returns_404_when_log_file_missing(client):
    resp = client.get("/api/logs")
    assert resp.status_code == 404


def test_returns_all_events_with_no_filters(client, tmp_path):
    _write_log(tmp_path, (
        "2026-07-02 06:10:00 | INFO  |  | a | first\n"
        "2026-07-02 06:10:01 | INFO  |  | a | second\n"
    ))
    resp = client.get("/api/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_lines"] == 2
    assert body["run_id"] == ""


def test_filters_by_run_id(client, tmp_path):
    _write_log(tmp_path, (
        "2026-07-02 06:10:00 | INFO  | run-1 | a | first\n"
        "2026-07-02 06:10:01 | INFO  | run-2 | a | second\n"
    ))
    resp = client.get("/api/logs", params={"run_id": "run-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_lines"] == 1
    assert "run-1" in body["lines"][0]["text"]


def test_filters_by_level(client, tmp_path):
    _write_log(tmp_path, (
        "2026-07-02 06:10:00 | INFO  |  | a | first\n"
        "2026-07-02 06:10:01 | ERROR |  | a | boom\n"
    ))
    resp = client.get("/api/logs", params={"level": "ERROR"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_lines"] == 1
    assert "boom" in body["lines"][0]["text"]


def test_filters_by_search_query(client, tmp_path):
    _write_log(tmp_path, (
        "2026-07-02 06:10:00 | INFO  |  | a | needle here\n"
        "2026-07-02 06:10:01 | INFO  |  | a | nothing relevant\n"
    ))
    resp = client.get("/api/logs", params={"q": "needle"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_lines"] == 1
    assert "needle" in body["lines"][0]["text"]


def test_requires_auth(client):
    resp = client.get("/api/logs", headers={"Authorization": ""})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_logs_routes.py -v`
Expected: FAIL — `api/routes/logs.py` doesn't exist yet, so `/api/logs` isn't a registered route and every request returns 404 for the wrong reason (or the import fails at collection time).

- [ ] **Step 3: Implement the route**

Create `api/routes/logs.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.services.log_parser import parse_log_events, filter_log_events

router = APIRouter(tags=["logs"])


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

- [ ] **Step 4: Wire the router into the app**

In `api/main.py`, add the import alongside the other route imports (after the `contracts_routes` import on line 15):

```python
from api.routes import logs as logs_routes
```

And add the include after the `contracts_routes` registration (after line 54):

```python
app.include_router(logs_routes.router, prefix="/api/logs")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_logs_routes.py -v`
Expected: 6 passed.

- [ ] **Step 6: Run the full unit test suite to check for regressions**

Run: `.venv/Scripts/python.exe -m pytest tests/unit -q`
Expected: no new failures beyond whatever pre-existing baseline the suite had before this change.

- [ ] **Step 7: Commit**

```bash
git add api/routes/logs.py api/main.py tests/unit/test_logs_routes.py
git commit -m "feat: add GET /api/logs for server-wide log viewing without a run_id"
```

---

### Task 4: Add a global "Logs" nav tab in the frontend

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`

- [ ] **Step 1: Add new state fields**

In `frontend/app.js`, in the state object right after the existing `logFilterLevel: '',` line (around line 322, in the "Monitor"/logs block near `allLogEvents`), add:

```javascript
    allLogEvents: [],
    allLogEventsLoading: false,
    logFilterQuery: '',
    logFilterLevel: '',

    // -----------------------------------------------------------
    // Global Logs tab (server-wide, no run_id required)
    // -----------------------------------------------------------
    globalLogEvents: [],
    globalLogsLoading: false,
    globalLogFilterQuery: '',
    globalLogFilterLevel: '',
    globalLogRunId: '',
    globalLogsPollTimer: null,
```

- [ ] **Step 2: Add the tab entry**

In `frontend/app.js`, in the `tabs` array (line 134-143), add a new entry after `contracts`:

```javascript
    tabs: [
      { id: 'config',   label: '⚙ Config' },
      { id: 'jobs',     label: '▶ Launch' },
      { id: 'monitor',  label: '📡 Monitor' },
      { id: 'history',  label: '📋 History' },
      { id: 'adapters', label: '🔌 Adapters' },
      { id: 'reports',  label: '📊 Reports' },
      { id: 'compare',  label: '⇄ Compare' },
      { id: 'contracts', label: '\u{1F4CB} Contracts' },
      { id: 'logs',     label: '🪵 Logs' },
    ],
```

- [ ] **Step 3: Add the polling and data-loading methods**

In `frontend/app.js`, add these methods near `loadAllLogEvents()` (around line 2646):

```javascript
    async loadGlobalLogs() {
      const isFirstLoad = this.globalLogEvents.length === 0;
      if (isFirstLoad) this.globalLogsLoading = true;
      const params = new URLSearchParams({ limit: '1000' });
      if (this.globalLogRunId.trim()) params.set('run_id', this.globalLogRunId.trim());
      const logList = document.querySelector('.global-log-list');
      const wasAtBottom = logList
        ? logList.scrollHeight - logList.scrollTop - logList.clientHeight < 16
        : true;
      try {
        const data = await api('GET', `/api/logs?${params.toString()}`);
        this.globalLogEvents = data.lines || [];
        if (wasAtBottom) {
          this.$nextTick(() => {
            if (logList) logList.scrollTop = logList.scrollHeight;
          });
        }
      } catch (e) {
        if (isFirstLoad) this.toast('error', 'Failed to load logs', e.message);
        // Swallow errors on background poll ticks — the next poll recovers.
      } finally {
        this.globalLogsLoading = false;
      }
    },

    filteredGlobalLogEvents() {
      let events = this.globalLogEvents;
      if (this.globalLogFilterLevel) {
        events = events.filter(e => e.level === this.globalLogFilterLevel);
      }
      if (this.globalLogFilterQuery.trim()) {
        const q = this.globalLogFilterQuery.toLowerCase();
        events = events.filter(e => (e.text || '').toLowerCase().includes(q));
      }
      return events;
    },

    startGlobalLogsPolling() {
      this.loadGlobalLogs();
      if (this.globalLogsPollTimer) return;
      this.globalLogsPollTimer = setInterval(() => {
        if (document.visibilityState === 'visible') this.loadGlobalLogs();
      }, 5000);
    },

    stopGlobalLogsPolling() {
      if (this.globalLogsPollTimer) {
        clearInterval(this.globalLogsPollTimer);
        this.globalLogsPollTimer = null;
      }
    },
```

- [ ] **Step 4: Wire tab clicks to start/stop polling**

In `frontend/index.html`, update the tab button click handler (around line 25):

```html
<!-- before -->
          @click="currentView = tab.id; if (tab.id === 'contracts') loadContracts()"

<!-- after -->
          @click="currentView = tab.id; if (tab.id === 'contracts') loadContracts(); if (tab.id === 'logs') startGlobalLogsPolling(); else stopGlobalLogsPolling();"
```

- [ ] **Step 5: Add the Logs tab section to the page**

In `frontend/index.html`, add a new tab section. Place it right before the closing `</main>` tag (search for where the last tab's `</div>` sits just above `</main>`), following the same `x-show="currentView === '...'"` structure used by every other tab:

```html
<!-- ====================================================================
     TAB N - GLOBAL LOGS
     ==================================================================== -->
<div x-show="currentView === 'logs'" x-cloak>
  <div class="section-header">
    <div>
      <h2>Logs</h2>
      <p class="text-muted text-sm">Server-wide application log, auto-refreshing every 5 seconds.</p>
    </div>
  </div>

  <div class="space-y-4">
    <div class="card">
      <div class="flex flex-col gap-3">
        <input
          x-model="globalLogRunId"
          @change="loadGlobalLogs()"
          class="field-input"
          placeholder="Filter to a run ID… (optional)"
        />
        <input
          x-model="globalLogFilterQuery"
          class="field-input"
          placeholder="Search logs… (live)"
        />
        <div class="flex gap-2 flex-wrap items-center">
          <button class="level-chip" :class="globalLogFilterLevel === '' ? 'chip-active-ALL' : ''" @click="globalLogFilterLevel = ''">ALL</button>
          <button class="level-chip" :class="globalLogFilterLevel === 'ERROR' ? 'chip-active-ERROR' : ''" @click="globalLogFilterLevel = globalLogFilterLevel === 'ERROR' ? '' : 'ERROR'">ERROR</button>
          <button class="level-chip" :class="globalLogFilterLevel === 'WARNING' ? 'chip-active-WARNING' : ''" @click="globalLogFilterLevel = globalLogFilterLevel === 'WARNING' ? '' : 'WARNING'">WARN</button>
          <button class="level-chip" :class="globalLogFilterLevel === 'INFO' ? 'chip-active-INFO' : ''" @click="globalLogFilterLevel = globalLogFilterLevel === 'INFO' ? '' : 'INFO'">INFO</button>
          <button class="level-chip" :class="globalLogFilterLevel === 'DEBUG' ? 'chip-active-DEBUG' : ''" @click="globalLogFilterLevel = globalLogFilterLevel === 'DEBUG' ? '' : 'DEBUG'">DEBUG</button>
          <span class="text-muted text-xs ml-2" x-text="filteredGlobalLogEvents().length + ' / ' + globalLogEvents.length + ' events'"></span>
        </div>
      </div>
    </div>

    <template x-if="globalLogsLoading">
      <div class="card empty-state"><div class="empty-state-title">Loading logs…</div></div>
    </template>

    <template x-if="!globalLogsLoading && globalLogEvents.length === 0">
      <div class="card empty-state"><div class="empty-state-title">No log events yet.</div></div>
    </template>

    <template x-if="!globalLogsLoading && globalLogEvents.length > 0">
      <div class="card p-0 overflow-hidden">
        <div class="log-list global-log-list">
          <template x-if="filteredGlobalLogEvents().length === 0">
            <div class="empty-state"><div class="empty-state-title">No events match the current filter.</div></div>
          </template>
          <template x-for="line in filteredGlobalLogEvents()" :key="line.number">
            <div class="log-entry" :class="logLevelClass(line.level)">
              <div class="log-entry-meta">
                <span class="font-mono" x-text="'#' + line.number"></span>
                <span class="badge" x-text="line.level"></span>
              </div>
              <pre x-html="highlightMatch(line.text, globalLogFilterQuery)"></pre>
            </div>
          </template>
        </div>
      </div>
    </template>
  </div>
</div>
```

- [ ] **Step 6: Rebuild CSS bundle (in case Tailwind purges unused classes)**

Run: `npm run build:css`
Expected: exits 0, `frontend/vendor/tailwind.css` updated. (No new classes were introduced, but running this after any `index.html` change matching existing project convention avoids a stale-bundle surprise.)

- [ ] **Step 7: Manually verify in the browser**

1. Start the server: `.venv/Scripts/python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload`
2. Open `http://127.0.0.1:8000/` in a browser, set up an API token if prompted.
3. Click the new "🪵 Logs" tab. Confirm existing log lines appear (startup messages at minimum).
4. Trigger a deliberate error — e.g. hit a nonexistent run's endpoint: `curl http://127.0.0.1:8000/api/runs/does-not-exist/metrics -H "Authorization: Bearer <your token>"` — or simply let the app run for a bit and watch new lines appear without refreshing the page (within ~5 seconds).
5. Confirm the level chips (ERROR/WARN/INFO/DEBUG) and the search box filter the visible list correctly.
6. Confirm entering a `run_id` in the "Filter to a run ID" box narrows the list to just that run.
7. Switch to another tab and back — confirm the poll timer doesn't duplicate (only one set of log entries, not doubled) and that logs continue to refresh.

- [ ] **Step 8: Commit**

```bash
git add frontend/app.js frontend/index.html frontend/vendor/tailwind.css
git commit -m "feat: add global Logs tab with auto-refresh polling"
```

---

## Self-Review Notes

- **Spec coverage:** Logging unification → Task 2. New `/api/logs` endpoint → Task 3. Global Logs tab, auto-refresh, scroll-preserving behavior, optional run_id filter → Task 4. Parser regression coverage called out in the spec's testing section → Task 1. All spec sections have a corresponding task.
- **Type/name consistency checked:** `globalLogEvents`, `globalLogFilterLevel`, `globalLogFilterQuery`, `globalLogRunId`, `globalLogsPollTimer`, `loadGlobalLogs()`, `filteredGlobalLogEvents()`, `startGlobalLogsPolling()`, `stopGlobalLogsPolling()` are used consistently between the state declaration (Step 1), the tab-click wiring (Step 4), and the template (Step 5) — no naming drift.
- **No placeholders:** every step has complete, runnable code or an exact command with expected output.
