# ETL & SAP BO Testing Framework — Improvement Design

**Date:** 2026-06-13  
**Scope:** Industry best-practice gaps identified across Scale/Performance, Data Quality, and Observability  
**Based on:** `.kiro/specs/etl-sapbo-testing-framework/requirements.md` + `design.md`

---

## 1. Overview

The base framework design is architecturally sound (layered, modular, property-tested). This document captures 13 improvement areas identified against industry standards and specifies the changes required to requirements.md and design.md to address each one.

All improvements are **additive** — none require redesigning existing components.

---

## 2. Scale & Performance Improvements

### 2.1 Chunked Reconciliation (Critical)

**Problem:** `ReconciliationEngine` performs a full in-memory `pd.merge(..., how="outer")`. On large tables (>500k rows), this will exhaust memory. `mismatch_row_limit` caps stored records but not the in-memory merge itself.

**Solution:** Two-phase reconciliation strategy:

**Phase 1 — DB-side hash check:**  
Before pulling any rows, execute a lightweight hash query on both environments:
```sql
SELECT COUNT(*) AS row_count,
       CHECKSUM_AGG(CHECKSUM(*)) AS data_hash
FROM ({user_query}) AS t
```
If `row_count` and `data_hash` match → return `ReconciliationResult` with zero mismatches immediately, no row pull.

**Phase 2 — Keyset-chunked pull (only when hashes differ):**  
Pull and compare in pages of `chunk_size` rows, using `ORDER BY {key_columns} OFFSET x FETCH NEXT chunk_size ROWS ONLY`. Aggregate counts across chunks. Never hold more than `chunk_size` rows in memory from either side simultaneously.

**Config addition:**
```yaml
reconciliation:
  chunk_size: 100000          # rows per chunk; 0 = no chunking (legacy behaviour)
  use_hash_precheck: true     # skip row pull when hashes match
```

**Requirements change:** Add Requirement 4.12:
> THE Reconciliation_Engine SHALL support a configurable `chunk_size`. WHEN `chunk_size` > 0 and the query result set exceeds `chunk_size` rows, THE Reconciliation_Engine SHALL compare data in sequential chunks and aggregate mismatch counts across all chunks without materialising the full merged DataFrame in memory.

**Design change:** `ReconciliationEngine.reconcile()` gains a `_reconcile_chunked()` private method. A `HashChecker` helper class executes the hash query via `DBEngine.execute_query()`.

---

### 2.2 Parallel Test Execution (Important)

**Problem:** `TestRunner.run()` is a sequential for-loop. Ten independent reconciliations run serially.

**Solution:** `concurrent.futures.ThreadPoolExecutor` for I/O-bound test cases. All `DBEngine` instances use SQLAlchemy connection pools (thread-safe). A `threading.Lock` guards `checkpoint()` calls.

**Config addition:**
```yaml
runner:
  max_workers: 4    # 1 = sequential (default, backward-compatible)
  fail_fast: false
```

**Requirements change:** Add Requirement 8.8:
> THE Run_Script SHALL accept a `--max-workers` argument. WHEN `--max-workers` > 1, THE TestRunner SHALL execute independent test cases concurrently using a thread pool of that size. WHEN `fail_fast` is true and a failure is detected, THE TestRunner SHALL cancel pending tasks and not start new ones.

**Design change:** `TestRunner.run()` branches on `max_workers`. The `checkpoint()` call is wrapped in a `threading.Lock`. `TestCaseState` status transitions use atomic updates (lock-protected dict).

---

### 2.3 Polars Backend Option (Nice-to-Have)

**Problem:** Pandas is the only comparison backend. For datasets above ~5M rows, Polars is 5–10x faster with lower memory overhead.

**Solution:** Abstract the comparison backend behind a `ComparisonBackend` protocol. Pandas is default. Polars is an optional extra.

**Config addition:**
```yaml
reconciliation:
  comparison_backend: "pandas"    # pandas | polars
```

**Requirements change:** Add Requirement 4.13:
> THE Reconciliation_Engine SHALL support a configurable `comparison_backend`. WHEN set to `polars`, the engine SHALL use the Polars library for DataFrame operations. WHEN set to `pandas` (default), existing behaviour is preserved.

