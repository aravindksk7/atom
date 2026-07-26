# AWS S3 Job Types — Design

**Date:** 2026-07-26
**Status:** Approved (design), pending implementation plan
**Author:** Principal data engineer (via brainstorming)
**Depends on:** `docs/superpowers/specs/2026-07-25-aws-web-ui-wiring-design.md`

## Context

Phase 1 wired the merged `aws_s3` backend into FastAPI and the frontend as read-only ad-hoc S3 checks. Those checks are useful interactively, but they do not yet run as tracked jobs. This spec turns the S3 checks into first-class job types so they flow through the existing run executor, `TestRun` history, scheduling, DQ checks, contracts, and reporting paths.

This is the first Phase 2 AWS expansion. Glue, Athena, and Airflow remain separate follow-on specs. The S3 job implementation should establish the pattern those later AWS services can reuse: saved config resolution, explicit job validation, executor builders, structured metrics, typed mismatch details, and small UI affordances that create tracked jobs from the AWS tab.

## Goals

- Add tracked S3 job types: `s3_row_count`, `s3_format_validation`, and `s3_partition_check`.
- Reuse Phase 1 saved AWS config resolution through `EnvironmentConfig` and `aws_config_from_env`.
- Map each S3 check into the existing `ReconciliationResult`/run-state model without adding a parallel persistence path.
- Upgrade schema assertion from name-set comparison to type-aware comparison for S3 format validation.
- Add frontend affordances to create tracked S3 jobs from the existing AWS S3 panel.
- Keep Glue, Athena, and Airflow as placeholders only in this spec.

## Non-Goals

- Implementing Glue catalog compare, Spark jobs, Athena query execution, Airflow DAG checks, or Airflow run tracking.
- Changing the ad-hoc `/api/aws/s3/*` contract except where it benefits from shared type-aware schema mismatch output.
- Mutating S3 objects or writing data back to AWS.
- Adding new storage tables for AWS job results.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Execution path | Extend `api/services/run_executor.py` with S3 builders/executors | Keeps History, scheduler, DQ, contracts, reports, and existing run lifecycle unchanged. |
| Credential source | Resolve a saved `config`/`config_id` through Phase 1 AWS config mapping | Avoids a second AWS credential model and preserves encryption/masking behavior. |
| Result shape | Map S3 outcomes into `ReconciliationResult` metrics and mismatches | Existing downstream consumers already understand this model. |
| Schema matching | Compare normalized column names and normalized types | Name-only checks miss incompatible schema drift; normalized types reduce backend/parser noise. |
| UI scope | Add create-job actions only for S3 | Keeps Phase 2 bounded while preserving the AWS sub-nav for later services. |

## Job Types

### `s3_row_count`

Params:

- `config` or `config_id`: saved environment config reference.
- `bucket`: S3 bucket.
- `key`: object key.
- `fmt`: one of the existing S3 formats (`csv`, `json`, `parquet`, `orc`).
- `min_rows`: optional inclusive lower bound.
- `max_rows`: optional inclusive upper bound.

Execution:

- Resolve AWS credentials from the saved config.
- Call the existing S3 row-count path used by `AwsS3Service`.
- Pass when the row count is available and within any supplied bounds.
- Fail when the count violates a bound.
- Error when S3 access, parsing, or unsupported format errors occur.

Metrics:

- `row_count`: integer row count.
- `engine`: row-count engine reported by the backend (`s3_select` or footer-based engine).
- `bucket`, `key`, `fmt`: copied into metrics/context for auditability.

Mismatch details:

- `row_count_below_min` with `actual` and `min_rows`.
- `row_count_above_max` with `actual` and `max_rows`.

### `s3_format_validation`

Params:

- `config` or `config_id`.
- `bucket`.
- `key`.
- `fmt`.
- `expected_schema`: optional mapping of column name to expected type.

Execution:

- Resolve AWS credentials from the saved config.
- Parse/validate the object with the existing `aws_s3` format validation path.
- If no expected schema is supplied, pass when the file parses.
- If expected schema is supplied, pass only when parsed schema matches by name and normalized type.
- Fail on schema drift; error on unreadable/unparseable files.

Metrics:

- `parsed`: boolean.
- `schema_ok`: boolean.
- `column_count`: discovered column count when available.
- `bucket`, `key`, `fmt`.

Mismatch details:

- `missing_columns`: columns expected but not found.
- `extra_columns`: columns found but not expected.
- `type_mismatches`: columns present on both sides whose normalized types differ, each with `column`, `expected_type`, and `actual_type`.

### `s3_partition_check`

Params:

- `config` or `config_id`.
- `bucket`.
- `prefix`.
- `expected_columns`: optional ordered list of Hive partition columns.
- `min_partitions`: optional inclusive lower bound.

Execution:

- Resolve AWS credentials from the saved config.
- Discover Hive-style partitions under the prefix with the existing `aws_s3` partition discovery path.
- Pass when discovered columns match expectations and partition count meets the minimum.
- Fail on partition-shape or count drift.
- Error on S3 access failures.

Metrics:

- `partition_count`: number of discovered partition entries.
- `partition_columns`: discovered columns.
- `object_count`: total object count across discovered partitions when available.
- `bucket`, `prefix`.

Mismatch details:

- `partition_columns_mismatch` with expected and actual ordered column lists.
- `partition_count_below_min` with `actual` and `min_partitions`.

## Backend Architecture

### Validation

Extend `etl_framework/runner/job_validation.py` so the new job types are validated before run creation/execution:

- Required S3 identity fields are present and non-empty.
- `fmt` is valid for row-count and format-validation jobs.
- Numeric thresholds are integers and non-negative when provided.
- `min_rows <= max_rows` when both are provided.
- `expected_schema` is a mapping of string column names to string expected types when provided.
- `expected_columns` is a non-empty list of strings when provided.

Validation should return `ValidationIssue` entries like existing checks, not raise directly except through `raise_for_job_validation_errors`.

### Executor

Extend `api/services/run_executor.py` with S3-specific builder and executor helpers near the existing specialized job handlers:

- `_build_case_s3_row_count(job_def)` delegates to `_execute_s3_row_count(job_def)`.
- `_build_case_s3_format_validation(job_def)` delegates to `_execute_s3_format_validation(job_def)`.
- `_build_case_s3_partition_check(job_def)` delegates to `_execute_s3_partition_check(job_def)`.

The existing `_build_case(job_def)` dispatch should route the new job types to these builders. Each executor should:

- Resolve params through the same helper style used by existing job types.
- Load the saved config through the repository/session already available to `RunExecutor`.
- Build `AWSConfig` via `aws_config_from_env` and construct the S3 backend client/filesystem as needed.
- Return/pass a `ReconciliationResult` through the existing `TestRunner` state flow.
- Store useful S3 metrics in the same metrics/results fields used by current jobs.

Implementation should avoid duplicating Phase 1 service logic where possible. If direct reuse of `AwsS3Service` would create awkward FastAPI or HTTP exception coupling, extract a small shared internal helper for S3 client/filesystem construction and keep both Phase 1 routes and Phase 2 executor on that helper.

## Type-Aware Schema Assertion

The current schema validation behavior detects missing and extra columns by name. Phase 2 adds type drift detection.

Expected schema input remains a mapping:

```json
{
  "customer_id": "int64",
  "order_total": "decimal(12,2)",
  "created_at": "timestamp"
}
```

Actual schema comes from the parsed S3 object. Before comparison, both sides are normalized:

- Column names compare exactly after string conversion; this spec does not add case folding.
- Types are lowercased and whitespace-normalized.
- Common aliases collapse where safe: `integer` -> `int64`, `long` -> `int64`, `double` -> `float64`, `string`/`str` -> `string`, `bool`/`boolean` -> `bool`.
- Parameterized types such as `decimal(12,2)` are preserved after whitespace normalization.

The comparison result has three independent categories:

- `missing_in_target`: expected column names absent from actual schema.
- `extra_in_target`: actual column names absent from expected schema.
- `type_mismatches`: same-name columns with different normalized types.

The existing `SchemaValidationError` should carry type mismatches in addition to missing/extra fields. Route error mapping can continue returning HTTP 400 for ad-hoc validation, now including `type_mismatches` in the detail body. Executor jobs convert the same mismatch information into a failed `ReconciliationResult` instead of an HTTP error.

## Frontend

The AWS tab remains a single top-level tab with S3 active and Glue/Athena/Airflow placeholders. This spec only enables S3 job creation affordances:

- Add row-count job creation fields for optional `min_rows` and `max_rows`.
- Add format-validation job creation using the existing expected-schema JSON field.
- Add partition-check job creation fields for optional expected columns and minimum partitions.
- Add buttons such as `Create Row Count Job`, `Create Format Validation Job`, and `Create Partition Check Job`.
- Persist jobs through the existing job creation API/repository path used by other job definitions.
- Render inline validation errors if job creation fails.

The ad-hoc action buttons remain unchanged: users can still run immediate S3 checks without creating tracked jobs.

## Error Handling

- Missing saved config resolves to the same not-found behavior used by Phase 1.
- AWS/S3 backend exceptions become job errors in executor mode and HTTP 400 in ad-hoc route mode.
- Schema drift becomes a job failure, not an executor error, because the file was readable and the check produced a comparison result.
- Invalid job params are rejected during job validation before execution.
- UI job creation surfaces validation failures inline and should not silently create incomplete jobs.

## Testing

### Unit Tests

- `tests/unit/test_job_validation.py`: required-param checks, invalid formats, threshold validation, expected schema validation, expected partition columns validation.
- `tests/unit/test_run_executor_s3.py`: executor pass/fail/error paths for all three S3 job types with injected/mocked S3 behavior.
- Existing or new `aws_s3` schema tests: missing columns, extra columns, type mismatches, normalized alias matches, parameterized type preservation.
- `tests/unit/test_aws_s3_routes.py`: ad-hoc format-validation error detail includes `type_mismatches`.

### Integration/Smoke Tests

- Existing AWS UI smoke test extends to assert S3 create-job controls render.
- Frontend build step regenerates `frontend/index.html` from the template and partials.
- Node syntax check covers the updated AWS feature slice.

## Rollout

1. Add type-aware schema comparison and tests in the backend `aws_s3` validation layer.
2. Add S3 job validation rules.
3. Add executor helpers and tests for `s3_row_count`, `s3_format_validation`, and `s3_partition_check`.
4. Add frontend job-creation controls in the AWS tab.
5. Rebuild frontend and run smoke tests.

This order keeps the schema assertion primitive stable before executor and UI code depend on it.

## Future Specs

After this spec ships, define separate specs for:

- AWS Glue: catalog compare and Spark job execution model.
- AWS Athena: query runner, result capture, and DQ metrics.
- Airflow: DAG integrity, operator mocks, and run tracking.
- AWS tab enablement for Glue/Athena/Airflow once each backend exists.
