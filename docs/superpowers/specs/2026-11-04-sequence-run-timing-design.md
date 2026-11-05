# Sequence Run Timing, Job Progress Bars and Gantt Timeline

**Date:** 2026-11-04
**Status:** Design agreed

## Goal

Three things people can't see today while a sequence runs or after it ends:

1. A colored progress bar for each job, with live runtime details and a defined progress rule. Shown in the UI and the CLI.
2. A function that gives a run's complete end-to-end elapsed time, with start and end, for each run of a sequence. Available in the UI, API and CLI.
3. A colored Gantt chart of the job sequence showing runtime, job type and status. UI only.

## What exists today

- `run_steps` records status but no start or end time. It only has `held_at` and `released_at`.
- The Monitor tab has one single-color bar per run, based on completed results ÷ total tests. Under it are two lists of the same steps.
- `GET /api/runs/{id}/stream` already reads the database every second, but only emits when the payload changes.
- `atom run` polls silently and prints one summary line at the end.
- Finding a sequence's runs means reading JSON in `test_runs.config_snapshot["sequence"]`, which can't be filtered efficiently.

## Existing bug this work has to fix

`BLOCKED` is a final run status: `RunExecutor` writes it together with `completed_at`. It's missing from `TERMINAL_STATUSES` in `etl_framework/repository/models.py`, `etl_framework/cli/app.py` and `frontend/app-config.js`, plus the fallback in `frontend/app.js`. So for a blocked run:

- `/stream` never sends `done` and keeps looping.
- `atom run` waits until `--timeout` and exits with code 6.
- `batch_launch` doesn't treat the run as finished.
- The Monitor keeps showing the run as active, with a Cancel button.

Live bars and timing would inherit all of this, so `BLOCKED` joins all four lists. `atom run` exits `EXIT_FAILED` (1) for a blocked run, since required work did not run.

## Decisions

| Topic | Decision |
|---|---|
| Surfaces | Bars and elapsed time in UI, API and CLI. Gantt in UI only |
| Running-job percent | Estimated from that job's history |
| Gantt color | Toggle: status by default, job family as the alternative. Choice remembered per browser |
| Monitor layout | Progress and Timeline tabs in each run card. Active runs open on Progress, finished runs on Timeline |
| Live updates | Server computes everything and `/stream` sends it each second. Browser and CLI only display it |
| Retries | One bar per step across all attempts, labelled "try N". No per-attempt history |

## Data

New nullable columns, added on existing databases at startup on every dialect:

- `run_steps.started_at`, `run_steps.finished_at`
- `test_runs.sequence_id`, indexed. Set in `RunRepository.create_run` from `config_snapshot["sequence"]["id"]`. Filled from existing snapshots once, at the moment the column is added.

Step timestamps are set in one place, `RunStepRepository.update_status`.

## Timing rules

| Case | Rule |
|---|---|
| Step start | First change to RUNNING sets `started_at`. Retries never reset it |
| Step end | First change away from RUNNING sets `finished_at`. Later changes (release, mark-passed) don't move it |
| Step runtime | `finished_at − started_at`. Includes `wait_seconds` and retries |
| Hold time | Separate from runtime. Runs from `held_at` to `released_at`. If the hold ended without `released_at` (timeout), it ends at the run's `completed_at`. A hold never extends past a finished run's `completed_at`; if a finished run has neither, the hold is not measured. While held in a live run, it runs to now |
| Blocked, or cancelled before start | No times and no Gantt bar. Shown as not run |
| Carried over from a restart | No times and no Gantt bar. Shown as carried over |
| Step still RUNNING in a finished run (crash) | End taken from the run's `completed_at`. Shown as interrupted |
| Runs from before this feature | Run start, end and elapsed still work. Timeline says "Step timing not recorded" |

**End-to-end time for a run:**

- `elapsed_seconds` = `completed_at − started_at`, or to now while the run is live. Time queued before start is excluded. Null if the run hasn't started, or if it's finished without `completed_at`.
- `held_seconds` = time spent on holds, with overlapping parallel holds merged.
- `active_seconds` = elapsed − held.
- `jobs_done` counts steps that finished with a result or were carried over; steps that never ran or were interrupted are left out.
- A restarted run reports its own time only.

Times stored without a timezone (SQLite) are read as UTC.

## Progress rule

One Python function decides state, percent and label for every step:

| State | When | Bar | Percent |
|---|---|---|---|
| `queued` | PENDING in a live run | Empty | 0 |
| `running` | RUNNING, runtime below estimate | Indigo, striped | `min(95, runtime ÷ estimate)` |
| `over_estimate` | RUNNING, runtime at or past estimate | Amber, striped | 95 |
| `running_unknown` | RUNNING, no estimate | Moving stripes | None |
| `held` | HELD | Violet, full | 100 |
| `done` | Finished, has timing | Full, status color | 100 |
| `not_recorded` | Finished, no timing (older run) | Full, status color | 100 |
| `not_run` | Never started, and the step or run is finished | Empty, grey | 0 |
| `carried_over` | `carried_over` is true | Full, grey | 100 |
| `interrupted` | RUNNING in a finished run | Full, grey | 100 |

**Estimate:** the step's `wait_seconds` + median `test_results.duration_seconds` from the last 10 results of that job. Counts PASSED, FAILED and SLOW with duration above 0. ERROR is left out because crashes end early and would pull the estimate down. Fewer than 3 results means no estimate. Estimates and job types are loaded once when a stream opens, not every second.

## Job families

Twenty-plus job types are too many distinct colors, so the toggle uses families. Unknown or deleted job types fall into `other`.

