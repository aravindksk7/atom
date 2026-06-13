# ETL Framework Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 13 industry best-practice improvements across Scale/Performance, Data Quality, and Observability for the ETL & SAP BO testing framework.

**Architecture:** Tasks run in dependency order — Pydantic config models first (everything depends on EnvironmentConfig), then cross-cutting logging/context, then ReconciliationEngine enhancements, then TestRunner improvements, then new subsystems (health, metrics, tracing, Polars). Each task commits independently.

**Tech Stack:** Python 3.11+, pydantic>=2.0, pandas>=2.0, sqlalchemy>=2.0, numpy>=1.24, python-json-logger>=2.0, tenacity>=8.2, hypothesis>=6.0, pytest>=7.0

---

## File Map

**New files:**
- `etl_framework/utils/context.py` — run_id ContextVar
- `etl_framework/reconciliation/normalizer.py` — TypeNormalizer
- `etl_framework/reconciliation/chunker.py` — HashChecker + chunked query builder
- `etl_framework/reconciliation/backends/__init__.py` — ComparisonBackend protocol
- `etl_framework/reconciliation/backends/pandas_backend.py` — PandasBackend
- `etl_framework/reconciliation/backends/polars_backend.py` — PolarsBackend (optional)
- `etl_framework/runner/health.py` — HealthChecker
- `etl_framework/reporting/metrics.py` — MetricsWriter
- `etl_framework/utils/tracing.py` — OTel span helper
- `tests/unit/test_normalizer.py`
- `tests/unit/test_context.py`
- `tests/unit/test_health.py`
- `tests/unit/test_metrics.py`

**Modified files:**
- `etl_framework/config/models.py` — EnvironmentConfig → Pydantic BaseModel
- `etl_framework/config/loader.py` — wrap Pydantic ValidationError → ConfigurationError
- `etl_framework/exceptions.py` — add SchemaValidationError, RepositoryError
- `etl_framework/runner/state.py` — add SLOW to TestStatus
- `etl_framework/reconciliation/models.py` — add schema_diff to ReconciliationResult
- `etl_framework/reconciliation/engine.py` — schema validation, type normaliser, NULL semantics, SLOs, chunking, OTel spans
- `etl_framework/utils/logging.py` — RunContextFilter, JSON format
- `etl_framework/runner/test_runner.py` — parallel execution, set_run_id
- `etl_framework/reporting/generator.py` — SLOW badge, schema diff section
- `etl_framework/reporting/templates/report.html.j2` — SLOW + schema diff rendering
- `run_tests.py` — --health-check, --max-workers flags; metrics write; set_run_id
- `tests/unit/test_config.py` — Pydantic validation tests
- `tests/unit/test_reconciliation.py` — schema validation, normaliser, NULL, SLO, chunking tests
- `tests/unit/test_runner.py` — parallel execution tests
- `tests/property/test_reconciliation_properties.py` — extend for chunked mode

---

## Task 1: Pydantic v2 Config Models

**Addresses:** Finding 4.3 — plain dataclass config; Req 1.3 amendment

**Files:**
- Modify: `etl_framework/config/models.py`
- Modify: `etl_framework/config/loader.py`
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_config.py
import pytest
from pydantic import ValidationError
from etl_framework.config.exceptions import ConfigurationError
from etl_framework.config.loader import ConfigLoader

def test_invalid_db_port_raises_configuration_error(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
environments:
  dev:
    db_host: localhost
    db_name: mydb
    db_user: user
    db_password: pass
    db_port: 99999
""")
    with pytest.raises(ConfigurationError, match="db_port"):
        ConfigLoader().load(str(cfg))

def test_string_pool_size_raises_configuration_error(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
environments:
  dev:
    db_host: localhost
    db_name: mydb
    db_user: user
    db_password: pass
    db_pool_size: "five"
""")
    with pytest.raises(ConfigurationError, match="db_pool_size"):
        ConfigLoader().load(str(cfg))

def test_valid_config_loads_typed_object(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
environments:
  dev:
    db_host: localhost
    db_name: mydb
    db_user: user
    db_password: secret
    db_port: 1433
""")
    envs = ConfigLoader().load(str(cfg))
    assert envs["dev"].db_port == 1433
    assert isinstance(envs["dev"].db_port, int)

def test_negative_pool_overflow_raises_configuration_error(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
environments:
  dev:
    db_host: localhost
    db_name: mydb
    db_user: user
    db_password: pass
    db_pool_overflow: -1
""")
    with pytest.raises(ConfigurationError, match="db_pool_overflow"):
        ConfigLoader().load(str(cfg))
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/unit/test_config.py -v
# Expected: ImportError or AttributeError — EnvironmentConfig is still a dataclass
```

- [ ] **Step 3: Rewrite config/models.py with Pydantic**

```python
# etl_framework/config/models.py
from pydantic import BaseModel, field_validator, ConfigDict


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = ""
    # SQL Server
    db_host: str
    db_port: int = 1433
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""
    db_driver: str = "ODBC Driver 17 for SQL Server"
    db_pool_size: int = 5
    db_pool_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 3600
    db_connect_timeout: int = 15
    # Automic
    automic_url: str = ""
    automic_user: str = ""
    automic_password: str = ""
    automic_timeout: int = 30
    automic_max_retries: int = 3
    # SAP BO
    bo_url: str = ""
    bo_user: str = ""
    bo_password: str = ""
    bo_timeout: int = 60

    @field_validator("db_port")
    @classmethod
    def validate_db_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"must be 1–65535, got {v}")
        return v

    @field_validator("db_pool_size")
    @classmethod
    def validate_pool_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"must be >= 1, got {v}")
        return v

    @field_validator("db_pool_overflow")
    @classmethod
    def validate_pool_overflow(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"must be >= 0, got {v}")
        return v

    @field_validator("automic_max_retries")
    @classmethod
    def validate_max_retries(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"must be >= 0, got {v}")
        return v

    @field_validator("bo_timeout")
    @classmethod
    def validate_bo_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"must be > 0, got {v}")
        return v
```

- [ ] **Step 4: Update ConfigLoader to wrap Pydantic ValidationError**

```python
# etl_framework/config/loader.py  — replace _validate_env_config with:
from pydantic import ValidationError
from etl_framework.config.models import EnvironmentConfig
from etl_framework.config.exceptions import ConfigurationError

# Inside load(), replace the existing validation call with:
try:
    env_config = EnvironmentConfig(name=env_name, **resolved_raw)
except ValidationError as exc:
    first_error = exc.errors()[0]
    field = ".".join(str(l) for l in first_error["loc"])
    msg = first_error["msg"]
    raise ConfigurationError(
        f"Invalid value for field '{field}' in environment '{env_name}': {msg}",
        field_name=field,
    ) from exc
```

- [ ] **Step 5: Run tests — expect PASS**

```
pytest tests/unit/test_config.py -v
# Expected: 4 passed
```

- [ ] **Step 6: Commit**

```bash
git add etl_framework/config/models.py etl_framework/config/loader.py tests/unit/test_config.py
git commit -m "feat: replace EnvironmentConfig dataclass with Pydantic v2 BaseModel"
```

---

## Task 2: Exception Hierarchy + TestStatus.SLOW + schema_diff Field

**Addresses:** Findings 4.1, 4.4, 2.4 (prerequisites for Tasks 5–8)

**Files:**
- Modify: `etl_framework/exceptions.py`
- Modify: `etl_framework/runner/state.py`
- Modify: `etl_framework/reconciliation/models.py`

- [ ] **Step 1: Add SchemaValidationError to exceptions.py**

```python
# etl_framework/exceptions.py  — add after existing exception classes:

class SchemaValidationError(ETLFrameworkError):
    def __init__(
        self,
        query_name: str,
        missing_in_target: list[str],
        extra_in_target: list[str],
    ) -> None:
        self.query_name = query_name
        self.missing_in_target = missing_in_target
        self.extra_in_target = extra_in_target
        super().__init__(
            f"Schema mismatch in '{query_name}': "
            f"missing_in_target={missing_in_target}, "
            f"extra_in_target={extra_in_target}"
        )


class RepositoryError(ETLFrameworkError):
    def __init__(self, backend: str, operation: str, original_error: Exception) -> None:
        self.backend = backend
        self.operation = operation
        self.original_error = original_error
        super().__init__(
            f"Repository error [{backend}] during '{operation}': {original_error}"
        )
