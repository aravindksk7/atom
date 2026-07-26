# AWS S3 Job Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add tracked S3 row-count, format-validation, and partition-check jobs with type-aware schema assertion and AWS-tab job creation controls.

**Architecture:** Extend the existing FastAPI/runner job model instead of adding a parallel AWS execution path. The S3 executor builds on Phase 1 AWS config resolution and S3 service/backend functions, then maps outcomes into `ReconciliationResult` so History, scheduler, DQ, contracts, and reports keep working. Type-aware schema comparison lives in the `aws_s3` validation layer and is exposed both through ad-hoc routes and tracked job execution.

**Tech Stack:** Python 3.11+, FastAPI, pydantic v2, SQLAlchemy, boto3/pyarrow, pytest, moto; vanilla JS + Alpine.js frontend, Node build step.

## Global Constraints

- No Glue, Athena, or Airflow backend implementation in this plan.
- No S3 mutation or write-back behavior.
- No new runtime dependencies.
- Reuse saved AWS config fields from `EnvironmentConfig` via `aws_config_from_env`.
- Store S3 job outcomes through existing `ReconciliationResult` and run persistence.
- Add exact type mismatch output with `column`, `expected_type`, and `actual_type`.
- Preserve existing ad-hoc `/api/aws/s3/*` behavior except adding `type_mismatches` detail for schema drift.
- Use TDD for each task: failing test, failing run, minimal implementation, passing run, commit.

---

## File Structure

**Modify:**
- `etl_framework/exceptions.py` — add optional `type_mismatches` support to `SchemaValidationError`.
- `etl_framework/aws_s3/formats.py` — normalize schema types and compare missing/extra/type mismatch categories.
- `api/routes/aws_s3.py` — include `type_mismatches` in schema-validation HTTP detail.
- `api/schemas.py` — add S3 job types to `JobDefinition.job_type` and validate required S3 params in pydantic.
- `etl_framework/runner/job_validation.py` — add non-pydantic validation issues for S3 job definitions.
- `api/services/run_executor.py` — dispatch and execute S3 job types, producing `ReconciliationResult`.
- `frontend/features/aws.js` — add S3 job creation state and methods.
- `frontend/partials/tab-aws.html` — add tracked job controls in the S3 panel.
- `frontend/index.html` — regenerated output from `scripts/build-html.js`.

**Create:**
- `api/services/aws_s3_runtime.py` — shared runtime helpers for resolving saved config and building S3 clients/filesystems without FastAPI coupling.
- `tests/unit/test_aws_s3_type_schema.py` — type-aware S3 schema validation unit tests.
- `tests/unit/test_run_executor_s3.py` — executor pass/fail/error tests for S3 jobs.

**Existing tests to extend:**
- `tests/unit/test_aws_s3_routes.py` — assert `type_mismatches` appears in route error detail.
- `tests/unit/test_job_validation.py` — add validation coverage for S3 job definitions.
- `tests/integration/test_aws_ui_smoke.py` — assert S3 create-job controls render.

---

### Task 1: Type-Aware S3 Schema Validation

**Files:**
- Modify: `etl_framework/exceptions.py:76-90`
- Modify: `etl_framework/aws_s3/formats.py:18-92`
- Modify: `api/routes/aws_s3.py:29-38`
- Modify: `tests/unit/test_aws_s3_routes.py:70-80`
- Create: `tests/unit/test_aws_s3_type_schema.py`

**Interfaces:**
- Consumes: `validate_format(client, bucket, key, fmt, expected_schema)` from `etl_framework/aws_s3/formats.py`.
- Produces: `normalize_schema_type(type_name: str) -> str`, `compare_expected_schema(expected: dict[str, str], actual: dict[str, str]) -> dict[str, Any]`, and `SchemaValidationError(..., type_mismatches: list[dict[str, str]] | None = None)`.

- [ ] **Step 1: Write the failing type-aware schema tests**

Create `tests/unit/test_aws_s3_type_schema.py`:

```python
from __future__ import annotations

import pytest

from etl_framework.aws_s3.formats import compare_expected_schema, normalize_schema_type, validate_format
from etl_framework.exceptions import SchemaValidationError


class FakeS3Client:
    def __init__(self, objects: dict[tuple[str, str], bytes]) -> None:
        self.objects = objects

    def get_object(self, bucket: str, key: str) -> bytes:
        return self.objects[(bucket, key)]


def test_normalize_schema_type_collapses_safe_aliases():
    assert normalize_schema_type(" INTEGER ") == "int64"
    assert normalize_schema_type("long") == "int64"
    assert normalize_schema_type("DOUBLE") == "float64"
    assert normalize_schema_type("str") == "string"
    assert normalize_schema_type("boolean") == "bool"
    assert normalize_schema_type("decimal(12, 2)") == "decimal(12,2)"


def test_compare_expected_schema_reports_missing_extra_and_type_mismatch():
    result = compare_expected_schema(
        {"id": "int64", "amount": "decimal(12,2)", "email": "string"},
        {"id": "integer", "amount": "string", "name": "string"},
    )

    assert result == {
        "missing_in_target": ["email"],
        "extra_in_target": ["name"],
        "type_mismatches": [
            {"column": "amount", "expected_type": "decimal(12,2)", "actual_type": "string"}
        ],
    }


def test_validate_format_raises_type_mismatches_for_csv_schema():
    client = FakeS3Client({("b", "orders.csv"): b"id,amount\n1,10.5\n"})

    with pytest.raises(SchemaValidationError) as err:
        validate_format(
            client,
            "b",
            "orders.csv",
            "csv",
            expected_schema={"id": "string", "amount": "decimal(12,2)"},
        )

    assert err.value.missing_in_target == []
    assert err.value.extra_in_target == []
    assert err.value.type_mismatches == [
        {"column": "amount", "expected_type": "decimal(12,2)", "actual_type": "string"}
    ]
```

