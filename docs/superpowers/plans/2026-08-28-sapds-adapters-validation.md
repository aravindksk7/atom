# SAPDS Validation in Adapters Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add connection validation, job status lookup, and Job Catalog import for SAP Data Services (SAPDS) in the Adapters tab of the Web UI.

**Architecture:** Extend FastAPI backend schemas, adapter service methods, and adapter routes for SAPDS using `DSRestClient`, and render a SAP Data Services card in `tab-adapters.html` with Alpine.js actions.

**Tech Stack:** FastAPI, Pydantic, Python 3.11+, Alpine.js, Tailwind CSS.

## Global Constraints

- **Python Version:** 3.11+
- **API Prefix:** `/api/adapters/sap-ds/`
- **Routing:** FastAPI APIRouter with `tags=["adapters"]`
- **UI Framework:** Alpine.js v3 + HTML templates (`tab-adapters.html`)

---

### Task 1: Add SAPDS Schemas in `api/schemas.py`

**Files:**
- Modify: `api/schemas.py`
- Test: `tests/unit/test_adapters_schemas.py` (or `tests/unit/test_adapters_routes.py`)

**Interfaces:**
- Consumes: `BaseModel`, `Field`, `Literal`, `datetime`
- Produces: `SAPDSTestRequest`, `SAPDSLookupRequest`, `SAPDSJobStatusOut`, `SAPDSJobCreateRequest`

- [ ] **Step 1: Write failing schema test**

Create/update test to check schema instantiation and validation:

```python
def test_sapds_schemas():
    from api.schemas import SAPDSTestRequest, SAPDSLookupRequest, SAPDSJobStatusOut, SAPDSJobCreateRequest
    from datetime import datetime, timezone

    req = SAPDSTestRequest(config_id=1)
    assert req.config_id == 1

    lookup = SAPDSLookupRequest(config_id=2, identifier="JOB_ETL_DEMO", id_type="job_name", repository="REPO_PROD")
    assert lookup.config_id == 2
    assert lookup.identifier == "JOB_ETL_DEMO"
    assert lookup.id_type == "job_name"
    assert lookup.repository == "REPO_PROD"

    now = datetime.now(timezone.utc)
    status_out = SAPDSJobStatusOut(
        identifier="run-101",
        identifier_type="run_id",
        repository="REPO_PROD",
        status="PASSED",
        environment="Production",
        checked_at=now
    )
    assert status_out.identifier == "run-101"
    assert status_out.status == "PASSED"

    create_req = SAPDSJobCreateRequest(name="sapds_job_1", job_name="JOB_ETL_DEMO", repository="REPO_PROD")
    assert create_req.name == "sapds_job_1"
    assert create_req.job_name == "JOB_ETL_DEMO"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_adapters_routes.py -k test_sapds_schemas`
Expected: FAIL with ImportError for SAPDS schemas.

- [ ] **Step 3: Add SAPDS schemas to `api/schemas.py`**

Add the following schemas under the Adapter section in `api/schemas.py`:

```python
class SAPDSTestRequest(BaseModel):
    config_id: int


class SAPDSLookupRequest(BaseModel):
    config_id: int
    identifier: str
    id_type: Literal["run_id", "job_name"] = "job_name"
    repository: str | None = None


class SAPDSJobStatusOut(BaseModel):
    identifier: str
    identifier_type: str
    repository: str
    status: str
    environment: str
    checked_at: datetime


class SAPDSJobCreateRequest(BaseModel):
    name: str
    job_name: str
    repository: str | None = None
    poll_interval_s: float = 5.0
    timeout_s: float = 600.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_adapters_routes.py -k test_sapds_schemas`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_adapters_routes.py
git commit -m "feat(adapters): add SAPDS request and response schemas"
```

---

### Task 2: Implement SAPDS Adapter Service Methods in `api/services/adapter_service.py`

**Files:**
- Modify: `api/services/adapter_service.py`
- Test: `tests/unit/test_adapter_service.py`

**Interfaces:**
- Consumes: `DSRestClient`, `EnvironmentConfig`, `AdapterTestOut`, `SAPDSJobStatusOut`
- Produces: `AdapterService.test_ds_connection()`, `AdapterService.lookup_ds_job()`

- [ ] **Step 1: Write failing service unit tests**

Add tests to `tests/unit/test_adapter_service.py`:

```python
def test_test_ds_connection_success(service):
    with unittest.mock.patch("api.services.adapter_service.DSRestClient") as mock_ds:
        instance = mock_ds.return_value
        res = service.test_ds_connection(1)
        assert res.ok is True
        assert "Connected successfully" in res.message
        instance.login.assert_called_once()
        instance.logout.assert_called_once()

