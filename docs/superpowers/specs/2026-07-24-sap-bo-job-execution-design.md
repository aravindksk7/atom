# SAP BO job execution ("bo_job") — design

## Problem

`etl_framework/sap_bo/` (`BORestClient`, `SAPBOReportRunner`, `SAPBOValidator`)
and the `bo_report` job type are read/browse/download-only: they authenticate,
list/download an already-generated WebI report, and reconcile it. There is no
way to make SAP BO actually *run* something — refresh a WebI document,
re-run a Crystal Report, or fire a Publication — from inside the ETL
framework's job orchestration. Users who need a BO object refreshed before
validating its output today must trigger that refresh manually in BO,
outside the ETL framework, before launching a job sequence.

Job orchestration itself is already generic: `RunExecutor.execute()` runs a
`job_sequence` (`SequenceStep` list) strictly sequentially, gating each step
on `depends_on`/`condition` (`api/services/run_executor.py` `execute`,
`_validate_dependencies`). Adding a job type that *triggers and waits for*
BO work needs no new orchestration mechanism — it needs a `_build_case_*`
step function, same as `automic_job`'s status-poll pattern.

## Approach

Add a new job type, `bo_job`, that schedules a SAP BO InfoStore object (any
type — WebI, Crystal, Publication — identified by its CUID) via the generic
BOE REST scheduling endpoint, then blocks (poll loop, bounded by a timeout)
until the run finishes, mapping the final BOE status to `TestStatus`.

`bo_job` is kept separate from `bo_report`:
- `bo_report` stays validation-only (download + reconcile row counts).
- `bo_job` is trigger-only (schedule + wait), producing a pass/fail result
  but no data comparison.

To run a BO object and then validate its output, a user defines two jobs and
chains them with the existing `depends_on` field — `bo_job` runs first,
`bo_report` (or a `bo_live` reconciliation job) depends on it and runs after.
No new dependency/orchestration plumbing is needed; `_validate_dependencies`
already enforces that a job's `depends_on` predecessors appear earlier in the
sequence.

Out of scope: direct SAP data extraction (RFC/OData/BAPI calls against
S/4HANA or ECC, bypassing the BO layer entirely) — a separate future
feature, tracked but not designed here.

## Components

1. **`etl_framework/sap_bo/client.py` — `BORestClient`**

   Two new methods, following this file's existing lazy-auth /
   `BOAPIError`-on-failure conventions:

   - `schedule_object(object_id: str, schedule_params: dict | None = None) -> str`
     — `POST /biprws/infostore/{object_id}/schedules` (empty body unless
     `schedule_params` is given, then passed through as the JSON body for
     object-specific run parameters, e.g. prompt values). Returns the new
     schedule instance id parsed from the response.
   - `wait_for_completion(instance_id: str, timeout_s: float, poll_interval_s: float) -> dict`
     — polls `GET /biprws/infostore/{instance_id}` in a `time.monotonic()`-
     bounded loop, sleeping `poll_interval_s` between checks, until the
     instance's status reads as terminal (`Success` / `Failed`) or
     `timeout_s` elapses. Returns the final instance status payload (dict);
     the caller maps it to `TestStatus`.

     Status field name/values are matched **best-effort, case-insensitively**
     (`success`, `failed`, `recurring`, `paused`, `pending`, `running`) —
     the exact BOE REST response shape for schedule instances is not yet
     confirmed against a live on-prem server. The implementer verifies this
     against a real biprws instance while coding and adjusts the matching
     logic if the field name or values differ, the same way `client.py`
     already documents and works around on-prem `biprws` pagination quirks
     (`_unwrap_collection`, `_paginate_biprws_collection`). If verification
     isn't possible before shipping, ship with the best-effort mapping and a
     `logger.warning` when an unrecognized status string is encountered, so
     the mismatch surfaces in run logs instead of silently mis-mapping to
     PASSED/FAILED.

2. **`api/schemas.py` — `JobDefinition`**

   - Add `"bo_job"` to the `job_type` `Literal`.
   - `validate_reconciliation_contract`: add a `bo_job` branch requiring
     `params.object_id`.
   - New optional params, all under `job.params` (no new top-level
     `JobDefinition` fields, consistent with how `automic_job`/`bo_report`
     params work):
     - `schedule_params: dict` — passed through to `schedule_object`.
     - `poll_interval_s: float` — default `5`.
     - `timeout_s: float` — default `600`.

