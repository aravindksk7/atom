# Technical Design Document

## ETL & SAP BO Testing Framework

---

## Overview

The framework is a modular Python package that provides automated data reconciliation, Automic job monitoring, SAP BusinessObjects report validation, and HTML dashboard reporting. It targets Microsoft SQL Server as the primary database and is designed for use both as a full test-suite runner (CI/CD) and as individual ad-hoc components (interactive investigation).

The design follows a layered architecture with strict separation of concerns:

- **CLI Layer** — `run_tests.py` parses arguments and routes execution
- **Orchestration Layer** — `TestRunner` wires components together for full-suite or ad-hoc runs; manages test lifecycle state transitions
- **Core Components** — independent, instantiable modules (Reconciliation, Automic, SAP BO, Reporting)
- **Persistence Layer** — `TestRunRepository` stores run history to a configurable backend (JSON file, SQLite, or DuckDB)
- **Infrastructure Layer** — `DB_Engine`, `Config_Loader`, logging utilities

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI Layer                                │
│                      run_tests.py                               │
│   --config  --run-sql  --run-bo  --run-jobs  --output-format    │
└───────────────────────┬─────────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────────┐
│                  Orchestration Layer                            │
│         TestRunner (state machine: PENDING→RUNNING→DONE)        │
│         AdHocRouter (isolated single-component execution)       │
└──┬──────────────┬──────────────┬──────────────┬─────────────────┘
   │              │              │              │
┌──▼──┐      ┌───▼────┐    ┌────▼────┐   ┌─────▼──────┐
│Config│      │Automic │    │Reconcil-│   │  SAP BO    │
│Loader│      │Client  │    │iation   │   │ Validator  │
└──┬──┘      └───┬────┘    │ Engine  │   └─────┬──────┘
   │              │         └────┬────┘         │
┌──▼──────────────▼──────────────▼─────────────▼──────┐
│                  Infrastructure Layer                │
│         DB_Engine (SQLAlchemy/pyodbc)                │
│         Config Models / Logging Utilities            │
└──────────────────────────┬───────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────┐
│                  Persistence Layer                    │
│   TestRunRepository  (JSON | SQLite | DuckDB)        │
│   - save(TestSuiteResult)                            │
│   - load_history(limit, filters) → list              │
│   - get_run(run_id) → TestSuiteResult                │
└──────────────────────────┬───────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────┐
│                  Reporting Layer                      │
│       Report_Generator (Jinja2 HTML)                 │
│       ConsoleRenderer (stdout table)                 │
└──────────────────────────────────────────────────────┘
```

---

## 3. Module / File Structure

```
etl_framework/
├── __init__.py
├── config/
│   ├── __init__.py
│   ├── loader.py          # Config_Loader: reads YAML, resolves env vars
│   ├── models.py          # EnvironmentConfig, FrameworkConfig dataclasses
│   └── exceptions.py      # ConfigurationError
├── automic/
│   ├── __init__.py
│   ├── client.py          # Automic_Client: REST queries, retry logic
│   └── models.py          # JobStatus dataclass, StatusEnum
├── db/
│   ├── __init__.py
│   └── engine.py          # DB_Engine: SQLAlchemy pool, DataFrame queries
├── reconciliation/
│   ├── __init__.py
│   ├── engine.py          # Reconciliation_Engine: pandas merge/compare
│   └── models.py          # ReconciliationResult, MismatchRecord dataclasses
├── sap_bo/
│   ├── __init__.py
│   ├── validator.py       # SAP_BO_Validator: dual SQL/REST mode
│   └── client.py          # BORestClient: session token, report fetch
├── runner/
│   ├── __init__.py
│   ├── test_runner.py     # TestRunner: orchestrates full-suite execution
│   ├── state.py           # TestStatus enum, TestCaseState dataclass
│   └── adhoc.py           # AdHocRouter: isolated single-component runs
├── repository/
│   ├── __init__.py
│   ├── base.py            # AbstractTestRunRepository (ABC)
│   ├── json_repo.py       # JsonTestRunRepository: NDJSON file backend
│   ├── sqlite_repo.py     # SqliteTestRunRepository: SQLite backend
│   ├── duckdb_repo.py     # DuckDbTestRunRepository: DuckDB backend
│   └── factory.py         # build_repository(config) → AbstractTestRunRepository
├── reporting/
│   ├── __init__.py
│   ├── generator.py       # Report_Generator: Jinja2 → HTML
│   ├── console.py         # ConsoleRenderer: tabulate stdout output
│   └── templates/
│       └── report.html.j2 # Jinja2 HTML template (self-contained)
├── utils/
│   ├── __init__.py
│   └── logging.py         # configure_logging(), get_logger()
└── exceptions.py          # ETLFrameworkError base + all shared exceptions

run_tests.py               # CLI entry point
config/
├── sample_config.yaml     # Dummy config for onboarding
└── sample_tests.yaml      # Dummy test suite definition
tests/
├── unit/
│   ├── test_config.py
│   ├── test_reconciliation.py
│   ├── test_automic.py
│   ├── test_runner.py
│   ├── test_repository.py
│   └── test_reporting.py
├── integration/
│   └── test_db_engine.py
└── property/
    └── test_reconciliation_properties.py  # hypothesis PBT