Extend `tests/unit/test_aws_s3_routes.py` in `test_schema_drift_maps_to_400_with_columns`:

```python
def test_schema_drift_maps_to_400_with_columns(client, mock_service):
    mock_service.validate_format.side_effect = SchemaValidationError(
        "s3://b/k",
        missing_in_target=["email"],
        extra_in_target=["name"],
        type_mismatches=[{"column": "amount", "expected_type": "decimal(12,2)", "actual_type": "string"}],
    )
    r = client.post("/api/aws/s3/validate-format",
                    json={"config_id": 1, "bucket": "b", "key": "k", "fmt": "parquet",
                          "expected_schema": {"id": "int64", "email": "string"}})
    assert r.status_code == 400
    body = r.json()["detail"]
    assert body["error_type"] == "schema_validation"
    assert body["missing_in_target"] == ["email"]
    assert body["extra_in_target"] == ["name"]
    assert body["type_mismatches"] == [
        {"column": "amount", "expected_type": "decimal(12,2)", "actual_type": "string"}
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_aws_s3_type_schema.py tests/unit/test_aws_s3_routes.py::test_schema_drift_maps_to_400_with_columns -v`

Expected: FAIL with import error for `compare_expected_schema`/`normalize_schema_type` or `SchemaValidationError.__init__()` not accepting `type_mismatches`.

- [ ] **Step 3: Implement type-aware schema comparison**

Update `etl_framework/exceptions.py`:

```python
class SchemaValidationError(ETLFrameworkError):
    def __init__(
        self,
        query_name: str,
        missing_in_target: list[str],
        extra_in_target: list[str],
        type_mismatches: list[dict[str, str]] | None = None,
    ) -> None:
        self.query_name = query_name
        self.missing_in_target = missing_in_target
        self.extra_in_target = extra_in_target
        self.type_mismatches = type_mismatches or []
        super().__init__(
            f"Schema mismatch in '{query_name}': "
            f"missing_in_target={missing_in_target}, "
            f"extra_in_target={extra_in_target}, "
            f"type_mismatches={self.type_mismatches}"
        )
```

Update `etl_framework/aws_s3/formats.py` by adding helpers after imports:

```python
_TYPE_ALIASES = {
    "integer": "int64",
    "long": "int64",
    "double": "float64",
    "str": "string",
    "boolean": "bool",
}


def normalize_schema_type(type_name: str) -> str:
    normalized = "".join(str(type_name).strip().lower().split())
    return _TYPE_ALIASES.get(normalized, normalized)


def compare_expected_schema(expected: dict[str, str], actual: dict[str, str]) -> dict[str, object]:
    expected_names = {str(name) for name in expected}
    actual_names = {str(name) for name in actual}
    common = sorted(expected_names & actual_names)
    type_mismatches: list[dict[str, str]] = []
    for column in common:
        expected_type = normalize_schema_type(expected[column])
        actual_type = normalize_schema_type(actual[column])
        if expected_type != actual_type:
            type_mismatches.append({
                "column": column,
                "expected_type": expected_type,
                "actual_type": actual_type,
            })
    return {
        "missing_in_target": sorted(expected_names - actual_names),
        "extra_in_target": sorted(actual_names - expected_names),
        "type_mismatches": type_mismatches,
    }
```

Then replace the schema check in `validate_format`:

```python
    actual = _actual_schema(fmt, data)
    comparison = compare_expected_schema(expected_schema, actual)
    missing = comparison["missing_in_target"]
    extra = comparison["extra_in_target"]
    type_mismatches = comparison["type_mismatches"]
    if missing or extra or type_mismatches:
        raise SchemaValidationError(
            query_name=f"s3://{bucket}/{key}",
            missing_in_target=missing,
            extra_in_target=extra,
            type_mismatches=type_mismatches,
        )
```

Update `api/routes/aws_s3.py` schema error detail:

```python
    except SchemaValidationError as exc:
        raise HTTPException(status_code=400, detail={
            "error_type": "schema_validation",
            "message": str(exc),
            "missing_in_target": exc.missing_in_target,
            "extra_in_target": exc.extra_in_target,
            "type_mismatches": exc.type_mismatches,
        }) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_aws_s3_type_schema.py tests/unit/test_aws_s3_routes.py::test_schema_drift_maps_to_400_with_columns -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/exceptions.py etl_framework/aws_s3/formats.py api/routes/aws_s3.py tests/unit/test_aws_s3_type_schema.py tests/unit/test_aws_s3_routes.py
git commit -m "feat(aws-s3): compare schema types"
```

---

### Task 2: S3 Job Definition Validation

**Files:**
- Modify: `api/schemas.py:447-529`
- Modify: `etl_framework/runner/job_validation.py:20-94`
- Modify: `tests/unit/test_job_validation.py`

**Interfaces:**
- Consumes: `api.schemas.JobDefinition` and `validate_job_definition(job: Any) -> list[ValidationIssue]`.
- Produces: accepted `job_type` values `s3_row_count`, `s3_format_validation`, `s3_partition_check`; reusable validator helpers `_validate_s3_row_count`, `_validate_s3_format_validation`, `_validate_s3_partition_check`.

- [ ] **Step 1: Write failing validation tests**

Append to `tests/unit/test_job_validation.py`:

