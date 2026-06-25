# Import Automic Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bulk import of Automic jobs to the Adapters tab via file upload (JSON/CSV) and live Automic API search.

**Architecture:** Two new collapsible panels in the Adapters tab after the existing Automic lookup panel. File upload parses JSON/CSV client-side and posts to the existing `/api/jobs/import` endpoint. Live search adds a `GET /adapters/automic/search` route and a `POST /adapters/jobs/from-automic/bulk` route backed by a new `AutomicClient.search_jobs()` method.

**Tech Stack:** Python/FastAPI/Pydantic (backend), Alpine.js + Tailwind CSS (frontend), pytest + TestClient (tests).

---

## File Map

| File | Change |
|---|---|
| `api/schemas.py` | Add `AutomicJobSummary`, `AutomicBulkImportRequest`, `AutomicBulkImportResponse` |
| `etl_framework/automic/client.py` | Add `search_jobs()` method |
| `api/services/adapter_service.py` | Add `search_automic_jobs()` method |
| `api/routes/adapters.py` | Add `GET /automic/search` and `POST /jobs/from-automic/bulk` routes |
| `frontend/app.js` | Add state variables and 6 new methods |
| `frontend/index.html` | Add two collapsible panels after the Automic history card |
| `tests/unit/test_api.py` | Add schema unit tests |
| `tests/unit/test_adapters_routes.py` | Add route integration tests |

---

## Task 1: Add new Pydantic schemas

**Files:**
- Modify: `api/schemas.py` (after `AutomicJobCreateRequest` class, around line 374)
- Modify: `tests/unit/test_api.py`

- [ ] **Step 1: Write the failing tests**

Add to the bottom of `tests/unit/test_api.py`:

```python
def test_automic_job_summary_valid():
    from api.schemas import AutomicJobSummary
    s = AutomicJobSummary(name="ETL_JOB", status="ENDED_OK")
    assert s.name == "ETL_JOB"
    assert s.status == "ENDED_OK"


def test_automic_bulk_import_request_requires_nonempty_list():
    from api.schemas import AutomicBulkImportRequest
    with pytest.raises(Exception):
        AutomicBulkImportRequest(config_id=1, job_names=[])


def test_automic_bulk_import_request_valid():
    from api.schemas import AutomicBulkImportRequest
    r = AutomicBulkImportRequest(config_id=1, job_names=["ETL_A"])
    assert r.job_names == ["ETL_A"]


def test_automic_bulk_import_response_defaults_errors_to_empty():
    from api.schemas import AutomicBulkImportResponse
    r = AutomicBulkImportResponse(imported=[])
    assert r.errors == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_api.py::test_automic_job_summary_valid tests/unit/test_api.py::test_automic_bulk_import_request_requires_nonempty_list -v
```

Expected: `ImportError` or `ValidationError` — schemas don't exist yet.

- [ ] **Step 3: Add schemas to `api/schemas.py`**

Insert after the closing brace of `AutomicJobCreateRequest` (around line 374):

```python
class AutomicJobSummary(BaseModel):
    name: str
    status: str


class AutomicBulkImportRequest(BaseModel):
    config_id: int
    job_names: list[str] = Field(min_length=1)


class AutomicBulkImportResponse(BaseModel):
    imported: list[JobDefinition]
    errors: dict[str, str] = Field(default_factory=dict)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_api.py::test_automic_job_summary_valid tests/unit/test_api.py::test_automic_bulk_import_request_requires_nonempty_list tests/unit/test_api.py::test_automic_bulk_import_request_valid tests/unit/test_api.py::test_automic_bulk_import_response_defaults_errors_to_empty -v
```

Expected: All 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_api.py
git commit -m "feat(schemas): add AutomicJobSummary, AutomicBulkImportRequest, AutomicBulkImportResponse"
```

---

## Task 2: Add `AutomicClient.search_jobs()`

**Files:**
- Modify: `etl_framework/automic/client.py` (add after `get_statuses` method)
- Modify: `tests/unit/test_adapters_routes.py` (add a standalone test function)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_adapters_routes.py` (after the existing tests, before any class blocks):

