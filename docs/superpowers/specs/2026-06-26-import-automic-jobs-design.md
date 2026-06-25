# Import Automic Jobs — Design Spec

**Date:** 2026-06-26  
**Status:** Approved  

## Problem

The Adapters tab lets users look up and add one Automic job at a time. There is no way to import multiple Automic jobs in bulk, either from a file or by browsing the Automic API. The backend `POST /api/jobs/import` endpoint already handles bulk upserts but has no frontend surface.

## Goals

1. Allow users to upload a JSON or CSV file of Automic job definitions and bulk-import them into the job catalog.
2. Allow users to search the Automic API for jobs by filter pattern, select multiple results, and bulk-import them.
3. Both workflows live in the existing Adapters tab, following current UI patterns.

## Out of Scope

- Importing SAP BO jobs in bulk (separate concern).
- Scheduling imported jobs (handled by the existing Schedules feature).
- Automic job discovery beyond name/status (e.g. pulling full job metadata).

---

## Architecture

```
Adapters tab (existing)
├── SAP BO panel          (unchanged)
├── Automic Lookup panel  (unchanged)
├── [NEW] Import from File panel
│     └── client-side JSON/CSV parse → POST /api/jobs/import (existing)
└── [NEW] Browse & Import from Automic panel
      ├── GET  /api/adapters/automic/search   (new)
      └── POST /api/adapters/jobs/from-automic/bulk  (new)
```

CSV parsing is done client-side in JavaScript. No new backend endpoint is needed for file import — the browser parses the file and posts the result to the existing `/api/jobs/import` endpoint.

---

## Backend

### 1. `AutomicClient.search_jobs(filter: str) -> list[dict]`

New method on the existing `etl_framework/automic/client.py` class.

```python
def search_jobs(self, filter: str) -> list[dict]:
    url = f"{self._base_url}/api/v1/jobs?filter={filter}&limit=100"
    data = self._request("GET", url)
    return data.get("data", [])
```

Returns raw dicts from the Automic API. The route layer selects only the fields the frontend needs.

### 2. `AdapterService.search_automic_jobs(config_id, filter) -> list[AutomicJobSummary]`

New method on `api/services/adapter_service.py`. Builds a client from the config, calls `search_jobs`, maps results to `AutomicJobSummary`.

### 3. New Pydantic schemas (`api/schemas.py`)

```python
class AutomicJobSummary(BaseModel):
    name: str
    status: str   # raw Automic status, e.g. ENDED_OK

class AutomicBulkImportRequest(BaseModel):
    config_id: int
    job_names: list[str] = Field(min_length=1)
```

### 4. New routes (`api/routes/adapters.py`)

**Search:**
```
GET /api/adapters/automic/search?config_id=1&filter=ETL_*
→ list[AutomicJobSummary]
```

**Bulk import:**
```
POST /api/adapters/jobs/from-automic/bulk
Body: AutomicBulkImportRequest
→ list[JobDefinition]
```

The bulk import endpoint iterates `job_names`, upserts each as `job_type="automic_job"` with `params.job_name` set, logs to `AuditService` with `source: "automic_browse"`, and collects per-name errors. It returns the successfully created jobs; errors are included in a separate `errors` field.

```python
class AutomicBulkImportResponse(BaseModel):
    imported: list[JobDefinition]
    errors: dict[str, str]  # job_name -> error message
```

---

## Frontend

### "Import from File" panel

Collapsible card placed below the existing Automic Lookup panel in `frontend/index.html`. Alpine.js state in `frontend/app.js`.

**State variables:**
```js
fileImportOpen: false,
fileImportJobs: [],       // parsed preview rows
fileImportErrors: [],     // per-row validation errors
fileImportLoading: false,
```

**Flow:**
1. User selects `.json` or `.csv` file.
2. Client parses file:
   - JSON: `JSON.parse()`, expects array of objects.
   - CSV: split by newline, parse header row, map columns to `JobDefinition` fields.
3. Preview table shown: name, job_type, tags, status badge ("new" / "exists" — checked against `this.jobs`).
4. Row count badge + warning if any rows missing `name`.
5. "Import N jobs" button → `POST /api/jobs/import` → toast success/error → `loadJobs()`.

**CSV column mapping:**

| CSV column    | JobDefinition field     | Default             |
|---------------|-------------------------|---------------------|
| `name`        | `name`                  | required            |
| `job_type`    | `job_type`              | `automic_job`       |
| `job_name`    | `params.job_name`       | —                   |
| `run_id`      | `params.run_id`         | —                   |
| `tags`        | `tags` (split on `,`)  | `[]`                |
| `description` | `description`           | `""`                |

### "Browse & Import from Automic" panel

Collapsible card placed below the file import panel.

**State variables:**
```js
browseAutomicOpen: false,
browseAutomicConfigId: '',
browseAutomicFilter: '',
browseAutomicResults: [],
browseAutomicSelected: [],   // list of job names
browseAutomicLoading: false,
browseAutomicImporting: false,
```

**Flow:**
1. User selects a config and enters a filter string (min 1 char).
2. "Search" button → `GET /api/adapters/automic/search?config_id=X&filter=Y`.
3. Results table: checkbox per row, job name, Automic status badge, "Select all" toggle.
4. "Import Selected (N)" button (disabled when nothing selected) → `POST /api/adapters/jobs/from-automic/bulk`.
5. On response: toast "N imported, M failed"; if errors, list failed names; reload jobs.

---

## Error Handling

| Scenario | Handling |
|---|---|
| Malformed JSON file | Client-side try/catch; show parse error inline, block import |
| CSV missing `name` column | Flag rows in preview; block import until resolved |
| Duplicate job names in file | Upsert silently updates; preview shows "will update" badge |
| Automic API error on search | Inline error message, same style as existing lookup panel |
| Empty search results | Empty state message; no import button |
| Partial bulk import failure | `errors` field in response; toast "N imported, M failed"; list failed names |
| Automic config not configured | Browse panel disabled with message linking to Config tab |

---

## Audit Trail

Both import paths use the existing `AuditService`:
- File import: `source: "file_import"` via the existing `/jobs/import` route (already logs `source: "import"`)
- Automic browse: `source: "automic_browse"` logged in the new bulk endpoint

---

## Files Changed

| File | Change |
|---|---|
| `etl_framework/automic/client.py` | Add `search_jobs()` method |
| `api/services/adapter_service.py` | Add `search_automic_jobs()` method |
| `api/schemas.py` | Add `AutomicJobSummary`, `AutomicBulkImportRequest`, `AutomicBulkImportResponse` |
| `api/routes/adapters.py` | Add `GET /automic/search`, `POST /jobs/from-automic/bulk` |
| `frontend/app.js` | Add state + methods for both panels |
| `frontend/index.html` | Add two collapsible panels in Adapters tab |
| `tests/unit/test_adapters_routes.py` | Tests for new routes |
| `tests/unit/test_api.py` | Tests for new schemas |
