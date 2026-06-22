# Design: Five Feature Improvements
**Date:** 2026-06-15
**Features:** Automic adapter wiring (3), Audit log (5), SSE streaming (6), Trend caching (10), dbt adapter (11)

---

## Overview

Five targeted additions to the ETL Test Framework. All follow Option B (service-per-feature): new standalone files for logic with testable surface, thin routes, no new package dependencies.

---

## Architecture Map

### New files

```
etl_framework/dbt/
  __init__.py
  parser.py              — DbtArtifactParser

api/services/
  audit_service.py       — AuditService
```

### Modified files

```
etl_framework/repository/models.py      — AuditEvent ORM model
etl_framework/repository/repository.py  — AuditRepository class
etl_framework/repository/database.py    — audit_events in init_db()
api/schemas.py                          — "dbt_test" added to job_type Literal
api/routes/runs.py                      — /runs/{id}/stream SSE endpoint + TTLCache
api/routes/jobs.py                      — automic_job UI fields
api/routes/audit.py                     — GET /api/audit endpoint (new route file)
api/services/run_executor.py            — _build_case_dbt(), automic cred path fix
api/main.py                             — include audit router
frontend/app.js                         — EventSource in Monitor, Audit sub-tab in History
```

### No new dependencies

SSE uses FastAPI `StreamingResponse`. Cache uses a hand-rolled TTL dict. dbt parser uses stdlib `json`. Automic uses the existing `requests`/`tenacity` stack already in `etl_framework/automic/client.py`.

---

## Feature 3: Automic Adapter Wiring

### Problem

`_build_case_automic` in `run_executor.py` already exists and calls `AutomicClient` correctly. Two gaps block end-to-end execution:

1. `trigger_run` never populates `automic_credentials` in the config snapshot, so `_build_case_automic` always gets an empty dict and builds a misconfigured `EnvironmentConfig`.
2. The UI job editor has no input fields for `automic_job` params (`job_name`, `run_id`).

### Fix: credential injection

In `trigger_run` (runs.py), after building `config_snapshot`, load the `SavedConfig` by `body.config_id` when provided and merge automic fields from `config_json` into the snapshot:

```python
if body.config_id:
    saved = ConfigRepository(db).get(body.config_id)
    if saved and saved.config_json:
        cfg = saved.config_json
        snapshot["automic_credentials"] = {
            "name": saved.name,
            "automic_url": cfg.get("automic_url", ""),
            "automic_user": cfg.get("automic_user", ""),
            "automic_password": cfg.get("automic_password", ""),
            "automic_timeout": cfg.get("automic_timeout", 30),
            "automic_max_retries": cfg.get("automic_max_retries", 3),
        }
```

### Fix: UI job editor

In `app.js`, inside the job editor template, add a conditional block rendered when `job.job_type === 'automic_job'`:

- `job_name` text input (maps to `job.params.job_name`)
- `run_id` text input (maps to `job.params.run_id`)
- Helper text: "Provide job_name OR run_id — run_id takes priority."

Schema validation (`api/schemas.py` lines 212-214) already enforces that at least one is present. No schema change needed.

### Acceptance criteria

- A saved `automic_job` job with `job_name` set and `use_live_connections=true` executes `AutomicClient.get_status_by_job_name` and stores the mapped `TestStatus` as a `TestResult`.
- UI renders `job_name` / `run_id` fields when job type is `automic_job`.
- Unit test: `_build_case_automic` with a mock `AutomicClient` returns `ReconciliationResult` with correct status.

---

## Feature 5: Audit Log

### Data model

New ORM model added to `etl_framework/repository/models.py`:

```python
class AuditEvent(Base):
    __tablename__ = "audit_events"

    id            = Column(Integer, primary_key=True, index=True)
    actor         = Column(String(255), nullable=True)   # token name or "system"
    action        = Column(String(100), nullable=False)  # e.g. "mismatch.accepted"
    resource_type = Column(String(50),  nullable=False)  # "run"|"job"|"config"|"mismatch"|"token"
    resource_id   = Column(String(255), nullable=True)
    diff          = Column(JSON,        nullable=True)   # before/after or extra context
    created_at    = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
```

`init_db()` in `database.py` creates the table on startup (same pattern as existing tables).

