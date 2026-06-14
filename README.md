# ETL Test Framework

ETL Test Framework is a FastAPI and Alpine.js application for running ETL reconciliation tests, comparing BusinessObjects reports, monitoring execution, reviewing mismatches, accepting known differences, and browsing themed reports, metrics, and logs.

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
- Run jobs in parallel or sequential execution mode.
- Store runs as database-backed `TestRun` records with full result history.
- Track mismatch details and accept known mismatches with a note and optional user.
- Compare two stored runs to see improved, regressed, unchanged, added, and removed tests.
- Compare SAP BusinessObjects report sources from live BO, file paths, or uploads.
- Launch dual-environment reconciliation runs and compare the paired results.
- Compare a stored reconciliation run or HTML report against a production HTML report.
- Browse generated HTML reports, metrics dashboards, and searchable logs in a black themed UI.
- Use SAP BO and Automic adapters from the UI and API.
- Use the REST API directly with OpenAPI documentation at `/docs`.

## Architecture

```text
Browser
  Alpine.js SPA
  Tabs: Config, Launch, Monitor, History, Adapters, Reports, Compare
      |
      | HTTP / JSON / HTML
      v
FastAPI app
  api/routes/configs.py
  api/routes/jobs.py
  api/routes/runs.py
  api/routes/adapters.py
  api/routes/compare.py
  api/routes/health.py
      |
      v
Core framework
  etl_framework/reconciliation
  etl_framework/runner
  etl_framework/reporting
  etl_framework/repository
  etl_framework/sap_bo
  etl_framework/automic
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

The default database is SQLite at `./etl_framework.db`. Existing SQLite databases are updated at startup for the Compare tab columns.

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

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/configs" -Body $body -ContentType "application/json"
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

- saved environment configs
- saved jobs
- test runs
- test results
- mismatch details

Compare Tab additions include:

- `TestRun.run_type`
- `TestRun.pair_id`
- `MismatchDetail.accepted`
- `MismatchDetail.accepted_note`
- `MismatchDetail.accepted_at`
- `MismatchDetail.accepted_by`

Startup calls `init_db()`, creates missing tables, and applies lightweight SQLite column additions for existing local databases.

### Files Written By The App

| Path | Purpose |
|---|---|
| `etl_framework.db` | Default SQLite app database. |
| `logs/etl_framework.log` | Main application log. |
| `logs/metrics_<run_id>.json` | Metrics sidecar for a run. |
| `reports/report_<run_id>.html` | Generated HTML report. |

## Using The Web UI

The web UI has seven tabs. Recent changes are tracked in [fix.md](fix.md).

### Config

Use this tab to:

- Create saved environment configs.
- Edit connection details.
- Validate config values.
- Store SAP BO and Automic credentials for adapter workflows.
- **Import YAML** — expand the "Import YAML" card, paste a YAML block defining one or more named environments, and click Import to create all configs in one step.

Typical workflow:

1. Open Config.
2. Select New Config or paste a YAML block into Import YAML.
3. Enter environment name and connection details.
4. Save.
5. Use the saved config in Launch, Adapters, or Compare.

### Launch

Use this tab to:

- Select source and target environment labels.
- Choose a saved config.
- Pick run settings.
- Select jobs from the catalog.
- **Create, edit, or delete jobs directly** — use the "+ New Job" button in the Job Catalog card to define a reconciliation job without going through the Adapters tab.
- Order jobs in the execution sequence.
- Start a run.

Default seed jobs are returned if the database has no saved jobs:

- `orders_reconciliation`
- `customers_reconciliation`
- `products_reconciliation`
- `inventory_check`
- `sales_summary_validation`
- `sap_bo_sales_report`
- `automic_nightly_load`

### Monitor

Use this tab to:

- Watch active runs.
- View run progress.
- See passed, failed, slow, and error counters.
- Track the current job where progress data is available.

### History

Use this tab to:

- Browse previous runs with **status** and **run type** filters.
- Open run details.
- Review per-test results.
- Expand stored mismatch details with **Load More** paging (50 rows per page).
- Accept a mismatch with a note.
- Compare two stored runs from the History compare panel; click **View →** on any regressed or failed row to open the mismatch drawer for that result.
- **Export CSV** — download all test results for a run as a CSV file from the run detail view.
- **Delete** a run from the run list.

Mismatch acceptance changes the mismatch row to accepted. If all mismatches for a failed test result are accepted, the result can be marked passed and run counters are adjusted.

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

The same pages are available directly:

```text
/api/runs/{run_id}/report
/api/runs/{run_id}/metrics
/api/runs/{run_id}/logs
```

### Compare

Use this tab for first-class comparison workflows.

BO Report mode:

- Compare live BO sources.
- Compare CSV/XLSX paths.
- Compare uploaded CSV/XLSX files.
- Configure key columns and excluded columns.
- Store the comparison as a `bo_comparison` run.
- Results table shows per-query row, with a **View** button that opens the mismatch drawer when mismatches exist, and an **Open in Reports** link to the themed HTML report.

Reconciliation (dual-env) mode:

- Launch two environment runs as a pair with full `run_settings` forwarded to both runs.
- Poll until both are complete.
- Compare the paired results.
- **Past Dual-Env Pairs** card shows all previously launched pairs; click **Load** on any row to reload those results.

Recon File Compare mode:

- Compare a stored run or HTML report against a production HTML report.
- **Source B** toggles between **Path** and **Upload** using pill buttons, showing only the relevant input.
- Both Source A and Source B cards have an editable **Label** field that sets the column heading in the comparison output.
- Results table shows per-query name, row counts for each source, and a status badge (Matched / Differs).

## Reports, Metrics, And Logs

### Report

Endpoint:

```text
GET /api/runs/{run_id}/report
```

This generates or serves a themed HTML report from `reports/report_<run_id>.html`. The report includes:

- run ID
- start time
- source and target environment labels
- summary counters
- reconciliation result table
- mismatch sections
- accepted mismatch notes

### Metrics

Endpoint:

```text
GET /api/runs/{run_id}/metrics
```

Browser requests receive themed HTML. API clients and tests can request JSON:

```text
GET /api/runs/{run_id}/metrics?format=json
```

Metrics include:

- run ID
- generated timestamp
- total tests
- passed count
- failed count
- slow count
- total duration
- per-test duration, row counts, status, and issue count

### Logs

Endpoint:

```text
GET /api/runs/{run_id}/logs
```

Browser requests receive a themed log explorer with search controls.

Raw text:

```text
GET /api/runs/{run_id}/logs?format=text
```

JSON search:

```text
GET /api/runs/{run_id}/logs?format=json&q=schema_check&level=ERROR&limit=500
```

Parameters:

| Parameter | Description |
|---|---|
| `q` | Case-insensitive text search. |
| `level` | `ERROR`, `WARNING`, `INFO`, or `DEBUG`. |
| `limit` | Maximum returned matching lines. |
| `format` | `html`, `json`, or `text`. |

Example:

```text
http://127.0.0.1:8000/api/runs/run-002/logs?q=schema_check&level=ERROR
```

## Compare Tab

### BO Report Compare API

```text
POST /api/compare/bo-report
```

Example upload-vs-upload request:

```json
{
  "source_a": {
    "source_type": "upload",
    "file_content_b64": "<base64 csv or xlsx>",
    "file_name": "a.csv"
  },
  "source_b": {
    "source_type": "upload",
    "file_content_b64": "<base64 csv or xlsx>",
    "file_name": "b.csv"
  },
  "key_columns": ["id"],
  "exclude_columns": ["last_updated"],
  "label_a": "Source A",
  "label_b": "Source B"
}
```

Supported source types:

- `live`: requires `config_id`
- `path`: requires `file_path`
- `upload`: requires `file_content_b64` and usually `file_name`

### Dual-Environment Compare API

```text
POST /api/compare/dual-env
GET /api/compare/pairs
GET /api/compare/pairs/{pair_id}
```

Example:

```json
{
  "config_id_a": 1,
  "config_id_b": 2,
  "source_env_a": "warehouse-dev",
  "target_env_a": "bo-dev",
  "source_env_b": "warehouse-prod",
  "target_env_b": "bo-prod",
  "job_names": ["orders_reconciliation"]
}
```

### Reconciliation File Compare API

```text
POST /api/compare/recon-file
```

Use it to compare:

- stored run vs production HTML report
- HTML file path vs production HTML report
- uploaded HTML content vs uploaded production HTML content

## API Usage

### Health

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

### List Jobs

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/jobs
```

