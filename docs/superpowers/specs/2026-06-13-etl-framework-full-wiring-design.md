# ETL Framework — Full Module Wiring + Rich UI Design

**Date:** 2026-06-13  
**Scope:** Big-bang rewrite wiring all `etl_framework` modules to the web GUI with a clean SaaS rich UI.

---

## 1. Goals

1. Wire every existing `etl_framework` module (`reconciliation`, `sap_bo`, `automic`, `reporting`, `runner`, `utils`) into the live request/response path.
2. Add a `use_live_connections` flag so real DB / SAP BO / Automic adapters activate on demand; simulation remains the default.
3. Expand the frontend from 4 tabs to **6 tabs** with slide-over drawers and a clean SaaS aesthetic (gradient stat cards, Chart.js charts).
4. Deliver a full Adapters integration hub (SAP BO report browser + Automic job lookup + push-to-job-catalog).

---

## 2. Architecture

### Repo layout changes

```
api/
  routes/
    adapters.py          NEW — /api/adapters router
  schemas.py             add use_live_connections, MismatchOut extensions, BODocOut, AutomicJobStatusOut, RunProgressOut
  services/
    adapter_service.py   NEW — thin bridge to BORestClient / AutomicClient
    run_executor.py      extend — live engine factory + bo_report/automic_job dispatch

frontend/
  index.html             rewrite — 6-tab nav, slide-over drawer scaffold, gradient card CSS
  app.js                 rewrite — all 6 views + drawer/toast logic
  styles.css             extend — gradient card, drawer, slide-over animation classes
```

`etl_framework/` — **no structural changes**; all modules are complete and consumed as-is.

### Request flow — live reconciliation run

```
POST /api/runs
  body: RunTrigger(use_live_connections=true, config_id=N, job_sequence=[...])
    → RunExecutor._build_engines()
        → reads SavedConfig credentials
        → creates sqlalchemy.create_engine (MSSQL/pyodbc) per env
    → RunExecutor._build_case() dispatches job_type:
        "reconciliation" → ReconciliationEngine (existing)
        "bo_report"      → BORestClient.fetch_report_data() as source DataFrame
        "automic_job"    → AutomicClient.get_status_by_job_name() → maps to PASSED/FAILED
    → persists TestResult + MismatchDetail rows
```

### Request flow — adapters tab

```
GET /api/adapters/sap-bo/documents?config_id=N
  → AdapterService.list_bo_documents(env_config)
  → BORestClient.authenticate() + list
  → returns BODocOut[]

POST /api/adapters/jobs/from-bo-report
  body: { config_id, doc_id, title }
  → creates SavedJob(job_type="bo_report", params={report_id: doc_id})
  → returns JobDefinition
```

---

## 3. Backend Wiring

### 3.1 `api/schemas.py` additions

| Schema | Change |
|---|---|
| `RunSettings` | + `use_live_connections: bool = False` |
| `RunProgressOut` | NEW: `run_id, status, total, completed, current_job, percent` |
| `BODocOut` | NEW: `id, title, folder` |
| `AutomicJobStatusOut` | NEW: `job_name, run_id, status, environment, checked_at` |
| `AdapterTestOut` | NEW: `ok: bool, message: str, latency_ms: int` |

### 3.2 `api/routes/adapters.py` — mounted at `/api/adapters`

| Method | Path | Description |
|---|---|---|
| `POST` | `/sap-bo/test` | Test SAP BO connection; body: `{config_id}` |
| `GET` | `/sap-bo/documents` | List BO documents; query: `?config_id=N` |
| `POST` | `/automic/lookup` | Look up job by name or run_id; body: `{config_id, identifier, id_type}` |
| `GET` | `/automic/recent` | Returns last 10 Automic lookups stored in client `sessionStorage` (no server state); endpoint omitted — handled client-side only |
| `POST` | `/jobs/from-bo-report` | Create `bo_report` SavedJob from BO doc; body: `{config_id, doc_id, title}` |
| `POST` | `/jobs/from-automic` | Create `automic_job` SavedJob; body: `{config_id, job_name}` |

