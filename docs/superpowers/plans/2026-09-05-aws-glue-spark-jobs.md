# AWS Glue Spark / ETL Job Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement AWS Glue Spark/ETL job execution: listing Glue jobs, triggering job runs with arguments, polling status to completion, tracking as `aws_glue_job_run` in RunExecutor, and UI controls in the Glue sub-tab with Playwright test coverage.

**Architecture:** Extend `AwsGlueService` with boto3 `glue` job methods, add FastAPI routes in `api/routes/aws_glue.py`, add `aws_glue_job_run` to `job_validation.py` and `run_executor.py`, and enhance the Glue UI tab in `tab-aws.html` and `aws.js`.

**Tech Stack:** Python 3.10+, boto3, FastAPI, Alpine.js, Playwright.

## Global Constraints

- Job type name: `aws_glue_job_run`.
- Allowed terminal states: `SUCCEEDED`, `FAILED`, `STOPPED`, `TIMEOUT`, `ERROR`.
- Route errors must map to HTTP 400 with `{ "error_type": ..., "message": ... }`.
- `RunExecutor` must return standard `ReconciliationResult` with `MismatchRecord` on status mismatch/errors without raising unhandled exceptions.

---

### Task 1: Glue Job Primitives in `AwsGlueService`

**Files:**
- Modify: `api/services/aws_glue_service.py`
- Modify: `tests/unit/test_aws_glue_service.py`

**Interfaces:**
- Produces: `list_jobs`, `get_job`, `start_job_run`, `get_job_run_status`, `run_job_to_completion`.

- [ ] **Step 1: Write failing unit tests**

```python
# In tests/unit/test_aws_glue_service.py
def test_list_jobs():
    fake_client = MagicMock()
    fake_client.get_jobs.return_value = {
        "Jobs": [
            {"Name": "spark_etl", "Description": "daily etl", "Role": "arn:aws:iam::123:role/GlueRole", "Command": {"ScriptLocation": "s3://b/script.py"}}
        ]
    }
    runtime = MagicMock()
    runtime.client.return_value = fake_client
    service = AwsGlueService(runtime=runtime)
    jobs = service.list_jobs(config_id=1)
    assert len(jobs) == 1
    assert jobs[0]["name"] == "spark_etl"

def test_run_job_to_completion_success():
    fake_client = MagicMock()
    fake_client.start_job_run.return_value = {"JobRunId": "jr_1"}
    fake_client.get_job_run.side_effect = [
        {"JobRun": {"JobRunState": "RUNNING", "ExecutionTime": 10}},
        {"JobRun": {"JobRunState": "SUCCEEDED", "ExecutionTime": 25}},
    ]
    runtime = MagicMock()
    runtime.client.return_value = fake_client
    service = AwsGlueService(runtime=runtime)
    result = service.run_job_to_completion(config_id=1, job_name="spark_etl", poll_interval_seconds=0.01)
    assert result["job_run_id"] == "jr_1"
    assert result["job_run_state"] == "SUCCEEDED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_aws_glue_service.py -v`
Expected: FAIL (missing methods)

- [ ] **Step 3: Implement methods in `AwsGlueService`**

