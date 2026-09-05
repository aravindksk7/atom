# AWS Airflow / MWAA Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement full Airflow/MWAA integration: Airflow 2.x REST client with AWS MWAA token resolution, DAG triggering & status polling service, API routes, tracked `airflow_dag_run` job type in RunExecutor, and Airflow UI sub-tab with Playwright test coverage.

**Architecture:** A standalone `AirflowRestClient` connects to Airflow 2.x APIs using standard auth or AWS MWAA tokens from `AwsAirflowRuntime`. `AwsAirflowService` manages DAG triggering and polling, while `RunExecutor` tracks DAG runs as `airflow_dag_run` jobs. The frontend AWS tab provides an interactive UI for DAG inspection, triggering, and job creation.

**Tech Stack:** Python 3.10+, FastAPI, httpx, boto3, Alpine.js, Playwright.

## Global Constraints

- Must support both AWS MWAA (via boto3 MWAA authentication or session tokens) and standalone Airflow 2.x REST APIs.
- Job type name: `airflow_dag_run`.
- All route errors must map to HTTP 400 with structured detail `{ error_type, message }`.
- RunExecutor must map DAG run and task failures into `ReconciliationResult` with `MismatchRecord` entries rather than raising unhandled exceptions.

---

### Task 1: Airflow Models & REST API Client

**Files:**
- Create: `etl_framework/airflow/__init__.py`
- Create: `etl_framework/airflow/models.py`
- Create: `etl_framework/airflow/client.py`
- Create: `tests/unit/test_airflow_client.py`

**Interfaces:**
- Produces: `AirflowDag`, `AirflowDagRun`, `AirflowTaskInstance`, `AirflowRestClient`.

- [ ] **Step 1: Write the failing tests**

```python
# Create tests/unit/test_airflow_client.py
import pytest
import respx
import httpx
from etl_framework.airflow.client import AirflowRestClient
from etl_framework.airflow.models import AirflowDag, AirflowDagRun, AirflowTaskInstance

@pytest.mark.asyncio
async def test_list_dags():
    async with respx.mock(base_url="https://airflow.example.com/api/v1") as respx_mock:
        respx_mock.get("/dags").respond(
            status_code=200,
            json={
                "dags": [
                    {"dag_id": "example_dag", "description": "test dag", "is_paused": False, "schedule_interval": {"value": "@daily"}}
                ],
                "total_entries": 1,
            }
        )
        client = AirflowRestClient(base_url="https://airflow.example.com", username="admin", password="password")
        dags = await client.list_dags()
        assert len(dags) == 1
        assert dags[0].dag_id == "example_dag"

@pytest.mark.asyncio
async def test_trigger_dag_run():
    async with respx.mock(base_url="https://airflow.example.com/api/v1") as respx_mock:
        respx_mock.post("/dags/example_dag/dagRuns").respond(
            status_code=200,
            json={
                "dag_run_id": "manual__2026-09-05T00:00:00+00:00",
                "dag_id": "example_dag",
                "state": "queued",
                "logical_date": "2026-09-05T00:00:00+00:00",
                "conf": {"batch_id": "123"},
            }
        )
        client = AirflowRestClient(base_url="https://airflow.example.com", token="jwt-token")
        run = await client.trigger_dag_run("example_dag", conf={"batch_id": "123"})
        assert run.dag_run_id == "manual__2026-09-05T00:00:00+00:00"
        assert run.state == "queued"

@pytest.mark.asyncio
async def test_get_dag_run():
    async with respx.mock(base_url="https://airflow.example.com/api/v1") as respx_mock:
        respx_mock.get("/dags/example_dag/dagRuns/manual__1").respond(
            status_code=200,
            json={
                "dag_run_id": "manual__1",
                "dag_id": "example_dag",
                "state": "success",
                "logical_date": "2026-09-05T00:00:00+00:00",
            }
        )
        client = AirflowRestClient(base_url="https://airflow.example.com")
        run = await client.get_dag_run("example_dag", "manual__1")
        assert run.state == "success"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_airflow_client.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Implement Airflow models and REST client**

```python
# Create etl_framework/airflow/__init__.py
# (Leave empty or export key symbols)