**Design change:** New `reconciliation/backends/` subpackage with `PandasBackend` and `PolarsBackend` implementing a `ComparisonBackend` protocol. `ReconciliationEngine.__init__` selects the backend from config.

---

### 2.4 Per-Test Duration SLOs (Nice-to-Have)

**Problem:** `duration_seconds` is captured but never acted on. Slow reconciliations are invisible until a human reads the timestamp.

**Solution:** Optional `max_duration_seconds` per test case. Exceeding it marks the result `SLOW` (new status, rendered amber) in the HTML report.

**Config addition (per test in `sample_tests.yaml`):**
```yaml
sql_reconciliations:
  - name: "orders_table_recon"
    query: "..."
    key_columns: ["order_id"]
    max_duration_seconds: 120    # optional; omit to disable
```

**Requirements change:** Add Requirement 4.14:
> WHEN a test case definition includes a `max_duration_seconds` value and the reconciliation duration exceeds that value, THE Reconciliation_Engine SHALL set `status` to `SLOW` on the returned `ReconciliationResult`. THE Report_Generator SHALL render `SLOW` results with amber colour-coding.

**Design change:** `TestStatus` enum gains `SLOW = "SLOW"`. `ReconciliationEngine.reconcile()` checks elapsed time post-execution. `ReportGenerator` template handles `SLOW` badge.

---

## 3. Data Quality & Schema Drift Improvements

### 3.1 Schema Validation Before Reconciliation (Critical)

**Problem:** If the target environment is missing a column (schema drift after deployment), the merge silently fails or produces misleading results. No acceptance criterion in Req 4 covers this.

**Solution:** Before the merge, compare `df_source.columns` vs `df_target.columns`. Surface the diff as a `schema_diff` field in `ReconciliationResult` and as a new `SchemaValidationError`.

**Behaviour options (configurable):**
- `schema_mismatch_policy: "warn"` — log WARNING, compare on common columns only, note diff in result
- `schema_mismatch_policy: "error"` — raise `SchemaValidationError`, mark test as `ERROR`

**Requirements change:** Add Requirement 4.15:
> WHEN the column sets of the source and target DataFrames differ, THE Reconciliation_Engine SHALL record the differing column names in a `schema_diff` field on the `ReconciliationResult`. WHEN `schema_mismatch_policy` is `"error"`, THE Reconciliation_Engine SHALL raise a `SchemaValidationError` identifying the missing and extra columns. WHEN `schema_mismatch_policy` is `"warn"` (default), THE Reconciliation_Engine SHALL proceed using only the common columns and log a WARNING.

**Design change:**
- New `SchemaValidationError` in `exceptions.py`: fields `missing_in_target: list[str]`, `extra_in_target: list[str]`
- `ReconciliationResult` gains `schema_diff: dict[str, list[str]] | None` field
- `ReconciliationEngine._validate_schemas(df_source, df_target)` private method called before merge
- HTML report: schema diff displayed as a WARNING banner on affected test rows

---

### 3.2 SQL Server Type Normalisation (Critical)

**Problem:** `_values_match` branches only on `is_float`. Several SQL Server types produce silent wrong comparison results:

| SQL Server type | Pandas representation | Failure mode |
|---|---|---|
| `DATETIME2` with timezone | `datetime64[ns, UTC]` vs `datetime64[ns]` | TZ-aware != TZ-naive raises or returns False |
| `DECIMAL(p,s)` | Python `Decimal` | `numpy.isclose(Decimal, Decimal)` raises `TypeError` |
| `UNIQUEIDENTIFIER` | Uppercase string | Case-sensitive mismatch if target normalises to lowercase |
| `BIT` | `True`/`1` depending on pyodbc version | `True != 1` without coercion |
| `VARBINARY` | `bytes` | NaN detection logic breaks |

**Solution:** A `TypeNormalizer` step runs immediately after DataFrame pull, before the merge. Applies per-column canonical transformations:
- `datetime64` (any tz) → `datetime64[ns, UTC]`
- `Decimal` → `float64` (logged as a precision note)
- `str` UUID → `.upper()` normalisation
- `bool`/`int` BIT → `bool`
- `bytes` → hex string for display