```

---

## Data Models

### 4.1 EnvironmentConfig

```python
@dataclass
class EnvironmentConfig:
    name: str                    # e.g. "dev", "qa"
    # SQL Server connection
    db_host: str
    db_port: int = 1433
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""        # never logged
    db_driver: str = "ODBC Driver 17 for SQL Server"
    db_pool_size: int = 5
    db_pool_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 3600
    db_connect_timeout: int = 15
    # Automic API
    automic_url: str = ""
    automic_user: str = ""
    automic_password: str = ""   # never logged
    automic_timeout: int = 30
    automic_max_retries: int = 3
    # SAP BO API
    bo_url: str = ""
    bo_user: str = ""
    bo_password: str = ""        # never logged
    bo_timeout: int = 60
```

### 4.2 TestStatus Enum (`runner/state.py`)

Explicit lifecycle states for every tracked test case, job check, and reconciliation:

```python
from enum import Enum

class TestStatus(str, Enum):
    PENDING   = "PENDING"    # registered but not yet started
    RUNNING   = "RUNNING"    # currently executing
    PASSED    = "PASSED"     # completed, no issues found
    FAILED    = "FAILED"     # completed, issues found (mismatches, job failure)
    ERROR     = "ERROR"      # component raised an exception before producing a result
    SKIPPED   = "SKIPPED"    # intentionally not executed (filtered out by --run-sql etc.)
```

### 4.3 TestCaseState (`runner/state.py`)

Tracks live state for each test case during a run. Separate from the final result objects so the runner can update state mid-execution without mutating immutable result dataclasses.

```python
@dataclass
class TestCaseState:
    name: str
    test_type: str              # "job" | "sql" | "bo"
    status: TestStatus = TestStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None   # populated on ERROR status
    result: ReconciliationResult | JobStatus | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
```

### 4.4 MismatchRecord

```python
@dataclass
class MismatchRecord:
    key_values: dict[str, Any]   # {key_col: value} identifying the row
    column_name: str
    source_value: Any
    target_value: Any
    mismatch_type: str           # "value_diff" | "missing_in_target" | "missing_in_source"
```

### 4.5 ReconciliationResult

```python
@dataclass
class ReconciliationResult:
    query_name: str
    source_env: str
    target_env: str
    source_row_count: int
    target_row_count: int
    matched_count: int
    missing_in_target_count: int
    missing_in_source_count: int
    value_mismatch_count: int
    mismatches: list[MismatchRecord]   # capped at mismatch_row_limit
    status: TestStatus                 # PASSED | FAILED | ERROR
    executed_at: datetime
    duration_seconds: float

    @property
    def total_issues(self) -> int:
        return (self.missing_in_target_count
                + self.missing_in_source_count
                + self.value_mismatch_count)
```

### 4.6 JobStatus

```python
@dataclass
class JobStatus:
    identifier: str              # Run_ID or Job_Name
    identifier_type: str         # "run_id" | "job_name"
    status: TestStatus           # SUCCESS maps to PASSED, FAILED/NOT_FOUND → FAILED, RUNNING → RUNNING
    environment: str
    checked_at: datetime
    raw_response: dict           # original API payload
```

### 4.7 TestSuiteResult

```python
@dataclass
class TestSuiteResult:
    run_id: str                        # uuid4 for this execution
    started_at: datetime
    completed_at: datetime | None      # None if run is still in progress
    source_env: str
    target_env: str
    framework_version: str
    fail_fast: bool                    # whether run aborted on first failure
    test_cases: list[TestCaseState]    # ordered list of all test case states

    # Derived views — filter test_cases by type for convenience
    @property
    def job_results(self) -> list[JobStatus]:
        return [t.result for t in self.test_cases
                if t.test_type == "job" and t.result is not None]

    @property
    def reconciliation_results(self) -> list[ReconciliationResult]:
        return [t.result for t in self.test_cases
                if t.test_type == "sql" and t.result is not None]

    @property
    def bo_results(self) -> list[ReconciliationResult]:
        return [t.result for t in self.test_cases
                if t.test_type == "bo" and t.result is not None]

    @property
    def total_passed(self) -> int:
        return sum(1 for t in self.test_cases if t.status == TestStatus.PASSED)

    @property
    def total_failed(self) -> int:
        return sum(1 for t in self.test_cases
                   if t.status in (TestStatus.FAILED, TestStatus.ERROR))

    @property
    def total_skipped(self) -> int:
        return sum(1 for t in self.test_cases if t.status == TestStatus.SKIPPED)

    @property
    def exit_code(self) -> int:
        return 0 if self.total_failed == 0 else 1