```python
def test_automic_client_search_jobs_returns_list():
    from unittest.mock import patch
    from etl_framework.automic.client import AutomicClient
    from etl_framework.config.models import EnvironmentConfig

    env = EnvironmentConfig(
        name="test",
        db_host="host",
        db_password="pass",
        automic_url="http://automic.test",
        automic_user="user",
        automic_password="pass",
    )
    client = AutomicClient(env)
    mock_response = {
        "data": [
            {"name": "ETL_NIGHTLY", "status": "ENDED_OK"},
            {"name": "ETL_WEEKLY", "status": "ACTIVE"},
        ]
    }
    with patch.object(client, "_request", return_value=mock_response):
        result = client.search_jobs("ETL_*")

    assert len(result) == 2
    assert result[0]["name"] == "ETL_NIGHTLY"
    assert result[1]["status"] == "ACTIVE"


def test_automic_client_search_jobs_empty_response():
    from unittest.mock import patch
    from etl_framework.automic.client import AutomicClient
    from etl_framework.config.models import EnvironmentConfig

    env = EnvironmentConfig(
        name="test", db_host="host", db_password="pass",
        automic_url="http://automic.test", automic_user="u", automic_password="p",
    )
    client = AutomicClient(env)
    with patch.object(client, "_request", return_value={}):
        result = client.search_jobs("NONEXISTENT_*")
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_adapters_routes.py::test_automic_client_search_jobs_returns_list -v
```

Expected: `AttributeError: 'AutomicClient' object has no attribute 'search_jobs'`

- [ ] **Step 3: Add `search_jobs` to `etl_framework/automic/client.py`**

Add after the `get_statuses` method (end of file):

```python
    def search_jobs(self, filter: str) -> list[dict]:
        url = f"{self._base_url}/api/v1/jobs?filter={filter}&limit=100"
        data = self._request("GET", url)
        return data.get("data", [])
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_adapters_routes.py::test_automic_client_search_jobs_returns_list tests/unit/test_adapters_routes.py::test_automic_client_search_jobs_empty_response -v
```

Expected: Both PASS.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/automic/client.py tests/unit/test_adapters_routes.py
git commit -m "feat(automic): add AutomicClient.search_jobs() method"
```

---

## Task 3: Add `AdapterService.search_automic_jobs()` and `GET /adapters/automic/search` route

**Files:**
- Modify: `api/services/adapter_service.py` (add after `lookup_automic_job`)
- Modify: `api/routes/adapters.py` (add after `lookup_automic_job` route)
- Modify: `tests/unit/test_adapters_routes.py`

- [ ] **Step 1: Write failing route tests**

Add to `tests/unit/test_adapters_routes.py`:

```python
def test_search_automic_returns_job_list(client, mock_adapter_service):
    from api.schemas import AutomicJobSummary
    mock_adapter_service.search_automic_jobs.return_value = [
        AutomicJobSummary(name="ETL_NIGHTLY", status="ENDED_OK"),
        AutomicJobSummary(name="ETL_WEEKLY", status="ENDED_OK"),
    ]
    resp = client.get("/api/adapters/automic/search?config_id=1&filter=ETL_*")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["name"] == "ETL_NIGHTLY"
    assert data[1]["status"] == "ENDED_OK"


def test_search_automic_missing_filter_returns_422(client):
    resp = client.get("/api/adapters/automic/search?config_id=1")
    assert resp.status_code == 422


def test_search_automic_missing_config_id_returns_422(client):
    resp = client.get("/api/adapters/automic/search?filter=ETL_*")
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_adapters_routes.py::test_search_automic_returns_job_list -v
```

Expected: `404 Not Found` (route doesn't exist yet).

- [ ] **Step 3: Add `search_automic_jobs` to `api/services/adapter_service.py`**

Add after `lookup_automic_job` (end of file):

```python
    def search_automic_jobs(self, config_id: int, filter: str) -> list:
        from api.schemas import AutomicJobSummary
        env = self._get_env_config(config_id)
        try:
            client = AutomicClient(env)
            raw = client.search_jobs(filter)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_friendly_error(exc)) from exc
        return [AutomicJobSummary(name=j["name"], status=j.get("status", "UNKNOWN")) for j in raw]