| Family | Job types |
|---|---|
| `reconciliation` | `reconciliation`, `api_reconciliation`, `compare`, `cross_job_assertion` |
| `file` | `file_transfer`, `file_watcher`, `s3_row_count`, `s3_format_validation`, `s3_partition_check` |
| `sap` | `bo_report`, `bo_job`, `ds_job` |
| `orchestration` | `automic_job`, `airflow_dag_run`, `aws_glue_job_run`, `aws_athena_query` |
| `quality` | `freshness`, `schema_snapshot`, `profile`, `dbt_artifact`, `aws_glue_catalog_compare` |

## API

The API returns states, statuses and family names, never colors.

- `GET /api/runs/{run_id}/timing`: one run. Returns `run_id`, `status`, `server_time`, `started_at`, `completed_at`, `elapsed_seconds`, `held_seconds`, `active_seconds`, `jobs_total`, `jobs_done`, `timing_recorded`, `steps`. Each step has `step_id`, `step_index`, `job_name`, `job_type`, `job_family`, `status`, `attempt`, `started_at`, `finished_at`, `held_at`, `hold_ended_at`, `runtime_seconds`, `estimate_seconds`, `held_seconds`, `progress` (`state`, `percent`, `label`).
- `GET /api/sequences/{sequence_id}/runs?limit=20&offset=0`: newest first. Each row has `run_id`, `status`, `started_at`, `completed_at`, `elapsed_seconds`, `held_seconds`, `active_seconds`. Limit defaults to 20, maximum 200.
- `GET /api/runs/{run_id}/stream`: gains a `timing` key with the `/timing` shape. Existing keys stay. If timing can't be built, the error is logged, `timing` is null and the stream keeps running.

## UI

**Monitor run card.** Header shows run or sequence name, status, Started, Ended, a large elapsed time and "3 of 6 jobs done". Below are Progress and Timeline tabs. These replace the single run bar and the two step lists. The Passed, Failed, Slow and Error cards, the Cancel and Release buttons and the HELD alert stay. Runs with no steps, such as test suite runs, keep the old bar.

**Progress tab.** One row per job: name and job type, colored bar, then runtime as `3m 37s / ~5m 50s`. Queued rows show "queued". Held rows keep the Release button. Failed bars are always labelled, so color is never the only signal. Stripes stop under `prefers-reduced-motion`.

**Timeline tab.** Gantt in plain HTML and CSS:

- One row per job in step order, with job name and type, bar and runtime.
- Time axis from run start to run end, or to now while live, with 4 evenly spaced clock labels.
- Parallel jobs overlap on the axis. Bars have a 2px minimum width so short jobs stay visible.
- A hatched segment after the bar shows hold time.
- Not-run and carried-over jobs show a row with no bar.
- Each bar's tooltip gives job, type, status, start, end, runtime and tries.
- "Color by: Status / Job type" toggle with a matching legend, stored in `localStorage` and working if storage is unavailable.

**History run detail.** Same Timeline in a card, using the same shared markup, plus Elapsed and Held next to Started and Completed.

**Sequences detail.** New "Recent runs" table with Started, Ended, Elapsed, Held and Status. Clicking a row opens the run in History.

**Fallback.** If `/stream` fails, the existing 5-second poll also loads `/timing`, so bars keep working less smoothly.

## CLI

| Mode | Behavior |
|---|---|
| `atom run`, interactive terminal | Live colored bars with `rich`, redrawn at least every 2 seconds whatever `--poll-interval` says |
| `atom run`, CI or piped | No bars or redraw codes. One plain line per job status change |
| `--output json` | Stdout stays one JSON document, gaining `started_at`, `completed_at`, `elapsed_seconds`, `held_seconds`, `active_seconds` |
| All text modes, on finish | Job table, then `Started / Ended / Elapsed`, then the existing summary line |

New commands: `atom timing <run_id>` and `atom sequence-runs <id or name> [--limit 20]`. `NO_COLOR` is respected. Exit codes are unchanged, apart from blocked runs.

If `/timing` is missing or fails (older server, temporary error), `atom run` carries on with the plain wait. The gate result never depends on the progress display. The CLI stays HTTP-only.

## Units

| Unit | Responsibility |
|---|---|
| `api/services/run_timing.py` | Pure `build_run_timing` and `build_run_elapsed`, plus `estimate_durations` and `load_timing_context` for database reads |
| `etl_framework/cli/progress.py` | Draws bars and status-change lines, and the wait loop |
| `frontend/features/run-timing.js` | Gantt layout, duration formatting, tab and toggle state |
| `frontend/partials/shared/run-gantt.html` | Timeline markup used by Monitor and History |

The build script gains one level of shared partial includes, because each tab partial can be included only once today and the Timeline appears in two tabs.

## Testing

- **Pure function:** every row of the timing and progress tables, with a fixed clock.
- **Repository:** timestamps at each transition, including retries and hold release. `sequence_id` set on create. Upgrade and backfill on an old-shape database.
- **Executor:** a real `DagExecutor` run on SQLite shows overlapping times for parallel steps.
- **API:** `/timing`, `/sequences/{id}/runs` paging and 404s, and `timing` in `/stream`. Blocked runs end the stream.
- **CLI:** interactive, plain and JSON modes, older-server fallback, blocked exit code, new commands.
- **Frontend:** node tests for Gantt layout and formatting, plus Playwright for tabs, toggle, Gantt, History and Recent runs.

## Out of scope

- Real internal percent from job types.
- Per-attempt history and bars.
- Job dependency arrows on the Gantt.
- Gantt in the CLI, HTML report or exports.
- Estimated finish time for the whole run.
- Filling in step times for old runs.