### 3.3 `api/routes/runs.py` additions

| Method | Path | Description |
|---|---|---|
| `GET` | `/{run_id}/progress` | Lightweight polling: status + percent complete |
| `GET` | `/{run_id}/results/{result_id}/mismatches` | Paginated `MismatchDetail` rows for a test result |

### 3.4 `api/services/adapter_service.py`

```python
class AdapterService:
    def __init__(self, config_repo: ConfigRepository): ...
    def _get_env_config(self, config_id: int) -> EnvironmentConfig: ...
    def test_bo_connection(self, config_id: int) -> AdapterTestOut: ...
    def list_bo_documents(self, config_id: int) -> list[BODocOut]: ...
    def lookup_automic_job(self, config_id: int, identifier: str, id_type: str) -> AutomicJobStatusOut: ...
    def list_automic_jobs(self, config_id: int) -> list[AutomicJobStatusOut]: ...
```

### 3.5 `api/services/run_executor.py` changes

**`_build_engines()`** — checks `run_settings.use_live_connections`:
- `False` → `DataFrameQueryEngine` (existing mock, unchanged)
- `True` → builds `sqlalchemy.create_engine` from `SavedConfig` credentials using pyodbc connection string; returns a thin `SQLAlchemyQueryEngine` wrapper that implements `execute_query(query, params) -> DataFrame`

**`_build_case()` dispatch on `job.job_type`:**
- `"reconciliation"` → existing `ReconciliationEngine` path (unchanged)
- `"bo_report"` → `BORestClient(env_config).fetch_report_data(job.params["report_id"])` returns source DataFrame; if `target_report_id` in params, fetches target too; otherwise uses target DB engine; runs through `ReconciliationEngine` with `key_columns`
- `"automic_job"` → `AutomicJobRunner(env_config).run_by_name(job.params["job_name"])` returns `JobStatus`; maps `ENDED_OK → PASSED`, `ENDED_NOT_OK → FAILED`, `ACTIVE/WAITING → RUNNING`; persists as `TestResult` with no row counts

**New `SQLAlchemyQueryEngine`** (small class in `run_executor.py`):
```python
class SQLAlchemyQueryEngine:
    def __init__(self, env_name: str, engine): ...
    def execute_query(self, query: str, params=None) -> pd.DataFrame:
        return pd.read_sql(query, self._engine, params=params)
```

---

## 4. Frontend Rewrite

### 4.1 Navigation

6-tab sticky top nav replacing the current 4-tab bar:

```
⚡ ETL Framework  |  ⚙ Config  ▶ Launch  📡 Monitor  📋 History  🔌 Adapters  📄 Reports  |  ● status
```

### 4.2 Visual system (SaaS aesthetic)

- **Gradient stat cards** — 5 colours: indigo (total), green (passed), red (failed), amber (slow), sky (pass rate). `linear-gradient(135deg, …)` white text.
- **Chart.js** — donut chart on run detail (passed/failed/slow/error segments); bar chart on History overview (pass rate over last N runs).
- **Slide-over drawers** — right-edge panel (width 580px), overlay backdrop, CSS `transform: translateX(100%)` → `translateX(0)` transition 250ms. Three drawer types: Mismatch Detail, Metrics, Log Viewer.
- **Progress bar** — gradient indigo-purple strip on active runs and run detail header.
- **Toast notifications** — bottom-right, auto-dismiss 4s: launch success, adapter connection result, job added to catalog.

### 4.3 Tab: Config (enhanced)