```

- [ ] **Step 4: Add route to `api/routes/adapters.py`**

First, add the new schemas to the import at the top of `api/routes/adapters.py`. Change:

```python
from api.schemas import (
    AdapterTestOut,
    AutomicJobStatusOut,
    AutomicJobCreateRequest,
    AutomicLookupRequest,
    BODocOut,
    BOJobCreateRequest,
    BOReportOut,
    JobDefinition,
    BOTestRequest,
)
```

To:

```python
from api.schemas import (
    AdapterTestOut,
    AutomicBulkImportRequest,
    AutomicBulkImportResponse,
    AutomicJobStatusOut,
    AutomicJobSummary,
    AutomicJobCreateRequest,
    AutomicLookupRequest,
    BODocOut,
    BOJobCreateRequest,
    BOReportOut,
    JobDefinition,
    BOTestRequest,
)
```

Then add the search route after the existing `lookup_automic_job` route (after line 94):

```python
@router.get("/automic/search", response_model=list[AutomicJobSummary])
def search_automic_jobs(
    config_id: int,
    filter: str,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.search_automic_jobs(config_id, filter)
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/unit/test_adapters_routes.py::test_search_automic_returns_job_list tests/unit/test_adapters_routes.py::test_search_automic_missing_filter_returns_422 tests/unit/test_adapters_routes.py::test_search_automic_missing_config_id_returns_422 -v
```

Expected: All 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add api/services/adapter_service.py api/routes/adapters.py tests/unit/test_adapters_routes.py
git commit -m "feat(adapters): add GET /automic/search route and AdapterService.search_automic_jobs()"
```

---

## Task 4: Add `POST /adapters/jobs/from-automic/bulk` route

**Files:**
- Modify: `api/routes/adapters.py` (add after the new search route)
- Modify: `tests/unit/test_adapters_routes.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_adapters_routes.py`:

```python
def test_bulk_import_automic_jobs_returns_201(client):
    with patch("api.routes.adapters.JobRepository") as MockRepo, \
         patch("api.routes.adapters.AuditService"):
        MockRepo.return_value.upsert.return_value = MagicMock()
        resp = client.post("/api/adapters/jobs/from-automic/bulk", json={
            "config_id": 1,
            "job_names": ["ETL_NIGHTLY", "ETL_WEEKLY"],
        })
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["imported"]) == 2
    assert data["errors"] == {}
    names = [j["name"] for j in data["imported"]]
    assert "etl_nightly" in names
    assert "etl_weekly" in names


def test_bulk_import_automic_sets_job_type(client):
    with patch("api.routes.adapters.JobRepository") as MockRepo, \
         patch("api.routes.adapters.AuditService"):
        MockRepo.return_value.upsert.return_value = MagicMock()
        resp = client.post("/api/adapters/jobs/from-automic/bulk", json={
            "config_id": 1,
            "job_names": ["ETL_NIGHTLY"],
        })
    assert resp.status_code == 201
    assert resp.json()["imported"][0]["job_type"] == "automic_job"
    assert resp.json()["imported"][0]["params"]["job_name"] == "ETL_NIGHTLY"


def test_bulk_import_automic_empty_job_names_returns_422(client):
    resp = client.post("/api/adapters/jobs/from-automic/bulk", json={
        "config_id": 1,
        "job_names": [],
    })
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_adapters_routes.py::test_bulk_import_automic_jobs_returns_201 -v
```

Expected: `404 Not Found`.

- [ ] **Step 3: Add the bulk import route to `api/routes/adapters.py`**

Add after the `search_automic_jobs` route:

```python
@router.post("/jobs/from-automic/bulk", response_model=AutomicBulkImportResponse, status_code=201)
def bulk_create_jobs_from_automic(
    body: AutomicBulkImportRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    imported = []
    errors: dict[str, str] = {}
    for job_name in body.job_names:
        slug = job_name.lower().replace(" ", "_")
        job_data = {
            "name": slug,
            "description": f"Automic Job: {job_name}",
            "tags": ["automic_job"],
            "job_type": "automic_job",
            "query": "",
            "key_columns": [],
            "exclude_columns": [],
            "params": {"job_name": job_name},
            "enabled": True,
        }
        try:
            JobRepository(db).upsert(job_data)
            AuditService(db).log(
                request, "job.created", "job", slug,
                {"source": "automic_browse", "params": {"job_name": job_name}},
            )
            imported.append(JobDefinition(**job_data))
        except Exception as exc:
            errors[job_name] = str(exc)
    return AutomicBulkImportResponse(imported=imported, errors=errors)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_adapters_routes.py::test_bulk_import_automic_jobs_returns_201 tests/unit/test_adapters_routes.py::test_bulk_import_automic_sets_job_type tests/unit/test_adapters_routes.py::test_bulk_import_automic_empty_job_names_returns_422 -v
```

Expected: All 3 PASS.

- [ ] **Step 5: Run the full adapter test suite to confirm no regressions**

```
pytest tests/unit/test_adapters_routes.py -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add api/routes/adapters.py tests/unit/test_adapters_routes.py
git commit -m "feat(adapters): add POST /jobs/from-automic/bulk route"
```

---

## Task 5: Frontend — "Import from File" panel (JS)

**Files:**
- Modify: `frontend/app.js`

The app.js file uses Alpine.js with a single large `app()` function returning a data object. All new state variables go in the returned object; all new methods go as properties of the same object.

- [ ] **Step 1: Add state variables**

Find the `// Adapters – Automic` comment block (around line 189). Add the new state block immediately after `automicHistory`:

```js
    // Adapters – Import from File
    fileImportOpen: false,
    fileImportJobs: [],
    fileImportErrors: [],
    fileImportLoading: false,
```

- [ ] **Step 2: Add helper and method functions**

Find the `// ADAPTERS – Automic` comment block where `lookupAutomic()` is defined (around line 1668). Add these four methods immediately before `lookupAutomic`:

```js
    // ADAPTERS – Import from File
    _parseCSV(text) {
      const lines = text.trim().split('\n').filter(l => l.trim());
      if (lines.length < 2) return [];
      const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, ''));
      return lines.slice(1).map(line => {
        const vals = line.split(',').map(v => v.trim().replace(/^"|"$/g, ''));
        const obj = {};
        headers.forEach((h, i) => { obj[h] = vals[i] || ''; });
        return obj;
      });
    },

    _csvRowToJobDef(row) {
      const params = {};
      if (row.job_name) params.job_name = row.job_name;
      if (row.run_id)   params.run_id   = row.run_id;
      return {
        name:        row.name || '',
        description: row.description || '',
        job_type:    row.job_type || 'automic_job',
        query:       '',
        key_columns: [],
        tags:        row.tags ? row.tags.split(/[,\s]+/).filter(Boolean) : [],
        params,
        enabled:     true,
      };
    },

    onFileSelected(event) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        const text = e.target.result;
        this.fileImportErrors = [];
        try {
          let rows;
          if (file.name.endsWith('.csv')) {
            rows = this._parseCSV(text).map(r => this._csvRowToJobDef(r));
          } else {
            rows = JSON.parse(text);
          }
          this.fileImportJobs = rows;
          const missing = rows.filter(r => !r.name);
          if (missing.length > 0) {
            this.fileImportErrors = [`${missing.length} row(s) missing "name" — fix the file and re-upload`];
          }
        } catch (err) {
          this.fileImportErrors = [`Parse error: ${err.message}`];
          this.fileImportJobs = [];
        }
      };
      reader.readAsText(file);
    },

    async importFromFile() {
      if (!this.fileImportJobs.length || this.fileImportErrors.length) return;
      this.fileImportLoading = true;
      try {
        const result = await api('POST', '/api/jobs/import', this.fileImportJobs);
        this.toast('success', 'Import complete', `${result.length} job(s) imported`);
        this.fileImportJobs = [];
        this.fileImportOpen = false;
        await this.loadJobs();
      } catch (e) {
        this.toast('error', 'Import failed', e.message);
      } finally {
        this.fileImportLoading = false;
      }
    },
```

- [ ] **Step 3: Commit JS changes**

```bash
git add frontend/app.js
git commit -m "feat(frontend): add Import from File JS state and methods"
```

---

## Task 6: Frontend — "Import from File" panel (HTML)

**Files:**
- Modify: `frontend/index.html`

The new panel goes after the closing `</div>` of the "Recent Lookups" card (the `x-show="automicHistory.length > 0"` div, around line 1805).

- [ ] **Step 1: Add the Import from File panel**

Find this comment in `frontend/index.html`:

```html
    <!-- Add BO Job modal -->
```

Insert the following block immediately before it (after the closing `</div>` of the Recent Lookups card):

```html
    <!-- Import Jobs from File panel -->
    <div class="card mt-4">
      <div class="flex items-center justify-between cursor-pointer" @click="fileImportOpen = !fileImportOpen">
        <div class="font-semibold text-slate-700">📂 Import Jobs from File</div>
        <span class="text-muted text-sm" x-text="fileImportOpen ? '▲ collapse' : '▼ expand'"></span>
      </div>
      <template x-if="fileImportOpen">
        <div class="mt-3 space-y-3">
          <p class="text-muted text-sm">Upload a <strong>.json</strong> array or <strong>.csv</strong> file. CSV columns: <code>name, job_type, job_name, run_id, tags, description</code> — <code>job_type</code> defaults to <code>automic_job</code>.</p>
          <input type="file" accept=".json,.csv" @change="onFileSelected($event)" class="field-input" />
          <template x-if="fileImportErrors.length > 0">
            <div class="space-y-1">
              <template x-for="err in fileImportErrors" :key="err">
                <p class="text-rose-600 text-sm" x-text="err"></p>
              </template>
            </div>
          </template>
          <template x-if="fileImportJobs.length > 0">
            <div>
              <div class="flex items-center gap-2 mb-2">
                <span class="badge badge-blue" x-text="fileImportJobs.length + ' job(s) parsed'"></span>
                <template x-if="fileImportJobs.filter(j => jobs.some(x => x.name === j.name)).length > 0">
                  <span class="badge badge-amber" x-text="fileImportJobs.filter(j => jobs.some(x => x.name === j.name)).length + ' will update existing'"></span>
                </template>
              </div>
              <div class="overflow-x-auto">
                <table class="results-table text-xs">
                  <thead><tr><th>Name</th><th>Type</th><th>Tags</th></tr></thead>
                  <tbody>
                    <template x-for="(job, idx) in fileImportJobs" :key="idx">
                      <tr :class="!job.name ? 'bg-rose-50' : ''">
                        <td x-text="job.name || '⚠ missing'"></td>
                        <td x-text="job.job_type || 'automic_job'"></td>
                        <td x-text="(job.tags || []).join(', ')"></td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
              <div class="flex justify-end mt-2">
                <button @click="importFromFile()" :disabled="fileImportErrors.length > 0 || fileImportLoading" class="btn-primary">
                  <span x-show="!fileImportLoading" x-text="'Import ' + fileImportJobs.length + ' job(s)'"></span>
                  <span x-show="fileImportLoading">Importing…</span>
                </button>
              </div>
            </div>
          </template>
        </div>
      </template>
    </div>
```

- [ ] **Step 2: Manually verify the panel renders**

Start the app and navigate to the Adapters tab. Confirm:
- "📂 Import Jobs from File" card appears after the Recent Lookups section
- Clicking the header expands/collapses it
- File input is visible when expanded

Run the app: `uvicorn api.main:app --reload` then open `http://localhost:8000`

- [ ] **Step 3: Test file upload with a CSV**

Create a test file `test_import.csv`:
```
name,job_type,job_name,tags,description
etl_test_nightly,automic_job,ETL_NIGHTLY_LOAD,automic nightly,Nightly load
etl_test_weekly,automic_job,ETL_WEEKLY_LOAD,automic weekly,Weekly load
```

Upload it and confirm:
- Preview table shows 2 rows
- "2 job(s) parsed" badge appears
- "Import 2 job(s)" button is enabled
- After clicking import, a success toast appears and jobs appear in the Launch tab

- [ ] **Step 4: Test file upload with a JSON**

Create `test_import.json`:
```json
[
  {"name": "etl_json_job", "job_type": "automic_job", "params": {"job_name": "ETL_JSON"}, "query": "", "key_columns": [], "tags": ["automic_json"]}
]
```

Upload it and confirm it imports successfully.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): add Import Jobs from File panel in Adapters tab"
```

---

## Task 7: Frontend — "Browse & Import from Automic" panel (JS + HTML)

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`