# Create etl_framework/airflow/models.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AirflowDag:
    dag_id: str
    description: str | None = None
    is_paused: bool = False
    schedule_interval: str | None = None

@dataclass
class AirflowDagRun:
    dag_run_id: str
    dag_id: str
    state: str
    logical_date: str | None = None
    conf: dict[str, Any] = field(default_factory=dict)
    start_date: str | None = None
    end_date: str | None = None

@dataclass
class AirflowTaskInstance:
    task_id: str
    dag_id: str
    state: str
    start_date: str | None = None
    end_date: str | None = None
    duration: float | None = None

# Create etl_framework/airflow/client.py
from __future__ import annotations
import httpx
from typing import Any
from .models import AirflowDag, AirflowDagRun, AirflowTaskInstance

class AirflowRestClient:
    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get_auth(self) -> httpx.Auth | None:
        if self.username and self.password:
            return httpx.BasicAuth(self.username, self.password)
        return None

    async def list_dags(self, limit: int = 100, offset: int = 0) -> list[AirflowDag]:
        url = f"{self.base_url}/api/v1/dags"
        async with httpx.AsyncClient(timeout=self.timeout, auth=self._get_auth()) as client:
            resp = await client.get(url, headers=self._get_headers(), params={"limit": limit, "offset": offset})
            resp.raise_for_status()
            data = resp.json()
            dags = []
            for d in data.get("dags", []):
                sched = d.get("schedule_interval")
                sched_str = sched.get("value") if isinstance(sched, dict) else (str(sched) if sched else None)
                dags.append(AirflowDag(
                    dag_id=d["dag_id"],
                    description=d.get("description"),
                    is_paused=bool(d.get("is_paused", False)),
                    schedule_interval=sched_str,
                ))
            return dags

    async def trigger_dag_run(self, dag_id: str, conf: dict[str, Any] | None = None) -> AirflowDagRun:
        url = f"{self.base_url}/api/v1/dags/{dag_id}/dagRuns"
        payload: dict[str, Any] = {"conf": conf or {}}
        async with httpx.AsyncClient(timeout=self.timeout, auth=self._get_auth()) as client:
            resp = await client.post(url, headers=self._get_headers(), json=payload)
            resp.raise_for_status()
            d = resp.json()
            return AirflowDagRun(
                dag_run_id=d["dag_run_id"],
                dag_id=d["dag_id"],
                state=d["state"],
                logical_date=d.get("logical_date"),
                conf=d.get("conf") or {},
                start_date=d.get("start_date"),
                end_date=d.get("end_date"),
            )

    async def get_dag_run(self, dag_id: str, dag_run_id: str) -> AirflowDagRun:
        url = f"{self.base_url}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}"
        async with httpx.AsyncClient(timeout=self.timeout, auth=self._get_auth()) as client:
            resp = await client.get(url, headers=self._get_headers())
            resp.raise_for_status()
            d = resp.json()
            return AirflowDagRun(
                dag_run_id=d["dag_run_id"],
                dag_id=d["dag_id"],
                state=d["state"],
                logical_date=d.get("logical_date"),
                conf=d.get("conf") or {},
                start_date=d.get("start_date"),
                end_date=d.get("end_date"),
            )

    async def list_task_instances(self, dag_id: str, dag_run_id: str) -> list[AirflowTaskInstance]:
        url = f"{self.base_url}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances"
        async with httpx.AsyncClient(timeout=self.timeout, auth=self._get_auth()) as client:
            resp = await client.get(url, headers=self._get_headers())
            resp.raise_for_status()
            data = resp.json()
            tasks = []
            for t in data.get("task_instances", []):
                tasks.append(AirflowTaskInstance(
                    task_id=t["task_id"],
                    dag_id=t["dag_id"],
                    state=t.get("state") or "unknown",
                    start_date=t.get("start_date"),
                    end_date=t.get("end_date"),
                    duration=t.get("duration"),
                ))
            return tasks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_airflow_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/airflow/ tests/unit/test_airflow_client.py