### Create A Job

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
} | ConvertTo-Json -Depth 6

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/jobs" -Body $job -ContentType "application/json"
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
    schema_mismatch_policy = "warn"
    comparison_backend = "pandas"
    metrics_enabled = $true
    use_live_connections = $false
  }
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/runs" -Body $run -ContentType "application/json"
```

### Check Run Status

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/runs/<run_id>/status
```

The response includes `run_type` (`reconciliation` or `bo_comparison`) and `pair_id` (set when the run belongs to a dual-env pair). Both fields are returned consistently by `GET /api/runs`, `GET /api/runs/{run_id}/status`, and the compare-runs endpoint.

### Get Run Detail

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/runs/<run_id>
```

### List Mismatches

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/runs/<run_id>/results/<result_id>/mismatches?limit=100&offset=0"
```

### Accept A Mismatch

```powershell
$body = @{ note = "Known rounding difference"; accepted_by = "analyst" } | ConvertTo-Json

Invoke-RestMethod `
  -Method Patch `
  -Uri "http://127.0.0.1:8000/api/runs/<run_id>/results/<result_id>/mismatches/<mismatch_id>/accept" `
  -Body $body `
  -ContentType "application/json"
```

### Compare Two Runs

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/runs/compare?run_a=<run_a>&run_b=<run_b>"
```

### Filter Runs

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/runs?status=FAILED&run_type=bo_comparison"
```

