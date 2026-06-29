# ETL Test Framework

ETL Test Framework is a FastAPI and Alpine.js application for running ETL reconciliation tests, comparing BusinessObjects reports, monitoring execution, reviewing mismatches, accepting known differences, browsing themed reports and metrics, enforcing data quality rules across environments, and managing **Data Contracts** with SLA tracking and automated breach notifications.

The application can run entirely in local simulation mode for development, or it can connect to live SQL Server, SAP BusinessObjects, and Automic environments when configured.

## Quick Start

```powershell
cd C:\atom
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000`. On first load the UI prompts for a token — follow the [bootstrap steps](#authentication) below.

## Contents

- [Capabilities](#capabilities)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Development Deployment](#development-deployment)
- [Production Deployment](#production-deployment)
- [On-Premises Deployment](#on-premises-deployment)
- [Docker Or Service Deployment](#docker-or-service-deployment)
- [Database And Storage](#database-and-storage)
- [Using The Web UI](#using-the-web-ui)
- [Reports, Metrics, And Logs](#reports-metrics-and-logs)
- [Compare Tab](#compare-tab)
- [Data Contracts](#data-contracts)
- [API Usage](#api-usage)
- [ETL Test Capabilities](#etl-test-capabilities)
- [Testing](#testing)
- [Operations](#operations)
- [Troubleshooting](#troubleshooting)

## Capabilities

- Reconcile source and target datasets with configurable key columns, excluded columns, float tolerance, null handling, hash precheck, chunking, and schema mismatch policy.
- Run jobs in parallel or sequential execution mode with optional **retry policy** (max retries, exponential backoff).
- Define **Data Quality (DQ) rules** per job — 20 rule types across two categories:
  - **Basic**: `not_null`, `unique`, `row_count_min`, `row_count_max`, `row_count_between`, `column_mean_between`, `match_regex`, `custom_sql`
  - **Advanced**: `completeness_ratio`, `distinct_count_between`, `column_sum_between`, `column_std_dev_between`, `column_percentile`, `column_type_check`, `column_value_between`, `cross_column_consistency`, `pii_mask_check`, `no_whitespace`, `referential_check`, `custom_sql_assert`
  - Violations are captured as typed mismatches with configurable `error`/`warn` severity.
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
- **SSE run streaming** — subscribe to live progress events with `GET /api/runs/{run_id}/stream`; the Monitor tab uses Server-Sent Events with automatic fallback to 5-second polling.
- **Trend caching** — trend responses are memoised in-process with a short TTL; the cache is invalidated automatically when matching result rows change.
- **dbt artifact adapter** — `dbt_artifact` job type parses `run_results.json` (and optionally `manifest.json`) and maps dbt test statuses to normal run results, with failing/error nodes recorded as mismatch details.
- **Freshness checks** — `freshness` job type queries a timestamp column and fails if the most recent record is older than a configurable `max_age_hours` threshold.
- **Column profiling** — `profile` job type computes per-column statistics (null rate, distinct count, min/max, mean, std dev, p25/p50/p75/p95) and optionally detects metric drift against the previous profile run.
- **Schema snapshots** — `schema_snapshot` job type captures the column names and types for a query result and diffs them against the previous snapshot, flagging added, removed, or type-changed columns.
- **Cross-job assertions** — `cross_job_assertion` job type compares a metric (e.g. row count or distinct count) from one job against another job's metric within a configurable absolute or percentage tolerance.
- **Profile API** — `GET /api/jobs/{job}/profile` returns the latest column profile; `GET /api/jobs/{job}/profile/history?column=<col>` returns the metric history for a column; `POST /api/jobs/{job}/suggest-rules` auto-generates DQ rules from the latest profile.
- **Schema history API** — `GET /api/jobs/{job}/schema-history?environment=source` returns all snapshots with per-snapshot diffs.
- **Profile and Schema sub-tabs** — the History tab includes Profile and Schema sub-tabs for browsing stored statistics and snapshot diffs directly in the UI.
- **Data Contracts** — define named contracts that point at a source job and enforce ownership, SLA, and data quality expectations:
  - Contracts are stored in `/api/contracts` as first-class entities with `name`, `source_job`, `owner`, `sla_hours`, `consumers`, and `breach_severity`.
  - When a source job run **fails**, a breach opens automatically and a `contract.breached` webhook fires to configured endpoints.
  - When the source job **passes**, open breaches auto-resolve with `duration_hours` computed and a `contract.resolved` webhook fires.
  - Breaches that remain open past `sla_hours` are **escalated** by a background APScheduler job (every 15 minutes) and a `contract.escalated` webhook fires.
  - Contracts carry a semantic **version** (`1.0` by default); bump minor or major with `POST /api/contracts/{name}/bump`.
  - The **Contracts tab** in the UI lists all contracts with live OK / BREACHED / OVERDUE status badges, breach history, and inline version bump.
  - Derived endpoints expose the source job's DQ rules (`/rules`) and latest schema snapshot (`/schema`) without duplicating configuration.

## Architecture

Additional runtime capabilities:

- Trend responses are cached briefly and automatically invalidated when matching result rows change.
- Active run progress can be streamed from `GET /api/runs/{run_id}/stream`; the web UI uses this SSE stream with polling fallback.
- External monitor jobs can be created for SAP BO reports, Automic job/run lookups, and dbt artifact summaries.
- Audit events are visible in the History tab and queryable with `GET /api/audit`.

```text
Browser
  Alpine.js SPA
  Tabs: Config, Launch, Monitor, History, Adapters, Reports, Compare, Contracts
      |
      | HTTP / JSON / HTML
      v
FastAPI app  (BearerTokenMiddleware on all /api/* routes)
  api/routes/configs.py
  api/routes/jobs.py          — CRUD, DQ rules, depends_on, EXPLAIN validate
  api/routes/runs.py          — runs, SSE stream, trends, badges, baseline, mismatch-distribution
  api/routes/audit.py         — GET /api/audit event log
  api/routes/tokens.py        — API token CRUD
  api/routes/notifications.py — webhook CRUD + test ping
  api/routes/schedules.py     — cron schedule CRUD + run-now
  api/routes/lineage.py          — job lineage graph
  api/routes/profiles.py         — column profile + suggest-rules endpoints
  api/routes/schema_snapshots.py — schema snapshot history endpoint
  api/routes/contracts.py        — data contract CRUD, status, breaches, versions, rules, schema
  api/routes/adapters.py
  api/routes/compare.py
  api/routes/health.py
      |
      v
Core framework
  etl_framework/reconciliation   — ReconciliationEngine, DQEngine, polars/pandas backends
  etl_framework/runner
  etl_framework/reporting
  etl_framework/repository       — ORM models, repositories (incl. AuditRepository, ContractRepository)
  etl_framework/sap_bo
  etl_framework/automic
  api/services/run_executor.py          — retry, DAG resolution, DQ evaluation, all job types
  api/services/contract_breach_checker.py — post-run hook: open/resolve contract breaches
  api/services/audit_service.py         — actor extraction + AuditRepository facade
  api/services/dbt_artifact_parser.py   — parse run_results.json / manifest.json
  api/services/profile_service.py       — compute_profile(), detect_drift()
  api/services/schema_snapshot_service.py — capture_schema(), diff_schemas()
  api/services/notifier.py       — webhook fire-and-forget (incl. contract.breached/resolved/escalated)
  api/services/scheduler.py      — APScheduler wrapper (incl. 15-min contract escalation job)
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
- `frontend/vendor/tailwind.css` — pre-built Tailwind CSS (tree-shaken from actual class usage)
- `frontend/vendor/alpine.min.js` — Alpine.js v3.14.1
- `frontend/vendor/chart.umd.min.js` — Chart.js v4.4.3

All frontend assets are self-contained. The UI makes no requests to CDNs or external hosts and is safe to deploy in air-gapped corporate networks.

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

All `/api/*` routes (except `/api/health`) require a Bearer token. See [docs/auth.md](docs/auth.md) for the full reference.

#### Bootstrap (first run)

The database starts empty. The first `POST /api/tokens` requires no auth and always creates an admin token.

**Option A — Web UI**

1. Start the server and open `http://127.0.0.1:8000`.
2. The auth modal appears automatically. Enter a name (e.g. `admin`) and click **Create Token**.
3. The raw token is shown once — copy it now.
4. Paste it into the **Use existing token** field and click **Activate**.
5. The token is stored in `sessionStorage` and sent automatically. It clears when the tab closes.

**Option B — curl (scripted or CI)**

```bash
ADMIN_TOKEN=$(curl -sS -X POST http://localhost:8000/api/tokens \
  -H "Content-Type: application/json" \
  -d '{"name": "admin"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['raw_token'])")

echo "$ADMIN_TOKEN"   # store in a password manager — shown once only
```

After bootstrap, all `POST /api/tokens` calls require an admin `Authorization` header.

#### Create a standard user token

```bash
curl -sS -X POST http://localhost:8000/api/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "alice", "is_admin": false, "expires_at": "2027-01-01T00:00:00Z"}'
```

#### Verify a token

```bash
curl http://localhost:8000/api/auth/verify \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# → {"ok": true, "actor": "admin", "is_admin": true}
```

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

## On-Premises Deployment

This application is designed to work in air-gapped and corporate network environments where browser access to the internet is blocked. The UI has no runtime dependency on any CDN, external font service, or third-party host.

### What Is Self-Contained

All frontend libraries are vendored and committed to the repository. The browser only ever loads files served by the FastAPI process itself:

| File | Library | Size |
|---|---|---|
| `frontend/vendor/tailwind.css` | Tailwind CSS v3 (tree-shaken) | ~17 KB |
| `frontend/vendor/alpine.min.js` | Alpine.js v3.14.1 | ~44 KB |
| `frontend/vendor/chart.umd.min.js` | Chart.js v4.4.3 | ~201 KB |

There are no Google Fonts requests, no `jsdelivr.net` requests, and no `cdn.tailwindcss.com` requests. The font stack falls back to the OS system font (`system-ui`, `Segoe UI`, `sans-serif`).

The Python API itself has no outbound internet dependencies at runtime. All pip packages are installed from your internal PyPI mirror or from a pre-built wheel cache.

### Server Requirements

No Node.js is required on the deployment server. The vendored files are pre-built and committed. Only Python is needed:

- Python 3.11 or later
- pip (to install the application)
- Microsoft ODBC Driver 17 or 18 for SQL Server (if using live database connections)

### Deployment Steps

**1. Transfer the code to the on-premises server**

Either clone directly from your internal Git mirror, or copy the archive:

```powershell
# From an internal Git mirror
git clone https://git.internal.corp/etl-framework.git C:\atom

# Or copy a zip and extract
Expand-Archive -Path etl-framework.zip -DestinationPath C:\atom
```

**2. Create and activate a virtual environment**

```powershell
cd C:\atom
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**3. Install from a local PyPI mirror or pre-downloaded wheels**

If the server has access to an internal PyPI mirror (Artifactory, Nexus, etc.):

```powershell
pip install -e . --index-url https://pypi.internal.corp/simple/
```

If working completely offline with pre-downloaded wheels:

```powershell
# On a machine with internet access, download all wheels first:
pip download -e . -d ./wheels/

# Transfer ./wheels/ to the server, then install offline:
pip install -e . --no-index --find-links=./wheels/
```

**4. Set required environment variables**

```powershell
$env:ETL_DATABASE_URL = "sqlite:///C:/atom/etl_framework.db"
```

**5. Start the server**

```powershell
C:\atom\.venv\Scripts\python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

**6. Open the UI**

Navigate to `http://<server-hostname>:8000` from a browser on the corporate network. The UI loads entirely from the server — no internet access is required on either the server or the client browser.

### Verifying No External Requests

To confirm the browser is not calling any external hosts, open the browser developer tools (F12), go to the **Network** tab, reload the page, and filter by domain. All requests should resolve to your server hostname only. You should see:

- `GET /` → `index.html`
- `GET /vendor/tailwind.css`
- `GET /vendor/alpine.min.js`
- `GET /vendor/chart.umd.min.js`
- `GET /styles.css`
- `GET /app.js`
- `GET /api/health`

No requests to `cdn.tailwindcss.com`, `cdn.jsdelivr.net`, `fonts.googleapis.com`, or any other external host.

### Updating Vendor Files After UI Changes

The vendored files are committed to source control so the server never needs Node.js or internet access. If you modify the HTML or JS and add new Tailwind utility classes, regenerate the CSS on a developer machine that has Node.js:

```powershell
# One-time: install dev dependencies
npm install

# Rebuild Tailwind CSS from current HTML/JS class usage
npm run build:css
# Outputs: frontend/vendor/tailwind.css

# Commit the updated file
git add frontend/vendor/tailwind.css
git commit -m "rebuild tailwind css"
```

The Alpine.js and Chart.js files only need to be regenerated when upgrading their versions. To update them:

```powershell
npm install alpinejs@<version> chart.js@<version> --save-dev
copy node_modules\alpinejs\dist\cdn.min.js frontend\vendor\alpine.min.js
copy node_modules\chart.js\dist\chart.umd.js frontend\vendor\chart.umd.min.js
git add frontend/vendor/
git commit -m "bump alpinejs and chart.js vendor files"
```

### Reverse Proxy Configuration

For production, place the application behind IIS, nginx, or your corporate reverse proxy. Example nginx upstream block:

```nginx
upstream etl_framework {
    server 127.0.0.1:8000;
}

server {
    listen 443 ssl;
    server_name etl.internal.corp;

    location / {
        proxy_pass http://etl_framework;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # Required for SSE (Server-Sent Events) on the Monitor tab
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600;
    }
}
```

The `proxy_buffering off` setting is required for the **Monitor** tab's live run stream (`GET /api/runs/{run_id}/stream`) to deliver progress events in real time.

### Troubleshooting On-Premises

**UI blank or unstyled after deployment**

Check the browser Network tab for failed requests. If `vendor/tailwind.css`, `vendor/alpine.min.js`, or `vendor/chart.umd.min.js` return 404, the vendor files were not included in the deployment package. Verify `frontend/vendor/` is present on disk:

```powershell
Get-ChildItem C:\atom\frontend\vendor\
```

Expected output:
```
alpine.min.js         44 KB
chart.umd.min.js     201 KB
tailwind.css          17 KB
```

**SSE progress stream not updating (Monitor tab)**

If progress events do not arrive in real time, a reverse proxy is buffering the SSE response. Add `proxy_buffering off` (nginx) or equivalent IIS response buffering configuration. The UI falls back to 5-second polling automatically if the SSE connection drops.

**pip install fails with no network**

Use the offline wheel approach described in step 3 above. Alternatively, install from an internal Artifactory or Nexus PyPI mirror by setting `--index-url`.

**SQL Server connection error**

Install Microsoft ODBC Driver 17 or 18 for SQL Server from your internal software distribution. Verify the installed driver name matches `db_driver` in your saved config (default: `ODBC Driver 17 for SQL Server`).

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
| `column_profiles` | Per-column statistics captured by `profile` job runs (null rate, distinct count, percentiles, etc.) |
| `schema_snapshots` | Column-name-and-type snapshots captured by `schema_snapshot` job runs, with environment tag |
| `contracts` | Named data contracts with source job, owner, SLA hours, consumers, version, and active flag |
| `contract_versions` | Immutable version bump history for each contract (minor/major, note, timestamp) |
| `contract_breaches` | Per-contract breach records with open/resolved timestamps, escalation flag, and duration |

Startup calls `init_db()`, creates missing tables, and applies lightweight SQLite column additions for existing local databases.

### Files Written By The App

| Path | Purpose |
|---|---|
| `etl_framework.db` | Default SQLite app database. |
| `logs/etl_framework.log` | Main application log. |
| `logs/metrics_<run_id>.json` | Metrics sidecar for a run. |
| `reports/report_<run_id>.html` | Generated HTML report. |

## Using The Web UI

The web UI has eight tabs.

### Config

Use this tab to:

- Create saved environment configs.
- Edit connection details.
- Validate config values.
- Store SAP BO and Automic credentials for adapter workflows.
- **Import YAML** — expand the "Import YAML" card, paste a YAML block defining one or more named environments, and click Import to create all configs in one step.
- **Security** — create and manage API tokens. Created tokens are stored in `sessionStorage` (cleared when the browser tab closes).
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

The generic job editor supports `reconciliation`, `bo_report`, `automic_job`, `dbt_artifact`, `freshness`, `profile`, `schema_snapshot`, and `cross_job_assertion` jobs:

- Reconciliation jobs require SQL and key columns.
- BO report jobs require a BO document ID and BO report/page ID, plus optional output format.
- Automic jobs require either an Automic job name or run ID.
- dbt artifact jobs require a `run_results.json` path and can optionally include a `manifest.json` path for friendly node names.
- Freshness jobs require a SQL query and a timestamp column name; optionally set `max_age_hours` (default 24).
- Profile jobs require a SQL query; optionally name columns (blank = all) and set a `drift_threshold_pct` for drift alerts.
- Schema snapshot jobs require a SQL query and an environment label (`source`, `target`, or `both`).
- Cross-job assertion jobs require a source job, source metric, target job, target metric, and a tolerance value with type (`absolute` or `pct`).
- Validate Query is shown only for reconciliation jobs.

Default seed jobs are returned if the database has no saved jobs.

### Monitor

Use this tab to:

- Watch active runs.
- View run progress.
- See passed, failed, slow, and error counters.
- Track the current job where progress data is available.
- Receive live updates through `GET /api/runs/{run_id}/stream`; the browser falls back to 5-second polling if SSE is unavailable.

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
- **Audit sub-tab** — filter audit events by resource type and resource ID.
- **Profile sub-tab** — select a job that has run a `profile` job type to view the latest column statistics table (null rate, distinct count, min/max, mean, std dev, p25–p95). Click **Suggest DQ Rules** to auto-generate rule JSON from the observed distribution.
- **Schema sub-tab** — select a job and environment to browse all schema snapshots with per-snapshot diffs showing added, removed, and type-changed columns.

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
- Add an Automic job to the job catalog with either job-name or run-ID lookup semantics.
- **Import from File** — upload a `.json` array or `.csv` file of Automic job definitions to bulk-import them into the job catalog. CSV columns: `name, job_type, job_name, run_id, tags, description` (`job_type` defaults to `automic_job`).
- **Browse & Import from Automic** — enter a filter pattern (e.g. `ETL_*`) to search the Automic API for matching jobs, select multiple results, and import them to the catalog in one step. Uses `GET /api/adapters/automic/search` backed by `AutomicClient.search_jobs()`.

dbt artifacts:

- Create `dbt_artifact` jobs in the Launch job editor.
- Point the job at `target/run_results.json`; optionally include `target/manifest.json`.
- Execution converts dbt result statuses into normal run results and records failing/error nodes as mismatch details.

### Reports

Use this tab to:

- Select a run.
- View the themed HTML report.
- View an improved metrics dashboard.
- Search logs by text, level, and result limit.

### Compare

Use this tab for first-class comparison workflows.

BO Report mode, Reconciliation (dual-env) mode, and Recon File Compare mode — see the [Compare Tab](#compare-tab) section.

### Contracts

Use this tab to manage data contracts.

- **Contract list** on the left shows all active contracts with a live status badge: **OK**, **BREACHED**, or **OVERDUE**.
- Click a contract to open its detail panel:
  - **Header** — name, source job, owner, SLA hours, version, and consumers.
  - **Breach History** — full breach log with type, opened timestamp, resolved timestamp, duration, and escalation flag.
  - **Version Bump** — choose minor or major, add an optional note, and click Bump to increment the semantic version and record the history.
- **+ New Contract** button opens a modal to create a contract; select source job, set owner, SLA hours, and consumers.
- Editing a contract updates owner, SLA hours, consumers, and breach severity.
- Deleting soft-deletes the contract (sets `active = false`); breach history is retained.

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

## Data Contracts

Data Contracts are a governance layer that links a named contract to a source ETL job and enforces ownership, SLA, and breach notifications automatically.

### Breach lifecycle

```text
source job run FAILS
  → ContractBreachChecker opens a breach (idempotent)
  → webhook fires: contract.breached

source job run PASSES
  → ContractBreachChecker resolves all open breaches for that job
  → duration_hours computed; webhook fires: contract.resolved

breach remains open ≥ sla_hours
  → APScheduler (every 15 min) escalates the breach
  → webhook fires: contract.escalated
```

### Webhook event types

| Event | When |
|---|---|
| `contract.breached` | Source job fails and a breach is opened |
| `contract.resolved` | Source job passes and a breach is closed |
| `contract.escalated` | Breach open time exceeds the contract SLA |

Contracts reuse the existing webhook notification hooks. Add `contract.breached` (and/or the other two events) to any hook's event filter in **Notifications** to receive alerts.

### Contracts API quick reference

```powershell
$h = @{ Authorization = "Bearer etl_<token>" }

# List all contracts
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/contracts

# Create a contract
$body = @{
  name            = "orders_v1"
  source_job      = "orders_reconciliation"
  owner           = "data-platform@co.com"
  sla_hours       = 4.0
  consumers       = @("finance", "ops")
  breach_severity = "error"
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/contracts" -Body $body -ContentType "application/json" -Headers $h

# Get a contract
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/contracts/orders_v1

# Update a contract
$body = @{ owner = "new-team@co.com"; sla_hours = 6.0 } | ConvertTo-Json
Invoke-RestMethod -Method Put -Uri "http://127.0.0.1:8000/api/contracts/orders_v1" -Body $body -ContentType "application/json" -Headers $h

# Check contract status (OK / BREACHED / OVERDUE)
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/contracts/orders_v1/status

# List breach history
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/contracts/orders_v1/breaches

# Bump version (minor or major)
$body = @{ bump_type = "minor"; note = "added freshness rule" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/contracts/orders_v1/bump" -Body $body -ContentType "application/json" -Headers $h

# List version history
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/contracts/orders_v1/versions

# Derived: DQ rules from source job
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/contracts/orders_v1/rules

# Derived: latest schema snapshot from source job
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/contracts/orders_v1/schema?environment=source"

# Delete (soft)
Invoke-RestMethod -Method Delete -Headers $h http://127.0.0.1:8000/api/contracts/orders_v1
```

## API Usage

All API calls require `Authorization: Bearer <token>` except `/api/health`.

### Health

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

### Token Management

All token endpoints except bootstrap require an admin Bearer token.

```powershell
$h = @{ Authorization = "Bearer etl_<ADMIN_TOKEN>" }

# Bootstrap — no auth needed, first call only
$body = @{ name = "admin" } | ConvertTo-Json
$resp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/tokens" -Body $body -ContentType "application/json"
# $resp.raw_token — store this now, shown once only

# Create a standard user token (90-day expiry)
$body = @{ name = "alice"; is_admin = $false; expires_at = "2027-01-01T00:00:00Z" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/tokens" -Body $body -ContentType "application/json" -Headers $h

# List tokens (hints only — raw token never returned after creation)
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/tokens

# Update expiry or disable a token
$body = @{ expires_at = "2028-01-01T00:00:00Z" } | ConvertTo-Json
Invoke-RestMethod -Method Patch -Uri "http://127.0.0.1:8000/api/tokens/2" -Body $body -ContentType "application/json" -Headers $h

$body = @{ enabled = $false } | ConvertTo-Json
Invoke-RestMethod -Method Patch -Uri "http://127.0.0.1:8000/api/tokens/2" -Body $body -ContentType "application/json" -Headers $h

# Revoke a token permanently
Invoke-RestMethod -Method Delete -Headers $h "http://127.0.0.1:8000/api/tokens/2"

# Rotate — atomically revoke old token and issue replacement with same name/role/expiry
# Use during planned credential rotation to avoid any access gap
Invoke-RestMethod -Method Post -Headers $h "http://127.0.0.1:8000/api/tokens/2/rotate"
# Returns new raw_token — old token is immediately invalid

# Verify the active token
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/auth/verify
```

### List Jobs

```powershell
$h = @{ Authorization = "Bearer etl_<token>" }
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/jobs
```

### Import Jobs (Bulk)

Import multiple job definitions in one request. Existing jobs with the same name are updated (upsert).

```powershell
$jobs = @(
  @{ name = "etl_nightly"; job_type = "automic_job"; params = @{ job_name = "ETL_NIGHTLY_LOAD" }; query = ""; key_columns = @(); tags = @("automic", "nightly"); enabled = $true }
  @{ name = "etl_weekly";  job_type = "automic_job"; params = @{ job_name = "ETL_WEEKLY_LOAD"  }; query = ""; key_columns = @(); tags = @("automic", "weekly"); enabled = $true }
) | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/jobs/import" -Body $jobs -ContentType "application/json" -Headers $h
```

### Search Automic Jobs

Search the Automic API for jobs matching a filter pattern and return their names and statuses.

```powershell
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/adapters/automic/search?config_id=1&filter=ETL_*"
# Returns: [{ "name": "ETL_NIGHTLY_LOAD", "status": "ENDED_OK" }, ...]
```

### Bulk Import Automic Jobs

Import multiple Automic jobs from the live API into the job catalog in one request.

```powershell
$body = @{
  config_id = 1
  job_names = @("ETL_NIGHTLY_LOAD", "ETL_WEEKLY_LOAD", "ETL_MONTHLY_CLOSE")
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/adapters/jobs/from-automic/bulk" -Body $body -ContentType "application/json" -Headers $h
# Returns: { "imported": [...], "errors": {} }
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

Trend responses are cached for a short TTL and invalidated when matching result rows change.

### SSE Run Progress

```text
GET /api/runs/{run_id}/stream
```

Returns a `text/event-stream` response with `progress` events while the run is active and a final `done` event when it reaches a terminal status.

### Audit Events

```powershell
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/audit?resource_type=job&limit=100"
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/audit?resource_type=run&resource_id=<run_id>"
```

Each event includes `actor`, `action`, `resource_type`, `resource_id`, `diff`, and `created_at`.

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

### Column Profile

```powershell
# Latest profile for a job
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/jobs/payments_reconciliation/profile"

# Column metric history
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/jobs/payments_reconciliation/profile/history?column=amount"

# Auto-suggest DQ rules from the latest profile
Invoke-RestMethod -Method Post -Headers $h "http://127.0.0.1:8000/api/jobs/payments_reconciliation/suggest-rules"
```

### Schema Snapshot History

```powershell
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/jobs/payments_reconciliation/schema-history?environment=source"
```

Returns a list of snapshots, each with `columns` (`[{name, dtype}]`) and a `diff` showing `added`, `removed`, and `changed` column names since the previous snapshot.

### Create A Freshness Job

```powershell
$job = @{
  name = "payments_freshness"
  job_type = "freshness"
  query = "SELECT * FROM payments"
  params = @{ timestamp_column = "updated_at"; max_age_hours = 6 }
  key_columns = @(); tags = @("freshness"); enabled = $true
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/jobs" -Body $job -ContentType "application/json" -Headers $h
```

### Create A Profile Job

```powershell
$job = @{
  name = "payments_profile"
  job_type = "profile"
  query = "SELECT * FROM payments"
  params = @{ columns = @("amount", "status", "created_at"); drift_threshold_pct = 20 }
  key_columns = @(); tags = @("profile"); enabled = $true
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/jobs" -Body $job -ContentType "application/json" -Headers $h
```

### Create A Cross-Job Assertion

```powershell
$job = @{
  name = "orders_payments_count_check"
  job_type = "cross_job_assertion"
  params = @{
    source_job = "orders_reconciliation"
    source_metric = "count"
    target_job = "payments_reconciliation"
    target_metric = "count"
    tolerance = 5
    tolerance_type = "pct"
  }
  key_columns = @(); tags = @("assertion"); enabled = $true
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/jobs" -Body $job -ContentType "application/json" -Headers $h
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

## ETL Test Capabilities

The following DQ rule types are supported in job definitions:

| Rule Type | Parameters | Description |
|---|---|---|
| `not_null` | `column` | Fails if any value in the column is null |
| `unique` | `column` | Fails if the column has duplicate values |
| `row_count_min` | `min_value` | Fails if total row count is below threshold |
| `row_count_max` | `max_value` | Fails if total row count is above threshold |
| `row_count_between` | `min_value`, `max_value` | Fails if row count is outside the range |
| `column_mean_between` | `column`, `min_value`, `max_value` | Fails if column mean is outside the range |
| `match_regex` | `column`, `pattern` | Fails if any value does not match the pattern |
| `custom_sql` | `column`, `pattern` (SQL fragment) | Custom WHERE condition; fails if any row matches |
| `completeness_ratio` | `column`, `min_value` | Fails if non-null ratio is below threshold (0–1) |
| `distinct_count_between` | `column`, `min_value`, `max_value` | Fails if distinct count is outside the range |
| `column_sum_between` | `column`, `min_value`, `max_value` | Fails if column sum is outside the range |
| `column_std_dev_between` | `column`, `min_value`, `max_value` | Fails if standard deviation is outside the range |
| `column_percentile` | `column`, `percentile`, `min_value`, `max_value` | Fails if the Nth percentile value is outside the range |
| `column_type_check` | `column`, `expected_type` | Fails if any value does not parse as `int`/`float`/`date`/`uuid` |
| `column_value_between` | `column`, `min_value`, `max_value` | Fails if any value is outside the numeric range |
| `cross_column_consistency` | `column`, `column_b`, `operator` | Fails if `column <op> column_b` is not universally true |
| `pii_mask_check` | `column`, `pattern` | Fails if any value matches the pattern (detects unmasked PII) |
| `no_whitespace` | `column` | Fails if any value has leading or trailing whitespace |
| `referential_check` | `column`, `lookup_query` | Fails if any value is not in the lookup query result set (DB engine required) |
| `custom_sql_assert` | `column`, `operator`, `min_value` | Runs a custom SQL query; fails if result is not a single `(1, 1)` true assertion |

The following special job types are supported in addition to the standard `reconciliation` type:

| Job Type | Required Params | Description |
|---|---|---|
| `freshness` | `timestamp_column`, `max_age_hours` | Checks that the most recent timestamp in the column is within `max_age_hours` of the run time |
| `profile` | (optional) `columns`, `drift_threshold_pct` | Computes column statistics and compares with the previous profile run to detect drift |
| `schema_snapshot` | `environment` | Captures column names and types; diffs against the previous snapshot |
| `cross_job_assertion` | `source_job`, `source_metric`, `target_job`, `target_metric`, `tolerance`, `tolerance_type` | Asserts that a metric from one job matches another within a tolerance |

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

Run property-based tests (requires `hypothesis`):

```powershell
python -m pytest tests/property/ -q
```

Run focused tests:

```powershell
python -m pytest tests/unit/test_compare_api.py -q
python -m pytest tests/unit/test_dq_engine.py -q
python -m pytest tests/unit/test_dag_retry_trends.py -q
python -m pytest tests/unit/test_p2_features.py -q
python -m pytest tests/unit/test_lineage.py -q
python -m pytest tests/unit/test_audit.py -q
python -m pytest tests/unit/test_dbt_artifact_parser.py -q
python -m pytest tests/unit/test_api.py -q
python -m pytest tests/unit/test_new_schemas.py -q
python -m pytest tests/unit/test_runs_extensions.py -q
python -m pytest tests/integration/test_api_frontend_smoke.py -q
```

Data Contracts test suite:

```powershell
# Unit: ContractRepository CRUD + breach lifecycle (20 tests)
python -m pytest tests/unit/test_contracts.py -q

# Unit: contract webhook event types (4 tests, extends test_notifier.py)
python -m pytest tests/unit/test_notifier.py -q

# Unit: ContractBreachChecker + RunExecutor wiring (3 tests, extends test_run_executor.py)
python -m pytest tests/unit/test_run_executor.py -q

# Integration: CRUD lifecycle, breach lifecycle, version bump (3 tests)
python -m pytest tests/integration/test_contracts_integration.py -q

# Property-based: breach math invariants via Hypothesis (5 tests)
python -m pytest tests/property/test_contracts_property.py -q
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

- No token created yet, token revoked, or token expired.
- Open the Security section in the Config tab, create a new token, and paste it into **Use existing token**.
- Tokens are stored in `sessionStorage` — re-entering is required after closing the browser tab.
- For API clients, pass `Authorization: Bearer etl_<token>` on every request (except `/api/health`).
- See [docs/auth.md](docs/auth.md) for bootstrap and token management reference.

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

### UI Does Not Load (On-Premises / Air-Gap Networks)

The API works but the browser shows a blank or unstyled page when deployed behind a corporate firewall that blocks internet access. This happens when an older deployment still references CDN URLs.

Check your `frontend/index.html` — the `<head>` should contain only local paths:

```html
<link rel="stylesheet" href="vendor/tailwind.css" />
<script defer src="vendor/alpine.min.js"></script>
<script src="vendor/chart.umd.min.js"></script>
<link rel="stylesheet" href="styles.css" />
```

If it still has `https://cdn.tailwindcss.com`, `cdn.jsdelivr.net`, or `fonts.googleapis.com` references, pull the latest code — the vendor files have been committed and CDN references removed. See [On-Premises Deployment](#on-premises-deployment) for the full checklist.

### Static UI Not Updating

Hard-refresh the browser or open dev tools and disable cache.

## Minimal First Run

1. Complete [bootstrap](#authentication) to create and activate a token.
2. Open **Launch** — keep `use_live_connections` off for a local simulation run.
3. Select one or more seed jobs and click **Run Tests**.
4. Open **Monitor** to watch progress.
5. Open **History** for per-test results and mismatch details.
6. Open **Reports** for the HTML report, metrics dashboard, and searchable logs.
7. Open **Compare** to diff two runs or compare BO report sources.