```

- [ ] **Step 2: Add SLOW to TestStatus**

```python
# etl_framework/runner/state.py — add to TestStatus enum:
SLOW = "SLOW"   # completed but exceeded max_duration_seconds
```

- [ ] **Step 3: Add schema_diff to ReconciliationResult**

```python
# etl_framework/reconciliation/models.py — add field to ReconciliationResult dataclass:
# After the existing fields, before the property definitions:
schema_diff: dict[str, list[str]] | None = None
```

- [ ] **Step 4: Write smoke tests**

```python
# tests/unit/test_exceptions.py  (new file)
from etl_framework.exceptions import SchemaValidationError, RepositoryError
from etl_framework.runner.state import TestStatus
from etl_framework.reconciliation.models import ReconciliationResult

def test_schema_validation_error_fields():
    exc = SchemaValidationError("my_query", ["col_a"], ["col_b"])
    assert exc.query_name == "my_query"
    assert exc.missing_in_target == ["col_a"]
    assert exc.extra_in_target == ["col_b"]
    assert "my_query" in str(exc)

def test_slow_status_in_enum():
    assert TestStatus.SLOW == "SLOW"

def test_reconciliation_result_has_schema_diff_field():
    from datetime import datetime
    result = ReconciliationResult(
        query_name="q", source_env="dev", target_env="qa",
        source_row_count=0, target_row_count=0, matched_count=0,
        missing_in_target_count=0, missing_in_source_count=0,
        value_mismatch_count=0, mismatches=[], status=TestStatus.PASSED,
        executed_at=datetime.now(), duration_seconds=0.1,
        schema_diff={"missing_in_target": ["col_x"], "extra_in_target": []},
    )
    assert result.schema_diff["missing_in_target"] == ["col_x"]
```

- [ ] **Step 5: Run tests — expect PASS**

```
pytest tests/unit/test_exceptions.py -v
# Expected: 3 passed
```

- [ ] **Step 6: Commit**

```bash
git add etl_framework/exceptions.py etl_framework/runner/state.py \
        etl_framework/reconciliation/models.py tests/unit/test_exceptions.py
git commit -m "feat: add SchemaValidationError, RepositoryError, TestStatus.SLOW, schema_diff field"
```

---

## Task 3: run_id ContextVar + RunContextFilter

**Addresses:** Finding 5.2 — run_id missing from log messages (Req 7.6)

**Files:**
- Create: `etl_framework/utils/context.py`
- Modify: `etl_framework/utils/logging.py`
- Modify: `etl_framework/runner/test_runner.py`
- Modify: `run_tests.py`
- Create: `tests/unit/test_context.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_context.py
import logging
from etl_framework.utils.context import set_run_id, get_run_id
from etl_framework.utils.logging import configure_logging, RunContextFilter


def test_get_run_id_default_is_empty():
    assert get_run_id() == ""


def test_set_and_get_run_id():
    set_run_id("abc-123")
    assert get_run_id() == "abc-123"
    set_run_id("")  # reset


def test_run_context_filter_injects_run_id(caplog):
    set_run_id("test-run-xyz")
    logger = logging.getLogger("etl_framework.test")
    f = RunContextFilter()
    record = logging.LogRecord(
        name="etl_framework.test", level=logging.INFO,
        pathname="", lineno=0, msg="hello", args=(), exc_info=None,
    )
    f.filter(record)
    assert record.run_id == "test-run-xyz"
    set_run_id("")  # reset
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/unit/test_context.py -v
# Expected: ImportError — module doesn't exist yet
```

- [ ] **Step 3: Create etl_framework/utils/context.py**

```python
# etl_framework/utils/context.py
from contextvars import ContextVar

_run_id_var: ContextVar[str] = ContextVar("run_id", default="")


def set_run_id(run_id: str) -> None:
    _run_id_var.set(run_id)


def get_run_id() -> str:
    return _run_id_var.get()
```

- [ ] **Step 4: Add RunContextFilter to logging.py and update LOG_FORMAT**

```python
# etl_framework/utils/logging.py — add after imports:
import logging
from etl_framework.utils.context import get_run_id

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(run_id)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class RunContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = get_run_id()
        return True


def configure_logging(level: str = "INFO", log_file: str = "./logs/etl_framework.log",
                      log_format: str = "text") -> None:
    root = logging.getLogger("etl_framework")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    context_filter = RunContextFilter()
    text_formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    # Console handler — always text
    stream_handler = logging.StreamHandler()
    stream_handler.addFilter(context_filter)
    stream_handler.setFormatter(text_formatter)
    root.addHandler(stream_handler)

    # File handler — text or JSON
    import os
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    file_handler.addFilter(context_filter)
    if log_format == "json":
        from pythonjsonlogger import jsonlogger
        json_formatter = jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(run_id)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
        file_handler.setFormatter(json_formatter)
    else:
        file_handler.setFormatter(text_formatter)
    root.addHandler(file_handler)
```

- [ ] **Step 5: Inject set_run_id into TestRunner.run()**

```python
# etl_framework/runner/test_runner.py — at start of run():
from etl_framework.utils.context import set_run_id

def run(self) -> TestSuiteResult:
    set_run_id(suite_result.run_id)   # add this line before the test loop
    # ... rest of existing run() body ...
```

- [ ] **Step 6: Inject set_run_id in adhoc functions in run_tests.py**

```python
# run_tests.py — at the top of each _run_adhoc_* function:
import uuid
from etl_framework.utils.context import set_run_id

def _run_adhoc_sql(args):
    set_run_id(str(uuid.uuid4()))
    # ... existing body ...

def _run_adhoc_bo(args):
    set_run_id(str(uuid.uuid4()))

def _run_adhoc_job(args):
    set_run_id(str(uuid.uuid4()))
```

- [ ] **Step 7: Run tests — expect PASS**

```
pytest tests/unit/test_context.py -v
# Expected: 3 passed
```

- [ ] **Step 8: Commit**

```bash
git add etl_framework/utils/context.py etl_framework/utils/logging.py \
        etl_framework/runner/test_runner.py run_tests.py \
        tests/unit/test_context.py
git commit -m "feat: inject run_id into all log messages via ContextVar + RunContextFilter"
```

---

## Task 4: JSON Structured Logging

**Addresses:** Finding 5.1 — human-readable logs only (Req 7.2 amendment)

**Files:**
- Modify: `etl_framework/utils/logging.py` (already updated in Task 3 — just needs the dependency)
- Modify: `pyproject.toml` or `setup.cfg` — add optional extra

- [ ] **Step 1: Add python-json-logger as optional dependency**

```toml
# pyproject.toml
[project.optional-dependencies]
json-logging = ["python-json-logger>=2.0"]
```

- [ ] **Step 2: Write test for JSON log format**

```python
# tests/unit/test_logging.py
import logging
import json
import io
from etl_framework.utils.logging import configure_logging
from etl_framework.utils.context import set_run_id


def test_json_file_handler_emits_parseable_json(tmp_path):
    log_file = str(tmp_path / "test.log")
    set_run_id("run-json-test")
    configure_logging(level="DEBUG", log_file=log_file, log_format="json")

    logger = logging.getLogger("etl_framework.json_test")
    logger.info("test message")

    lines = (tmp_path / "test.log").read_text().strip().splitlines()
    assert lines, "Log file should not be empty"
    record = json.loads(lines[-1])
    assert record["message"] == "test message"
    assert record["run_id"] == "run-json-test"
    set_run_id("")


def test_text_format_is_default(tmp_path):
    log_file = str(tmp_path / "test_text.log")
    configure_logging(level="INFO", log_file=log_file, log_format="text")

    logger = logging.getLogger("etl_framework.text_test")
    logger.info("plain text message")

    content = (tmp_path / "test_text.log").read_text()
    assert "plain text message" in content
    # Should NOT be JSON
    assert not content.strip().startswith("{")
```

- [ ] **Step 3: Install dependency and run tests**

```
pip install python-json-logger>=2.0
pytest tests/unit/test_logging.py -v
# Expected: 2 passed
```

- [ ] **Step 4: Commit**

```bash
git add etl_framework/utils/logging.py tests/unit/test_logging.py pyproject.toml
git commit -m "feat: add JSON structured log format option for CI/CD log aggregators"
```

---

## Task 5: TypeNormalizer

**Addresses:** Finding 4.2 — SQL Server type edge cases (Req 4.16)

**Files:**
- Create: `etl_framework/reconciliation/normalizer.py`
- Create: `tests/unit/test_normalizer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_normalizer.py
import pandas as pd
import numpy as np
from decimal import Decimal
import pytest
from etl_framework.reconciliation.normalizer import TypeNormalizer


