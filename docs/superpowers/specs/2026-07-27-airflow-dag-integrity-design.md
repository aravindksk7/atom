# Airflow DAG Integrity — Design

**Date:** 2026-07-27
**Status:** Approved (design), pending implementation plan
**Author:** Principal data engineer (via brainstorming)
**Depends on:** `docs/superpowers/specs/2026-07-26-aws-athena-query-runner-design.md`

## Context

The AWS data platform expansion now includes S3 object checks, Glue catalog comparison, and Athena query execution/DQ metrics. The AWS tab enables S3, Glue, and Athena. Airflow is the remaining disabled placeholder.

Airflow is the orchestration layer around the data checks already implemented. This phase adds validation for DAG structure, mocked operator contracts, and run/task-state tracking without requiring a live Airflow Docker service. Existing repository exploration did not find Airflow implementation files or a local Airflow service, so this phase uses an injectable client and mocked tests.

## Goals

- Add Airflow connectivity/runtime support using saved config fields or explicit Airflow connection params.
- Add ad-hoc Airflow API routes for DAG list, DAG detail, DAG integrity check, operator mock validation, and run status lookup.
- Add tracked job types for DAG integrity, operator mock validation, and run tracking.
- Enable the Airflow AWS sub-tab with ad-hoc checks, result rendering, and tracked-job creation.
- Preserve existing S3, Glue, and Athena behavior and UI state isolation.
- Cover the feature with unit, route, executor, smoke, and mocked Playwright tests.

## Non-Goals

- Starting, pausing, unpausing, triggering, clearing, or mutating Airflow DAGs/runs/tasks.
- Adding Airflow, Astronomer, MWAA, LocalStack, or Docker services.
- Running live Airflow e2e tests.
- Parsing arbitrary Python DAG source code locally.
- Implementing a full Airflow scheduler/executor mock.
- Replacing existing project scheduler/history behavior.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Airflow client | Add injectable HTTP-style `AirflowClient` wrapper | Keeps routes/executor testable without live Airflow. |
| Config source | Accept saved config fields plus explicit params | Supports both project config storage and ad-hoc tests. |
| Mutation policy | Read-only API calls only | Avoids changing orchestration state from validation UI/jobs. |
| Result model | Use `ReconciliationResult` for tracked jobs | Preserves History, scheduler, DQ checks, contracts, and reports. |
| Operator mocks | Validate declarative mock specs against DAG task metadata | Provides useful validation without executing operators. |
| E2E | Mocked Playwright route coverage only | No live Airflow service exists in Docker Compose. |

## Backend Architecture

### Airflow Runtime

Create `api/services/airflow_runtime.py`.

Responsibilities:

- Resolve connection settings from a saved config id/name or explicit params.
- Build an injectable Airflow client.
- Keep route and executor code independent from raw HTTP details.

Supported connection fields:

- `airflow_base_url`: required unless supplied directly in request/job params.
- `airflow_username`: optional basic-auth username.
- `airflow_password`: optional basic-auth password.
- `airflow_token`: optional bearer token.
- `airflow_verify_ssl`: optional boolean, default `true`.
- `airflow_timeout_seconds`: optional float, default `10`.

Authentication precedence:

1. Bearer token when `airflow_token` is set.
2. Basic auth when username/password are set.
3. Anonymous requests otherwise.

The runtime exposes:

- `config_id(config_ref: int | str) -> int`
- `settings(config_ref: int | str | None, overrides: dict[str, Any] | None = None) -> AirflowConnectionSettings`
- `client(config_ref: int | str | None, overrides: dict[str, Any] | None = None, override_client: Any | None = None) -> AirflowClient`

Missing saved configs map to HTTP 404 in route mode and tracked-job `ERROR` in executor mode.

### Airflow Client

Create a small read-only client wrapper in `api/services/airflow_service.py` or `api/services/airflow_client.py`.

Client methods:

- `list_dags() -> list[dict[str, Any]]`
- `get_dag(dag_id: str) -> dict[str, Any]`
- `get_tasks(dag_id: str) -> list[dict[str, Any]]`
- `get_dag_runs(dag_id: str, limit: int = 10) -> list[dict[str, Any]]`
- `get_dag_run(dag_id: str, dag_run_id: str) -> dict[str, Any]`
- `get_task_instances(dag_id: str, dag_run_id: str) -> list[dict[str, Any]]`

HTTP calls use Airflow stable REST API shape:

- `GET /api/v1/dags`
- `GET /api/v1/dags/{dag_id}`
- `GET /api/v1/dags/{dag_id}/tasks`
- `GET /api/v1/dags/{dag_id}/dagRuns`
- `GET /api/v1/dags/{dag_id}/dagRuns/{dag_run_id}`
- `GET /api/v1/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances`

The implementation should tolerate common response wrapper keys such as `dags`, `tasks`, `dag_runs`, and `task_instances`.

### Airflow Service

Create `api/services/airflow_service.py` with normalized service methods:

- `list_dags(config_ref, overrides=None) -> AirflowDagListResponse`
- `describe_dag(config_ref, dag_id, overrides=None) -> AirflowDagDetailResponse`
- `check_dag_integrity(config_ref, dag_id, required_tasks=None, required_dependencies=None, overrides=None) -> AirflowDagIntegrityResponse`
- `validate_operator_mocks(config_ref, dag_id, operator_mocks, overrides=None) -> AirflowOperatorMockResponse`
- `get_run_status(config_ref, dag_id, dag_run_id, overrides=None) -> AirflowRunStatusResponse`

Normalized DAG detail:

- `dag_id`
- `is_paused`
- `is_active`
- `fileloc`
- `owners`
- `schedule_interval`
- `tasks`: list of `{task_id, operator_name, upstream_task_ids, downstream_task_ids}`

## DAG Integrity Check

Input:

- `dag_id`
- `required_tasks`: optional list of task ids.
- `required_dependencies`: optional list of `{upstream: str, downstream: str}`.

Validation categories:

- `missing_tasks`: required tasks not present in DAG metadata.
- `extra_tasks`: optional future category, not enabled by default.
- `missing_dependencies`: required upstream/downstream edges not present.
- `inactive_dag`: DAG exists but is inactive.
- `paused_dag`: optional warning when DAG is paused.
- `import_errors`: captured if Airflow API exposes import error metadata for the DAG.

A DAG integrity check returns `match: true` only when no blocking mismatch categories are present. Paused DAGs are warnings unless the request/job asks `fail_on_paused: true`.

## Operator Mock Validation

Operator mock specs are declarative and do not execute real operators.

Input example:

```json
{
  "task_id": "load_orders",
  "operator_name": "PythonOperator",
  "required_upstream": ["extract_orders"],
  "required_downstream": ["dq_orders"],
  "mock_outputs": {"row_count": 100}
}
```

Validation categories:

- `missing_mock_tasks`: mock references a task id not present in the DAG.
- `operator_mismatches`: actual operator differs from expected operator name.
- `upstream_mismatches`: required upstream task ids are missing.
- `downstream_mismatches`: required downstream task ids are missing.
- `invalid_mock_outputs`: mock outputs are not JSON object values where required.

Operator mocks return `match: true` only when every mock spec aligns with DAG task metadata.

## Run Tracking

Run tracking checks one DAG run and its task instances.

Input:

- `dag_id`
- `dag_run_id`
- `expected_dag_state`: optional, default `success`.
- `expected_task_states`: optional map of task id to expected state.
- `allow_running`: optional boolean, default `false`.

Validation categories:

- `dag_state_mismatch`
- `task_state_mismatches`
- `missing_task_instances`
- `unexpected_running_tasks`

Run tracking returns `match: true` when the DAG run and requested task states meet expectations.

## API Design

Add `api/routes/airflow.py` and include it from `api/main.py` under `/api/aws/airflow` to keep it within the existing AWS tab route namespace.

Routes:

- `POST /api/aws/airflow/dags`
  - Request: `{ "config_id": 1, "airflow_base_url": "https://..." }`
  - Response: `{ "dags": [...] }`
- `POST /api/aws/airflow/dag`
  - Request: `{ "config_id": 1, "dag_id": "orders_dag" }`
  - Response: normalized DAG detail.
- `POST /api/aws/airflow/dag-integrity`
  - Request: `{ "config_id": 1, "dag_id": "orders_dag", "required_tasks": [...], "required_dependencies": [...] }`
  - Response: `{ "match": true|false, "dag": ..., "diff": ..., "warnings": ... }`
- `POST /api/aws/airflow/operator-mocks`
  - Request: `{ "config_id": 1, "dag_id": "orders_dag", "operator_mocks": [...] }`
  - Response: `{ "match": true|false, "dag": ..., "diff": ... }`
- `POST /api/aws/airflow/run-status`
  - Request: `{ "config_id": 1, "dag_id": "orders_dag", "dag_run_id": "manual__...", "expected_dag_state": "success" }`
  - Response: `{ "match": true|false, "dag_run": ..., "task_instances": [...], "diff": ... }`

Error handling:

- Missing saved config returns HTTP 404.
- Missing Airflow base URL returns HTTP 400.
- Airflow HTTP/API failures return HTTP 400 with structured `{ "error_type": ..., "message": ... }`.
- Integrity/mock/run mismatches are not HTTP errors; they return `match: false` and diff details.

## Tracked Job Types

### `airflow_dag_integrity`

Params:

- `config` or `config_id`: optional when explicit Airflow connection params are supplied.
- `airflow_base_url`: optional override.
- `dag_id`: required.
- `required_tasks`: optional list of strings.
- `required_dependencies`: optional list of `{upstream, downstream}`.
- `fail_on_paused`: optional boolean, default `false`.

Execution:

- Call `AirflowService.check_dag_integrity()`.
- Return `PASSED` when `match` is true.
- Return `FAILED` when integrity diff has blocking mismatches.
- Return `ERROR` for config/client/API failures.

Mismatch types:

- `missing_tasks`
- `missing_dependencies`
- `inactive_dag`
- `paused_dag`
- `airflow_import_error`

### `airflow_operator_mock`

Params:

- `config` or `config_id`: optional when explicit connection params are supplied.
- `airflow_base_url`: optional override.
- `dag_id`: required.
- `operator_mocks`: required non-empty list of mock specs.

Execution:

- Call `AirflowService.validate_operator_mocks()`.
- Return `PASSED`/`FAILED` based on `match`.
- Return `ERROR` for config/client/API failures.

Mismatch types:

- `missing_mock_tasks`
- `operator_mismatch`
- `upstream_mismatch`
- `downstream_mismatch`
- `invalid_mock_outputs`

### `airflow_run_tracking`

Params:

- `config` or `config_id`: optional when explicit connection params are supplied.
- `airflow_base_url`: optional override.
- `dag_id`: required.
- `dag_run_id`: required.
- `expected_dag_state`: optional, default `success`.
- `expected_task_states`: optional object map.
- `allow_running`: optional boolean, default `false`.

Execution:

- Call `AirflowService.get_run_status()`.
- Return `PASSED`/`FAILED` based on `match`.
- Return `ERROR` for config/client/API failures.

Mismatch types:

- `dag_state_mismatch`
- `task_state_mismatch`
- `missing_task_instance`
- `unexpected_running_task`

All three job types add an `airflow` section to `mismatch_summary` containing normalized response, diff, warnings, and connection context without secrets.

## Frontend Design

Enable the Airflow AWS sub-tab in `frontend/partials/tab-aws.html` and extend `frontend/features/aws.js`.

Airflow state:

- `awsAirflowBaseUrl`
- `awsAirflowDagId`
- `awsAirflowDagRunId`
- `awsAirflowRequiredTasksRaw`
- `awsAirflowRequiredDependenciesRaw`
- `awsAirflowOperatorMocksRaw`
- `awsAirflowExpectedDagState`
- `awsAirflowExpectedTaskStatesRaw`
- `awsAirflowAllowRunning`
- `awsAirflowFailOnPaused`
- `awsAirflowJobName`
- `awsAirflowResult`
- `awsAirflowError`
- `awsAirflowLoading`

Controls:

- Reuse the AWS config selector.
- Optional Airflow base URL override.
- DAG id input.
- DAG run id input for run tracking.
- Required tasks textarea as newline/comma separated ids.
- Required dependencies JSON textarea.
- Operator mocks JSON textarea.
- Expected DAG/task state controls.
- Buttons for:
  - `Check DAG Integrity`
  - `Validate Operator Mocks`
  - `Check Run Status`
  - `Create DAG Integrity Job`
  - `Create Operator Mock Job`
  - `Create Run Tracking Job`

UI behavior:

- Render Airflow result/diff/warning categories in the Airflow panel.
- Render Airflow errors only in the Airflow panel.
- Do not mutate S3, Glue, or Athena result/error state.
- Client-side JSON parsing errors for dependencies/operator mocks/task state maps display inline before API calls.

## Testing

### Unit Tests

- Airflow runtime config resolution and explicit override handling.
- Client URL/auth construction with fake session/client.
- Service tests with fake Airflow client:
  - list DAGs.
  - describe DAG and normalize tasks.
  - DAG integrity pass/fail categories.
  - operator mock pass/fail categories.
  - run tracking pass/fail categories.
- Job validation tests for all three Airflow job types.
- RunExecutor tests for pass/fail/error outcomes and `airflow` mismatch summaries.

### Route Tests

- `/api/aws/airflow/dags` returns DAG list.
- `/api/aws/airflow/dag` returns normalized DAG detail.
- `/api/aws/airflow/dag-integrity` returns `match: false` with diff for missing tasks/dependencies.
- `/api/aws/airflow/operator-mocks` returns `match: false` with mock diff.
- `/api/aws/airflow/run-status` returns `match: false` with run/task-state diff.
- Missing config/base URL and fake client errors map to expected HTTP statuses.

### Frontend/Smoke Tests

- AWS smoke test asserts Airflow tab is enabled.
- Smoke test asserts Airflow check/job controls render in `frontend/index.html`.
- Node syntax check covers `frontend/features/aws.js`.
- Build step regenerates `frontend/index.html`.

### Playwright

Add mocked Playwright coverage for the Airflow AWS tab:

- Intercept `/api/aws/airflow/dag-integrity` and return a missing task/dependency diff.
- Assert the Airflow panel renders diff categories.
- Intercept `/api/aws/airflow/operator-mocks` and verify mock diff rendering.
- Intercept `/api/aws/airflow/run-status` and verify run-state diff rendering.
- Intercept `/api/jobs` and assert each tracked job payload.
- Do not require Docker or live Airflow.

## Rollout

1. Add Airflow runtime/client/service primitives and tests.
2. Add Airflow routes and route tests.
3. Add tracked-job validation and executor support.
4. Enable Airflow UI panel and smoke tests.
5. Add mocked Playwright coverage.
6. Run focused backend, frontend, and Playwright verification.

## Future Specs

After Airflow ships, possible follow-up specs:

- Glue Spark job execution.
- Richer Athena assertion operators.
- Live Airflow integration tests if a local Airflow service is later added.
- Optional Airflow DAG trigger controls, if mutation is explicitly approved.
