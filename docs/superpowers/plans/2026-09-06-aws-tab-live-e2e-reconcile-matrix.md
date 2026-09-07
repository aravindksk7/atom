# AWS Tab Live E2E: S3/Glue/Athena/Airflow Reconcile + Matrix Coverage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every AWS tab connector (S3, Glue, Athena, Airflow) real, live-docker Playwright coverage for both the AWS tab's direct operations (including tracked-job/reconcile creation+run) and the Compare tab's Matrix comparison — fixing the two backend gaps (`aws_glue` has no data-extraction branch at all; `aws_athena`'s Matrix branch requires a `query_runner` object that is never actually built from `config_id`) and adding the missing live Airflow container and LocalStack Glue/Athena seed data that make those live tests possible.

**Architecture:** LocalStack (`localstack/localstack:3`, already in `docker-compose.integration.yml`, running `s3,glue,athena`) backs live S3/Glue/Athena; a new `apache/airflow` standalone container backs live Airflow. `tests/e2e/global-setup.ts` gets a new `seedGlueAthena()` step (S3 bucket + Glue database/table pointing at it) alongside the existing `seedMinio()`. The Matrix-compare gap is fixed entirely inside `api/services/compare_service.py` (a new `_resolve_source_spec()` that turns a `config_id`-bearing `aws_athena`/`aws_glue` `DataSourceSpec` into the `query_runner`/`rows` shapes `etl_framework/reconciliation/data_sources.py` already knows how to consume) plus one small, symmetric addition to that file itself (an `aws_glue` dispatch branch, mirroring the existing `sap_bo` raw-data fallback) — `etl_framework` stays free of boto3/AWS-specific code; only `api/services/` talks to AWS. Frontend gets two additive changes: new Athena-mode fields (database/output-location/workgroup) and an entirely new Glue mode, both in the Matrix source-A/B cards.

**Tech Stack:** FastAPI + Pydantic (backend), Alpine.js (frontend), Playwright + TypeScript (e2e), pytest (unit), Docker Compose + LocalStack + Apache Airflow (live infra).

---

## Context for the engineer picking this up

Read these first if anything below is unclear — this plan assumes you have NOT read them yet:
- `etl_framework/reconciliation/data_sources.py` — the `extract_data_source()` dispatcher every compare mode (SQL/BO/Matrix) funnels through.
- `api/services/compare_service.py` — `CompareService.compare_matrix()` around line 959 is what this plan patches.
- `api/schemas.py` — `DataSourceSpec` (~line 1256) and `MatrixCompareRequest` (~line 1273).
- `api/services/aws_athena_service.py`, `api/services/aws_glue_service.py`, and their `*_runtime.py` siblings — the real, working AWS clients this plan reuses (never re-implemented).
- `tests/e2e/45-oracle-compare.spec.ts` and `tests/e2e/18-aws-s3-tab-live.spec.ts` — the reference patterns for every new live spec in this plan.
- `tests/e2e/global-setup.ts` — `seedMinio()`/`seedSqlServer()`/`seedOracle()` are the templates for the new `seedGlueAthena()`.
- `docker-compose.integration.yml` — note the `localstack` service is already present (`SERVICES=s3,glue,athena`) but **never seeded**, and there is **no Airflow service at all** yet.

**Known environment risk (verify at Task 9, not before):** LocalStack Community's Athena emulation may only support a subset of SQL against Glue-catalog-registered S3 tables. If `SELECT * FROM e2e_raw.orders` fails against the seeded table in Task 9's manual verification step, the fallback is documented inline in that task — don't silently swap approaches without hitting that checkpoint.

---

## SCOPE CHANGE (decided during Task 8, applies to everything below)

Task 8's real-environment verification found the risk above was worse than anticipated: `localstack/localstack:3` Community edition does not implement the Glue or Athena APIs **at all** — confirmed via `GET http://127.0.0.1:4566/_localstack/health`, whose `services` map doesn't list `glue`/`athena` even as `"disabled"`; both are gated behind a paid `LOCALSTACK_AUTH_TOKEN` (LocalStack Pro), which this environment does not have. `seedGlueAthena()`'s `create_database`/`create_table` calls and a direct `start_query_execution` call against Athena both fail identically with `"API for service '<x>' not yet implemented or pro feature"`.