```python
# In api/services/aws_glue_service.py
GLUE_JOB_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT", "ERROR"}

# Add to AwsGlueService:
    def list_jobs(self, config_id: int | str) -> list[dict[str, Any]]:
        client = self.runtime.client(config_id)
        resp = client.get_jobs()
        return [
            {
                "name": j.get("Name"),
                "description": j.get("Description"),
                "role": j.get("Role"),
                "script_location": (j.get("Command") or {}).get("ScriptLocation"),
                "worker_type": j.get("WorkerType"),
            }
            for j in resp.get("Jobs", [])
        ]

    def get_job(self, config_id: int | str, job_name: str) -> dict[str, Any]:
        client = self.runtime.client(config_id)
        resp = client.get_job(JobName=job_name)
        j = resp.get("Job", {})
        return {
            "name": j.get("Name"),
            "description": j.get("Description"),
            "role": j.get("Role"),
            "script_location": (j.get("Command") or {}).get("ScriptLocation"),
            "worker_type": j.get("WorkerType"),
            "max_capacity": j.get("MaxCapacity"),
        }

    def start_job_run(self, config_id: int | str, job_name: str, arguments: dict[str, str] | None = None) -> dict[str, Any]:
        client = self.runtime.client(config_id)
        resp = client.start_job_run(JobName=job_name, Arguments=arguments or {})
        return {"job_run_id": resp.get("JobRunId"), "job_name": job_name}

    def get_job_run_status(self, config_id: int | str, job_name: str, job_run_id: str) -> dict[str, Any]:
        client = self.runtime.client(config_id)
        resp = client.get_job_run(JobName=job_name, RunId=job_run_id)
        jr = resp.get("JobRun", {})
        return {
            "job_run_id": job_run_id,
            "job_name": job_name,
            "job_run_state": jr.get("JobRunState", "UNKNOWN"),
            "execution_time": jr.get("ExecutionTime"),
            "error_message": jr.get("ErrorMessage"),
        }

    def run_job_to_completion(
        self,
        config_id: int | str,
        job_name: str,
        arguments: dict[str, str] | None = None,
        poll_interval_seconds: float = 2.0,
        max_attempts: int = 120,
    ) -> dict[str, Any]:
        start = self.start_job_run(config_id=config_id, job_name=job_name, arguments=arguments)
        run_id = start["job_run_id"]
        for _ in range(max_attempts):
            time.sleep(poll_interval_seconds)
            status = self.get_job_run_status(config_id=config_id, job_name=job_name, job_run_id=run_id)
            if status["job_run_state"] in GLUE_JOB_TERMINAL_STATES:
                return status
        raise TimeoutError(f"Glue job '{job_name}' run '{run_id}' timed out after {max_attempts * poll_interval_seconds}s")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_aws_glue_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/aws_glue_service.py tests/unit/test_aws_glue_service.py
git commit -m "feat(aws-glue): add job start, status, and completion primitives to AwsGlueService"
```

---

### Task 2: API Routes for Glue Jobs

**Files:**
- Modify: `api/routes/aws_glue.py`
- Modify: `tests/unit/test_aws_glue_routes.py`

**Interfaces:**
- Produces: `/api/aws/glue/jobs`, `/api/aws/glue/jobs/{job_name}/start`, `/api/aws/glue/jobs/{job_name}/runs/{job_run_id}`, `/api/aws/glue/jobs/{job_name}/run`.

- [ ] **Step 1: Write failing route tests**

```python
# In tests/unit/test_aws_glue_routes.py
def test_list_glue_jobs_route(client, mock_glue_service):
    mock_glue_service.list_jobs.return_value = [{"name": "spark_job"}]
    resp = client.get("/api/aws/glue/jobs?config_id=1")
    assert resp.status_code == 200
    assert resp.json()["jobs"][0]["name"] == "spark_job"

def test_run_glue_job_route(client, mock_glue_service):
    mock_glue_service.run_job_to_completion.return_value = {"job_run_id": "jr_1", "job_run_state": "SUCCEEDED"}
    resp = client.post("/api/aws/glue/jobs/spark_job/run", json={"config_id": 1, "arguments": {"--env": "prod"}})
    assert resp.status_code == 200
    assert resp.json()["job_run_state"] == "SUCCEEDED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_aws_glue_routes.py -v`
Expected: FAIL (404 on /jobs endpoints)

- [ ] **Step 3: Implement routes in `api/routes/aws_glue.py`**

