# AWS Web UI Wiring — Design

**Date:** 2026-07-25
**Status:** Approved (design), pending implementation plan
**Author:** Principal data engineer (via brainstorming)
**Depends on:** `aws_s3` backend (merged — `2026-07-25-aws-s3-schema-validation-design.md`)

## Context

The `aws_s3` backend module (S3 metadata, row counts, Hive partition
discovery, format validation) is merged to `master` but has no web-UI or API
surface. This spec wires it into the existing FastAPI + vanilla-JS frontend,
following the established **adapter** pattern (SAP BO / Automic / REST API),
and lays down an **extensible "AWS" tab shell** so the later Glue / Athena /
Airflow subsystems (Specs 2–4) slot in without re-architecting the UI.

The work is delivered in **two sequential plans**:

- **Phase 1 (ships alone):** config foundation + `AwsS3Service` + ad-hoc
  `/api/aws/s3/*` routes + the extensible **AWS** frontend tab with an S3
  panel. Read-only, self-contained.
- **Phase 2 (follow-on plan):** S3 **job types** (`s3_row_count`,
  `s3_format_validation`, `s3_partition_check`) wired into the run executor so
  checks run as tracked `TestRun`s with History, scheduling, DQ, and contracts.

Each phase produces working, testable software on its own.

## Goals

- Supply AWS credentials from a saved config (like every other adapter).
- Let a user run S3 metadata / row-count / partition / format-validation
  checks ad hoc from the UI and via `/api/aws/s3/*`.
- Provide an extensible AWS tab shell (service sub-nav) ready for Glue/Athena/
  Airflow.
- (Phase 2) Run S3 checks as first-class tracked jobs.

## Non-Goals

- Glue / Athena / Airflow functionality or their UI panels (placeholders only;
  wired when their backends land).
- Mutating S3 (read/validate only).
- Changing the `aws_s3` backend behavior.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Credential source | Fields on saved `EnvironmentConfig`, resolved by `config_id` | Matches SAP BO/Automic/REST adapters; encryption-at-rest already covers the new secret fields. |
| UI surface | Both ad-hoc tab (Phase 1) **and** job types (Phase 2) | User needs interactive checks now and tracked/scheduled checks later. |
| Tab structure | One extensible "AWS" tab with a service sub-nav | Glue/Athena/Airflow become sub-panels, not new top-level tabs. |
| Delivery | Two sequential plans | Phase 1 ships value immediately; Phase 2 is heavier engine surgery. |

## Config Foundation (both phases)

Add to `etl_framework/config/models.py` `EnvironmentConfig`:

- `aws_region: str = ""`
- `aws_access_key_id: str = ""`
- `aws_secret_access_key: str = ""`
- `aws_session_token: str = ""`
- `aws_endpoint_url: str = ""`
- `aws_verify_ssl: bool = True`

`aws_secret_access_key` and `aws_session_token` are added to `SECRET_FIELDS`
(the bare `secret_access_key`/`session_token` added in Spec 1 stay for the
standalone `AWSConfig`; add the prefixed names too so the saved-config path is
masked/encrypted).

Add a resolver `etl_framework/aws/config.py::aws_config_from_env(env: EnvironmentConfig) -> AWSConfig`
that maps the `aws_*` fields onto the existing `AWSConfig` model. This keeps
`AWSConfig` the single credential type used by `AWSSession`.

## Phase 1 — Ad-hoc Surface

### Backend

**`api/services/aws_s3_service.py` — `AwsS3Service`** (mirrors `AdapterService`):
- `__init__(self, configs: ConfigRepository)`.
- Private `_client(config_id) -> S3Client`: load the saved config, resolve
  `EnvironmentConfig`, build `AWSConfig` via `aws_config_from_env`, construct
  `AWSSession` → `S3Client`.
- Private `_fs(config_id) -> pyarrow.fs.S3FileSystem`: build an S3FileSystem
  from the same config (region/keys/endpoint) for footer row counts.
- Methods returning the Phase-1 result schemas:
  - `metadata(config_id, bucket, key) -> ObjectMetadataOut`
  - `row_count(config_id, bucket, key, fmt) -> RowCountOut`
  - `partitions(config_id, bucket, prefix) -> PartitionSchemeOut`
  - `validate_format(config_id, bucket, key, fmt, expected_schema=None) -> FormatValidationOut`
- Framework `AWSError`/`SchemaValidationError` are caught at the route layer
  and mapped to HTTP 400 with a structured detail (see Error Handling).

**`api/routes/aws_s3.py`** (`router` tagged `aws-s3`, mounted at
`prefix="/api/aws/s3"` in `api/main.py`):
- `POST /metadata` — body `{config_id, bucket, key}`
- `POST /row-count` — body `{config_id, bucket, key, fmt}`
- `POST /partitions` — body `{config_id, bucket, prefix}`
- `POST /validate-format` — body `{config_id, bucket, key, fmt, expected_schema?}`
- `get_aws_s3_service(db) -> AwsS3Service` dependency (like `get_adapter_service`).
- Each write-ish action audit-logged via `AuditService` (`aws_s3.check`), as
  adapters do.