```

---

## Components and Interfaces

### 5.1 Config_Loader (`config/loader.py`)

Responsibilities: read YAML, resolve `${VAR}` references, validate required fields, return typed `EnvironmentConfig` objects.

```python
class ConfigLoader:
    REQUIRED_DB_FIELDS = ["db_host", "db_name", "db_user", "db_password"]

    def load(self, config_path: str) -> dict[str, EnvironmentConfig]:
        """
        Returns a dict keyed by environment name.
        Raises ConfigurationError on missing file, missing field, or unresolved env var.
        """

    def _resolve_env_vars(self, value: str) -> str:
        """
        Replaces ${VAR_NAME} with os.environ[VAR_NAME].
        Raises ConfigurationError if VAR_NAME is not set.
        """

    def _validate_env_config(self, name: str, raw: dict) -> None:
        """Checks all REQUIRED_DB_FIELDS are present."""
```

**Env var resolution** uses `re.sub(r'\$\{(\w+)\}', resolver_fn, value)` where `resolver_fn` looks up `os.environ` and raises on missing key.

---

### 5.2 DB_Engine (`db/engine.py`)

Responsibilities: create and pool SQLAlchemy engines for SQL Server, execute queries as DataFrames, manage connection lifecycle.

```python
class DBEngine:
    def __init__(self, env_config: EnvironmentConfig):
        self._env = env_config
        self._engine: Engine | None = None

    def connect(self) -> None:
        """
        Creates SQLAlchemy engine with mssql+pyodbc dialect.
        Connection string pattern:
          mssql+pyodbc://{user}:{password}@{host}:{port}/{db}
            ?driver={driver}&TrustServerCertificate=yes
        Pool config from env_config (pool_size, max_overflow, pool_timeout, pool_recycle).
        Raises DatabaseConnectionError on failure.
        """

    def execute_query(
        self,
        query: str,
        params: dict | None = None
    ) -> pd.DataFrame:
        """
        Executes parameterised query, returns DataFrame.
        Raises QueryExecutionError wrapping the SQLAlchemy exception.
        Logs query text and row count at DEBUG level.
        """

    def dispose(self) -> None:
        """Disposes engine pool gracefully."""

    def __enter__(self) -> "DBEngine": ...
    def __exit__(self, *args) -> None:
        self.dispose()
```

**Connection string construction:**
```python
url = (
    f"mssql+pyodbc://{quote_plus(user)}:{quote_plus(password)}"
    f"@{host}:{port}/{db}"
    f"?driver={quote_plus(driver)}"
    f"&TrustServerCertificate=yes"
    f"&connect_timeout={connect_timeout}"
)
engine = create_engine(
    url,
    pool_size=pool_size,
    max_overflow=pool_overflow,
    pool_timeout=pool_timeout,
    pool_recycle=pool_recycle,
    echo=False,  # SQL logged via our own logger at DEBUG
)
```

---

### 5.3 Reconciliation_Engine (`reconciliation/engine.py`)

Responsibilities: compare two DataFrames using pandas, identify missing/mismatched rows, enforce float tolerance, exclude columns, cap mismatch output.

```python
class ReconciliationEngine:
    def __init__(
        self,
        source_engine: DBEngine,
        target_engine: DBEngine,
        key_columns: list[str],
        exclude_columns: list[str] | None = None,
        float_tolerance: float = 1e-9,
        mismatch_row_limit: int = 1000,
    ): ...

    def reconcile(
        self,
        query: str,
        query_name: str,
        params: dict | None = None,
    ) -> ReconciliationResult:
        """
        1. Execute query on source and target DBEngines → df_source, df_target
        2. Drop exclude_columns from both DataFrames
        3. Outer merge on key_columns with indicator column (_merge)
        4. left_only rows → missing_in_target
        5. right_only rows → missing_in_source
        6. both rows → compare column-by-column
           - For float columns: use numpy.isclose(a, b, atol=float_tolerance)
           - For other types: direct equality, treating NaN == NaN as match
        7. Collect MismatchRecord for each difference
        8. Cap mismatches list at mismatch_row_limit
        9. Return ReconciliationResult
        """
```

**Merge strategy:**
```python
merged = pd.merge(
    df_source.assign(_src=True),
    df_target.assign(_tgt=True),
    on=key_columns,
    how="outer",
    indicator=True,
    suffixes=("_src", "_tgt"),
)
missing_in_target = merged[merged["_merge"] == "left_only"]
missing_in_source = merged[merged["_merge"] == "right_only"]
both = merged[merged["_merge"] == "both"]
```

**Float comparison:**
```python
import numpy as np

def _values_match(a, b, is_float: bool, tolerance: float) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    if is_float:
        return bool(np.isclose(float(a), float(b), atol=tolerance))
    return a == b