```python
# Add Request Models and Endpoints in api/routes/aws_glue.py
class StartGlueJobRequest(BaseModel):
    config_id: int | str
    arguments: dict[str, str] | None = None

class RunGlueJobRequest(BaseModel):
    config_id: int | str
    arguments: dict[str, str] | None = None
    poll_interval_seconds: float = 2.0
    max_attempts: int = 120

@router.get("/jobs")
def list_glue_jobs(config_id: int | str = Query(...), service: AwsGlueService = Depends(get_aws_glue_service), db: Session = Depends(get_db)):
    try:
        jobs = service.list_jobs(config_id)
        audit = AuditLog(actor="system", action="aws_glue.check", target_type="aws_glue_jobs", target_id=str(config_id), details={"count": len(jobs)})
        db.add(audit)
        db.commit()
        return {"jobs": jobs}
    except Exception as e:
        _handle(e)

@router.post("/jobs/{job_name}/start")
def start_glue_job(job_name: str, req: StartGlueJobRequest, service: AwsGlueService = Depends(get_aws_glue_service), db: Session = Depends(get_db)):
    try:
        res = service.start_job_run(req.config_id, job_name, req.arguments)
        return res
    except Exception as e:
        _handle(e)

@router.get("/jobs/{job_name}/runs/{job_run_id}")
def get_glue_job_run_status(job_name: str, job_run_id: str, config_id: int | str = Query(...), service: AwsGlueService = Depends(get_aws_glue_service)):
    try:
        return service.get_job_run_status(config_id, job_name, job_run_id)
    except Exception as e:
        _handle(e)

@router.post("/jobs/{job_name}/run")
def run_glue_job_sync(job_name: str, req: RunGlueJobRequest, service: AwsGlueService = Depends(get_aws_glue_service), db: Session = Depends(get_db)):
    try:
        return service.run_job_to_completion(req.config_id, job_name, req.arguments, req.poll_interval_seconds, req.max_attempts)
    except Exception as e:
        _handle(e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_aws_glue_routes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/routes/aws_glue.py tests/unit/test_aws_glue_routes.py
git commit -m "feat(aws-glue): add Glue ETL job REST API endpoints"
```

---

### Task 3: Job Validation & RunExecutor Execution

**Files:**
- Modify: `api/schemas.py`
- Modify: `etl_framework/runner/job_validation.py`
- Modify: `api/services/run_executor.py`
- Modify: `tests/unit/test_job_validation.py`
- Create: `tests/unit/test_run_executor_glue_jobs.py`

**Interfaces:**
- Produces: `_validate_aws_glue_job_run`, `_execute_aws_glue_job_run`.

- [ ] **Step 1: Write failing tests**

```python
# Create tests/unit/test_run_executor_glue_jobs.py
def test_execute_aws_glue_job_run_passes(db_session, monkeypatch):
    mock_service = MagicMock()
    mock_service.run_job_to_completion.return_value = {
        "job_name": "spark_etl", "job_run_id": "jr_1", "job_run_state": "SUCCEEDED", "execution_time": 45
    }
    monkeypatch.setattr("api.services.run_executor.AwsGlueService", lambda repo: mock_service)
    executor = RunExecutor(db=db_session, run_id="run-1", source_env="qa", target_env="prod", job_sequence=[], run_settings=RunSettings())
    job = JobDefinition(name="glue_job", job_type="aws_glue_job_run", params={"config_id": 1, "job_name": "spark_etl", "expected_status": "SUCCEEDED"})
    result = executor._execute_aws_glue_job_run(job)
    assert result.status == TestStatus.PASSED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_run_executor_glue_jobs.py -v`
Expected: FAIL

- [ ] **Step 3: Implement validation and execution logic**

1. In `api/schemas.py`: Add `"aws_glue_job_run"` to `job_type` Literal, add Pydantic validator requiring `config_id`/`config` and `job_name`.
2. In `etl_framework/runner/job_validation.py`: Add `_validate_aws_glue_job_run(params, issues)` and dispatch.
3. In `api/services/run_executor.py`: Add `_build_case_aws_glue_job_run` and `_execute_aws_glue_job_run(job)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_run_executor_glue_jobs.py tests/unit/test_job_validation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py etl_framework/runner/job_validation.py api/services/run_executor.py tests/unit/test_run_executor_glue_jobs.py tests/unit/test_job_validation.py
git commit -m "feat(aws-glue): add aws_glue_job_run validation and execution support"
```

---

### Task 4: Frontend UI for Glue Jobs

**Files:**
- Modify: `frontend/partials/tab-aws.html`
- Modify: `frontend/features/aws.js`
- Modify: `frontend/index.html` (rebuilt via `node scripts/build-html.js`)
- Modify: `tests/integration/test_aws_ui_smoke.py`

**Interfaces:**
- Produces: Enhanced Glue sub-tab with Glue job select, arguments JSON, run button, and create tracked job button.

- [ ] **Step 1: Write failing smoke tests in `tests/integration/test_aws_ui_smoke.py`**