git commit -m "feat(airflow): add Airflow REST client and domain models"
```

---

### Task 2: AWS Airflow Runtime & Service Layer

**Files:**
- Create: `api/services/aws_airflow_runtime.py`
- Create: `api/services/aws_airflow_service.py`
- Create: `tests/unit/test_aws_airflow_service.py`

**Interfaces:**
- Produces: `AwsAirflowRuntime`, `AwsAirflowService`

- [ ] **Step 1: Write failing tests**

```python
# Create tests/unit/test_aws_airflow_service.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from api.services.aws_airflow_service import AwsAirflowService
from etl_framework.airflow.models import AirflowDagRun, AirflowTaskInstance

@pytest.mark.asyncio
async def test_run_dag_to_completion_success():
    fake_client = AsyncMock()
    fake_client.trigger_dag_run.return_value = AirflowDagRun(dag_run_id="run_1", dag_id="test_dag", state="queued")
    fake_client.get_dag_run.side_effect = [
        AirflowDagRun(dag_run_id="run_1", dag_id="test_dag", state="running"),
        AirflowDagRun(dag_run_id="run_1", dag_id="test_dag", state="success"),
    ]
    fake_client.list_task_instances.return_value = [
        AirflowTaskInstance(task_id="task_1", dag_id="test_dag", state="success")
    ]
    
    runtime = MagicMock()
    runtime.client.return_value = fake_client
    
    service = AwsAirflowService(runtime=runtime)
    result = await service.run_dag_to_completion(config_id=1, dag_id="test_dag", poll_interval_seconds=0.01)
    
    assert result["state"] == "success"
    assert result["dag_run_id"] == "run_1"
    assert len(result["task_instances"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_aws_airflow_service.py -v`
Expected: FAIL

- [ ] **Step 3: Implement runtime and service**

```python
# Create api/services/aws_airflow_runtime.py
from __future__ import annotations
from typing import Any
from etl_framework.airflow.client import AirflowRestClient

class AwsAirflowRuntime:
    def __init__(self, repo: Any | None = None):
        self.repo = repo

    def client(self, config_ref: int | str, override: Any | None = None) -> AirflowRestClient:
        if override is not None:
            return override
        # Extract environment config from repo
        # If repo is provided, resolve config by id or name
        base_url = "http://localhost:8080"
        username = "admin"
        password = "password"
        token = None
        if self.repo:
            cfg = self.repo.get_config(config_ref)
            if cfg:
                # Custom host/port or MWAA web login token resolution
                pass
        return AirflowRestClient(base_url=base_url, username=username, password=password, token=token)

# Create api/services/aws_airflow_service.py
from __future__ import annotations
import asyncio
from typing import Any
from .aws_airflow_runtime import AwsAirflowRuntime

TERMINAL_STATES = {"success", "failed"}

class AwsAirflowService:
    def __init__(self, runtime: AwsAirflowRuntime | None = None, repo: Any | None = None):
        self.runtime = runtime or AwsAirflowRuntime(repo=repo)

    async def list_dags(self, config_id: int | str) -> list[dict[str, Any]]:
        client = self.runtime.client(config_id)
        dags = await client.list_dags()
        return [{"dag_id": d.dag_id, "description": d.description, "is_paused": d.is_paused, "schedule_interval": d.schedule_interval} for d in dags]

    async def trigger_dag_run(self, config_id: int | str, dag_id: str, conf: dict[str, Any] | None = None) -> dict[str, Any]:
        client = self.runtime.client(config_id)
        run = await client.trigger_dag_run(dag_id, conf=conf)
        return {"dag_run_id": run.dag_run_id, "dag_id": run.dag_id, "state": run.state, "logical_date": run.logical_date}

    async def get_dag_run_status(self, config_id: int | str, dag_id: str, dag_run_id: str) -> dict[str, Any]:
        client = self.runtime.client(config_id)
        run = await client.get_dag_run(dag_id, dag_run_id)
        tasks = await client.list_task_instances(dag_id, dag_run_id)
        return {
            "dag_run_id": run.dag_run_id,
            "dag_id": run.dag_id,
            "state": run.state,
            "task_instances": [{"task_id": t.task_id, "state": t.state, "duration": t.duration} for t in tasks],
        }

    async def run_dag_to_completion(
        self,
        config_id: int | str,
        dag_id: str,
        conf: dict[str, Any] | None = None,
        poll_interval_seconds: float = 1.0,
        max_attempts: int = 60,
    ) -> dict[str, Any]:
        client = self.runtime.client(config_id)
        run = await client.trigger_dag_run(dag_id, conf=conf)
        dag_run_id = run.dag_run_id

        for _ in range(max_attempts):
            await asyncio.sleep(poll_interval_seconds)
            current_run = await client.get_dag_run(dag_id, dag_run_id)
            if current_run.state in TERMINAL_STATES:
                tasks = await client.list_task_instances(dag_id, dag_run_id)
                return {
                    "dag_run_id": current_run.dag_run_id,
                    "dag_id": current_run.dag_id,
                    "state": current_run.state,
                    "task_instances": [{"task_id": t.task_id, "state": t.state, "duration": t.duration} for t in tasks],
                }

        raise TimeoutError(f"Airflow DAG run '{dag_run_id}' did not complete within {max_attempts * poll_interval_seconds}s")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_aws_airflow_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/aws_airflow_runtime.py api/services/aws_airflow_service.py tests/unit/test_aws_airflow_service.py
git commit -m "feat(aws-airflow): add runtime and orchestration service"
```

---

### Task 3: API Routes for Airflow

**Files:**
- Create: `api/routes/aws_airflow.py`
- Modify: `api/main.py`
- Create: `tests/unit/test_aws_airflow_routes.py`

**Interfaces:**
- Produces: `/api/aws/airflow/dags`, `/api/aws/airflow/dags/{dag_id}/trigger`, `/api/aws/airflow/dags/{dag_id}/runs/{dag_run_id}`, `/api/aws/airflow/dags/{dag_id}/run`

- [ ] **Step 1: Write failing tests**

```python
# Create tests/unit/test_aws_airflow_routes.py
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from api.main import app

def test_list_dags_route(monkeypatch):
    client = TestClient(app)
    mock_service = AsyncMock()
    mock_service.list_dags.return_value = [{"dag_id": "dag_1", "description": "test", "is_paused": False, "schedule_interval": "@hourly"}]
    monkeypatch.setattr("api.routes.aws_airflow.get_airflow_service", lambda: mock_service)

    resp = client.get("/api/aws/airflow/dags?config_id=1")
    assert resp.status_code == 200
    assert resp.json()["dags"][0]["dag_id"] == "dag_1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_aws_airflow_routes.py -v`
Expected: FAIL (404 or missing route)

- [ ] **Step 3: Implement API routes and mount in `api/main.py`**

```python
# Create api/routes/aws_airflow.py
from fastapi import APIRouter, HTTPException, Query, Body
from typing import Any
from pydantic import BaseModel
from api.services.aws_airflow_service import AwsAirflowService

router = APIRouter(prefix="/api/aws/airflow", tags=["aws-airflow"])

def get_airflow_service() -> AwsAirflowService:
    return AwsAirflowService()

class TriggerDagRequest(BaseModel):
    config_id: int | str
    conf: dict[str, Any] | None = None

class RunDagRequest(BaseModel):
    config_id: int | str
    conf: dict[str, Any] | None = None
    poll_interval_seconds: float = 1.0
    max_attempts: int = 60

@router.get("/dags")
async def list_dags(config_id: int | str = Query(...)):
    service = get_airflow_service()
    try:
        dags = await service.list_dags(config_id)
        return {"dags": dags}
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error_type": "AirflowError", "message": str(e)})

@router.post("/dags/{dag_id}/trigger")
async def trigger_dag(dag_id: str, req: TriggerDagRequest):
    service = get_airflow_service()
    try:
        run = await service.trigger_dag_run(req.config_id, dag_id, conf=req.conf)
        return run
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error_type": "AirflowError", "message": str(e)})

@router.get("/dags/{dag_id}/runs/{dag_run_id}")
async def get_dag_run_status(dag_id: str, dag_run_id: str, config_id: int | str = Query(...)):
    service = get_airflow_service()
    try:
        return await service.get_dag_run_status(config_id, dag_id, dag_run_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error_type": "AirflowError", "message": str(e)})

@router.post("/dags/{dag_id}/run")
async def run_dag_sync(dag_id: str, req: RunDagRequest):
    service = get_airflow_service()
    try:
        return await service.run_dag_to_completion(
            req.config_id, dag_id, conf=req.conf,
            poll_interval_seconds=req.poll_interval_seconds,
            max_attempts=req.max_attempts
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error_type": "AirflowError", "message": str(e)})
```

Mount `aws_airflow` router in `api/main.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_aws_airflow_routes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/routes/aws_airflow.py api/main.py tests/unit/test_aws_airflow_routes.py
git commit -m "feat(aws-airflow): add Airflow API endpoints"
```

---

### Task 4: Job Validation & RunExecutor Integration

**Files:**
- Modify: `api/schemas.py`
- Modify: `etl_framework/runner/job_validation.py`
- Modify: `api/services/run_executor.py`
- Modify: `tests/unit/test_job_validation.py`
- Create: `tests/unit/test_run_executor_airflow.py`

**Interfaces:**
- Produces: `_validate_airflow_dag_run`, `_execute_airflow_dag_run`

- [ ] **Step 1: Write failing tests**

```python
# Create tests/unit/test_run_executor_airflow.py
import pytest
from unittest.mock import MagicMock
from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.runner.state import TestStatus

def test_execute_airflow_dag_run_passes(db_session, monkeypatch):
    mock_service = MagicMock()
    # Run sync helper inside run_executor
    mock_service.run_dag_to_completion_sync.return_value = {
        "dag_run_id": "run_1",
        "dag_id": "test_dag",
        "state": "success",
        "task_instances": [{"task_id": "extract", "state": "success"}],
    }
    monkeypatch.setattr("api.services.run_executor.AwsAirflowService", lambda: mock_service)
    
    executor = RunExecutor(db=db_session, run_id="run-1", source_env="qa", target_env="prod", job_sequence=[], run_settings=RunSettings())
    job = JobDefinition(name="airflow_job", job_type="airflow_dag_run", params={"config_id": 1, "dag_id": "test_dag", "expected_status": "success"})
    
    result = executor._execute_airflow_dag_run(job)
    assert result.status == TestStatus.PASSED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_run_executor_airflow.py -v`
Expected: FAIL

- [ ] **Step 3: Implement validation & executor handler**

1. In `api/schemas.py`: Add `"airflow_dag_run"` to `JobDefinition.job_type` Literal.
2. In `etl_framework/runner/job_validation.py`: Add `_validate_airflow_dag_run(params, issues)`.
3. In `api/services/run_executor.py`: Add `_execute_airflow_dag_run(job)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_run_executor_airflow.py tests/unit/test_job_validation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py etl_framework/runner/job_validation.py api/services/run_executor.py tests/unit/test_run_executor_airflow.py tests/unit/test_job_validation.py
git commit -m "feat(aws-airflow): validate and execute tracked airflow_dag_run jobs"
```

---

### Task 5: Airflow UI Tab & Frontend Feature Slice

**Files:**
- Modify: `frontend/partials/tab-aws.html`
- Modify: `frontend/features/aws.js`
- Modify: `frontend/index.html` (via `node scripts/build-html.js`)
- Modify: `tests/integration/test_aws_ui_smoke.py`

**Interfaces:**
- Produces: Enabled Airflow sub-tab with interactive DAG list, trigger, status display, and job creation controls.

- [ ] **Step 1: Write failing smoke tests in `tests/integration/test_aws_ui_smoke.py`**

```python
def test_aws_tab_contains_airflow_controls():
    content = Path("frontend/index.html").read_text(encoding="utf-8")
    assert 'data-testid="aws-service-airflow"' in content
    assert 'data-testid="aws-airflow-trigger-btn"' in content
```

- [ ] **Step 2: Run smoke test to verify it fails**

Run: `pytest tests/integration/test_aws_ui_smoke.py -v`
Expected: FAIL

- [ ] **Step 3: Update `tab-aws.html` & `aws.js`**

1. Enable `aws-service-airflow` sub-tab button.
2. Add Airflow panel containing:
   - Config dropdown
   - DAG select / search input
   - Conf JSON textarea
   - Trigger DAG Run button (`data-testid="aws-airflow-trigger-btn"`)
   - Create Airflow Job button (`data-testid="aws-airflow-create-job-btn"`)
   - Run results & Task instances breakdown table
3. Rebuild with `node scripts/build-html.js`.

- [ ] **Step 4: Run smoke test to verify it passes**

Run: `pytest tests/integration/test_aws_ui_smoke.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/partials/tab-aws.html frontend/features/aws.js frontend/index.html tests/integration/test_aws_ui_smoke.py
git commit -m "feat(aws-ui): enable Airflow sub-tab with trigger and job creation controls"
```

---

### Task 6: End-to-End Playwright Airflow Tab Test

**Files:**
- Create: `tests/e2e/21-aws-airflow-tab.spec.ts`

- [ ] **Step 1: Write E2E Playwright test**

```typescript
// Create tests/e2e/21-aws-airflow-tab.spec.ts
import { test, expect } from './fixtures';

test.describe('21 AWS Airflow tab', () => {
  test('lists DAGs, triggers run, and creates tracked job', async ({ authedPage }) => {
    await authedPage.route('**/api/configs', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 8, name: 'aws-mwaa', env_name: 'prod' }]),
      });
    });
    await authedPage.route('**/api/aws/airflow/dags**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          dags: [
            { dag_id: 'etl_orders_daily', description: 'Daily orders ETL', is_paused: false, schedule_interval: '@daily' }
          ]
        }),
      });
    });
    await authedPage.route('**/api/aws/airflow/dags/etl_orders_daily/run', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          dag_run_id: 'manual__2026-09-05',
          dag_id: 'etl_orders_daily',
          state: 'success',
          task_instances: [
            { task_id: 'extract_orders', state: 'success', duration: 1.2 }
          ]
        }),
      });
    });

    let jobBody: any = null;
    await authedPage.route('**/api/jobs', async (route) => {
      if (route.request().method() === 'POST') {
        jobBody = route.request().postDataJSON();
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 10, name: jobBody.name }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
      }
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-aws"]').click();
    await authedPage.locator('[data-testid="aws-service-airflow"]').click();
    await authedPage.locator('[data-testid="aws-config-select"]').selectOption('8');

    await authedPage.locator('[data-testid="aws-airflow-dag-input"]').fill('etl_orders_daily');
    await authedPage.locator('[data-testid="aws-airflow-trigger-btn"]').click();

    await expect(authedPage.locator('[data-testid="aws-airflow-result"]')).toContainText('success');
    await expect(authedPage.locator('[data-testid="aws-airflow-result"]')).toContainText('extract_orders');

    await authedPage.locator('[data-testid="aws-airflow-job-name-input"]').fill('e2e-airflow-orders');
    await authedPage.locator('[data-testid="aws-airflow-create-job-btn"]').click();

    await expect.poll(() => jobBody).not.toBeNull();
    expect(jobBody.job_type).toBe('airflow_dag_run');
    expect(jobBody.params).toMatchObject({
      config_id: 8,
      dag_id: 'etl_orders_daily',
    });
  });
});
```

- [ ] **Step 2: Run Playwright test**

Run: `npm run test:e2e tests/e2e/21-aws-airflow-tab.spec.ts`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/21-aws-airflow-tab.spec.ts
git commit -m "test(aws-airflow): add e2e Playwright coverage for Airflow sub-tab"
```