### AuditRepository

Added to `repository.py`:

```python
class AuditRepository:
    def log(self, actor, action, resource_type, resource_id, diff=None): ...
    def list(self, resource_type=None, resource_id=None, limit=50, offset=0): ...
```

### AuditService

`api/services/audit_service.py` wraps `AuditRepository` and resolves the actor from the request:

```python
class AuditService:
    def __init__(self, db: Session) -> None: ...

    def log(self, request: Request, action: str, resource_type: str,
            resource_id: str, diff: dict | None = None) -> None:
        token = getattr(request.state, "token", None)
        actor = token.name if token else "system"
        AuditRepository(self._db).log(actor, action, resource_type, resource_id, diff)
```

Called synchronously before route response — audit failures surface as 500 errors, not silent drops.

### Audited actions

| Route | Action |
|---|---|
| `POST /api/runs` | `run.created` |
| `DELETE /api/runs/{id}` | `run.deleted` |
| `PATCH .../mismatches/{id}/accept` | `mismatch.accepted` |
| `POST /api/jobs` | `job.created` |
| `PUT /api/jobs/{name}` | `job.updated` |
| `DELETE /api/jobs/{name}` | `job.deleted` |
| `POST /api/configs` | `config.created` |
| `PUT /api/configs/{id}` | `config.updated` |
| `DELETE /api/configs/{id}` | `config.deleted` |
| `POST /api/tokens` | `token.created` |
| `DELETE /api/tokens/{id}` | `token.deleted` |

### Actor resolution

`BearerTokenMiddleware` sets `request.state.token` (an `ApiToken` ORM object) on authenticated requests. `/api/runs/` paths are currently exempt from auth (see `api/middleware/auth.py` `_EXEMPT_PREFIXES`), so `request.state.token` may be absent on run routes.

`AuditService.log` resolves the actor as:

```python
token = getattr(request.state, "token", None)
actor = token.name if token else "system"
```

Falls back to `"system"` for background tasks and exempt paths.

### API

New route file `api/routes/audit.py`:

```
GET /api/audit
  ?resource_type=run|job|config|mismatch|token
  ?resource_id=<string>
  ?limit=50   (max 500)
  ?offset=0
```

Returns `list[AuditEventOut]`:
```json
[{"id": 1, "actor": "ci-token", "action": "run.created",
  "resource_type": "run", "resource_id": "abc-123",
  "diff": null, "created_at": "2026-06-15T10:00:00Z"}]
```

Registered in `api/main.py` under prefix `/api/audit`.

### UI: Audit sub-tab

History tab gains a 4th sub-tab "Audit" alongside Runs / Trends / Lineage.

- On tab activation: `GET /api/audit?limit=100`
- Table columns: Timestamp · Actor · Action · Resource Type · Resource ID
- `<select>` filter for resource_type; on change re-fetches with `?resource_type=<value>`
- No pagination in v1 (100-row cap is sufficient for initial use)

### Acceptance criteria

- Every audited action writes one `AuditEvent` row with correct actor, action, and resource_id.
- `GET /api/audit?resource_type=run` returns only run events.
- Actor is the token name for API-triggered actions.
- UI Audit sub-tab renders the table and filter correctly.
- Unit tests for `AuditRepository.log` and `AuditService.log`.
- API test: trigger a run → accept a mismatch → query `/api/audit` → verify both rows present.

---

## Feature 6: SSE Streaming for Monitor Tab

### New endpoint

```
GET /api/runs/{run_id}/stream
Content-Type: text/event-stream
```

Implementation in `runs.py`:

```python
@router.get("/{run_id}/stream")
async def stream_run(run_id: str):
    async def generate():
        from etl_framework.repository.database import SessionLocal
        import asyncio, json

        while True:
            db = SessionLocal()
            try:
                run = RunRepository(db).get_run(run_id)
                if run is None:
                    yield "event: error\ndata: {\"detail\": \"not found\"}\n\n"
                    return
                payload = json.dumps({
                    "run_id": run_id, "status": run.status,
                    "passed": run.passed, "failed": run.failed,
                    "slow": run.slow, "error": run.error,
                    "total_tests": run.total_tests,
                })
                yield f"data: {payload}\n\n"
                if run.status in _TERMINAL:
                    return
            except GeneratorExit:
                return
            finally:
                db.close()
            await asyncio.sleep(1.5)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
```