```python
def test_aws_tab_contains_glue_job_controls():
    content = Path("frontend/index.html").read_text(encoding="utf-8")
    assert 'data-testid="aws-glue-job-run-btn"' in content
    assert 'data-testid="aws-glue-create-job-run-btn"' in content
```

- [ ] **Step 2: Run smoke test to verify it fails**

Run: `pytest tests/integration/test_aws_ui_smoke.py -v`
Expected: FAIL

- [ ] **Step 3: Update `tab-aws.html` & `aws.js` and rebuild HTML**

1. Add Glue Jobs section in `tab-aws.html`:
   - Job selection / entry (`data-testid="aws-glue-job-input"`, `data-testid="aws-glue-load-jobs-btn"`, `data-testid="aws-glue-job-select"`)
   - Arguments JSON textarea (`data-testid="aws-glue-job-args-input"`)
   - Expected status dropdown (`data-testid="aws-glue-job-expected-status-select"`)
   - Action buttons: Run Glue Job (`data-testid="aws-glue-job-run-btn"`), Create Glue Job Run (`data-testid="aws-glue-create-job-run-btn"`)
   - Results viewer (`data-testid="aws-glue-job-run-result"`)
2. Add corresponding state and actions in `frontend/features/aws.js`.
3. Rebuild with `node scripts/build-html.js`.

- [ ] **Step 4: Run smoke test to verify it passes**

Run: `pytest tests/integration/test_aws_ui_smoke.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/partials/tab-aws.html frontend/features/aws.js frontend/index.html tests/integration/test_aws_ui_smoke.py
git commit -m "feat(aws-ui): add Glue ETL job execution controls to AWS tab"
```

---

### Task 5: End-to-End Playwright Glue Job Test

**Files:**
- Modify: `tests/e2e/19-aws-glue-tab.spec.ts`

- [ ] **Step 1: Write E2E Playwright test**

```typescript
// In tests/e2e/19-aws-glue-tab.spec.ts
test('loads Glue jobs, runs job to completion, and creates tracked job', async ({ authedPage }) => {
  await authedPage.route('**/api/aws/glue/jobs?**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ jobs: [{ name: 'spark_orders_etl', description: 'daily etl' }] }),
    });
  });

  await authedPage.route('**/api/aws/glue/jobs/spark_orders_etl/run', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_run_id: 'jr_123', job_name: 'spark_orders_etl', job_run_state: 'SUCCEEDED', execution_time: 30 }),
    });
  });

  let createdJob: any = null;
  await authedPage.route('**/api/jobs', async (route) => {
    if (route.request().method() === 'POST') {
      createdJob = route.request().postDataJSON();
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 99, name: createdJob.name }) });
    } else {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    }
  });

  await authedPage.goto('/');
  await authedPage.locator('[data-testid="nav-tab-aws"]').click();
  await authedPage.locator('[data-testid="aws-service-glue"]').click();
  await authedPage.locator('[data-testid="aws-config-select"]').selectOption('7');

  await authedPage.locator('[data-testid="aws-glue-load-jobs-btn"]').click();
  await authedPage.locator('[data-testid="aws-glue-job-select"]').selectOption('spark_orders_etl');
  await authedPage.locator('[data-testid="aws-glue-job-run-btn"]').click();

  await expect(authedPage.locator('[data-testid="aws-glue-job-run-result"]')).toContainText('SUCCEEDED');
  await expect(authedPage.locator('[data-testid="aws-glue-job-run-result"]')).toContainText('jr_123');

  await authedPage.locator('[data-testid="aws-glue-job-run-name-input"]').fill('e2e-glue-spark-orders');
  await authedPage.locator('[data-testid="aws-glue-create-job-run-btn"]').click();

  await expect.poll(() => createdJob).not.toBeNull();
  expect(createdJob.job_type).toBe('aws_glue_job_run');
  expect(createdJob.params).toMatchObject({
    config_id: 7,
    job_name: 'spark_orders_etl',
  });
});
```

- [ ] **Step 2: Run Playwright test**

Run: `npm run test:e2e tests/e2e/19-aws-glue-tab.spec.ts`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/19-aws-glue-tab.spec.ts
git commit -m "test(aws-glue): add e2e Playwright test for Glue ETL job execution"
```
