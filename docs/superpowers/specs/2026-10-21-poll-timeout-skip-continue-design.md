# Poll-timeout resilience and "Mark passed & continue"

Date: 2026-10-21
Status: approved design, pending implementation plan

## Problem

A job sequence step that launches a remote job (SAP DS, SAP BO) can be marked
ERROR even though the remote job finished successfully. The common cause is a
`requests.ReadTimeout` raised by a single status-poll call: the poll loops in
`DSRestClient.wait_for_completion` (`etl_framework/sap_ds/client.py`) and
`BORestClient.wait_for_completion` (`etl_framework/sap_bo/client.py`) have no
exception handling, so one slow status response aborts the step while the job
keeps running remotely.

Once the step is ERROR, the operator has no good way forward:

- Downstream steps are BLOCKED (default `on_failure="skip_downstream"`).
- `POST /api/runs/{id}/restart` re-runs every step not in
  `CARRIED_OVER_STATUSES`, so the timed-out step re-triggers the remote job and
  it runs twice.
- Step-level retries (`max_retries > 0`, `retry_on=["error"]`) have the same
  double-execution problem, because the retry calls `trigger_job` again.

## Goals

1. A transient network error while polling must not fail the step (Part C).
2. When a step still fails but the operator knows the job passed in the source
   system, they can mark it passed and continue the sequence from the next step
   without re-executing that job (Part A).

## Non-goals

- A live "hold on failure" mode that pauses a running run for a decision.
- Changing launch-call (`trigger_job` / `schedule_object`) error handling: a
  timeout there is ambiguous (the job may or may not have started) and stays a
  failure.
- Automic, Airflow and REST API clients. Automic already retries via tenacity;
  the others are out of scope until a timeout is observed there.

## Part C: resilient polling

### New helper

`etl_framework/utils/polling.py`:

```python
def poll_until_terminal(
    fetch_status: Callable[[], TestStatus],
    *,
    timeout_s: float,
    poll_interval_s: float,
    max_poll_errors: int,
    describe: str,
) -> TestStatus:
```

Behavior:

- Calls `fetch_status()` in a loop. A result other than `TestStatus.RUNNING` is
  returned immediately.
- If `fetch_status()` raises `requests.Timeout` or `requests.ConnectionError`,
  it logs a warning naming `describe`, the error and the consecutive error
  count. Then it sleeps `poll_interval_s` and polls again.
- A successful poll resets the consecutive error counter to 0.
- When the consecutive error count reaches `max_poll_errors`, the last
  exception is re-raised.
- Any other exception (including `DSAPIError`, BO API errors, HTTP 4xx/5xx,
  SOAP faults) propagates immediately and is not retried.
- The overall deadline is unchanged: once `timeout_s` has elapsed after a
  RUNNING result or a tolerated error, it raises
  `TimeoutError(f"{describe} did not complete within {timeout_s}s")`.
- `max_poll_errors` must be >= 1. A value of 1 is the same as today's
  behavior: the first error is raised.

### Client changes

- `DSRestClient.wait_for_completion` and `BORestClient.wait_for_completion`
  gain a `max_poll_errors: int = 5` keyword and delegate to
  `poll_until_terminal`. Their existing `TimeoutError` messages are kept, passed
  in through `describe`.
- `RunExecutor._build_case_ds_job` and `_build_case_bo_job`
  (`api/services/run_executor.py`) pass
  `max_poll_errors=job.params.get("max_poll_errors", 5)`, mirroring
  `timeout_s` and `poll_interval_s`.

## Part A: "Mark passed & continue"

### Endpoint

`POST /api/runs/{run_id}/steps/by-id/{step_id}/mark-passed`

Request body (`RunStepMarkPassedRequest`):

| field         | type | rules                                  |
|---------------|------|----------------------------------------|
| `note`        | str  | required, stripped, non-empty, max 1000 |
| `released_by` | str  | required, non-empty                    |