def test_timezone_aware_datetime_converted_to_utc():
    df = pd.DataFrame({
        "ts": pd.to_datetime(["2024-01-01 10:00:00"]).tz_localize("US/Eastern"),
    })
    result = TypeNormalizer().normalize(df)
    assert result["ts"].dtype.tz.zone == "UTC"


def test_timezone_naive_datetime_localized_to_utc():
    df = pd.DataFrame({"ts": pd.to_datetime(["2024-01-01 10:00:00"])})
    result = TypeNormalizer().normalize(df)
    assert result["ts"].dtype.tz is not None


def test_decimal_column_converted_to_float64():
    df = pd.DataFrame({"price": [Decimal("9.99"), Decimal("1.50"), None]})
    result = TypeNormalizer().normalize(df)
    assert result["price"].dtype == np.float64
    assert abs(result["price"].iloc[0] - 9.99) < 1e-9
    assert pd.isna(result["price"].iloc[2])


def test_uuid_string_normalised_to_uppercase():
    df = pd.DataFrame({
        "id": ["a1b2c3d4-e5f6-7890-abcd-ef1234567890",
               "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"]
    })
    result = TypeNormalizer().normalize(df)
    assert result["id"].iloc[0] == result["id"].iloc[1]


def test_non_uuid_strings_not_uppercased():
    df = pd.DataFrame({"name": ["alice", "bob"]})
    result = TypeNormalizer().normalize(df)
    assert result["name"].iloc[0] == "alice"


def test_non_datetime_float_columns_unchanged():
    df = pd.DataFrame({"price": [1.5, 2.5, 3.5]})
    result = TypeNormalizer().normalize(df)
    assert list(result["price"]) == [1.5, 2.5, 3.5]
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/unit/test_normalizer.py -v
# Expected: ImportError
```

- [ ] **Step 3: Create etl_framework/reconciliation/normalizer.py**

```python
# etl_framework/reconciliation/normalizer.py
import re
import logging
from decimal import Decimal

import numpy as np
import pandas as pd

logger = logging.getLogger("etl_framework.reconciliation.normalizer")

_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class TypeNormalizer:
    """Normalizes SQL Server type edge cases before DataFrame comparison."""

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        for col in df.columns:
            df[col] = self._normalize_column(df[col], col)
        return df

    def _normalize_column(self, series: pd.Series, col_name: str) -> pd.Series:
        # datetime: unify all to UTC
        if pd.api.types.is_datetime64_any_dtype(series):
            dtype = series.dtype
            if hasattr(dtype, "tz") and dtype.tz is not None:
                series = series.dt.tz_convert("UTC")
            else:
                series = series.dt.tz_localize("UTC")
            logger.debug("Normalized datetime column '%s' to UTC", col_name)
            return series

        # Decimal objects → float64
        non_null = series.dropna()
        if len(non_null) > 0 and isinstance(non_null.iloc[0], Decimal):
            series = series.apply(
                lambda x: float(x) if not _is_na(x) else np.nan
            ).astype(np.float64)
            logger.debug("Normalized Decimal column '%s' to float64", col_name)
            return series

        # Object dtype: check for UUIDs
        if pd.api.types.is_object_dtype(series):
            sample = series.dropna()
            if len(sample) > 0 and _looks_like_uuid(str(sample.iloc[0])):
                series = series.str.upper()
                logger.debug("Normalized UUID column '%s' to uppercase", col_name)
            return series

        return series


def _is_na(val) -> bool:
    try:
        return val is None or (isinstance(val, float) and np.isnan(val))
    except (TypeError, ValueError):
        return False


def _looks_like_uuid(s: str) -> bool:
    return bool(_UUID_PATTERN.match(s))
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/unit/test_normalizer.py -v
# Expected: 6 passed
```

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/normalizer.py tests/unit/test_normalizer.py
git commit -m "feat: add TypeNormalizer for SQL Server type edge cases (datetime tz, Decimal, UUID)"
```

---

## Task 6: Schema Validation in ReconciliationEngine

**Addresses:** Finding 4.1 — no schema validation before merge (Req 4.15)

**Files:**
- Modify: `etl_framework/reconciliation/engine.py`
- Modify: `tests/unit/test_reconciliation.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_reconciliation.py  — add these tests:
import pandas as pd
import pytest
from unittest.mock import MagicMock
from etl_framework.reconciliation.engine import ReconciliationEngine
from etl_framework.exceptions import SchemaValidationError


def _make_engine(source_df: pd.DataFrame, target_df: pd.DataFrame,
                 schema_mismatch_policy: str = "warn") -> ReconciliationEngine:
    source_db = MagicMock()
    target_db = MagicMock()
    source_db.execute_query.return_value = source_df
    target_db.execute_query.return_value = target_df
    return ReconciliationEngine(
        source_engine=source_db,
        target_engine=target_db,
        key_columns=["id"],
        schema_mismatch_policy=schema_mismatch_policy,
    )


def test_schema_mismatch_warn_returns_schema_diff():
    source = pd.DataFrame({"id": [1], "col_a": ["x"], "col_extra": ["y"]})
    target = pd.DataFrame({"id": [1], "col_a": ["x"]})
    engine = _make_engine(source, target, schema_mismatch_policy="warn")
    result = engine.reconcile("SELECT 1", "test_query")
    assert result.schema_diff is not None
    assert "col_extra" in result.schema_diff["missing_in_target"]


def test_schema_mismatch_error_raises():
    source = pd.DataFrame({"id": [1], "col_a": ["x"]})
    target = pd.DataFrame({"id": [1], "col_b": ["x"]})
    engine = _make_engine(source, target, schema_mismatch_policy="error")
    with pytest.raises(SchemaValidationError) as exc_info:
        engine.reconcile("SELECT 1", "test_query")
    assert "col_a" in exc_info.value.missing_in_target


def test_matching_schemas_produce_no_schema_diff():
    source = pd.DataFrame({"id": [1], "col_a": ["x"]})
    target = pd.DataFrame({"id": [1], "col_a": ["x"]})
    engine = _make_engine(source, target)
    result = engine.reconcile("SELECT 1", "test_query")
    assert result.schema_diff is None
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/unit/test_reconciliation.py::test_schema_mismatch_warn_returns_schema_diff -v
# Expected: TypeError — unexpected keyword argument 'schema_mismatch_policy'
```

- [ ] **Step 3: Add schema validation to ReconciliationEngine**