**Requirements change:** Add Requirement 4.16:
> THE Reconciliation_Engine SHALL apply type normalisation to both DataFrames before comparison. Normalisation SHALL convert timezone-aware and timezone-naive datetimes to UTC, Decimal types to float64, UUID strings to uppercase, and BIT values to bool. The normalisation step SHALL be logged at DEBUG level per column.

**Design change:** New `reconciliation/normalizer.py` with `TypeNormalizer.normalize(df: pd.DataFrame) -> pd.DataFrame`. Called inside `reconcile()` after both DataFrames are fetched. Normalisation rules are data-driven (a dict mapping dtype patterns to transform functions) so they're extensible without modifying `ReconciliationEngine`.

---

### 3.3 Pydantic v2 Config Models (Important)

**Problem:** `EnvironmentConfig` is a plain `@dataclass`. Validation in `ConfigLoader._validate_env_config()` only checks field presence. Type errors (e.g., `db_pool_size: "five"`) are silently accepted and fail late at engine creation.

**Solution:** Replace `@dataclass` with `pydantic.BaseModel`. Keep all existing fields; add field validators for:
- `db_port`: `1 <= v <= 65535`
- `db_pool_size`: `>= 1`
- `db_pool_overflow`: `>= 0`
- `automic_max_retries`: `>= 0`
- `bo_timeout`: `> 0`

**Requirements change:** Req 1.3 amended:
> WHEN a required field is absent OR has an invalid type or value, THE Config_Loader SHALL raise a `ConfigurationError` identifying the field name and the validation failure reason.

**Design change:**
- `config/models.py`: `EnvironmentConfig(BaseModel)` with `model_config = ConfigDict(str_strip_whitespace=True)`
- `ConfigLoader.load()`: replace `_validate_env_config()` with `EnvironmentConfig.model_validate(raw_env_dict)`
- `pydantic>=2.0` added to dependency table
- All `dataclasses.asdict()` calls in repository serialisation replaced with `.model_dump()`

---

### 3.4 Explicit NULL Semantics (Nice-to-Have)

**Problem:** `NaN == NaN → True` is hardcoded. Some consumers expect ANSI SQL NULL semantics (`NULL != NULL`).

**Solution:** `null_equals_null: bool = True` config option on `ReconciliationEngine`. When `False`, two `NULL`/`NaN` values in the same cell are recorded as a `value_mismatch`.

**Requirements change:** Add Requirement 4.17:
> THE Reconciliation_Engine SHALL support a configurable `null_equals_null` flag (default: `true`). WHEN `false`, two NULL values in matching row/column positions SHALL be recorded as a `value_mismatch`.

---

## 4. Observability Improvements

### 4.1 Structured JSON Logging (Critical for CI/CD)

**Problem:** Log format `"%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"` is human-readable but unparseable by log aggregators (Datadog, Splunk, ELK, CloudWatch).

**Solution:** Dual-format logging. Console handler: text format (current). File handler: JSON format via `python-json-logger`. Controlled by `log_format` config key.

**JSON log record shape:**
```json
{
  "timestamp": "2026-06-13T17:30:00Z",
  "level": "INFO",
  "logger": "etl_framework.reconciliation.engine",
  "message": "Reconciliation complete",
  "run_id": "a1b2c3d4-...",
  "query_name": "orders_table_recon",
  "source_row_count": 42310,
  "target_row_count": 42308,
  "mismatch_count": 2
}
```

**Config addition:**
```yaml
framework:
  log_level: "INFO"
  log_format: "text"     # text | json
  log_file: "./logs/etl_framework.log"
```

**Requirements change:** Req 7.2 amended:
> THE Framework SHALL support a configurable `log_format` of `"text"` (default, human-readable) or `"json"` (newline-delimited JSON objects). WHEN `json`, the file handler SHALL emit one JSON object per log record including all structured fields. The console handler SHALL always use text format for readability.

**Design change:**
- `utils/logging.py`: import `pythonjsonlogger.jsonlogger.JsonFormatter` when `log_format == "json"`
- `python-json-logger>=2.0` added to dependency table (optional extra: `pip install etl-framework[json-logging]`)