Response: `RunStatusOut` for the new run (`status="PENDING"`,
`restarted_from_run_id=<run_id>`), status code 202.

Errors:

- 404 if the run or step does not exist.
- 409 if the run status is `PENDING` or `RUNNING`.
- 409 if the step status is not `FAILED` or `ERROR`.
- 422 if the note is blank, or if `restart_run` raises `RestartError` (for
  example, "selection already has an active run").

### Processing order

1. Validate as above.
2. `RunStepRepository.mark_step_passed(run_id, step_index, note, released_by)`:
   sets `status="APPROVED"`, `release_action="mark_passed"`, `release_note`,
   `released_by` and `released_at`, then commits.
3. Find the step's `TestResult` in the original run. Use the same
   occurrence-rank correlation `restart_run` uses: the Nth step with this
   `job_name` maps to the Nth `TestResult` with that `query_name`, ordered by
   id. Set `override_status="PASSED"`, `override_reason=note`,
   `override_by=released_by` and `override_at=now`. If there is no result row,
   skip this step; the run-step status alone drives the restart.
4. Write the audit event `run.step_marked_passed` with `run_id`, `step_id`,
   `job_name`, `previous_status` and `note`.
5. Call `restart_run(db, run_id)`. APPROVED is in `CARRIED_OVER_STATUSES`, so
   the step is carried over and not executed. `_outcome_status` seeds it as
   `PASSED`, so `all_success` children fire. The copied `TestResult` keeps the
   override fields.
6. Schedule `_execute_run` with the plan, exactly like `restart_failed_run`,
   and return its `RunStatusOut`.

If step 5 raises `RestartError`, the APPROVED mark from step 2 stays. The
operator can then use the normal Restart button, which will carry the step
over.

The original run's status (`ERROR` / `BLOCKED`) is not changed. The new run is
linked through `restarted_from_run_id`, as with a normal restart.

### Shared code

Move the occurrence-rank lookup in `restart_run` into a small helper that both
`restart_run` and the new route use, so the correlation logic lives in one
place.

### UI

In the run detail view (`frontend/features/history.js`):

- Each step row whose status is `FAILED` or `ERROR`, in a run whose status is
  not `PENDING` or `RUNNING`, shows a **"Mark passed & continue"** button.
- Clicking it opens a modal explaining that the job will not be re-run and the
  sequence continues from the next step. A note is required; the submit button
  stays disabled until the note is non-empty.
- On submit, the UI POSTs to the endpoint, shows the toast
  `"Step marked passed — continuing as run <id8>"`, and opens the new run with
  `viewRunDetail`, the same way `restartRun` does.
- On error, it shows an error toast with the response `detail`.
- `released_by` is set the same way the existing held-step release modal sets
  it.
- Steps marked this way show their `release_note` the same way released held
  steps do.

## Testing

Unit tests, run with `python -m pytest`:

- `tests/unit/test_polling.py`:
  - success after N timeouts with N < max
  - raises after `max_poll_errors` consecutive errors
  - the counter resets after a successful poll
  - a non-network exception propagates immediately
  - the deadline raises `TimeoutError`
- DS and BO clients: `wait_for_completion` returns PASSED when the first status
  call raises `requests.ReadTimeout` and the second returns a terminal status.
- `tests/unit/test_run_mark_passed.py`:
  - 409 for a running run
  - 409 for a PASSED step
  - 422 for a blank note
  - success: the original step is APPROVED with the release fields set, the
    `TestResult` has `override_status="PASSED"`, a new run is created with
    `restarted_from_run_id`, the marked step is carried over (its `run_step` is
    not called), and the downstream step executes
  - an audit row is written
- Frontend static test: the button and modal markup are present in
  `history.js` and gated on the FAILED/ERROR status.

## Docs

Add a short section to the existing run-restart help content
(`frontend/help-content.js`) describing "Mark passed & continue" and the new
`max_poll_errors` job param.
