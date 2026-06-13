# ETL Framework

A reconciliation and testing framework for ETL pipelines with a FastAPI backend, Alpine.js web GUI, and integrations for SAP BusinessObjects and Automic Workload Automation.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Running the Web GUI](#running-the-web-gui)
7. [Using the Web GUI](#using-the-web-gui)
8. [Running Tests from the Command Line](#running-tests-from-the-command-line)
9. [Running the Unit Test Suite](#running-the-unit-test-suite)
10. [API Reference](#api-reference)
11. [Project Structure](#project-structure)
12. [External Integrations](#external-integrations)
13. [Troubleshooting](#troubleshooting)

---

## Overview

The ETL Framework compares data between source and target databases row-by-row, identifies mismatches, and produces structured reports. It supports:

- **Reconciliation jobs** — SQL query pairs executed against source and target databases, compared with configurable tolerance
- **SAP BusinessObjects reports** — download and track BO reports via the RESTful Web Service SDK
- **Automic job status** — look up job status by name or run ID from Automic Workload Automation
- **Live or simulated mode** — runs in simulation by default (no external connections needed); flip `use_live_connections` to hit real databases and adapters
- **Web GUI** — 6-tab single-page app for config management, job launching, live monitoring, history, adapter integration, and report viewing
- **REST API** — full FastAPI backend with OpenAPI docs at `/docs`

---

## Architecture

```
┌─────────────────────────────────────────┐
│  Browser – Alpine.js + Tailwind SPA     │
│  (Config · Launch · Monitor · History · │
│   Adapters · Reports)                   │
└───────────────┬─────────────────────────┘
                │ HTTP / JSON
┌───────────────▼─────────────────────────┐
│  FastAPI (api/)                         │
│  /api/configs  /api/runs  /api/jobs     │
│  /api/adapters  /api/health             │
└──────┬──────────────┬────────────────────┘
       │              │
┌──────▼──────┐ ┌─────▼──────────────────┐
│ SQLite DB   │ │ etl_framework/          │
│ (dev/test)  │ │  reconciliation/        │
│             │ │  sap_bo/ (BORestClient) │
│ or MSSQL    │ │  automic/ (AutomicClient│
│ (live mode) │ │  runner/  reporting/    │
└─────────────┘ └────────────────────────┘
```

The database defaults to **SQLite** (`etl_framework.db` in the project root) for development and testing. Switch to MSSQL by supplying credentials in a config and enabling `use_live_connections`.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11 or later | 3.14 used in development |
| pip | 23+ | Bundled with Python |
| ODBC Driver | 17 or 18 for SQL Server | Only needed for live MSSQL connections |

Optional:

| Tool | Purpose |
|---|---|
| SAP BusinessObjects server | Live BO report downloads |
| Automic Workload server | Live job status lookups |

---

## Installation

### 1. Clone the repository

```bash
git clone <repo-url>
cd atom
```

### 2. Create and activate a virtual environment

**Windows (PowerShell)**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**macOS / Linux**
```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -e .
```

Install optional extras as needed:

```bash
# Structured JSON logging
pip install -e ".[json-logging]"

# Polars comparison backend (faster than pandas for large datasets)
pip install -e ".[polars]"

# OpenTelemetry distributed tracing
pip install -e ".[tracing]"

# Development tools (pytest, hypothesis, coverage)
pip install -e ".[dev]"
```

Install everything at once:

```bash
pip install -e ".[json-logging,polars,tracing,dev]"
```

---

## Configuration

### Environment config file (YAML)

Create a YAML file describing your environments. The framework resolves `${ENV_VAR}` placeholders from the process environment.

```yaml
# config/environments.yaml
environments:
  dev:
    db_host: dev-sql-server.internal
    db_port: 1433
    db_name: etl_source_dev
    db_user: sa
    db_password: ${DEV_DB_PASSWORD}
    db_driver: "ODBC Driver 17 for SQL Server"
    db_pool_size: 5
    db_connect_timeout: 15

    bo_url: http://bi-server.internal:6405
    bo_user: bo_svc_account
    bo_password: ${BO_PASSWORD}
    bo_timeout: 60

    automic_url: http://automic.internal:8080
    automic_user: automic_svc
    automic_password: ${AUTOMIC_PASSWORD}
    automic_timeout: 30
    automic_max_retries: 3

  prod:
    db_host: prod-sql-server.internal
    db_port: 1433
    db_name: etl_source_prod
    db_user: sa
    db_password: ${PROD_DB_PASSWORD}
    db_driver: "ODBC Driver 17 for SQL Server"
    bo_url: http://bi-server-prod.internal:6405
    bo_user: bo_svc_account
    bo_password: ${PROD_BO_PASSWORD}
    automic_url: http://automic-prod.internal:8080
    automic_user: automic_svc
    automic_password: ${PROD_AUTOMIC_PASSWORD}
```

Export secrets before starting the server:

```bash
# Windows PowerShell
$env:DEV_DB_PASSWORD  = "your_password"
$env:BO_PASSWORD      = "your_bo_password"
$env:AUTOMIC_PASSWORD = "your_automic_password"

# macOS / Linux
export DEV_DB_PASSWORD="your_password"
export BO_PASSWORD="your_bo_password"
export AUTOMIC_PASSWORD="your_automic_password"
```

### EnvironmentConfig fields

| Field | Default | Description |
|---|---|---|
| `db_host` | *(required)* | SQL Server hostname or IP |
| `db_port` | `1433` | SQL Server port |
| `db_name` | `""` | Database name |
| `db_user` | `""` | Login username |
| `db_password` | *(required)* | Login password |
| `db_driver` | `ODBC Driver 17 for SQL Server` | pyodbc driver string |
| `db_pool_size` | `5` | SQLAlchemy connection pool size |
| `db_pool_overflow` | `10` | Max overflow connections |
| `db_pool_timeout` | `30` | Pool checkout timeout (seconds) |
| `db_pool_recycle` | `3600` | Connection recycle interval (seconds) |
| `db_connect_timeout` | `15` | Initial connection timeout (seconds) |
| `bo_url` | `""` | SAP BO server base URL |
| `bo_user` | `""` | SAP BO username |
| `bo_password` | `""` | SAP BO password |
| `bo_timeout` | `60` | SAP BO request timeout (seconds) |
| `automic_url` | `""` | Automic REST API base URL |
| `automic_user` | `""` | Automic username |
| `automic_password` | `""` | Automic password |
| `automic_timeout` | `30` | Automic request timeout (seconds) |
| `automic_max_retries` | `3` | Automic retry count on transient errors |

---

## Running the Web GUI

### Start the server

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

- `--reload` enables hot-reload during development (omit in production)
- The first startup creates `etl_framework.db` (SQLite) in the project root

**Open the GUI:** [http://localhost:8000](http://localhost:8000)

**OpenAPI docs:** [http://localhost:8000/docs](http://localhost:8000/docs)

**ReDoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)

### Production startup (no reload, multiple workers)

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Environment variable overrides

| Variable | Default | Description |
|---|---|---|
| `ETL_DB_PATH` | `etl_framework.db` | Path to SQLite file |
| `ETL_LOG_LEVEL` | `INFO` | Logging level |
| `ETL_METRICS_DIR` | `logs/` | Directory for metrics JSON sidecars |

---

## Using the Web GUI

The GUI is a 6-tab single-page application. Each tab is described below.

---

### Tab 1 — Config

**Purpose:** Create and manage environment configurations (database credentials, SAP BO credentials, Automic credentials).

**Workflow:**

1. Click **+ New Config**.
2. Fill in **Config Name** (used as a label) and **Environment** (dev / qa / staging / prod).
3. Under **Database**, enter the SQL Server host, port, name, user, and password.
4. Under **SAP BusinessObjects**, enter the BO server URL, user, and password.
5. Under **Automic**, enter the Automic REST API URL, user, and password.
6. Click **Validate** to check the field values without saving.
7. Click **Save** to persist the config to the database.

Configs are stored in the local SQLite database and referenced by ID when launching runs or browsing adapters.

**Import from YAML:**  
POST to `/api/configs/import-yaml` with a `yaml_content` body, or use the API docs at `/docs`.

---

### Tab 2 — Launch

**Purpose:** Select jobs, configure run settings, and trigger a test run.

**Workflow:**

1. **Live Connections toggle** (top-right of tab): off by default (simulation mode). Turn it on to use real database and adapter connections using the selected saved config's credentials.
2. Choose **Source Env** and **Target Env** from the dropdowns.
3. Optionally select a **Saved Config** — when live connections is on, credentials are pulled from this config.
4. Adjust run settings:
   - **Execution Mode**: parallel (default) or sequential
   - **Max Workers**: number of concurrent test threads (parallel mode only)
   - **Max Duration (s)**: per-job timeout; `0` = unlimited
   - **Comparison Backend**: `pandas` (default) or `polars`
   - **Health gate**: run DB connectivity checks before tests and abort if any fail
   - **Metrics**: write a JSON metrics sidecar to `logs/metrics_<run_id>.json`
   - **NULL = NULL**: treat SQL NULLs as equal (default on)
   - **Hash precheck**: fast hash comparison before row-level diff (default on)
5. Check jobs from the **Job Catalog** to add them to the **Execution Sequence**.
6. Reorder jobs in the sequence using the ↑ / ↓ buttons, or remove with ✕.
7. Click **▶ Run Tests**. The run is submitted in the background and you are redirected to the Monitor tab.

**Job types in the catalog:**

| Badge | Type | Behaviour |
|---|---|---|
| `reconciliation` | SQL query diff | Runs paired SQL queries on source and target, compares row by row |
| `bo_report` | SAP BO download | Downloads a BO report; PASSED if download succeeds |
| `automic_job` | Automic status | Polls Automic for job status; maps result to PASSED / FAILED / ERROR |
| `health_check` | Connectivity probe | Checks DB reachability only |

---

### Tab 3 — Monitor

**Purpose:** Watch active and recently completed runs in real time.

The page polls every 5 seconds. For each active run you can see:

- Run ID (truncated), status badge, source → target environment
- **Progress bar** driven by `/api/runs/{id}/progress`
- **Current job** being executed
- Stat cards: Passed / Failed / Slow / Error

When a run finishes the status badge updates automatically on the next poll.

---

### Tab 4 — History

**Purpose:** Browse completed runs and drill into per-test results.

**History list:** Click any row (or **View →**) to open the detail view.

**Detail view:**

- Gradient stat cards: Total / Passed / Failed / Slow / Error
- **Donut chart** (Chart.js) showing the pass/fail/slow distribution
- Run metadata: ID, environments, start time, completion time
- Links to **Report**, **Metrics JSON**, and **Logs**
- Per-test result table: status badge, duration, row counts, mismatch count
- **Details →** button on rows with mismatches opens the **slide-over drawer** showing individual mismatch records (column name, source value, target value, key values, mismatch type) with pagination (100 per page, Load more button)

---

### Tab 5 — Adapters

**Purpose:** Browse SAP BO documents and Automic jobs, then push them to the Job Catalog.

#### SAP BusinessObjects panel

1. Select a **Config** from the dropdown (must have `bo_url`, `bo_user`, `bo_password` set).
2. Click **Test Connection** — displays latency and OK/error message.
3. Click **Browse Documents** to list all BI documents from the BO server.
4. Click a document row to expand it and load its report tabs.
5. For each report tab:
   - **XLSX** / **PDF** — downloads the report file to your browser immediately.
   - **+ Job** — opens the Add Job modal. Fill in job name, key columns, and format, then save. The job appears in the Job Catalog on the Launch tab.

#### Automic panel

1. Select a **Config** (must have `automic_url`, `automic_user`, `automic_password` set).
2. Choose lookup type: **Job Name** or **Run ID**.
3. Enter the identifier and click **Lookup**.
4. The result shows status badge, environment, and checked timestamp.
5. Click **+ Add to Job Catalog** to create an `automic_job` entry in the catalog.

Recent lookups are saved to **sessionStorage** and shown below the lookup panel for the duration of the browser session.

---

### Tab 6 — Reports

**Purpose:** View HTML reconciliation reports for completed runs in an embedded iframe.

1. Select a run from the dropdown.
2. Click **Load Report** to render the HTML report inline.
3. The report is generated on demand by the server from the run's test results.

---

## Running Tests from the Command Line

### Option A — Trigger a run via the REST API

Use `curl` or any HTTP client to submit a run without opening the browser:

```bash
# Trigger a run with specific jobs in simulation mode
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{
    "source_env": "dev",
    "target_env": "prod",
    "job_sequence": ["orders_reconciliation", "customers_reconciliation"],
    "run_settings": {
      "execution_mode": "parallel",
      "max_workers": 4,
      "metrics_enabled": true,
      "use_live_connections": false
    }
  }'
```

The response includes a `run_id`. Poll for status:

```bash
curl http://localhost:8000/api/runs/<run_id>/status
```

Poll for live progress:

```bash
curl http://localhost:8000/api/runs/<run_id>/progress
```

Fetch the full result with per-test breakdown:

```bash
curl http://localhost:8000/api/runs/<run_id>
```

Download the HTML report:

```bash
curl http://localhost:8000/api/runs/<run_id>/report -o report.html
```

Download metrics JSON:

```bash
curl http://localhost:8000/api/runs/<run_id>/metrics
```

### Option B — Trigger a run with live connections

First create a config:

```bash
curl -X POST http://localhost:8000/api/configs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "dev-config",
    "env_name": "dev",
    "config_data": {
      "db_host": "dev-sql-server.internal",
      "db_port": 1433,
      "db_name": "etl_source",
      "db_user": "sa",
      "db_password": "secret",
      "bo_url": "http://bi-server:6405",
      "bo_user": "admin",
      "bo_password": "bo_secret",
      "automic_url": "http://automic:8080",
      "automic_user": "admin",
      "automic_password": "ac_secret"
    }
  }'
```

Then trigger with `use_live_connections: true` and the returned config ID:

```bash
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{
    "source_env": "dev",
    "target_env": "prod",
    "job_sequence": ["orders_reconciliation"],
    "config_id": 1,
    "run_settings": {
      "use_live_connections": true,
      "execution_mode": "sequential",
      "metrics_enabled": true
    }
  }'
```

### Option C — Import configs from YAML and trigger a run

```bash
# Import environments from YAML file
YAML_CONTENT=$(cat config/environments.yaml)
curl -X POST http://localhost:8000/api/configs/import-yaml \
  -H "Content-Type: application/json" \
  -d "{\"yaml_content\": $(echo "$YAML_CONTENT" | python -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}"

# Trigger run
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{
    "source_env": "dev",
    "target_env": "prod",
    "job_sequence": ["orders_reconciliation"],
    "run_settings": {"use_live_connections": true}
  }'
```

### Option D — CLI runner script

The included `run_tests.py` script provides a command-line entry point for quick inspection:

```bash
python run_tests.py \
  --config config/environments.yaml \
  --source-env dev \
  --target-env prod \
  --max-workers 4 \
  --log-level INFO \
  --log-format json

# Run DB health checks only (no test execution)
python run_tests.py \
  --config config/environments.yaml \
  --source-env dev \
  --target-env prod \
  --health-check
```

### Option E — SAP BO adapter via CLI (curl)

Browse documents:

```bash
curl "http://localhost:8000/api/adapters/sap-bo/documents?config_id=1"
```

List report tabs for a document:

```bash
curl "http://localhost:8000/api/adapters/sap-bo/documents/101/reports?config_id=1"
```

Download a report as XLSX:

```bash
curl -OJ "http://localhost:8000/api/adapters/sap-bo/documents/101/reports/1/download?config_id=1&format=xlsx"
```

Download as PDF:

```bash
curl -OJ "http://localhost:8000/api/adapters/sap-bo/documents/101/reports/1/download?config_id=1&format=pdf"
```

### Option F — Automic lookup via CLI (curl)

Look up by job name:

```bash
curl -X POST http://localhost:8000/api/adapters/automic/lookup \
  -H "Content-Type: application/json" \
  -d '{"config_id": 1, "identifier": "ETL_NIGHTLY_LOAD", "id_type": "job_name"}'
```

Look up by run ID:

```bash
curl -X POST http://localhost:8000/api/adapters/automic/lookup \
  -H "Content-Type: application/json" \
  -d '{"config_id": 1, "identifier": "RUN_42", "id_type": "run_id"}'
```

---

## Running the Unit Test Suite

### Run all tests

```bash
pytest
```

### Run with verbose output

```bash
pytest -v
```

### Run a specific test file

```bash
pytest tests/unit/test_reconciliation.py -v
pytest tests/unit/test_adapters_routes.py -v
pytest tests/unit/test_run_executor_live.py -v
```

### Run a specific test by name

```bash
pytest tests/unit/test_reconciliation.py::test_identical_frames_pass -v
pytest tests/unit/test_run_executor_live.py::test_bo_report_job_returns_passed_on_success -v
```

### Run with coverage report

```bash
pytest --cov=etl_framework --cov=api --cov-report=term-missing
```

### Run only fast unit tests (exclude integration)

```bash
pytest tests/unit/ -v
```

### Run integration tests (requires the server to be running)

```bash
pytest tests/integration/ -v
```

### Run tests matching a keyword

```bash
# All tests related to SAP BO
pytest -k "bo" -v

# All reconciliation tests
pytest -k "reconciliation" -v

# All adapter and executor tests
pytest -k "adapter or executor" -v
```

### Test suite breakdown

| File | What it covers |
|---|---|
| `test_reconciliation.py` | Core engine: value diff, missing rows, schema policy, chunking, hash precheck |
| `test_backends.py` | PandasBackend and PolarsBackend comparison logic |
| `test_run_executor.py` | RunExecutor simulation mode end-to-end |
| `test_run_executor_live.py` | Live engine wiring: SQLAlchemyQueryEngine, bo_report, automic_job dispatch |
| `test_adapters_routes.py` | `/api/adapters/*` FastAPI routes |
| `test_runs_extensions.py` | `/progress` and `/results/{id}/mismatches` endpoints |
| `test_adapter_service.py` | AdapterService unit tests |
| `test_bo_rest_client.py` | BORestClient: list_documents, list_reports, download_report |
| `test_db_engine.py` | DBEngine construction and query delegation |
| `test_new_schemas.py` | Pydantic schema validation for all new schemas |
| `test_api.py` | Full FastAPI route tests via TestClient |
| `test_repository.py` | RunRepository and ConfigRepository CRUD |
| `test_config.py` | ConfigLoader YAML parsing and env var resolution |
| `test_external_adapters.py` | BORestClient and AutomicClient importability |
| `test_health.py` | `/api/health/checks` endpoint |
| `test_runner.py` | TestRunner parallel and sequential execution |
| `test_tracing.py` | OpenTelemetry span context manager |
| `test_metrics.py` | MetricsWriter JSON output |

---

## API Reference

The full interactive API documentation is available at [http://localhost:8000/docs](http://localhost:8000/docs) when the server is running.

### Quick reference

#### Configs

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/configs` | List all saved configs |
| `POST` | `/api/configs` | Create a config |
| `GET` | `/api/configs/{id}` | Get a config by ID |
| `PUT` | `/api/configs/{id}` | Update a config |
| `DELETE` | `/api/configs/{id}` | Delete a config |
| `POST` | `/api/configs/validate` | Validate config fields without saving |
| `POST` | `/api/configs/import-yaml` | Import configs from YAML content |

#### Runs

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/runs` | List runs (newest first, limit/offset) |
| `POST` | `/api/runs` | Trigger a new run (202 Accepted) |
| `GET` | `/api/runs/{run_id}` | Full run detail with per-test results |
| `GET` | `/api/runs/{run_id}/status` | Lightweight status poll |
| `GET` | `/api/runs/{run_id}/progress` | Live progress with percent_complete |
| `GET` | `/api/runs/{run_id}/results/{result_id}/mismatches` | Paginated mismatch rows |
| `GET` | `/api/runs/{run_id}/report` | Generated HTML report (file download) |
| `GET` | `/api/runs/{run_id}/metrics` | Metrics JSON sidecar |
| `GET` | `/api/runs/{run_id}/logs` | Plain-text log tail |
| `GET` | `/api/runs/{run_id}/artifacts` | List of available artifacts |

#### Jobs

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/jobs` | List all jobs in catalog |
| `POST` | `/api/jobs` | Create a job definition |
| `GET` | `/api/jobs/{name}` | Get a job by name |
| `PUT` | `/api/jobs/{name}` | Update a job |
| `POST` | `/api/jobs/import` | Bulk import jobs from JSON array |

#### Adapters

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/adapters/sap-bo/test` | Test SAP BO connection |
| `GET` | `/api/adapters/sap-bo/documents` | List BI documents |
| `GET` | `/api/adapters/sap-bo/documents/{doc_id}/reports` | List report tabs |
| `GET` | `/api/adapters/sap-bo/documents/{doc_id}/reports/{report_id}/download` | Download report (`?format=xlsx\|pdf\|csv`) |
| `POST` | `/api/adapters/automic/lookup` | Look up Automic job status |
| `POST` | `/api/adapters/jobs/from-bo-report` | Create a bo_report job from a BO report |
| `POST` | `/api/adapters/jobs/from-automic` | Create an automic_job from a lookup result |

#### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Server liveness check |
| `POST` | `/api/health/checks` | Run connectivity probes for given environments |

---

## Project Structure

```
atom/
├── api/
│   ├── main.py                  # FastAPI app factory, middleware, router registration
│   ├── schemas.py               # All Pydantic request/response models
│   ├── dependencies.py          # get_session dependency
│   ├── routes/
│   │   ├── configs.py           # /api/configs CRUD + validate + import-yaml
│   │   ├── runs.py              # /api/runs + /progress + /mismatches
│   │   ├── jobs.py              # /api/jobs CRUD
│   │   ├── adapters.py          # /api/adapters SAP BO + Automic
│   │   └── health.py            # /api/health/checks
│   └── services/
│       ├── run_executor.py      # Background run orchestration, engine selection
│       ├── adapter_service.py   # SAP BO / Automic facade
│       └── artifact_service.py  # HTML report generation
│
├── etl_framework/
│   ├── config/
│   │   ├── models.py            # EnvironmentConfig Pydantic model
│   │   └── loader.py            # YAML loader with ${ENV_VAR} resolution
│   ├── db/
│   │   └── engine.py            # DBEngine (SQLAlchemy MSSQL wrapper)
│   ├── reconciliation/
│   │   ├── engine.py            # ReconciliationEngine (query → compare → result)
│   │   ├── models.py            # ReconciliationResult, MismatchRecord
│   │   ├── normalizer.py        # DataFrame normalisation before comparison
│   │   ├── chunker.py           # Windowed chunked query support
│   │   └── backends/
│   │       ├── pandas_backend.py
│   │       └── polars_backend.py
│   ├── repository/
│   │   ├── database.py          # SQLAlchemy engine + SessionLocal + Base
│   │   ├── models.py            # ORM: TestRun, TestResult, MismatchDetail, SavedConfig, SavedJob
│   │   └── repository.py        # RunRepository, ConfigRepository, JobRepository
│   ├── runner/
│   │   ├── test_runner.py       # Parallel/sequential TestRunner
│   │   ├── state.py             # TestStatus enum, TestCaseState
│   │   └── health.py            # HealthChecker
│   ├── reporting/
│   │   ├── generator.py         # HTML report via Jinja2
│   │   ├── metrics.py           # MetricsWriter JSON sidecar
│   │   └── templates/
│   │       └── report.html.j2
│   ├── sap_bo/
│   │   ├── client.py            # BORestClient (SAP BO RESTful Web Service SDK)
│   │   ├── reports.py           # SAPBOReportRunner
│   │   └── validator.py         # SAPBOValidator (reconcile BO output vs DB)
│   ├── automic/
│   │   ├── client.py            # AutomicClient (job status lookup)
│   │   ├── jobs.py              # AutomicJobRunner
│   │   └── models.py            # JobStatus dataclass
│   └── utils/
│       ├── logging.py           # configure_logging (text / JSON)
│       ├── tracing.py           # configure_tracing + span() context manager
│       └── context.py           # run_id context variable
│
├── frontend/
│   ├── index.html               # 6-tab SPA shell (Alpine.js + Tailwind)
│   ├── app.js                   # All Alpine.js view logic
│   └── styles.css               # Custom SaaS UI stylesheet
│
├── tests/
│   ├── unit/                    # 236 unit tests (no network or server required)
│   └── integration/             # Smoke tests (server must be running)
│
├── run_tests.py                 # CLI entry point
├── pyproject.toml               # Package metadata and dependencies
└── etl_framework.db             # SQLite database (auto-created on first run)
```

---

## External Integrations

### SAP BusinessObjects RESTful Web Service SDK

The framework uses the SAP BO RESTful Web Service SDK endpoints:

| Endpoint | Method | Purpose |
|---|---|---|
| `/biprws/logon/long` | `POST` | Authenticate, obtain session token |
| `/biprws/raylight/v1/documents` | `GET` | List all BI documents |
| `/biprws/raylight/v1/documents/{doc_id}/reports` | `GET` | List report tabs within a document |
| `/biprws/raylight/v1/documents/{doc_id}/reports/{report_id}/content` | `GET` | Download report content |

Supported download formats via `Accept` header:

| Format | MIME type |
|---|---|
| `xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `pdf` | `application/pdf` |
| `csv` | `text/csv` |

The BO client authenticates automatically before each API call. Set `bo_url`, `bo_user`, and `bo_password` in the environment config.

### Automic Workload Automation

The framework polls Automic's REST API to retrieve job execution status. Results are mapped to the internal `TestStatus` enum (PASSED / FAILED / ERROR / RUNNING / PENDING).

---

## Troubleshooting

### Server won't start — `ModuleNotFoundError`

Ensure you installed the package in editable mode:

```bash
pip install -e .
```

### Database errors on startup

The SQLite file `etl_framework.db` is created automatically. If it is corrupted, delete it and restart:

```bash
rm etl_framework.db
uvicorn api.main:app --reload
```

### `pyodbc` / ODBC driver errors (live connections only)

Install the Microsoft ODBC Driver for SQL Server:

- **Windows:** [Download from Microsoft](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)
- **Ubuntu:** `sudo apt-get install msodbcsql17`
- **macOS:** `brew install msodbcsql17`

Verify the driver name matches the `db_driver` field in your config (e.g., `ODBC Driver 17 for SQL Server`).

### SAP BO connection errors

- Confirm `bo_url` points to the BI platform server including port (default `6405` for HTTP)
- The `/biprws/logon/long` endpoint must be accessible from the machine running the framework
- Check that the service account has permission to list and download reports

### Runs stuck in PENDING

The run is executed in a background thread. If the server process is killed mid-run the status stays PENDING. Re-trigger the run or manually update the status via the database.

### Tests failing — `pydantic ValidationError` on `EnvironmentConfig`

`db_host` and `db_password` are required fields. When writing tests, supply them explicitly:

```python
from etl_framework.config.models import EnvironmentConfig

env = EnvironmentConfig(
    name="test",
    db_host="localhost",
    db_password="secret",
)
```

### Port 8000 already in use

```bash
# Use a different port
uvicorn api.main:app --port 8080 --reload
```

---

## Quick-Start Checklist

```
□ python -m venv .venv && source .venv/bin/activate  (or .venv\Scripts\Activate.ps1)
□ pip install -e ".[dev]"
□ uvicorn api.main:app --reload
□ Open http://localhost:8000
□ Config tab → New Config → fill in credentials → Save
□ Launch tab → select jobs → Run Tests
□ Monitor tab → watch progress
□ History tab → click a run → view detail and mismatches
□ Adapters tab → select config → Browse Documents (SAP BO) or Lookup (Automic)
□ Reports tab → select a run → Load Report
```