```python
def test_s3_row_count_valid_job_has_no_issues():
    issues = validate_job_definition({
        "name": "orders_rows",
        "job_type": "s3_row_count",
        "params": {"config_id": 1, "bucket": "b", "key": "orders.csv", "fmt": "csv", "min_rows": 1, "max_rows": 10},
    })
    assert issues == []


def test_s3_row_count_requires_identity_and_valid_bounds():
    issues = validate_job_definition({
        "name": "orders_rows",
        "job_type": "s3_row_count",
        "params": {"config_id": 1, "bucket": "", "key": "", "fmt": "xml", "min_rows": 10, "max_rows": 1},
    })
    fields = {issue.field for issue in issues}
    assert fields == {"params.bucket", "params.key", "params.fmt", "params.min_rows"}


def test_s3_format_validation_requires_schema_mapping_when_present():
    issues = validate_job_definition({
        "name": "orders_schema",
        "job_type": "s3_format_validation",
        "params": {"config": "qa", "bucket": "b", "key": "orders.csv", "fmt": "csv", "expected_schema": ["id"]},
    })
    assert any(issue.field == "params.expected_schema" for issue in issues)


def test_s3_partition_check_validates_columns_and_minimum():
    issues = validate_job_definition({
        "name": "orders_partitions",
        "job_type": "s3_partition_check",
        "params": {"config_id": 1, "bucket": "b", "prefix": "orders/", "expected_columns": ["dt", "region"], "min_partitions": 1},
    })
    assert issues == []

    bad = validate_job_definition({
        "name": "orders_partitions",
        "job_type": "s3_partition_check",
        "params": {"config_id": 1, "bucket": "b", "prefix": "", "expected_columns": ["dt", 3], "min_partitions": -1},
    })
    fields = {issue.field for issue in bad}
    assert fields == {"params.prefix", "params.expected_columns", "params.min_partitions"}
```

Also add a pydantic acceptance check:

```python
def test_job_definition_accepts_s3_job_types():
    for job_type in ("s3_row_count", "s3_format_validation", "s3_partition_check"):
        job = JobDefinition(
            name=job_type,
            job_type=job_type,
            params={"config_id": 1, "bucket": "b", "key": "k", "prefix": "p", "fmt": "csv"},
        )
        assert job.job_type == job_type
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_job_validation.py -v`

Expected: FAIL because `JobDefinition.job_type` Literal does not include S3 job types and `validate_job_definition` does not emit S3 validation issues.

- [ ] **Step 3: Add pydantic and validation support**

Update `api/schemas.py` job type literal:

```python
        "bo_job", "ds_job", "s3_row_count", "s3_format_validation", "s3_partition_check",
```

Add S3 validation branch before `return self` in `JobDefinition.validate_reconciliation_contract`:

```python
        elif self.job_type in ("s3_row_count", "s3_format_validation"):
            if not (self.params.get("config_id") or self.params.get("config")):
                raise ValueError(f"{self.job_type} jobs require 'config_id' or 'config' in params")
            for field in ("bucket", "key", "fmt"):
                if not self.params.get(field):
                    raise ValueError(f"{self.job_type} jobs require '{field}' in params")
        elif self.job_type == "s3_partition_check":
            if not (self.params.get("config_id") or self.params.get("config")):
                raise ValueError("s3_partition_check jobs require 'config_id' or 'config' in params")
            for field in ("bucket", "prefix"):
                if not self.params.get(field):
                    raise ValueError(f"s3_partition_check jobs require '{field}' in params")
```

Update `etl_framework/runner/job_validation.py` by adding constants and helpers after imports:

```python
S3_FORMATS = {"csv", "json", "parquet", "orc"}


def _params(job: Any) -> dict[str, Any]:
    if isinstance(job, dict):
        return dict(job.get("params") or {})
    return dict(getattr(job, "params", {}) or {})


def _job_type(job: Any) -> str:
    if isinstance(job, dict):
        return str(job.get("job_type") or "reconciliation")
    return str(getattr(job, "job_type", "reconciliation"))


def _has_config_ref(params: dict[str, Any]) -> bool:
    return bool(params.get("config_id") or params.get("config"))


def _require_non_empty(params: dict[str, Any], field: str, issues: list[ValidationIssue]) -> None:
    if not params.get(field):
        issues.append(ValidationIssue(f"params.{field}", f"S3 jobs require '{field}' in params"))


def _non_negative_int(params: dict[str, Any], field: str, issues: list[ValidationIssue]) -> int | None:
    if field not in params or params.get(field) in (None, ""):
        return None
    try:
        value = int(params[field])
    except (TypeError, ValueError):
        issues.append(ValidationIssue(f"params.{field}", f"{field} must be a non-negative integer"))
        return None
    if value < 0:
        issues.append(ValidationIssue(f"params.{field}", f"{field} must be a non-negative integer"))
        return None
    return value


def _validate_s3_common(params: dict[str, Any], issues: list[ValidationIssue], fields: tuple[str, ...]) -> None:
    if not _has_config_ref(params):
        issues.append(ValidationIssue("params.config_id", "S3 jobs require 'config_id' or 'config' in params"))
    for field in fields:
        _require_non_empty(params, field, issues)


def _validate_s3_format(params: dict[str, Any], issues: list[ValidationIssue]) -> None:
    fmt = params.get("fmt")
    if fmt not in S3_FORMATS:
        issues.append(ValidationIssue("params.fmt", "fmt must be one of csv, json, parquet, or orc"))


def _validate_s3_row_count(params: dict[str, Any], issues: list[ValidationIssue]) -> None:
    _validate_s3_common(params, issues, ("bucket", "key"))
    _validate_s3_format(params, issues)
    min_rows = _non_negative_int(params, "min_rows", issues)
    max_rows = _non_negative_int(params, "max_rows", issues)
    if min_rows is not None and max_rows is not None and min_rows > max_rows:
        issues.append(ValidationIssue("params.min_rows", "min_rows must be less than or equal to max_rows"))


def _validate_s3_format_validation(params: dict[str, Any], issues: list[ValidationIssue]) -> None:
    _validate_s3_common(params, issues, ("bucket", "key"))
    _validate_s3_format(params, issues)
    expected_schema = params.get("expected_schema")
    if expected_schema is not None:
        if not isinstance(expected_schema, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in expected_schema.items()):
            issues.append(ValidationIssue("params.expected_schema", "expected_schema must map column names to type strings"))


def _validate_s3_partition_check(params: dict[str, Any], issues: list[ValidationIssue]) -> None:
    _validate_s3_common(params, issues, ("bucket", "prefix"))
    _non_negative_int(params, "min_partitions", issues)
    expected_columns = params.get("expected_columns")
    if expected_columns is not None:
        if not isinstance(expected_columns, list) or not expected_columns or not all(isinstance(v, str) and v for v in expected_columns):
            issues.append(ValidationIssue("params.expected_columns", "expected_columns must be a non-empty list of strings"))
```