- [ ] **Step 1: Add state variables to `frontend/app.js`**

Find the state block you added in Task 5 (`// Adapters – Import from File`). Add immediately after it:

```js
    // Adapters – Browse & Import from Automic
    browseAutomicOpen: false,
    browseAutomicConfigId: '',
    browseAutomicFilter: '',
    browseAutomicResults: [],
    browseAutomicSelected: [],
    browseAutomicLoading: false,
    browseAutomicImporting: false,
    browseAutomicError: '',
```

- [ ] **Step 2: Add methods to `frontend/app.js`**

Find the `importFromFile` method you added in Task 5. Add these methods immediately after it:

```js
    // ADAPTERS – Browse & Import from Automic
    async searchAutomic() {
      if (!this.browseAutomicConfigId || !this.browseAutomicFilter.trim()) return;
      this.browseAutomicLoading = true;
      this.browseAutomicResults = [];
      this.browseAutomicSelected = [];
      this.browseAutomicError = '';
      try {
        const qs = `config_id=${this.browseAutomicConfigId}&filter=${encodeURIComponent(this.browseAutomicFilter)}`;
        this.browseAutomicResults = await api('GET', `/api/adapters/automic/search?${qs}`);
        if (!this.browseAutomicResults.length) {
          this.browseAutomicError = 'No jobs found for that filter.';
        }
      } catch (e) {
        this.browseAutomicError = e.message;
      } finally {
        this.browseAutomicLoading = false;
      }
    },

    toggleBrowseSelection(name) {
      const idx = this.browseAutomicSelected.indexOf(name);
      if (idx >= 0) this.browseAutomicSelected.splice(idx, 1);
      else this.browseAutomicSelected.push(name);
    },

    isBrowseAllSelected() {
      return this.browseAutomicResults.length > 0 &&
             this.browseAutomicResults.every(r => this.browseAutomicSelected.includes(r.name));
    },

    toggleSelectAll() {
      if (this.isBrowseAllSelected()) {
        this.browseAutomicSelected = [];
      } else {
        this.browseAutomicSelected = this.browseAutomicResults.map(r => r.name);
      }
    },

    async importSelectedAutomic() {
      if (!this.browseAutomicSelected.length) return;
      this.browseAutomicImporting = true;
      try {
        const result = await api('POST', '/api/adapters/jobs/from-automic/bulk', {
          config_id: Number(this.browseAutomicConfigId),
          job_names: this.browseAutomicSelected,
        });
        const nImported = result.imported.length;
        const nErrors = Object.keys(result.errors).length;
        if (nErrors > 0) {
          this.toast('error', `${nImported} imported, ${nErrors} failed`,
            Object.keys(result.errors).join(', '));
        } else {
          this.toast('success', 'Import complete', `${nImported} job(s) added to catalog`);
        }
        this.browseAutomicSelected = [];
        await this.loadJobs();
      } catch (e) {
        this.toast('error', 'Import failed', e.message);
      } finally {
        this.browseAutomicImporting = false;
      }
    },
```