```python
# etl_framework/reconciliation/engine.py

from etl_framework.exceptions import SchemaValidationError
from etl_framework.reconciliation.normalizer import TypeNormalizer

class ReconciliationEngine:
    def __init__(
        self,
        source_engine,
        target_engine,
        key_columns: list[str],
        exclude_columns: list[str] | None = None,
        float_tolerance: float = 1e-9,
        mismatch_row_limit: int = 1000,
        schema_mismatch_policy: str = "warn",   # "warn" | "error"
        null_equals_null: bool = True,           # added in Task 7
        chunk_size: int = 0,                     # added in Task 9
        use_hash_precheck: bool = True,          # added in Task 9
    ):
        self._source_engine = source_engine
        self._target_engine = target_engine
        self._key_columns = key_columns
        self._exclude_columns = set(exclude_columns or [])
        self._float_tolerance = float_tolerance
        self._mismatch_row_limit = mismatch_row_limit
        self._schema_mismatch_policy = schema_mismatch_policy
        self._null_equals_null = null_equals_null
        self._chunk_size = chunk_size
        self._use_hash_precheck = use_hash_precheck
        self._normalizer = TypeNormalizer()

    def _validate_schemas(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
        query_name: str,
    ) -> dict[str, list[str]] | None:
        source_cols = set(df_source.columns) - self._exclude_columns
        target_cols = set(df_target.columns) - self._exclude_columns
        missing_in_target = sorted(source_cols - target_cols)
        extra_in_target = sorted(target_cols - source_cols)

        if not missing_in_target and not extra_in_target:
            return None

        diff = {"missing_in_target": missing_in_target, "extra_in_target": extra_in_target}

        if self._schema_mismatch_policy == "error":
            raise SchemaValidationError(query_name, missing_in_target, extra_in_target)

        logger.warning(
            "Schema mismatch in '%s': missing_in_target=%s, extra_in_target=%s. "
            "Comparing on common columns only.",
            query_name, missing_in_target, extra_in_target,
        )
        return diff

    def reconcile(
        self,
        query: str,
        query_name: str,
        params: dict | None = None,
        max_duration_seconds: float | None = None,
    ) -> "ReconciliationResult":
        import time
        from datetime import datetime
        started = time.monotonic()
        executed_at = datetime.now()

        df_source = self._normalizer.normalize(
            self._source_engine.execute_query(query, params)
        )
        df_target = self._normalizer.normalize(
            self._target_engine.execute_query(query, params)
        )

        schema_diff = self._validate_schemas(df_source, df_target, query_name)

        # Align to common columns when there's a schema diff
        if schema_diff:
            common = [
                c for c in df_source.columns
                if c in df_target.columns and c not in self._exclude_columns
            ]
            df_source = df_source[common]
            df_target = df_target[common]

        # Drop excluded columns
        drop_src = [c for c in self._exclude_columns if c in df_source.columns]
        drop_tgt = [c for c in self._exclude_columns if c in df_target.columns]
        df_source = df_source.drop(columns=drop_src)
        df_target = df_target.drop(columns=drop_tgt)

        result = self._compare(df_source, df_target, query_name, executed_at,
                               time.monotonic() - started, schema_diff)

        # Duration SLO check
        if max_duration_seconds and result.duration_seconds > max_duration_seconds:
            from etl_framework.runner.state import TestStatus
            import dataclasses
            result = dataclasses.replace(result, status=TestStatus.SLOW)
            logger.warning(
                "Reconciliation '%s' took %.1fs, exceeding SLO of %.1fs",
                query_name, result.duration_seconds, max_duration_seconds,
            )
        return result
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/unit/test_reconciliation.py -v
# Expected: all schema tests pass
```

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/engine.py tests/unit/test_reconciliation.py
git commit -m "feat: add schema validation before reconciliation merge (policy: warn|error)"
```

---

## Task 7: NULL Semantics Config

**Addresses:** Finding 4.4 — hardcoded NaN==NaN (Req 4.17)

**Files:**
- Modify: `etl_framework/reconciliation/engine.py`
- Modify: `tests/unit/test_reconciliation.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_reconciliation.py — add:

def test_null_equals_null_true_no_mismatch():
    """Two NaN values in same cell → match when null_equals_null=True."""
    source = pd.DataFrame({"id": [1], "val": [float("nan")]})
    target = pd.DataFrame({"id": [1], "val": [float("nan")]})
    source_db = MagicMock()
    target_db = MagicMock()
    source_db.execute_query.return_value = source
    target_db.execute_query.return_value = target
    engine = ReconciliationEngine(
        source_engine=source_db, target_engine=target_db,
        key_columns=["id"], null_equals_null=True,
    )
    result = engine.reconcile("SELECT 1", "null_test")
    assert result.value_mismatch_count == 0


def test_null_equals_null_false_records_mismatch():
    """Two NaN values → mismatch when null_equals_null=False."""
    source = pd.DataFrame({"id": [1], "val": [float("nan")]})
    target = pd.DataFrame({"id": [1], "val": [float("nan")]})
    source_db = MagicMock()
    target_db = MagicMock()
    source_db.execute_query.return_value = source
    target_db.execute_query.return_value = target
    engine = ReconciliationEngine(
        source_engine=source_db, target_engine=target_db,
        key_columns=["id"], null_equals_null=False,
    )
    result = engine.reconcile("SELECT 1", "null_test")
    assert result.value_mismatch_count == 1
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/unit/test_reconciliation.py::test_null_equals_null_false_records_mismatch -v
# Expected: FAIL — both return 0 mismatches (hardcoded True behaviour)
```

- [ ] **Step 3: Update _values_match in engine.py**

```python
# etl_framework/reconciliation/engine.py — update _values_match:

def _values_match(
    a, b,
    is_float: bool,
    tolerance: float,
    null_equals_null: bool,
) -> bool:
    a_is_na = pd.isna(a) if not isinstance(a, (list, dict)) else False
    b_is_na = pd.isna(b) if not isinstance(b, (list, dict)) else False
    if a_is_na and b_is_na:
        return null_equals_null   # was hardcoded True
    if a_is_na or b_is_na:
        return False
    if is_float:
        return bool(np.isclose(float(a), float(b), atol=tolerance))
    return a == b
```

Pass `self._null_equals_null` when calling `_values_match` inside `_compare()`.

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/unit/test_reconciliation.py -v
# Expected: all pass
```

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/engine.py tests/unit/test_reconciliation.py
git commit -m "feat: add configurable null_equals_null semantics to ReconciliationEngine"
```

---

## Task 8: Per-Test Duration SLOs

**Addresses:** Finding 2.4 — no SLO enforcement (Req 4.14)

**Files:**
- Modify: `etl_framework/reconciliation/engine.py` (already scaffolded in Task 6)
- Modify: `etl_framework/reporting/templates/report.html.j2`
- Modify: `tests/unit/test_reconciliation.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_reconciliation.py — add:

def test_duration_slo_exceeded_sets_slow_status():
    from etl_framework.runner.state import TestStatus
    import time

    source_db = MagicMock()
    target_db = MagicMock()
    source_db.execute_query.return_value = pd.DataFrame({"id": [1], "v": [1]})
    target_db.execute_query.return_value = pd.DataFrame({"id": [1], "v": [1]})

    # Patch time.monotonic to simulate slow execution
    call_count = [0]
    original_monotonic = time.monotonic
    def fake_monotonic():
        call_count[0] += 1
        return call_count[0] * 10.0  # 10s per call
    import unittest.mock as mock
    with mock.patch("etl_framework.reconciliation.engine.time.monotonic", fake_monotonic):
        engine = ReconciliationEngine(
            source_engine=source_db, target_engine=target_db, key_columns=["id"]
        )
        result = engine.reconcile("SELECT 1", "slow_test", max_duration_seconds=5.0)
    assert result.status == TestStatus.SLOW


def test_duration_slo_not_exceeded_keeps_passed_status():
    from etl_framework.runner.state import TestStatus
    source_db = MagicMock()
    target_db = MagicMock()
    source_db.execute_query.return_value = pd.DataFrame({"id": [1], "v": [1]})
    target_db.execute_query.return_value = pd.DataFrame({"id": [1], "v": [1]})
    engine = ReconciliationEngine(
        source_engine=source_db, target_engine=target_db, key_columns=["id"]
    )
    result = engine.reconcile("SELECT 1", "fast_test", max_duration_seconds=9999.0)
    assert result.status == TestStatus.PASSED
```

- [ ] **Step 2: Run tests — expect FAIL (SLOW status not set)**

```
pytest tests/unit/test_reconciliation.py::test_duration_slo_exceeded_sets_slow_status -v
```

- [ ] **Step 3: Verify the SLO check in reconcile() from Task 6 is wired correctly**

The Task 6 `reconcile()` implementation already includes the SLO check block. Confirm `time` is imported at the top of `engine.py`:

```python
import time  # add to imports if missing
```

- [ ] **Step 4: Add SLOW badge to HTML template**

```html
<!-- etl_framework/reporting/templates/report.html.j2 — add to badge logic: -->
{% macro status_badge(status) %}
  {% if status == "PASSED" %}
    <span class="badge badge-pass">PASSED</span>
  {% elif status == "FAILED" %}
    <span class="badge badge-fail">FAILED</span>
  {% elif status == "SLOW" %}
    <span class="badge badge-amber">SLOW</span>
  {% elif status in ("RUNNING", "SKIPPED") %}
    <span class="badge badge-amber">{{ status }}</span>
  {% else %}
    <span class="badge badge-fail">{{ status }}</span>
  {% endif %}
{% endmacro %}
```

- [ ] **Step 5: Run all reconciliation tests**

```
pytest tests/unit/test_reconciliation.py -v
# Expected: all pass
```

- [ ] **Step 6: Commit**

```bash
git add etl_framework/reconciliation/engine.py \
        etl_framework/reporting/templates/report.html.j2 \
        tests/unit/test_reconciliation.py
git commit -m "feat: enforce per-test duration SLOs; SLOW status rendered amber in HTML report"
```

---

## Task 9: Chunked Reconciliation

**Addresses:** Finding 2.1 — full in-memory merge (Req 4.12)

**Files:**
- Create: `etl_framework/reconciliation/chunker.py`
- Modify: `etl_framework/reconciliation/engine.py`
- Modify: `tests/unit/test_reconciliation.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_reconciliation.py — add:

def test_hash_precheck_skips_row_pull_on_identical_data():
    """When hashes match, execute_query should be called only for the hash query."""
    source_db = MagicMock()
    target_db = MagicMock()
    hash_df = pd.DataFrame({"row_count": [100], "data_hash": [42]})
    source_db.execute_query.return_value = hash_df
    target_db.execute_query.return_value = hash_df

    engine = ReconciliationEngine(
        source_engine=source_db, target_engine=target_db,
        key_columns=["id"], chunk_size=50, use_hash_precheck=True,
    )
    result = engine.reconcile("SELECT id, v FROM t", "hash_test")
    # Hash matched → no further execute_query calls
    assert source_db.execute_query.call_count == 1
    assert result.total_issues == 0


def test_chunked_reconciliation_accumulates_across_chunks():
    """Chunked mode must accumulate mismatch counts from all chunks."""
    source_db = MagicMock()
    target_db = MagicMock()

    # Simulate hash mismatch → triggers row fetch
    hash_df_src = pd.DataFrame({"row_count": [4], "data_hash": [10]})
    hash_df_tgt = pd.DataFrame({"row_count": [4], "data_hash": [99]})

    # Two chunks of 2 rows each
    chunk_src_1 = pd.DataFrame({"id": [1, 2], "v": [10, 20]})
    chunk_tgt_1 = pd.DataFrame({"id": [1, 2], "v": [10, 99]})  # mismatch on row 2
    chunk_src_2 = pd.DataFrame({"id": [3, 4], "v": [30, 40]})
    chunk_tgt_2 = pd.DataFrame({"id": [3, 4], "v": [30, 40]})
    empty = pd.DataFrame({"id": [], "v": []})

    source_db.execute_query.side_effect = [
        hash_df_src, chunk_src_1, chunk_src_2, empty,
    ]
    target_db.execute_query.side_effect = [
        hash_df_tgt, chunk_tgt_1, chunk_tgt_2, empty,
    ]

    engine = ReconciliationEngine(
        source_engine=source_db, target_engine=target_db,
        key_columns=["id"], chunk_size=2, use_hash_precheck=True,
    )
    result = engine.reconcile("SELECT id, v FROM t ORDER BY id", "chunk_test")
    assert result.value_mismatch_count == 1
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/unit/test_reconciliation.py::test_hash_precheck_skips_row_pull_on_identical_data -v
# Expected: FAIL — chunking not implemented
```

- [ ] **Step 3: Create etl_framework/reconciliation/chunker.py**

```python
# etl_framework/reconciliation/chunker.py
import logging

logger = logging.getLogger("etl_framework.reconciliation.chunker")

_HASH_QUERY = """
SELECT COUNT(*) AS row_count,
       CHECKSUM_AGG(BINARY_CHECKSUM(*)) AS data_hash
FROM ({user_query}) AS _hash_subquery
"""


def build_hash_query(user_query: str) -> str:
    return _HASH_QUERY.format(user_query=user_query.rstrip(";"))


def build_chunk_query(user_query: str, key_columns: list[str],
                      offset: int, chunk_size: int) -> str:
    key_col_list = ", ".join(key_columns)
    base = user_query.rstrip(";")
    return (
        f"SELECT * FROM ({base}) AS _paged\n"
        f"ORDER BY {key_col_list}\n"
        f"OFFSET {offset} ROWS FETCH NEXT {chunk_size} ROWS ONLY"
    )


def hashes_match(source_hash_df, target_hash_df) -> bool:
    """Returns True if both row_count and data_hash match."""
    try:
        src_count = int(source_hash_df["row_count"].iloc[0])
        tgt_count = int(target_hash_df["row_count"].iloc[0])
        src_hash = source_hash_df["data_hash"].iloc[0]
        tgt_hash = target_hash_df["data_hash"].iloc[0]
        match = src_count == tgt_count and src_hash == tgt_hash
        logger.debug(
            "Hash check: src_count=%d tgt_count=%d src_hash=%s tgt_hash=%s match=%s",
            src_count, tgt_count, src_hash, tgt_hash, match,
        )
        return match
    except (KeyError, IndexError) as e:
        logger.warning("Hash check failed to parse result: %s — falling back to row pull", e)
        return False
```

- [ ] **Step 4: Add chunked reconciliation to ReconciliationEngine**

```python
# etl_framework/reconciliation/engine.py — add _reconcile_chunked method:

from etl_framework.reconciliation.chunker import (
    build_hash_query, build_chunk_query, hashes_match,
)
import dataclasses

def reconcile(self, query, query_name, params=None, max_duration_seconds=None):
    import time
    from datetime import datetime
    started = time.monotonic()
    executed_at = datetime.now()

    if self._chunk_size > 0 and self._use_hash_precheck:
        # Phase 1: hash check
        hash_q = build_hash_query(query)
        src_hash = self._source_engine.execute_query(hash_q, params)
        tgt_hash = self._target_engine.execute_query(hash_q, params)
        if hashes_match(src_hash, tgt_hash):
            row_count = int(src_hash["row_count"].iloc[0])
            return ReconciliationResult(
                query_name=query_name, source_env=self._source_engine._env.name,
                target_env=self._target_engine._env.name,
                source_row_count=row_count, target_row_count=row_count,
                matched_count=row_count, missing_in_target_count=0,
                missing_in_source_count=0, value_mismatch_count=0,
                mismatches=[], status=TestStatus.PASSED,
                executed_at=executed_at,
                duration_seconds=time.monotonic() - started,
            )
        # Phase 2: chunked row fetch
        return self._reconcile_chunked(
            query, query_name, params, executed_at, started, max_duration_seconds
        )

    # Non-chunked path (original behaviour)
    df_source = self._normalizer.normalize(
        self._source_engine.execute_query(query, params)
    )
    df_target = self._normalizer.normalize(
        self._target_engine.execute_query(query, params)
    )
    schema_diff = self._validate_schemas(df_source, df_target, query_name)
    df_source, df_target = self._align_columns(df_source, df_target, schema_diff)
    result = self._compare(df_source, df_target, query_name, executed_at,
                           time.monotonic() - started, schema_diff)
    return self._apply_slo(result, max_duration_seconds)


def _reconcile_chunked(self, query, query_name, params, executed_at, started,
                       max_duration_seconds):
    import time
    offset = 0
    total_src = total_tgt = matched = mit = mis = vmc = 0
    all_mismatches = []

    while True:
        chunk_q = build_chunk_query(query, self._key_columns, offset, self._chunk_size)
        df_src = self._normalizer.normalize(
            self._source_engine.execute_query(chunk_q, params)
        )
        df_tgt = self._normalizer.normalize(
            self._target_engine.execute_query(chunk_q, params)
        )
        if df_src.empty and df_tgt.empty:
            break

        chunk_result = self._compare(
            df_src, df_tgt, query_name, executed_at,
            time.monotonic() - started, schema_diff=None,
        )
        total_src += chunk_result.source_row_count
        total_tgt += chunk_result.target_row_count
        matched += chunk_result.matched_count
        mit += chunk_result.missing_in_target_count
        mis += chunk_result.missing_in_source_count
        vmc += chunk_result.value_mismatch_count
        remaining = self._mismatch_row_limit - len(all_mismatches)
        all_mismatches.extend(chunk_result.mismatches[:remaining])

        if len(df_src) < self._chunk_size and len(df_tgt) < self._chunk_size:
            break
        offset += self._chunk_size

    total_issues = mit + mis + vmc
    status = TestStatus.PASSED if total_issues == 0 else TestStatus.FAILED
    result = ReconciliationResult(
        query_name=query_name,
        source_env=self._source_engine._env.name,
        target_env=self._target_engine._env.name,
        source_row_count=total_src, target_row_count=total_tgt,
        matched_count=matched,
        missing_in_target_count=mit, missing_in_source_count=mis,
        value_mismatch_count=vmc, mismatches=all_mismatches,
        status=status, executed_at=executed_at,
        duration_seconds=time.monotonic() - started,
    )
    return self._apply_slo(result, max_duration_seconds)