Inside `validate_job_definition`, after `issues: list[ValidationIssue] = []`, add:

```python
    job_type = _job_type(job)
    params = _params(job)
    if job_type == "s3_row_count":
        _validate_s3_row_count(params, issues)
    elif job_type == "s3_format_validation":
        _validate_s3_format_validation(params, issues)
    elif job_type == "s3_partition_check":
        _validate_s3_partition_check(params, issues)
```

Keep the existing validation logic below this addition so non-S3 jobs retain current behavior.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_job_validation.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py etl_framework/runner/job_validation.py tests/unit/test_job_validation.py
git commit -m "feat(aws-s3): validate tracked jobs"
```

---

### Task 3: Shared S3 Runtime Helper

**Files:**
- Create: `api/services/aws_s3_runtime.py`
- Modify: `api/services/aws_s3_service.py:13-60`
- Test: `tests/unit/test_aws_s3_service.py`

**Interfaces:**
- Consumes: `ConfigRepository.get(config_id)`, `EnvironmentConfig`, `aws_config_from_env`, `AWSSession`, `S3Client`.
- Produces: `AwsS3Runtime(config_repo: ConfigRepository)`, with methods `env(config_id: int) -> EnvironmentConfig`, `client(config_id: int, override: Any | None = None) -> S3Client`, and `filesystem(config_id: int) -> pyarrow.fs.FileSystem`.

- [ ] **Step 1: Write the failing runtime helper test**

Append to `tests/unit/test_aws_s3_service.py`:

```python
def test_aws_s3_service_uses_runtime_for_missing_config(config_repo):
    from fastapi import HTTPException
    from api.services.aws_s3_runtime import AwsS3Runtime

    runtime = AwsS3Runtime(config_repo)
    with pytest.raises(HTTPException) as err:
        runtime.env(999)

    assert err.value.status_code == 404
    assert err.value.detail == "Config not found"
```

If `tests/unit/test_aws_s3_service.py` has no `config_repo` fixture, add this local fixture using its existing database/repository pattern:

```python
@pytest.fixture
def config_repo(db_session):
    from etl_framework.repository.repository import ConfigRepository
    return ConfigRepository(db_session)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_aws_s3_service.py::test_aws_s3_service_uses_runtime_for_missing_config -v`

Expected: FAIL with `ModuleNotFoundError: api.services.aws_s3_runtime`.

- [ ] **Step 3: Implement shared runtime helper and refactor service**

Create `api/services/aws_s3_runtime.py`:

```python
from __future__ import annotations

from typing import Any

import pyarrow.fs as pafs
from fastapi import HTTPException

from etl_framework.aws.config import aws_config_from_env
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.config.models import EnvironmentConfig
from etl_framework.repository.repository import ConfigRepository


class AwsS3Runtime:
    def __init__(self, config_repo: ConfigRepository) -> None:
        self._config_repo = config_repo

    def env(self, config_id: int) -> EnvironmentConfig:
        cfg = self._config_repo.get(config_id)
        if cfg is None:
            raise HTTPException(status_code=404, detail="Config not found")
        return EnvironmentConfig(name=cfg.env_name, **cfg.config_json)

    def client(self, config_id: int, override: Any | None = None) -> S3Client:
        if override is not None:
            session = AWSSession.__new__(AWSSession)
            session._cfg = None
            session._clients = {"s3": override}
            return S3Client(session)
        return S3Client(AWSSession(aws_config_from_env(self.env(config_id))))

    def filesystem(self, config_id: int) -> pafs.FileSystem:
        env = self.env(config_id)
        cfg = aws_config_from_env(env)
        kwargs: dict[str, Any] = {}
        if cfg.region:
            kwargs["region"] = cfg.region
        if cfg.access_key_id:
            kwargs["access_key"] = cfg.access_key_id
            kwargs["secret_key"] = cfg.secret_access_key
            if cfg.session_token:
                kwargs["session_token"] = cfg.session_token
        if cfg.endpoint_url:
            kwargs["endpoint_override"] = cfg.endpoint_url
        return pafs.S3FileSystem(**kwargs)
```

Update `api/services/aws_s3_service.py` imports to remove direct runtime construction imports and add:

```python
from api.services.aws_s3_runtime import AwsS3Runtime
```

Update `AwsS3Service` internals:

```python
    def __init__(self, config_repo: ConfigRepository) -> None:
        self._runtime = AwsS3Runtime(config_repo)
        self._s3_client_override = None

    def _client(self, config_id: int) -> S3Client:
        return self._runtime.client(config_id, self._s3_client_override)

    def _fs(self, config_id: int) -> pafs.FileSystem:
        return self._runtime.filesystem(config_id)
```

Remove the old `_env` method from `AwsS3Service`.

- [ ] **Step 4: Run service tests to verify they pass**

Run: `pytest tests/unit/test_aws_s3_service.py tests/unit/test_aws_s3_routes.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/aws_s3_runtime.py api/services/aws_s3_service.py tests/unit/test_aws_s3_service.py
git commit -m "refactor(aws-s3): share runtime setup"
```

---

### Task 4: Run Executor S3 Job Types

**Files:**
- Modify: `api/services/run_executor.py:18-35,451-493,918-1140`
- Create: `tests/unit/test_run_executor_s3.py`

**Interfaces:**
- Consumes: `AwsS3Runtime`, `read_object_metadata`, `select_row_count`, `RowCounter`, `discover_partitions`, `validate_format`, `SchemaValidationError`, `ReconciliationResult`, `MismatchRecord`, `TestStatus`.
- Produces: `RunExecutor._build_case_s3_row_count`, `RunExecutor._execute_s3_row_count`, `RunExecutor._build_case_s3_format_validation`, `RunExecutor._execute_s3_format_validation`, `RunExecutor._build_case_s3_partition_check`, `RunExecutor._execute_s3_partition_check`.

- [ ] **Step 1: Write failing executor tests**

Create `tests/unit/test_run_executor_s3.py`:

```python
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.exceptions import SchemaValidationError
from etl_framework.repository.database import Base
from etl_framework.runner.state import TestStatus


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def executor(db_session):
    return RunExecutor(
        db=db_session,
        run_id="run-1",
        source_env="qa",
        target_env="prod",
        job_sequence=[],
        run_settings=RunSettings(use_live_connections=True),
    )