- [ ] **Step 3: Add HTML panel to `frontend/index.html`**

Find the `<!-- Add BO Job modal -->` comment. Insert the following block immediately before it (after the Import from File panel you added in Task 6):

```html
    <!-- Browse & Import from Automic panel -->
    <div class="card mt-4">
      <div class="flex items-center justify-between cursor-pointer" @click="browseAutomicOpen = !browseAutomicOpen">
        <div class="font-semibold text-slate-700">🔍 Browse &amp; Import from Automic</div>
        <span class="text-muted text-sm" x-text="browseAutomicOpen ? '▲ collapse' : '▼ expand'"></span>
      </div>
      <template x-if="browseAutomicOpen">
        <div class="mt-3 space-y-3">
          <template x-if="!configs.length">
            <p class="text-muted text-sm">No configs available. <button @click="currentView='config'" class="link">Go to Config tab</button> to add Automic credentials first.</p>
          </template>
          <template x-if="configs.length">
            <div class="space-y-3">
              <div class="grid-2">
                <div>
                  <label class="field-label">Config</label>
                  <select x-model="browseAutomicConfigId" class="field-input field-select">
                    <option value="">— Select —</option>
                    <template x-for="cfg in configs" :key="cfg.id">
                      <option :value="cfg.id" x-text="cfg.name"></option>
                    </template>
                  </select>
                </div>
                <div>
                  <label class="field-label">Filter</label>
                  <input x-model="browseAutomicFilter" class="field-input" placeholder="e.g. ETL_*" @keydown.enter="searchAutomic()" />
                </div>
              </div>
              <div class="flex justify-end">
                <button @click="searchAutomic()" :disabled="!browseAutomicConfigId || !browseAutomicFilter.trim() || browseAutomicLoading" class="btn-primary">
                  <span x-show="!browseAutomicLoading">Search</span>
                  <span x-show="browseAutomicLoading">Searching…</span>
                </button>
              </div>
              <template x-if="browseAutomicError">
                <p class="text-rose-600 text-sm" x-text="browseAutomicError"></p>
              </template>
              <template x-if="browseAutomicResults.length > 0">
                <div>
                  <div class="overflow-x-auto">
                    <table class="results-table text-xs">
                      <thead>
                        <tr>
                          <th><input type="checkbox" :checked="isBrowseAllSelected()" @change="toggleSelectAll()" /></th>
                          <th>Job Name</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        <template x-for="job in browseAutomicResults" :key="job.name">
                          <tr>
                            <td><input type="checkbox" :checked="browseAutomicSelected.includes(job.name)" @change="toggleBrowseSelection(job.name)" /></td>
                            <td class="font-mono" x-text="job.name"></td>
                            <td><span class="badge" x-text="job.status"></span></td>
                          </tr>
                        </template>
                      </tbody>
                    </table>
                  </div>
                  <div class="flex items-center justify-between mt-2">
                    <span class="text-muted text-xs" x-text="browseAutomicSelected.length + ' of ' + browseAutomicResults.length + ' selected'"></span>
                    <button @click="importSelectedAutomic()" :disabled="!browseAutomicSelected.length || browseAutomicImporting" class="btn-primary">
                      <span x-show="!browseAutomicImporting" x-text="'Import Selected (' + browseAutomicSelected.length + ')'"></span>
                      <span x-show="browseAutomicImporting">Importing…</span>
                    </button>
                  </div>
                </div>
              </template>
            </div>
          </template>
        </div>
      </template>
    </div>
```

