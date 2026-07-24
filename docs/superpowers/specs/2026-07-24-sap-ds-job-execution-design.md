# SAP Data Services (DS) job execution ("ds_job") — design

## Problem

The ETL framework's job orchestration can already trigger and wait on SAP BO
InfoStore objects (`bo_job`, added in a prior feature) and Automic jobs
(`automic_job`). SAP Data Services (BODS) is a separate SAP product — an ETL
tool, not the BI/reporting platform SAP BO is — with its own connection
endpoint, its own login/authentication flow, and its own job-addressing
model (batch jobs identified by name within a named repository, not by
InfoStore CUID). There is currently no way to trigger or monitor a SAP DS
batch job from inside this framework's job orchestration; users must trigger
DS jobs manually, outside the framework, before or after an ETL job
sequence runs.

## Approach

Mirror the existing `bo_job`/`BORestClient`/`AutomicClient` pattern exactly.
This codebase already has a consistent, three-times-proven shape for
"connect to an external system via its own endpoint/login, trigger a named
unit of work, poll for completion, map the result to `TestStatus`." SAP DS
gets the same treatment: its own `EnvironmentConfig` fields (separate from
`bo_*`/`automic_*`), its own REST client module, its own job type wired into
`RunExecutor`'s existing sequential dispatcher — no new orchestration
mechanism, chained via the existing `depends_on` field exactly like `bo_job`
and `automic_job` already are.

**API uncertainty, called out up front:** SAP BO's REST API (`biprws`) is
officially documented and was reliable to build `BORestClient` against. SAP
DS's Administrator/Management Console REST API is far less standardized
across DS versions. This design's endpoint paths and payload shapes for
`DSRestClient` are **best-effort**, based on SAP DS Administrator's commonly
documented session-login + batch-job-trigger-by-name-and-repository shape,
and are **not verified against a live SAP DS instance**. This is the same
approach already used successfully for `bo_job`'s schedule-status field
parsing (`etl_framework/sap_bo/client.py`'s `STATUS_MAP`/
`_normalise_schedule_status`), which shipped with a documented best-effort
mapping, a `logger.warning` on any unrecognized value, and an explicit note
for the implementer to verify against a real server. The same discipline
applies here throughout, not just on one field.

Out of scope: SAP DS design-time operations (deploying, modifying, or
validating dataflows/jobs in the Designer sense — this is purely about
*running* an already-designed, already-deployed batch job). Also out of
scope: the alternate "Job Server per-job web service" SAP DS interface
(each job individually published as its own SOAP endpoint via "Enable as
Web Service" in Designer) — deliberately not used here in favor of the
Management Console/Administrator's single central API, which needs no
per-job publishing step and matches how `automic_job`/`bo_job` already
address jobs by name rather than by pre-registered endpoint.

## Components

1. **`etl_framework/config/models.py` — `EnvironmentConfig`**

   New fields, placed after the existing `bo_*` block, mirroring its shape
   exactly:
   - `ds_url: str = ""`
   - `ds_user: str = ""`
   - `ds_password: str = ""`
   - `ds_repository: str = ""` — the named SAP DS repository job operations
     are scoped to. Unlike SAP BO (single InfoStore namespace per BOE
     deployment), a single DS Administrator instance can manage jobs across
     multiple named repositories, so the default repository lives in config
     and can be overridden per-job (see Component 3).
   - `ds_timeout: int = 60`
   - `ds_verify_ssl: bool = True`
   - `ds_proxy_url: str = ""`

   `SECRET_FIELDS` (same file) gains `"ds_password"`, alongside the existing
   `"db_password"`, `"automic_password"`, `"bo_password"` entries — this is
   the single list `api/routes/configs.py` (response masking) and
   `ConfigRepository` (encryption at rest) both already read from, so no
   other file needs to know about the new secret field.

   No new validators beyond mirroring `bo_timeout`'s `> 0` check for
   `ds_timeout`.

2. **`etl_framework/exceptions.py` — `DSAPIError`**

   New exception, mirroring `BOAPIError`'s shape (adapted: DS jobs are
   addressed by name, not report id):
   ```python
   class DSAPIError(ETLFrameworkError):
       def __init__(self, job_name: str, http_status: int, response_body: str) -> None:
           self.job_name = job_name
           self.http_status = http_status
           self.response_body = response_body
           super().__init__(f"SAP DS API error {http_status} for job '{job_name}'")
   ```