```

---

### 5.4 Automic_Client (`automic/client.py`)

Responsibilities: authenticate with Automic REST API, query job/run statuses, retry with exponential backoff.

```python
class AutomicClient:
    STATUS_MAP = {
        "ENDED_OK": "SUCCESS",
        "ENDED_NOT_OK": "FAILED",
        "ACTIVE": "RUNNING",
        "WAITING": "RUNNING",
    }

    def __init__(self, env_config: EnvironmentConfig):
        self._base_url = env_config.automic_url
        self._session = requests.Session()
        self._session.headers.update(self._build_auth_header(env_config))
        self._timeout = env_config.automic_timeout
        self._max_retries = env_config.automic_max_retries

    def get_status_by_run_id(self, run_id: str) -> JobStatus:
        """GET /api/v1/executions/{run_id}"""

    def get_status_by_job_name(self, job_name: str) -> JobStatus:
        """GET /api/v1/jobs/{job_name}/executions?limit=1&sort=start_time:desc"""

    def get_statuses(
        self,
        identifiers: list[str],
        id_type: str = "run_id",
    ) -> dict[str, JobStatus]:
        """Loops over identifiers, calls appropriate method per entry."""

    @retry(
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(requests.RequestException),
        reraise=False,
    )
    def _request(self, method: str, url: str) -> dict:
        """
        Executes HTTP request.
        Raises AutomicAPIError on 4xx/5xx.
        Raises AutomicTimeoutError after all retries exhausted.
        Logs request URL and status at DEBUG.
        """

    def _normalise_status(self, raw_status: str) -> str:
        return self.STATUS_MAP.get(raw_status.upper(), "NOT_FOUND")
```

**Auth header construction:**
```python
# Bearer token approach
headers = {"Authorization": f"Bearer {token}"}

# Basic auth fallback
import base64
credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
headers = {"Authorization": f"Basic {credentials}"}
```

---

### 5.5 SAP_BO_Validator (`sap_bo/validator.py`)

Responsibilities: validate SAP BO report data across two environments using either direct SQL (primary) or BO REST API (alternative).

```python
class SAPBOValidator:
    def __init__(
        self,
        source_config: EnvironmentConfig,
        target_config: EnvironmentConfig,
        mode: str = "sql",   # "sql" | "api"
    ):
        self._source_config = source_config
        self._target_config = target_config
        self._mode = mode
        self._reconciler: ReconciliationEngine | None = None

    def validate_report(
        self,
        report_id: str,
        sql_query: str | None = None,
        key_columns: list[str] | None = None,
        exclude_columns: list[str] | None = None,
        float_tolerance: float = 1e-9,
    ) -> ReconciliationResult:
        """
        SQL mode: uses sql_query with DBEngine + ReconciliationEngine.
        API mode: fetches report data via BORestClient, normalises to DataFrame,
                  then delegates to ReconciliationEngine.compare_dataframes().
        Raises ReportNotFoundError if report_id not found.
        Raises BOAPIError on API failure.
        """
```

**BORestClient (`sap_bo/client.py`):**
```python
class BORestClient:
    LOGON_ENDPOINT = "/biprws/logon/long"
    REPORT_ENDPOINT = "/biprws/raylight/v1/documents/{doc_id}/reports"

    def __init__(self, env_config: EnvironmentConfig):
        self._base_url = env_config.bo_url
        self._token: str | None = None

    def authenticate(self) -> None:
        """POST to LOGON_ENDPOINT, stores X-SAP-LogonToken."""

    def fetch_report_data(self, report_id: str) -> pd.DataFrame:
        """
        GET REPORT_ENDPOINT, parses JSON/XML response into DataFrame.
        Raises ReportNotFoundError on 404.
        Raises BOAPIError on other errors.
        """

    def logout(self) -> None:
        """POST /biprws/logoff to release session."""
```

---

### 5.6 Report_Generator (`reporting/generator.py`)

Responsibilities: render a self-contained HTML report from `TestSuiteResult` using Jinja2.

```python
class ReportGenerator:
    TEMPLATE_NAME = "report.html.j2"
    DEFAULT_OUTPUT_DIR = "./reports"
    MAX_MISMATCH_DISPLAY = 100   # configurable

    def __init__(
        self,
        output_dir: str = DEFAULT_OUTPUT_DIR,
        max_mismatch_display: int = MAX_MISMATCH_DISPLAY,
    ):
        self._output_dir = output_dir
        loader = FileSystemLoader(
            Path(__file__).parent / "templates"
        )
        self._jinja_env = Environment(loader=loader, autoescape=True)

    def generate(self, suite_result: TestSuiteResult) -> str:
        """
        Renders template with suite_result context.
        Creates output_dir if missing (raises ReportOutputError on failure).
        Writes file to {output_dir}/report_{timestamp}.html.
        Returns the file path written.
        """