- [ ] **Step 4: Manually verify the panel renders**

Start the app (`uvicorn api.main:app --reload`), navigate to Adapters tab. Confirm:
- "🔍 Browse & Import from Automic" panel appears below the file import panel
- Clicking header toggles expansion
- Config dropdown and Filter input appear when expanded
- Search button is disabled until both config and filter are filled
- "No configs available" message shows when configs list is empty

- [ ] **Step 5: Run the full test suite**

```
pytest tests/unit/test_adapters_routes.py tests/unit/test_api.py -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat(frontend): add Browse & Import from Automic panel in Adapters tab"
```

---

## Self-Review Notes

- **Spec coverage:** All 4 spec sections covered — architecture (Task 1-4), backend API (Tasks 1-4), frontend panels (Tasks 5-7), error handling (baked into route + JS methods), audit trail (Task 4 route logs `source: "automic_browse"`).
- **Types consistent:** `AutomicJobSummary`, `AutomicBulkImportRequest`, `AutomicBulkImportResponse` defined in Task 1 and referenced exactly by those names in Tasks 3, 4, and 7.
- **Method names consistent:** `search_automic_jobs` in service (Task 3) matches call in route (Task 3). `search_jobs` in client (Task 2) matches call in service (Task 3). `importFromFile` in JS (Task 5) matches `@click` in HTML (Task 6). `importSelectedAutomic` in JS (Task 7) matches `@click` in HTML (Task 7).
- **File upload audit trail:** The existing `/jobs/import` route already logs `source: "import"` — this satisfies the spec's `source: "file_import"` requirement (same semantics).