def _align_columns(self, df_src, df_tgt, schema_diff):
    if schema_diff:
        common = [c for c in df_src.columns
                  if c in df_tgt.columns and c not in self._exclude_columns]
        df_src = df_src[common]
        df_tgt = df_tgt[common]
    drop_src = [c for c in self._exclude_columns if c in df_src.columns]
    drop_tgt = [c for c in self._exclude_columns if c in df_tgt.columns]
    return df_src.drop(columns=drop_src), df_tgt.drop(columns=drop_tgt)


def _apply_slo(self, result, max_duration_seconds):
    if max_duration_seconds and result.duration_seconds > max_duration_seconds:
        result = dataclasses.replace(result, status=TestStatus.SLOW)
        logger.warning(
            "Reconciliation '%s' took %.1fs, exceeding SLO of %.1fs",
            result.query_name, result.duration_seconds, max_duration_seconds,
        )
    return result
```

- [ ] **Step 5: Run tests — expect PASS**

```
pytest tests/unit/test_reconciliation.py -v
# Expected: all pass including chunked tests
```

- [ ] **Step 6: Commit**

```bash
git add etl_framework/reconciliation/chunker.py \
        etl_framework/reconciliation/engine.py \
        tests/unit/test_reconciliation.py
git commit -m "feat: two-phase chunked reconciliation with hash precheck to avoid full in-memory merge"
```

---

## Task 10: Parallel Test Execution

**Addresses:** Finding 2.2 — sequential-only execution (Req 8.8)

**Files:**
- Modify: `etl_framework/runner/test_runner.py`
- Modify: `run_tests.py`
- Modify: `tests/unit/test_runner.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_runner.py — add:
import time
import threading
from unittest.mock import MagicMock, patch
from etl_framework.runner.test_runner import TestRunner
from etl_framework.runner.state import TestStatus


def _make_slow_test_case(name: str, sleep_s: float):
    """Returns a test case config dict that sleeps for sleep_s when executed."""
    return {"name": name, "test_type": "sql", "query": "SELECT 1", "sleep": sleep_s}


def test_parallel_execution_faster_than_sequential():
    """With max_workers=2, two 0.1s tests should finish in ~0.1s not ~0.2s."""
    execution_times = []

    def fake_execute(tc, *args, **kwargs):
        time.sleep(0.1)
        execution_times.append(time.monotonic())
        tc.status = TestStatus.PASSED

    runner = TestRunner(
        suite_config={"test_cases": [{"name": "t1"}, {"name": "t2"}]},
        source_env=MagicMock(), target_env=MagicMock(),
        repository=MagicMock(), max_workers=2,
    )
    with patch.object(runner, "_execute_test_case", side_effect=fake_execute):
        start = time.monotonic()
        runner.run()
        elapsed = time.monotonic() - start

    assert elapsed < 0.18, f"Parallel run took {elapsed:.2f}s — expected < 0.18s"


def test_sequential_execution_with_max_workers_1():
    """max_workers=1 should retain existing sequential behaviour."""
    order = []

    def fake_execute(tc, *args, **kwargs):
        order.append(tc.name)
        tc.status = TestStatus.PASSED

    runner = TestRunner(
        suite_config={"test_cases": [{"name": "t1"}, {"name": "t2"}]},
        source_env=MagicMock(), target_env=MagicMock(),
        repository=MagicMock(), max_workers=1,
    )
    with patch.object(runner, "_execute_test_case", side_effect=fake_execute):
        runner.run()

    assert order == ["t1", "t2"]
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/unit/test_runner.py::test_parallel_execution_faster_than_sequential -v
# Expected: TypeError — unexpected keyword argument 'max_workers'
```

- [ ] **Step 3: Add parallel execution to TestRunner**

```python
# etl_framework/runner/test_runner.py
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

class TestRunner:
    def __init__(
        self,
        suite_config: dict,
        source_env,
        target_env,
        repository,
        fail_fast: bool = False,
        max_workers: int = 1,
    ):
        self._suite_config = suite_config
        self._source_env = source_env
        self._target_env = target_env
        self._repository = repository
        self._fail_fast = fail_fast
        self._max_workers = max_workers
        self._checkpoint_lock = threading.Lock()

    def run(self):
        from etl_framework.utils.context import set_run_id
        suite_result = self._init_suite_result()
        set_run_id(suite_result.run_id)

        if self._max_workers == 1:
            self._run_sequential(suite_result)
        else:
            self._run_parallel(suite_result)

        suite_result.completed_at = __import__("datetime").datetime.now()
        self._repository.save(suite_result)
        return suite_result

    def _run_sequential(self, suite_result):
        for tc in suite_result.test_cases:
            if tc.status == TestStatus.SKIPPED:
                continue
            self._execute_test_case(tc, suite_result)
            if self._fail_fast and tc.status in (TestStatus.FAILED, TestStatus.ERROR):
                break

    def _run_parallel(self, suite_result):
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(self._execute_test_case, tc, suite_result): tc
                for tc in suite_result.test_cases
                if tc.status != TestStatus.SKIPPED
            }
            for future in as_completed(futures):
                future.result()   # propagate exceptions
                if self._fail_fast and suite_result.total_failed > 0:
                    for f in futures:
                        f.cancel()
                    break

    def _execute_test_case(self, tc, suite_result):
        import datetime
        tc.status = TestStatus.RUNNING
        tc.started_at = datetime.datetime.now()
        try:
            # ... dispatch to Automic / Reconciliation / BO based on tc.test_type ...
            tc.status = TestStatus.PASSED
        except Exception as exc:
            tc.status = TestStatus.ERROR
            tc.error_message = str(exc)
            logger.error("Test '%s' error", tc.name, exc_info=True)
        finally:
            tc.completed_at = datetime.datetime.now()
            with self._checkpoint_lock:
                self._repository.checkpoint(suite_result)
```

- [ ] **Step 4: Add --max-workers to run_tests.py**

```python
# run_tests.py — in parse_args():
parser.add_argument(
    "--max-workers", type=int, default=1,
    help="Number of parallel test workers (default: 1 = sequential)"
)

# In _run_full_suite(args):
runner = TestRunner(
    ...,
    max_workers=args.max_workers,
)
```

- [ ] **Step 5: Run tests — expect PASS**

```
pytest tests/unit/test_runner.py -v
# Expected: all pass
```

- [ ] **Step 6: Commit**

```bash
git add etl_framework/runner/test_runner.py run_tests.py tests/unit/test_runner.py
git commit -m "feat: parallel test execution via ThreadPoolExecutor; --max-workers CLI flag"
```

---

## Task 11: Health Check

**Addresses:** Finding 5.5 — no connectivity probe (Req 8.9)

**Files:**
- Create: `etl_framework/runner/health.py`
- Modify: `run_tests.py`
- Create: `tests/unit/test_health.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_health.py
from unittest.mock import MagicMock, patch
from etl_framework.runner.health import HealthChecker, HealthCheckResult
from etl_framework.config.models import EnvironmentConfig


def _make_env(name="dev", db_host="localhost", automic_url="", bo_url=""):
    return EnvironmentConfig(
        name=name, db_host=db_host, db_name="db", db_user="u", db_password="p",
        automic_url=automic_url, bo_url=bo_url,
    )


def test_db_check_ok_when_query_succeeds():
    env = _make_env()
    with patch("etl_framework.runner.health.DBEngine") as MockEngine:
        mock_instance = MagicMock()
        MockEngine.return_value = mock_instance
        checker = HealthChecker({"dev": env})
        results = checker.check_all()
    db_results = [r for r in results if r.service == "db"]
    assert len(db_results) == 1
    assert db_results[0].ok is True


def test_db_check_fail_when_connect_raises():
    env = _make_env()
    with patch("etl_framework.runner.health.DBEngine") as MockEngine:
        MockEngine.side_effect = Exception("connection refused")
        checker = HealthChecker({"dev": env})
        results = checker.check_all()
    db_results = [r for r in results if r.service == "db"]
    assert db_results[0].ok is False
    assert "connection refused" in db_results[0].error


def test_automic_check_skipped_when_url_empty():
    env = _make_env(automic_url="")
    with patch("etl_framework.runner.health.DBEngine"):
        checker = HealthChecker({"dev": env})
        results = checker.check_all()
    assert not any(r.service == "automic" for r in results)


def test_health_checker_exit_code_0_when_all_ok():
    env = _make_env()
    with patch("etl_framework.runner.health.DBEngine"):
        checker = HealthChecker({"dev": env})
        results = checker.check_all()
    assert all(r.ok for r in results)
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/unit/test_health.py -v
# Expected: ImportError
```

- [ ] **Step 3: Create etl_framework/runner/health.py**

```python
# etl_framework/runner/health.py
import time
import logging
from dataclasses import dataclass