**`api/schemas.py`** — request/response models:
- Requests: `S3MetadataRequest`, `S3RowCountRequest`, `S3PartitionsRequest`,
  `S3ValidateFormatRequest` (all carry `config_id`).
- Responses: `ObjectMetadataOut`, `RowCountOut`, `PartitionEntryOut`,
  `PartitionSchemeOut`, `FormatValidationOut`. These are thin API-facing mirrors
  of the `etl_framework/aws_s3/models.py` result models (kept separate so the
  API contract is explicit and maskable), following how `BODocOut` etc. mirror
  adapter data.

### Frontend

**`frontend/features/aws.js`** — new "AWS" feature module (mirrors
`features/adapters.js`):
- A service sub-nav: **S3** (active), plus disabled **Glue / Athena / Airflow**
  placeholders (enabled in later specs).
- **S3 panel:** config picker (reuse existing config-select component), inputs
  for bucket / key / prefix / format, and four actions (Metadata, Row Count,
  Partitions, Validate Format) that call the `/api/aws/s3/*` endpoints and
  render results into a results pane (metadata table, row-count with engine
  badge, partition table, validation pass/fail with missing/extra columns).
- Errors from the API render inline (not toast-only), showing the mapped
  message.

**`frontend/index.template.html`** — add an "AWS" tab entry + panel container;
the generated `index.html` follows the repo's existing build step for the
template. **`frontend/app.js`** — register the AWS tab in the tab router
alongside the existing features.

### Error Handling (Phase 1)

- Route layer wraps service calls: `AWSError` subclasses → HTTP 400 with
  `{detail: {error_type, message, bucket?, key?}}`.
- `SchemaValidationError` (format validation drift) → HTTP 400 with
  `{detail: {error_type: "schema_validation", missing_in_target, extra_in_target}}`
  so the frontend can render missing/extra columns distinctly.
- Missing/invalid `config_id` → 404 (reuse the repository's existing not-found
  behavior).

### Testing (Phase 1)

- `tests/unit/test_aws_s3_routes.py` — TestClient with a bearer token and
  `AwsS3Service` replaced via `dependency_overrides` (exact pattern from
  `tests/unit/test_adapters_routes.py`): assert each route shapes the request,
  returns the schema, and maps `AWSError`/`SchemaValidationError` to 400.
- `tests/unit/test_aws_s3_service.py` — `AwsS3Service` against moto-backed S3
  (config loaded from an in-memory repo), covering the happy path and the
  credential-resolution mapping.
- `tests/unit/test_aws_config_from_env.py` — `aws_config_from_env` maps every
  field correctly.
- Config-schema round-trip: extend an existing config-model test to cover the
  new `aws_*` fields and secret masking.

## Phase 2 — Job-Type Integration (follow-on plan)

New job types, validated in `etl_framework/runner/job_validation.py` and
executed in `api/services/run_executor.py` via `_build_case_s3_*` /
`_execute_s3_*` (following `_build_case_freshness` / `_build_case_schema_snapshot`):

- **`s3_row_count`** — params `{config, bucket, key, fmt, min_rows?, max_rows?}`;
  passes when the count is within bounds. Metrics: `row_count`, `engine`.
- **`s3_format_validation`** — params `{config, bucket, key, fmt, expected_schema?}`;
  passes when the file parses and (if given) the schema matches; drift becomes
  typed mismatches.
- **`s3_partition_check`** — params `{config, bucket, prefix, expected_columns?, min_partitions?}`;
  passes when the discovered scheme matches expectations.

Each `_execute_s3_*` builds the S3 client from the job's resolved config and
maps the `aws_s3` result into a `ReconciliationResult` (status + metrics +
mismatch details), so History, trend/drift, scheduling, DQ pass-conditions, and
contracts all work unchanged.

Frontend: a **"Create job from S3"** action in the AWS tab S3 panel (mirrors
`POST /api/adapters/jobs/from-bo-report`) that persists a job via
`JobRepository` and audit-logs it.

### Testing (Phase 2)

- `tests/unit/test_run_executor_s3.py` — one test per job type with an injected
  S3 client / row counter, asserting pass, fail (out-of-bounds / drift), and
  metric capture (pattern from existing executor tests).
- `job_validation` tests for each new type's required-param checks.

## Dependencies

- No new runtime deps (`boto3`, `pyarrow` already present; `moto` dev-only).

## Open Questions

None. Phase 2 is intentionally a separate plan; if Phase 1 review surfaces
engine constraints, Phase 2's plan absorbs them.