---

### 4.2 run_id Correlation in All Log Messages (Critical)

**Problem:** `run_id` is a UUID on `TestSuiteResult` but is absent from all log messages. Diagnosing a specific run in a shared log file requires timestamp-based correlation, which breaks for concurrent runs.

**Solution:** Python `contextvars.ContextVar` holds the active `run_id`. A `RunContextFilter` reads it and injects it into every `LogRecord`.

**Implementation:**
```python
# utils/context.py
from contextvars import ContextVar
_run_id_var: ContextVar[str] = ContextVar("run_id", default="")

def set_run_id(run_id: str) -> None:
    _run_id_var.set(run_id)

# utils/logging.py
class RunContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id_var.get()
        return True
```

`TestRunner.run()` calls `set_run_id(suite_result.run_id)` before the test loop. Each `_run_adhoc_*` function calls it with a freshly generated UUID.

**Requirements change:** Add Requirement 7.6:
> THE Framework SHALL inject the active `run_id` into every log message. FOR full-suite runs, `run_id` SHALL be set before the first test executes. FOR ad-hoc runs, a UUID SHALL be generated and set at the start of the ad-hoc execution.

**Design change:**
- New `utils/context.py` with `ContextVar` and `set_run_id()`
- `RunContextFilter` added to all handlers in `configure_logging()`
- Log format updated: `"%(asctime)s | %(levelname)-8s | %(run_id)s | %(name)s | %(message)s"`

---

### 4.3 Metrics Emission (Important)

**Problem:** No operational metrics are emitted. Teams cannot dashboard or alert on reconciliation trends without parsing HTML reports.

**Solution (phased):**

**Phase 1 — Metrics JSON sidecar (zero new deps):**  
After each run, write `metrics_<run_id>.json` alongside the HTML report:
```json
{
  "run_id": "...",
  "started_at": "...",
  "duration_seconds": 45.2,
  "total_tests": 10,
  "passed": 8,
  "failed": 2,
  "reconciliations": [
    {
      "name": "orders_table_recon",
      "source_rows": 42310,
      "target_rows": 42308,
      "mismatches": 2,
      "duration_seconds": 12.1
    }
  ]
}
```

**Phase 2 — Prometheus push gateway (optional extra):**  
`prometheus_client.push_to_gateway()` after run completion. Metrics: `etl_reconciliation_duration_seconds` (histogram), `etl_reconciliation_mismatches_total` (counter), `etl_job_status_total` (counter by status label).

**Config addition:**
```yaml
metrics:
  enabled: true
  format: "json"                           # json | prometheus
  output_dir: "./reports"                  # for json sidecar
  prometheus_gateway: ""                   # for prometheus push; empty = disabled
  prometheus_job_name: "etl_framework"
```

**Requirements change:** Add Requirement 7.7:
> WHEN `metrics.enabled` is true, THE Framework SHALL write a metrics summary file after each run. WHEN `metrics.format` is `"json"`, a JSON sidecar file SHALL be written alongside the HTML report. WHEN `metrics.format` is `"prometheus"` and `prometheus_gateway` is configured, metrics SHALL be pushed to the configured Prometheus push gateway.

---

### 4.4 OpenTelemetry Tracing (Nice-to-Have)

**Problem:** No distributed traces — diagnosing which component is slow requires log scraping.

**Solution:** OTel SDK instrumentation as an optional extra. Each component method emits a span. Zero overhead when OTel is not configured.

**Span tree example:**
```
TestRunner.run [run_id=abc123]
  └─ ReconciliationEngine.reconcile [query_name=orders_table_recon]
       ├─ DBEngine.execute_query [env=dev, rows=42310]
       ├─ DBEngine.execute_query [env=qa, rows=42308]
       └─ _merge_and_compare [mismatches=2]
  └─ AutomicClient.get_status_by_job_name [job=ETL_NIGHTLY_LOAD]
       └─ _request [url=..., http.status=200]
```

