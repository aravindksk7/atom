# Robust difference export rebuild

## Problem

A difference export or Full HTML report rebuilds its rows by querying the sources again. Four things make that fragile:

1. One job's query failing fails the whole export, so one bad connection loses every other job's differences.
2. The browser stops waiting after 8 minutes (240 polls, 2 seconds apart), even when the export is still working.
3. An export left `PENDING` or `RUNNING` by an API restart stays that way. `create_or_reuse_export_job` keeps returning it, so that run and format can never export again.
4. Nothing tells the user that rebuilt rows come from current data. A warning is saved in `metadata_json`, and neither the page nor the file shows it.

Separately, ad-hoc SQL compare exports skip the column-case alignment and read-only check that job-run SQL compares have.

## Goals

- A failure in one job costs only that job, and the file says so.
- An export always reaches a finished state the page can see.
- While it runs, the user can see which job is being worked on and for how long.
- Rebuilt data is labelled wherever the file format allows.

## Non-goals

- Saving differences during the run. Rebuilt data can still differ from what the run saw.
- Per-job query timeouts or cancel. Stopping a running query needs driver-specific work.
- Per-job percentage bars. Most of a job's time is spent waiting on its queries, which report no progress.
- Sharing one rebuild between formats. Downloading CSV and HTML still rebuilds twice.
- Changing the CSV or Parquet columns.

## Design

### 1. Job isolation

Each job in `_write_reconciliation_run` rebuilds inside its own error handling. On an exception:

- If the job wrote no rows, export its stored rows and add an `export_incomplete` row.
- If it wrote rows first, as a later multi-file pair can, keep them and add the `export_incomplete` row without the stored rows, which would duplicate them.

The note gives the reason, with the error text cut to 500 characters.

"Live connections off" for a job that needs them follows the same path, so it stops failing the export. One-off compares have a single job and keep failing visibly.

Failed job names are saved in the export's `metadata_json` as `failed_jobs`. `create_or_reuse_export_job` does not reuse a completed export whose `failed_jobs` is not empty, so the next download retries. Jobs that can never be rebuilt, such as `bo_live`, are not listed there, so they do not force a rebuild on every click.

### 2. Liveness

New nullable `heartbeat_at` column on `difference_export_jobs`, added with the existing `ensure_column` pattern.

While an export runs, a daemon thread with its own DB session writes `heartbeat_at` every 30 seconds, and writes progress as soon as a job changes state. Its errors are logged and never fail the export.

A `PENDING` or `RUNNING` export with no heartbeat for 5 minutes is marked `FAILED` with "Export stopped unexpectedly, try again". A job that never started is timed from `created_at`. The check runs on status polls and on new export requests.

The check is not done at startup, because the README documents `--workers 4`, and one restarting worker must not kill exports running in another.

A hung query keeps its heartbeat alive, so the export stays `RUNNING`. The progress panel shows which job it is stuck on.

### 3. Progress

`metadata_json.progress` holds one entry for each job needing a rebuild, which excludes passed and errored jobs:

```json
{"jobs": [{"name": "curves", "state": "running", "rows": 0,
           "started_at": "...", "finished_at": null, "error": null}]}
```

`state` is `waiting`, `running`, `done` or `failed`. One-off runs have no job list and show only the elapsed time.

### 4. Page

The polling deadline goes. The page polls until the export completes or fails, and tolerates up to 5 poll errors in a row, so a brief API restart does not read as a failure.

History shows a panel under the export buttons while an export runs, one per format:

- An overall bar of finished jobs out of total.
- One line per job, with its state, row count, and elapsed time. The browser calculates running time from `started_at`, so it ticks between polls.

When the export finishes, the job list collapses and leaves a summary. Buttons keep a plain "Preparing..." label.

After a rebuilt download the panel shows "Rebuilt from live data at <time>; may differ from the original run". If `failed_jobs` is not empty, it also shows how many jobs failed and that clicking again retries them.

### 5. HTML report

The report shows the same rebuilt notice as a banner at the top. CSV and Parquet carry no notice. Failed jobs are visible there as `export_incomplete` rows.

### 6. One-off SQL compares

`_write_sql_compare` loads through `CompareService._load_sql_frames`, gaining column-case alignment and the read-only check.

## Testing

Unit tests:

- One job fails and the others still export, for both the no-rows and rows-written cases.
- An export with `failed_jobs` is rebuilt on the next request, and one with only unrebuildable jobs is reused.
- Live connections off produces a note, not a failed export.
- A stale export expires on poll and on a new request. An export with a fresh heartbeat does not.
- A failing heartbeat write does not break the export.
- Progress moves through its states, and lists only jobs needing a rebuild.
- A one-off Oracle-case SQL compare exports its differences, and a non-read-only query is refused.
- The HTML report holds the rebuilt notice.

Browser test: the panel shows running, done and failed jobs and then collapses, an export longer than the old limit is not reported as timed out, and the rebuilt notice and gap warning appear.

Nothing here runs against real Oracle or Postgres, so a run against real databases is the final check.