def test_execute_s3_row_count_passes_within_bounds(monkeypatch, db_session):
    ex = executor(db_session)
    monkeypatch.setattr("api.services.run_executor.AwsS3Runtime", lambda repo: SimpleNamespace(client=lambda config_id: object(), filesystem=lambda config_id: object()))
    monkeypatch.setattr("api.services.run_executor.select_row_count", lambda client, bucket, key, fmt: 5)

    result = ex._execute_s3_row_count(JobDefinition(
        name="orders_rows",
        job_type="s3_row_count",
        params={"config_id": 1, "bucket": "b", "key": "orders.csv", "fmt": "csv", "min_rows": 1, "max_rows": 10},
    ))

    assert result.status == TestStatus.PASSED
    assert result.source_row_count == 5
    assert result.target_row_count == 5
    assert result.mismatch_summary["metrics"]["row_count"] == 5
    assert result.mismatch_summary["metrics"]["engine"] == "s3_select"


def test_execute_s3_row_count_fails_outside_bounds(monkeypatch, db_session):
    ex = executor(db_session)
    monkeypatch.setattr("api.services.run_executor.AwsS3Runtime", lambda repo: SimpleNamespace(client=lambda config_id: object(), filesystem=lambda config_id: object()))
    monkeypatch.setattr("api.services.run_executor.select_row_count", lambda client, bucket, key, fmt: 0)

    result = ex._execute_s3_row_count(JobDefinition(
        name="orders_rows",
        job_type="s3_row_count",
        params={"config_id": 1, "bucket": "b", "key": "orders.csv", "fmt": "csv", "min_rows": 1},
    ))

    assert result.status == TestStatus.FAILED
    assert result.value_mismatch_count == 1
    assert result.mismatches[0].mismatch_type == "row_count_below_min"


def test_execute_s3_format_validation_fails_schema_drift(monkeypatch, db_session):
    ex = executor(db_session)
    monkeypatch.setattr("api.services.run_executor.AwsS3Runtime", lambda repo: SimpleNamespace(client=lambda config_id: object()))

    def drift(client, bucket, key, fmt, expected_schema):
        raise SchemaValidationError(
            "s3://b/orders.csv",
            missing_in_target=["email"],
            extra_in_target=["name"],
            type_mismatches=[{"column": "amount", "expected_type": "decimal(12,2)", "actual_type": "string"}],
        )

    monkeypatch.setattr("api.services.run_executor.validate_format", drift)

    result = ex._execute_s3_format_validation(JobDefinition(
        name="orders_schema",
        job_type="s3_format_validation",
        params={"config_id": 1, "bucket": "b", "key": "orders.csv", "fmt": "csv", "expected_schema": {"amount": "decimal(12,2)"}},
    ))

    assert result.status == TestStatus.FAILED
    assert {m.mismatch_type for m in result.mismatches} == {"missing_columns", "extra_columns", "type_mismatch"}
    assert result.mismatch_summary["schema_diff"]["type_mismatches"] == [
        {"column": "amount", "expected_type": "decimal(12,2)", "actual_type": "string"}
    ]