Escalated to the user; decision was to **descope** rather than pause for a Pro token or switch to a real AWS account:
- Tasks 1-3 (backend Matrix fix), 4-5 (frontend Matrix Athena/Glue UI), 6-7 (live Airflow container + readiness wait) are unaffected — they don't depend on LocalStack's Glue/Athena support and are already done, verified, and merged as-is.
- Task 8 keeps `seedGlueAthena()` in the file (documents exactly what's missing and what a Pro token would unlock) but changed it to **warn and return instead of throwing** on failure, so it can never take down the rest of `global-setup.ts` (SQL Server/Oracle/MinIO/Airflow seeding must keep working regardless of this unrelated, unfixable gap).
- **Task 9 (live Glue tab spec) and Task 10 (live Athena tab spec) are DROPPED.** There is no live backend to test against. The existing mocked specs (`tests/e2e/19-aws-glue-tab.spec.ts`, `tests/e2e/20-aws-athena-tab.spec.ts`) remain the only Glue/Athena AWS-tab coverage — unchanged, still valid, not touched by this plan.
- **Task 11 (live Airflow tab spec) proceeds as originally planned** — Airflow has no LocalStack dependency; the container built in Tasks 6-7 is fully real and working.
- **Task 12 (live Matrix AWS-combinations spec) is DROPPED** in its original form (it depended entirely on live Athena/Glue query execution against LocalStack, which doesn't exist). The Matrix backend fix itself (Tasks 1-3) already has thorough unit coverage (real `AwsAthenaService`/`AwsGlueService`/`AwsS3Runtime` code paths exercised against fakes at the boto3-client boundary, not mocks of the fix's own logic), and the frontend UI (Tasks 4-5) already has non-live Playwright coverage via the existing `tests/e2e/42-live-docker-matrix-reconciliation.spec.ts` (which doesn't require `E2E_LIVE_BACKENDS` — it hits the app's real `/api/compare/matrix` endpoint against file/SQL sources, and exercises the new Athena/Glue UI fields' visibility/isolation). No further live spec is achievable or necessary for this gap.
- Task 13 (final full-suite verification) is scoped down accordingly: verify SQL Server/Oracle/MinIO/S3/Airflow live specs + Matrix UI specs (42, 26) pass; there is no live Glue/Athena run to include.

---

## Part A — Backend: fix the Matrix `aws_athena` / `aws_glue` gaps

### Task 1: `DataSourceSpec` — add Athena/Glue resolution fields

**Files:**
- Modify: `api/schemas.py:1256-1271` (`DataSourceSpec`)
- Test: `tests/unit/test_compare_matrix_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_compare_matrix_service.py`:

```python
def test_data_source_spec_accepts_athena_and_glue_resolution_fields() -> None:
    spec = DataSourceSpec(
        source_type="aws_athena",
        config_id=1,
        query_or_table="SELECT * FROM db.tbl",
        athena_database="db",
        athena_output_location="s3://bucket/athena-output/",
        athena_workgroup="primary",
    )
    assert spec.athena_database == "db"
    assert spec.athena_output_location == "s3://bucket/athena-output/"
    assert spec.athena_workgroup == "primary"

    glue_spec = DataSourceSpec(
        source_type="aws_glue",
        config_id=1,
        glue_database="raw",
        glue_table="orders",
    )
    assert glue_spec.glue_database == "raw"
    assert glue_spec.glue_table == "orders"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_compare_matrix_service.py::test_data_source_spec_accepts_athena_and_glue_resolution_fields -v`
Expected: FAIL with `ValidationError` / `AttributeError: athena_database` (field doesn't exist yet — extra fields are silently allowed by `ConfigDict(extra="allow")` but accessed as attributes only if declared, so the assertion fails).

- [ ] **Step 3: Add the fields**

In `api/schemas.py`, extend `DataSourceSpec` (around line 1256):

```python
class DataSourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_type: Literal["sql", "file", "aws_athena", "aws_glue", "sap_bo", "api"] | str
    config_id: int | None = None
    connection_name: str | None = None
    query_or_table: str | None = None
    file_path: str | None = None
    file_b64: str | None = None
    file_name: str | None = None
    endpoint_url: str | None = None
    http_method: str | None = "GET"
    headers: dict[str, str] | None = None
    bo_doc_id: str | None = None
    bo_report_id: str | None = None
    athena_database: str | None = None
    athena_output_location: str | None = None
    athena_workgroup: str | None = None
    glue_database: str | None = None
    glue_table: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_compare_matrix_service.py::test_data_source_spec_accepts_athena_and_glue_resolution_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_compare_matrix_service.py
git commit -m "feat(schemas): add athena/glue resolution fields to DataSourceSpec

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `etl_framework` — add the `aws_glue` extraction branch

**Files:**
- Modify: `etl_framework/reconciliation/data_sources.py:16-43` (dispatcher) and append a new function
- Test: `tests/unit/test_data_sources.py`

`aws_glue` currently has zero branch in the dispatcher — it falls through to `else: raise ValueError(f"Unsupported source_type: '{source_type}'")`. Glue itself is a catalog, not a query engine, so — exactly like the existing `sap_bo` branch — the real AWS resolution (describe the table via `AwsGlueService`, pull its S3-backed rows) must happen in `api/services/compare_service.py` (Task 3) *before* calling `extract_data_source`; this function only needs to accept the already-resolved rows, the same `df`/`data`/`rows` convention `_extract_athena_source`/`_extract_sap_bo_source` already use.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_data_sources.py` (after `test_extract_aws_athena_mock_runner`):

```python
def test_extract_aws_glue_with_resolved_rows():
    spec = {
        "source_type": "aws_glue",
        "rows": [{"id": 1, "amount": 10.0}, {"id": 2, "amount": 20.0}],
    }
    df = extract_data_source(spec)
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["id", "amount"]
    assert df.iloc[0]["id"] == 1


def test_extract_aws_glue_without_resolved_rows_raises():
    with pytest.raises(ValueError, match="AWS Glue data source requires"):
        extract_data_source({"source_type": "aws_glue", "config_id": 1})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_data_sources.py -k aws_glue -v`
Expected: FAIL — `test_extract_aws_glue_without_resolved_rows_raises` fails because the current error message is `"Unsupported source_type: 'aws_glue'"`, not `"AWS Glue data source requires"`; `test_extract_aws_glue_with_resolved_rows` fails with the same `ValueError`.

- [ ] **Step 3: Add the dispatch branch and function**

In `etl_framework/reconciliation/data_sources.py`, change the dispatcher (lines 32-43):

```python
    if source_type == "file":
        return _extract_file_source(spec)
    elif source_type == "sql":
        return _extract_sql_source(spec, db_session=db_session)
    elif source_type == "aws_athena":
        return _extract_athena_source(spec)
    elif source_type == "aws_glue":
        return _extract_glue_source(spec)
    elif source_type == "api":
        return _extract_api_source(spec)
    elif source_type == "sap_bo":
        return _extract_sap_bo_source(spec)
    else:
        raise ValueError(f"Unsupported source_type: '{source_type}'")
```

Then append a new function after `_extract_athena_source` (after line 151):

```python
def _extract_glue_source(spec: dict[str, Any]) -> pd.DataFrame:
    """AWS Glue is a catalog, not a query engine: real config_id -> S3-object
    resolution happens upstream in api/services/compare_service.py (which knows
    how to talk to AwsGlueService/S3), exactly like _extract_sap_bo_source's
    bo_client resolution happens upstream of this module. This function only
    ever sees the already-resolved rows.
    """
    if "df" in spec or "data" in spec or "rows" in spec:
        raw_data = spec.get("df") or spec.get("data") or spec.get("rows")
        return pd.DataFrame(raw_data)

    raise ValueError(
        "AWS Glue data source requires pre-resolved 'rows'/'data' "
        "(resolve config_id -> S3 object via CompareService before calling extract_data_source)"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_data_sources.py -v`
Expected: PASS (all tests in the file, including the two new ones and the existing `test_extract_invalid_source_type` which must still pass unchanged since `"unknown_type"` still isn't `aws_glue`)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/data_sources.py tests/unit/test_data_sources.py
git commit -m "feat(reconciliation): add aws_glue data source branch (resolved-rows convention)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `CompareService` — resolve `config_id` into real Athena/Glue data before extraction

**Files:**
- Modify: `api/services/compare_service.py` (imports near top, new method before `compare_matrix`, and `compare_matrix` itself at line 959)
- Test: `tests/unit/test_compare_matrix_service.py`

This is the actual product fix: today `compare_matrix()` passes the raw `DataSourceSpec.model_dump()` straight to `extract_data_source()`, so an `aws_athena` spec with only `config_id`/`query_or_table` (which is all the current Matrix UI collects, and all `_buildMatrixSourceSpec` in `frontend/features/compare.js:1076-1078` sends) hits `_extract_athena_source`'s final `raise ValueError("AWS Athena data source requires 'query_runner', 'query', or mock data in spec")` — there is no `query_runner` in the dict. This task builds one.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_compare_matrix_service.py` (needs new imports at the top — add `MagicMock` and the Fake clients):

```python
from unittest.mock import MagicMock
```

Append these tests:

```python
class _FakeAthenaClient:
    """Mirrors tests/unit/test_aws_athena_service.py's FakeAthenaClient, trimmed
    to a single-page, always-SUCCEEDED result so these tests stay focused on
    _resolve_source_spec's wiring rather than re-testing AwsAthenaService itself."""

    def __init__(self, rows: list[dict[str, str]]):
        self._rows = rows

    def start_query_execution(self, **kwargs):
        self.output_location = kwargs["ResultConfiguration"]["OutputLocation"]
        self.database = (kwargs.get("QueryExecutionContext") or {}).get("Database")
        self.workgroup = kwargs.get("WorkGroup")
        return {"QueryExecutionId": "qid-1"}

    def get_query_execution(self, QueryExecutionId: str):
        return {"QueryExecution": {"QueryExecutionId": QueryExecutionId, "Status": {"State": "SUCCEEDED"}, "Statistics": {}}}

    def get_query_results(self, QueryExecutionId: str, MaxResults: int = 100, NextToken: str | None = None):
        header = {"Data": [{"VarCharValue": k} for k in self._rows[0].keys()]}
        body = [{"Data": [{"VarCharValue": str(v)} for v in row.values()]} for row in self._rows]
        return {"ResultSet": {"Rows": [header, *body]}}


def test_resolve_source_spec_builds_real_athena_query_runner_from_config_id(tmp_path) -> None:
    from api.services.compare_service import CompareService

    db = _make_db()
    try:
        config_repo = ConfigRepository(db)
        cfg = config_repo.create("aws-athena-e2e", "dev", {"aws_region": "us-east-1"})
        svc = CompareService(db, config_repo)

        fake_client = _FakeAthenaClient([{"id": "1", "amount": "10.0"}])
        import api.services.aws_athena_service as athena_service_module
        original_client = athena_service_module.AwsAthenaService._client
        athena_service_module.AwsAthenaService._client = lambda self, config_id: fake_client
        try:
            spec = {
                "source_type": "aws_athena",
                "config_id": cfg.id,
                "query_or_table": "SELECT * FROM raw.orders",
                "athena_database": "raw",
                "athena_output_location": "s3://bucket/athena-output/",
                "athena_workgroup": "primary",
            }
            resolved = svc._resolve_source_spec(spec)
            assert "query_runner" in resolved
            df = extract_data_source(resolved)
            assert list(df.columns) == ["id", "amount"]
            assert df.iloc[0]["id"] == "1"
            assert fake_client.database == "raw"
            assert fake_client.output_location == "s3://bucket/athena-output/"
            assert fake_client.workgroup == "primary"
        finally:
            athena_service_module.AwsAthenaService._client = original_client
    finally:
        db.close()


def test_resolve_source_spec_athena_requires_output_location() -> None:
    from api.services.compare_service import CompareService

    db = _make_db()
    try:
        config_repo = ConfigRepository(db)
        cfg = config_repo.create("aws-athena-e2e-2", "dev", {"aws_region": "us-east-1"})
        svc = CompareService(db, config_repo)
        spec = {"source_type": "aws_athena", "config_id": cfg.id, "query_or_table": "SELECT 1"}
        with pytest.raises(HTTPException) as err:
            svc._resolve_source_spec(spec)
        assert err.value.status_code == 422
        assert "athena_output_location" in err.value.detail
    finally:
        db.close()


def test_resolve_source_spec_builds_rows_from_glue_table(tmp_path) -> None:
    from api.services.compare_service import CompareService

    csv_bytes = b"id,amount\n1,10.0\n2,20.0\n"

    db = _make_db()
    try:
        config_repo = ConfigRepository(db)
        cfg = config_repo.create("aws-glue-e2e", "dev", {"aws_region": "us-east-1"})
        svc = CompareService(db, config_repo)

        fake_glue_client = MagicMock()
        fake_glue_client.get_table.return_value = {
            "Table": {
                "Name": "orders",
                "StorageDescriptor": {
                    "Location": "s3://bucket/raw/orders/",
                    "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
                    "Columns": [{"Name": "id", "Type": "int"}, {"Name": "amount", "Type": "double"}],
                },
                "PartitionKeys": [],
            }
        }

        class _FakeS3Boto:
            def get_paginator(self, name):
                assert name == "list_objects_v2"
                class _P:
                    def paginate(self, Bucket, Prefix):
                        return [{"Contents": [{"Key": "raw/orders/part-0.csv"}]}]
                return _P()

            def get_object(self, Bucket, Key):
                assert Bucket == "bucket"
                assert Key == "raw/orders/part-0.csv"
                return {"Body": MagicMock(read=lambda: csv_bytes)}

        import api.services.aws_glue_service as glue_service_module
        import api.services.aws_s3_runtime as s3_runtime_module
        original_glue_client = glue_service_module.AwsGlueService._client
        original_s3_client = s3_runtime_module.AwsS3Runtime.client
        glue_service_module.AwsGlueService._client = lambda self, config_id: fake_glue_client
        s3_runtime_module.AwsS3Runtime.client = (
            lambda self, config_id, override=None: __import__(
                "etl_framework.aws_s3.client", fromlist=["S3Client"]
            ).S3Client.__new__(
                __import__("etl_framework.aws_s3.client", fromlist=["S3Client"]).S3Client
            )
        )
        try:
            # __new__ above sidesteps S3Client.__init__'s AWSSession requirement;
            # set the private attribute it actually reads from directly.
            def _client_with_fake_s3(self, config_id, override=None):
                from etl_framework.aws_s3.client import S3Client
                c = S3Client.__new__(S3Client)
                c._s3 = _FakeS3Boto()
                return c
            s3_runtime_module.AwsS3Runtime.client = _client_with_fake_s3

            spec = {
                "source_type": "aws_glue",
                "config_id": cfg.id,
                "glue_database": "raw",
                "glue_table": "orders",
            }
            resolved = svc._resolve_source_spec(spec)
            assert "rows" in resolved
            df = extract_data_source(resolved)
            assert list(df.columns) == ["id", "amount"]
            assert df.iloc[0]["id"] == 1
            assert df.iloc[1]["amount"] == 20.0
        finally:
            glue_service_module.AwsGlueService._client = original_glue_client
            s3_runtime_module.AwsS3Runtime.client = original_s3_client
    finally:
        db.close()
```

Also add the missing top-of-file import in `tests/unit/test_compare_matrix_service.py`:

```python
from fastapi import HTTPException
from etl_framework.reconciliation.data_sources import extract_data_source
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_matrix_service.py -k resolve_source_spec -v`
Expected: FAIL with `AttributeError: 'CompareService' object has no attribute '_resolve_source_spec'`

- [ ] **Step 3: Implement `_resolve_source_spec` and wire it into `compare_matrix`**

In `api/services/compare_service.py`, add near the top imports (after the existing `from etl_framework.repository.repository import ConfigRepository, RunRepository`):

```python
import io
```

Add a new private helper method to `CompareService`, placed just above `compare_matrix` (before line 959):

```python
    _GLUE_INPUT_FORMAT_TO_FMT = {
        "parquet": "parquet",
        "orc": "orc",
        "json": "json",
    }

    def _glue_object_format(self, input_format: str | None) -> str:
        name = (input_format or "").lower()
        for needle, fmt in self._GLUE_INPUT_FORMAT_TO_FMT.items():
            if needle in name:
                return fmt
        return "csv"

    @staticmethod
    def _parse_s3_uri(uri: str) -> tuple[str, str]:
        if not uri or not uri.startswith("s3://"):
            raise HTTPException(status_code=422, detail=f"Unsupported Glue table location: {uri!r}")
        rest = uri[len("s3://"):]
        bucket, _, prefix = rest.partition("/")
        return bucket, prefix

    def _read_glue_table_rows(self, config_id: int, database: str, table: str) -> list[dict[str, Any]]:
        from api.services.aws_glue_service import AwsGlueService
        from api.services.aws_s3_runtime import AwsS3Runtime

        described = AwsGlueService(self._config_repo).describe_table(config_id, database, table)
        bucket, prefix = self._parse_s3_uri(described.location or "")
        client = AwsS3Runtime(self._config_repo).client(config_id)
        data_key = next((obj["Key"] for obj in client.list_objects(bucket, prefix) if not obj["Key"].endswith("/")), None)
        if data_key is None:
            raise HTTPException(status_code=404, detail=f"No objects found under s3://{bucket}/{prefix}")
        raw = client.get_object(bucket, data_key)
        fmt = self._glue_object_format(described.input_format)
        buf = io.BytesIO(raw)
        if fmt == "parquet":
            df = pd.read_parquet(buf)
        elif fmt == "orc":
            import pyarrow.orc as orc
            df = orc.ORCFile(buf).read().to_pandas()
        elif fmt == "json":
            df = pd.read_json(buf, lines=True)
        else:
            df = pd.read_csv(buf)
        return df.to_dict(orient="records")

    def _resolve_source_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Turn a config_id-bearing aws_athena/aws_glue DataSourceSpec dict into
        the query_runner/rows shape etl_framework.reconciliation.data_sources
        already knows how to consume. A spec that already carries a query_runner
        (or df/data/rows) is left untouched -- this only fills the gap for the
        real, config_id-driven path the Matrix UI actually sends.
        """
        source_type = str(spec.get("source_type") or "").lower()
        config_id = spec.get("config_id")
        if config_id is None:
            return spec

        if source_type == "aws_athena" and not any(
            k in spec for k in ("query_runner", "athena_service", "athena_runtime", "runner", "df", "data", "rows")
        ):
            from api.services.aws_athena_service import AwsAthenaService

            output_location = spec.get("athena_output_location")
            if not output_location:
                raise HTTPException(
                    status_code=422,
                    detail="athena_output_location is required on an aws_athena Matrix source using config_id",
                )
            query = spec.get("query_or_table") or spec.get("query")
            if not query:
                raise HTTPException(
                    status_code=422,
                    detail="query_or_table is required on an aws_athena Matrix source using config_id",
                )
            service = AwsAthenaService(self._config_repo)
            database = spec.get("athena_database")
            workgroup = spec.get("athena_workgroup")

            class _Runner:
                def run_query(_self, q: str) -> list[dict[str, Any]]:
                    return service.run_query(config_id, database, q, output_location, workgroup).results.rows

            return {**spec, "query_runner": _Runner()}

        if source_type == "aws_glue" and not any(k in spec for k in ("df", "data", "rows")):
            database = spec.get("glue_database")
            table = spec.get("glue_table")
            if not database or not table:
                raise HTTPException(
                    status_code=422,
                    detail="glue_database and glue_table are required on an aws_glue Matrix source",
                )
            rows = self._read_glue_table_rows(config_id, database, table)
            return {**spec, "rows": rows}

        return spec
```

Then update `compare_matrix` (currently lines 963-964) to route specs through it:

```python
        spec_a = self._resolve_source_spec(spec_a)
        spec_b = self._resolve_source_spec(spec_b)
        df_a = extract_data_source(spec_a, self._db)
        df_b = extract_data_source(spec_b, self._db)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_matrix_service.py -v`
Expected: PASS (all tests in the file, including the pre-existing file/file Matrix tests, which must be unaffected since `_resolve_source_spec` no-ops when `config_id` is `None`)

Also run the full existing suites these files touch, to catch regressions:

Run: `python -m pytest tests/unit/test_data_sources.py tests/unit/test_aws_athena_service.py tests/unit/test_aws_glue_service.py tests/unit/test_compare_matrix_route.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/compare_service.py tests/unit/test_compare_matrix_service.py
git commit -m "fix(compare): resolve config_id into real Athena query_runner / Glue rows for Matrix compare

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Part B — Frontend: Matrix UI gets Athena's missing fields + a real Glue mode

### Task 4: Matrix Athena mode — add database / output-location / workgroup fields

**Files:**
- Modify: `frontend/partials/tab-compare.html:1639-1647` (Source A) and `:1703-1711` (Source B)
- Modify: `frontend/features/compare.js:129-132` (state) and `:1076-1078` (`_buildMatrixSourceSpec`)
- Test: `tests/e2e/42-live-docker-matrix-reconciliation.spec.ts` (`MODE_FIELDS` map + a new field-population test)

- [ ] **Step 1: Write the failing test**

In `tests/e2e/42-live-docker-matrix-reconciliation.spec.ts`, update `MODE_FIELDS` (line 17):

```typescript
  athena: ['athena-config', 'athena-query-textarea', 'athena-database-input', 'athena-output-location-input', 'athena-workgroup-input'],
```

Add a new test after `'configures AWS Athena vs API matrix reconciliation'` (after line 67):

```typescript
  test('Athena mode collects database, output location, and workgroup', async ({ authedPage }) => {
    await openMatrix(authedPage);
    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-athena"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-athena-database-input"]').fill('raw');
    await authedPage.locator('[data-testid="compare-matrix-source-a-athena-output-location-input"]').fill('s3://bucket/athena-output/');
    await authedPage.locator('[data-testid="compare-matrix-source-a-athena-workgroup-input"]').fill('primary');
    await expect(authedPage.locator('[data-testid="compare-matrix-source-a-athena-database-input"]')).toHaveValue('raw');
    await expect(authedPage.locator('[data-testid="compare-matrix-source-a-athena-output-location-input"]')).toHaveValue('s3://bucket/athena-output/');
    await expect(authedPage.locator('[data-testid="compare-matrix-source-a-athena-workgroup-input"]')).toHaveValue('primary');
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx playwright test tests/e2e/42-live-docker-matrix-reconciliation.spec.ts -g "Athena mode collects"`
Expected: FAIL — `compare-matrix-source-a-athena-database-input` does not exist yet.

- [ ] **Step 3: Add the frontend fields**

In `frontend/features/compare.js`, extend the two Matrix source state objects (lines 131-132):

```javascript
    matrixSourceA: { configId: '', connectionName: '', queryOrTable: '', filePath: '', fileB64: '', fileName: '', athenaQuery: '', athenaDatabase: '', athenaOutputLocation: '', athenaWorkgroup: '', glueDatabase: '', glueTable: '', docId: '', reportId: '', endpointUrl: '', httpMethod: 'GET', label: 'Source A' },
    matrixSourceB: { configId: '', connectionName: '', queryOrTable: '', filePath: '', fileB64: '', fileName: '', athenaQuery: '', athenaDatabase: '', athenaOutputLocation: '', athenaWorkgroup: '', glueDatabase: '', glueTable: '', docId: '', reportId: '', endpointUrl: '', httpMethod: 'GET', label: 'Source B' },
```

Update `_buildMatrixSourceSpec` (lines 1076-1078) to send the new fields, and add an `aws_glue` branch:

```javascript
      } else if (type === 'aws_athena') {
        if (src.configId) spec.config_id = parseInt(src.configId, 10);
        if (src.athenaQuery || src.queryOrTable) spec.query_or_table = src.athenaQuery || src.queryOrTable;
        if (src.athenaDatabase) spec.athena_database = src.athenaDatabase;
        if (src.athenaOutputLocation) spec.athena_output_location = src.athenaOutputLocation;
        if (src.athenaWorkgroup) spec.athena_workgroup = src.athenaWorkgroup;
      } else if (type === 'aws_glue') {
        if (src.configId) spec.config_id = parseInt(src.configId, 10);
        if (src.glueDatabase) spec.glue_database = src.glueDatabase;
        if (src.glueTable) spec.glue_table = src.glueTable;
      } else if (type === 'sap_bo') {
```

(This replaces the existing `} else if (type === 'aws_athena') { ... } else if (type === 'sap_bo') {` block — the `sap_bo` branch itself is unchanged, just re-indent/re-paste as shown so the new `aws_glue` branch sits between them.)

Also update the near-identity reverse-mapping helper mentioned in the comment at line 490-504 (the one that reconstructs `{type, src}` from a saved job's spec, used when re-editing a saved Matrix job) — read that block in full before editing (it starts at line 497 with `const base = {...}`) and add `glueDatabase`/`glueTable`/`athenaDatabase`/`athenaOutputLocation`/`athenaWorkgroup` to its `base` object and to whatever branch currently handles `cfg.source_type === 'aws_athena'`, mirroring the same field names used above. Add an `aws_glue` branch there too:

```javascript
      if (type === 'aws_glue') {
        return { type, src: { ...base, configId: cfg.config_id ?? '', glueDatabase: cfg.glue_database || '', glueTable: cfg.glue_table || '' } };
      }
```

Now in `frontend/partials/tab-compare.html`, extend the Athena `template x-if` block for Source A (lines 1639-1647):

```html
        <template x-if="matrixSourceAType === 'aws_athena'">
          <div class="space-y-2">
            <select data-testid="compare-matrix-source-a-athena-config" x-model="matrixSourceA.configId" class="field-input field-select mb-2" aria-label="athena config a">
              <option value="">Select Athena config</option>
              <template x-for="cfg in configs" :key="cfg.id"><option :value="cfg.id" x-text="cfg.name"></option></template>
            </select>
            <input data-testid="compare-matrix-source-a-athena-database-input" x-model="matrixSourceA.athenaDatabase" class="field-input mb-2" placeholder="database (optional)" aria-label="athena database a" />
            <textarea data-testid="compare-matrix-source-a-athena-query-textarea" x-model="matrixSourceA.athenaQuery" class="field-input mb-2" rows="3" placeholder="SELECT * FROM athena_db.table_a" aria-label="athena query a"></textarea>
            <input data-testid="compare-matrix-source-a-athena-output-location-input" x-model="matrixSourceA.athenaOutputLocation" class="field-input mb-2" placeholder="s3://bucket/athena-output/" aria-label="athena output location a" />
            <input data-testid="compare-matrix-source-a-athena-workgroup-input" x-model="matrixSourceA.athenaWorkgroup" class="field-input" placeholder="primary" aria-label="athena workgroup a" />
          </div>
        </template>
```

And the mirror for Source B (lines 1703-1711):

```html
        <template x-if="matrixSourceBType === 'aws_athena'">
          <div class="space-y-2">
            <select data-testid="compare-matrix-source-b-athena-config" x-model="matrixSourceB.configId" class="field-input field-select mb-2" aria-label="athena config b">
              <option value="">Select Athena config</option>
              <template x-for="cfg in configs" :key="cfg.id"><option :value="cfg.id" x-text="cfg.name"></option></template>
            </select>
            <input data-testid="compare-matrix-source-b-athena-database-input" x-model="matrixSourceB.athenaDatabase" class="field-input mb-2" placeholder="database (optional)" aria-label="athena database b" />
            <textarea data-testid="compare-matrix-source-b-athena-query-textarea" x-model="matrixSourceB.athenaQuery" class="field-input mb-2" rows="3" placeholder="SELECT * FROM athena_db.table_b" aria-label="athena query b"></textarea>
            <input data-testid="compare-matrix-source-b-athena-output-location-input" x-model="matrixSourceB.athenaOutputLocation" class="field-input mb-2" placeholder="s3://bucket/athena-output/" aria-label="athena output location b" />
            <input data-testid="compare-matrix-source-b-athena-workgroup-input" x-model="matrixSourceB.athenaWorkgroup" class="field-input" placeholder="primary" aria-label="athena workgroup b" />
          </div>
        </template>
```

Rebuild the bundled HTML (the partial is the source of truth; `frontend/index.html` is generated — see CLAUDE.md build step referenced in the Explore report):

Run: `node scripts/build-html.js`

- [ ] **Step 4: Run test to verify it passes**

Run: `npx playwright test tests/e2e/42-live-docker-matrix-reconciliation.spec.ts -g "Athena mode collects"`
Expected: PASS

Also re-run the full spec 42 to confirm the `MODE_FIELDS` isolation tests (`every Source A/B mode reveals only its own fields`) still pass with the three new fields added:

Run: `npx playwright test tests/e2e/42-live-docker-matrix-reconciliation.spec.ts`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/partials/tab-compare.html frontend/features/compare.js frontend/index.html tests/e2e/42-live-docker-matrix-reconciliation.spec.ts
git commit -m "feat(compare-ui): collect Athena database/output-location/workgroup in Matrix mode

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Matrix UI — add a Glue mode (Source A and B)

**Files:**
- Modify: `frontend/partials/tab-compare.html:1612-1618` (Source A pill row) and a new `template x-if` block; `:1676-1682` (Source B pill row) and its mirror
- Test: `tests/e2e/42-live-docker-matrix-reconciliation.spec.ts`

- [ ] **Step 1: Write the failing test**

In `tests/e2e/42-live-docker-matrix-reconciliation.spec.ts`, add a `glue` entry to `MODE_FIELDS`:

```typescript
const MODE_FIELDS: Record<string, string[]> = {
  sql: ['config-select', 'query-textarea'],
  file: ['path-input', 'upload-input'],
  athena: ['athena-config', 'athena-query-textarea', 'athena-database-input', 'athena-output-location-input', 'athena-workgroup-input'],
  glue: ['glue-config', 'glue-database-input', 'glue-table-input'],
  bo: ['bo-config', 'bo-doc', 'bo-report'],
  api: ['api-url-input'],
};
```

Add a standalone test:

```typescript
  test('Glue mode is selectable and collects database/table', async ({ authedPage }) => {
    await openMatrix(authedPage);
    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-glue"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-glue-database-input"]').fill('raw');
    await authedPage.locator('[data-testid="compare-matrix-source-a-glue-table-input"]').fill('orders');
    await expect(authedPage.locator('[data-testid="compare-matrix-source-a-glue-database-input"]')).toHaveValue('raw');
    await expect(authedPage.locator('[data-testid="compare-matrix-source-a-glue-table-input"]')).toHaveValue('orders');
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx playwright test tests/e2e/42-live-docker-matrix-reconciliation.spec.ts -g "Glue mode is selectable"`
Expected: FAIL — `compare-matrix-source-a-mode-glue` does not exist.

- [ ] **Step 3: Add the Glue mode**

In `frontend/features/compare.js`, `_buildMatrixSourceSpec`'s `aws_glue` branch was already added in Task 4 Step 3 — no further JS change needed here (state fields `glueDatabase`/`glueTable` were added in Task 4 too). This task is HTML-only.

In `frontend/partials/tab-compare.html`, add a Glue pill to the Source A mode row (line 1612-1618), right after the Athena pill:

```html
          <button data-testid="compare-matrix-source-a-mode-glue" @click="matrixSourceAType = 'aws_glue'" :class="matrixSourceAType === 'aws_glue' ? 'pill active' : 'pill'">Glue</button>
```

Add a new `template x-if` block right after the Athena block for Source A (after line 1647, before the `sap_bo` block):

```html
        <template x-if="matrixSourceAType === 'aws_glue'">
          <div class="space-y-2">
            <select data-testid="compare-matrix-source-a-glue-config" x-model="matrixSourceA.configId" class="field-input field-select mb-2" aria-label="glue config a">
              <option value="">Select Glue config</option>
              <template x-for="cfg in configs" :key="cfg.id"><option :value="cfg.id" x-text="cfg.name"></option></template>
            </select>
            <input data-testid="compare-matrix-source-a-glue-database-input" x-model="matrixSourceA.glueDatabase" class="field-input mb-2" placeholder="database" aria-label="glue database a" />
            <input data-testid="compare-matrix-source-a-glue-table-input" x-model="matrixSourceA.glueTable" class="field-input" placeholder="table" aria-label="glue table a" />
          </div>
        </template>
```

Mirror both changes for Source B: the pill (after line 1682's Athena pill):

```html
          <button data-testid="compare-matrix-source-b-mode-glue" @click="matrixSourceBType = 'aws_glue'" :class="matrixSourceBType === 'aws_glue' ? 'pill active' : 'pill'">Glue</button>
```

and the block (after the Source B Athena block, before its `sap_bo` block):

```html
        <template x-if="matrixSourceBType === 'aws_glue'">
          <div class="space-y-2">
            <select data-testid="compare-matrix-source-b-glue-config" x-model="matrixSourceB.configId" class="field-input field-select mb-2" aria-label="glue config b">
              <option value="">Select Glue config</option>
              <template x-for="cfg in configs" :key="cfg.id"><option :value="cfg.id" x-text="cfg.name"></option></template>
            </select>
            <input data-testid="compare-matrix-source-b-glue-database-input" x-model="matrixSourceB.glueDatabase" class="field-input mb-2" placeholder="database" aria-label="glue database b" />
            <input data-testid="compare-matrix-source-b-glue-table-input" x-model="matrixSourceB.glueTable" class="field-input" placeholder="table" aria-label="glue table b" />
          </div>
        </template>
```

Rebuild: `node scripts/build-html.js`

- [ ] **Step 4: Run test to verify it passes**

Run: `npx playwright test tests/e2e/42-live-docker-matrix-reconciliation.spec.ts`
Expected: PASS (all tests, including the two mode-isolation tests now iterating over 6 modes instead of 5)

- [ ] **Step 5: Commit**

```bash
git add frontend/partials/tab-compare.html frontend/index.html tests/e2e/42-live-docker-matrix-reconciliation.spec.ts
git commit -m "feat(compare-ui): add Glue mode to Matrix source A/B cards

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Part C — Live infra: Airflow container + LocalStack Glue/Athena seeding

### Task 6: Add a live Airflow container to `docker-compose.integration.yml`

**Files:**
- Modify: `docker-compose.integration.yml` (new `airflow` service)
- Create: `docker/airflow-seed/dags/etl_e2e_dag.py`

- [ ] **Step 1: Write the seeded DAG**

Create `docker/airflow-seed/dags/etl_e2e_dag.py`:

```python
from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="etl_orders_daily",
    description="E2E fixture DAG for live Airflow tests",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["e2e"],
) as dag:
    extract_orders = BashOperator(task_id="extract_orders", bash_command="echo extracting orders")
    transform_orders = BashOperator(task_id="transform_orders", bash_command="echo transforming orders")
    extract_orders >> transform_orders
```

(Task IDs `extract_orders`/`transform_orders` deliberately match the mocked spec `tests/e2e/21-aws-airflow-tab.spec.ts:47-48`, so the new live spec in Task 12 can assert on the same names.)

- [ ] **Step 2: Add the compose service**

Append to `docker-compose.integration.yml` (after the `localstack` service, before `gitlab`):

```yaml
  airflow:
    # Single-container "standalone-ish" Airflow: SequentialExecutor + its default
    # sqlite metadata DB (no Postgres/MySQL sidecar needed for a throwaway e2e
    # container). `airflow users create` gives us the deterministic admin/admin
    # credentials AwsAirflowRuntime._standalone_client's basic auth expects --
    # the built-in `airflow standalone` CLI command instead generates a random
    # password per boot, which would defeat a fixed e2e Config.
    image: apache/airflow:2.9.3-python3.11
    container_name: atom-airflow-integration
    environment:
      AIRFLOW__CORE__EXECUTOR: SequentialExecutor
      AIRFLOW__CORE__LOAD_EXAMPLES: "false"
    volumes:
      - ./docker/airflow-seed/dags:/opt/airflow/dags
    ports:
      - "18085:8080"
    command:
      - bash
      - -c
      - >-
        airflow db migrate &&
        (airflow users create --username admin --password admin --firstname E2E --lastname Admin --role Admin --email admin@example.com || true) &&
        (airflow scheduler &) &&
        exec airflow webserver --port 8080
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - >-
          import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=3).read()
      interval: 10s
      timeout: 5s
      retries: 40
      start_period: 40s
```

- [ ] **Step 3: Bring the stack up manually and verify**

Run: `docker compose -f docker-compose.integration.yml up -d --wait airflow`
Expected: command exits 0 once the healthcheck passes (may take 60-90s for `db migrate` + webserver gunicorn boot — if it times out, run `docker compose -f docker-compose.integration.yml logs airflow` and check whether `db migrate` or the user-create step is the blocker before changing anything).

Run: `python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:18085/api/v1/dags', timeout=5).status)"` — this will 401 (no auth) which is fine, it confirms the webserver answers; then:

Run: `python -c "import requests; r = requests.get('http://127.0.0.1:18085/api/v1/dags', auth=('admin','admin')); print(r.status_code, r.json()['dags'][0]['dag_id'])"`
Expected: `200 etl_orders_daily`

- [ ] **Step 4: Tear down**

Run: `docker compose -f docker-compose.integration.yml down -v`

- [ ] **Step 5: Commit**

```bash
git add docker-compose.integration.yml docker/airflow-seed/dags/etl_e2e_dag.py
git commit -m "feat(e2e): add live Airflow container with seeded etl_orders_daily DAG

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Update `global-setup.ts`/`global-teardown.ts` to boot Airflow and wait for it

**Files:**
- Modify: `tests/e2e/global-setup.ts`

- [ ] **Step 1: Add an Airflow readiness wait**

In `tests/e2e/global-setup.ts`, add a new function (after `seedMinio`, before `seedSqlServer`) and call it from `globalSetup`:

```typescript
function waitForAirflow() {
  // `docker compose up -d --wait` already blocks on the airflow service's own
  // Docker healthcheck (docker-compose.integration.yml), so this is a belt-and-
  // suspenders check that the seeded DAG is actually visible through the REST
  // API (not just that the process is listening) before any spec tries to use it.
  const script = `
import time
import requests

for attempt in range(30):
    try:
        resp = requests.get("http://127.0.0.1:18085/api/v1/dags", auth=("admin", "admin"), timeout=3)
        if resp.status_code == 200 and any(d["dag_id"] == "etl_orders_daily" for d in resp.json().get("dags", [])):
            break
    except Exception:
        pass
    time.sleep(2)
else:
    raise RuntimeError("Airflow did not report the seeded etl_orders_daily DAG within 60s")
print("airflow ready")
`;
  const result = spawnSync('python', ['-c', script], { encoding: 'utf-8' });
  if (result.status !== 0) {
    throw new Error(`Airflow readiness check failed:\n${result.stdout}\n${result.stderr}`);
  }
  console.log('[global-setup] Airflow ready:', result.stdout.trim());
}
```

Update `globalSetup`'s body to call it:

```typescript
    seedSqlServer();
    seedOracle();
    seedMinio();
    seedGlueAthena();
    waitForAirflow();
```

(`seedGlueAthena` is added in Task 8 — add this call now so Task 8's function has a caller; if Task 8 hasn't landed yet in your working tree, comment this one line out temporarily and this task's own manual verification will fail loudly, telling you to do Task 8 first.)

- [ ] **Step 2: Verify manually**

Run: `docker compose -f docker-compose.integration.yml up -d --wait` (full stack)
Run: `E2E_LIVE_BACKENDS=1 node -e "require('./tests/e2e/global-setup.ts')"` — actually global-setup.ts is TypeScript; instead trigger it through Playwright itself:

Run: `E2E_LIVE_BACKENDS=1 npx playwright test tests/e2e/00-auth-setup.spec.ts` (any single spec triggers `globalSetup`)
Expected: console shows `[global-setup] Airflow ready: airflow ready` among the other seed lines, and the spec itself passes.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/global-setup.ts
git commit -m "feat(e2e): wait for live Airflow's seeded DAG in global-setup

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Seed LocalStack Glue (+ S3 bucket) in `global-setup.ts`

**Files:**
- Modify: `tests/e2e/global-setup.ts`

- [ ] **Step 1: Add `seedGlueAthena()`**

Add this function (after `seedMinio`, before `waitForAirflow` — order doesn't matter functionally, but keep AWS-adjacent seeders grouped):

```typescript
function seedGlueAthena() {
  // LocalStack backs s3+glue+athena on one endpoint (docker-compose.integration.yml's
  // `localstack` service, SERVICES=s3,glue,athena). Real AWS would need a Glue
  // crawler or DDL to register a table; here we just PUT a CSV and register its
  // location directly via create_table, matching how a real data lake table
  // looks once already cataloged (which is all _read_glue_table_rows in
  // compare_service.py or a direct AWS-tab Glue call ever needs to see).
  const script = `
import time
import boto3

endpoint = "http://127.0.0.1:4566"
creds = dict(aws_access_key_id="test", aws_secret_access_key="test", region_name="us-east-1")
s3 = boto3.client("s3", endpoint_url=endpoint, **creds)
glue = boto3.client("glue", endpoint_url=endpoint, **creds)

for attempt in range(30):
    try:
        s3.list_buckets()
        break
    except Exception:
        time.sleep(1)
else:
    raise RuntimeError("LocalStack did not become ready within 30s")

bucket = "atom-e2e-glue"
existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
if bucket not in existing:
    s3.create_bucket(Bucket=bucket)

csv_body = b"id,sku,amount\\n1,A100,25.50\\n2,B200,50.00\\n3,C300,75.00\\n"
s3.put_object(Bucket=bucket, Key="raw/orders/part-0.csv", Body=csv_body)

database = "e2e_raw"
try:
    glue.get_database(Name=database)
except glue.exceptions.EntityNotFoundException:
    glue.create_database(DatabaseInput={"Name": database})

table_input = {
    "Name": "orders",
    "TableType": "EXTERNAL_TABLE",
    "StorageDescriptor": {
        "Columns": [
            {"Name": "id", "Type": "int"},
            {"Name": "sku", "Type": "string"},
            {"Name": "amount", "Type": "double"},
        ],
        "Location": f"s3://{bucket}/raw/orders/",
        "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
        "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
        "SerdeInfo": {
            "SerializationLibrary": "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe",
            "Parameters": {"field.delim": ",", "skip.header.line.count": "1"},
        },
    },
}
try:
    glue.delete_table(DatabaseName=database, Name="orders")
except glue.exceptions.EntityNotFoundException:
    pass
glue.create_table(DatabaseName=database, TableInput=table_input)

print("seeded")
`;
  const result = spawnSync('python', ['-c', script], { encoding: 'utf-8' });
  if (result.status !== 0) {
    throw new Error(`Glue/Athena seed failed:\n${result.stdout}\n${result.stderr}`);
  }
  console.log('[global-setup] Glue/Athena seeded:', result.stdout.trim());
}
```

- [ ] **Step 2: Verify manually, including the Athena-emulation risk flagged at the top of this plan**

Run: `docker compose -f docker-compose.integration.yml up -d --wait localstack`
Run:
```bash
python -c "
import boto3
kw = dict(endpoint_url='http://127.0.0.1:4566', aws_access_key_id='test', aws_secret_access_key='test', region_name='us-east-1')
boto3.client('s3', **kw).list_buckets()
"
```
(confirms LocalStack itself is reachable, then apply Step 1's script directly via `python -c "<script>"` to seed it.)

Then check Athena can actually query the seeded Glue table (**this is the environment-risk checkpoint flagged at the top of this plan**):

```bash
python -c "
import time, boto3
kw = dict(endpoint_url='http://127.0.0.1:4566', aws_access_key_id='test', aws_secret_access_key='test', region_name='us-east-1')
athena = boto3.client('athena', **kw)
s3 = boto3.client('s3', **kw)
if 'atom-e2e-glue' not in {b['Name'] for b in s3.list_buckets()['Buckets']}:
    s3.create_bucket(Bucket='atom-e2e-glue')
qid = athena.start_query_execution(
    QueryString='SELECT * FROM e2e_raw.orders',
    ResultConfiguration={'OutputLocation': 's3://atom-e2e-glue/athena-output/'},
)['QueryExecutionId']
for _ in range(20):
    state = athena.get_query_execution(QueryExecutionId=qid)['QueryExecution']['Status']['State']
    if state in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
        break
    time.sleep(1)
print(state)
if state == 'SUCCEEDED':
    print(athena.get_query_results(QueryExecutionId=qid))
else:
    print(athena.get_query_execution(QueryExecutionId=qid)['QueryExecution']['Status'])
"
```

Expected: `SUCCEEDED` and a 3-row result set matching the seeded CSV.

**If this fails** (LocalStack Community's Athena emulation doesn't support `SELECT` against a Glue-external table in the pinned `:3` image version): don't touch `_read_glue_table_rows`/`_resolve_source_spec` (those read Glue+S3 directly via boto3, not through Athena, and are unaffected). Instead, scope down Task 11/13's *Athena-specific* live assertions to whatever LocalStack's Athena actually supports in this environment (e.g., a `SELECT 1` sanity query instead of a real table read) and note the narrowed scope in that spec file's own comment, the same way `tests/e2e/42-live-docker-matrix-reconciliation.spec.ts` already documents its SAP BO/API "KNOWN GAP"s inline rather than silently working around them.

- [ ] **Step 3: Wire the call into `globalSetup`** (already done in Task 7 Step 1's edit to the function body — if you did Task 7 first, nothing further to do here; if you're doing Task 8 before Task 7, add the `seedGlueAthena();` call to `globalSetup` now.)

- [ ] **Step 4: Tear down**

Run: `docker compose -f docker-compose.integration.yml down -v`

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/global-setup.ts
git commit -m "feat(e2e): seed LocalStack Glue database/table for live AWS tests

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Part D — Live Playwright specs

Each spec below follows `tests/e2e/18-aws-s3-tab-live.spec.ts`'s shape: `test.skip(!liveBackends, ...)`, a `beforeAll` that creates a real `Config` via `createConfig`, an `afterAll` that cleans it (and any created jobs) up via `deleteConfig`/`deleteJob`, and assertions against the real backend rather than route mocks.

### Task 9 [DROPPED — see "SCOPE CHANGE" above]: `tests/e2e/46-aws-glue-tab-live.spec.ts` — live Glue catalog operations

**Files:**
- Create: `tests/e2e/46-aws-glue-tab-live.spec.ts`

- [ ] **Step 1: Write the spec**

```typescript
// tests/e2e/46-aws-glue-tab-live.spec.ts
import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const LOCALSTACK_ENDPOINT = 'http://127.0.0.1:4566';

test.describe('46 AWS Glue tab - live LocalStack', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml localstack service, seeded by global-setup.ts seedGlueAthena())');

  let configId: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-aws-glue-cfg-${Date.now()}`, 'dev', {
        db_host: 'localhost',
        db_password: 'unused',
        aws_region: 'us-east-1',
        aws_access_key_id: 'test',
        aws_secret_access_key: 'test',
        aws_endpoint_url: LOCALSTACK_ENDPOINT,
        aws_verify_ssl: false,
      });
      configId = cfg.id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    if (!configId) return;
    const ctx = await authedContext(adminToken);
    try {
      await deleteConfig(ctx, configId);
    } finally {
      await ctx.dispose();
    }
  });

  async function openGlueTab(page: Page) {
    await page.goto('/');
    await page.getByRole('button', { name: 'AWS' }).click();
    await page.locator('[data-testid="aws-service-glue"]').click();
    await page.locator('[data-testid="aws-config-select"]').selectOption(String(configId));
  }

  test('lists real databases and tables from the seeded catalog', async ({ authedPage }) => {
    await openGlueTab(authedPage);
    await authedPage.locator('[data-testid="aws-glue-list-databases-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-glue-databases-result"]')).toContainText('e2e_raw', { timeout: 20_000 });

    await authedPage.locator('[data-testid="aws-glue-database-input"]').fill('e2e_raw');
    await authedPage.locator('[data-testid="aws-glue-list-tables-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-glue-tables-result"]')).toContainText('orders', { timeout: 20_000 });
  });

  test('describes the seeded orders table with real columns', async ({ authedPage }) => {
    await openGlueTab(authedPage);
    await authedPage.locator('[data-testid="aws-glue-database-input"]').fill('e2e_raw');
    await authedPage.locator('[data-testid="aws-glue-table-input"]').fill('orders');
    await authedPage.locator('[data-testid="aws-glue-describe-table-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-glue-table-result"]')).toContainText('sku', { timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="aws-glue-table-result"]')).toContainText('amount');
  });

  test('compares the seeded table against itself and reports a match', async ({ authedPage }) => {
    await openGlueTab(authedPage);
    await authedPage.locator('[data-testid="aws-glue-source-database-input"]').fill('e2e_raw');
    await authedPage.locator('[data-testid="aws-glue-source-table-input"]').fill('orders');
    await authedPage.locator('[data-testid="aws-glue-target-database-input"]').fill('e2e_raw');
    await authedPage.locator('[data-testid="aws-glue-target-table-input"]').fill('orders');
    await authedPage.locator('[data-testid="aws-glue-compare-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-glue-compare-result"]')).toContainText('match', { timeout: 20_000 });
  });
});
```

**Before writing this spec's assertions further, read `frontend/partials/tab-aws.html`'s Glue panel (`x-show="awsService === 'glue'"`, around line 4638)** to confirm the exact `data-testid`s for list-databases/list-tables/describe-table/compare buttons and results — the ones above (`aws-glue-list-databases-btn`, `aws-glue-databases-result`, etc.) are named by the same convention as the Athena/S3 panels already in that file (`aws-run-metadata-btn` -> `aws-result`, `aws-athena-run-query-btn` -> `aws-athena-result`) but must be verified against the actual file before trusting them; adjust the locators to match whatever is actually there.

- [ ] **Step 2: Run against live docker**

Run: `docker compose -f docker-compose.integration.yml up -d --wait`
Run: `$env:E2E_LIVE_BACKENDS = "1"; npx playwright test tests/e2e/46-aws-glue-tab-live.spec.ts` (PowerShell; use `E2E_LIVE_BACKENDS=1 npx playwright test tests/e2e/46-aws-glue-tab-live.spec.ts` on a POSIX shell)
Expected: all 3 tests PASS. If a `data-testid` doesn't match, fix the locator (not the frontend) unless the frontend genuinely lacks the operation entirely, in which case treat that as a new, separate finding to raise rather than silently inventing a testid that doesn't exist.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/46-aws-glue-tab-live.spec.ts
git commit -m "test(e2e): add live Glue tab spec against seeded LocalStack catalog

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10 [DROPPED — see "SCOPE CHANGE" above]: `tests/e2e/47-aws-athena-tab-live.spec.ts` — live Athena query execution

**Files:**
- Create: `tests/e2e/47-aws-athena-tab-live.spec.ts`

- [ ] **Step 1: Write the spec**

```typescript
// tests/e2e/47-aws-athena-tab-live.spec.ts
import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const LOCALSTACK_ENDPOINT = 'http://127.0.0.1:4566';
const OUTPUT_LOCATION = 's3://atom-e2e-glue/athena-output/';

test.describe('47 AWS Athena tab - live LocalStack', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (localstack + seedGlueAthena() from global-setup.ts)');

  let configId: number;
  const createdJobs: string[] = [];

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-aws-athena-cfg-${Date.now()}`, 'dev', {
        db_host: 'localhost',
        db_password: 'unused',
        aws_region: 'us-east-1',
        aws_access_key_id: 'test',
        aws_secret_access_key: 'test',
        aws_endpoint_url: LOCALSTACK_ENDPOINT,
        aws_verify_ssl: false,
      });
      configId = cfg.id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    if (!configId && createdJobs.length === 0) return;
    const ctx = await authedContext(adminToken);
    try {
      for (const name of createdJobs) await deleteJob(ctx, name);
      if (configId) await deleteConfig(ctx, configId);
    } finally {
      await ctx.dispose();
    }
  });

  async function openAthenaTab(page: Page) {
    await page.goto('/');
    await page.getByRole('button', { name: 'AWS' }).click();
    await page.locator('[data-testid="aws-service-athena"]').click();
    await page.locator('[data-testid="aws-config-select"]').selectOption(String(configId));
    await page.locator('[data-testid="aws-athena-database-input"]').fill('e2e_raw');
    await page.locator('[data-testid="aws-athena-query-input"]').fill('SELECT * FROM orders');
    await page.locator('[data-testid="aws-athena-output-location-input"]').fill(OUTPUT_LOCATION);
  }

  test('runs a real query against the seeded orders table', async ({ authedPage }) => {
    await openAthenaTab(authedPage);
    await authedPage.locator('[data-testid="aws-athena-run-query-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-athena-result"]')).toContainText('SUCCEEDED', { timeout: 30_000 });
    await expect(authedPage.locator('[data-testid="aws-athena-result"]')).toContainText('sku');
  });

  test('creates a tracked Athena query job and runs it through the backend', async ({ authedPage, adminToken }) => {
    await openAthenaTab(authedPage);
    const jobName = `e2e-athena-query-${Date.now()}`;
    createdJobs.push(jobName);
    await authedPage.locator('[data-testid="aws-athena-job-name-input"]').fill(jobName);
    await authedPage.locator('[data-testid="aws-athena-min-rows-input"]').fill('1');
    await authedPage.locator('[data-testid="aws-athena-create-job-btn"]').click();
    await expect(authedPage.getByText('Athena job created').last()).toBeVisible({ timeout: 20_000 });

    const ctx = await authedContext(adminToken);
    try {
      const run = await triggerRun(ctx, [jobName], configId);
      const status = await waitForTerminal(ctx, run.run_id, 60_000);
      expect(status.status).toBe('PASSED');
    } finally {
      await ctx.dispose();
    }
  });
});
```

**Before finalizing**, read `frontend/partials/tab-aws.html`'s Athena panel (lines 300-390, already excerpted during planning) to confirm the exact success-message text for job creation (`awsCreateAthenaQueryJob()` — grep `frontend/features/aws.js` or equivalent for the toast text it fires, e.g. `'Athena job created'` used above is a guess following the S3 tab's `'S3 job created'` pattern from `18-aws-s3-tab-live.spec.ts:119` — verify against the real toast string before trusting it).

- [ ] **Step 2: Run against live docker**

Run: `E2E_LIVE_BACKENDS=1 npx playwright test tests/e2e/47-aws-athena-tab-live.spec.ts`
Expected: both tests PASS, subject to the Task 8 Step 2 environment-risk checkpoint (if LocalStack's Athena can't execute this query, narrow the assertions per that task's fallback note and document it inline here the same way).

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/47-aws-athena-tab-live.spec.ts
git commit -m "test(e2e): add live Athena tab spec against seeded LocalStack table

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: `tests/e2e/48-aws-airflow-tab-live.spec.ts` — live Airflow DAG run

**Files:**
- Create: `tests/e2e/48-aws-airflow-tab-live.spec.ts`

- [ ] **Step 1: Write the spec**

```typescript
// tests/e2e/48-aws-airflow-tab-live.spec.ts
import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const AIRFLOW_URL = 'http://127.0.0.1:18085';