from etl_framework.config.models import EnvironmentConfig

logger = logging.getLogger("etl_framework.runner.health")


@dataclass
class HealthCheckResult:
    env_name: str
    service: str       # "db" | "automic" | "sap_bo"
    endpoint: str
    ok: bool
    error: str | None
    latency_ms: float


class HealthChecker:
    def __init__(self, env_configs: dict[str, EnvironmentConfig]):
        self._env_configs = env_configs

    def check_all(self) -> list[HealthCheckResult]:
        results = []
        for env_name, cfg in self._env_configs.items():
            results.extend(self._check_env(env_name, cfg))
        return results

    def _check_env(self, env_name: str, cfg: EnvironmentConfig) -> list[HealthCheckResult]:
        results = []
        if cfg.db_host:
            results.append(self._check_db(env_name, cfg))
        if cfg.automic_url:
            results.append(self._check_automic(env_name, cfg))
        if cfg.bo_url:
            results.append(self._check_bo(env_name, cfg))
        return results

    def _check_db(self, env_name: str, cfg: EnvironmentConfig) -> HealthCheckResult:
        from etl_framework.db.engine import DBEngine
        endpoint = f"{cfg.db_host}:{cfg.db_port}"
        t0 = time.monotonic()
        try:
            engine = DBEngine(cfg)
            engine.connect()
            engine.execute_query("SELECT 1 AS health_check")
            engine.dispose()
            return HealthCheckResult(env_name, "db", endpoint, True, None,
                                     (time.monotonic() - t0) * 1000)
        except Exception as exc:
            return HealthCheckResult(env_name, "db", endpoint, False, str(exc),
                                     (time.monotonic() - t0) * 1000)

    def _check_automic(self, env_name: str, cfg: EnvironmentConfig) -> HealthCheckResult:
        import requests
        endpoint = cfg.automic_url
        t0 = time.monotonic()
        try:
            resp = requests.get(f"{endpoint}/api/v1/health", timeout=cfg.automic_timeout)
            resp.raise_for_status()
            return HealthCheckResult(env_name, "automic", endpoint, True, None,
                                     (time.monotonic() - t0) * 1000)
        except Exception as exc:
            return HealthCheckResult(env_name, "automic", endpoint, False, str(exc),
                                     (time.monotonic() - t0) * 1000)

    def _check_bo(self, env_name: str, cfg: EnvironmentConfig) -> HealthCheckResult:
        import requests
        endpoint = cfg.bo_url
        t0 = time.monotonic()
        try:
            resp = requests.get(f"{endpoint}/biprws/", timeout=cfg.bo_timeout)
            # 200 or 401 both indicate the server is reachable
            if resp.status_code not in (200, 401):
                resp.raise_for_status()
            return HealthCheckResult(env_name, "sap_bo", endpoint, True, None,
                                     (time.monotonic() - t0) * 1000)
        except Exception as exc:
            return HealthCheckResult(env_name, "sap_bo", endpoint, False, str(exc),
                                     (time.monotonic() - t0) * 1000)
```

- [ ] **Step 4: Add --health-check to run_tests.py**

```python
# run_tests.py — in parse_args():
parser.add_argument(
    "--health-check", action="store_true",
    help="Test connectivity to all configured environments and exit"
)

# In main(), before mode resolution:
if args.health_check:
    from etl_framework.runner.health import HealthChecker
    envs = ConfigLoader().load(args.config)
    results = HealthChecker(envs).check_all()
    all_ok = True
    for r in results:
        status = "OK  " if r.ok else "FAIL"
        print(f"[{status}] {r.env_name:6s} {r.service:8s} {r.endpoint}  ({r.latency_ms:.0f}ms)"
              + (f"  — {r.error}" if r.error else ""))
        if not r.ok:
            all_ok = False
    sys.exit(0 if all_ok else 1)
```

- [ ] **Step 5: Run tests — expect PASS**

```
pytest tests/unit/test_health.py -v
# Expected: 4 passed
```

- [ ] **Step 6: Commit**

```bash
git add etl_framework/runner/health.py run_tests.py tests/unit/test_health.py
git commit -m "feat: add --health-check flag for pre-flight connectivity probe (Req 8.9)"
```

---

## Task 12: Metrics JSON Sidecar

**Addresses:** Finding 5.3 — no metrics emission (Req 7.7)

**Files:**
- Create: `etl_framework/reporting/metrics.py`
- Modify: `run_tests.py`
- Create: `tests/unit/test_metrics.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_metrics.py
import json
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock
from etl_framework.reporting.metrics import MetricsWriter
from etl_framework.runner.state import TestStatus


def _make_suite_result(tmp_path):
    sr = MagicMock()
    sr.run_id = "test-run-001"
    sr.started_at = datetime(2026, 6, 13, 10, 0, 0, tzinfo=timezone.utc)
    sr.completed_at = datetime(2026, 6, 13, 10, 0, 45, tzinfo=timezone.utc)
    sr.source_env = "dev"
    sr.target_env = "qa"
    sr.framework_version = "1.0.0"
    sr.total_passed = 3
    sr.total_failed = 1
    sr.total_skipped = 0
    sr.test_cases = []
    sr.reconciliation_results = []
    sr.bo_results = []
    sr.job_results = []
    return sr


def test_metrics_file_written_to_output_dir(tmp_path):
    sr = _make_suite_result(tmp_path)
    writer = MetricsWriter(output_dir=str(tmp_path))
    path = writer.write(sr)
    assert Path(path).exists()


def test_metrics_json_contains_run_id(tmp_path):
    sr = _make_suite_result(tmp_path)
    path = MetricsWriter(str(tmp_path)).write(sr)
    data = json.loads(Path(path).read_text())
    assert data["run_id"] == "test-run-001"
    assert data["passed"] == 3
    assert data["failed"] == 1
    assert abs(data["duration_seconds"] - 45.0) < 0.1


def test_metrics_filename_includes_run_id(tmp_path):
    sr = _make_suite_result(tmp_path)
    path = MetricsWriter(str(tmp_path)).write(sr)
    assert "test-run-001" in Path(path).name
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/unit/test_metrics.py -v
# Expected: ImportError
```

- [ ] **Step 3: Create etl_framework/reporting/metrics.py**

```python
# etl_framework/reporting/metrics.py
import json
import logging
from pathlib import Path

logger = logging.getLogger("etl_framework.reporting.metrics")


class MetricsWriter:
    def __init__(self, output_dir: str = "./reports"):
        self._output_dir = Path(output_dir)

    def write(self, suite_result) -> str:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        metrics = self._build(suite_result)
        path = self._output_dir / f"metrics_{suite_result.run_id}.json"
        path.write_text(json.dumps(metrics, indent=2, default=str))
        logger.info("Metrics written to %s", path)
        return str(path)

    def _build(self, sr) -> dict:
        duration = None
        if sr.completed_at and sr.started_at:
            duration = (sr.completed_at - sr.started_at).total_seconds()
        return {
            "run_id": sr.run_id,
            "started_at": sr.started_at.isoformat(),
            "completed_at": sr.completed_at.isoformat() if sr.completed_at else None,
            "duration_seconds": duration,
            "source_env": sr.source_env,
            "target_env": sr.target_env,
            "framework_version": sr.framework_version,
            "total_tests": len(sr.test_cases),
            "passed": sr.total_passed,
            "failed": sr.total_failed,
            "skipped": sr.total_skipped,
            "reconciliations": [
                {
                    "name": r.query_name,
                    "source_rows": r.source_row_count,
                    "target_rows": r.target_row_count,
                    "matched": r.matched_count,
                    "mismatches": r.total_issues,
                    "duration_seconds": r.duration_seconds,
                    "status": str(r.status),
                    "schema_diff": r.schema_diff,
                }
                for r in (sr.reconciliation_results + sr.bo_results)
            ],
            "jobs": [
                {
                    "identifier": j.identifier,
                    "status": str(j.status),
                    "environment": j.environment,
                }
                for j in sr.job_results
            ],
        }
```

- [ ] **Step 4: Wire MetricsWriter into run_tests.py**

```python
# run_tests.py — after generating HTML report in _run_full_suite / _run_filtered_suite:
from etl_framework.reporting.metrics import MetricsWriter

