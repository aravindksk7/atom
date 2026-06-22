# Five Feature Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Automic credential wiring (3), audit log (5), SSE run streaming (6), trend query caching (10), and dbt test result adapter (11) in the ETL Test Framework.

**Architecture:** Service-per-feature — new standalone files for testable business logic (`AuditService`, `DbtArtifactParser`); route-level additions for SSE and caching; minimal wiring changes for Automic. Each feature is independently testable before the next begins.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, Alpine.js, stdlib `json`/`time` (no new package dependencies).

**Spec:** `docs/superpowers/specs/2026-06-15-five-feature-improvements-design.md`

---

## File Map

| Status | Path | What changes |
|---|---|---|
| modify | `etl_framework/repository/models.py` | add `AuditEvent` ORM model |
| modify | `etl_framework/repository/repository.py` | add `AuditRepository` class |
| modify | `etl_framework/repository/database.py` | `init_db()` picks up new table automatically via `Base.metadata.create_all` — no change needed if that pattern already handles it; confirm in Task 1 |
| create | `api/services/audit_service.py` | `AuditService` |
| create | `api/routes/audit.py` | `GET /api/audit` |
| modify | `api/schemas.py` | add `AuditEventOut`; add `"dbt_test"` to `job_type` Literal |
| modify | `api/main.py` | include audit router |
| modify | `api/routes/runs.py` | audit calls + SSE endpoint + TTL cache |
| modify | `api/routes/jobs.py` | audit calls |
| modify | `api/routes/configs.py` | audit calls |
| modify | `api/routes/tokens.py` | audit calls |
| modify | `api/services/run_executor.py` | `_build_case_dbt()` + automic cred injection + `dbt_test` branch in `_build_case` |
| create | `etl_framework/dbt/__init__.py` | empty |
| create | `etl_framework/dbt/parser.py` | `DbtArtifactParser` + `DbtTestNode` |
| modify | `frontend/app.js` | Automic job editor fields + SSE Monitor + dbt job editor fields + Audit sub-tab |
| create | `tests/unit/test_audit.py` | audit repository + service tests |
| create | `tests/unit/test_dbt_parser.py` | parser tests |
| create | `tests/unit/test_sse_stream.py` | SSE generator test |
| modify | `tests/unit/test_dag_retry_trends.py` | trend cache test appended |

---

## Task 1: AuditEvent ORM model + AuditRepository

**Files:**
- Modify: `etl_framework/repository/models.py`
- Modify: `etl_framework/repository/repository.py`
- Create: `tests/unit/test_audit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_audit.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import AuditRepository


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_audit_log_writes_row():
    db = _session()
    repo = AuditRepository(db)
    event = repo.log(
        actor="ci-token",
        action="run.created",
        resource_type="run",
        resource_id="abc-123",
    )
    assert event.id is not None
    assert event.actor == "ci-token"
    assert event.action == "run.created"
    assert event.resource_type == "run"
    assert event.resource_id == "abc-123"
    assert event.diff is None
    assert event.created_at is not None


def test_audit_log_with_diff():
    db = _session()
    repo = AuditRepository(db)
    diff = {"note": "Known rounding difference", "accepted_by": "analyst"}
    event = repo.log(
        actor="analyst-token",
        action="mismatch.accepted",
        resource_type="mismatch",
        resource_id="99",
        diff=diff,
    )
    assert event.diff == diff


def test_audit_list_filters_by_resource_type():
    db = _session()
    repo = AuditRepository(db)
    repo.log(actor="a", action="run.created", resource_type="run", resource_id="r1")
    repo.log(actor="a", action="job.created", resource_type="job", resource_id="j1")
    repo.log(actor="a", action="run.deleted", resource_type="run", resource_id="r2")

    results = repo.list(resource_type="run")
    assert len(results) == 2
    assert all(e.resource_type == "run" for e in results)


def test_audit_list_filters_by_resource_id():
    db = _session()
    repo = AuditRepository(db)
    repo.log(actor="a", action="run.created", resource_type="run", resource_id="r1")
    repo.log(actor="a", action="run.deleted", resource_type="run", resource_id="r2")

    results = repo.list(resource_id="r1")
    assert len(results) == 1
    assert results[0].resource_id == "r1"


def test_audit_list_default_limit():
    db = _session()
    repo = AuditRepository(db)
    for i in range(60):
        repo.log(actor="a", action="run.created", resource_type="run", resource_id=str(i))
    results = repo.list()
    assert len(results) == 50  # default limit
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m pytest tests/unit/test_audit.py -v
```

Expected: `ImportError` or `AttributeError` — `AuditRepository` does not exist yet.

- [ ] **Step 3: Add AuditEvent to models.py**

Open `etl_framework/repository/models.py`. After the last class (`ScheduledRun`), add:

```python
# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditEvent(Base):
    __tablename__ = "audit_events"

    id            = Column(Integer, primary_key=True, index=True)
    actor         = Column(String(255), nullable=True)
    action        = Column(String(100), nullable=False)
    resource_type = Column(String(50),  nullable=False)
    resource_id   = Column(String(255), nullable=True)
    diff          = Column(JSON,        nullable=True)
    created_at    = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
```

- [ ] **Step 4: Add AuditRepository to repository.py**

Open `etl_framework/repository/repository.py`. Add this import at the top of the file alongside the existing model imports:

```python
from etl_framework.repository.models import (
    SavedConfig, SavedJob, TestRun, TestResult, MismatchDetail,
    ApiToken, NotificationHook, ScheduledRun, JobLineageEdge, AuditEvent,
)
```

Then append the class at the end of the file:

```python
class AuditRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def log(
        self,
        actor: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        diff: dict | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            diff=diff,
        )
        self._db.add(event)
        self._db.commit()
        self._db.refresh(event)
        return event

    def list(
        self,
        resource_type: str | None = None,
        resource_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        q = self._db.query(AuditEvent).order_by(AuditEvent.created_at.desc())
        if resource_type:
            q = q.filter(AuditEvent.resource_type == resource_type)
        if resource_id:
            q = q.filter(AuditEvent.resource_id == resource_id)
        return q.offset(offset).limit(limit).all()
```

- [ ] **Step 5: Run tests to verify they pass**

