# Job Design Scheduling Automation Help Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the in-app Help Center and README with a complete end-to-end guide for designing, modeling, saving, executing, scheduling, API-triggering, pytest-integrating, and CI/CD-gating ETL jobs.

**Architecture:** This is a documentation-only change. Add a focused Help Center section using the existing `window.ETL_HELP.sections[]` data model, then add a durable README workflow guide that links from the contents and uses existing API/CLI surfaces.

**Tech Stack:** JavaScript Help Center content, Markdown README, Playwright e2e help test, Node.js syntax checks.

## Global Constraints

- Keep this as a documentation/help update only; no runtime behavior changes.
- Use the existing Help tab renderer fields: `title`, `text`, `where`, `tip`, and `warn`.
- Do not modify `frontend/partials/tab-help.html` unless the existing renderer cannot display the new content.
- Use existing documented endpoints and commands: `POST /api/jobs`, `PUT /api/jobs/{name}`, `POST /api/jobs/import`, `POST /api/runs`, `GET /api/runs/{run_id}/status`, `POST /api/schedules`, `POST /api/schedules/{schedule_id}/run-now`, `POST /api/gates/{job}/evaluate`, `POST /api/runs/test-suite`, and `python -m etl_framework.runner.cli --gate-run <run_id>`.
- Use placeholders such as `<token>`, `<job-name>`, `<run_id>`, and `<schedule_id>` in README examples.
- Do not commit unless the user explicitly requests it.

---

## File Structure

- Modify `frontend/help-content.js`: add a new searchable Help Center section called `Job Design, Scheduling & Automation` after the existing `Jobs & Launch` section.
- Modify `tests/e2e/11-help.spec.ts`: add an e2e assertion that the new help section is visible and searchable.
- Modify `README.md`: add a contents entry and a new guide section near the existing Launch/job sections.
- No changes to `frontend/partials/tab-help.html`: existing rendering is sufficient.

---

### Task 1: Add In-App Help Workflow Section

**Files:**
- Modify: `frontend/help-content.js:85-153`
- Test: `tests/e2e/11-help.spec.ts`

**Interfaces:**
- Consumes: `window.ETL_HELP.sections[]` entries shaped as `{ id, title, intro, steps }`.
- Produces: a section with `id: 'job-automation'`, `title: 'Job Design, Scheduling & Automation'`, and short workflow steps.

- [ ] **Step 1: Add failing e2e coverage for the new section**

Modify `tests/e2e/11-help.spec.ts` by adding this test inside the existing `test.describe('11 help', () => { ... })` block after the negative search test:

```ts
  test('job automation guide is visible and searchable', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-help"]').click();

    await expect(authedPage.locator('text=Job Design, Scheduling & Automation')).toBeVisible();

    await authedPage.locator('[data-testid="help-search-input"]').fill('pytest CI/CD');
    await expect(authedPage.locator('text=Run from external pytest')).toBeVisible();
    await expect(authedPage.locator('text=Gate CI/CD pipelines')).toBeVisible();
  });
```

- [ ] **Step 2: Run the focused help test and verify it fails**

Run:

```powershell
npx playwright test tests/e2e/11-help.spec.ts -g "job automation guide is visible and searchable"
```

Expected: FAIL because `Job Design, Scheduling & Automation`, `Run from external pytest`, and `Gate CI/CD pipelines` are not in the Help Center yet.

- [ ] **Step 3: Add the Help Center section**

In `frontend/help-content.js`, insert this new section immediately after the existing `launch` section object that ends with `id: 'launch'` and before the `monitor` section:

```js
    {
      id: 'job-automation',
      title: 'Job Design, Scheduling & Automation',
      intro: 'Follow the complete lifecycle for reusable jobs: model the test, save it, run it now, schedule it, trigger it through the API, and gate pytest or CI/CD automation on the result.',
      steps: [
        {
          title: 'Design the job model',
          text: 'Start from the business check you need: choose the job type, source and target systems, query or file/API/artifact inputs, key columns, excluded columns, DQ rules, dependencies, and pass conditions. Keep jobs idempotent so UI, schedules, pytest, and CI/CD can run the same definition safely.',
          where: 'Launch -> Job Catalog -> + New Job',
        },
        {
          title: 'Save a reusable job',
          text: 'Click Save in the job editor. The saved job becomes the canonical definition used by the Launch tab, POST /api/runs, schedules, external pytest tests, CI/CD stages, reports, lineage, and gates.',
          where: 'Job editor -> Save',
          tip: 'Prefer saved jobs over ad hoc automation payloads so every execution path runs the same reviewed configuration.',
        },
        {
          title: 'Execute from the UI',
          text: 'Select one or more saved jobs, order them, set Run Settings, then click Run Tests. Monitor streams queued/running/passed/failed/skipped states and History stores the durable run record and report links.',
          where: 'Launch -> Job Catalog -> Run Tests',
        },
        {
          title: 'Schedule from the UI',
          text: 'Open the Schedules sub-tab, choose the saved jobs or sequence, set source/target labels, config, run settings, cron expression, and Enabled. Save the schedule, then use Run Now to execute it immediately outside its normal cron time.',
          where: 'Launch -> Schedules sub-tab',
        },
        {
          title: 'Execute through the API',
          text: 'Use Bearer auth, create or update jobs with POST /api/jobs or PUT /api/jobs/{name}, start runs with POST /api/runs, poll GET /api/runs/{run_id}/status, and evaluate POST /api/gates/{job}/evaluate after completion.',
          where: 'API -> /api/jobs, /api/runs, /api/gates/{job}/evaluate',
        },
        {
          title: 'Schedule through the API',
          text: 'Create recurring execution with POST /api/schedules using name, cron_expr, source_env, target_env, config_id, job_sequence, run_settings, and enabled. Call POST /api/schedules/{schedule_id}/run-now to execute the saved schedule immediately.',
          where: 'API -> /api/schedules',
          tip: 'Use GET /api/schedules/stats to confirm scheduler health and recent schedule outcomes.',
        },
        {
          title: 'Run from external pytest',
          text: 'In an external pytest suite, call the running FastAPI service with a scoped token, trigger a saved job or POST /api/runs/test-suite, wait for a terminal status, then assert PASSED or call the job gate endpoint for the promotion verdict.',
          where: 'pytest -> requests/httpx client fixture',
        },
        {
          title: 'Gate CI/CD pipelines',
          text: 'In CI/CD, keep tokens in secret variables, trigger or reference a run, wait for completion, then fail the pipeline with python -m etl_framework.runner.cli --gate-run <run_id> or an API gate verdict. Publish reports, logs, and scheduler stats as artifacts.',
          where: 'Pipeline stage -> API or CLI gate',
          warn: 'Do not hard-code tokens, DB passwords, or BO/Automic credentials in job definitions or pipeline YAML.',
        },
      ],
    },
```

- [ ] **Step 4: Parse-check the edited JavaScript**

Run:

```powershell
node --check frontend/help-content.js
```

Expected: PASS with no output.

- [ ] **Step 5: Run the focused help e2e test and verify it passes**

Run:

```powershell
npx playwright test tests/e2e/11-help.spec.ts -g "job automation guide is visible and searchable"
```

Expected: PASS. If Playwright dependencies or browser setup are unavailable, record the exact failure and keep the Node parse-check as the minimum validation evidence.

---

### Task 2: Add README End-To-End Workflow Guide

**Files:**
- Modify: `README.md:25-58`
- Modify: `README.md:901-955`

**Interfaces:**
- Consumes: existing README contents structure and existing API/CLI endpoints.
- Produces: a new table-of-contents entry `Job Design, Scheduling, And Automation` and a guide section anchored as `#job-design-scheduling-and-automation`.

- [ ] **Step 1: Add a README anchor check to the help e2e test**

Modify `tests/e2e/11-help.spec.ts` by adding this test after the job automation guide test:

```ts
  test('README documents job automation workflow', async ({ page }) => {
    const response = await page.request.get('/README.md');
    expect(response.status()).toBe(200);
    const readme = await response.text();

    expect(readme).toContain('### Job Design, Scheduling, And Automation');
    expect(readme).toContain('POST /api/jobs');
    expect(readme).toContain('POST /api/schedules');
    expect(readme).toContain('POST /api/runs/test-suite');
    expect(readme).toContain('python -m etl_framework.runner.cli --gate-run <run_id>');
  });
```

- [ ] **Step 2: Run the README-focused test and verify it fails**

Run:

```powershell
npx playwright test tests/e2e/11-help.spec.ts -g "README documents job automation workflow"
```

Expected: FAIL because the new README section is not present yet. If `/README.md` is not served by the local app, replace this e2e assertion with a Node filesystem check in Step 5.

- [ ] **Step 3: Add the contents entry**

In `README.md`, under the `Using The Web UI` entries, add this bullet after `Creating And Managing Jobs`:

```markdown
  - [Job Design, Scheduling, And Automation](#job-design-scheduling-and-automation)
```

The resulting contents block should include:

```markdown
- [Using The Web UI](#using-the-web-ui)
  - [Job Launcher — Step-By-Step](#job-launcher--step-by-step)
  - [Creating And Managing Jobs](#creating-and-managing-jobs)
  - [Job Design, Scheduling, And Automation](#job-design-scheduling-and-automation)
  - [Job Types Reference](#job-types-reference)
  - [Run Settings Reference](#run-settings-reference)
```

- [ ] **Step 4: Add the README workflow section**

Insert this section after the existing `#### Edit or delete a job` section and before `#### Bulk import jobs (API)`:

````markdown
### Job Design, Scheduling, And Automation

Use the same saved job definition everywhere: the UI, REST API, schedules, external pytest suites, and CI/CD pipelines. This keeps the modeled test reviewed once and executed consistently from every entry point.

#### Lifecycle model

| Object | Purpose | Created from | Executed by |
|---|---|---|---|
| Job | Reusable test definition: job type, query or input source, keys, rules, dependencies, and pass condition | UI Job Catalog or `POST /api/jobs` | UI Launch, `POST /api/runs`, schedules, pytest, CI/CD |
| Run | One execution record with status, results, mismatches, reports, logs, and metrics | UI Run Tests, `POST /api/runs`, `POST /api/runs/test-suite`, schedule trigger | Monitor, History, reports, gates |
| Schedule | Recurring trigger that stores environment labels, config, job sequence, run settings, cron, and enabled state | UI Schedules sub-tab or `POST /api/schedules` | APScheduler cron or `POST /api/schedules/{schedule_id}/run-now` |
| Gate | Machine-readable pass/fail decision for automation | Latest job result or run id | `POST /api/gates/{job}/evaluate` or `python -m etl_framework.runner.cli --gate-run <run_id>` |

#### UI workflow

1. Open **Launch -> Job Catalog -> + New Job**.
2. Pick the job type and model the required inputs:
   - `reconciliation`: query or file inputs plus `key_columns`.
   - `api_reconciliation`: saved REST endpoints plus `key_columns`.
   - `bo_report`: SAP BO document/report parameters.
   - `automic_job`: Automic job name or run id.
   - `dbt_artifact`, `freshness`, `profile`, `schema_snapshot`, or `cross_job_assertion`: fill the type-specific `params`.
3. Add optional DQ rules, dependencies, excluded columns, pass conditions, and tags.
4. Click **Save** so UI runs, API runs, schedules, pytest, and CI/CD all reuse the same definition.
5. Select the saved jobs, order them, tune **Run Settings**, and click **Run Tests**.
6. Watch **Monitor** for live progress, then review **History**, **Reports**, and **Logs**.
7. Open **Launch -> Schedules**, create a cron schedule, enable it, and click **Run Now** to execute the saved schedule immediately.

#### API workflow

Authenticate every `/api/*` request with a Bearer token created in **Config -> Security**.

```powershell
$base = "http://127.0.0.1:8000"
$h = @{ Authorization = "Bearer etl_<token>" }
```

Create or update a saved job:

```powershell
$job = @{
  name = "orders_recon"
  description = "Compare source and target orders"
  tags = @("daily", "orders")
  job_type = "reconciliation"
  query = "SELECT order_id, amount, status FROM orders"
  key_columns = @("order_id")
  exclude_columns = @("updated_at")
  rules = @(
    @{ type = "not_null"; column = "order_id"; severity = "error" },
    @{ type = "row_count_min"; min_value = 1; severity = "error" }
  )
  pass_condition = @{ require_status = @("PASSED"); max_value_mismatches = 0 }
  enabled = $true
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post -Uri "$base/api/jobs" -Headers $h -ContentType "application/json" -Body $job
# For an existing job, use:
# Invoke-RestMethod -Method Put -Uri "$base/api/jobs/orders_recon" -Headers $h -ContentType "application/json" -Body $job
```

Trigger a run and poll for completion:

```powershell
$runBody = @{
  source_env = "dev"
  target_env = "prod"
  job_sequence = @("orders_recon")
  run_settings = @{
    use_live_connections = $true
    execution_mode = "sequential"
    max_workers = 2
    metrics_enabled = $true
  }
} | ConvertTo-Json -Depth 8

$run = Invoke-RestMethod -Method Post -Uri "$base/api/runs" -Headers $h -ContentType "application/json" -Body $runBody
$runId = $run.run_id

do {
  Start-Sleep -Seconds 5
  $status = Invoke-RestMethod -Method Get -Uri "$base/api/runs/$runId/status" -Headers $h
  "$($status.run_id) $($status.status)"
} while ($status.status -in @("PENDING", "RUNNING"))

if ($status.status -ne "PASSED") { throw "ETL run $runId finished with $($status.status)" }
```