test.describe('48 AWS Airflow tab - live standalone Airflow', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml airflow service, seeded DAG etl_orders_daily)');

  let configId: number;
  const createdJobs: string[] = [];

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-aws-airflow-cfg-${Date.now()}`, 'dev', {
        db_host: 'localhost',
        db_password: 'unused',
        airflow_url: AIRFLOW_URL,
        airflow_username: 'admin',
        airflow_password: 'admin',
      });
      configId = cfg.id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    if (!configId && createdJobs.length === 0) return;
    const ctx = await authedContext(adminToken);
    try {
      for (const name of createdJobs) await deleteJob(ctx, name);
      if (configId) await deleteConfig(ctx, configId);
    } finally {
      await ctx.dispose();
    }
  });

  async function openAirflowTab(page: Page) {
    await page.goto('/');
    await page.getByRole('button', { name: 'AWS' }).click();
    await page.locator('[data-testid="aws-service-airflow"]').click();
    await page.locator('[data-testid="aws-config-select"]').selectOption(String(configId));
  }

  test('lists the real seeded DAG and runs it to completion', async ({ authedPage }) => {
    await openAirflowTab(authedPage);
    await authedPage.locator('[data-testid="aws-airflow-load-dags-btn"]').click();
    await authedPage.locator('[data-testid="aws-airflow-dag-select"]').selectOption('etl_orders_daily');
    await authedPage.locator('[data-testid="aws-airflow-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-airflow-result"]')).toContainText('success', { timeout: 60_000 });
    await expect(authedPage.locator('[data-testid="aws-airflow-result"]')).toContainText('extract_orders');
    await expect(authedPage.locator('[data-testid="aws-airflow-result"]')).toContainText('transform_orders');
  });

  test('creates a tracked Airflow DAG-run job and runs it through the backend', async ({ authedPage, adminToken }) => {
    await openAirflowTab(authedPage);
    await authedPage.locator('[data-testid="aws-airflow-dag-input"]').fill('etl_orders_daily');
    await authedPage.locator('[data-testid="aws-airflow-expected-status-select"]').selectOption('success');

    const jobName = `e2e-airflow-orders-${Date.now()}`;
    createdJobs.push(jobName);
    await authedPage.locator('[data-testid="aws-airflow-job-name-input"]').fill(jobName);
    await authedPage.locator('[data-testid="aws-airflow-create-job-btn"]').click();

    const ctx = await authedContext(adminToken);
    try {
      const run = await triggerRun(ctx, [jobName], configId);
      const status = await waitForTerminal(ctx, run.run_id, 60_000);
      expect(status.status).toBe('PASSED');
    } finally {
      await ctx.dispose();
    }
  });
});
```