def test_lookup_ds_job_success(service):
    from etl_framework.runner.state import TestStatus
    with unittest.mock.patch("api.services.adapter_service.DSRestClient") as mock_ds:
        instance = mock_ds.return_value
        instance.get_job_status.return_value = TestStatus.PASSED
        res = service.lookup_ds_job(1, "JOB_DEMO", "job_name", repository="REPO_TEST")
        assert res.identifier == "JOB_DEMO"
        assert res.status == "PASSED"
        assert res.repository == "REPO_TEST"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/unit/test_adapter_service.py -k test_test_ds_connection`
Expected: FAIL with AttributeError: 'AdapterService' object has no attribute 'test_ds_connection'.

- [ ] **Step 3: Implement service methods in `api/services/adapter_service.py`**

Add `test_ds_connection` and `lookup_ds_job` methods to `AdapterService`:

```python
    def test_ds_connection(self, config_id: int) -> AdapterTestOut:
        start = time.monotonic()
        try:
            env = self._get_env_config(config_id)
            if not env.ds_url:
                return AdapterTestOut(ok=False, message="SAP DS URL is not configured", latency_ms=0)
            client = DSRestClient(env)
            client.login()
            try:
                pass
            finally:
                client.logout()
            latency = int((time.monotonic() - start) * 1000)
            return AdapterTestOut(ok=True, message="Connected successfully to SAP DS API", latency_ms=max(1, latency))
        except Exception as exc:
            return AdapterTestOut(ok=False, message=_friendly_error(exc), latency_ms=0)

    def lookup_ds_job(self, config_id: int, identifier: str, id_type: str, repository: str | None = None) -> SAPDSJobStatusOut:
        from datetime import datetime, timezone
        from etl_framework.sap_ds.client import DSRestClient
        env = self._get_env_config(config_id)
        repo = repository or env.ds_repository
        try:
            client = DSRestClient(env)
            status = client.get_job_status(identifier, repository=repo)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_friendly_error(exc)) from exc
        return SAPDSJobStatusOut(
            identifier=identifier,
            identifier_type=id_type,
            repository=repo,
            status=status.value,
            environment=env.name,
            checked_at=datetime.now(timezone.utc),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_adapter_service.py -k "test_test_ds_connection or test_lookup_ds_job"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/adapter_service.py tests/unit/test_adapter_service.py
git commit -m "feat(adapters): implement SAPDS connection testing and job lookup in AdapterService"
```

---

### Task 3: Implement SAPDS API Routes in `api/routes/adapters.py`

**Files:**
- Modify: `api/routes/adapters.py`
- Test: `tests/unit/test_adapters_routes.py`

**Interfaces:**
- Consumes: `SAPDSTestRequest`, `SAPDSLookupRequest`, `SAPDSJobCreateRequest`, `AdapterService`
- Produces: `POST /api/adapters/sap-ds/test`, `POST /api/adapters/sap-ds/lookup`, `POST /api/adapters/jobs/from-sap-ds`

- [ ] **Step 1: Write failing route tests**

Add route tests to `tests/unit/test_adapters_routes.py`:

```python
def test_test_sapds_connection_route(client, monkeypatch):
    from api.schemas import AdapterTestOut
    def mock_test_ds(self, config_id):
        return AdapterTestOut(ok=True, message="Connected successfully to SAP DS API", latency_ms=12)
    monkeypatch.setattr("api.services.adapter_service.AdapterService.test_ds_connection", mock_test_ds)

    resp = client.post("/api/adapters/sap-ds/test", json={"config_id": 1})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

def test_lookup_sapds_job_route(client, monkeypatch):
    from api.schemas import SAPDSJobStatusOut
    from datetime import datetime, timezone
    def mock_lookup_ds(self, config_id, identifier, id_type, repository=None):
        return SAPDSJobStatusOut(
            identifier=identifier, identifier_type=id_type, repository=repository or "REPO1",
            status="PASSED", environment="Dev", checked_at=datetime.now(timezone.utc)
        )
    monkeypatch.setattr("api.services.adapter_service.AdapterService.lookup_ds_job", mock_lookup_ds)

    resp = client.post("/api/adapters/sap-ds/lookup", json={"config_id": 1, "identifier": "JOB_1", "id_type": "job_name"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "PASSED"
```

- [ ] **Step 2: Run route tests to verify failure**

Run: `pytest tests/unit/test_adapters_routes.py -k "test_test_sapds_connection_route or test_lookup_sapds_job_route"`
Expected: FAIL with 404 Not Found.

- [ ] **Step 3: Add SAPDS routes to `api/routes/adapters.py`**

Add imports and route handlers:

```python
@router.post("/sap-ds/test", response_model=AdapterTestOut)
def test_ds_connection(
    body: SAPDSTestRequest,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.test_ds_connection(body.config_id)


@router.post("/sap-ds/lookup", response_model=SAPDSJobStatusOut)
def lookup_ds_job(
    body: SAPDSLookupRequest,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.lookup_ds_job(body.config_id, body.identifier, body.id_type, body.repository)


@router.post("/jobs/from-sap-ds", response_model=JobDefinition, status_code=201)
def create_job_from_sap_ds(
    body: SAPDSJobCreateRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    job_data = {
        "name": body.name,
        "description": f"SAP DS Job: {body.job_name}",
        "tags": ["ds_job"],
        "job_type": "ds_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "params": {
            "job_name": body.job_name,
            "repository": body.repository or "",
            "poll_interval_s": body.poll_interval_s,
            "timeout_s": body.timeout_s,
        },
        "enabled": True,
    }
    JobRepository(db).upsert(job_data)
    AuditService(db).log(
        request, "job.created", "job", body.name,
        {"source": "sap_ds", "params": job_data["params"]},
    )
    return JobDefinition(**job_data)
```

- [ ] **Step 4: Run route tests to verify they pass**

Run: `pytest tests/unit/test_adapters_routes.py -k "test_test_sapds_connection_route or test_lookup_sapds_job_route"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/routes/adapters.py tests/unit/test_adapters_routes.py
git commit -m "feat(adapters): add SAPDS test, lookup, and job import API routes"
```

---

### Task 4: Add SAPDS Adapter Panel in `tab-adapters.html` and Alpine.js Actions

**Files:**
- Modify: `frontend/partials/tab-adapters.html`
- Modify: `frontend/app.js` (or `frontend/js/adapters.js` depending on frontend modularization)

**Interfaces:**
- Consumes: Alpine.js state, `/api/adapters/sap-ds/test`, `/api/adapters/sap-ds/lookup`, `/api/adapters/jobs/from-sap-ds`
- Produces: SAP Data Services panel UI in Adapters tab

- [ ] **Step 1: Add SAP Data Services card HTML to `frontend/partials/tab-adapters.html`**

Add the SAP DS panel inside `<div class="grid-2 gap-6">` or as a 3rd adapter panel in `frontend/partials/tab-adapters.html`:

```html
    <!-- SAP DS Panel -->
    <div>
      <div class="card mb-4">
        <div class="flex items-center justify-between mb-3">
          <div class="font-semibold text-slate-700">SAP Data Services</div>
          <span class="badge badge-purple">Management Console</span>
        </div>
        <div class="grid-2 mb-3">
          <div>
            <label class="field-label" for="a11y-adapters-config-ds">Config</label>
            <select x-model="dsConfigId" class="field-input field-select" data-testid="ds-config-select" id="a11y-adapters-config-ds">
              <option value="">— Select —</option>
              <template x-for="cfg in configs" :key="cfg.id">
                <option :value="cfg.id" x-text="cfg.name"></option>
              </template>
            </select>
          </div>
          <div class="flex items-end">
            <button @click="testDSConnection()" :disabled="!dsConfigId || dsTesting" class="btn-primary w-full" data-testid="ds-test-connection-btn">
              <span x-show="!dsTesting">Test Connection</span>
              <span x-show="dsTesting">Testing…</span>
            </button>
          </div>
        </div>
        <template x-if="dsTestResult">
          <div :class="dsTestResult.ok ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : 'bg-rose-50 border-rose-200 text-rose-700'"
               class="border rounded-lg p-2 text-sm mb-3" data-testid="ds-test-result">
            <span x-text="dsTestResult.ok ? '✓ ' + dsTestResult.message : '✗ ' + dsTestResult.message"></span>
            <span x-show="dsTestResult.latency_ms" class="text-muted ml-2" x-text="dsTestResult.latency_ms + 'ms'"></span>
          </div>
        </template>
        <div class="border-t pt-3 mt-3">
          <div class="font-medium text-xs text-slate-500 uppercase tracking-wider mb-2">Job Status Lookup</div>
          <div class="grid-2 mb-2 gap-2">
            <div>
              <label class="field-label" for="a11y-ds-id-type">Lookup Type</label>
              <select x-model="dsIdType" class="field-input field-select" id="a11y-ds-id-type">
                <option value="job_name">Job Name</option>
                <option value="run_id">Run ID</option>
              </select>
            </div>
            <div>
              <label class="field-label" for="a11y-ds-repository">Repository (Optional)</label>
              <input x-model="dsRepository" class="field-input" placeholder="Config Default" id="a11y-ds-repository" />
            </div>
          </div>
          <div class="flex gap-2 mb-3">
            <input x-model="dsIdentifier" class="field-input" :placeholder="dsIdType === 'run_id' ? 'e.g. run-123' : 'e.g. JOB_ETL_NIGHTLY'" data-testid="ds-identifier-input" />
            <button @click="lookupSAPDS()" :disabled="!dsConfigId || !dsIdentifier || dsLoading" class="btn-primary flex-shrink-0" data-testid="ds-lookup-btn">
              <span x-show="!dsLoading">Lookup</span>
              <span x-show="dsLoading">…</span>
            </button>
          </div>
          <template x-if="dsResult">
            <div class="border rounded-lg p-3 text-sm" :class="dsResult.status==='PASSED' ? 'border-emerald-200 bg-emerald-50' : 'border-slate-200 bg-slate-50'" data-testid="ds-result">
              <div class="flex items-center justify-between">
                <span class="font-medium" x-text="dsResult.identifier"></span>
                <span class="badge" :class="statusBadgeClass(dsResult.status)" x-text="dsResult.status"></span>
              </div>
              <div class="text-muted mt-1" x-text="'Repo: ' + (dsResult.repository || 'default') + ' · Env: ' + dsResult.environment + ' · Checked: ' + fmtDate(dsResult.checked_at)"></div>
              <button @click="addSAPDSJob()" class="btn-primary btn-sm mt-2">+ Add to Job Catalog</button>
            </div>
          </template>
        </div>
      </div>
    </div>
```

- [ ] **Step 2: Add SAPDS state & functions to Alpine app in `frontend/app.js`**

Add properties and methods:

```javascript
    dsConfigId: '',
    dsTesting: false,
    dsTestResult: null,
    dsIdType: 'job_name',
    dsIdentifier: '',
    dsRepository: '',
    dsLoading: false,
    dsResult: null,

    async testDSConnection() {
      if (!this.dsConfigId) return;
      this.dsTesting = true;
      this.dsTestResult = null;
      try {
        const res = await fetch('/api/adapters/sap-ds/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ config_id: parseInt(this.dsConfigId) })
        });
        this.dsTestResult = await res.json();
      } catch (err) {
        this.dsTestResult = { ok: false, message: err.message, latency_ms: 0 };
      } finally {
        this.dsTesting = false;
      }
    },

    async lookupSAPDS() {
      if (!this.dsConfigId || !this.dsIdentifier) return;
      this.dsLoading = true;
      this.dsResult = null;
      try {
        const res = await fetch('/api/adapters/sap-ds/lookup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            config_id: parseInt(this.dsConfigId),
            identifier: this.dsIdentifier,
            id_type: this.dsIdType,
            repository: this.dsRepository.trim() || null
          })
        });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || 'Lookup failed');
        }
        this.dsResult = await res.json();
      } catch (err) {
        this.showToast(err.message, 'error');
      } finally {
        this.dsLoading = false;
      }
    },

    async addSAPDSJob() {
      if (!this.dsResult) return;
      const jobName = `ds_${this.dsResult.identifier.toLowerCase().replace(/[^a-z0-9_]/g, '_')}`;
      try {
        const res = await fetch('/api/adapters/jobs/from-sap-ds', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: jobName,
            job_name: this.dsResult.identifier,
            repository: this.dsResult.repository || null
          })
        });
        if (res.ok) {
          this.showToast(`Job '${jobName}' added to catalog`, 'success');
          await this.loadJobs();
        } else {
          const err = await res.json();
          this.showToast(err.detail || 'Failed to add job', 'error');
        }
      } catch (err) {
        this.showToast(err.message, 'error');
      }
    }
```

- [ ] **Step 3: Run full pytest suite to verify backend and frontend assets integration**

Run: `pytest tests/unit/test_adapters_routes.py tests/unit/test_adapter_service.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit UI changes**

```bash
git add frontend/partials/tab-adapters.html frontend/app.js
git commit -m "feat(ui): add SAP Data Services panel and Alpine.js actions to Adapters tab"
```