3. **`api/services/run_executor.py` — `RunExecutor`**

   - `_build_case` dispatcher: add
     `if job.job_type == "bo_job" and self._settings.use_live_connections: return self._build_case_bo_job(job)`
     next to the `bo_report`/`automic_job`/`api_reconciliation` lines, and
     (matching the `bo_live` reconciliation branch) a case that raises
     `ValueError("bo_job jobs require live connections to be enabled")` when
     `use_live_connections` is off, so runs fail fast and explicitly in
     environments where live external calls are disabled (e.g. CI) rather
     than hanging or silently no-op'ing.
   - New `_build_case_bo_job(job)`, following `_build_case_bo_report`'s
     structure: resolve `bo_credentials` from `self._config_snapshot` into
     an `EnvironmentConfig`, build a `BORestClient`, `authenticate()`,
     `schedule_object(job.params["object_id"], job.params.get("schedule_params"))`,
     `wait_for_completion(...)` using the job's `poll_interval_s`/
     `timeout_s`, `logout()` in a `finally`. Maps the terminal status to
     `TestStatus` (`Success → PASSED`, `Failed → FAILED`, timeout-without-
     terminal-status → `ERROR` with a "timed out after {timeout_s}s" message
     surfaced via the result's mismatch/error text). Returns a
     `ReconciliationResult` with `source_row_count`/`target_row_count`/etc.
     all `0` (no data comparison — same shape `automic_job`'s result uses),
     `executed_at`, and `duration_seconds` measured across the schedule+wait
     call.

4. **Frontend — `frontend/index.html` job editor modal**

   - Add `<option value="bo_job">bo_job</option>` to the Job Type select
     (next to `bo_report`/`automic_job`, ~line 1174).
   - New conditional param block, `x-show="jobModal.job_type === 'bo_job'"`
     (mirrors the existing `automic_job` block at line 1469): BO Object ID
     (CUID) input, Schedule Params (JSON textarea, optional), Poll Interval
     (seconds, number input, default 5), Timeout (seconds, number input,
     default 600).
   - Reuses the existing BO config/environment selector pattern already
     used by `bo_report`'s fields for choosing which `EnvironmentConfig`
     (`bo_url`/`bo_user`/etc.) to authenticate with.

5. **No new API routes, no new CLI commands.** Existing generic
   `JobDefinition` CRUD/launch endpoints already accept any `job_type`
   literal; `bo_job` jobs are created, saved, sequenced, and launched
   through the same paths as every other job type. The CLI (`etl_framework/
   cli/app.py`) launches by *selection*, not by job type, so it needs no
   changes either.

## Error handling

- Missing `object_id`: same pattern as `bo_report`'s missing `report_id` —
  `ValueError("bo_job jobs require 'object_id' in params")` raised from the
  schema validator at job-save time, not at run time.
- `use_live_connections` disabled: fail-fast `ValueError` at run time (see
  Components §3), same pattern as the `bo_live` reconciliation branch.
- BOE schedule POST failure (auth, 4xx/5xx, object not found): existing
  `BOAPIError` propagates unchanged through to the run's ERROR status path,
  same as every other `BORestClient` call site.
- Poll timeout without a terminal status: `TestStatus.ERROR` (not `FAILED` —
  the run didn't fail, it didn't finish in time; distinguishable in run
  history and consistent with `TestStatus.ERROR`'s existing meaning
  elsewhere in the codebase).
- Unrecognized status string from BOE: logged as a warning (see Components
  §1), not silently treated as success.

## Testing

- Unit — `BORestClient.schedule_object` / `wait_for_completion`: mock
  `requests.Session`, assert correct URL/method, instance id parsing,
  poll-loop timing/timeout behavior (using a fake clock or monkeypatched
  `time.monotonic`/`time.sleep`), and status-string mapping including an
  unrecognized-status warning case.
- Unit — `JobDefinition` validator: `bo_job` requires `object_id`; accepts
  optional `schedule_params`/`poll_interval_s`/`timeout_s`.
- Unit — `RunExecutor._build_case_bo_job`: mock `BORestClient`, assert
  Success/Failed/timeout each map to the correct `TestStatus`, and that
  `use_live_connections=False` raises before touching the client.
- E2E: extend the existing SAP BO Playwright coverage
  (`tests/e2e/08a-compare-bo-report.spec.ts`) with a case that creates a
  `bo_job`, chains a dependent `bo_report` job after it via `depends_on`,
  and launches the sequence -- asserting the `bo_job` step completes before
  the `bo_report` step starts. (Superseded in practice by the integration
  test added against the SAP BO mock server, which exercises the same
  schedule-and-wait behavior without needing browser automation; add the
  Playwright case only if UI-level coverage of the job modal's `bo_job`
  fields is also desired.)

## Out of scope

- Direct SAP data extraction (RFC/OData/BAPI) bypassing SAP BO — separate
  future feature.
- Cancelling an in-flight BOE schedule instance — not needed for the
  trigger-and-wait use case; can be added later if a job needs to be
  abortable mid-run.
- A shared/reusable "BO object picker" UI beyond a plain CUID text input —
  matches the existing `bo_report` fields' level of polish (plain ID
  inputs, no BO folder browser embedded in the job modal).
- Changes to `bo_report`'s existing (validation-only) behavior — `bo_job` is
  purely additive.