- [ ] **Step 2: Run against live docker**

Run: `E2E_LIVE_BACKENDS=1 npx playwright test tests/e2e/48-aws-airflow-tab-live.spec.ts`
Expected: both tests PASS. If DAG triggering hangs, check `docker compose -f docker-compose.integration.yml logs airflow` for the scheduler picking up the trigger — the `SequentialExecutor` processes one task at a time, which is fine for this 2-task DAG but means don't run this spec's tests concurrently with anything else hitting the same Airflow container (Playwright's `workers: 1` config already guarantees this repo-wide).

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/48-aws-airflow-tab-live.spec.ts
git commit -m "test(e2e): add live Airflow tab spec against seeded standalone container

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 12 [DROPPED — see "SCOPE CHANGE" above]: `tests/e2e/49-live-docker-matrix-aws-combinations.spec.ts` — Matrix combos across all 4 services

**Files:**
- Create: `tests/e2e/49-live-docker-matrix-aws-combinations.spec.ts`

This is the payoff task: it exercises the Part A backend fix + Part B frontend fields together, covering the combinations the user asked for — S3 file data vs a live Athena query, a live Glue table vs a live Athena query, and a live Glue table vs a local file — plus a negative case preserving the "KNOWN GAP" documentation style already used in `42-live-docker-matrix-reconciliation.spec.ts` for anything still not fully wired (Airflow has no natural row-level "data source" shape, so it is intentionally excluded from Matrix — it's a DAG orchestrator, not a queryable/tabular source; note this explicitly in the spec rather than silently omitting it).

- [ ] **Step 1: Write the spec**

```typescript
// tests/e2e/49-live-docker-matrix-aws-combinations.spec.ts
import { test, expect } from './fixtures';
import path from 'node:path';
import { authedContext, createConfig, deleteConfig } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const LOCALSTACK_ENDPOINT = 'http://127.0.0.1:4566';
const ATHENA_OUTPUT_LOCATION = 's3://atom-e2e-glue/athena-output/';
const dataFile = (name: string) => path.join(__dirname, 'fixtures', 'data', name);

test.describe('49 Live Docker Matrix - AWS combinations', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (localstack + seedGlueAthena() from global-setup.ts)');

  let awsConfigId: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-matrix-aws-cfg-${Date.now()}`, 'dev', {
        db_host: 'localhost',
        db_password: 'unused',
        aws_region: 'us-east-1',
        aws_access_key_id: 'test',
        aws_secret_access_key: 'test',
        aws_endpoint_url: LOCALSTACK_ENDPOINT,
        aws_verify_ssl: false,
      });
      awsConfigId = cfg.id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    if (!awsConfigId) return;
    const ctx = await authedContext(adminToken);
    try {
      await deleteConfig(ctx, awsConfigId);
    } finally {
      await ctx.dispose();
    }
  });

  async function openMatrix(page: import('@playwright/test').Page) {
    await page.goto('/#compare');
    await page.locator('[data-testid="compare-subtab-matrix"]').click();
  }

  test('Athena (seeded orders) vs local file: real reconciliation with a deterministic mismatch', async ({ authedPage }) => {
    // Seeded Athena table (id,sku,amount): 1/A100/25.50, 2/B200/50.00, 3/C300/75.00.
    // fixtures/data/target.csv (id,val,num, per api-helpers.ts's createFileJob
    // comment) has a different shape -- so this deliberately compares against
    // fixtures/data/source.csv's own id column instead, matched 1:1 by 'id'
    // to keep the assertion about a REAL cross-engine key-match, not a schema
    // coincidence. See fixtures/data/source.csv for the exact 4 rows (ids 1-4with id=2's amount
    // differing between the two fixture files used elsewhere) -- here we only
    // assert row counts land somewhere sane and the run reaches a terminal
        // state with a real Athena payload, since source.csv's schema doesn't
    // line up with orders' columns for a value-level diff.
    await openMatrix(authedPage);
    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-athena"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-athena-config"]').selectOption(String(awsConfigId));
    await authedPage.locator('[data-testid="compare-matrix-source-a-athena-database-input"]').fill('e2e_raw');
    await authedPage.locator('[data-testid="compare-matrix-source-a-athena-query-textarea"]').fill('SELECT * FROM orders');
    await authedPage.locator('[data-testid="compare-matrix-source-a-athena-output-location-input"]').fill(ATHENA_OUTPUT_LOCATION);

    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-path-input"]').fill(dataFile('source.csv'));

    await authedPage.locator('[data-testid="matrix-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="btn-run-matrix-compare"]').click();

    await expect(authedPage.locator('[data-testid="matrix-compare-status-badge"]')).toBeVisible({ timeout: 30_000 });
    await expect(authedPage.locator('[data-testid="matrix-compare-results"]')).toBeVisible();
  });

  test('Glue (seeded orders table) vs Athena (same table): self-consistent, reports PASSED', async ({ authedPage }) => {
    await openMatrix(authedPage);
    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-glue"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-glue-config"]').selectOption(String(awsConfigId));
    await authedPage.locator('[data-testid="compare-matrix-source-a-glue-database-input"]').fill('e2e_raw');
    await authedPage.locator('[data-testid="compare-matrix-source-a-glue-table-input"]').fill('orders');

    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-athena"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-athena-config"]').selectOption(String(awsConfigId));
    await authedPage.locator('[data-testid="compare-matrix-source-b-athena-database-input"]').fill('e2e_raw');
    await authedPage.locator('[data-testid="compare-matrix-source-b-athena-query-textarea"]').fill('SELECT * FROM orders');
    await authedPage.locator('[data-testid="compare-matrix-source-b-athena-output-location-input"]').fill(ATHENA_OUTPUT_LOCATION);

    await authedPage.locator('[data-testid="matrix-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="btn-run-matrix-compare"]').click();

    await expect(authedPage.locator('[data-testid="matrix-compare-status-badge"]')).toHaveText('PASSED', { timeout: 30_000 });
    await expect(authedPage.locator('[data-testid="matrix-mismatched-count"]')).toHaveText('0');
  });

  test('Glue (seeded orders table) vs itself: reports PASSED', async ({ authedPage }) => {
    await openMatrix(authedPage);
    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-glue"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-glue-config"]').selectOption(String(awsConfigId));
    await authedPage.locator('[data-testid="compare-matrix-source-a-glue-database-input"]').fill('e2e_raw');
    await authedPage.locator('[data-testid="compare-matrix-source-a-glue-table-input"]').fill('orders');

    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-glue"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-glue-config"]').selectOption(String(awsConfigId));
    await authedPage.locator('[data-testid="compare-matrix-source-b-glue-database-input"]').fill('e2e_raw');
    await authedPage.locator('[data-testid="compare-matrix-source-b-glue-table-input"]').fill('orders');

    await authedPage.locator('[data-testid="matrix-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="btn-run-matrix-compare"]').click();

    await expect(authedPage.locator('[data-testid="matrix-compare-status-badge"]')).toHaveText('PASSED', { timeout: 30_000 });
    await expect(authedPage.locator('[data-testid="matrix-mismatched-count"]')).toHaveText('0');
  });

  test('negative: Athena mode without output_location surfaces a 422 as an ERROR run', async ({ authedPage }) => {
    // Exercises _resolve_source_spec's explicit validation (api/services/compare_service.py)
    // rather than a silent hang or an opaque 500.
    await openMatrix(authedPage);
    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-athena"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-athena-config"]').selectOption(String(awsConfigId));
    await authedPage.locator('[data-testid="compare-matrix-source-a-athena-query-textarea"]').fill('SELECT * FROM orders');
    // deliberately leave output-location blank

    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-path-input"]').fill(dataFile('source.csv'));
    await authedPage.locator('[data-testid="btn-run-matrix-compare"]').click();

    await expect(authedPage.locator('[data-testid="matrix-compare-status-badge"]')).toHaveText('ERROR', { timeout: 15_000 });
  });

  // NOTE: Airflow is intentionally NOT a Matrix source mode. Matrix compares
  // row-level tabular data between two sources; Airflow is a DAG orchestrator
  // with no queryable tabular shape of its own (a DAG run's "data" is whatever
  // its tasks produce, arbitrary and DAG-specific) -- the reconcile+job-run
  // coverage for Airflow lives entirely in tests/e2e/48-aws-airflow-tab-live.spec.ts.
});
```

- [ ] **Step 2: Run against live docker**

Run: `E2E_LIVE_BACKENDS=1 npx playwright test tests/e2e/49-live-docker-matrix-aws-combinations.spec.ts`
Expected: all 4 tests PASS, subject to the same Athena-emulation risk checkpoint from Task 8 Step 2 (if Athena queries against the Glue table don't work in this LocalStack version, narrow/skip the Athena-involving tests here with the same inline "KNOWN GAP" documentation style and keep the two pure-Glue tests, which don't depend on Athena at all).

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/49-live-docker-matrix-aws-combinations.spec.ts
git commit -m "test(e2e): add live Matrix comparison spec covering Athena/Glue/file combinations

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Part E — Full-suite verification

### Task 13: Run the complete live suite and reconcile any drift

**Files:** none (verification only)

- [ ] **Step 1: Full teardown/clean start**

Run: `docker compose -f docker-compose.integration.yml down -v`

- [ ] **Step 2: Run the entire live e2e suite**

Run: `$env:E2E_LIVE_BACKENDS = "1"; $env:LIVE_SQLSERVER_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"; npx playwright test` (PowerShell, this host's installed driver — omit/adjust the ODBC var on a machine with Driver 17) — this triggers `global-setup.ts` (SQL Server/Oracle/MinIO seeding, the now-warn-only Glue/Athena seed attempt, Airflow readiness wait) once, then runs every spec serially (`workers: 1`), including the pre-existing Oracle/SQL Server/S3/SFTP specs alongside the ones this plan actually added (Task 11's live Airflow spec; Tasks 9/10/12 were dropped per the SCOPE CHANGE above — do not expect or add specs numbered 46/47/49).