`_TERMINAL` is already defined in `runs.py` as `{"PASSED", "FAILED", "SLOW", "ERROR", "COMPLETED"}`.

`X-Accel-Buffering: no` disables nginx proxy buffering, which would otherwise hold SSE frames until a buffer threshold.

### Frontend changes

In `app.js`, `startPolling()` is replaced:

```javascript
startStream(runId) {
  const es = new EventSource(`/api/runs/${runId}/stream`);
  es.onmessage = (e) => {
    const data = JSON.parse(e.data);
    const idx = this.activeRuns.findIndex(r => r.run_id === data.run_id);
    if (idx !== -1) this.activeRuns[idx] = { ...this.activeRuns[idx], ...data };
    if (['PASSED','FAILED','SLOW','ERROR'].includes(data.status)) es.close();
  };
  es.onerror = () => {
    es.close();
    this.pollActiveRuns(); // single fallback poll
  };
},
```

`startPolling` interval is kept only as a safety net for runs that were in progress before the page loaded (when `this.activeRuns` is pre-populated from `GET /api/runs` on mount). For those, the existing 5-second interval runs one cycle and then replaces itself with a stream once the run_id is known.

### Acceptance criteria

- `GET /api/runs/{run_id}/stream` emits JSON status frames every ~1.5 seconds.
- Stream closes automatically when run reaches a terminal status.
- Monitor tab updates in real time without polling interval for new runs.
- Fallback poll fires once on EventSource error.
- Unit test: async generator yields correct frames and stops at terminal status.

---

## Feature 10: Trend Query Caching

### Implementation

Module-level TTL cache in `runs.py` (above the route functions):

```python
import time as _time

_trends_cache: dict[tuple, tuple[float, dict]] = {}
_TRENDS_TTL: int = 60  # seconds
```

In `get_trends`:

```python
cache_key = (job_name, metric, window)
_entry = _trends_cache.get(cache_key)
if _entry and (_time.monotonic() - _entry[0]) < _TRENDS_TTL:
    return _entry[1]

# ... existing query logic ...

result = {"job_name": job_name, ...}
_trends_cache[cache_key] = (_time.monotonic(), result)
return result
```

### Why no lock

CPython's GIL makes `dict.__getitem__` and `dict.__setitem__` atomic for this pattern. Two concurrent requests for the same key may both miss the cache and both write — the second write replaces the first with an identical result. This is acceptable (cache stampede risk is negligible for a 60-second TTL with low cardinality keys).

### Cache invalidation

No explicit invalidation. TTL expiry is the only mechanism. 60 seconds is short enough that drift alerts appear within one minute of a completed run.

### Acceptance criteria

