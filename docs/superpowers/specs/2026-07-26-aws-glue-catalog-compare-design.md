# AWS Glue Catalog Compare — Design

**Date:** 2026-07-26
**Status:** Approved (design), pending implementation plan
**Author:** Principal data engineer (via brainstorming)
**Depends on:** `docs/superpowers/specs/2026-07-26-aws-s3-job-types-design.md`

## Context

The AWS S3 phase added saved-config AWS runtime resolution, S3 ad-hoc checks, tracked S3 job types, AWS-tab job creation controls, and live MinIO Playwright coverage. The next AWS service should build on that pattern without creating a separate result or credential model.

AWS Glue is the natural next phase because Glue catalog metadata describes S3-backed datasets. Catalog comparison gives users a higher-level schema and partition drift check before moving to Athena query execution or Airflow orchestration.

There is no existing Glue implementation in the repo. The AWS tab currently has disabled Glue/Athena/Airflow placeholders, and there are no Docker live Glue services. This phase therefore uses mocked Glue clients for backend and UI tests, not live Docker e2e.

## Goals

- Add AWS Glue catalog inspection support using saved AWS config resolution.
- Add ad-hoc Glue API routes for listing databases, listing tables, describing a table, and comparing two catalog tables.
- Add a tracked `aws_glue_catalog_compare` job type that maps catalog drift into `ReconciliationResult`.
- Enable the Glue AWS sub-tab with form controls for source/target database and table selection, ad-hoc compare, and tracked-job creation.
- Preserve the AWS tab navigation model and keep Athena/Airflow disabled placeholders.
- Cover the feature with unit, route, executor, smoke, and mocked Playwright tests.

## Non-Goals

- Running Glue Spark jobs.
- Creating, updating, or deleting Glue catalog resources.
- Adding Glue Local, LocalStack, or another Docker service.
- Implementing Athena query execution or Airflow DAG/run tracking.
- Adding new result-storage tables.
- Implementing cross-account assume-role flows beyond the existing saved AWS config fields.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Credential source | Reuse saved AWS config via `AwsS3Runtime`-style AWS config resolution | Keeps AWS credential behavior consistent across S3 and Glue. |
| Glue client layer | Add a small Glue runtime/service wrapper instead of direct boto3 calls in routes/executor | Makes route, executor, and tests share one contract. |
| Result shape | Use `ReconciliationResult` for tracked jobs | Preserves History, scheduler, DQ checks, contracts, reports, and run lifecycle. |
| Comparison scope | Table metadata, columns, partition keys, location, input/output formats | Covers catalog drift without executing data queries. |
| UI scope | Enable Glue sub-tab only; Athena/Airflow remain placeholders | Keeps this phase bounded. |
| E2E scope | Mocked Playwright route coverage | No live Glue service exists in Docker Compose. |

## Backend Architecture

### Glue Runtime

Create `api/services/aws_glue_runtime.py` with a small runtime helper similar to the S3 runtime:

- Resolves saved config by `config_id` or config name.
- Builds an AWS config using `aws_config_from_env`.
- Creates a boto3 Glue client through the existing AWS session/config model where possible.
- Allows tests to inject a fake Glue client without touching AWS.

The helper should avoid FastAPI route coupling where practical. Missing config should map to the same 404 behavior in route mode and job error behavior in executor mode.

### Glue Service

Create `api/services/aws_glue_service.py` with methods:

- `list_databases(config_id: int) -> GlueDatabasesResponse`
- `list_tables(config_id: int, database: str) -> GlueTablesResponse`
- `describe_table(config_id: int, database: str, table: str) -> GlueTableResponse`
- `compare_tables(config_id: int, source_database: str, source_table: str, target_database: str, target_table: str) -> GlueCatalogCompareResponse`

The service should normalize Glue table metadata into focused internal dictionaries before comparison:

- `database`
- `table`
- `columns`: ordered list of `{name, type, comment?}`
- `partition_keys`: ordered list of `{name, type, comment?}`
- `location`
- `input_format`
- `output_format`
- `table_type`

Comparison categories:

- `missing_columns`: columns in source absent from target.
- `extra_columns`: columns in target absent from source.
- `type_mismatches`: same-name columns whose normalized Glue types differ.
- `partition_key_mismatches`: partition key name/type/order drift.
- `location_mismatch`: source and target S3 locations differ when both exist.
- `format_mismatch`: input/output format or table type differs.

Glue type normalization should share the S3 schema normalization helper where safe. It should also preserve Glue complex types such as `array<string>`, `map<string,string>`, and `struct<...>` after whitespace normalization rather than flattening them.

## API Design

Add `api/routes/aws_glue.py` and include it from `api/main.py` under `/api/aws/glue`.

Request/response schemas should live in `api/schemas.py` or a nearby existing schema module following current repo conventions.

Routes:

- `POST /api/aws/glue/databases`
  - Request: `{ "config_id": 1 }`
  - Response: `{ "databases": ["raw", "curated"] }`
- `POST /api/aws/glue/tables`
  - Request: `{ "config_id": 1, "database": "raw" }`
  - Response: `{ "database": "raw", "tables": ["orders", "customers"] }`