Create a schedule, then execute it immediately:

```powershell
$scheduleBody = @{
  name = "weekday-orders-recon"
  cron_expr = "0 6 * * 1-5"
  source_env = "dev"
  target_env = "prod"
  job_sequence = @("orders_recon")
  run_settings = @{ use_live_connections = $true; execution_mode = "sequential" }
  enabled = $true
} | ConvertTo-Json -Depth 8

$schedule = Invoke-RestMethod -Method Post -Uri "$base/api/schedules" -Headers $h -ContentType "application/json" -Body $scheduleBody
Invoke-RestMethod -Method Post -Uri "$base/api/schedules/$($schedule.id)/run-now" -Headers $h
Invoke-RestMethod -Method Get -Uri "$base/api/schedules/stats?days=30" -Headers $h
```

Evaluate a job gate after a run has completed:

```powershell
$gate = Invoke-RestMethod -Method Post -Uri "$base/api/gates/orders_recon/evaluate" -Headers $h
if ($gate.verdict -ne "PROMOTE") { throw "Gate held orders_recon: $($gate.reason)" }
```

#### External pytest integration

External pytest suites can treat the ETL service as a black-box quality gate. Keep the service URL and token in environment variables and trigger saved jobs from fixtures or tests.

```python
import os
import time

import requests

BASE_URL = os.environ.get("ETL_BASE_URL", "http://127.0.0.1:8000")
TOKEN = os.environ["ETL_API_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TERMINAL = {"PASSED", "FAILED", "ERROR", "CANCELLED"}


def wait_for_run(run_id: str, timeout_seconds: int = 600) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = requests.get(f"{BASE_URL}/api/runs/{run_id}/status", headers=HEADERS, timeout=10)
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in TERMINAL:
            return payload
        time.sleep(5)
    raise AssertionError(f"run {run_id} did not finish within {timeout_seconds} seconds")


def test_orders_reconciliation_promotes():
    response = requests.post(
        f"{BASE_URL}/api/runs",
        headers=HEADERS,
        json={
            "source_env": "dev",
            "target_env": "prod",
            "job_sequence": ["orders_recon"],
            "run_settings": {"use_live_connections": True, "execution_mode": "sequential"},
        },
        timeout=10,
    )
    response.raise_for_status()
    status = wait_for_run(response.json()["run_id"])
    assert status["status"] == "PASSED"

    gate = requests.post(f"{BASE_URL}/api/gates/orders_recon/evaluate", headers=HEADERS, timeout=10)
    gate.raise_for_status()
    assert gate.json()["verdict"] == "PROMOTE"
```

To execute the repository's own pytest suite as a tracked framework run, call the suite-runner endpoint and poll the returned run id:

```python
def test_framework_pytest_suite_passes():
    response = requests.post(f"{BASE_URL}/api/runs/test-suite", headers=HEADERS, timeout=10)
    response.raise_for_status()
    status = wait_for_run(response.json()["run_id"], timeout_seconds=900)
    assert status["status"] == "PASSED"
```

#### CI/CD pipeline integration

Use secret variables for `ETL_BASE_URL` and `ETL_API_TOKEN`. A pipeline stage can trigger a run through the API, wait for completion, and then use the CLI gate exit code to fail or pass the pipeline.

```yaml
etl_quality_gate:
  stage: test
  image: python:3.11
  script:
    - pip install -e ".[dev]"
    - python - <<'PY'
      import os, time, requests
      base = os.environ["ETL_BASE_URL"].rstrip("/")
      headers = {"Authorization": f"Bearer {os.environ['ETL_API_TOKEN']}"}
      run = requests.post(
          f"{base}/api/runs",
          headers=headers,
          json={"source_env": "dev", "target_env": "prod", "job_sequence": ["orders_recon"]},
          timeout=10,
      )
      run.raise_for_status()
      run_id = run.json()["run_id"]
      print(f"RUN_ID={run_id}")
      with open("run_id.txt", "w", encoding="utf-8") as fh:
          fh.write(run_id)
      terminal = {"PASSED", "FAILED", "ERROR", "CANCELLED"}
      while True:
          status = requests.get(f"{base}/api/runs/{run_id}/status", headers=headers, timeout=10).json()
          print(status["status"])
          if status["status"] in terminal:
              break
          time.sleep(5)
      PY
    - python -m etl_framework.runner.cli --gate-run $(cat run_id.txt)
    - python -m etl_framework.runner.cli --scheduler-stats --fail-on-stopped --min-success-rate 95 --output json > scheduler-stats.json
  artifacts:
    when: always
    paths:
      - run_id.txt
      - scheduler-stats.json
```

