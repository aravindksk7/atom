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

## CI/CD Job Status

<!-- ATOM:JOB-STATUS:START -->
_No CI-triggered run yet. Open a Job Selection's **CI/CD** button in the Launch tab for setup instructions and a ready-to-copy `.gitlab-ci.yml` snippet._
<!-- ATOM:JOB-STATUS:END -->

## Contents

- [CI/CD Job Status](#cicd-job-status)
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
  - [Job Launcher — Step-By-Step](#job-launcher--step-by-step)
  - [Creating And Managing Jobs](#creating-and-managing-jobs)
  - [Job Types Reference](#job-types-reference)
  - [Run Settings Reference](#run-settings-reference)
- [Reports, Metrics, And Logs](#reports-metrics-and-logs)
  - [Global Logs Tab](#global-logs-tab)
- [Compare Tab](#compare-tab)
  - [BO Report Compare](#bo-report-compare)
  - [Reconciliation Dual-Environment Compare](#reconciliation-dual-environment-compare)
  - [Recon File Compare](#recon-file-compare)
- [Data Contracts](#data-contracts)
- [Write-Audit-Publish Gate](#write-audit-publish-gate)
- [Rules-As-Code & Schema Compatibility](#rules-as-code--schema-compatibility)
- [API Usage](#api-usage)
- [ETL Test Capabilities](#etl-test-capabilities)
- [Testing](#testing)
  - [Isolated Transform Testing (TransformCase)](#isolated-transform-testing-transformcase)
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
- Define **multiple named DB connections within a single saved config** (e.g. `hr_db`, `finance_db` sharing one environment's BO/Automic settings) and pick which one a run or SQL compare uses at launch time.
- Define **named REST API endpoints within a saved config** (auth: none/API key/bearer/basic; JSON with dot-path record extraction or CSV; cursor or page/limit pagination) and use them as a job source/target or as either side of a Compare-tab comparison — see [API Endpoints (REST API Data Sources)](#api-endpoints-rest-api-data-sources).
- **`api_reconciliation`** job type — reconcile two REST API endpoints against each other the same way `reconciliation` reconciles two SQL queries, with the same key columns, DQ rules, and pass-condition support.
- Browse generated HTML reports, metrics dashboards, and searchable logs in a dark-themed UI.
- Use SAP BO and Automic adapters from the UI and API.
- Use the REST API directly with OpenAPI documentation at `/docs`.
- Manage **API tokens** (Bearer token auth on all `/api/*` routes).
- Configure **webhook notifications** for run events (`run.failed`, `run.passed`, etc.) with optional HMAC-SHA256 signing.
- Schedule recurring runs with **APScheduler cron expressions**.
- View the **job lineage DAG** (job → job dependency graph) in the History tab.
- **Audit log** — every create, update, delete, and mismatch-accept action is recorded with actor, action, resource type, resource ID, and a JSON diff. Queryable via `GET /api/audit`.
- **SSE run streaming** — subscribe to live progress events with `GET /api/runs/{run_id}/stream`; the Monitor tab uses Server-Sent Events with automatic fallback to 5-second polling.
- **Run cancellation** — send `POST /api/runs/{run_id}/cancel` at any time to cooperatively stop an active ETL run. The executor finishes its current job step, cancels all remaining steps, and sets the run to `CANCELLED`. Safe to call on already-terminal runs (returns `cancel_requested: false`).
- **Pytest suite runner** — trigger the project's pytest suite as a tracked run with `POST /api/runs/test-suite`. Progress (collected count, passed/failed/error counters) streams via the same SSE endpoint; the run appears in History with `run_type=test_suite`. Supports the cancel endpoint to terminate a running test process.
- **Cooperative Cancellation**: A `cancel_requested` flag is added to the `TestRun` model, acting as a shared signal for cancellation. The `RunExecutor` will check this flag between steps.
- **Trend caching** — trend responses are memoised in-process with a short TTL; the cache is invalidated automatically when matching result rows change.
- **dbt artifact adapter** — `dbt_artifact` job type parses `run_results.json` (and optionally `manifest.json`) and maps dbt test statuses to normal run results, with failing/error nodes recorded as mismatch details.
- **Freshness checks** — `freshness` job type queries a timestamp column and fails if the most recent record is older than a configurable `max_age_hours` threshold.
- **Column profiling** — `profile` job type computes per-column statistics (null rate, distinct count, min/max, mean, std dev, p25/p50/p75/p95) and optionally detects metric drift against the previous profile run.
- **Schema snapshots** — `schema_snapshot` job type captures the column names and types for a query result and diffs them against the previous snapshot, flagging added, removed, or type-changed columns.
- **Cross-job assertions** — `cross_job_assertion` job type compares a metric (e.g. row count or distinct count) from one job against another job's metric within a configurable absolute or percentage tolerance.
- **Profile API** — `GET /api/jobs/{job}/profile` returns the latest column profile; `GET /api/jobs/{job}/profile/history?column=<col>` returns the metric history for a column; `POST /api/jobs/{job}/suggest-rules` auto-generates DQ rules from the latest profile.
- **Schema history API** — `GET /api/jobs/{job}/schema-history?environment=source` returns all snapshots with per-snapshot diffs.
- **Profile and Schema sub-tabs** — the History tab includes Profile and Schema sub-tabs for browsing stored statistics and snapshot diffs directly in the UI.
- **Coverage matrix** — `GET /api/coverage` maps every table/column seen by the framework to the jobs and DQ rules covering it, with `tested` / `observed` / `untested` levels and a gap filter in the History → Coverage sub-tab.
- **Flaky-test detection** — `GET /api/coverage/flaky?window=20` scores each job by pass/fail flip-flops across recent runs (transitions ÷ window); scores ≥ 0.3 are flagged.
- **Mismatch segment drill-down** — configure `params.segment_columns` on a reconciliation job (or let the framework auto-pick low-cardinality columns from the latest profile) and each failed result stores a per-segment mismatch summary; `POST /api/runs/{run_id}/results/{result_id}/drilldown` re-queries live per-segment row counts on both sides.
- **Data Contracts** — define named contracts that point at a source job and enforce ownership, SLA, and data quality expectations:
  - Contracts are stored in `/api/contracts` as first-class entities with `name`, `source_job`, `owner`, `sla_hours`, `consumers`, and `breach_severity`.
  - When a source job run **fails**, a breach opens automatically and a `contract.breached` webhook fires to configured endpoints.
  - When the source job **passes**, open breaches auto-resolve with `duration_hours` computed and a `contract.resolved` webhook fires.
  - Breaches that remain open past `sla_hours` are **escalated** by a background APScheduler job (every 15 minutes) and a `contract.escalated` webhook fires.
  - Contracts carry a semantic **version** (`1.0` by default); bump minor or major with `POST /api/contracts/{name}/bump`.
  - The **Contracts tab** in the UI lists all contracts with live OK / BREACHED / OVERDUE status badges, breach history, and inline version bump.
  - Derived endpoints expose the source job's DQ rules (`/rules`) and latest schema snapshot (`/schema`) without duplicating configuration.
- **Write-Audit-Publish gate** — `POST /api/gates/{job}/evaluate` returns a machine-readable `PROMOTE`/`HOLD` verdict (latest result status + open contract breaches) so orchestrators can gate a staging→production swap on data quality.
- **CI gate exit codes** — `python -m etl_framework.runner.cli --gate-run <run_id>` queries the run's status without needing `--config`/`--source-env`/`--target-env`, returning exit code `0` (passed), `1` (failed), `2` (cancelled), `3` (error), or `4` (not found) so CI pipelines can gate on the result.
- **Shadow run profile** — set `run_settings.run_profile` to `"shadow"` (default `"full"`) to reconcile a `shadow_sample_frac` sample (default `0.02`) of rows instead of the full dataset, wrapping the comparison backend in `SamplingBackend` for cheap, fast per-PR checks.
- **Rules-as-code** — export job DQ rules to versioned YAML suites in `expectations/`, review them in PRs, and sync them back with `POST /api/expectations/sync`. Schema snapshot diffs now include a `compatibility` verdict (`full` / `non_breaking` / `risky` / `breaking`).

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
  api/routes/runs.py          — runs, SSE stream, trends, badges, baseline, mismatch-distribution, cancel, test-suite
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
  api/services/run_executor.py          — retry, DAG resolution, DQ evaluation, all job types; cooperative cancel check after each step
  api/services/pytest_runner.py         — spawn pytest subprocess, parse output, stream progress, support cancel
  api/services/contract_breach_checker.py — post-run hook: open/resolve contract breaches
  api/services/run_cancel.py            - NEW: handles the logic for run cancellation.
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
- `bo_auth_type` — the SAP BO CMS security plugin to authenticate against: `secEnterprise` (default), `secWinAD`, `secLDAP`, or `secSAPR3`. On-premises SAP BO deployments that authenticate users against Active Directory must set this to `secWinAD` — logging on with `secEnterprise` against an AD-only account returns HTTP 401 ("Authentication failed") even with correct credentials.
- `bo_timeout`
- `automic_url`
- `automic_user`
- `automic_password`

### Config Overlays & Secret Providers (standalone CLI)

The **standalone CLI runner** (`python -m etl_framework.runner.cli --config <file.yml> --source-env dev --target-env prod`) reads its own YAML file — separate from the web app's DB-backed configs above — via `etl_framework.config.loader.ConfigLoader`. That file supports two things worth knowing about:

**1. A `base` overlay** — shared settings merged under every named environment (per-environment keys win on conflict). Keeps multi-environment files from repeating themselves:

```yaml
environments:
  base:                     # not itself an environment — merged into every one below
    db_port: 1433
    db_driver: "ODBC Driver 17 for SQL Server"
    db_password: secret://env/DB_PASSWORD

  dev:
    db_host: dev-sql.internal
    db_name: dev_db

  qa:
    db_host: qa-sql.internal
    db_name: qa_db

  prod:
    db_host: prod-sql.internal
    db_name: prod_db
    db_password: secret://env/PROD_DB_PASSWORD   # overrides base for this env only
```

**2. `secret://<provider>/<name>` values** — resolved at load time instead of being read as a literal string. The built-in `env` provider reads an environment variable (`secret://env/DB_PASSWORD` → `os.environ["DB_PASSWORD"]`); a missing variable raises a clear `ValueError` rather than silently passing through an empty password. Register a custom provider (Vault, Azure Key Vault, ...) once at process startup:

```python
from etl_framework.config.secrets import register_provider

class VaultSecretProvider:
    def get(self, name: str) -> str:
        ...  # fetch `name` from your secret store

register_provider("vault", VaultSecretProvider())
# now usable in YAML as: db_password: secret://vault/prod-db-password
```

`${ENV_VAR}` substitution (the older mechanism) still works unchanged for values that aren't `secret://` URIs.

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

### Named Connections (Multiple DB Connections Per Environment)

A single saved config can define more than one database connection under a `connections` map, so one environment entry (with one set of BO/Automic settings) can target several databases — e.g. `hr_db` and `finance_db` inside the same `prod` config.

```json
{
  "name": "prod-main",
  "env_name": "prod",
  "config_data": {
    "db_host": "sql-prod.internal",
    "db_name": "default_db",
    "db_user": "etl_user",
    "db_password": "secret",
    "connections": {
      "hr_db": { "db_host": "hr-sql-prod.internal", "db_name": "HR" },
      "finance_db": { "db_host": "fin-sql-prod.internal", "db_name": "FIN" }
    }
  }
}
```

**How it resolves:**

- Each entry under `connections` may override any subset of `db_host`, `db_port`, `db_name`, `db_user`, `db_password`, `db_driver`, `db_pool_size`, `db_pool_overflow`, `db_pool_timeout`, `db_pool_recycle`, `db_connect_timeout`. Fields left out fall back to the top-level config values.
- The merged result is validated as a strict `EnvironmentConfig` — if a required field (e.g. `db_host`, `db_password`) is missing after merging, the request fails with `422` and field-level detail.
- Picking no named connection behaves exactly as before — the run or compare uses the top-level config values directly.
- An unknown connection name is rejected with `422` at request time, before any query runs.

**Where it's used:**

- **Run trigger** (`POST /api/runs`) — `source_connection` / `target_connection` select a named connection from the config used for `source_env` / `target_env` respectively.
- **SQL Direct Compare** (`POST /api/compare/sql`) — `connection_a` / `connection_b` select a named connection from `config_id_a` / `config_id_b` respectively.
- **Web UI** — the Config modal has a **Named Connections** section for adding, editing, and removing connections on a saved config. The Launch tab's Run Settings panel and the Compare tab's SQL Direct Compare cards show a connection picker dropdown whenever the selected config has named connections.
- Secrets in `connections` entries are masked the same way as top-level config fields when a config is read back via the API or UI (`db_password`, etc. are never returned in plaintext after creation).

### API Endpoints (REST API Data Sources)

A saved config can also define named REST API endpoints under an `api_endpoints` map, so a config with DB/BO/Automic settings can also point at one or more HTTP APIs — e.g. an internal microservice or partner API you want to reconcile against a database table.

```json
{
  "name": "prod-main",
  "env_name": "prod",
  "config_data": {
    "api_endpoints": {
      "orders_api": {
        "base_url": "https://api.example.com/v1/orders",
        "method": "GET",
        "auth_type": "bearer",
        "bearer_token": "secret-token",
        "headers": { "Accept": "application/json" },
        "response_format": "json",
        "json_root_path": "data.items",
        "pagination_type": "cursor",
        "pagination_cursor_path": "meta.next_cursor",
        "pagination_cursor_param": "cursor",
        "pagination_max_pages": 50
      }
    }
  }
}
```

**Ingesting multiple endpoints on the same host:** set a top-level `api_base_host` on the config and give each endpoint a `path` instead of repeating the full `base_url`:

```json
{
  "config_data": {
    "api_base_host": "https://api.example.com/v1",
    "api_endpoints": {
      "orders_api":    { "path": "orders",    "auth_type": "bearer", "bearer_token": "secret-token" },
      "customers_api": { "path": "customers", "auth_type": "bearer", "bearer_token": "secret-token" },
      "invoices_api":  { "path": "/invoices",  "auth_type": "bearer", "bearer_token": "secret-token" }
    }
  }
}
```

`api_base_host` and `path` are joined (`/` normalized either way). An endpoint that sets its own `base_url` ignores `api_base_host`/`path` entirely (explicit `base_url` always wins) — handy for the rare endpoint that lives on a different host. Auth, headers, and pagination settings are still per-endpoint; only the host is shared.

**Fields per endpoint:**

| Field | Default | Description |
|---|---|---|
| `base_url` | `""` | Full URL, must include `http://` or `https://` if set. Required unless `path` + config-level `api_base_host` are both set |
| `path` | `""` | Path appended to the config's top-level `api_base_host` when `base_url` is not set; ignored if `base_url` is set |
| `method` | `GET` | `GET` or `POST` |
| `auth_type` | `none` | `none`, `api_key`, `bearer`, or `basic` |
| `api_key_header` / `api_key` | `X-API-Key` / `""` | Header name and value when `auth_type = api_key` |
| `bearer_token` | `""` | Sent as `Authorization: Bearer <token>` when `auth_type = bearer` |
| `basic_username` / `basic_password` | `""` / `""` | HTTP Basic credentials when `auth_type = basic` |
| `headers` / `query_params` | `{}` / `{}` | Extra headers / query string parameters sent on every request |
| `body` | `null` | JSON body sent when `method = POST` |
| `timeout` | `30` | Per-request timeout in seconds |
| `verify_ssl` | `true` | Set `false` to skip TLS certificate verification (trusted internal endpoints only) |
| `response_format` | `json` | `json` or `csv` |
| `json_root_path` | `""` | Dot-path to the array of records inside a JSON response (e.g. `data.items`); empty means the response body itself is the array |
| `pagination_type` | `none` | `none`, `cursor`, or `page` |
| `pagination_cursor_path` / `pagination_cursor_param` | `""` / `cursor` | Dot-path to the next-page cursor/URL in the response, and the query param used to send it back (ignored if the cursor value is itself a full URL — it's followed directly) |
| `pagination_page_param` / `pagination_size_param` / `pagination_page_size` | `page` / `limit` / `100` | Query param names and page size for page/limit-style pagination |
| `pagination_max_pages` | `50` | Safety cap on the number of pages fetched (1–1000) |

**Where it's used:**

- **Compare tab** — both **BO Report Compare** and **Column Stats Compare** accept `source_type: "api"` for either side, referencing `config_id` + `api_endpoint_name`. See [Compare Tab](#compare-tab).
- **Jobs** — the `api_reconciliation` job type reconciles `source_api_endpoint` against `target_api_endpoint`. See [Job Types Reference](#job-types-reference).
- **Adapters tab / API** — `POST /api/adapters/rest-api/test` checks connectivity (one page only); `POST /api/adapters/rest-api/preview` fetches a sample of rows to confirm parsing before wiring it into a job or comparison.
- **Web UI** — the Config modal has an **API Endpoints** section (parallel to Named Connections) for adding, editing, testing, and previewing endpoints on a saved config.
- Secrets (`api_key`, `bearer_token`, `basic_password`) are masked the same way as other config secrets when read back via the API or UI.

### YAML Import

Configs can also be imported as YAML through `/api/configs/import-yaml`. The YAML should describe one or more named environments. Keep secrets out of source control.

### Run Settings

Every run accepts a `run_settings` block. See [Run Settings Reference](#run-settings-reference) for the full table of options and their defaults.

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

There is no committed application Dockerfile in this repository at the time of writing. A minimal container deployment should:

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
- **Named Connections** — add, expand/collapse, and remove multiple named DB connections within a single config (e.g. `hr_db`, `finance_db`), each overriding only the DB fields it needs while sharing the config's BO/Automic settings. See [Named Connections](#named-connections-multiple-db-connections-per-environment).
- Validate config values.
- Store SAP BO and Automic credentials for adapter workflows.
- **Import YAML** — expand the "Import YAML" card, paste a YAML block defining one or more named environments, and click Import to create all configs in one step.
- **Security** — create and manage API tokens. Created tokens are stored in `sessionStorage` (cleared when the browser tab closes).
- **Notifications** — add webhook endpoints with event filters and optional HMAC-SHA256 secret signing.

### Launch

Use this tab to configure and start ETL test runs, manage the job catalog, and set up recurring schedules.

---

### Job Launcher — Step-By-Step

**1. Set environment labels**

Enter `Source Env` and `Target Env` text labels (e.g. `dev` and `prod`). These labels are stored with each run and appear in History and reports. They are arbitrary strings — they identify the pair, not live connection credentials.

**2. Select a saved config**

Choose a saved config from the dropdown. A config bundles all connection details (SQL Server host, SAP BO URL, Automic URL, and credentials). If no config is selected the run uses simulation data (safe for development). To use live connections, select a config **and** enable `Use Live Connections` in Run Settings.

If the selected config has [named connections](#named-connections-multiple-db-connections-per-environment), a **Source Connection** / **Target Connection** picker appears so you can target a specific named DB connection instead of the config's top-level defaults. Leaving both unset uses the top-level config values.

**3. Configure Run Settings**

Expand the **Run Settings** panel to tune execution behaviour before starting a run. See the [Run Settings Reference](#run-settings-reference) section for all options.

**4. Select jobs**

The **Job Catalog** card lists all saved jobs (or seed jobs if the database is empty). Check the box next to each job you want to include. You can also reorder jobs in the execution sequence by dragging rows or using up/down controls.

- Only `enabled` jobs appear in the catalog by default.
- Use the tag filter to narrow the list by tag (e.g. `daily`, `payments`).

**5. (Optional) Check a job's promotion gate**

Click the **Gate** button on any job row to call `POST /api/gates/{job}/evaluate` and see whether that job is currently safe to promote. A `PROMOTE`/`HOLD` badge appears next to the button (also shown as a toast). See [Write-Audit-Publish Gate](#write-audit-publish-gate) for what drives the verdict and how to call it from an orchestrator.

**6. Start the run**

Click **Run Tests**. The page switches to the **Monitor** tab automatically and streams live progress via Server-Sent Events. When complete, results appear in **History**.

**Schedules sub-tab**

Create, edit, enable, disable, and manually trigger cron-scheduled runs without opening the Launch form each time. Each schedule stores the full run configuration (env labels, config, job list, run settings) and fires at the configured interval.

| Field | Description |
|---|---|
| Name | Human-readable label for the schedule |
| Cron expression | Standard 5-field cron (e.g. `0 6 * * 1-5` = weekdays at 06:00) |
| Source / Target Env | Environment pair labels used for every triggered run |
| Config | Saved config to use |
| Job names | Comma-separated or JSON list of jobs |
| Run Settings | Full run settings block |
| Enabled | Toggle without deleting the schedule |
| Run Now | Trigger immediately outside the normal schedule |

---

### Creating And Managing Jobs

All job management is available from the **Job Catalog** card in the Launch tab or via the REST API.

#### Create a new job (UI)

1. Click **+ New Job** in the Job Catalog card.
2. Enter a **Name** (unique, required).
3. Enter an optional **Description** and comma-separated **Tags**.
4. Select a **Job Type** — see [Job Types Reference](#job-types-reference) for full details on each type and its required settings.
5. Fill in the type-specific fields that appear (SQL query or file paths, key columns, BO report IDs, etc.).
6. Optionally set **Depends On** — enter one or more job names whose results must be available before this job can run. The run executor resolves the execution order with a topological sort; jobs with failed upstreams are skipped automatically.
7. Optionally add **DQ Rules** — click **+ Add Rule**, pick a rule type, and fill in the parameters. Multiple rules can be stacked on a single job.
8. Optionally set a **Pass Condition** — override whether the job is considered passed based on row counts, mismatch thresholds, or a custom SQL assertion (see `PassCondition` fields below).
9. For reconciliation jobs, click **Validate Query** to run a dry-run EXPLAIN against both the source and target databases before saving.
10. Click **Preview** to run the query for real against a selected config's database and see a live sample of rows (`POST /api/configs/{config_id}/preview-query`, capped at 200 rows). Pick which saved config to preview against from the dropdown next to the query field — this can be any saved config, not just the one the job will ultimately run under, so you can sanity-check a query against a different environment (e.g. a smaller dev DB) before wiring it into the job. Requires a valid API token; unlike Validate Query this executes the SQL, so avoid previewing destructive or expensive queries.
11. Click **Save**.

#### Edit or delete a job

Click the pencil icon on any catalog row to open the edit form. Click the trash icon to delete. Deletion is permanent; the job will not appear in future runs but historical run results are retained.

#### Bulk import jobs (API)

POST a JSON array to `/api/jobs/import`. Existing jobs with the same name are updated (upsert).

```powershell
$h = @{ Authorization = "Bearer etl_<token>" }
$jobs = @(
  @{ name = "sales_recon"; job_type = "reconciliation"; query = "SELECT * FROM sales"; key_columns = @("id") }
  @{ name = "nightly_load"; job_type = "automic_job"; params = @{ job_name = "ETL_NIGHTLY_LOAD" } }
) | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/jobs/import" -Body $jobs -ContentType "application/json" -Headers $h
```

#### Pass Condition fields

A `pass_condition` block lets you define custom pass/fail logic that runs after reconciliation. All fields are optional; only the ones you supply are checked.

| Field | Type | Description |
|---|---|---|
| `min_row_count` | int | Fail if source row count is below this threshold |
| `max_row_count` | int | Fail if source row count is above this threshold |
| `max_value_mismatches` | int | Fail if value mismatch count exceeds this |
| `max_missing_in_target` | int | Fail if missing-in-target count exceeds this |
| `max_missing_in_source` | int | Fail if missing-in-source count exceeds this |
| `require_status` | list[str] | Fail if the reconciliation status is not in this list (e.g. `["PASSED"]`) |
| `pass_sql` | str | Custom SQL query; evaluated after reconciliation |
| `pass_sql_mode` | str | `rows_mean_pass` (rows returned = pass) or `rows_mean_fail` (rows returned = fail) |

#### Sequence steps (advanced)

When triggering a run via the API you can specify `job_sequence` as a list of structured `SequenceStep` objects instead of plain job names. This enables step-level gating and controlled pauses.

| Field | Default | Description |
|---|---|---|
| `job_name` | required | Name of the job to run at this step |
| `hold_after` | `false` | Pause execution after this step until released manually via the Monitor tab |
| `condition` | `null` | Gate the **next** step on this step's result (see condition fields below) |
| `wait_seconds` | `0` | Sleep this many seconds before running this step |

**Condition fields** (`StepCondition`):

| Field | Description |
|---|---|
| `require_status` | Proceed only if the previous job had one of these statuses (default `["PASSED"]`) |
| `max_mismatch_count` | Cancel remaining steps if total mismatches exceed this |
| `min_row_count` | Cancel if source row count is below this |
| `max_row_count` | Cancel if source row count exceeds this |
| `max_value_mismatches` | Cancel if value-mismatch count exceeds this |
| `max_missing_in_target` | Cancel if missing-in-target count exceeds this |
| `max_missing_in_source` | Cancel if missing-in-source count exceeds this |

---

### Job Types Reference

The framework supports nine job types. Each type has its own required parameters and behaviour. Choose the job type that matches what you want to test or monitor.

#### `reconciliation` (default)

Compares two SQL queries (one per environment) or two tabular files row-by-row. This is the most common job type for ETL validation.

**Required fields:**

| Field | Description |
|---|---|
| `query` | SQL query executed against both source and target environments when `params.source_mode` is not `files` |
| `key_columns` | One or more columns that uniquely identify a row (used to join the two result sets) |

**Optional fields:**

| Field | Default | Description |
|---|---|---|
| `params.source_mode` | `sql` | Set to `files` to load source/target data from server-side files |
| `params.source_file_path` / `params.target_file_path` | — | CSV/XLSX/XLS/JSON/XML/TSV file paths scoped to `SERVER_FILE_ALLOWED_DIRS` or `UPLOAD_BASE_DIR` |
| `params.source_file_label` / `params.target_file_label` | source/target env | Display labels for file-backed runs |
| `exclude_columns` | `[]` | Columns to ignore during comparison (e.g. `last_updated`, audit timestamps) |
| `rules` | `[]` | DQ rules evaluated against the source result set |
| `depends_on` | `[]` | Job names that must complete (and pass) before this job runs |
| `pass_condition` | `null` | Custom threshold overrides for pass/fail logic |

**How it works:**

1. The query is run against the source and target databases, or file paths are read when `params.source_mode` is `files`.
2. Rows are matched on `key_columns`.
3. Value differences, rows missing from target, and rows missing from source are recorded as mismatch details.
4. DQ rules (if configured) are applied to the source result set and any violations are added as additional mismatches.
5. Status is `PASSED` if there are zero issues (or if `pass_condition` thresholds are all met), otherwise `FAILED`.

Use `Validate Query` in the editor to run a dry-run EXPLAIN before saving; this checks SQL syntax against both environments without fetching any data.

Example file-backed job:

```json
{
  "name": "orders_file_recon",
  "job_type": "reconciliation",
  "key_columns": ["order_id"],
  "params": {
    "source_mode": "files",
    "source_file_path": "C:\\temp\\orders_source.csv",
    "target_file_path": "C:\\temp\\orders_target.csv"
  }
}
```

---

#### `bo_report`

Monitors a SAP BusinessObjects WebIntelligence report execution status. When `use_live_connections` is enabled, the executor authenticates to the BO REST API and checks the report/document state.

**Required params:**

| Param | Description |
|---|---|
| `report_id` | The BO report or page ID within the document |

**Optional params:**

| Param | Default | Description |
|---|---|---|
| `doc_id` | — | BO document (WebI) ID; overrides any document-level config |
| `format` | `xlsx` | Output format for download: `csv`, `xlsx`, or `xls` |
| `mode` | `api` | `api` = live BO REST call; any other value uses simulation |

**How it works (live mode):**

1. The executor authenticates with the BO RESTful Web Services API at the URL in the saved config (`bo_url`, `bo_user`, `bo_password`).
2. It fetches the report data for the given document and report ID.
3. The report data is converted to a DataFrame and compared against the target environment's equivalent report (or simulation data in offline mode).
4. Mismatches are recorded normally.

**Adding BO jobs from the Adapters tab:**

The **Adapters → SAP BO** section lets you browse documents, expand report tabs, and click **Add to Catalog** to create a `bo_report` job without manually entering IDs.

---

#### `automic_job`

Checks the execution status of an Automic (UC4) job or workflow run. Used to verify that upstream ETL processes completed successfully before running reconciliation.

**Required params (one of):**

| Param | Description |
|---|---|
| `job_name` | Automic job name (e.g. `ETL_NIGHTLY_LOAD`); the executor looks up the most recent run |
| `run_id` | Specific Automic run ID; used when you want to pin the check to a particular execution |

**Optional params:**

| Param | Default | Description |
|---|---|---|
| `run_id` | — | Use alongside `job_name` to check a specific run rather than the latest |

**How it works:**

1. The executor queries the Automic REST API (configured via `automic_url`, `automic_user`, `automic_password` in the saved config).
2. If `job_name` is provided, it fetches the most recent run for that job name.
3. If `run_id` is provided, it fetches that specific run.
4. The job status (`ENDED_OK`, `ENDED_NOT_OK`, `RUNNING`, etc.) is mapped to `PASSED`/`FAILED`.
5. In simulation mode (no live connection), the job returns a simulated PASSED result.

**Bulk import from the Adapters tab:**

- **Import from File** — upload a `.json` array or `.csv` with columns `name, job_type, job_name, run_id, tags, description`.
- **Browse & Import from Automic** — enter a filter pattern (e.g. `ETL_*`) to search for matching jobs and import them in bulk.

---

#### `dbt_artifact`

Parses dbt's `run_results.json` (and optionally `manifest.json`) and maps dbt test outcomes to standard run results. Use this to bring dbt test failures into the same history and mismatch tracking workflow as your other ETL jobs.

**Required params:**

| Param | Description |
|---|---|
| `run_results_path` | Absolute or relative path to dbt's `target/run_results.json` |

**Optional params:**

| Param | Description |
|---|---|
| `manifest_json_path` | Path to `target/manifest.json`; when provided, node names are resolved to friendly model names |

**How it works:**

1. The executor reads `run_results.json` and maps each node's status (`pass`, `fail`, `error`, `skip`) to the framework's status values.
2. Failing or erroring nodes are recorded as mismatch details with the node name and error message.
3. If `manifest.json` is also provided, the unique node ID is resolved to the human-readable model name.
4. The overall job status is `PASSED` only if all nodes passed or were skipped.

---

#### `freshness`

Checks that the most recent timestamp in a table column is within an acceptable age. Use this to verify that source data is being loaded on schedule.

**Required params:**

| Param | Description |
|---|---|
| `timestamp_column` | Name of the column containing the most recent load timestamp |

**Optional params:**

| Param | Default | Description |
|---|---|---|
| `max_age_hours` | `24` | Maximum acceptable age of the most recent record in hours |

**Required fields:**

| Field | Description |
|---|---|
| `query` | SQL query that returns the table to check (the executor finds the MAX of `timestamp_column`) |

**How it works:**

1. The query is run against the source environment.
2. The MAX value of `timestamp_column` is extracted.
3. If the most recent record is older than `max_age_hours` from the run time, the job fails.
4. The result is stored as a standard test result; no row-level mismatches are produced.

---

#### `profile`

Computes per-column statistics (null rate, distinct count, min, max, mean, std dev, p25/p50/p75/p95) for a query result and optionally detects drift against the previous profile run.

**Required fields:**

| Field | Description |
|---|---|
| `query` | SQL query whose result set is profiled |

**Optional params:**

| Param | Default | Description |
|---|---|---|
| `columns` | all | List of column names to profile; leave empty to profile all columns |
| `drift_threshold_pct` | none | If set, drift is flagged when a numeric metric changes by more than this percentage vs the previous run |

**How it works:**

1. The query is run against the source environment.
2. Per-column statistics are computed and stored in `column_profiles`.
3. If a previous profile exists for this job, drift is detected column-by-column.
4. Drift violations are recorded as mismatch details; overall status is `FAILED` if any metric drifts beyond the threshold.

**Profile API:**

```powershell
# Latest profile statistics
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/jobs/my_profile_job/profile"

# History for a specific column
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/jobs/my_profile_job/profile/history?column=amount"

# Auto-suggest DQ rules based on observed distribution
Invoke-RestMethod -Method Post -Headers $h "http://127.0.0.1:8000/api/jobs/my_profile_job/suggest-rules"
```

Profile results are browsable in the **History → Profile** sub-tab.

---

#### `schema_snapshot`

Captures the column names and data types for a query result and diffs them against the previous snapshot. Use this to detect schema drift (added, removed, or type-changed columns) between releases.

**Required fields:**

| Field | Description |
|---|---|
| `query` | SQL query whose schema is captured |

**Required params:**

| Param | Description |
|---|---|
| `environment` | Which side to snapshot: `source`, `target`, or `both` |

**How it works:**

1. The query is executed (or EXPLAINed) against the specified environment(s).
2. Column names and inferred types are stored in `schema_snapshots`.
3. The diff against the previous snapshot is computed: `added`, `removed`, and `changed` (type mismatch) columns.
4. Any schema change causes the job to fail; a clean run (no diff) passes.

**Schema history API:**

```powershell
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/jobs/my_schema_job/schema-history?environment=source"
```

Schema snapshots are browsable in the **History → Schema** sub-tab.

---

#### `cross_job_assertion`

Asserts that a numeric metric from one job's latest result matches another job's metric within a configurable tolerance. Use this to enforce cross-table consistency (e.g. orders count should match payments count within 5%).

**Required params:**

| Param | Description |
|---|---|
| `source_job` | Name of the job whose metric is the left-hand side |
| `source_metric` | Metric to read from the source job: `count`, `mismatch_count`, `missing_in_target`, `missing_in_source` |
| `target_job` | Name of the job whose metric is the right-hand side |
| `target_metric` | Same options as `source_metric` |
| `tolerance` | Numeric tolerance value |
| `tolerance_type` | `absolute` (raw difference) or `pct` (percentage difference) |

**How it works:**

1. The executor reads the latest stored result for `source_job` and `target_job`.
2. The specified metrics are extracted.
3. The absolute or percentage difference is computed.
4. If the difference exceeds `tolerance`, the job fails.
5. No SQL query is needed — the assertion runs purely against stored results.

---

#### `api_reconciliation`

Reconciles two REST API endpoints against each other, row-by-row, the same way `reconciliation` reconciles two SQL queries. Use this to validate a microservice or partner API against another API, or (via the Compare tab instead) against a database table.

**Required fields:**

| Field | Description |
|---|---|
| `key_columns` | One or more columns that uniquely identify a row (used to join the two API responses) |

**Required params:**

| Param | Description |
|---|---|
| `source_api_endpoint` | Name of an endpoint defined in the config's `api_endpoints` map (source side) |

**Optional params:**

| Param | Description |
|---|---|
| `target_api_endpoint` | Name of an endpoint defined in the config's `api_endpoints` map (target side). Leave unset to save the job as a draft — see below |

**Optional fields:**

| Field | Default | Description |
|---|---|---|
| `exclude_columns` | `[]` | Columns to ignore during comparison |
| `rules` | `[]` | DQ rules evaluated against the source result set |
| `depends_on` | `[]` | Job names that must complete (and pass) before this job runs |
| `pass_condition` | `null` | Custom threshold overrides for pass/fail logic |

**How it works (live mode):**

1. When `use_live_connections` is enabled, the executor resolves `source_api_endpoint`/`target_api_endpoint` from the `api_endpoints` map on the config used for the run, fetches both (following any configured pagination), and reconciles the resulting datasets on `key_columns`.
2. Value differences, rows missing from target, and rows missing from source are recorded as mismatch details, exactly like `reconciliation`.
3. Without `use_live_connections`, the job falls back to simulation data like other job types.
4. **`target_api_endpoint` is optional.** A job with only `source_api_endpoint` set saves fine and runs with status `SKIPPED` (no comparison performed) — useful for wiring up the source side first and adding the target once it's ready. Set `target_api_endpoint` later (edit the job) to turn on real reconciliation on the next run.

See [API Endpoints (REST API Data Sources)](#api-endpoints-rest-api-data-sources) for how to define the endpoints an `api_reconciliation` job references.

---

### Run Settings Reference

Run settings are configured in the **Run Settings** panel in the Launch tab or passed as `run_settings` in the API payload. All fields are optional; defaults are shown.

| Setting | Default | Description |
|---|---:|---|
| `use_live_connections` | `false` | When `true`, the run connects to the actual SQL Server, SAP BO, and Automic systems using the selected config. When `false`, the run uses built-in simulation data (safe for development and testing). |
| `execution_mode` | `parallel` | `parallel` runs all jobs concurrently up to `max_workers`; `sequential` runs jobs one at a time in the listed order. |
| `max_workers` | `4` | Number of parallel threads when `execution_mode` is `parallel`. Increase for large job sets; reduce if database connections are limited. |
| `max_duration_seconds` | `0` | SLO threshold in seconds per job. A job that takes longer than this is marked `SLOW` even if it passes. `0` disables the threshold. |
| `float_tolerance` | `1e-9` | Floating-point comparison tolerance. Two numeric values are considered equal if their absolute difference is less than this. Increase for currency columns with rounding differences. |
| `schema_mismatch_policy` | `warn` | `warn` records column count or name differences as a warning and compares only common columns. `error` immediately fails the job when schemas differ. |
| `null_equals_null` | `true` | When `true`, a null in source and a null in target are treated as equal. Set to `false` to flag null-to-null pairs as mismatches. |
| `chunk_size` | `0` | Chunk large tables by fetching `chunk_size` rows at a time. `0` fetches the entire result set in one query. Useful for very large tables to reduce peak memory. |
| `use_hash_precheck` | `true` | Before comparing values column-by-column, hash the entire source and target rows. If the hashes match, the row is immediately marked clean. Speeds up runs with many passing rows. |
| `comparison_backend` | `pandas` | Data comparison engine: `pandas` (default, broad compatibility) or `polars` (faster for large result sets). Both are included in the base install. |
| `run_profile` | `full` | `full` compares every row. `shadow` wraps the backend in `SamplingBackend`, comparing only a `shadow_sample_frac` sample — rows missing on either side are always kept regardless of sampling. Set in the Launch tab's **Run Profile** dropdown or via the API; use `shadow` for cheap, fast per-PR checks and `full` for the nightly/authoritative run. |
| `shadow_sample_frac` | `0.02` | Fraction of rows (0-1) sampled per key when `run_profile` is `shadow`. Ignored when `run_profile` is `full`. |
| `mismatch_row_limit` | `1000` | Maximum number of mismatch rows stored per job result. Rows beyond this limit are still counted but not stored in detail. |
| `exclude_columns` | `[]` | Global list of column names to skip during comparison. Applies to all jobs in the run. Job-level `exclude_columns` are merged with this list. |
| `key_columns` | `[]` | Global key columns used as a fallback when a job does not specify its own `key_columns`. Job-level `key_columns` take precedence. |
| `health_check` | `false` | When `true`, connectivity to both environments is verified before any job runs. The run aborts early if a health check fails. |
| `metrics_enabled` | `true` | Write a per-run metrics JSON sidecar to `logs/metrics_<run_id>.json`. |
| `notes` | `""` | Free-form text attached to the run record; visible in History. |
| `max_retries` | `0` | Retry a failed job up to this many times using exponential backoff. Maximum is `10`. |
| `retry_delay_seconds` | `30` | Initial delay before the first retry. Doubles on each subsequent attempt (30s, 60s, 120s, …). |
| `retry_on` | `["error"]` | Which failure conditions trigger a retry. Options: `"error"` (exception during execution) and/or `"timeout"` (job exceeded `max_duration_seconds`). |

Default seed jobs are returned if the database has no saved jobs.

### Monitor

Use this tab to:

- Watch active runs, including pytest test suite runs (`run_type=test_suite`).
- View run progress.
- See passed, failed, slow, and error counters.
- Track the current job where progress data is available.
- Receive live updates through `GET /api/runs/{run_id}/stream`; the browser falls back to 5-second polling if SSE is unavailable.
- **Cancel** an active ETL run or pytest suite run, with a confirmation dialog to prevent accidental clicks.

### History

Use this tab to:

- **Runs sub-tab** — browse previous runs with status and run-type filters (includes `test_suite` runs from the pytest runner).
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

REST API endpoints:

- Defined per saved config in the Config modal's **API Endpoints** section — see [API Endpoints (REST API Data Sources)](#api-endpoints-rest-api-data-sources).
- **Test** — `POST /api/adapters/rest-api/test` fetches a single page and reports latency, without pulling the full (possibly paginated) dataset.
- **Preview** — `POST /api/adapters/rest-api/preview` fetches a sample of rows so you can confirm auth, `json_root_path`, and response parsing are correct before wiring the endpoint into a job or comparison.

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

### Global Logs Tab

The **Logs** tab in the UI shows the server-wide application log — not scoped to any single run — and auto-refreshes every 5 seconds. Use it for troubleshooting things that aren't tied to a specific run: startup errors, scheduler activity, auth failures, unhandled exceptions, and uvicorn request errors (uvicorn's own logger is routed into the same log file, so tracebacks that used to only appear on the console now show up here too).

It supports the same search box, level chips (ALL/ERROR/WARN/INFO/DEBUG), and an optional "Filter to a run ID" field to narrow down to one run without leaving the tab.

Backing endpoint:

```text
GET /api/logs
GET /api/logs?run_id=<run_id>&q=schema_check&level=ERROR&limit=500
```

`run_id` is optional — omit it to see everything.

## Compare Tab

The **Compare** tab provides three first-class comparison modes for ad-hoc analysis that does not fit the standard job-driven run workflow. Each mode produces a standard `TestRun` record (visible in History with full mismatch details, export, and baseline support).

---

### BO Report Compare

Compare two SAP BusinessObjects report outputs side-by-side. Use this when you want to verify that a report produces identical data in two different environments (or between two report versions) without setting up a full reconciliation job.

**Source types**

Each side (Source A, Source B) can be any combination of:

| Source Type | When to use | Required inputs |
|---|---|---|
| `live` | Fetch the report directly from a live SAP BO server | Saved config (with BO URL/credentials), Document ID, Report/Page ID, Download format (`csv`/`xlsx`/`xls`) |
| `path` | Read a previously downloaded file from a file system path | Absolute file path on the server |
| `upload` | Upload a file directly from your browser | File contents (CSV, XLSX, or XLS) |
| `api` | Fetch data from a named REST API endpoint defined on a saved config | Saved config, endpoint name (from the config's `api_endpoints` map) — see [API Endpoints (REST API Data Sources)](#api-endpoints-rest-api-data-sources) |

**Steps (UI):**

1. Open the **Compare** tab and select **BO Report**.
2. For **Source A**: select the source type, then fill in the required fields.
   - For `live`: pick a saved config, enter the Document ID and Report ID, choose the download format.
   - For `path`: enter the full server-side path to the report file.
   - For `upload`: click **Browse** and select a CSV/XLSX/XLS file from your computer.
3. Repeat for **Source B**.
4. Optionally enter **Key Columns** — the columns used to join the two report outputs. If left blank, the engine attempts to infer a key column from common ID-like column names (`id`, `employee_id`, `order_id`, etc.). If no key can be inferred automatically, key columns are required.
5. Optionally enter **Exclude Columns** — columns present in both outputs that should not be compared (e.g. report run timestamps or page numbers).
6. Optionally set **Label A** and **Label B** — human-readable names shown in History and mismatch details.
7. Click **Compare**. The comparison runs and the result is stored as a run with `run_type = bo_comparison`.

**Options:**

| Option | Description |
|---|---|
| `key_columns` | Join columns; auto-inferred from well-known names if omitted |
| `exclude_columns` | Columns to skip during value comparison |
| `label_a` / `label_b` | Display names for each source in mismatch details |
| `doc_id` | Document-level BO ID (can be set once at the top instead of per-source) |
| `report_id` | Report/page-level BO ID (same as above) |
| `api_endpoint_name` | Endpoint name (from the config's `api_endpoints` map) when a side uses `source_type: "api"` |

**API:**

```powershell
$h = @{ Authorization = "Bearer etl_<token>" }
$body = @{
  source_a = @{
    source_type = "live"
    config_id   = 1
    doc_id      = "FI_DOC_001"
    report_id   = "RPT_SALES_SUMMARY_001"
    format      = "xlsx"
  }
  source_b = @{
    source_type = "path"
    file_path   = "C:\reports\sales_prod_20240101.xlsx"
  }
  key_columns     = @("region", "product_category")
  exclude_columns = @("report_generated_at")
  label_a         = "Dev BO"
  label_b         = "Prod File"
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/compare/bo-report" -Body $body -ContentType "application/json" -Headers $h
```

---

### Reconciliation Dual-Environment Compare

Launch two reconciliation runs (each against a different config/environment pair) simultaneously and compare their results side-by-side. Use this when you want to validate that the same job produces equivalent outcomes across two entirely different database environments (e.g. two staging instances, or a staging-vs-DR pair).

**Steps (UI):**

1. Open the **Compare** tab and select **Dual-Environment Reconciliation**.
2. For **Environment A**: select the saved config, enter Source Env label and Target Env label.
3. For **Environment B**: select a different saved config, enter its Source Env and Target Env labels.
4. Select the shared job list — only jobs present in the catalog are available.
5. Optionally adjust **Run Settings** (same options as the main Launch tab).
6. Click **Run Dual Compare**. Two runs are created in parallel and linked by a `pair_id`.

**Options:**

| Option | Description |
|---|---|
| `config_id_a` / `config_id_b` | Saved config IDs for the two environment pairs |
| `source_env_a` / `target_env_a` | Env pair labels for run A |
| `source_env_b` / `target_env_b` | Env pair labels for run B |
| `job_names` | Shared job list; both runs execute the same jobs |
| `run_settings` | Full run settings block (applied to both runs) |

**Viewing results:**

After both runs complete, the **Pair** view shows:

- Status of each run side-by-side.
- Per-job comparison: which tests improved, regressed, stayed the same, were added, or were removed.
- Links to each individual run for full mismatch drill-down.

**API:**

```powershell
# Launch a dual-environment run
$body = @{
  config_id_a   = 1
  config_id_b   = 2
  source_env_a  = "staging-a"
  target_env_a  = "prod-a"
  source_env_b  = "staging-b"
  target_env_b  = "prod-b"
  job_names     = @("orders_reconciliation", "customers_reconciliation")
  run_settings  = @{ execution_mode = "parallel"; max_workers = 4 }
} | ConvertTo-Json -Depth 6
$resp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/compare/dual-env" -Body $body -ContentType "application/json" -Headers $h
# $resp.pair_id, $resp.run_id_a, $resp.run_id_b

# List all paired runs
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/compare/pairs"

# Get a specific pair result
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/compare/pairs/$($resp.pair_id)"
```

---

### Recon File Compare

Compare a stored reconciliation run result (or an HTML report file) against a production HTML report or any other tabular file. Use this to validate a historical run against a new production extract, or to compare two external files (CSV/XLSX) without running a live query.

**Source types for each side:**

| Source | How to specify |
|---|---|
| Stored run | `stored_run_id` — a run ID from History |
| File on disk | `file_a_path` / `file_b_path` — server-side path scoped to `SERVER_FILE_ALLOWED_DIRS` or `UPLOAD_BASE_DIR`; on local Windows installs, `C:\temp` is allowed by default |
| Browser upload | `file_a_content_b64` / `file_b_content_b64` — base64-encoded file content (set `file_a_name` / `file_b_name` for format detection) |

Exactly one source must be provided for each side.

**Steps (UI):**

1. Open the **Compare** tab and select **Recon File Compare**.
2. For **Source A**: choose a stored run from the dropdown, or upload/path a file.
3. For **Source B**: choose a stored run, or upload/path a production file.
4. Optionally specify **Key Columns**. If omitted, the engine tries to auto-detect an ID column; if no key column is identifiable, it falls back to positional (row-number) comparison.
5. Optionally specify **Exclude Columns**.
6. Optionally set display **Labels** for both sides.
7. Click **Compare**.

**Options:**

| Option | Description |
|---|---|
| `stored_run_id` / `stored_run_id_b` | IDs of previously stored runs to use as input |
| `file_a_path` / `file_b_path` | Server file paths scoped to `SERVER_FILE_ALLOWED_DIRS` or `UPLOAD_BASE_DIR`; on local Windows installs, `C:\temp` is allowed by default |
| `file_a_content_b64` / `file_b_content_b64` | Base64-encoded file contents for browser uploads |
| `file_a_name` / `file_b_name` | File names (used for format detection, e.g. `.xlsx`, `.csv`) |
| `key_columns` | Join columns; falls back to row-position if none are found |
| `exclude_columns` | Columns to skip; matching ignores case, spaces, hyphens, and underscores (for example `sequence_number` also matches `Sequence Number`) |
| `label_a` / `label_b` | Display names |

**Supported file formats:** CSV (auto-delimited), XLSX, XLS, JSON, XML, TSV/TXT.

For on-prem server paths outside `C:\temp`, configure allowed roots before starting the API. On Windows, separate multiple roots with `;`, for example:

```powershell
$env:SERVER_FILE_ALLOWED_DIRS = 'D:\TDS\XML;D:\TDS\Reports'
```

**Positional fallback:** When no key column can be inferred and `key_columns` is not specified, the engine inserts a synthetic `__row__` column (1-based row number) and uses it as the key. This ensures every row is compared even for files without a natural identifier (e.g. summary reports).

**API:**

```powershell
# Compare a stored run against a file upload
$fileBytes = [System.IO.File]::ReadAllBytes("C:\reports\prod_export.xlsx")
$fileB64   = [Convert]::ToBase64String($fileBytes)

$body = @{
  stored_run_id      = "abc123"
  file_b_content_b64 = $fileB64
  file_b_name        = "prod_export.xlsx"
  key_columns        = @("order_id")
  exclude_columns    = @("export_timestamp")
  label_a            = "Stored Run"
  label_b            = "Production Extract"
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/compare/recon-file" -Body $body -ContentType "application/json" -Headers $h
```

---

### SQL Direct Compare

Compare two SQL queries run against two different saved configs and diff the result sets directly. This mode is useful for ad-hoc cross-database queries without creating a saved job.

**Options:**

| Option | Description |
|---|---|
| `config_id_a` / `config_id_b` | Saved config IDs for the two databases |
| `query_a` / `query_b` | SQL queries; may differ (different table names, filters, etc.) |
| `key_columns` | Join columns for the diff |
| `exclude_columns` | Columns to skip |
| `label_a` / `label_b` | Display names |
| `connection_a` / `connection_b` | Optional [named connection](#named-connections-multiple-db-connections-per-environment) on `config_id_a` / `config_id_b` to use instead of the config's top-level DB values |
| `advanced` | Optional [Advanced compare options](#advanced-compare-options) block |

If `config_id_a` or `config_id_b` has named connections, the UI shows a connection picker on the corresponding Source A / Source B card.

**API:**

```powershell
$body = @{
  config_id_a     = 1
  config_id_b     = 2
  query_a         = "SELECT id, amount, status FROM orders WHERE created_date = '2024-01-01'"
  query_b         = "SELECT id, amount, status FROM dbo.orders WHERE created_date = '2024-01-01'"
  key_columns     = @("id")
  exclude_columns = @()
  label_a         = "Dev DB"
  label_b         = "Prod DB"
  connection_a    = "hr_db"
  connection_b    = "hr_db"
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/compare/sql" -Body $body -ContentType "application/json" -Headers $h
```

---

### Advanced Compare Options

All three tabular compare modes (BO Report, Recon File, SQL Direct) accept an optional `advanced` block that controls the comparison engine in detail. In the UI these options appear in an **Advanced Options** accordion on each compare panel.

| Option | Default | Description |
|---|---|---|
| `comparison_backend` | `"pandas"` | Which backend computes the row diff. `"pandas"` — vectorised Pandas merge (default, works everywhere). `"polars"` — Polars FULL OUTER JOIN; faster on large datasets (`pip install polars`). `"duckdb"` — in-process DuckDB SQL; fastest for very wide tables (`pip install duckdb`). |
| `float_tolerance` | `1e-9` | Global floating-point tolerance. Two numeric values are considered equal when `abs(a - b) <= float_tolerance`. |
| `column_tolerances` | `{}` | Per-column tolerance overrides. Supersedes `float_tolerance` for the named column. Example: `{"price": 0.01, "weight_kg": 0.001}`. |
| `datetime_tolerance_seconds` | `0.0` | Maximum allowed difference (in seconds) between two datetime values before they are flagged as mismatched. Useful when comparing timestamps that may differ by sub-second rounding. |
| `case_insensitive_columns` | `[]` | String columns where comparison is case-insensitive. Values are lowercased before diffing. Example: `["status", "country_code"]`. **Pandas backend only.** |
| `whitespace_normalize_columns` | `[]` | String columns where leading/trailing whitespace is stripped and runs of internal whitespace are collapsed to a single space before comparison. **Pandas backend only.** |
| `mismatch_row_limit` | `5000` | Maximum number of mismatch detail rows stored for the compare result. Counts still include all mismatches. |
| `sample_frac` | `null` | When set (0.01–1.0) both source and target DataFrames are randomly sampled to this fraction before comparison. Useful for quick smoke-tests on very large datasets. |
| `parallel_columns` | `false` | When `true`, value comparison is distributed across `parallel_workers` threads — one thread per column. Speeds up wide tables (100+ columns). |
| `parallel_workers` | `4` | Thread-pool size used when `parallel_columns` is `true`. |

**Mismatch delta fields**

When the comparison produces numeric value mismatches, each mismatch record now carries:

| Field | Description |
|---|---|
| `delta` | `target_value − source_value` (numeric columns only; `null` for non-numeric or structural mismatches) |
| `relative_delta` | `delta / abs(source_value)` — relative difference as a fraction (e.g. `0.02` = 2%) |

Both fields are visible in the **mismatch drawer**, the **History** mismatch detail table, and the SQL/File compare expanded diff tables.

**API example with advanced options:**

```powershell
$body = @{
  source_a = @{ source_type = "upload"; file_content_b64 = $fileB64a; file_name = "source.csv" }
  source_b = @{ source_type = "upload"; file_content_b64 = $fileB64b; file_name = "target.csv" }
  key_columns = @("order_id")
  advanced = @{
    comparison_backend          = "polars"
    float_tolerance             = 1e-6
    column_tolerances           = @{ price = 0.01; tax = 0.005 }
    datetime_tolerance_seconds  = 1.0
    case_insensitive_columns    = @("status", "region")
    whitespace_normalize_columns = @("product_name")
    sample_frac                 = 0.25
    parallel_columns            = $true
    parallel_workers            = 8
  }
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/compare/bo-report" -Body $body -ContentType "application/json" -Headers $h
```

---

### Column Stats Compare

The **Column Stats** sub-tab computes aggregate statistics for each column (row count, null count, distinct count, min, max, mean, std dev, sum) in two data sources and reports which metrics have drifted beyond tolerance. This is useful for very large tables where a full row-level diff is too expensive but distribution-level drift still needs to be detected.

**Steps (UI):**

1. Open the **Compare** tab and select **Column Stats**.
2. Configure **Source A** and **Source B** (same upload / path / live / api options as BO Compare).
3. Set **Float Tolerance** (default `1e-9`) and **Row Count Tolerance** (default `0`, meaning exact row counts are required).
4. Optionally set a **Query/Report name** for labelling.
5. Click **Compute Column Stats**.

**Result:** A table of differing metrics grouped by column, with the source value, target value, and numeric delta for each.

**API:**

```powershell
$body = @{
  source_a = @{ source_type = "upload"; file_content_b64 = $fileB64a; file_name = "source.csv" }
  source_b = @{ source_type = "upload"; file_content_b64 = $fileB64b; file_name = "target.csv" }
  label_a              = "Source"
  label_b              = "Target"
  query_name           = "orders_stats"
  float_tolerance      = 1e-6
  row_count_tolerance  = 0
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/compare/column-stats" -Body $body -ContentType "application/json" -Headers $h
```

**Response shape:**

```json
{
  "query_name": "orders_stats",
  "source_env": "Source",
  "target_env": "Target",
  "executed_at": "2026-07-02T06:00:00Z",
  "has_diffs": true,
  "diffs": [
    { "column": "amount", "metric": "mean",  "source_value": 152.4, "target_value": 154.1, "delta": 1.7 },
    { "column": "amount", "metric": "sum",   "source_value": 15240.0, "target_value": 15410.0, "delta": 170.0 },
    { "column": "status", "metric": "distinct_count", "source_value": 3, "target_value": 4, "delta": null }
  ],
  "diff_by_column": { "amount": [...], "status": [...] }
}
```

---

### Cross-Run Mismatch Diff

The **Mismatch Diff** sub-tab compares the mismatch sets of two previously stored runs and classifies each mismatch into one of three categories:

| Category | Meaning |
|---|---|
| **New** (regressions) | Mismatches present in Run B but not Run A — something broke |
| **Resolved** (fixes) | Mismatches present in Run A but not Run B — something was fixed |
| **Persistent** | Mismatches present in both runs — still unresolved |

**Steps (UI):**

1. Open the **Compare** tab and select **Mismatch Diff**.
2. Enter **Run A** (baseline run ID — e.g. yesterday's run) and **Run B** (current run ID — e.g. today's).
3. Optionally set labels and a query name filter.
4. Click **Run Mismatch Diff**.

**Result:** Three collapsible tables (New, Resolved, Persistent) with a summary counter row.

**API:**

```powershell
$body = @{
  run_id_a    = "uuid-of-baseline-run"
  run_id_b    = "uuid-of-current-run"
  run_a_label = "2026-07-01 run"
  run_b_label = "2026-07-02 run"
  query_name  = "orders_reconciliation"   # optional — filter to one test
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/compare/mismatch-diff" -Body $body -ContentType "application/json" -Headers $h
```

**Response shape:**

```json
{
  "query_name": "orders_reconciliation",
  "run_a_label": "2026-07-01 run",
  "run_b_label": "2026-07-02 run",
  "compared_at": "2026-07-02T06:05:00Z",
  "summary": { "new": 3, "resolved": 7, "persistent": 12 },
  "has_regressions": true,
  "new": [...],
  "resolved": [...],
  "persistent": [...]
}
```

---

### Comparison Backends

The framework supports three interchangeable comparison backends. All backends implement the same `ComparisonBackend` protocol: `compare(df_source, df_target) -> list[MismatchRecord]`.

| Backend | Class | When to use | Requirement |
|---|---|---|---|
| **Pandas** | `PandasBackend` | Default — works everywhere, full feature set (case-insensitive, whitespace-normalize, per-column tolerance, datetime tolerance) | Always available |
| **Polars** | `PolarsBackend` | Large datasets with many rows; FULL OUTER JOIN via Polars | `pip install polars` |
| **DuckDB** | `DuckDBBackend` | Very wide tables (100+ columns); SQL-level FULL OUTER JOIN via DuckDB's C++ engine | `pip install duckdb` |
| **Sampling** | `SamplingBackend` | Wraps any backend and samples N% of rows before comparison (quick smoke-tests) | Always available |

**Using backends programmatically:**

```python
from etl_framework.reconciliation.backends import PandasBackend, PolarsBackend, DuckDBBackend, SamplingBackend
from etl_framework.reconciliation.engine import ReconciliationEngine

# DuckDB backend with per-column tolerances
backend = DuckDBBackend(
    key_columns=["order_id"],
    column_tolerances={"price": 0.01, "tax": 0.005},
    datetime_tolerance_seconds=1.0,
)

# Wrap with sampling for quick checks
sampled = SamplingBackend(backend, sample_frac=0.10, seed=42)

engine = ReconciliationEngine(
    source_engine, target_engine,
    key_columns=["order_id"],
    backend=sampled,
    parallel_columns=True,
    parallel_workers=8,
)
result = engine.reconcile("SELECT * FROM orders", "orders")
print(result.mismatch_by_column)  # {'price': 12, 'status': 3}
```

**Column stats (aggregate drift detection):**

```python
from etl_framework.reconciliation.column_stats import ColumnStatsComparer

comparer = ColumnStatsComparer(float_tolerance=1e-6, row_count_tolerance=0)
result = comparer.compare(df_source, df_target, query_name="orders", source_env="dev", target_env="prod")
if result.has_diffs:
    for diff in result.diffs:
        print(f"{diff.column}.{diff.metric}: {diff.source_value} vs {diff.target_value}  (Δ {diff.delta})")
```

**Cross-run mismatch diff:**

```python
from etl_framework.reconciliation.mismatch_diff import diff_mismatches

result = diff_mismatches(
    mismatches_a,  # list[MismatchRecord] from run A
    mismatches_b,  # list[MismatchRecord] from run B
    query_name="orders_reconciliation",
    run_a_label="yesterday",
    run_b_label="today",
)
print(result.summary)          # {'new': 3, 'resolved': 7, 'persistent': 12}
print(result.has_regressions)  # True
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

## Write-Audit-Publish Gate

The gate answers one question for one job: **is it safe to promote?** It never publishes or moves data itself — it only returns a verdict for your orchestrator (Automic, Airflow, a CI job, a deploy script) to act on.

**Typical Write-Audit-Publish flow:**

```text
1. WRITE  — load new/changed data into a staging table or schema
2. AUDIT  — run the job's reconciliation/DQ checks against staging
3. GATE   — call POST /api/gates/{job}/evaluate
4. PUBLISH — only if the verdict is PROMOTE: swap staging into production
             (e.g. SQL Server schema swap, table rename, or synonym repoint)
```

**Verdict logic** (`api/services/gate_service.py`): `PROMOTE` requires **both** of:

- The job's most recent `TestResult` has status `PASSED`.
- No unresolved `ContractBreach` exists on an active [Data Contract](#data-contracts) whose `source_job` is this job.

Anything else returns `HOLD` with a `reasons` list explaining exactly why (e.g. `"Latest result for 'orders_reconciliation' is FAILED"` or `"1 open contract breach(es) on 'orders_reconciliation'"`).

**From the UI:** Launch tab → click **Gate** on any job row. A `PROMOTE`/`HOLD` badge appears next to the button and as a toast.

**From the API / an orchestrator:**

```powershell
$h = @{ Authorization = "Bearer etl_<token>" }
$verdict = Invoke-RestMethod -Method Post -Headers $h `
  "http://127.0.0.1:8000/api/gates/orders_reconciliation/evaluate"
$verdict.verdict   # "PROMOTE" or "HOLD"
$verdict.reasons   # [] when PROMOTE, else a list of strings
```

```bash
curl -s -X POST -H "Authorization: Bearer etl_<token>" \
  http://127.0.0.1:8000/api/gates/orders_reconciliation/evaluate | jq
```

**From the CLI (no server round trip needed once you have a `run_id`):**

```powershell
python -m etl_framework.runner.cli --gate-run "$RUN_ID" --output json
# exit codes: 0 passed, 1 failed, 2 cancelled, 3 error, 4 not found
```

Both paths are audited: every gate evaluation is logged as a `gate.evaluated` audit event, queryable via `GET /api/audit`.

## Rules-As-Code & Schema Compatibility

Job DQ rules normally live in the database, edited through the Jobs UI or API. **Rules-as-code** lets you keep those same rules as versioned YAML files instead — reviewed in pull requests, synced into the database on demand.

### Expectation suites

One YAML file per job under `expectations/` (see [expectations/README.md](expectations/README.md)). The file's `rules` list **replaces** the job's DQ rules on sync:

```yaml
# expectations/orders_reconciliation.yml
job: orders_reconciliation
rules:
  - type: not_null
    column: id
    severity: error
  - type: row_count_min
    min_value: 1
```

**Export** current job rules to YAML (useful the first time you adopt this, or to snapshot rules for review):

```powershell
$h = @{ Authorization = "Bearer etl_<token>" }
Invoke-RestMethod -Method Post -Headers $h -ContentType "application/json" `
  -Body (@{ directory = "expectations" } | ConvertTo-Json) `
  http://127.0.0.1:8000/api/expectations/export
```

**Sync** YAML files back into job rules (run this in CI after merging a PR that touched `expectations/`):

```powershell
$h = @{ Authorization = "Bearer etl_<token>" }
$report = Invoke-RestMethod -Method Post -Headers $h -ContentType "application/json" `
  -Body (@{ directory = "expectations" } | ConvertTo-Json) `
  http://127.0.0.1:8000/api/expectations/sync
$report.synced        # job names successfully updated
$report.missing_jobs   # suite files whose `job` doesn't exist yet — create the job first
$report.errors         # suites with an invalid rule type/field — that job's rules are left untouched
```

A suite naming a job that doesn't exist yet is reported in `missing_jobs`, not treated as an error — create the job (UI or API), then re-run sync. A suite with an invalid rule is reported in `errors` and skipped; every other suite in the same sync call still applies.

Every sync is audited as an `expectations.synced` event (`GET /api/audit`).

### Schema-drift compatibility

Schema snapshot diffs (`schema_snapshot` job type, and `GET /api/jobs/{job}/schema-history`) now include a `compatibility` verdict alongside `added`/`removed`/`changed`:

| Verdict | Meaning |
|---|---|
| `full` | No schema change at all. |
| `non_breaking` | Only additive/widening changes — a new column, or a numeric type getting wider (e.g. `int32` → `int64`, `int64` → `float64`). Existing consumers keep working. |
| `risky` | A type change that isn't a clean widening or narrowing (e.g. anything to/from `object`, datetime unit/tz changes) — review before promoting. |
| `breaking` | A column was removed, or a numeric type narrowed (e.g. `int64` → `int32`, `float64` → `int64`) — likely to break downstream consumers. |

The overall `compatibility` on a diff is always the worst individual change (`breaking` > `risky` > `non_breaking` > `full`). See `etl_framework/expectations/schema_compat.py` for the exact rules.

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
  config_id = 1
  source_connection = "hr_db"
  target_connection = "hr_db"
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

`source_connection` / `target_connection` are optional — set them to pick a [named connection](#named-connections-multiple-db-connections-per-environment) from the config instead of its top-level DB values. Omit them to use the config's defaults (unchanged behavior).

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

### Cancel A Run

```powershell
Invoke-RestMethod -Method Post -Headers $h "http://127.0.0.1:8000/api/runs/<run_id>/cancel"
# → { "run_id": "...", "cancel_requested": true }
```

Cooperative cancellation: the executor finishes its current job step, then stops and sets the run to `CANCELLED`. Calling cancel on a run that is already in a terminal status (`PASSED`, `FAILED`, `ERROR`, `CANCELLED`, etc.) returns `cancel_requested: false` with HTTP 202 (not an error).

### Trigger A Pytest Suite Run

```powershell
$body = @{ pytest_args = @("tests/unit/", "-q") } | ConvertTo-Json
$resp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/runs/test-suite" -Body $body -ContentType "application/json" -Headers $h
# $resp.run_id — stream progress with GET /api/runs/{run_id}/stream
```

The run appears in History with `run_type=test_suite`. `pytest_args` is optional; omitting it runs the full suite. Progress events include collected test count and rolling passed/failed/error counters. Exit codes map to: `0 → PASSED`, `1 → COMPLETED` (some tests failed), anything else `→ ERROR`. Send `POST /api/runs/{run_id}/cancel` to terminate the subprocess early.

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

### Coverage Matrix And Flaky Tests

```powershell
# Table/column coverage matrix (tested / observed / untested)
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/coverage"

# Flaky-test report (status flip-flop score over the last N runs)
Invoke-RestMethod -Headers $h "http://127.0.0.1:8000/api/coverage/flaky?window=20"
```

### Segment Drilldown

```powershell
# Live re-query of source vs target counts for a segment column
$body = @{ segment_column = "region" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Headers $h -Uri "http://127.0.0.1:8000/api/runs/<run_id>/results/<result_id>/drilldown" -Body $body -ContentType "application/json"
```

Requires `params.segment_columns` set on the job (or auto-picked low-cardinality columns from the latest profile) — see [Mismatch Segment Drill-Down](#capabilities). Only valid for `reconciliation` job type; returns `400` otherwise.

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
| `outlier_zscore` | `column`, `threshold` | Fails if any value exceeds the absolute z-score threshold |
| `outlier_iqr` | `column`, `iqr_multiplier`, `fence_type` | Fails if any value falls outside the computed IQR fence |
| `outlier_grubbs` | `column`, `alpha` | Runs a single-outlier Grubbs test and fails when an outlier is detected |
| `distribution_ks_test` | `column`, `distribution`, `alpha` | Uses a Kolmogorov-Smirnov test to reject a configured reference distribution |
| `distribution_chi_square` | `column`, `bins`, `expected_frequencies`, `alpha` | Uses chi-square goodness-of-fit against expected bin frequencies |
| `distribution_anderson_darling` | `column`, `alpha` | Uses Anderson-Darling to reject normality |
| `hypothesis_test_proportion` | `column`, `condition`, `expected_proportion`, `alpha` | Fails if observed ratio of a target value differs significantly from the expected proportion |
| `anomaly_detection_sigma` | `column`, `threshold`, `window` | Fails if rolling sigma analysis detects anomalous spikes against prior observations |

The following special job types are supported in addition to the standard `reconciliation` type:

| Job Type | Required Params | Description |
|---|---|---|
| `freshness` | `timestamp_column`, `max_age_hours` | Checks that the most recent timestamp in the column is within `max_age_hours` of the run time |
| `profile` | (optional) `columns`, `drift_threshold_pct` | Computes column statistics and compares with the previous profile run to detect drift |
| `schema_snapshot` | `environment` | Captures column names and types; diffs against the previous snapshot |
| `cross_job_assertion` | `source_job`, `source_metric`, `target_job`, `target_metric`, `tolerance`, `tolerance_type` | Asserts that a metric from one job matches another within a tolerance |

## Testing

### CI quality gate

    # trigger a run via API, capture RUN_ID, then:
    python -m etl_framework.runner.cli --gate-run "$RUN_ID" --output json
    # exit codes: 0 passed, 1 failed, 2 cancelled, 3 error, 4 not found

For cheap per-PR shadow runs, launch with `run_settings: {"run_profile": "shadow", "shadow_sample_frac": 0.02}` — every reconciliation samples ~2% of rows (missing rows always kept). Nightly runs use the default `full` profile.

Run the suite in parallel: `python -m pytest tests/unit -n auto`. Most of the suite is parallel-safe and the pass count matches the serial run. A small number of route tests (`test_mismatch_search.py`, `test_selections_routes.py`) occasionally fail only under `-n auto` and only when run alongside the full suite (they pass standalone) — this is shared module-level/singleton state (e.g. `SessionLocal` monkeypatching, in-memory SQLite) racing across xdist workers, not a bug in the tests themselves. Known follow-up; if you hit it, re-run serially or scope `-n auto` to a subset of files.

### Isolated Transform Testing (TransformCase)

Reconciliation jobs test data *at rest* (source vs. target). `TransformCase` tests business logic *in isolation*: it runs a transform SQL statement against small, in-memory DuckDB fixture tables and reconciles the output against an expected DataFrame — no live database, no network, no fixtures beyond plain `pandas.DataFrame` objects.

```python
# tests/transforms/test_daily_revenue.py
import pandas as pd
from etl_framework.transform_testing.harness import TransformCase

TRANSFORM_SQL = """
    SELECT order_date, SUM(amount) FILTER (WHERE status <> 'CANCELLED') AS revenue
    FROM orders
    GROUP BY order_date
"""

def test_cancelled_orders_excluded_from_revenue():
    mismatches = TransformCase(
        transform_sql=TRANSFORM_SQL,
        inputs={"orders": pd.DataFrame({
            "order_date": ["2026-07-01", "2026-07-01"],
            "amount": [100.0, 50.0],
            "status": ["COMPLETE", "CANCELLED"],
        })},
        expected=pd.DataFrame({"order_date": ["2026-07-01"], "revenue": [100.0]}),
        key_columns=["order_date"],
    ).run()
    assert mismatches == []
```

`inputs` accepts one or more named tables (each a `DataFrame`) — pass multiple to test transforms that join across tables. `TransformCase` reuses the same `DuckDBBackend` comparison engine production reconciliation runs use, so a passing `TransformCase` test and a passing production job mean the same thing. See `tests/transforms/test_example_daily_revenue.py` for a complete example, and add new transform tests under `tests/transforms/`.

Run just the transform tests:

```powershell
python -m pytest tests/transforms -v
```

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

Run the live SQL Server integration test with Docker Compose:

```powershell
docker compose -f docker-compose.integration.yml up -d sqlserver
$env:RUN_LIVE_SQLSERVER_TESTS = "1"
$env:LIVE_SQLSERVER_ODBC_DRIVER = "SQL Server"
python -m pytest tests/integration/test_sqlserver_live_reconciliation.py -q
docker compose -f docker-compose.integration.yml down -v
```

Run the SAP BO mock integration test with Docker Compose:

```powershell
docker compose -f docker-compose.integration.yml up -d --build sapbo
$env:RUN_LIVE_SAPBO_TESTS = "1"
python -m pytest tests/integration/test_sapbo_mock_container.py -q
docker compose -f docker-compose.integration.yml down -v
```

The `sapbo` service is a local HTTPS mock of the SAP BO RESTful Web Services endpoints used by this project. It is not a SAP BusinessObjects distribution. Use `https://127.0.0.1:18443` with username `administrator`, password `Password1`, and SSL verification disabled for the mock's self-signed certificate.

### End-to-end (Playwright) tests

```powershell
npx playwright test                      # full UI suite against a throwaway DB, file/upload-mode compare coverage only
$env:E2E_LIVE_BACKENDS = "1"; npx playwright test  # also covers live SAP BO / SQL Server paths (requires Docker + ODBC Driver 17 for SQL Server)
npx playwright show-report               # view the last HTML report
```

Run property-based tests (requires `hypothesis`):

```powershell
python -m pytest tests/property/ -q
```

Run cancellation and pytest-runner tests:

```powershell
# Unit: repository cancel methods, cancel endpoint (9 tests)
python -m pytest tests/unit/test_run_cancel.py -q

# Unit: PytestRunExecutor output parsing and cancel (10 tests)
python -m pytest tests/unit/test_pytest_runner.py -q

# Integration: two-thread cancel flow (1 test)
python -m pytest tests/integration/test_cancel_flow.py -q
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

This file contains both the application's own logging (`etl_framework.*` loggers) and uvicorn's logging (`uvicorn`, `uvicorn.error`, `uvicorn.access`) — unhandled exceptions, request errors, and startup/shutdown/reload messages all land here regardless of how the server is launched. View it in-app via the **Logs** tab (see [Global Logs Tab](#global-logs-tab)), or tail it directly on disk.

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

### Where To Look First When Something Breaks

Open the **Logs** tab — it shows the live, server-wide log (including uvicorn tracebacks and request errors), auto-refreshing every 5 seconds, with search and level filtering. See [Global Logs Tab](#global-logs-tab). No need to shell into the server or tail `logs/etl_framework.log` by hand unless the UI itself is unreachable.

## Minimal First Run

1. Complete [bootstrap](#authentication) to create and activate a token.
2. Open **Launch** — keep `use_live_connections` off for a local simulation run.
3. Select one or more seed jobs and click **Run Tests**.
4. Open **Monitor** to watch progress.
5. Open **History** for per-test results and mismatch details.
6. Open **Reports** for the HTML report, metrics dashboard, and searchable logs.
7. Open **Compare** to diff two runs or compare BO report sources.