3. **New module `etl_framework/sap_ds/client.py` — `DSRestClient`**

   Structurally identical to `BORestClient` (`etl_framework/sap_bo/client.py`):
   lazy session-based auth, a `requests.Session`, `_base_url`/`_timeout`/
   `_verify_ssl`/proxy setup from `EnvironmentConfig`, raising `DSAPIError`
   on any HTTP status >= 400.

   - `login(username=None, password=None) -> str | None` — best-effort
     `POST {ds_url}/... /Login` (or equivalent session-establishment
     endpoint; exact path TBD, verify against a live server), sending
     `ds_user`/`ds_password`, returning/storing a session token the same
     way `BORestClient.authenticate()` does (`X-...-Token`-style header,
     exact header name TBD).
   - `logout() -> None` — mirrors `BORestClient.logout()`'s
     `if token and owns token: POST logoff` pattern.
   - `trigger_job(job_name: str, repository: str | None = None, job_params: dict | None = None) -> str`
     — triggers a batch job run by name within a repository (falling back
     to `EnvironmentConfig.ds_repository` if `repository` isn't given),
     passing `job_params` as substitution/global variables if provided.
     Returns a run/task id parsed from the response (best-effort `{"id": "..."}`
     shape, matching the convention already established for
     `BORestClient.schedule_object`'s response parsing).
   - `get_job_status(run_id: str) -> TestStatus` — polls run status, maps
     raw DS status strings to `TestStatus` via a `STATUS_MAP` class
     attribute, same pattern as `BORestClient.STATUS_MAP`/
     `_normalise_schedule_status`: unrecognized strings map to
     `TestStatus.RUNNING` with a `logger.warning`, not silently treated as
     success or failure.
   - `wait_for_completion(run_id: str, timeout_s: float = 600, poll_interval_s: float = 5) -> TestStatus`
     — identical poll-loop shape to `BORestClient.wait_for_completion`:
     `time.monotonic()`-bounded loop, checks status before checking the
     deadline (so a fast job never sleeps unnecessarily), raises a plain
     `TimeoutError` (not a custom exception) if never terminal, letting the
     existing `TestRunner` exception-to-`TestStatus.ERROR` mapping handle it
     exactly as `bo_job`'s timeout case already does.

4. **`api/schemas.py` — `JobDefinition`**

   - Add `"ds_job"` to the `job_type` `Literal`.
   - `validate_reconciliation_contract`: add a `ds_job` branch requiring
     `params.job_name`.
   - New optional params under `job.params` (no new top-level
     `JobDefinition` fields, consistent with `bo_job`/`automic_job`):
     - `repository: str` — overrides `ds_repository` from config for this
       job.
     - `job_params: dict` — passed through to `trigger_job` as DS
       substitution/global variables.
     - `poll_interval_s: float` — default `5`.
     - `timeout_s: float` — default `600`.

5. **`etl_framework/runner/job_validation.py`**

   Parallel `ds_job` branch (this file duplicates every job type's required-
   field check as `ValidationIssue`s for friendlier pre-save UI validation,
   alongside the pydantic contract in Component 4 — same duplication that
   already exists for every other job type, not new duplication introduced
   here): requires `params.job_name`.

6. **`api/services/run_executor.py` — `RunExecutor`**

   - `_build_case` dispatcher: add a `ds_job` branch next to the `bo_job`
     branch, same fail-fast-when-live-connections-disabled shape:
     ```python
     if job.job_type == "ds_job":
         if not self._settings.use_live_connections:
             def run_job() -> ReconciliationResult:
                 raise ValueError("ds_job jobs require live connections to be enabled")
             return run_job
         return self._build_case_ds_job(job)
     ```
   - New `_build_case_ds_job(job)`, following `_build_case_bo_job`'s
     structure exactly: resolve `ds_credentials` from
     `self._config_snapshot` into an `EnvironmentConfig`, build a
     `DSRestClient`, `login()`, `try/finally: logout()` around
     `trigger_job(job.params["job_name"], job.params.get("repository"), job.params.get("job_params"))`
     then `wait_for_completion(...)` using the job's `poll_interval_s`/
     `timeout_s`. Returns a `ReconciliationResult` with all row-count fields
     zeroed (same no-data-comparison shape `bo_job`/`automic_job` use),
     `status` from the client, `executed_at`, `duration_seconds`.
   - `RunExecutor`'s config-snapshot resolution (wherever
     `bo_credentials`/`automic_credentials` keys are assembled from the
     selected `EnvironmentConfig` before a run starts) gains a parallel
     `ds_credentials` key, mirroring the existing `bo_credentials`/
     `automic_credentials` assembly exactly.

7. **Frontend**

   - **Config tab** (`frontend/partials/tab-config.html` +
     `frontend/features/config.js`): new "SAP DS URL" / "DS User" / "DS
     Password" / "DS Repository" / "DS Timeout" / "DS Proxy URL" / "DS
     Verify SSL" fields in the environment config editor, mirroring the
     existing SAP BO fields block (`tab-config.html:603-618`) and its
     `configModal` defaults/hydration/save-payload wiring in `config.js`
     exactly.
   - **Job modal** (`frontend/partials/tab-launch.html` +
     `frontend/features/launch.js`): new `ds_job` option in the Job Type
     select, a conditional param block (Job Name, Repository (optional,
     falls back to config default), Job Params (JSON, optional), Poll
     Interval, Timeout), and the same 4-location wiring (`openNewJobModal`
     defaults, `openEditJobModal` hydration, `_buildJobRequestBody` payload
     building, `canSaveJob` save-gate) that `bo_job`'s job modal support
     used — this is now the second time this exact 4-location pattern is
     being followed, reinforcing it as the established convention for
     adding a job type to the UI.
   - No new API routes, no new CLI commands — same reasoning as `bo_job`:
     existing generic `JobDefinition` CRUD/launch endpoints already accept
     any `job_type` literal.

8. **Mock server for testing — `docker/sapds-mock/server.py`**

   New sibling to `docker/sapbo-mock/server.py`, same
   `BaseHTTPRequestHandler`-based single-file fake server pattern: fixture
   batch jobs (e.g. `"nightly_load"` → eventually `"Completed"`,
   `"bad_load"` → eventually `"Error"`), a login endpoint, a trigger
   endpoint returning a run id, and a status endpoint requiring 2+ polls to
   reach a terminal state (same multi-poll-exercise rationale as the SAP BO
   mock's `SCHEDULE_POLLS_TO_TERMINAL`). Added to
   `docker-compose.integration.yml` as a new `sapds` service, gated by the
   same kind of `RUN_LIVE_SAPDS_TESTS=1` environment variable pattern
   `RUN_LIVE_SAPBO_TESTS` already establishes.

## Error handling

- Missing `job_name`: `ValueError("ds_job jobs require 'job_name' in params")`
  at job-save time, same pattern as `bo_job`'s missing-`object_id` check.
- `use_live_connections` disabled: fail-fast `ValueError` at run time, same
  pattern as `bo_job`.
- DS login/trigger/status HTTP failure: `DSAPIError` propagates unchanged
  through to the run's ERROR status path, same as every `BORestClient`/
  `DSRestClient` call site convention.
- Poll timeout without a terminal status: `TimeoutError` raised from
  `wait_for_completion`, caught by the existing generic `TestRunner`
  exception handling and surfaced as `TestStatus.ERROR` — identical
  mechanism to `bo_job`'s timeout handling, no new error-to-status mapping
  code needed.
- Unrecognized DS status string: logged as a warning, treated as
  `TestStatus.RUNNING` (keep polling) rather than silently mapped to
  success or failure — same discipline as `bo_job`'s `STATUS_MAP` fallback.

## Testing

- Unit — `DSRestClient.login`/`trigger_job`/`get_job_status`/
  `wait_for_completion`: mock `requests.Session`, covering the same
  scenarios `BORestClient`'s equivalent methods are covered by (success,
  HTTP failure, missing-id-in-response, status-mapping table including an
  unrecognized-status-warning case, poll-loop timing/timeout).
- Unit — `JobDefinition`/`job_validation.py` validators: `ds_job` requires
  `job_name`; accepts optional `repository`/`job_params`/`poll_interval_s`/
  `timeout_s`.
- Unit — `RunExecutor._build_case_ds_job`: mock `DSRestClient`, assert
  Success/Failure/timeout each map to the correct `TestStatus`, and that
  `use_live_connections=False` raises before touching the client.
- Integration — `tests/integration/test_sapds_mock_container.py` (new,
  sibling to `test_sapbo_mock_container.py`): real HTTP round-trips against
  the SAP DS mock server, covering trigger-and-wait success/failure and an
  unknown-job-name error, mirroring the SAP BO mock integration test
  structure exactly.

## Out of scope

- SAP DS design-time operations (dataflow/job design, validation,
  deployment).
- The Job Server per-job web-service interface (per-job SOAP endpoints via
  "Enable as Web Service") — deliberately not built; Management
  Console/Administrator's central API is used instead.
- Cancelling an in-flight DS job run.
- A repository/job browser UI (plain text inputs for job name/repository,
  matching the level of polish `bo_job`'s plain CUID input already
  established).
- Changes to `bo_job`, `bo_report`, or `automic_job`'s existing behavior —
  this feature is purely additive, a new job type alongside them.
