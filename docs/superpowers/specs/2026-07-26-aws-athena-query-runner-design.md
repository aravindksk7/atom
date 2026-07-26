# AWS Athena Query Runner — Design

**Date:** 2026-07-26
**Status:** Approved (design), pending implementation plan
**Author:** Principal data engineer (via brainstorming)
**Depends on:** `docs/superpowers/specs/2026-07-26-aws-glue-catalog-compare-design.md`

## Context

The AWS expansion now includes S3 object checks/tracked jobs and Glue catalog inspection/compare. Athena is the next bounded service because it queries S3-backed datasets through Glue catalogs and produces row-level result sets that can feed data-quality metrics.

The AWS tab currently enables S3 and Glue. Athena and Airflow remain disabled placeholders. There is no existing Athena implementation and Docker Compose does not provide Athena or LocalStack, so this phase uses mocked Athena clients and mocked Playwright route coverage rather than live Athena e2e.

## Goals

- Add AWS Athena query execution support using saved AWS config resolution.
- Add ad-hoc Athena API routes to start a query, poll execution status, fetch results, and run a full query-to-results helper.
- Add lightweight DQ metric extraction from Athena result sets.
- Add a tracked `aws_athena_query` job type that maps query status, row counts, DQ metrics, and assertion failures into `ReconciliationResult`.
- Enable the Athena AWS sub-tab with query/database/output controls, ad-hoc execution, result/metric rendering, and tracked-job creation.
- Preserve Airflow as a disabled placeholder.
- Cover the feature with unit, route, executor, smoke, and mocked Playwright tests.

## Non-Goals

- Adding LocalStack, Athena Local, or other Docker services.
- Running live AWS Athena in automated tests.
- Supporting long-running async background polling workers.
- Writing Athena query results into project-owned storage tables.
- Mutating Glue, S3, or Athena resources.
- Implementing Airflow DAG/run tracking.
- Implementing a SQL parser or arbitrary query validation engine.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Credential source | Reuse saved AWS config fields and existing AWS config model | Keeps S3, Glue, and Athena auth behavior consistent. |
| Runtime layer | Add `AwsAthenaRuntime` with injectable Athena client | Keeps routes/executor testable without AWS. |
| Service layer | Add `AwsAthenaService` around start/status/results/DQ helpers | Avoids direct boto3 calls in routes and executor. |
| Result model | Use `ReconciliationResult` for tracked jobs | Keeps History, scheduler, DQ, contracts, and reports on existing paths. |
| Query execution | Synchronous helper polls with bounded attempts for ad-hoc and tracked jobs | Simple, deterministic, no worker infrastructure. |
| E2E | Mocked Playwright route coverage only | No live Athena-compatible Docker service exists. |

## Backend Architecture

### Athena Runtime

Create `api/services/aws_athena_runtime.py`.

Responsibilities:

- Resolve saved config by `config_id` or config name.
- Build `EnvironmentConfig` and AWS config through `aws_config_from_env`.
- Create an Athena client through the existing `AWSSession` pattern.
- Allow tests to inject a fake Athena client.

The runtime should mirror the Glue runtime shape and expose:

- `config_id(config_ref: int | str) -> int`
- `env(config_ref: int | str) -> EnvironmentConfig`
- `client(config_ref: int | str, override: Any | None = None) -> Any`

Missing configs map to HTTP 404 in route mode and tracked-job `ERROR` in executor mode.

### Athena Service

Create `api/services/aws_athena_service.py`.

Service methods:

- `start_query(config_id: int, database: str | None, query: str, output_location: str, workgroup: str | None = None) -> AthenaStartQueryResponse`
- `get_query_status(config_id: int, query_execution_id: str) -> AthenaQueryStatusResponse`
- `get_query_results(config_id: int, query_execution_id: str, max_rows: int = 100) -> AthenaQueryResultsResponse`
- `run_query(config_id: int, database: str | None, query: str, output_location: str, workgroup: str | None = None, poll_interval_seconds: float = 0.2, max_attempts: int = 20, max_rows: int = 100) -> AthenaRunQueryResponse`
- `compute_dq_metrics(rows: list[dict[str, str | None]]) -> dict[str, Any]`