For scheduled production checks, let APScheduler trigger the recurring job and have CI collect health instead of starting a duplicate run:

```powershell
python -m etl_framework.runner.cli --scheduler-stats --days 30 --fail-on-stopped --min-success-rate 95 --output text
```

Automation notes:

- Store tokens, DB passwords, SAP BO credentials, and Automic credentials in the app config or CI secret store, not in README snippets or committed pipeline files.
- Use saved jobs for automated runs so UI, pytest, and CI/CD share the same reviewed model.
- Make scheduled jobs safe to retry; avoid destructive SQL and non-idempotent external side effects.
- Publish `reports/report_<run_id>.html`, run logs, and scheduler stats as CI artifacts when your pipeline environment has access to the app filesystem or report download endpoint.
````

- [ ] **Step 5: Verify README content with a Node filesystem check**

Run:

```powershell
node -e "const fs=require('fs'); const s=fs.readFileSync('README.md','utf8'); for (const needle of ['### Job Design, Scheduling, And Automation','POST /api/jobs','POST /api/schedules','POST /api/runs/test-suite','python -m etl_framework.runner.cli --gate-run <run_id>']) { if (!s.includes(needle)) { throw new Error('Missing '+needle); } }"
```

Expected: PASS with no output.

- [ ] **Step 6: Run the README-focused test if the app serves README.md**

Run:

```powershell
npx playwright test tests/e2e/11-help.spec.ts -g "README documents job automation workflow"
```

Expected: PASS if `/README.md` is served. If it returns 404, keep the Node filesystem check from Step 5 as the authoritative validation and remove or skip the e2e README test before finalizing.

---

### Task 3: Final Validation and Cleanup

**Files:**
- Modify: `frontend/help-content.js`
- Modify: `tests/e2e/11-help.spec.ts`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1 Help Center section and Task 2 README guide.
- Produces: verified documentation/help changes ready for review.

- [ ] **Step 1: Run all help e2e tests**

Run:

```powershell
npx playwright test tests/e2e/11-help.spec.ts
```

Expected: PASS. If the README e2e assertion from Task 2 cannot pass because `/README.md` is not served, remove that e2e test and rely on the Node filesystem check instead.

- [ ] **Step 2: Run JavaScript syntax validation**

Run:

```powershell
node --check frontend/help-content.js
```

Expected: PASS with no output.

- [ ] **Step 3: Inspect the working tree diff**

Run:

```powershell
git diff -- frontend/help-content.js tests/e2e/11-help.spec.ts README.md docs/superpowers/specs/2026-07-18-job-design-scheduling-automation-help-design.md docs/superpowers/plans/2026-07-18-job-design-scheduling-automation-help.md
```

Expected: Diff only contains the approved spec, this plan, Help Center additions, test additions, and README guide updates.

- [ ] **Step 4: Check status for unrelated changes**

Run:

```powershell
git status --short
```

Expected: Changed files include only intended documentation/help/test files, or any unrelated pre-existing user changes are left untouched and called out in the final response.

- [ ] **Step 5: Prepare final summary**

Final response must include:

```text
- Updated `frontend/help-content.js` with the new Job Design, Scheduling & Automation guide.
- Updated `README.md` with the end-to-end UI/API/pytest/CI/CD workflow guide.
- Updated `tests/e2e/11-help.spec.ts` with focused Help Center coverage, unless README e2e had to be removed because `/README.md` is not served.
- Validation commands run and whether they passed.
```

---

## Self-Review

- Spec coverage: Task 1 covers the Help Center section; Task 2 covers README guide, UI/API/pytest/CI/CD examples, and existing endpoint usage; Task 3 covers validation and no-runtime-change cleanup.
- Placeholder scan: No `TBD`, `TODO`, `implement later`, or undefined behavior remains.
- Type consistency: Help section uses existing `id`, `title`, `intro`, `steps`, `text`, `where`, `tip`, and `warn` fields; README examples use existing schemas and endpoints listed in the global constraints.