**Config addition:**
```yaml
tracing:
  enabled: false
  exporter: "otlp"         # otlp | jaeger | console
  endpoint: ""             # OTLP collector endpoint
```

**Requirements change:** Add Requirement 7.8:
> WHEN `tracing.enabled` is true, THE Framework SHALL emit OpenTelemetry spans for each major operation (test runner, reconciliation, DB query, API request). Spans SHALL include the `run_id` as a span attribute.

**Design change:**
- New `utils/tracing.py` with `get_tracer()` returning a no-op tracer when disabled
- `opentelemetry-sdk` and `opentelemetry-exporter-otlp` added as optional extras

---

### 4.5 Health Check / Connectivity Probe (Important)

**Problem:** No way to verify environment connectivity without running a full test suite. First failure appears mid-run.

**Solution:** `--health-check` flag. When set, `run_tests.py` attempts to connect to all configured environments (DB, Automic, SAP BO) and exits 0 (all reachable) or 1 (any unreachable). No test data touched.

**Health check output (console):**
```
[OK]   dev  DB       dev-sqlserver.example.com:1433
[OK]   dev  Automic  https://automic-dev.example.com
[FAIL] qa   DB       qa-sqlserver.example.com:1433 — connection timeout after 15s
[OK]   qa   Automic  https://automic-qa.example.com
```

**Requirements change:** Add Requirement 8.9:
> THE Run_Script SHALL accept a `--health-check` flag. WHEN provided, THE Run_Script SHALL attempt to establish a connection to every configured environment endpoint (database, Automic API, SAP BO API) and print a per-endpoint status line. THE Run_Script SHALL exit with return code `0` if all connections succeed, or `1` if any connection fails.

**Design change:**
- `runner/health.py`: `HealthChecker` class with `check_all(env_configs) -> list[HealthCheckResult]`
- `HealthCheckResult`: `env_name`, `service`, `endpoint`, `ok: bool`, `error: str | None`, `latency_ms: float`
- `run_tests.py`: `--health-check` flag routes to `HealthChecker` before any component initialisation

---

## 5. Exception Hierarchy Updates

Add to `exceptions.py`:
```
ETLFrameworkError
├── SchemaValidationError          # new — Req 4.15
│     fields: query_name, missing_in_target: list[str], extra_in_target: list[str]
└── RepositoryError                # was already in design diagram, make explicit
      fields: backend, operation, original_error
```

`TestStatus` enum additions:
```python
SLOW  = "SLOW"   # completed but exceeded max_duration_seconds
```

---

## 6. Dependency Table Updates

| Package | Version | Purpose | Type |
|---|---|---|---|
| `pydantic` | `>=2.0` | Config model validation | Required |
| `python-json-logger` | `>=2.0` | Structured JSON log format | Optional extra: `json-logging` |
| `polars` | `>=0.20` | High-performance DataFrame backend | Optional extra: `polars` |
| `opentelemetry-sdk` | `>=1.20` | Distributed tracing | Optional extra: `tracing` |
| `opentelemetry-exporter-otlp` | `>=1.20` | OTLP trace export | Optional extra: `tracing` |
| `prometheus_client` | `>=0.17` | Prometheus metrics push | Optional extra: `metrics` |

---

## 7. Summary of All Requirement Changes

| # | Finding | Req change |
|---|---|---|
| 2.1 | Chunked reconciliation | Add Req 4.12 |
| 2.2 | Parallel test execution | Add Req 8.8 |
| 2.3 | Polars backend option | Add Req 4.13 |
| 2.4 | Duration SLOs | Add Req 4.14 + new `SLOW` TestStatus |
| 4.1 | Schema validation | Add Req 4.15 + SchemaValidationError |
| 4.2 | Type normalisation | Add Req 4.16 + TypeNormalizer |
| 4.3 | Pydantic config | Amend Req 1.3 |
| 4.4 | NULL semantics | Add Req 4.17 |
| 5.1 | JSON logging | Amend Req 7.2 |
| 5.2 | run_id in logs | Add Req 7.6 |
| 5.3 | Metrics emission | Add Req 7.7 |
| 5.4 | OTel tracing | Add Req 7.8 |
| 5.5 | Health check | Add Req 8.9 |