- Two back-to-back requests for the same `(job_name, metric, window)` within 60 seconds hit the cache (verified by mocking the DB query and confirming it's called only once).
- After 60 seconds the cache is bypassed and the query runs again.
- Unit test: cache hit returns identical dict object reference.

---

## Feature 11: dbt Test Result Adapter

### New job type

`"dbt_test"` added to the `job_type` Literal in `api/schemas.py`:

```python
job_type: Literal[
    "reconciliation", "health_check", "bo_report", "automic_job", "dbt_test"
] = "reconciliation"
```

Schema validation: `dbt_test` jobs require `artifact_path` in params.

### DbtArtifactParser

`etl_framework/dbt/parser.py`:

```python
@dataclass
class DbtTestNode:
    name: str
    status: str          # "pass" | "fail" | "error" | "warn"
    failures: int
    execution_time: float
    message: str | None

class DbtArtifactParser:
    def parse(self, path: str, select: list[str] | None = None) -> list[DbtTestNode]:
        ...
```

Raises `ConfigurationError` (existing exception class in `etl_framework/exceptions.py`) when:
- File does not exist at `path`
- JSON is malformed
- Top-level `results` key is missing

`select` filters nodes whose `unique_id` or `name` contains any of the substrings (case-insensitive). Empty list = include all.

### dbt `run_results.json` contract

The parser targets dbt schema v4 (dbt ≥ 1.0). Key fields consumed:

```json
{
  "results": [
    {
      "unique_id": "test.project.not_null_orders_id.abc",
      "status": "pass|fail|error|warn",
      "failures": 0,
      "execution_time": 0.12,
      "message": null
    }
  ]
}
```

Node name is extracted as the portion after the last `.` in `unique_id` (e.g. `not_null_orders_id`). If `unique_id` is absent, falls back to `node.name` if present.

### Status mapping

| dbt status | Contribution |
|---|---|
| `pass` | counted as matched |
| `warn` | counted as matched (message stored in mismatch note) |
| `fail` | `value_mismatch_count++`, MismatchRecord created |
| `error` | `value_mismatch_count++`, MismatchRecord created, overall → ERROR |

Overall `ReconciliationResult.status`:
- Any `error` node → `TestStatus.ERROR`
- Any `fail` node (no errors) → `TestStatus.FAILED`
- Otherwise → `TestStatus.PASSED`

### MismatchRecord shape for failures

```python
MismatchRecord(
    key_values={"node": node.name},
    column_name="",
    source_value=str(node.failures),  # actual failure count
    target_value="0",                  # expected
    mismatch_type="dbt_failure",
)
```

### _build_case_dbt in run_executor.py

```python
def _build_case_dbt(self, job: JobDefinition):
    def run_job() -> ReconciliationResult:
        from etl_framework.dbt.parser import DbtArtifactParser
        path = job.params.get("artifact_path", "")
        select = job.params.get("select", [])
        nodes = DbtArtifactParser().parse(path, select)
        # map nodes → ReconciliationResult (see status mapping above)
        ...
    return run_job
```

Called from `_build_case` when `job.job_type == "dbt_test"` (no `use_live_connections` gate — file reading works in both modes).

### UI: dbt job editor fields

When `job.job_type === 'dbt_test'`:
- `artifact_path` text input: "Path to dbt `target/run_results.json`"
- `select` text input (comma-separated): "Optional node name filter"

### Acceptance criteria

- `DbtArtifactParser.parse` on a valid `run_results.json` returns correct `DbtTestNode` list.
- `DbtArtifactParser.parse` raises `ConfigurationError` on missing file.
- `_build_case_dbt` with 2 fail nodes produces `ReconciliationResult` with `value_mismatch_count=2`, `status=FAILED`, and 2 `MismatchRecord` entries.
- `_build_case_dbt` with 1 error node produces `status=ERROR`.
- Unit test for `select` filtering.
- API smoke test: create a `dbt_test` job, trigger a run pointing at a fixture `run_results.json`, confirm result is persisted.

---

## Test Strategy

| Layer | What is tested |
|---|---|
| Unit — `AuditRepository` | `log()` writes correct row; `list()` filters by resource_type |
| Unit — `AuditService` | actor resolved from `request.state.token_name`; falls back to "system" |
| Unit — `DbtArtifactParser` | valid file, missing file, malformed JSON, select filtering, all 4 status mappings |
| Unit — `_build_case_dbt` | fail count, error promotion, empty result set |
| Unit — `_build_case_automic` | mock client returns PASSED / FAILED, mapped correctly |
| Unit — SSE generator | frames emitted per tick; stops at terminal status |
| Unit — trend cache | cache hit skips query; stale entry triggers re-query |
| API — audit | trigger run → accept mismatch → `GET /api/audit` returns both rows |
| API — SSE | `GET /api/runs/{id}/stream` returns `text/event-stream` content-type |
| API — dbt job | create dbt_test job → trigger run → result persisted with correct counts |
| Integration smoke | existing `test_api_frontend_smoke.py` still passes after all changes |

---

## Implementation Order

Dependencies first:

1. `AuditEvent` ORM model + `AuditRepository` (no dependents, safe first)
2. `AuditService` + `api/routes/audit.py` + `api/main.py` wiring
3. Audit calls in runs / jobs / configs / tokens routes + History Audit sub-tab
4. Automic cred injection in `trigger_run` + UI job editor fields
5. SSE endpoint in `runs.py` + `startStream` in `app.js`
6. Trend TTL cache in `runs.py`
7. `etl_framework/dbt/parser.py` + `_build_case_dbt` + schema + UI fields

Each step has its own passing tests before the next begins.