Query start behavior:

- Call `start_query_execution` with `QueryString`, optional `QueryExecutionContext.Database`, required `ResultConfiguration.OutputLocation`, and optional `WorkGroup`.
- Return the `QueryExecutionId`.

Status behavior:

- Call `get_query_execution` and normalize:
  - `query_execution_id`
  - `state`: `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, or raw AWS state string.
  - `state_change_reason`
  - `submission_time`, `completion_time`
  - `engine_execution_time_ms`
  - `data_scanned_bytes`

Results behavior:

- Call `get_query_results` and convert Athena rows into list-of-dicts using the first returned row as headers.
- Preserve missing values as `None`.
- Limit fetched data to `max_rows` data rows.
- Return columns and rows separately.

Run helper behavior:

- Start query.
- Poll status until terminal state or `max_attempts` is exhausted.
- Fetch results only when state is `SUCCEEDED`.
- Return DQ metrics computed from returned rows.
- Timeout after `max_attempts` produces a controlled error in tracked-job execution and HTTP 400 in ad-hoc route mode.

## DQ Metrics

`compute_dq_metrics(rows)` computes lightweight metrics from the returned result rows:

- `row_count`: number of returned data rows.
- `columns`: ordered column names.
- `null_counts`: count of `None` or empty string values per column.
- `distinct_counts`: count of distinct non-null stringified values per column.
- `numeric`: per-column numeric stats for columns where every non-null value can parse as a number:
  - `min`
  - `max`
  - `avg`

Metric extraction is intentionally bounded to returned rows, not full Athena table scans. Users who need full-table metrics should write an aggregate SQL query and assert the returned values.

## API Design

Add `api/routes/aws_athena.py` and include it from `api/main.py` under `/api/aws/athena`.

Routes:

- `POST /api/aws/athena/start-query`
  - Request: `{ "config_id": 1, "database": "curated", "query": "select * from orders limit 10", "output_location": "s3://bucket/athena/", "workgroup": "primary" }`
  - Response: `{ "query_execution_id": "..." }`
- `POST /api/aws/athena/query-status`
  - Request: `{ "config_id": 1, "query_execution_id": "..." }`
  - Response: normalized status fields.
- `POST /api/aws/athena/query-results`
  - Request: `{ "config_id": 1, "query_execution_id": "...", "max_rows": 100 }`
  - Response: `{ "columns": [...], "rows": [...] }`
- `POST /api/aws/athena/run-query`
  - Request: start-query fields plus optional `poll_interval_seconds`, `max_attempts`, and `max_rows`.
  - Response: status, results, and `dq_metrics`.

Error handling:

- Missing config returns HTTP 404.
- AWS/Athena client errors return HTTP 400 with `{ "error_type": ..., "message": ... }`.
- Failed/cancelled queries return HTTP 400 for `run-query`, with normalized status included in detail.
- `start-query`, `query-status`, and `query-results` do not interpret DQ assertion failures; they only report Athena behavior.

## Tracked Job Type

Add `aws_athena_query`.

Params:

- `config` or `config_id`: saved AWS config reference.
- `database`: optional Glue/Athena database.
- `query`: SQL query string.
- `output_location`: S3 output location for Athena results.
- `workgroup`: optional Athena workgroup.
- `max_rows`: optional integer, default `100`.
- `poll_interval_seconds`: optional float, default `0.2`.
- `max_attempts`: optional integer, default `20`.
- `min_rows`: optional inclusive returned-row lower bound.
- `max_rows_assert`: optional inclusive returned-row upper bound. This is separate from `max_rows`, which controls fetch size.
- `expected_status`: optional terminal status, default `SUCCEEDED`.
- `metric_assertions`: optional map of metric paths to expected values, e.g. `{ "numeric.amount.min": 0, "null_counts.customer_id": 0 }`.

Validation:

- Config reference, query, and output location are required.
- `max_rows`, `max_attempts`, `min_rows`, and `max_rows_assert` must be non-negative integers when provided.
- `poll_interval_seconds` must be non-negative when provided.
- `min_rows <= max_rows_assert` when both are provided.
- `expected_status` must be one of `SUCCEEDED`, `FAILED`, `CANCELLED` when provided.
- `metric_assertions` must be an object/map when provided.

Execution:

- Resolve saved config.
- Use `AwsAthenaService.run_query()`.
- Return `PASSED` when the query reaches `expected_status` and assertions pass.
- Return `FAILED` when query execution completes but expected status, row bounds, or metric assertions fail.
- Return `ERROR` for config resolution errors, AWS client failures, polling timeout, or invalid service responses.

Metrics:

- `query_execution_id`
- `state`
- `row_count`
- `columns`
- `null_counts`
- `distinct_counts`
- `numeric`
- `engine_execution_time_ms`
- `data_scanned_bytes`

Mismatch records:

- `athena_status_mismatch`
- `athena_row_count_below_min`
- `athena_row_count_above_max`
- `athena_metric_mismatch`

The executor should add an `athena` section to `mismatch_summary` with normalized status, DQ metrics, and assertion details for reporting/debugging.

## Frontend Design

Enable the Athena sub-tab in `frontend/partials/tab-aws.html` and extend `frontend/features/aws.js`.

Athena state:

- `awsAthenaDatabase`
- `awsAthenaQuery`
- `awsAthenaOutputLocation`
- `awsAthenaWorkgroup`
- `awsAthenaMaxRows`
- `awsAthenaMinRows`
- `awsAthenaMaxRowsAssert`
- `awsAthenaJobName`
- `awsAthenaResult`
- `awsAthenaError`
- `awsAthenaLoading`

Controls:

- Reuse existing AWS config selector.
- Database input.
- SQL textarea.
- Output S3 location input.
- Workgroup input.
- Max rows input.
- Optional min/max row assertion inputs.
- `Run Athena Query` button.
- `Create Athena Query Job` button.

UI behavior:

- `Run Athena Query` calls `/api/aws/athena/run-query` and renders status, rows, and DQ metrics.
- `Create Athena Query Job` posts `job_type: "aws_athena_query"` to `/api/jobs` with the same params plus row assertions.
- Athena errors render in the Athena panel without mutating S3 or Glue result/error state.
- Airflow remains a disabled placeholder.

## Testing

### Unit Tests

- Athena runtime config resolution including named config and missing config 404.
- Service tests using fake Athena client:
  - start query passes database/output/workgroup arguments.
  - status normalization includes timing and scanned bytes.
  - results parsing converts headers + rows into dictionaries.
  - DQ metrics count rows/nulls/distincts/numeric stats.
  - run helper handles success, failure, cancellation, and timeout.
- Job validation tests for `aws_athena_query`.
- RunExecutor tests for pass/fail/error outcomes and `athena` mismatch summary.

### Route Tests

- `/api/aws/athena/start-query` returns query execution id.
- `/api/aws/athena/query-status` returns normalized status.
- `/api/aws/athena/query-results` returns columns/rows.
- `/api/aws/athena/run-query` returns status, results, and DQ metrics.
- Missing config and fake AWS errors map to expected HTTP statuses.

### Frontend/Smoke Tests

- AWS smoke test asserts Athena tab is enabled and Airflow remains disabled.
- Smoke test asserts Athena query/job controls render in `frontend/index.html`.
- Node syntax check covers `frontend/features/aws.js`.
- Build step regenerates `frontend/index.html`.

### Playwright

Add mocked Playwright coverage for the Athena AWS tab:

- Intercept `/api/aws/athena/run-query` and return a successful result with DQ metrics.
- Assert the Athena panel renders status, row preview, and DQ metric sections.
- Intercept `/api/jobs` and assert `job_type: "aws_athena_query"` plus params.
- Do not require Docker or live Athena.

## Rollout

1. Add Athena runtime/service primitives and tests.
2. Add Athena routes and route tests.
3. Add tracked-job validation and executor support.
4. Enable Athena UI panel and smoke tests.
5. Add mocked Playwright coverage.
6. Run focused backend, frontend, and Playwright verification.

## Future Specs

After Athena ships, define separate specs for:

- Airflow: DAG integrity, operator mocks, and run tracking.
- Glue Spark job execution, if still needed after Athena query execution is available.
- Optional richer Athena DQ assertion operators such as greater-than/less-than expressions if exact metric equality is too limiting.