def test_execute_s3_partition_check_fails_column_and_count(monkeypatch, db_session):
    ex = executor(db_session)
    monkeypatch.setattr("api.services.run_executor.AwsS3Runtime", lambda repo: SimpleNamespace(client=lambda config_id: object()))
    monkeypatch.setattr("api.services.run_executor.discover_partitions", lambda client, bucket, prefix: SimpleNamespace(
        columns=["dt"],
        entries=[SimpleNamespace(object_count=1)],
    ))

    result = ex._execute_s3_partition_check(JobDefinition(
        name="orders_partitions",
        job_type="s3_partition_check",
        params={"config_id": 1, "bucket": "b", "prefix": "orders/", "expected_columns": ["dt", "region"], "min_partitions": 2},
    ))

    assert result.status == TestStatus.FAILED
    assert {m.mismatch_type for m in result.mismatches} == {"partition_columns_mismatch", "partition_count_below_min"}
    assert result.mismatch_summary["metrics"]["partition_count"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_run_executor_s3.py -v`

Expected: FAIL because `api.services.run_executor` does not import S3 helpers and `RunExecutor` has no `_execute_s3_*` methods.

- [ ] **Step 3: Add imports and dispatch**

Add imports near the top of `api/services/run_executor.py`:

```python
from api.services.aws_s3_runtime import AwsS3Runtime
from etl_framework.aws_s3.formats import validate_format
from etl_framework.aws_s3.partitions import discover_partitions
from etl_framework.aws_s3.row_count import RowCounter, select_row_count
from etl_framework.exceptions import SchemaValidationError
from etl_framework.repository.repository import ConfigRepository
```

Add dispatch cases near existing `_build_case` specialized jobs:

```python
        if job.job_type == "s3_row_count":
            return self._build_case_s3_row_count(job)
        if job.job_type == "s3_format_validation":
            return self._build_case_s3_format_validation(job)
        if job.job_type == "s3_partition_check":
            return self._build_case_s3_partition_check(job)
```

- [ ] **Step 4: Add S3 executor helpers**

Add this block before the freshness section in `api/services/run_executor.py`:

```python
    # ── AWS S3 Jobs ────────────────────────────────────────────────────────

    def _s3_runtime(self) -> AwsS3Runtime:
        return AwsS3Runtime(ConfigRepository(self._db))

    def _s3_config_id(self, job: JobDefinition) -> int:
        raw = job.params.get("config_id") or job.params.get("config")
        return int(raw)

    def _s3_result(
        self,
        job: JobDefinition,
        status: TestStatus,
        metrics: dict[str, Any],
        mismatches: list[MismatchRecord],
        executed_at: datetime,
        duration_seconds: float,
    ) -> ReconciliationResult:
        return ReconciliationResult(
            query_name=job.name,
            source_env=self._source_env,
            target_env=self._target_env,
            source_row_count=int(metrics.get("row_count") or metrics.get("partition_count") or 0),
            target_row_count=int(metrics.get("row_count") or metrics.get("partition_count") or 0),
            matched_count=0 if mismatches else 1,
            missing_in_target_count=0,
            missing_in_source_count=0,
            value_mismatch_count=len(mismatches),
            mismatches=mismatches,
            status=status,
            executed_at=executed_at,
            duration_seconds=duration_seconds,
            mismatch_summary={"metrics": metrics, "by_type": {m.mismatch_type: 1 for m in mismatches}},
        )

    def _build_case_s3_row_count(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            return self._execute_s3_row_count(job)
        return run_job

    def _execute_s3_row_count(self, job: JobDefinition) -> ReconciliationResult:
        t0 = time.monotonic()
        executed_at = datetime.now(timezone.utc)
        p = job.params
        bucket = str(p["bucket"])
        key = str(p["key"])
        fmt = str(p["fmt"])
        runtime = self._s3_runtime()
        try:
            client = runtime.client(self._s3_config_id(job))
            if fmt in ("csv", "json"):
                row_count = select_row_count(client, bucket, key, fmt)
                engine = "s3_select"
            else:
                row_result = RowCounter(client, fs=runtime.filesystem(self._s3_config_id(job))).count(bucket, key, fmt)
                row_count = row_result.row_count
                engine = row_result.engine
        except Exception as exc:
            return self._s3_result(job, TestStatus.ERROR, {"bucket": bucket, "key": key, "fmt": fmt, "error": str(exc)}, [
                MismatchRecord({"job": job.name}, "s3", "ok", str(exc), "s3_error")
            ], executed_at, time.monotonic() - t0)

        metrics = {"bucket": bucket, "key": key, "fmt": fmt, "row_count": int(row_count), "engine": engine}
        mismatches: list[MismatchRecord] = []
        if p.get("min_rows") not in (None, "") and row_count < int(p["min_rows"]):
            mismatches.append(MismatchRecord({"job": job.name}, "row_count", int(p["min_rows"]), row_count, "row_count_below_min"))
        if p.get("max_rows") not in (None, "") and row_count > int(p["max_rows"]):
            mismatches.append(MismatchRecord({"job": job.name}, "row_count", int(p["max_rows"]), row_count, "row_count_above_max"))
        return self._s3_result(job, TestStatus.FAILED if mismatches else TestStatus.PASSED, metrics, mismatches, executed_at, time.monotonic() - t0)

    def _build_case_s3_format_validation(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            return self._execute_s3_format_validation(job)
        return run_job

    def _execute_s3_format_validation(self, job: JobDefinition) -> ReconciliationResult:
        t0 = time.monotonic()
        executed_at = datetime.now(timezone.utc)
        p = job.params
        bucket = str(p["bucket"])
        key = str(p["key"])
        fmt = str(p["fmt"])
        expected_schema = p.get("expected_schema")
        metrics = {"bucket": bucket, "key": key, "fmt": fmt, "parsed": False, "schema_ok": False}
        try:
            result = validate_format(self._s3_runtime().client(self._s3_config_id(job)), bucket, key, fmt, expected_schema)
            metrics.update({"parsed": bool(result.parsed), "schema_ok": result.schema_ok is not False})
            return self._s3_result(job, TestStatus.PASSED, metrics, [], executed_at, time.monotonic() - t0)
        except SchemaValidationError as exc:
            mismatches: list[MismatchRecord] = []
            for column in exc.missing_in_target:
                mismatches.append(MismatchRecord({"job": job.name}, column, "expected", "missing", "missing_columns"))
            for column in exc.extra_in_target:
                mismatches.append(MismatchRecord({"job": job.name}, column, "absent", "present", "extra_columns"))
            for item in exc.type_mismatches:
                mismatches.append(MismatchRecord({"job": job.name}, item["column"], item["expected_type"], item["actual_type"], "type_mismatch"))
            result = self._s3_result(job, TestStatus.FAILED, metrics, mismatches, executed_at, time.monotonic() - t0)
            result.mismatch_summary["schema_diff"] = {
                "missing_in_target": exc.missing_in_target,
                "extra_in_target": exc.extra_in_target,
                "type_mismatches": exc.type_mismatches,
            }
            return result
        except Exception as exc:
            return self._s3_result(job, TestStatus.ERROR, {**metrics, "error": str(exc)}, [
                MismatchRecord({"job": job.name}, "s3", "ok", str(exc), "s3_error")
            ], executed_at, time.monotonic() - t0)

    def _build_case_s3_partition_check(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            return self._execute_s3_partition_check(job)
        return run_job

    def _execute_s3_partition_check(self, job: JobDefinition) -> ReconciliationResult:
        t0 = time.monotonic()
        executed_at = datetime.now(timezone.utc)
        p = job.params
        bucket = str(p["bucket"])
        prefix = str(p["prefix"])
        try:
            scheme = discover_partitions(self._s3_runtime().client(self._s3_config_id(job)), bucket, prefix)
        except Exception as exc:
            return self._s3_result(job, TestStatus.ERROR, {"bucket": bucket, "prefix": prefix, "error": str(exc)}, [
                MismatchRecord({"job": job.name}, "s3", "ok", str(exc), "s3_error")
            ], executed_at, time.monotonic() - t0)
        entries = list(scheme.entries)
        object_count = sum(int(getattr(entry, "object_count", 0) or 0) for entry in entries)
        metrics = {"bucket": bucket, "prefix": prefix, "partition_count": len(entries), "partition_columns": list(scheme.columns), "object_count": object_count}
        mismatches: list[MismatchRecord] = []
        expected_columns = p.get("expected_columns")
        if expected_columns is not None and list(expected_columns) != list(scheme.columns):
            mismatches.append(MismatchRecord({"job": job.name}, "partition_columns", list(expected_columns), list(scheme.columns), "partition_columns_mismatch"))
        if p.get("min_partitions") not in (None, "") and len(entries) < int(p["min_partitions"]):
            mismatches.append(MismatchRecord({"job": job.name}, "partition_count", int(p["min_partitions"]), len(entries), "partition_count_below_min"))
        return self._s3_result(job, TestStatus.FAILED if mismatches else TestStatus.PASSED, metrics, mismatches, executed_at, time.monotonic() - t0)
```

- [ ] **Step 5: Run executor tests to verify they pass**

Run: `pytest tests/unit/test_run_executor_s3.py -v`

Expected: PASS.

- [ ] **Step 6: Run focused runner tests**

Run: `pytest tests/unit/test_run_executor.py tests/unit/test_run_executor_api_reconciliation.py tests/unit/test_run_executor_gates.py tests/unit/test_run_executor_s3.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_executor_s3.py
git commit -m "feat(aws-s3): execute tracked jobs"
```

---

### Task 5: AWS Tab S3 Job Creation Controls

**Files:**
- Modify: `frontend/features/aws.js:15-101`
- Modify: `frontend/partials/tab-aws.html:55-139`
- Modify: `frontend/index.html` via build script
- Modify: `tests/integration/test_aws_ui_smoke.py`

**Interfaces:**
- Consumes: existing global `api(method, path, body)` helper and `loadJobs()` from frontend feature slices.
- Produces: `awsCreateRowCountJob()`, `awsCreateFormatValidationJob()`, `awsCreatePartitionCheckJob()`, `awsJobError`, `awsJobName`, threshold fields, expected partition fields.

- [ ] **Step 1: Write failing UI smoke assertions**

Extend `tests/integration/test_aws_ui_smoke.py` with assertions near existing AWS-tab checks:

```python
def test_aws_tab_contains_s3_job_creation_controls():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert "data-testid=\"aws-create-row-count-job-btn\"" in html
    assert "data-testid=\"aws-create-format-validation-job-btn\"" in html
    assert "data-testid=\"aws-create-partition-check-job-btn\"" in html
    assert "data-testid=\"aws-job-name-input\"" in html
    assert "data-testid=\"aws-min-rows-input\"" in html
    assert "data-testid=\"aws-expected-columns-input\"" in html
```

If `Path` is not imported, add:

```python
from pathlib import Path
```

- [ ] **Step 2: Run smoke test to verify it fails**

Run: `pytest tests/integration/test_aws_ui_smoke.py::test_aws_tab_contains_s3_job_creation_controls -v`

Expected: FAIL because the create-job controls are not in `frontend/index.html`.

- [ ] **Step 3: Add AWS feature state and methods**

Update the returned object in `frontend/features/aws.js` state block:

```javascript
      awsJobName: '',
      awsMinRows: '',
      awsMaxRows: '',
      awsExpectedColumnsRaw: '',
      awsMinPartitions: '',
      awsJobError: null,
```

Add these methods before the closing `return` object brace:

```javascript
      _awsDefaultJobName(kind) {
        const base = [kind, this.awsBucket, this.awsKey || this.awsPrefix]
          .filter(Boolean).join('_').replace(/[^a-z0-9_]+/gi, '_').toLowerCase();
        return base || kind;
      },

      _awsJobParams(common) {
        return Object.assign({ config_id: Number(this.awsConfigId), bucket: this.awsBucket }, common);
      },

      async _awsCreateJob(kind, params) {
        this.awsJobError = null;
        const name = (this.awsJobName || this._awsDefaultJobName(kind)).trim();
        try {
          await api('POST', '/api/jobs', {
            name,
            job_type: kind,
            params,
            key_columns: [],
          });
          if (this.loadJobs) await this.loadJobs();
          this.toast('success', 'S3 job created', name);
          this.awsJobName = '';
        } catch (e) {
          this.awsJobError = e.message;
          this.toast('error', 'S3 job creation failed', e.message);
        }
      },

      async awsCreateRowCountJob() {
        const params = this._awsJobParams({ key: this.awsKey, fmt: this.awsFmt });
        if (this.awsMinRows !== '') params.min_rows = Number(this.awsMinRows);
        if (this.awsMaxRows !== '') params.max_rows = Number(this.awsMaxRows);
        await this._awsCreateJob('s3_row_count', params);
      },

      async awsCreateFormatValidationJob() {
        let expected = null;
        if (this.awsExpectedSchemaRaw.trim()) {
          try {
            expected = JSON.parse(this.awsExpectedSchemaRaw);
          } catch (e) {
            this.awsJobError = 'expected_schema must be valid JSON';
            return;
          }
        }
        const params = this._awsJobParams({ key: this.awsKey, fmt: this.awsFmt });
        if (expected) params.expected_schema = expected;
        await this._awsCreateJob('s3_format_validation', params);
      },

      async awsCreatePartitionCheckJob() {
        const params = this._awsJobParams({ prefix: this.awsPrefix });
        const expectedColumns = this.awsExpectedColumnsRaw.split(',').map(s => s.trim()).filter(Boolean);
        if (expectedColumns.length) params.expected_columns = expectedColumns;
        if (this.awsMinPartitions !== '') params.min_partitions = Number(this.awsMinPartitions);
        await this._awsCreateJob('s3_partition_check', params);
      },
```

- [ ] **Step 4: Add HTML controls**

In `frontend/partials/tab-aws.html`, after the existing expected schema row and before the ad-hoc action buttons, add:

```html
      <div class="card mb-3">
        <div class="font-semibold text-slate-700 mb-2">Create tracked S3 job</div>
        <div class="grid-2 mb-3">
          <div>
            <label class="field-label">Job Name (optional)</label>
            <input x-model="awsJobName" class="field-input" placeholder="auto-generated from S3 path" data-testid="aws-job-name-input" />
          </div>
          <div>
            <label class="field-label">Expected Partition Columns</label>
            <input x-model="awsExpectedColumnsRaw" class="field-input" placeholder="dt, region" data-testid="aws-expected-columns-input" />
          </div>
        </div>
        <div class="grid-2 mb-3">
          <div>
            <label class="field-label">Min Rows</label>
            <input x-model="awsMinRows" type="number" min="0" class="field-input" placeholder="0" data-testid="aws-min-rows-input" />
          </div>
          <div>
            <label class="field-label">Max Rows</label>
            <input x-model="awsMaxRows" type="number" min="0" class="field-input" placeholder="1000000" data-testid="aws-max-rows-input" />
          </div>
        </div>
        <div class="grid-2 mb-3">
          <div>
            <label class="field-label">Min Partitions</label>
            <input x-model="awsMinPartitions" type="number" min="0" class="field-input" placeholder="1" data-testid="aws-min-partitions-input" />
          </div>
        </div>
        <div class="flex gap-2 flex-wrap mb-2">
          <button @click="awsCreateRowCountJob()" :disabled="awsLoading || !awsConfigId || !awsBucket || !awsKey" class="btn-secondary" data-testid="aws-create-row-count-job-btn">Create Row Count Job</button>
          <button @click="awsCreateFormatValidationJob()" :disabled="awsLoading || !awsConfigId || !awsBucket || !awsKey" class="btn-secondary" data-testid="aws-create-format-validation-job-btn">Create Format Validation Job</button>
          <button @click="awsCreatePartitionCheckJob()" :disabled="awsLoading || !awsConfigId || !awsBucket || !awsPrefix" class="btn-secondary" data-testid="aws-create-partition-check-job-btn">Create Partition Check Job</button>
        </div>
        <div x-show="awsJobError" class="bg-rose-50 border-rose-200 text-rose-700 border rounded-lg p-2 text-sm" data-testid="aws-job-error" x-text="awsJobError"></div>
      </div>
```

- [ ] **Step 5: Rebuild frontend**

Run: `node scripts/build-html.js`

Expected: exits 0, reports include count, and rewrites `frontend/index.html`.

- [ ] **Step 6: Run frontend checks**

Run: `node --check frontend/features/aws.js; if ($?) { pytest tests/integration/test_aws_ui_smoke.py -v }`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/features/aws.js frontend/partials/tab-aws.html frontend/index.html tests/integration/test_aws_ui_smoke.py
git commit -m "feat(aws-ui): create tracked S3 jobs"
```

---

### Task 6: Full Verification and Documentation Touch-Up

**Files:**
- Modify: `README.md` only if the current AWS-tab paragraph does not mention tracked S3 jobs.

**Interfaces:**
- Consumes: all prior task outputs.
- Produces: verified Phase 2 implementation with no unrelated files staged.

- [ ] **Step 1: Inspect README for AWS wording**

Run: `rg -n "AWS|S3|tracked" README.md`

Expected: shows whether the README already documents the AWS tab. If no tracked S3 job wording exists, proceed to Step 2. If it already describes tracked S3 jobs, skip Step 2 and do not edit README.

- [ ] **Step 2: Add README wording only if needed**

Add this sentence to the existing AWS-tab paragraph in `README.md`:

```markdown
The S3 panel can also create tracked row-count, format-validation, and partition-check jobs so the same checks flow through run history, scheduling, DQ gates, and reports.
```

- [ ] **Step 3: Run backend focused tests**

Run: `pytest tests/unit/test_aws_s3_type_schema.py tests/unit/test_aws_s3_routes.py tests/unit/test_aws_s3_service.py tests/unit/test_job_validation.py tests/unit/test_run_executor_s3.py -v`

Expected: PASS.

- [ ] **Step 4: Run executor regression tests**

Run: `pytest tests/unit/test_run_executor.py tests/unit/test_run_executor_api_reconciliation.py tests/unit/test_run_executor_gates.py tests/unit/test_run_executor_s3.py -v`

Expected: PASS.

- [ ] **Step 5: Run frontend verification**

Run: `node --check frontend/features/aws.js; if ($?) { node scripts/build-html.js }; if ($?) { pytest tests/integration/test_aws_ui_smoke.py -v }`

Expected: PASS, and `scripts/build-html.js` exits 0.

- [ ] **Step 6: Inspect git status and diff**

Run: `git status --short; git diff --stat; git diff -- README.md`

Expected: only intended files remain modified or no files remain modified if README was not changed.

- [ ] **Step 7: Commit README only if changed**

If README changed, run:

```bash
git add README.md
git commit -m "docs(aws): document tracked S3 jobs"
```

If README did not change, do not create a commit.

---

## Self-Review Notes

- **Spec coverage:** Type-aware schema comparison is Task 1. Pydantic and validation rules are Task 2. Shared AWS config/client resolution is Task 3. Executor job types, metrics, mismatches, and error/failure mapping are Task 4. AWS-tab job creation and smoke checks are Task 5. Final verification and README touch-up are Task 6.
- **Scope:** Glue, Athena, and Airflow remain placeholders and future specs only. No new storage path or dependency is introduced.
- **Type consistency:** The plan consistently uses `type_mismatches` for route/error payloads, `type_mismatch` for individual executor mismatch records, and the required function names from the approved spec: `_build_case_s3_row_count`, `_execute_s3_row_count`, `_build_case_s3_format_validation`, `_execute_s3_format_validation`, `_build_case_s3_partition_check`, `_execute_s3_partition_check`.