```
python -m pytest tests/unit/test_audit.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 6: Commit**

```
git add etl_framework/repository/models.py etl_framework/repository/repository.py tests/unit/test_audit.py
git commit -m "feat(audit): add AuditEvent model and AuditRepository"
```

---

## Task 2: AuditEventOut schema + AuditService

**Files:**
- Modify: `api/schemas.py`
- Create: `api/services/audit_service.py`
- Modify: `tests/unit/test_audit.py`

- [ ] **Step 1: Add AuditEventOut to schemas.py**

Open `api/schemas.py`. Add this near the end of the file, before the last class:

```python
class AuditEventOut(BaseModel):
    id: int
    actor: str | None
    action: str
    resource_type: str
    resource_id: str | None
    diff: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: Write the failing AuditService tests**

Append to `tests/unit/test_audit.py`:

```python
from unittest.mock import MagicMock
from api.services.audit_service import AuditService


def _mock_request(token_name: str | None):
    req = MagicMock()
    if token_name:
        req.state.token = MagicMock()
        req.state.token.name = token_name
    else:
        # Simulate exempt path — token not set on state
        del req.state.token
    return req


def test_audit_service_resolves_actor_from_token():
    db = _session()
    req = _mock_request("ci-token")
    svc = AuditService(db)
    svc.log(req, "run.created", "run", "abc-123")

    events = AuditRepository(db).list()
    assert len(events) == 1
    assert events[0].actor == "ci-token"


def test_audit_service_falls_back_to_system_when_no_token():
    db = _session()
    req = _mock_request(None)
    svc = AuditService(db)
    svc.log(req, "run.created", "run", "abc-123")

    events = AuditRepository(db).list()
    assert events[0].actor == "system"


def test_audit_service_list_delegates_to_repository():
    db = _session()
    req = _mock_request("tok")
    svc = AuditService(db)
    svc.log(req, "job.created", "job", "my-job")
    svc.log(req, "config.created", "config", "my-cfg")

    results = svc.list(resource_type="job")
    assert len(results) == 1
    assert results[0].action == "job.created"
```

- [ ] **Step 3: Run test to verify it fails**

```
python -m pytest tests/unit/test_audit.py::test_audit_service_resolves_actor_from_token -v
```

Expected: `ImportError` — `api.services.audit_service` does not exist.

- [ ] **Step 4: Create api/services/audit_service.py**

```python
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from etl_framework.repository.repository import AuditRepository


class AuditService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def log(
        self,
        request: Request,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        diff: dict | None = None,
    ) -> None:
        token = getattr(request.state, "token", None)
        actor = token.name if token else "system"
        AuditRepository(self._db).log(actor, action, resource_type, resource_id, diff)

    def list(
        self,
        resource_type: str | None = None,
        resource_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        return AuditRepository(self._db).list(
            resource_type=resource_type,
            resource_id=resource_id,
            limit=limit,
            offset=offset,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```
python -m pytest tests/unit/test_audit.py -v
```

Expected: 8 tests PASS.

- [ ] **Step 6: Commit**

```
git add api/schemas.py api/services/audit_service.py tests/unit/test_audit.py
git commit -m "feat(audit): add AuditEventOut schema and AuditService"
```

---

## Task 3: GET /api/audit route + main.py wiring

**Files:**
- Create: `api/routes/audit.py`
- Modify: `api/main.py`

- [ ] **Step 1: Create api/routes/audit.py**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import AuditEventOut
from api.services.audit_service import AuditService

router = APIRouter(tags=["audit"])


@router.get("", response_model=list[AuditEventOut])
def list_audit_events(
    resource_type: str | None = None,
    resource_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_session),
):
    svc = AuditService(db)
    return svc.list(
        resource_type=resource_type,
        resource_id=resource_id,
        limit=min(limit, 500),
        offset=offset,
    )
```

- [ ] **Step 2: Wire the router in api/main.py**

Add the import alongside the other route imports (line 10-11 area):

```python
from api.routes import audit as audit_routes
```

Add the router registration after `lineage_routes` (after line 39):

```python
app.include_router(audit_routes.router, prefix="/api/audit")
```

- [ ] **Step 3: Smoke-test the endpoint**

```
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 &
curl -s http://127.0.0.1:8000/api/audit | python -m json.tool
```

Expected: `[]` (empty list, HTTP 200).

Kill the server: `kill %1`

- [ ] **Step 4: Run existing integration smoke test to confirm nothing broke**

```
python -m pytest tests/integration/test_api_frontend_smoke.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```
git add api/routes/audit.py api/main.py
git commit -m "feat(audit): add GET /api/audit route and wire router"
```

---

## Task 4: Audit calls in write routes

**Files:**
- Modify: `api/routes/runs.py`
- Modify: `api/routes/jobs.py`
- Modify: `api/routes/configs.py`
- Modify: `api/routes/tokens.py`

- [ ] **Step 1: Add audit import to each route file**

In each of the four files, add at the top alongside existing service imports:

```python
from api.services.audit_service import AuditService
```

- [ ] **Step 2: Add audit call to trigger_run in runs.py**

Find the `trigger_run` function. After `repo.create_run(...)` and before `background_tasks.add_task(...)`, add:

```python
    AuditService(db).log(request, "run.created", "run", run_id)