Expected: full PASS, with a `[global-setup] Glue/Athena seed skipped (expected -- ...)` warning line present but not causing any failure. Investigate any real failure by category:
- A `data-testid` mismatch in the Airflow spec -> re-check the actual `frontend/partials/tab-aws.html` markup for that panel and fix the spec's locator.
- A timeout on Airflow -> check `docker compose -f docker-compose.integration.yml logs airflow` for scheduler/webserver startup time; raise the relevant test's timeout if the container is simply slow on this machine, don't change the product code to compensate for CI/local Docker speed.

- [ ] **Step 3: Full teardown**

Run: `docker compose -f docker-compose.integration.yml down -v`

- [ ] **Step 4: Final commit (if Step 2 required any fixes)**

```bash
git add -A
git commit -m "fix(e2e): reconcile live AWS suite after full-run verification

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

(Skip this commit entirely if Step 2 passed clean with no changes.)

---

## Self-review notes (for whoever executes this plan)

- **Spec coverage:** S3 already had live AWS-tab + job coverage (`18-...spec.ts`, untouched by this plan). Airflow (Task 11) gets the same live shape, backed by the real container from Tasks 6-7. Matrix gets both Athena and Glue wired end-to-end at the backend (Tasks 1-3, real `AwsAthenaService`/`AwsGlueService`/`AwsS3Runtime` code paths, unit-tested against fakes at the boto3-client boundary) and frontend (Tasks 4-5, real UI fields + non-live Playwright coverage via the pre-existing `42-live-docker-matrix-reconciliation.spec.ts`). Live Glue/Athena AWS-tab specs and a live Matrix-AWS-combinations spec (originally Tasks 9/10/12) were dropped mid-plan — see "SCOPE CHANGE" above — because LocalStack Community cannot back them at all (not a partial-support gap; the Glue/Athena APIs are entirely absent from the community build), and the user chose to descope rather than pay for LocalStack Pro or wire up real AWS credentials. This is a real, deliberate reduction in what "all relevant combinations" ended up meaning, not an oversight.
- **Placeholders:** none — every executed task has runnable code (backend/frontend/infra) verified against a real, live container. The dropped tasks are marked `[DROPPED — see "SCOPE CHANGE" above]` in their own headers rather than silently deleted, so their original intent and the reason they didn't ship stays visible.
- **Type/name consistency:** `athena_database`/`athena_output_location`/`athena_workgroup`/`glue_database`/`glue_table` are used identically across `api/schemas.py` (Task 1), `compare_service.py`'s `_resolve_source_spec` (Task 3), and `frontend/features/compare.js`'s `_buildMatrixSourceSpec` (Task 4-5) — cross-checked field-by-field while writing this plan, and confirmed again during Task 4/5's code review.