- Existing config list + modal kept.
- Modal gains two new collapsible sections: **SAP BO Settings** (`bo_url`, `bo_user`, `bo_password`, `bo_timeout`) and **Automic Settings** (`automic_url`, `automic_user`, `automic_password`, `automic_timeout`, `automic_max_retries`).
- "Validate Configuration" button now validates the full `EnvironmentConfig` Pydantic model (already wired via `/api/configs/validate`).

### 4.4 Tab: Launch (enhanced)

- Launch settings panel gains `use_live_connections` toggle.
- Job type badge shown per job in catalog: `reconciliation` (blue), `bo_report` (purple), `automic_job` (orange).

### 4.5 Tab: Monitor (enhanced)

- Active run cards gain real-time progress bar polling `/api/runs/{id}/progress` every 3s.
- `current_job` label shown below progress bar while RUNNING.

### 4.6 Tab: History (new rich layout)

- **Summary stat cards** (5 gradient cards across full run history aggregate).
- **Bar chart** — pass rate over last 10 runs (Chart.js).
- **Run list table** with clickable rows expanding to run detail inline.
- **Run detail** — progress bar, test results table, links to Report/Metrics/Logs.
- **Clicking a test row** → opens Mismatch slide-over drawer:
  - Table: key values | column | source value | target value | mismatch type.
  - Paginated: shows first 100, "Load more" fetches next page.
- **Metrics drawer** — fetches `/api/runs/{id}/metrics` JSON, renders key fields as stat cards.
- **Log drawer** — fetches `/api/runs/{id}/logs`, renders in monospace scrollable panel.

### 4.7 Tab: Adapters (new)

Two-column layout:

**SAP BO panel:**
- Config selector dropdown (from saved configs).
- "Test Connection" → `POST /api/adapters/sap-bo/test` → green/red badge.
- "Browse Documents" → `GET /api/adapters/sap-bo/documents` → scrollable list with folder path and "＋ Add Job" button per document.
- "+ Add Job" → opens a small inline form with a `key_columns` input (required) before calling `POST /api/adapters/jobs/from-bo-report` → toast "Job added to catalog". `key_columns` is passed in the request body and stored on the `SavedJob`.

**Automic panel:**
- Config selector + job name / run ID input.
- "Lookup" → `POST /api/adapters/automic/lookup` → status card (job name, status badge, checked_at).
- "Add to Jobs" → `POST /api/adapters/jobs/from-automic` → toast "Job added".
- Recent lookups stored in `sessionStorage` (client-side); shown as a compact history list below the lookup form — no server endpoint needed.

### 4.8 Tab: Reports (new)

- List of generated HTML reports from `/api/runs/{id}/artifacts` (type=report).
- "View" → opens report in embedded `<iframe>` within the tab (full width, 80vh height).
- "Download" → direct link to `/api/runs/{id}/report` with `Content-Disposition: attachment`.

---

## 5. Error Handling

- All new API routes return `FrameworkErrorOut` on adapter failures (BOAPIError, AutomicAPIError, AutomicTimeoutError).
- Frontend catches API errors and shows red inline error banners (not alert dialogs).
- `use_live_connections=true` with missing credentials → 422 with clear field-level error from Pydantic.
- Drawer fetches that 404 → show "No data available" empty state inside drawer (not a crash).

---

## 6. Testing Considerations

- Existing unit tests for reconciliation engine, repository, config loader — no changes needed.
- `AdapterService` methods are thin wrappers; tested via existing `BORestClient`/`AutomicClient` unit tests.
- New routes (`/adapters/*`, `/runs/{id}/progress`, `/runs/{id}/results/{id}/mismatches`) need integration test stubs added to `tests/integration/test_api_frontend_smoke.py`.
- `SQLAlchemyQueryEngine` needs a unit test with an in-memory SQLite engine.

---

## 7. Out of Scope

- Real-time WebSocket streaming (polling covers the monitoring need).
- Authentication / user management.
- Multi-tenant isolation.
- Dark mode toggle (B aesthetic is light-mode; dark mode is a future enhancement).