```

Update the function signature to accept `request: Request`:

```python
def trigger_run(
    body: RunTrigger,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
```

(`Request` is already imported from `fastapi` in this file.)

- [ ] **Step 3: Add audit call to delete_run in runs.py**

Find the `DELETE /{run_id}` handler. After confirming the run exists and before deleting, add:

```python
    AuditService(db).log(request, "run.deleted", "run", run_id)
```

Update the signature:

```python
def delete_run(run_id: str, request: Request, db: Session = Depends(get_session)):
```

- [ ] **Step 4: Add audit call to accept_mismatch in runs.py**

Find the `PATCH .../mismatches/{mismatch_id}/accept` handler. After the update succeeds, add:

```python
    AuditService(db).log(
        request, "mismatch.accepted", "mismatch", str(mismatch_id),
        diff={"note": body.note, "accepted_by": body.accepted_by},
    )
```

Update the signature to include `request: Request`.

- [ ] **Step 5: Add audit calls in jobs.py**

In `create_job`: after the job is created, add:
```python
    AuditService(db).log(request, "job.created", "job", body.name)
```

In `update_job`: after the update succeeds, add:
```python
    AuditService(db).log(request, "job.updated", "job", name)
```

In `delete_job`: after confirming success, add:
```python
    AuditService(db).log(request, "job.deleted", "job", name)
```

Add `request: Request` to each handler signature.

- [ ] **Step 6: Add audit calls in configs.py**

In `create_config`: after creation, add:
```python
    AuditService(db).log(request, "config.created", "config", str(result.id))
```

In `update_config`: after update, add:
```python
    AuditService(db).log(request, "config.updated", "config", str(config_id))
```

In `delete_config`: after delete, add:
```python
    AuditService(db).log(request, "config.deleted", "config", str(config_id))
```

Add `request: Request` to each handler signature.

- [ ] **Step 7: Add audit calls in tokens.py**

In `create_token`: after creation, add:
```python
    AuditService(db).log(request, "token.created", "token", result.name)
```

In `delete_token`: after deletion, add:
```python
    AuditService(db).log(request, "token.deleted", "token", str(token_id))
```

Add `request: Request` to each handler signature.

- [ ] **Step 8: Run integration smoke test**

```
python -m pytest tests/integration/test_api_frontend_smoke.py tests/unit/test_audit.py -v
```

Expected: all PASS.

- [ ] **Step 9: API-level audit test**

Append to `tests/unit/test_audit.py`:

```python
from fastapi.testclient import TestClient
from api.main import app

_client = TestClient(app, raise_server_exceptions=True)


def _auth_headers():
    # Bootstrap a token — /api/tokens is exempt from auth
    resp = _client.post("/api/tokens", json={"name": "test-tok"})
    raw = resp.json()["raw_token"]
    return {"Authorization": f"Bearer {raw}"}


def test_audit_api_records_run_and_mismatch():
    h = _auth_headers()
    # Trigger a run
    run_resp = _client.post(
        "/api/runs",
        json={
            "source_env": "dev",
            "target_env": "prod",
            "job_sequence": [],
            "run_settings": {"metrics_enabled": False},
        },
        headers=h,
    )
    assert run_resp.status_code == 202
    run_id = run_resp.json()["run_id"]

    # Query audit log
    audit_resp = _client.get("/api/audit?resource_type=run", headers=h)
    assert audit_resp.status_code == 200
    events = audit_resp.json()
    run_events = [e for e in events if e["resource_id"] == run_id]
    assert any(e["action"] == "run.created" for e in run_events)
```

Run:
```
python -m pytest tests/unit/test_audit.py::test_audit_api_records_run_and_mismatch -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```
git add api/routes/runs.py api/routes/jobs.py api/routes/configs.py api/routes/tokens.py tests/unit/test_audit.py
git commit -m "feat(audit): wire audit logging into all write routes"
```

---

## Task 5: History Audit sub-tab (frontend)

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Add audit data property**

In `app.js`, find the History data section (near `activeRuns: []`). Add:

```javascript
// Audit
auditEvents: [],
auditResourceTypeFilter: '',
```

- [ ] **Step 2: Add loadAuditEvents method**

Find the History methods section (near `async loadHistory()`). Add:

```javascript
async loadAuditEvents() {
  const params = this.auditResourceTypeFilter
    ? `?resource_type=${this.auditResourceTypeFilter}&limit=100`
    : '?limit=100';
  try {
    this.auditEvents = await this.api(`/api/audit${params}`);
  } catch (e) {
    this.toast('error', 'Audit load failed', e.message);
  }
},
```

- [ ] **Step 3: Add Audit sub-tab button**

Find the History tab sub-tab buttons in the HTML template (the section with Runs / Trends / Lineage sub-tab buttons). Add a 4th button after Lineage:

```html
<button @click="historyTab='audit'; loadAuditEvents()"
        :class="historyTab==='audit' ? 'tab-btn active' : 'tab-btn'">
  Audit
</button>
```

- [ ] **Step 4: Add Audit sub-tab panel**

After the Lineage sub-tab panel closing tag (`</div>`), add:

```html
<!-- Audit sub-tab -->
<div x-show="historyTab==='audit'">
  <div class="card">
    <div class="card-header d-flex align-items-center gap-2">
      <span>Audit Log</span>
      <select x-model="auditResourceTypeFilter" @change="loadAuditEvents()" class="form-select form-select-sm w-auto">
        <option value="">All Types</option>
        <option value="run">Run</option>
        <option value="job">Job</option>
        <option value="config">Config</option>
        <option value="mismatch">Mismatch</option>
        <option value="token">Token</option>
      </select>
    </div>
    <div class="card-body p-0">
      <table class="table table-sm table-dark mb-0">
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Actor</th>
            <th>Action</th>
            <th>Resource Type</th>
            <th>Resource ID</th>
          </tr>
        </thead>
        <tbody>
          <template x-for="ev in auditEvents" :key="ev.id">
            <tr>
              <td x-text="new Date(ev.created_at).toLocaleString()"></td>
              <td x-text="ev.actor || 'system'"></td>
              <td x-text="ev.action"></td>
              <td x-text="ev.resource_type"></td>
              <td x-text="ev.resource_id || '—'"></td>
            </tr>
          </template>
          <tr x-show="auditEvents.length === 0">
            <td colspan="5" class="text-center text-muted">No audit events</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
```

- [ ] **Step 5: Verify JS syntax**

```
node --check frontend/app.js
```

Expected: no output (clean).

- [ ] **Step 6: Commit**

```
git add frontend/app.js
git commit -m "feat(audit): add Audit sub-tab to History tab"
```

---

## Task 6: Automic credential injection

**Files:**
- Modify: `api/routes/runs.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_run_executor.py`:

```python
def test_automic_credentials_injected_from_saved_config():
    """trigger_run snapshot includes automic_credentials when config_id is provided."""
    from api.schemas import RunTrigger, RunSettings
    from etl_framework.repository.repository import ConfigRepository

    db = _session()
    cfg = ConfigRepository(db).create(
        name="prod-automic",
        env_name="prod",
        config_data={
            "automic_url": "http://automic.internal:8080",
            "automic_user": "svc_etl",
            "automic_password": "secret",
            "automic_timeout": 60,
            "automic_max_retries": 5,
        },
    )

    # Simulate what trigger_run does when building the snapshot
    from api.routes.runs import _build_config_snapshot
    snapshot = _build_config_snapshot(db, config_id=cfg.id, config_data={})

    assert "automic_credentials" in snapshot
    creds = snapshot["automic_credentials"]
    assert creds["automic_url"] == "http://automic.internal:8080"
    assert creds["automic_user"] == "svc_etl"
    assert creds["name"] == "prod-automic"
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m pytest tests/unit/test_run_executor.py::test_automic_credentials_injected_from_saved_config -v
```

Expected: `ImportError` — `_build_config_snapshot` does not exist.

- [ ] **Step 3: Extract _build_config_snapshot helper in runs.py**

In `runs.py`, add this function before `trigger_run`:

```python
def _build_config_snapshot(
    db: Session,
    config_id: int | None,
    config_data: dict,
) -> dict:
    snapshot = dict(config_data)
    if config_id:
        from etl_framework.repository.repository import ConfigRepository
        saved = ConfigRepository(db).get(config_id)
        if saved and saved.config_json:
            cfg = saved.config_json
            if cfg.get("automic_url"):
                snapshot["automic_credentials"] = {
                    "name": saved.name,
                    "automic_url": cfg.get("automic_url", ""),
                    "automic_user": cfg.get("automic_user", ""),
                    "automic_password": cfg.get("automic_password", ""),
                    "automic_timeout": cfg.get("automic_timeout", 30),
                    "automic_max_retries": cfg.get("automic_max_retries", 3),
                }
            if cfg.get("bo_url"):
                snapshot["bo_credentials"] = {
                    "name": saved.name,
                    "bo_url": cfg.get("bo_url", ""),
                    "bo_user": cfg.get("bo_user", ""),
                    "bo_password": cfg.get("bo_password", ""),
                    "bo_timeout": cfg.get("bo_timeout", 30),
                }
    return snapshot
```

Update `trigger_run` to call it. Replace the current snapshot-building block:

```python
    # Before:
    config_snapshot = dict(body.config_data or {})

    # After:
    config_snapshot = _build_config_snapshot(db, body.config_id, dict(body.config_data or {}))
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m pytest tests/unit/test_run_executor.py::test_automic_credentials_injected_from_saved_config -v
```

Expected: PASS.

- [ ] **Step 5: Run full unit suite**

```
python -m pytest tests/unit/ -q
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```
git add api/routes/runs.py tests/unit/test_run_executor.py
git commit -m "feat(automic): inject automic/bo credentials from saved config into run snapshot"
```

---

## Task 7: Automic UI job editor fields

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Find the job editor form in app.js**

Search for `job_type` or `job.job_type` in `app.js` to locate the job editor template section.

- [ ] **Step 2: Add automic_job conditional block**

Inside the job editor form, after any existing job-type-specific block (e.g., the BO report block), add:

```html
<!-- automic_job fields -->
<template x-if="editJob.job_type === 'automic_job'">
  <div>
    <div class="mb-2">
      <label class="form-label">Job Name <span class="text-muted">(or provide Run ID)</span></label>
      <input type="text" class="form-control"
             x-model="editJob.params.job_name"
             placeholder="e.g. ETL_DAILY_LOAD">
    </div>
    <div class="mb-2">
      <label class="form-label">Run ID <span class="text-muted">(takes priority over Job Name)</span></label>
      <input type="text" class="form-control"
             x-model="editJob.params.run_id"
             placeholder="e.g. 20240615_001">
    </div>
    <p class="text-muted small">Provide <strong>job_name</strong> OR <strong>run_id</strong> — run_id takes priority. Requires <code>use_live_connections: true</code> at run time.</p>
  </div>
</template>
```

- [ ] **Step 3: Add automic_job to the job type dropdown**

Find the `<select>` for `job_type` in the job editor. Add the option if not already present:

```html
<option value="automic_job">Automic Job</option>
```

- [ ] **Step 4: Verify JS syntax**

```
node --check frontend/app.js
```

Expected: no output.

- [ ] **Step 5: Commit**

```
git add frontend/app.js
git commit -m "feat(automic): add automic_job fields to UI job editor"
```

---

## Task 8: SSE streaming endpoint

**Files:**
- Modify: `api/routes/runs.py`
- Create: `tests/unit/test_sse_stream.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sse_stream.py
import asyncio
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import RunRepository


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _collect_frames(gen_func, max_frames=3):
    """Collect frames from an async generator, stopping after max_frames."""
    async def _run():
        frames = []
        async for chunk in gen_func():
            frames.append(chunk)
            if len(frames) >= max_frames:
                break
        return frames
    return asyncio.get_event_loop().run_until_complete(_run())


def test_sse_emits_status_frame_and_stops_at_terminal():
    from api.routes.runs import _sse_generator

    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-sse-1", "dev", "prod", {})
    # Mark it as PASSED immediately
    run = repo.get_run("run-sse-1")
    run.status = "PASSED"
    run.passed = 2
    run.total_tests = 2
    db.commit()

    frames = _collect_frames(lambda: _sse_generator("run-sse-1", db), max_frames=5)

    assert len(frames) == 1  # stops after first frame because status is terminal
    data = json.loads(frames[0].removeprefix("data: ").strip())
    assert data["run_id"] == "run-sse-1"
    assert data["status"] == "PASSED"
    assert data["passed"] == 2


def test_sse_emits_error_frame_for_missing_run():
    from api.routes.runs import _sse_generator

    db = _session()
    frames = _collect_frames(lambda: _sse_generator("nonexistent", db), max_frames=5)

    assert len(frames) == 1
    assert "error" in frames[0]
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m pytest tests/unit/test_sse_stream.py -v
```

Expected: `ImportError` — `_sse_generator` does not exist.

- [ ] **Step 3: Add _sse_generator and stream endpoint to runs.py**

At the top of `runs.py`, `StreamingResponse` is already imported. Add `import asyncio` if not present.

Add the generator function (place it before the `@router` route definitions, after imports):

```python
async def _sse_generator(run_id: str, db):
    import asyncio
    import json as _json
    while True:
        run = RunRepository(db).get_run(run_id)
        if run is None:
            yield 'event: error\ndata: {"detail": "not found"}\n\n'
            return
        payload = _json.dumps({
            "run_id": run_id,
            "status": run.status,
            "passed": run.passed,
            "failed": run.failed,
            "slow": run.slow,
            "error": run.error,
            "total_tests": run.total_tests,
        })
        yield f"data: {payload}\n\n"
        if run.status in _TERMINAL:
            return
        try:
            await asyncio.sleep(1.5)
        except GeneratorExit:
            return
```

Add the route:

```python
@router.get("/{run_id}/stream")
async def stream_run_status(run_id: str, db: Session = Depends(get_session)):
    from etl_framework.repository.database import SessionLocal

    async def generate():
        stream_db = SessionLocal()
        try:
            async for chunk in _sse_generator(run_id, stream_db):
                yield chunk
        finally:
            stream_db.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```
python -m pytest tests/unit/test_sse_stream.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Verify content-type via TestClient**

Append to `tests/unit/test_sse_stream.py`:

```python
from fastapi.testclient import TestClient
from api.main import app


def test_sse_endpoint_returns_event_stream_content_type():
    client = TestClient(app, raise_server_exceptions=True)
    # Bootstrap token
    tok = client.post("/api/tokens", json={"name": "sse-tok"}).json()["raw_token"]
    h = {"Authorization": f"Bearer {tok}"}

    # Create a run that is already terminal so stream closes fast
    run_resp = client.post(
        "/api/runs",
        json={"source_env": "dev", "target_env": "prod",
              "job_sequence": [], "run_settings": {"metrics_enabled": False}},
        headers=h,
    )
    run_id = run_resp.json()["run_id"]

    with client.stream("GET", f"/api/runs/{run_id}/stream") as resp:
        assert "text/event-stream" in resp.headers["content-type"]
```

```
python -m pytest tests/unit/test_sse_stream.py -v
```

Expected: all 3 PASS.

- [ ] **Step 6: Commit**

```
git add api/routes/runs.py tests/unit/test_sse_stream.py
git commit -m "feat(sse): add /api/runs/{id}/stream SSE endpoint"
```

---

## Task 9: SSE Monitor tab (frontend)

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Add _eventSources tracking map**

In the data properties section (near `pollTimer: null`), replace `pollTimer: null` with:

```javascript
pollTimer: null,
_eventSources: {},  // run_id -> EventSource
```

- [ ] **Step 2: Add startStream method**

Find the `startPolling()` method. Add `startStream` alongside it:

```javascript
startStream(runId) {
  if (this._eventSources[runId]) return;  // already streaming
  const es = new EventSource(`/api/runs/${runId}/stream`);
  this._eventSources[runId] = es;
  es.onmessage = (e) => {
    const data = JSON.parse(e.data);
    const idx = this.activeRuns.findIndex(r => r.run_id === data.run_id);
    if (idx !== -1) this.activeRuns[idx] = { ...this.activeRuns[idx], ...data };
    if (['PASSED', 'FAILED', 'SLOW', 'ERROR', 'COMPLETED'].includes(data.status)) {
      es.close();
      delete this._eventSources[data.run_id];
    }
  };
  es.onerror = () => {
    es.close();
    delete this._eventSources[runId];
    this.pollActiveRuns();  // single fallback poll
  };
},
```

- [ ] **Step 3: Call startStream from the run trigger handler**

Find the section in `app.js` that handles the response after triggering a run (near `this.activeRuns.unshift(run)`). After adding the run, add:

```javascript
this.startStream(run.run_id);
```

- [ ] **Step 4: Keep pollTimer only for pre-existing runs**

Find `startPolling()`. Update `pollActiveRuns` to skip runs that already have an active EventSource:

```javascript
async pollActiveRuns() {
  const liveRuns = this.activeRuns.filter(r =>
    ['PENDING', 'RUNNING'].includes(r.status) && !this._eventSources[r.run_id]
  );
  for (const run of liveRuns) {
    try {
      const updated = await this.api(`/api/runs/${run.run_id}/status`);
      const idx = this.activeRuns.findIndex(r => r.run_id === run.run_id);
      if (idx !== -1) this.activeRuns[idx] = { ...this.activeRuns[idx], ...updated };
      if (!['PENDING', 'RUNNING'].includes(updated.status)) {
        // Run finished — open a stream for any future pre-existing runs next poll cycle
      }
    } catch {}
  }
},
```

- [ ] **Step 5: Verify JS syntax**

```
node --check frontend/app.js
```

Expected: no output.

- [ ] **Step 6: Commit**

```
git add frontend/app.js
git commit -m "feat(sse): replace Monitor polling with EventSource stream for new runs"
```

---

## Task 10: Trend TTL cache

**Files:**
- Modify: `api/routes/runs.py`
- Modify: `tests/unit/test_dag_retry_trends.py`

- [ ] **Step 1: Write the failing test**

Open `tests/unit/test_dag_retry_trends.py`. Append:

```python
def test_trend_cache_hit_skips_db_query():
    """Second request for same (job_name, metric, window) returns cached result."""
    from unittest.mock import patch, MagicMock
    from api.routes.runs import get_trends, _trends_cache
    import time

    # Clear cache before test
    _trends_cache.clear()

    db = MagicMock()
    db.query.return_value.join.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = []

    # First call — cache miss, DB queried
    result1 = get_trends(job_name="orders", metric="mismatch_rate", window=30, db=db)
    assert db.query.call_count == 1

    # Second call — cache hit, DB not queried again
    result2 = get_trends(job_name="orders", metric="mismatch_rate", window=30, db=db)
    assert db.query.call_count == 1  # still 1 — cache hit
    assert result1 == result2


def test_trend_cache_expires_after_ttl():
    """Cache entry older than _TRENDS_TTL seconds is bypassed."""
    import time
    from api.routes.runs import get_trends, _trends_cache, _TRENDS_TTL
    from unittest.mock import MagicMock

    _trends_cache.clear()

    db = MagicMock()
    db.query.return_value.join.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = []

    get_trends(job_name="payments", metric="total_issues", window=7, db=db)
    assert db.query.call_count == 1

    # Force-expire the cache entry
    key = ("payments", "total_issues", 7)
    ts, val = _trends_cache[key]
    _trends_cache[key] = (ts - _TRENDS_TTL - 1, val)

    get_trends(job_name="payments", metric="total_issues", window=7, db=db)
    assert db.query.call_count == 2  # cache miss — DB queried again
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m pytest tests/unit/test_dag_retry_trends.py::test_trend_cache_hit_skips_db_query -v
```

Expected: `ImportError` or `AttributeError` — `_trends_cache` does not exist.

- [ ] **Step 3: Add TTL cache to runs.py**

Near the top of `api/routes/runs.py` (after imports, before route definitions), add:

```python
import time as _time

_trends_cache: dict[tuple, tuple[float, dict]] = {}
_TRENDS_TTL: int = 60  # seconds
```

- [ ] **Step 4: Wrap the trends query with cache logic**

In the `get_trends` function, add a cache check at the very start (before the `cutoff = ...` line) and a cache store at the very end (before `return`):

```python
@router.get("/trends")
def get_trends(
    job_name: str,
    metric: str = "mismatch_rate",
    window: int = 30,
    db: Session = Depends(get_session),
):
    cache_key = (job_name, metric, window)
    _entry = _trends_cache.get(cache_key)
    if _entry and (_time.monotonic() - _entry[0]) < _TRENDS_TTL:
        return _entry[1]

    # ... existing query code unchanged ...

    result = {
        "job_name": job_name,
        "metric": metric,
        "window": window,
        "points": points,
        "drift_detected": drift_detected,
    }
    _trends_cache[cache_key] = (_time.monotonic(), result)
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

```
python -m pytest tests/unit/test_dag_retry_trends.py -v
```

Expected: all PASS (including the two new tests).

- [ ] **Step 6: Commit**

```
git add api/routes/runs.py tests/unit/test_dag_retry_trends.py
git commit -m "feat(cache): add 60-second TTL cache for /api/runs/trends endpoint"
```

---

## Task 11: DbtArtifactParser

**Files:**
- Create: `etl_framework/dbt/__init__.py`
- Create: `etl_framework/dbt/parser.py`
- Create: `tests/unit/test_dbt_parser.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_dbt_parser.py
import json
import os
import tempfile
import pytest

from etl_framework.dbt.parser import DbtArtifactParser, DbtTestNode
from etl_framework.exceptions import ConfigurationError


def _write_artifact(data: dict) -> str:
    """Write a dbt run_results.json to a temp file and return the path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(data, f)
    f.close()
    return f.name


def _minimal_artifact(results: list[dict]) -> dict:
    return {"metadata": {"dbt_version": "1.7.0"}, "results": results}


def test_parse_returns_all_nodes_when_no_select():
    path = _write_artifact(_minimal_artifact([
        {"unique_id": "test.proj.not_null_id.abc", "status": "pass", "failures": 0, "execution_time": 0.1, "message": None},
        {"unique_id": "test.proj.unique_name.def", "status": "fail", "failures": 3, "execution_time": 0.2, "message": "3 rows"},
    ]))
    try:
        nodes = DbtArtifactParser().parse(path)
        assert len(nodes) == 2
        assert nodes[0].name == "not_null_id"
        assert nodes[0].status == "pass"
        assert nodes[0].failures == 0
        assert nodes[1].name == "unique_name"
        assert nodes[1].status == "fail"
        assert nodes[1].failures == 3
    finally:
        os.unlink(path)


def test_parse_select_filters_by_name_substring():
    path = _write_artifact(_minimal_artifact([
        {"unique_id": "test.proj.not_null_orders_id.abc", "status": "pass", "failures": 0, "execution_time": 0.1, "message": None},
        {"unique_id": "test.proj.unique_customers_email.def", "status": "pass", "failures": 0, "execution_time": 0.1, "message": None},
    ]))
    try:
        nodes = DbtArtifactParser().parse(path, select=["orders"])
        assert len(nodes) == 1
        assert nodes[0].name == "not_null_orders_id"
    finally:
        os.unlink(path)


def test_parse_raises_configuration_error_on_missing_file():
    with pytest.raises(ConfigurationError, match="not found"):
        DbtArtifactParser().parse("/nonexistent/path/run_results.json")


def test_parse_raises_configuration_error_on_malformed_json():
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    f.write("{ not valid json")
    f.close()
    try:
        with pytest.raises(ConfigurationError, match="malformed"):
            DbtArtifactParser().parse(f.name)
    finally:
        os.unlink(f.name)


def test_parse_raises_configuration_error_on_missing_results_key():
    path = _write_artifact({"metadata": {}})
    try:
        with pytest.raises(ConfigurationError, match="missing 'results'"):
            DbtArtifactParser().parse(path)
    finally:
        os.unlink(path)


def test_parse_handles_error_status():
    path = _write_artifact(_minimal_artifact([
        {"unique_id": "test.proj.my_test.abc", "status": "error", "failures": 1, "execution_time": 0.05, "message": "Compilation error"},
    ]))
    try:
        nodes = DbtArtifactParser().parse(path)
        assert nodes[0].status == "error"
        assert nodes[0].message == "Compilation error"
    finally:
        os.unlink(path)


def test_parse_handles_warn_status():
    path = _write_artifact(_minimal_artifact([
        {"unique_id": "test.proj.my_warn.abc", "status": "warn", "failures": 0, "execution_time": 0.05, "message": "Warning"},
    ]))
    try:
        nodes = DbtArtifactParser().parse(path)
        assert nodes[0].status == "warn"
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m pytest tests/unit/test_dbt_parser.py -v
```

Expected: `ModuleNotFoundError` — `etl_framework.dbt` does not exist.

- [ ] **Step 3: Create etl_framework/dbt/__init__.py**

```python
# etl_framework/dbt/__init__.py
```

(empty file)

- [ ] **Step 4: Create etl_framework/dbt/parser.py**

```python
from __future__ import annotations

import json
from dataclasses import dataclass

from etl_framework.exceptions import ConfigurationError


@dataclass
class DbtTestNode:
    name: str
    status: str          # "pass" | "fail" | "error" | "warn"
    failures: int
    execution_time: float
    message: str | None


class DbtArtifactParser:
    def parse(self, path: str, select: list[str] | None = None) -> list[DbtTestNode]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            raise ConfigurationError(
                f"dbt artifact not found: {path}", file_path=path
            )
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"dbt artifact malformed JSON at {path}: {exc}", file_path=path
            )

        raw_results = data.get("results")
        if raw_results is None:
            raise ConfigurationError(
                f"dbt artifact missing 'results' key: {path}", file_path=path
            )

        nodes: list[DbtTestNode] = []
        for r in raw_results:
            uid = r.get("unique_id", "")
            if uid:
                name = uid.rsplit(".", 1)[-1]
            else:
                name = r.get("node", {}).get("name", "unknown")
            nodes.append(DbtTestNode(
                name=name,
                status=r.get("status", "error").lower(),
                failures=int(r.get("failures") or 0),
                execution_time=float(r.get("execution_time", 0.0)),
                message=r.get("message"),
            ))

        if select:
            lo = [s.lower() for s in select]
            nodes = [n for n in nodes if any(s in n.name.lower() for s in lo)]

        return nodes
```

- [ ] **Step 5: Run tests to verify they pass**

```
python -m pytest tests/unit/test_dbt_parser.py -v
```

Expected: 7 tests PASS.

- [ ] **Step 6: Commit**

```
git add etl_framework/dbt/ tests/unit/test_dbt_parser.py
git commit -m "feat(dbt): add DbtArtifactParser for run_results.json ingestion"
```

---

## Task 12: _build_case_dbt + schema update

**Files:**
- Modify: `api/schemas.py`
- Modify: `api/services/run_executor.py`
- Modify: `tests/unit/test_run_executor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_run_executor.py`:

```python
import json
import os
import tempfile


def _write_dbt_artifact(results):
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump({"metadata": {}, "results": results}, f)
    f.close()
    return f.name


def test_build_case_dbt_all_pass():
    from api.schemas import JobDefinition, RunSettings
    from api.services.run_executor import RunExecutor

    path = _write_dbt_artifact([
        {"unique_id": "test.proj.not_null_id.a", "status": "pass", "failures": 0, "execution_time": 0.1, "message": None},
        {"unique_id": "test.proj.unique_name.b", "status": "pass", "failures": 0, "execution_time": 0.1, "message": None},
    ])
    try:
        db = _session()
        RunRepository(db).create_run("run-dbt-1", "dev", "prod", {})
        JobRepository(db).create({
            "name": "my_dbt_tests",
            "description": "",
            "tags": [],
            "job_type": "dbt_test",
            "query": "",
            "key_columns": [],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {"artifact_path": path},
            "enabled": True,
        })
        RunExecutor(
            db=db, run_id="run-dbt-1", source_env="dev", target_env="prod",
            job_sequence=["my_dbt_tests"],
            run_settings=RunSettings(metrics_enabled=False),
        ).execute()
        run = RunRepository(db).get_run("run-dbt-1")
        assert run.status == "PASSED"
        assert run.results[0].source_row_count == 2
        assert run.results[0].value_mismatch_count == 0
    finally:
        os.unlink(path)


def test_build_case_dbt_with_failures():
    from api.schemas import JobDefinition, RunSettings
    from api.services.run_executor import RunExecutor

    path = _write_dbt_artifact([
        {"unique_id": "test.proj.not_null_id.a", "status": "pass", "failures": 0, "execution_time": 0.1, "message": None},
        {"unique_id": "test.proj.unique_email.b", "status": "fail", "failures": 5, "execution_time": 0.2, "message": "5 rows failed"},
    ])
    try:
        db = _session()
        RunRepository(db).create_run("run-dbt-2", "dev", "prod", {})
        JobRepository(db).create({
            "name": "my_dbt_checks",
            "description": "",
            "tags": [],
            "job_type": "dbt_test",
            "query": "",
            "key_columns": [],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {"artifact_path": path},
            "enabled": True,
        })
        RunExecutor(
            db=db, run_id="run-dbt-2", source_env="dev", target_env="prod",
            job_sequence=["my_dbt_checks"],
            run_settings=RunSettings(metrics_enabled=False),
        ).execute()
        run = RunRepository(db).get_run("run-dbt-2")
        assert run.status == "FAILED"
        assert run.results[0].value_mismatch_count == 1
        assert len(run.results[0].mismatches) == 1
        assert run.results[0].mismatches[0].mismatch_type == "dbt_failure"
        assert run.results[0].mismatches[0].source_value == "5"
    finally:
        os.unlink(path)


def test_build_case_dbt_with_error_node():
    from api.schemas import RunSettings
    from api.services.run_executor import RunExecutor

    path = _write_dbt_artifact([
        {"unique_id": "test.proj.bad_test.a", "status": "error", "failures": 1, "execution_time": 0.05, "message": "SQL error"},
    ])
    try:
        db = _session()
        RunRepository(db).create_run("run-dbt-3", "dev", "prod", {})
        JobRepository(db).create({
            "name": "error_test",
            "description": "",
            "tags": [],
            "job_type": "dbt_test",
            "query": "",
            "key_columns": [],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {"artifact_path": path},
            "enabled": True,
        })
        RunExecutor(
            db=db, run_id="run-dbt-3", source_env="dev", target_env="prod",
            job_sequence=["error_test"],
            run_settings=RunSettings(metrics_enabled=False),
        ).execute()
        run = RunRepository(db).get_run("run-dbt-3")
        assert run.status in ("FAILED", "ERROR")
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/unit/test_run_executor.py::test_build_case_dbt_all_pass -v
```

Expected: FAIL — `dbt_test` is not a valid job_type in schemas.

- [ ] **Step 3: Add "dbt_test" to job_type Literal in schemas.py**

Find line 196 in `api/schemas.py`:

```python
job_type: Literal["reconciliation", "health_check", "bo_report", "automic_job"] = "reconciliation"
```

Replace with:

```python
job_type: Literal["reconciliation", "health_check", "bo_report", "automic_job", "dbt_test"] = "reconciliation"
```

Also update the validator (lines 209-214) to add a check for `dbt_test`:

```python
        elif self.job_type == "dbt_test":
            if not self.params.get("artifact_path"):
                raise ValueError("dbt_test jobs require 'artifact_path' in params")
```

- [ ] **Step 4: Add _build_case_dbt to run_executor.py**

Add this method to `RunExecutor` after `_build_case_automic`:

```python
    def _build_case_dbt(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            from etl_framework.dbt.parser import DbtArtifactParser

            path = job.params.get("artifact_path", "")
            select_raw = job.params.get("select", [])
            if isinstance(select_raw, str):
                select = [s.strip() for s in select_raw.split(",") if s.strip()]
            else:
                select = list(select_raw)

            nodes = DbtArtifactParser().parse(path, select)
            total = len(nodes)
            failures = [n for n in nodes if n.status in ("fail", "error")]
            fail_count = len(failures)

            mismatches = [
                MismatchRecord(
                    key_values={"node": n.name},
                    column_name="",
                    source_value=str(n.failures),
                    target_value="0",
                    mismatch_type="dbt_failure",
                )
                for n in failures
            ]

            has_error = any(n.status == "error" for n in failures)
            if has_error:
                status = TestStatus.ERROR
            elif fail_count:
                status = TestStatus.FAILED
            else:
                status = TestStatus.PASSED

            return ReconciliationResult(
                query_name=job.name,
                source_env=self._source_env,
                target_env=self._target_env,
                source_row_count=total,
                target_row_count=total,
                matched_count=total - fail_count,
                missing_in_target_count=0,
                missing_in_source_count=0,
                value_mismatch_count=fail_count,
                mismatches=mismatches,
                status=status,
                executed_at=datetime.now(timezone.utc),
                duration_seconds=sum(n.execution_time for n in nodes),
            )

        return run_job
```

- [ ] **Step 5: Wire dbt_test into _build_case**

In `_build_case`, add the branch before the default reconciliation path:

```python
    def _build_case(self, job: JobDefinition):
        if job.job_type == "bo_report" and self._settings.use_live_connections:
            return self._build_case_bo_report(job)
        if job.job_type == "automic_job" and self._settings.use_live_connections:
            return self._build_case_automic(job)
        if job.job_type == "dbt_test":
            return self._build_case_dbt(job)

        # existing reconciliation path below ...
```

- [ ] **Step 6: Run tests to verify they pass**

```
python -m pytest tests/unit/test_run_executor.py -v
```

Expected: all PASS including the three new dbt tests.

- [ ] **Step 7: Commit**

```
git add api/schemas.py api/services/run_executor.py tests/unit/test_run_executor.py
git commit -m "feat(dbt): add dbt_test job type and _build_case_dbt executor"
```

---

## Task 13: dbt UI job editor fields

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Add dbt_test to job type dropdown**

Find the job type `<select>` in the job editor form. Add:

```html
<option value="dbt_test">dbt Test</option>
```

- [ ] **Step 2: Add dbt_test conditional block**

After the automic_job block added in Task 7, add:

```html
<!-- dbt_test fields -->
<template x-if="editJob.job_type === 'dbt_test'">
  <div>
    <div class="mb-2">
      <label class="form-label">Artifact Path <span class="text-danger">*</span></label>
      <input type="text" class="form-control"
             x-model="editJob.params.artifact_path"
             placeholder="e.g. /path/to/project/target/run_results.json">
      <div class="form-text">Absolute path to dbt <code>target/run_results.json</code>.</div>
    </div>
    <div class="mb-2">
      <label class="form-label">Node Filter <span class="text-muted">(optional)</span></label>
      <input type="text" class="form-control"
             x-model="editJob.params.select"
             placeholder="e.g. orders, customers">
      <div class="form-text">Comma-separated name substrings. Leave blank to include all tests.</div>
    </div>
  </div>
</template>
```

- [ ] **Step 3: Verify JS syntax**

```
node --check frontend/app.js
```

Expected: no output.

- [ ] **Step 4: Commit**

```
git add frontend/app.js
git commit -m "feat(dbt): add dbt_test job editor fields to UI"
```

---

## Task 14: Final integration sweep

**Files:**
- Modify: `tests/integration/test_api_frontend_smoke.py`

- [ ] **Step 1: Run the full test suite**

```
python -m pytest -q
```

Expected: all tests PASS. Note the count — it should be higher than before these changes.

- [ ] **Step 2: Add integration smoke tests for new features**

Open `tests/integration/test_api_frontend_smoke.py`. Append:

```python
def test_audit_endpoint_reachable(client, auth_headers):
    resp = client.get("/api/audit", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_audit_endpoint_filter_by_resource_type(client, auth_headers):
    resp = client.get("/api/audit?resource_type=run", headers=auth_headers)
    assert resp.status_code == 200


def test_sse_stream_endpoint_reachable(client, auth_headers):
    resp = client.post(
        "/api/runs",
        json={"source_env": "dev", "target_env": "prod",
              "job_sequence": [], "run_settings": {"metrics_enabled": False}},
        headers=auth_headers,
    )
    run_id = resp.json()["run_id"]
    with client.stream("GET", f"/api/runs/{run_id}/stream") as stream_resp:
        assert "text/event-stream" in stream_resp.headers["content-type"]


def test_trends_endpoint_returns_cached_on_second_call(client, auth_headers):
    from api.routes.runs import _trends_cache
    _trends_cache.clear()
    r1 = client.get("/api/runs/trends?job_name=orders&metric=mismatch_rate&window=30", headers=auth_headers)
    r2 = client.get("/api/runs/trends?job_name=orders&metric=mismatch_rate&window=30", headers=auth_headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()


def test_dbt_test_job_type_accepted_in_schema():
    from api.schemas import JobDefinition
    job = JobDefinition(
        name="my_dbt_job",
        job_type="dbt_test",
        params={"artifact_path": "/tmp/run_results.json"},
    )
    assert job.job_type == "dbt_test"
    assert job.params["artifact_path"] == "/tmp/run_results.json"
```

- [ ] **Step 3: Run integration tests**

```
python -m pytest tests/integration/ -v
```

Expected: all PASS.

- [ ] **Step 4: Run full suite one final time**

```
python -m pytest -q
```

Expected: all tests PASS, no regressions.

- [ ] **Step 5: Final commit**

```
git add tests/integration/test_api_frontend_smoke.py
git commit -m "test: integration smoke tests for audit, SSE, trend cache, and dbt adapter"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task covering it |
|---|---|
| Automic cred injection from SavedConfig | Task 6 |
| Automic UI fields (job_name / run_id) | Task 7 |
| AuditEvent ORM model | Task 1 |
| AuditRepository (log + list) | Task 1 |
| AuditService (actor from request.state.token) | Task 2 |
| AuditEventOut schema | Task 2 |
| GET /api/audit route | Task 3 |
| Audit calls in runs/jobs/configs/tokens routes | Task 4 |
| History Audit sub-tab | Task 5 |
| SSE _sse_generator function | Task 8 |
| GET /api/runs/{id}/stream endpoint | Task 8 |
| EventSource in Monitor tab | Task 9 |
| Trend TTL cache (_trends_cache dict) | Task 10 |
| DbtArtifactParser + DbtTestNode | Task 11 |
| dbt_test Literal + param validation | Task 12 |
| _build_case_dbt in run_executor | Task 12 |
| dbt UI fields (artifact_path / select) | Task 13 |

All spec requirements covered. No placeholders found. Type names are consistent across all tasks (`DbtTestNode`, `AuditEvent`, `AuditRepository`, `AuditService`, `_sse_generator`, `_trends_cache`, `_TRENDS_TTL`).
