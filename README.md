# ETL Test Framework

ETL Test Framework is a FastAPI and Alpine.js application for running ETL reconciliation tests, comparing BusinessObjects reports, monitoring execution, reviewing mismatches, accepting known differences, browsing themed reports and metrics, and enforcing data quality rules across environments.

The application can run entirely in local simulation mode for development, or it can connect to live SQL Server, SAP BusinessObjects, and Automic environments when configured.

## Contents

- [Capabilities](#capabilities)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Development Deployment](#development-deployment)
- [Production Deployment](#production-deployment)
- [Docker Or Service Deployment](#docker-or-service-deployment)
- [Database And Storage](#database-and-storage)
- [Using The Web UI](#using-the-web-ui)
- [Reports, Metrics, And Logs](#reports-metrics-and-logs)
- [Compare Tab](#compare-tab)
- [API Usage](#api-usage)
- [Testing](#testing)
- [Operations](#operations)
- [Troubleshooting](#troubleshooting)

## Capabilities

- Reconcile source and target datasets with configurable key columns, excluded columns, float tolerance, null handling, hash precheck, chunking, and schema mismatch policy.
- Run jobs in parallel or sequential execution mode with optional **retry policy** (max retries, exponential backoff).
- Define **Data Quality (DQ) rules** per job — `not_null`, `unique`, `row_count_min/max/between`, `column_mean_between`, `match_regex`, `custom_sql`. Violations are captured as typed mismatches.
- Define **job dependencies** (`depends_on`) and resolve execution order with topological sort; jobs with failed upstreams are skipped automatically.
- **Validate job queries** with a dry-run EXPLAIN check before launching a run.
- Store runs as database-backed `TestRun` records with full result history.
- Track mismatch details and accept known mismatches with a note and optional user.
- Compare two stored runs to see improved, regressed, unchanged, added, and removed tests.
- Pin any run as the **baseline** for an environment pair; compare any run against baseline in one click.
- View **mismatch value distribution** (top-N column/source/target triples) for each result.
- Detect metric **drift** with σ-based analysis across a rolling window (`/api/runs/trends`).
- Serve CI/CD **badge SVGs** per run and per job (`/api/runs/{id}/badge`, `/api/runs/latest/badge`).
- Compare SAP BusinessObjects report sources from live BO, file paths, or uploads.
- Launch dual-environment reconciliation runs and compare the paired results.
- Compare a stored reconciliation run or HTML report against a production HTML report.
- Browse generated HTML reports, metrics dashboards, and searchable logs in a dark-themed UI.
- Use SAP BO and Automic adapters from the UI and API.
- Use the REST API directly with OpenAPI documentation at `/docs`.
- Manage **API tokens** (Bearer token auth on all `/api/*` routes).
- Configure **webhook notifications** for run events (`run.failed`, `run.passed`, etc.) with optional HMAC-SHA256 signing.
- Schedule recurring runs with **APScheduler cron expressions**.
- View the **job lineage DAG** (job → job dependency graph) in the History tab.
- **Audit log** — every create, update, delete, and mismatch-accept action is recorded with actor, action, resource type, resource ID, and a JSON diff. Queryable via `GET /api/audit`.

## Architecture

```text
Browser
  Alpine.js SPA
  Tabs: Config, Launch, Monitor, History, Adapters, Reports, Compare
      |
      | HTTP / JSON / HTML
      v
FastAPI app  (BearerTokenMiddleware on all /api/* routes)
  api/routes/configs.py
  api/routes/jobs.py          — CRUD, DQ rules, depends_on, EXPLAIN validate
  api/routes/runs.py          — runs, trends, badges, baseline, mismatch-distribution
  api/routes/tokens.py        — API token CRUD
  api/routes/notifications.py — webhook CRUD + test ping
  api/routes/schedules.py     — cron schedule CRUD + run-now
  api/routes/lineage.py       — job lineage graph
  api/routes/adapters.py
  api/routes/compare.py
  api/routes/health.py
      |
      v
Core framework
  etl_framework/reconciliation   — ReconciliationEngine, DQEngine, polars/pandas backends
  etl_framework/runner
  etl_framework/reporting
  etl_framework/repository       — ORM models, repositories
  etl_framework/sap_bo
  etl_framework/automic
  api/services/run_executor.py   — retry, DAG resolution, DQ evaluation
  api/services/notifier.py       — webhook fire-and-forget
  api/services/scheduler.py      — APScheduler wrapper
      |
      v
SQLite by default, or SQLAlchemy-compatible database via ETL_DATABASE_URL
Logs and metric sidecars under ./logs
HTML reports under ./reports
```

The frontend is served by FastAPI from `frontend/`:

- `frontend/index.html`
- `frontend/app.js`
- `frontend/styles.css`

The default database is SQLite at `./etl_framework.db`. Existing SQLite databases are updated at startup — new columns and tables are added automatically.

## Requirements

Minimum:

- Python 3.11 or later
- pip
- Windows PowerShell, macOS shell, or Linux shell

Recommended for this workspace:

- Python 3.14
- Microsoft ODBC Driver 17 or 18 for SQL Server if using live SQL Server connections
- Network access to SAP BO RESTful Web Services if using live BO features
- Network access to Automic REST API if using live Automic features

Python dependencies are declared in [pyproject.toml](pyproject.toml).

## Installation

### 1. Clone And Enter The Repository

```powershell
git clone <repo-url>
cd C:\atom
```

On macOS or Linux:

```bash
git clone <repo-url>
cd atom
```

### 2. Create A Virtual Environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS or Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install The Application

Basic install:

```powershell
pip install -e .
```

Development install:

```powershell
pip install -e ".[dev]"
```

Optional extras:

```powershell
pip install -e ".[json-logging]"
pip install -e ".[tracing]"
pip install -e ".[metrics]"
```

All common extras:

```powershell
pip install -e ".[json-logging,tracing,metrics,dev]"
```

`polars`, `pyarrow`, and `beautifulsoup4` are bundled as core dependencies and are included in the base `pip install -e .`.

## Configuration

### Authentication

All `/api/*` routes (except `/api/health` and the token bootstrap endpoint) require a Bearer token.

**First-time setup**:

1. Start the server.
2. Open `http://127.0.0.1:8000` in a browser.
3. Go to the **Config** tab and scroll to **Security**.
4. Enter a token name and click **Create Token**. The raw token is shown once.
5. Paste the token into the **Set Active Token** input. It is saved in `localStorage` and sent automatically.

API clients: pass `Authorization: Bearer etl_<token>` on every request.

### Application Database

The application database is controlled by `ETL_DATABASE_URL`.

Default:

```text
sqlite:///./etl_framework.db
```

Windows PowerShell examples:

```powershell
# Use default local SQLite database
Remove-Item Env:\ETL_DATABASE_URL -ErrorAction SilentlyContinue

# Use a specific SQLite database
$env:ETL_DATABASE_URL = "sqlite:///C:/atom/etl_framework.db"
```

For non-SQLite deployments, set any SQLAlchemy-supported database URL and ensure the required driver is installed.

### Saved Environment Configs

Environment configs can be created in the Config tab or through `/api/configs`. A config stores values such as:

- `db_host`
- `db_port`
- `db_name`
- `db_user`
- `db_password`
- `db_driver`
- `db_connect_timeout`
- `bo_url`
- `bo_user`
- `bo_password`
- `bo_timeout`
- `automic_url`
- `automic_user`
- `automic_password`

Example API request:

```powershell
$headers = @{ Authorization = "Bearer etl_<your-token>" }

$body = @{
  name = "dev-sql"
  env_name = "dev"
  config_data = @{
    db_host = "sql-dev.internal"
    db_port = 1433
    db_name = "warehouse_dev"
    db_user = "etl_user"
    db_password = "secret"
    db_driver = "ODBC Driver 17 for SQL Server"
    bo_url = "http://bo-server:6405"
    bo_user = "bo_user"
    bo_password = "secret"
    automic_url = "http://automic:8080"
    automic_user = "automic_user"
    automic_password = "secret"
  }
} | ConvertTo-Json -Depth 6

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/configs" -Body $body -ContentType "application/json" -Headers $headers
```

### YAML Import

Configs can also be imported as YAML through `/api/configs/import-yaml`. The YAML should describe one or more named environments. Keep secrets out of source control.

### Run Settings

Every run can include `run_settings`:

| Setting | Default | Description |
|---|---:|---|
| `use_live_connections` | `false` | Use live SQL/adapter connections instead of simulation paths. |
| `execution_mode` | `parallel` | `parallel` or `sequential`. |
| `max_workers` | `4` | Worker count for parallel execution. |
| `max_duration_seconds` | `0` | SLO threshold. `0` disables duration thresholding. |
| `float_tolerance` | `1e-9` | Numeric comparison tolerance. |
| `schema_mismatch_policy` | `warn` | `warn` records schema diff and compares common columns; `error` fails the test. |
| `null_equals_null` | `true` | Treat two null values as equal. |
| `chunk_size` | `0` | Chunk size for chunked reconciliation. `0` disables chunking. |
| `use_hash_precheck` | `true` | Use hash shortcut before expensive value comparison where possible. |
| `comparison_backend` | `pandas` | `pandas` or `polars`. Both backends are included in the base install. |
| `mismatch_row_limit` | `1000` | Maximum mismatch rows stored per result. |
| `exclude_columns` | `[]` | Global columns to ignore. |
| `key_columns` | `[]` | Global key columns. Job-specific keys can also be stored on jobs. |
| `health_check` | `false` | Run health checks before execution. |
| `metrics_enabled` | `true` | Write metrics JSON to `logs/metrics_<run_id>.json`. |
| `notes` | `""` | Free-form run notes. |
| `max_retries` | `0` | Retry a failed job up to this many times (exponential backoff). |
| `retry_delay_seconds` | `30` | Initial delay before first retry; doubles each attempt. |
| `retry_on` | `["error"]` | Conditions that trigger a retry: `"error"` and/or `"timeout"`. |

## Development Deployment

Start the server with reload:

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

- Web UI: `http://127.0.0.1:8000`
- OpenAPI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- Health: `http://127.0.0.1:8000/api/health`

If port 8000 is already in use:

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8004 --reload
```

## Production Deployment

For production, do not use `--reload`.

Single-process production command:

```powershell
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Multiple worker command:

```powershell
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Recommended production checklist:

- Use a dedicated virtual environment.
- Pin dependency versions in a lock file or deployment manifest.
- Set `ETL_DATABASE_URL` explicitly.
- Put the app behind a reverse proxy such as IIS, nginx, or a platform load balancer.
- Terminate TLS at the proxy or platform edge.
- Restrict network access to `/docs` if the API is exposed outside a trusted network.
- Back up `etl_framework.db` if using SQLite.
- Back up `logs/` and `reports/` if those artifacts are operationally important.
- Configure log rotation for `logs/etl_framework.log`.
- Store credentials outside Git.
- Prefer a managed database instead of SQLite when multiple app instances write concurrently.
- Create at least one API token before exposing the app to users.

### Windows Service Pattern

One practical Windows deployment is:

1. Create `C:\atom\.venv`.
2. Install with `pip install -e ".[json-logging,dev]"` or your selected extras.
3. Create a service using NSSM, Windows Task Scheduler, or your enterprise process manager.
4. Service command:

```powershell
C:\atom\.venv\Scripts\python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

5. Working directory:

```text
C:\atom
```

6. Environment:

```text
ETL_DATABASE_URL=sqlite:///C:/atom/etl_framework.db
```

## Docker Or Service Deployment

There is no committed Dockerfile in this repository at the time of writing. A minimal container deployment should:

- Use Python 3.11 or later.
- Copy the repository.
- Install the package with required extras.
- Expose the selected uvicorn port.
- Mount persistent volumes for:
  - `/app/etl_framework.db` or the configured database
  - `/app/logs`
  - `/app/reports`

Example container command:

```bash
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

For Kubernetes or container platforms, run one app instance per SQLite database. If scaling horizontally, use a server database through `ETL_DATABASE_URL`.

## Database And Storage

### Database Tables

The repository layer stores:

| Table | Purpose |
|---|---|
| `saved_configs` | Named environment connection configs |
| `saved_jobs` | Job definitions including DQ rules and dependencies in `params` JSON |
| `test_runs` | Run-level records with status, env pair, baseline flag |
| `test_results` | Per-job reconciliation results |
| `mismatch_details` | Row-level mismatch records, acceptance state |
| `api_tokens` | Hashed Bearer tokens |
| `notification_hooks` | Webhook endpoints with event filters |
| `scheduled_runs` | Cron-driven job sequences |
| `job_lineage_edges` | Job-to-job dependency edges (synced from `depends_on`) |
| `audit_events` | Immutable log of all mutating API actions with actor, action, resource, and diff |

Startup calls `init_db()`, creates missing tables, and applies lightweight SQLite column additions for existing local databases.

### Files Written By The App

| Path | Purpose |
|---|---|
| `etl_framework.db` | Default SQLite app database. |
| `logs/etl_framework.log` | Main application log. |
| `logs/metrics_<run_id>.json` | Metrics sidecar for a run. |
| `reports/report_<run_id>.html` | Generated HTML report. |

## Using The Web UI

The web UI has seven tabs.

### Config

Use this tab to:

- Create saved environment configs.
- Edit connection details.
- Validate config values.
- Store SAP BO and Automic credentials for adapter workflows.
- **Import YAML** — expand the "Import YAML" card, paste a YAML block defining one or more named environments, and click Import to create all configs in one step.
- **Security** — create and manage API tokens. Created tokens are automatically stored in `localStorage`.
- **Notifications** — add webhook endpoints with event filters and optional HMAC-SHA256 secret signing.

### Launch

Use this tab to:

- Select source and target environment labels.
- Choose a saved config.
- Pick run settings including **retry policy** (max retries, retry delay).
- Select jobs from the catalog.
- **Create, edit, or delete jobs directly** — use the "+ New Job" button in the Job Catalog card.
  - Set `Depends On` to create job dependencies; the run executor will topologically sort and execute in order.
  - Add **DQ Rules** per job (not_null, unique, row_count checks, regex, mean range).
  - Use **Validate Query** (edit mode) to EXPLAIN the query against both environments before running.
- Order jobs in the execution sequence.
- Start a run.
- **Schedules sub-tab** — create, edit, enable, disable, and manually trigger cron-scheduled runs.

Default seed jobs are returned if the database has no saved jobs.

### Monitor

Use this tab to:

- Watch active runs.
- View run progress.
- See passed, failed, slow, and error counters.
- Track the current job where progress data is available.

### History

Use this tab to:

- **Runs sub-tab** — browse previous runs with status and run-type filters.
  - Open run details with per-test results.
  - Expand mismatch rows with Load More paging.
  - Accept mismatches with a note.
  - Compare two runs.
  - **Pin as Baseline ★** — mark a run as the baseline for its environment pair.
  - **Badge URL** — copy the SVG badge URL for use in CI/CD pipelines or dashboards.
  - **Value Distribution** — click "Load Value Distribution" inside an expanded mismatch panel to see the top-N column/value patterns.
  - Export run results as CSV.
  - Delete a run.
- **Trends sub-tab** — select a job and metric (`mismatch_rate`, `row_count_delta`, `duration_seconds`, `total_issues`), choose a rolling window, and view a line chart. Drift is flagged in red when the latest point is more than 2σ above the mean.
- **Lineage sub-tab** — view the job dependency DAG as an SVG diagram. Nodes are job boxes; arrows show `depends_on` relationships.

### Adapters

Use this tab for external systems.

SAP BO:

- Test a BO config.
- List documents.
- Expand document report tabs.
- Download report content as PDF, XLSX, or CSV.
- Add BO reports to the job catalog.

Automic:

- Look up a job by job name or run ID.
- Review recent lookups in browser session storage.
- Add an Automic job to the job catalog.

### Reports

Use this tab to:

- Select a run.
- View the themed HTML report.
- View an improved metrics dashboard.
- Search logs by text, level, and result limit.

### Compare

Use this tab for first-class comparison workflows.

BO Report mode, Reconciliation (dual-env) mode, and Recon File Compare mode — see the [Compare Tab](#compare-tab) section.

## Reports, Metrics, And Logs

### Report

```text
GET /api/runs/{run_id}/report
```

### Metrics

```text
GET /api/runs/{run_id}/metrics
GET /api/runs/{run_id}/metrics?format=json
```

### Logs

```text
GET /api/runs/{run_id}/logs
GET /api/runs/{run_id}/logs?format=json&q=schema_check&level=ERROR&limit=500
```

## Compare Tab

### BO Report Compare API

```text
POST /api/compare/bo-report
```

### Dual-Environment Compare API

```text
POST /api/compare/dual-env
GET /api/compare/pairs
GET /api/compare/pairs/{pair_id}
```

### Reconciliation File Compare API

```text
POST /api/compare/recon-file
```

## API Usage

All API calls require `Authorization: Bearer <token>` except `/api/health`.

### Health

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

### Create A Token (Bootstrap)

```powershell
$body = @{ name = "my-token" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/tokens" -Body $body -ContentType "application/json"
# Returns { raw_token: "etl_..." } — store this value, it is shown once.
```

### List Jobs

```powershell
$h = @{ Authorization = "Bearer etl_<token>" }
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/jobs
```

### Create A Job With DQ Rules

```powershell
$job = @{
  name = "payments_reconciliation"
  description = "Reconcile payments"
  tags = @("payments", "daily")
  job_type = "reconciliation"
  query = "SELECT * FROM payments"
  key_columns = @("id")
  exclude_columns = @("last_updated")
  enabled = $true
  depends_on = @()
  rules = @(
    @{ type = "not_null"; column = "amount"; severity = "error" }
    @{ type = "row_count_min"; min_value = 100; severity = "warn" }
  )
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/jobs" -Body $job -ContentType "application/json" -Headers $h
```

### Trigger A Run

```powershell
$run = @{
  source_env = "dev"
  target_env = "prod"
  job_sequence = @("orders_reconciliation", "customers_reconciliation")
  config_data = @{}
  run_settings = @{
    execution_mode = "parallel"
    max_workers = 4
    comparison_backend = "pandas"
    metrics_enabled = $true
    max_retries = 2
    retry_delay_seconds = 10
  }
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/runs" -Body $run -ContentType "application/json" -Headers $h
```

### Check Run Status

```powershell
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/runs/<run_id>/status
```

### Trends Endpoint

```powershell
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/runs/trends?job_name=orders_reconciliation&metric=mismatch_rate&window=30"
```

Response includes `drift_detected: true` when the last point is more than 2σ above the 30-day mean.

### Badge SVG

```text
GET /api/runs/{run_id}/badge
GET /api/runs/latest/badge?job_name=orders_reconciliation
```

Returns an inline SVG shield. Colors: green=PASSED, red=FAILED/ERROR, yellow=SLOW, grey=RUNNING/PENDING.

### Pin Baseline

```powershell
Invoke-RestMethod -Method Post -Headers $h "http://127.0.0.1:8000/api/runs/<run_id>/set-baseline"
```

### Compare Run Vs Baseline

```powershell
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/runs/<run_id>/vs-baseline"
```

### Mismatch Distribution

```powershell
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/runs/<run_id>/results/<result_id>/mismatch-distribution"
```

### Job Lineage

```powershell
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/lineage/jobs
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/lineage/jobs/orders_reconciliation/upstream"
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/lineage/jobs/orders_reconciliation/downstream"
```

### Other Run Endpoints

```powershell
# Get full run detail
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/runs/<run_id>

# List mismatches
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/runs/<run_id>/results/<result_id>/mismatches?limit=100&offset=0"

# Accept a mismatch
$body = @{ note = "Known rounding difference"; accepted_by = "analyst" } | ConvertTo-Json
Invoke-RestMethod -Method Patch -Headers $h -Uri "http://127.0.0.1:8000/api/runs/<run_id>/results/<result_id>/mismatches/<mismatch_id>/accept" -Body $body -ContentType "application/json"

# Compare two runs
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/runs/compare?run_a=<run_a>&run_b=<run_b>"

# Filter runs
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/runs?status=FAILED&run_type=bo_comparison"

# Export CSV
Invoke-WebRequest -Headers $h "http://127.0.0.1:8000/api/runs/<run_id>/export" -OutFile "results.csv"

# Delete run
Invoke-RestMethod -Method Delete -Headers $h "http://127.0.0.1:8000/api/runs/<run_id>"
```

## Testing

Run all tests:

```powershell
python -m pytest
```

Run unit tests:

```powershell
python -m pytest tests/unit/ -q
```

Run integration tests:

```powershell
python -m pytest tests/integration/ -q
```

Run focused tests:

```powershell
python -m pytest tests/unit/test_compare_api.py -q
python -m pytest tests/unit/test_dq_engine.py -q
python -m pytest tests/unit/test_dag_retry_trends.py -q
python -m pytest tests/unit/test_p2_features.py -q
python -m pytest tests/unit/test_lineage.py -q
python -m pytest tests/unit/test_audit.py -q
python -m pytest tests/integration/test_api_frontend_smoke.py -q
```

Check JavaScript syntax:

```powershell
node --check frontend/app.js
```

Compile a Python module:

```powershell
python -m py_compile api/routes/runs.py
```

Coverage example:

```powershell
python -m pytest --cov=etl_framework --cov=api --cov-report=term-missing
```

## Operations

### Logs

Main log:

```text
logs/etl_framework.log
```

### Metrics

```text
logs/metrics_<run_id>.json
```

### Reports

```text
reports/report_<run_id>.html
```

### Backup

For SQLite deployments, back up:

```text
etl_framework.db
logs/
reports/
```

### Upgrades

Recommended upgrade flow:

1. Stop the service.
2. Back up `etl_framework.db`, `logs/`, and `reports/`.
3. Pull or deploy the new code.
4. Activate the virtual environment.
5. Run `pip install -e .` or your deployment install command.
6. Start the service.
7. Open `/api/health`.
8. Run a small smoke reconciliation.

## Troubleshooting

### Port Already In Use

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
python -m uvicorn api.main:app --port 8004 --reload
```

### 401 Unauthorized

- You have not created a token yet, or the token was revoked.
- Open the Security section in the Config tab and create a new token.
- Paste the raw token into the "Set Active Token" input in the Security section.
- For API clients, pass `Authorization: Bearer etl_<token>` on every request (except `/api/health`).

### Metrics Not Found

- The run did not complete.
- `metrics_enabled` was false.
- The metrics file was deleted.

### Report Not Found Or Empty

Confirm the run exists:

```powershell
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/runs/<run_id>
```

### Schema Mismatch Error

Change `schema_mismatch_policy` to `warn`, exclude the column, or fix the target schema.

### Polars Backend Error

```powershell
pip install -e .
```

### pyodbc Or SQL Server Driver Error

Install Microsoft ODBC Driver for SQL Server and ensure `db_driver` exactly matches the installed driver name.

### Runs Stuck In PENDING

Background execution happens in the API process. If the process exits mid-run, status can remain `PENDING` or `RUNNING`. Restart the server and trigger a new run.

### Static UI Not Updating

Hard-refresh the browser or open dev tools and disable cache.

## Quick Start

```powershell
cd C:\atom
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
```

Then open:

```text
http://127.0.0.1:8000
```

Minimal first run:

1. Open Config → Security → create a token → paste it into Set Active Token.
2. Open Launch.
3. Keep `use_live_connections` off.
4. Select one or more seed jobs.
5. Click Run Tests.
6. Open Monitor.
7. Open History for details and mismatches.
8. Open Reports for report, metrics, and logs.
9. Open Compare to compare runs or files.