```

**Template structure (`report.html.j2`):**
```
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>ETL Test Report - {{ suite.started_at }}</title>
  <style>
    /* Inline CSS - no external dependencies */
    .badge-pass  { background: #28a745; color: white; ... }
    .badge-fail  { background: #dc3545; color: white; ... }
    .badge-amber { background: #ffc107; color: black; ... }
    .collapsible { cursor: pointer; ... }
    /* table styles, search input, layout */
  </style>
</head>
<body>
  {% block header %}   ... run metadata, env names, timestamp  {% endblock %}
  {% block summary %}  ... stat cards: Total/Passed/Failed      {% endblock %}
  {% block jobs %}     ... searchable jobs table                {% endblock %}
  {% block recon %}    ... reconciliation results table         {% endblock %}
  {% block mismatches %}
    {% for result in suite.reconciliation_results %}
      {% if result.mismatches %}
        <details><summary>{{ result.query_name }} mismatches</summary>
          <table>...</table>
        </details>
      {% endif %}
    {% endfor %}
  {% endblock %}
  <script>
    /* Inline vanilla JS for table search/filter */
    function filterTable(inputId, tableId) { ... }
  </script>
</body>
</html>
```

---

### 5.7 ConsoleRenderer (`reporting/console.py`)

Renders a summary table to stdout when `--output-format console` is used. Uses `tabulate` for formatting.

```python
class ConsoleRenderer:
    def render(self, suite_result: TestSuiteResult) -> None:
        """Prints summary stats + jobs table + reconciliation table to stdout."""

    def render_reconciliation(self, result: ReconciliationResult) -> None:
        """Prints a single reconciliation result with mismatch rows."""
```

---

### 5.8 TestRunner (`runner/test_runner.py`)

Responsibilities: orchestrate full-suite execution, manage `TestCaseState` transitions, persist results after each test, enforce fail-fast policy.

```python
class TestRunner:
    def __init__(
        self,
        suite_config: dict,
        source_env: EnvironmentConfig,
        target_env: EnvironmentConfig,
        repository: AbstractTestRunRepository,
        fail_fast: bool = False,
    ): ...

    def run(self) -> TestSuiteResult:
        """
        State machine per test case:

          PENDING → RUNNING → PASSED
                            → FAILED
                            → ERROR   (exception during execution)
                    ↓
                  SKIPPED   (test filtered out before starting)

        Steps:
        1. Build TestSuiteResult with all test cases in PENDING state
        2. For each test case in order:
           a. If filtered out → set SKIPPED, continue
           b. Set RUNNING, record started_at
           c. Execute the component (Automic / Reconciliation / BO)
           d. On success → set PASSED or FAILED based on result
           e. On exception → set ERROR, record error_message, log traceback
           f. Record completed_at
           g. Call repository.checkpoint(suite_result) to persist interim state
           h. If fail_fast=True and status is FAILED or ERROR → break
        3. Set suite completed_at, call repository.save(suite_result)
        4. Return TestSuiteResult
        """
```

**State transition diagram:**

```
                    ┌─────────┐
                    │ PENDING │
                    └────┬────┘
          filtered?      │ not filtered
              ↓          ▼
          SKIPPED    ┌─────────┐
                     │ RUNNING │
                     └────┬────┘
              ┌───────────┼───────────┐
              ▼           ▼           ▼
           PASSED      FAILED       ERROR
         (no issues) (mismatches/ (exception
                      job failure)  raised)
```

**Fail-fast vs collect-all:**

```python
# In config/sample_config.yaml:
runner:
  fail_fast: false   # collect all results before reporting (default)
                     # set true to abort on first FAILED or ERROR
```

---

### 5.9 TestRunRepository (`repository/`)

The repository is the persistence layer for `TestSuiteResult` objects. All three backends implement the same abstract interface, and the choice is controlled by a single config key.

#### Abstract Interface (`repository/base.py`)

```python
from abc import ABC, abstractmethod

class AbstractTestRunRepository(ABC):

    @abstractmethod
    def save(self, result: TestSuiteResult) -> None:
        """Persist a completed TestSuiteResult. Overwrites if run_id exists."""

    @abstractmethod
    def checkpoint(self, result: TestSuiteResult) -> None:
        """
        Persist an in-progress TestSuiteResult (partial results).
        Called after each test case completes for crash recovery.
        Implementations may batch or throttle writes for performance.
        """

    @abstractmethod
    def get_run(self, run_id: str) -> TestSuiteResult | None:
        """Retrieve a single run by its run_id. Returns None if not found."""

    @abstractmethod
    def load_history(
        self,
        limit: int = 50,
        source_env: str | None = None,
        target_env: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
    ) -> list[TestSuiteResult]:
        """
        Return up to `limit` past runs ordered by started_at DESC.
        Optional filters: source_env, target_env, status, since (datetime).
        """

    @abstractmethod
    def delete_run(self, run_id: str) -> bool:
        """Delete a run record. Returns True if deleted, False if not found."""
```

#### Backend 1: JSON (`repository/json_repo.py`)

- Storage: one NDJSON file (newline-delimited JSON), one line per `TestSuiteResult`
- Serialisation: `dataclasses.asdict()` + `json.dumps()` with a custom encoder for `datetime` and `TestStatus` enum
- `checkpoint()`: rewrites the entire file (small overhead, acceptable for <10k runs)
- `load_history()`: reads all lines, deserialises, filters in memory, sorts, slices
- Best for: zero-dependency setups, file-based CI artefact storage, sharing run history as a flat file

```python
class JsonTestRunRepository(AbstractTestRunRepository):
    def __init__(self, file_path: str = "./run_history/history.ndjson"):
        self._path = Path(file_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
```

#### Backend 2: SQLite (`repository/sqlite_repo.py`)

- Storage: single `.db` file, no server required
- Schema: two tables — `test_runs` (one row per run) and `test_cases` (one row per test case, FK to run)
- Uses Python's built-in `sqlite3` module — no extra dependency
- `checkpoint()`: upserts the current run and all its test cases in a single transaction
- `load_history()`: parameterised SQL query with WHERE / ORDER BY / LIMIT
- Best for: local development, teams that want SQL query access to history without a server

```python
class SqliteTestRunRepository(AbstractTestRunRepository):
    def __init__(self, db_path: str = "./run_history/history.db"):
        self._db_path = db_path
        self._init_schema()

    def _init_schema(self) -> None:
        """
        CREATE TABLE IF NOT EXISTS test_runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT,
            completed_at TEXT,
            source_env TEXT,
            target_env TEXT,
            framework_version TEXT,
            fail_fast INTEGER,
            total_passed INTEGER,
            total_failed INTEGER,
            total_skipped INTEGER,
            exit_code INTEGER,
            raw_json TEXT    -- full serialised TestSuiteResult for lossless retrieval
        );
        CREATE TABLE IF NOT EXISTS test_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT REFERENCES test_runs(run_id),
            name TEXT,
            test_type TEXT,
            status TEXT,
            started_at TEXT,
            completed_at TEXT,
            duration_seconds REAL,
            error_message TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_runs_started ON test_runs(started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_cases_run_id ON test_cases(run_id);
        """
```

#### Backend 3: DuckDB (`repository/duckdb_repo.py`)

- Storage: single `.duckdb` file (or in-memory for testing)
- Adds `duckdb` as an optional dependency (`pip install etl-framework[duckdb]`)
- Schema mirrors SQLite but stored as Parquet-backed columnar format internally
- Key advantage over SQLite: fast analytical queries — e.g. "trend of mismatch counts over 30 days" runs as a vectorised scan
- `load_history()` supports pandas DataFrame output for direct charting
- Best for: teams wanting to run trend analysis or export history to BI tools

```python
class DuckDbTestRunRepository(AbstractTestRunRepository):
    def __init__(self, db_path: str = "./run_history/history.duckdb"):
        import duckdb
        self._conn = duckdb.connect(db_path)
        self._init_schema()

    def load_history_as_dataframe(self, **filters) -> pd.DataFrame:
        """Returns history as a DataFrame for downstream analysis or charting."""
```

#### Factory (`repository/factory.py`)

```python
def build_repository(config: dict) -> AbstractTestRunRepository:
    """
    Reads config["repository"]["backend"]:
      "json"   → JsonTestRunRepository(config["repository"]["path"])
      "sqlite" → SqliteTestRunRepository(config["repository"]["path"])
      "duckdb" → DuckDbTestRunRepository(config["repository"]["path"])
    Defaults to "sqlite" if not specified.
    """
```

**Config example:**

```yaml
repository:
  backend: "sqlite"          # json | sqlite | duckdb
  path: "./run_history/etl_runs.db"
  checkpoint_every_n: 1      # persist after every N test cases (1 = after each)
```

---

### 5.10 Ad-Hoc Router (`run_tests.py`)

The CLI entry point uses `argparse` with flag-based routing. No subcommands — flags control execution scope.

```python
# Execution mode detection
def _resolve_execution_mode(args) -> ExecutionMode:
    """
    Returns one of: FULL_SUITE | ADHOC_SQL | ADHOC_BO | ADHOC_JOB | FILTERED_SUITE
    Priority: adhoc inline args → type flags → full suite
    """

# Component factory for standalone use
def build_db_engine(env_config: EnvironmentConfig) -> DBEngine: ...
def build_reconciler(src: DBEngine, tgt: DBEngine, **kwargs) -> ReconciliationEngine: ...
def build_automic_client(env_config: EnvironmentConfig) -> AutomicClient: ...
def build_bo_validator(src_cfg, tgt_cfg, mode) -> SAPBOValidator: ...

# Main routing
def main():
    args = parse_args()
    mode = _resolve_execution_mode(args)

    if mode == ExecutionMode.ADHOC_SQL:
        _run_adhoc_sql(args)
    elif mode == ExecutionMode.ADHOC_BO:
        _run_adhoc_bo(args)
    elif mode == ExecutionMode.ADHOC_JOB:
        _run_adhoc_job(args)
    elif mode == ExecutionMode.FILTERED_SUITE:
        _run_filtered_suite(args)
    else:
        _run_full_suite(args)
```

**Isolation guarantee:** each `_run_adhoc_*` function only instantiates the components it requires. No DB connections are opened for BO-only runs, no Automic sessions for SQL-only runs.

**CLI argument surface:**
```
run_tests.py
  --config PATH          config YAML path (required except pure adhoc modes)
  --env-source NAME      source env name (default: first env in config)
  --env-target NAME      target env name (default: second env in config)
  --output-dir PATH      HTML report output directory
  --output-format        html | console  (default: html)

  # Type filters (can be combined → union)
  --run-jobs             only Automic checks
  --run-sql              only DB reconciliation
  --run-bo               only SAP BO validation

  # Inline ad-hoc (no test suite YAML needed)
  --query "SELECT ..."   inline SQL for ad-hoc reconciliation
  --query-file PATH      path to .sql file for ad-hoc reconciliation
  --report-id ID         SAP BO report identifier for ad-hoc BO comparison
  --run-id ID            Automic Run_ID for ad-hoc job check
  --job-name NAME        Automic Job_Name for ad-hoc job check
  --test-name NAME       run only the named test from the suite

  # Config-free mode (--run-sql without --config)
  --env-source-url CONN  SQLAlchemy connection string for source
  --env-target-url CONN  SQLAlchemy connection string for target
```

---

## Error Handling

```
ETLFrameworkError (base, in exceptions.py)
├── ConfigurationError
│     fields: message, field_name=None, file_path=None
├── DatabaseConnectionError
│     fields: env_name, host, port, db_name (no password)
├── QueryExecutionError
│     fields: env_name, query, original_error
├── AutomicAPIError
│     fields: http_status, response_body, url
├── AutomicTimeoutError
│     fields: url, attempts, timeout_seconds
├── ReportNotFoundError
│     fields: report_id, env_name
├── BOAPIError
│     fields: report_id, http_status, response_body
├── ReportOutputError
│     fields: target_path, original_os_error
└── RepositoryError
      fields: backend, operation, original_error
```

All custom exceptions accept a human-readable `message` as first arg and carry structured fields for programmatic inspection.

---

## 7. Logging Design

### Logger naming

```
etl_framework.config.loader
etl_framework.db.engine
etl_framework.reconciliation.engine
etl_framework.automic.client
etl_framework.sap_bo.validator
etl_framework.reporting.generator
```

### Configuration (`utils/logging.py`)

```python
def configure_logging(
    level: str = "INFO",
    log_file: str = "./logs/etl_framework.log",
) -> None:
    """
    Sets up root logger 'etl_framework' with:
    - StreamHandler → stdout (always active)
    - RotatingFileHandler → log_file
        maxBytes=10 * 1024 * 1024  (10 MB)
        backupCount=5
    Both handlers are always created; neither can be suppressed via config.
    """

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
```

### What gets logged at each level

| Level   | Examples |
|---------|----------|
| DEBUG   | SQL query text, API request URLs, response payload sizes, row counts |
| INFO    | Component start/stop, reconciliation summary (X rows, Y mismatches) |
| WARNING | Combined flag selection, row limit hit, deprecated config keys |
| ERROR   | All handled exceptions with full traceback |

---

## Testing Strategy

Location: `tests/property/test_reconciliation_properties.py`

Library: `hypothesis`

```python
from hypothesis import given, settings
from hypothesis import strategies as st

@given(st.data())
def test_round_trip_symmetry(data):
    """
    For any pair of DataFrames with identical schemas:
    reconcile(source, target).missing_in_target
    == reconcile(target, source).missing_in_source
    """
    df_a = data.draw(dataframe_strategy())
    df_b = data.draw(dataframe_strategy())
    result_ab = engine.compare_dataframes(df_a, df_b, key_cols)
    result_ba = engine.compare_dataframes(df_b, df_a, key_cols)
    assert result_ab.missing_in_target_count == result_ba.missing_in_source_count
    assert result_ab.missing_in_source_count == result_ba.missing_in_target_count
```

### Property 2: Idempotency

```python
@given(st.data())
def test_idempotency(data):
    """
    Running reconciliation twice on the same unchanged DataFrames
    returns identical ReconciliationResult (same counts and mismatches).
    """
    df_a = data.draw(dataframe_strategy())
    df_b = data.draw(dataframe_strategy())
    result_1 = engine.compare_dataframes(df_a, df_b, key_cols)
    result_2 = engine.compare_dataframes(df_a, df_b, key_cols)
    assert result_1.missing_in_target_count == result_2.missing_in_target_count
    assert result_1.value_mismatch_count == result_2.value_mismatch_count
```

### Property 3: Zero Mismatches on Identical Data

```python
@given(st.data())
def test_identical_data_zero_mismatches(data):
    df = data.draw(dataframe_strategy())
    result = engine.compare_dataframes(df, df.copy(), key_cols)
    assert result.total_issues == 0
    assert result.status == "PASSED"
```

---

## 9. Sample Configuration YAML

### `config/sample_config.yaml`

```yaml
framework:
  version: "1.0.0"
  log_level: "INFO"
  log_file: "./logs/etl_framework.log"

runner:
  fail_fast: false             # true = abort on first FAILED/ERROR

repository:
  backend: "sqlite"            # json | sqlite | duckdb
  path: "./run_history/etl_runs.db"
  checkpoint_every_n: 1        # persist after every N test cases

environments:
  dev:
    # SQL Server connection
    db_host: "dev-sqlserver.example.com"
    db_port: 1433
    db_name: "ETL_DEV"
    db_user: "etl_user_dev"
    db_password: "${DEV_DB_PASSWORD}"
    db_driver: "ODBC Driver 17 for SQL Server"
    db_pool_size: 5
    db_pool_overflow: 10
    db_connect_timeout: 15
    # Automic
    automic_url: "https://automic-dev.example.com"
    automic_user: "automic_svc_dev"
    automic_password: "${DEV_AUTOMIC_PASSWORD}"
    automic_timeout: 30
    automic_max_retries: 3
    # SAP BO
    bo_url: "https://bo-dev.example.com:8080"
    bo_user: "bo_svc_dev"
    bo_password: "${DEV_BO_PASSWORD}"

  qa:
    db_host: "qa-sqlserver.example.com"
    db_port: 1433
    db_name: "ETL_QA"
    db_user: "etl_user_qa"
    db_password: "${QA_DB_PASSWORD}"
    db_driver: "ODBC Driver 17 for SQL Server"
    db_pool_size: 5
    db_pool_overflow: 10
    db_connect_timeout: 15
    automic_url: "https://automic-qa.example.com"
    automic_user: "automic_svc_qa"
    automic_password: "${QA_AUTOMIC_PASSWORD}"
    automic_timeout: 30
    automic_max_retries: 3
    bo_url: "https://bo-qa.example.com:8080"
    bo_user: "bo_svc_qa"
    bo_password: "${QA_BO_PASSWORD}"
```

### `config/sample_tests.yaml`

```yaml
test_suite:
  source_env: "dev"
  target_env: "qa"

  automic_jobs:
    - name: "check_nightly_etl_load"
      job_name: "ETL_NIGHTLY_LOAD"
    - name: "check_daily_transform"
      run_id: "RUN_20240101_001"

  sql_reconciliations:
    - name: "orders_table_recon"
      query: |
        SELECT order_id, customer_id, order_date, total_amount
        FROM dbo.Orders
        WHERE order_date >= CAST(GETDATE()-1 AS DATE)
      key_columns: ["order_id"]
      exclude_columns: ["created_at", "updated_at"]
      float_tolerance: 0.01

    - name: "customer_dim_recon"
      query_file: "./queries/customer_dim.sql"
      key_columns: ["customer_id"]
      exclude_columns: ["last_login_at"]

  bo_reports:
    - name: "sales_summary_report"
      report_id: "RPT_SALES_SUMMARY_001"
      mode: "sql"
      query: |
        SELECT region, product_category, SUM(revenue) AS total_revenue
        FROM dbo.SalesFact
        GROUP BY region, product_category
      key_columns: ["region", "product_category"]
      float_tolerance: 0.01

report:
  output_dir: "./reports"
  output_format: "html"
  max_mismatch_display: 100
```

---

## 10. Dependency Summary

| Package | Version | Purpose |
|---------|---------|---------|
| `pandas` | >=2.0 | Data comparison and reconciliation |
| `sqlalchemy` | >=2.0 | Database connection management |
| `pyodbc` | >=4.0 | SQL Server ODBC driver interface |
| `requests` | >=2.31 | Automic and SAP BO REST API calls |
| `tenacity` | >=8.2 | Retry logic with exponential backoff |
| `jinja2` | >=3.1 | HTML report template rendering |
| `pyyaml` | >=6.0 | Configuration file parsing |
| `numpy` | >=1.24 | Float tolerance comparison |
| `tabulate` | >=0.9 | Console output formatting |
| `hypothesis` | >=6.0 | Property-based testing |
| `pytest` | >=7.0 | Test runner |
| `sqlite3` | stdlib | SQLite repository backend (no install needed) |
| `duckdb` | >=0.10 | DuckDB repository backend (optional extra) |

---

## Correctness Properties

### Property 1: Round-Trip Symmetry

**Validates: Requirements 4.11**

For any pair of DataFrames with the same schema:
`reconcile(A, B).missing_in_target == reconcile(B, A).missing_in_source`

This ensures the engine has no directional bias — swapping source and target produces mirror-image missing counts.

### Property 2: Idempotency

**Validates: Requirements 4.1, 4.8, 4.9**

Running reconciliation twice on the same unchanged DataFrames must return identical `ReconciliationResult` objects (same counts, same mismatch records). This guarantees the engine is stateless and produces stable output regardless of invocation count.

### Property 3: Zero Mismatches on Identical Data

**Validates: Requirements 4.9**

Given any DataFrame `df`, `reconcile(df, df.copy())` must return `total_issues == 0` and `status == "PASSED"`. This is the baseline correctness invariant for the entire comparison pipeline.

### Implementation

All three properties are encoded as `hypothesis`-driven tests in `tests/property/test_reconciliation_properties.py`. The `dataframe_strategy()` helper generates random DataFrames with SQL Server-compatible dtypes (int64, float64, str, datetime64) to exercise the comparison logic across a wide input space.