Supported `status` values: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `ERROR`.
Supported `run_type` values: `reconciliation`, `bo_comparison`.

### Export Run Results As CSV

```powershell
Invoke-WebRequest "http://127.0.0.1:8000/api/runs/<run_id>/export" -OutFile "results.csv"
```

### Delete A Run

```powershell
Invoke-RestMethod -Method Delete "http://127.0.0.1:8000/api/runs/<run_id>"
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
python -m pytest tests/unit/test_mismatch_accept.py -q
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

Search from PowerShell:

```powershell
Select-String -Path .\logs\etl_framework.log -Pattern "ERROR|schema_check" -Context 3,10
```

Search from browser:

```text
http://127.0.0.1:8000/api/runs/<run_id>/logs?q=schema_check&level=ERROR
```

### Metrics

Metrics are written only when `metrics_enabled` is true.

```text
logs/metrics_<run_id>.json
```

Open in browser:

```text
http://127.0.0.1:8000/api/runs/<run_id>/metrics
```

### Reports

Reports are generated under:

```text
reports/report_<run_id>.html
```

Open in browser:

```text
http://127.0.0.1:8000/api/runs/<run_id>/report
```

### Backup

For SQLite deployments, back up:

```text
etl_framework.db
logs/
reports/
```

Back up while the service is stopped, or use SQLite online backup tooling if downtime is not acceptable.

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

### Metrics Not Found

Cause:

- The run did not complete.
- `metrics_enabled` was false.
- The metrics file was deleted.

Fix:

- Run again with `metrics_enabled: true`.
- Check `logs/etl_framework.log`.

### Report Not Found Or Empty

The report route generates a report from stored run data. Confirm the run exists:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/runs/<run_id>
```

### Schema Mismatch Error

Example:

```text
Schema mismatch in 'schema_check': missing_in_target=['amount'], extra_in_target=[]
```

Meaning:

- Source has column `amount`.
- Target does not.
- The run used `schema_mismatch_policy: "error"`.

Fix options:

- Add the missing target column.
- Exclude the column for that job.
- Change schema mismatch policy to `warn`.

### Polars Backend Error

`polars` and `pyarrow` are bundled as core dependencies. If you see an import error for either package, re-run the base install:

```powershell
pip install -e .
```

### pyodbc Or SQL Server Driver Error

Install Microsoft ODBC Driver for SQL Server and ensure `db_driver` exactly matches the installed driver name.

Common values:

```text
ODBC Driver 17 for SQL Server
ODBC Driver 18 for SQL Server
```

### SAP BO Authentication Error

Check:

- `bo_url`
- `bo_user`
- `bo_password`
- network access to `/biprws/logon/long`
- account permissions for documents and reports

### Automic Lookup Error

Check:

- `automic_url`
- `automic_user`
- `automic_password`
- whether the identifier type is `job_name` or `run_id`
- network route from the app host to Automic

### Runs Stuck In PENDING

Background execution happens in the API process. If the process exits mid-run, status can remain `PENDING` or `RUNNING`.

Fix:

- Restart the server.
- Trigger a new run.
- Inspect logs for the original failure.

### Static UI Not Updating

Browsers can cache `app.js` and `styles.css`. Hard-refresh the browser or open dev tools and disable cache.

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

1. Open Launch.
2. Keep `use_live_connections` off.
3. Select one or more seed jobs.
4. Click Run Tests.
5. Open Monitor.
6. Open History for details and mismatches.
7. Open Reports for report, metrics, and logs.
8. Open Compare to compare runs or files.