- `POST /api/aws/glue/table`
  - Request: `{ "config_id": 1, "database": "raw", "table": "orders" }`
  - Response: normalized table metadata.
- `POST /api/aws/glue/compare-tables`
  - Request: `{ "config_id": 1, "source_database": "raw", "source_table": "orders", "target_database": "curated", "target_table": "orders" }`
  - Response: `{ "match": true|false, "source": ..., "target": ..., "diff": ... }`

Error handling:

- Missing config returns HTTP 404.
- AWS/Glue client errors return HTTP 400 with a structured message.
- Missing database/table returns HTTP 400 unless the underlying client exposes a clearer not-found exception.
- Schema/catalog drift is not an HTTP error for compare routes; it returns `match: false` with diff details.

## Tracked Job Type

Add `aws_glue_catalog_compare`.

Params:

- `config` or `config_id`: saved AWS config reference.
- `source_database`: Glue source database.
- `source_table`: Glue source table.
- `target_database`: Glue target database.
- `target_table`: Glue target table.
- `compare_location`: optional boolean, default `true`.
- `compare_formats`: optional boolean, default `true`.
- `compare_partitions`: optional boolean, default `true`.

Validation:

- Config reference is required.
- Source and target database/table names are required and non-empty.
- Optional compare flags must be booleans when provided.

Execution:

- Resolve the saved AWS config.
- Use `AwsGlueService.compare_tables()`.
- Return `PASSED` when `match` is true.
- Return `FAILED` when comparison completes with drift.
- Return `ERROR` when config resolution, AWS access, or Glue API access fails.

Metrics:

- `source_database`, `source_table`, `target_database`, `target_table`.
- `source_column_count`, `target_column_count`.
- `source_partition_key_count`, `target_partition_key_count`.
- `match`: boolean.

Mismatch records:

- `missing_columns`
- `extra_columns`
- `type_mismatch`
- `partition_key_mismatch`
- `location_mismatch`
- `format_mismatch`

The executor should add a `catalog_diff` section to `mismatch_summary` mirroring the route diff body for report/debug readability.

## Frontend Design

Enable the Glue sub-tab in `frontend/partials/tab-aws.html` and extend `frontend/features/aws.js`.

Glue state:

- `awsGlueSourceDatabase`
- `awsGlueSourceTable`
- `awsGlueTargetDatabase`
- `awsGlueTargetTable`
- `awsGlueResult`
- `awsGlueError`
- `awsGlueJobName`
- compare flags: `awsGlueCompareLocation`, `awsGlueCompareFormats`, `awsGlueComparePartitions`

Controls:

- Config selector reuses the existing AWS config list.
- Source database/table inputs.
- Target database/table inputs.
- Compare-location/formats/partitions toggles.
- `Compare Glue Catalog Tables` button.
- `Create Glue Catalog Compare Job` button.

UI behavior:

- Glue compare calls `/api/aws/glue/compare-tables` and renders match status plus diff categories.
- Job creation posts to `/api/jobs` with `job_type: "aws_glue_catalog_compare"` and the same params.
- Glue errors render in the Glue panel without interfering with S3 result/error state.
- Athena/Airflow tabs remain disabled placeholders.

## Testing

### Unit Tests

- Glue type normalization and table comparison:
  - matching columns pass.
  - missing/extra columns are separated.
  - type mismatches include `column`, `expected_type`, and `actual_type`.
  - partition key order/type mismatches are detected.
  - location and format mismatches obey compare flags.
- Glue service fake-client tests for databases, tables, table describe, and compare.
- Job validation tests for `aws_glue_catalog_compare`.
- RunExecutor tests for pass/fail/error outcomes and `catalog_diff` summary.

### Route Tests

- `/api/aws/glue/databases` returns database names.
- `/api/aws/glue/tables` returns table names.
- `/api/aws/glue/table` returns normalized metadata.
- `/api/aws/glue/compare-tables` returns `match: false` and diff details for drift.
- Missing config and fake AWS errors map to expected HTTP statuses.

### Frontend/Smoke Tests

- AWS smoke test asserts Glue tab is enabled and Athena/Airflow remain disabled.
- Smoke test asserts Glue compare/job controls render in `frontend/index.html`.
- Node syntax check covers `frontend/features/aws.js`.
- Build step regenerates `frontend/index.html`.

### Playwright

Add mocked Playwright coverage for the Glue AWS tab:

- Intercept `/api/aws/glue/compare-tables` and return a drift response.
- Assert the Glue panel renders diff categories.
- Intercept `/api/jobs` and assert `job_type: "aws_glue_catalog_compare"` plus params.
- Do not require Docker or live Glue.

## Rollout

1. Add Glue comparison primitives and service tests.
2. Add Glue routes and route tests.
3. Add job validation and executor support.
4. Enable Glue UI panel and frontend smoke tests.
5. Add mocked Playwright coverage.
6. Run focused backend, frontend, and Playwright verification.

## Future Specs

After Glue ships, define separate specs for:

- AWS Athena: query runner, result capture, and DQ metrics.
- Airflow: DAG integrity, operator mocks, and run tracking.
- Glue Spark job execution, if needed after catalog compare proves useful.