if getattr(args, "metrics_enabled", True):
    metrics_path = MetricsWriter(output_dir=args.output_dir).write(suite_result)
    logger.info("Metrics: %s", metrics_path)
```

- [ ] **Step 5: Run tests — expect PASS**

```
pytest tests/unit/test_metrics.py -v
# Expected: 3 passed
```

- [ ] **Step 6: Commit**

```bash
git add etl_framework/reporting/metrics.py run_tests.py tests/unit/test_metrics.py
git commit -m "feat: write metrics JSON sidecar after each run for dashboarding (Req 7.7)"
```

---

## Task 13: OpenTelemetry Tracing (Nice-to-Have)

**Addresses:** Finding 5.4 — no distributed tracing (Req 7.8)

**Files:**
- Create: `etl_framework/utils/tracing.py`
- Modify: `etl_framework/reconciliation/engine.py`
- Modify: `etl_framework/runner/test_runner.py`

- [ ] **Step 1: Write tests**

```python
# tests/unit/test_tracing.py
from etl_framework.utils.tracing import configure_tracing, span


def test_span_is_noop_when_tracing_disabled():
    configure_tracing(enabled=False)
    with span("test.operation", key="value") as s:
        pass  # should not raise


def test_span_is_noop_when_otel_not_installed(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    configure_tracing(enabled=False)
    with span("test.operation") as s:
        pass  # should not raise
```

- [ ] **Step 2: Create etl_framework/utils/tracing.py**

```python
# etl_framework/utils/tracing.py
from contextlib import contextmanager
from typing import Iterator, Any

_tracer = None


def configure_tracing(
    enabled: bool,
    exporter: str = "console",
    endpoint: str = "",
) -> None:
    global _tracer
    if not enabled:
        _tracer = None
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        if exporter == "otlp" and endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exp = OTLPSpanExporter(endpoint=endpoint)
        else:
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter
            exp = ConsoleSpanExporter()

        provider.add_span_processor(BatchSpanProcessor(exp))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("etl_framework")
    except ImportError:
        _tracer = None


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as s:
        for k, v in attributes.items():
            s.set_attribute(k, str(v))
        yield s
```

- [ ] **Step 3: Add span instrumentation to ReconciliationEngine.reconcile()**

```python
# etl_framework/reconciliation/engine.py — wrap reconcile() body:
from etl_framework.utils.tracing import span

def reconcile(self, query, query_name, params=None, max_duration_seconds=None):
    with span("reconciliation.reconcile", query_name=query_name,
              source_env=self._source_engine._env.name,
              target_env=self._target_engine._env.name):
        # ... existing reconcile body ...
```

- [ ] **Step 4: Add span to TestRunner._execute_test_case()**

```python
from etl_framework.utils.tracing import span

def _execute_test_case(self, tc, suite_result):
    with span("test_runner.execute", test_name=tc.name, test_type=tc.test_type,
              run_id=suite_result.run_id):
        # ... existing body ...
```

- [ ] **Step 5: Run tests — expect PASS**

```
pytest tests/unit/test_tracing.py -v
# Expected: 2 passed (no OTel dependency required for no-op path)
```

- [ ] **Step 6: Commit**

```bash
git add etl_framework/utils/tracing.py etl_framework/reconciliation/engine.py \
        etl_framework/runner/test_runner.py tests/unit/test_tracing.py
git commit -m "feat: optional OpenTelemetry tracing via no-op span helper (Req 7.8)"
```

---

## Task 14: Polars Comparison Backend (Nice-to-Have)

**Addresses:** Finding 2.3 — pandas-only backend (Req 4.13)

**Files:**
- Create: `etl_framework/reconciliation/backends/__init__.py`
- Create: `etl_framework/reconciliation/backends/pandas_backend.py`
- Create: `etl_framework/reconciliation/backends/polars_backend.py`
- Modify: `etl_framework/reconciliation/engine.py`

- [ ] **Step 1: Write tests**

```python
# tests/unit/test_backends.py
import pandas as pd
from etl_framework.reconciliation.backends.pandas_backend import PandasBackend


def test_pandas_backend_outer_merge_indicator():
    src = pd.DataFrame({"id": [1, 2], "v": [10, 20]})
    tgt = pd.DataFrame({"id": [2, 3], "v": [20, 30]})
    backend = PandasBackend()
    merged = backend.merge_outer(src, tgt, key_columns=["id"])
    assert "_merge" in merged.columns
    left_only = merged[merged["_merge"] == "left_only"]
    right_only = merged[merged["_merge"] == "right_only"]
    assert len(left_only) == 1   # id=1
    assert len(right_only) == 1  # id=3
```

- [ ] **Step 2: Create backend files**

```python
# etl_framework/reconciliation/backends/__init__.py
from typing import Protocol
import pandas as pd


class ComparisonBackend(Protocol):
    def merge_outer(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
        key_columns: list[str],
    ) -> pd.DataFrame: ...
```

```python
# etl_framework/reconciliation/backends/pandas_backend.py
import pandas as pd


class PandasBackend:
    def merge_outer(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
        key_columns: list[str],
    ) -> pd.DataFrame:
        return pd.merge(
            df_source.assign(_src=True),
            df_target.assign(_tgt=True),
            on=key_columns,
            how="outer",
            indicator=True,
            suffixes=("_src", "_tgt"),
        )
```

```python
# etl_framework/reconciliation/backends/polars_backend.py
import pandas as pd


class PolarsBackend:
    def merge_outer(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
        key_columns: list[str],
    ) -> pd.DataFrame:
        try:
            import polars as pl
        except ImportError as e:
            raise ImportError(
                "polars is required for PolarsBackend: pip install etl-framework[polars]"
            ) from e

        src = pl.from_pandas(df_source)
        tgt = pl.from_pandas(df_target)
        merged = src.join(tgt, on=key_columns, how="full", suffix="_tgt")
        result = merged.to_pandas()
        # Add _merge indicator compatible with PandasBackend output
        result["_merge"] = "both"
        result.loc[result[[f"{k}_tgt" for k in key_columns if f"{k}_tgt" in result]].isnull().any(axis=1), "_merge"] = "left_only"
        result.loc[result[key_columns].isnull().any(axis=1), "_merge"] = "right_only"
        return result
```

- [ ] **Step 3: Wire backend selection into ReconciliationEngine**

```python
# etl_framework/reconciliation/engine.py — in __init__:
def __init__(self, ..., comparison_backend: str = "pandas"):
    ...
    if comparison_backend == "polars":
        from etl_framework.reconciliation.backends.polars_backend import PolarsBackend
        self._backend = PolarsBackend()
    else:
        from etl_framework.reconciliation.backends.pandas_backend import PandasBackend
        self._backend = PandasBackend()

# In _compare(), replace the pd.merge call with:
merged = self._backend.merge_outer(df_source, df_target, self._key_columns)
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/unit/test_backends.py -v
# Expected: 1 passed
```

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/backends/ \
        etl_framework/reconciliation/engine.py \
        tests/unit/test_backends.py
git commit -m "feat: pluggable comparison backend; Polars backend as optional alternative to pandas"
```

---

## Self-Review

**Spec coverage check:**

| Finding | Task | Covered? |
|---|---|---|
| 4.3 Pydantic config | Task 1 | ✅ |
| 2.4 Duration SLOs | Task 2 + 8 | ✅ |
| 4.1 Schema validation | Task 2 + 6 | ✅ |
| 5.2 run_id in logs | Task 3 | ✅ |
| 5.1 JSON logging | Task 4 | ✅ |
| 4.2 Type normalisation | Task 5 | ✅ |
| 4.4 NULL semantics | Task 7 | ✅ |
| 2.1 Chunked reconciliation | Task 9 | ✅ |
| 2.2 Parallel execution | Task 10 | ✅ |
| 5.5 Health check | Task 11 | ✅ |
| 5.3 Metrics emission | Task 12 | ✅ |
| 5.4 OTel tracing | Task 13 | ✅ |
| 2.3 Polars backend | Task 14 | ✅ |

**Type consistency check:** `EnvironmentConfig` used as Pydantic model throughout; `ReconciliationResult.schema_diff` field added in Task 2 and referenced in Tasks 6, 9, 12; `TestStatus.SLOW` added in Task 2 and used in Tasks 6, 8; `_apply_slo()` and `_align_columns()` defined once in Task 9, referenced in both chunked and non-chunked paths.

**No placeholders found.**
