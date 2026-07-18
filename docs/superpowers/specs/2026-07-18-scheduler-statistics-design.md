# Scheduler Statistics Report Design

## Goal

Add scheduler statistics reporting across the web UI, HTTP API, command line, and CI/CD pipelines. The report covers both schedule execution health and live scheduler process health so users can see whether schedules are registered, running, succeeding, and safe to gate on in automation.

## Scope

- Add a reusable scheduler statistics service that is shared by API and CLI entry points.
- Add `GET /api/schedules/stats?days=30` for UI, scripts, and CI artifact collection.
- Add scheduler stats controls and summary cards to the existing Schedules web UI.
- Add CLI report and optional gate flags for CI/CD pipelines.
- Use a default execution-history window of 30 days.
- Avoid adding a new scheduler event table in this first version.

## Existing Context

The application is a FastAPI and Alpine.js ETL reconciliation dashboard. Schedule CRUD already lives in `api/routes/schedules.py`, runtime scheduling lives in `api/services/scheduler.py`, schedule UI behavior lives in `frontend/features/launch.js` and `frontend/index.html`, and CLI behavior lives in `etl_framework/runner/cli.py`. Existing tests include `tests/unit/test_scheduler.py` for schedule repository and scheduler service behavior.

## Recommended Approach

Create a dedicated scheduler stats service rather than computing statistics inline in routes. This keeps aggregation logic reusable and testable, gives API/UI/CLI one source of truth, and avoids a persistence migration until event-level observability is needed.

Alternatives considered:

- Inline API route aggregation: less code initially, but harder to share with CLI and easier to make `api/routes/schedules.py` too broad.
- New scheduler event table: best long-term audit trail, but unnecessary for the first report because run history plus live APScheduler state covers the requested output.

## Service Design

Add `api/services/scheduler_stats.py` with a public function similar to:

```python
def build_scheduler_stats(db, days=30, now=None, gate_options=None) -> dict:
    ...
```

Responsibilities:

- Validate or receive a validated `days` window, defaulting to 30.
- Read all schedules from the database.
- Aggregate recent run outcomes for scheduled executions in the selected window.
- Read live scheduler state from `api/services/scheduler.py` through read-only helper functions.
- Return a serializable payload that includes aggregate summary, per-schedule details, live process status, and optional gate evaluation.

Add read-only helpers in `api/services/scheduler.py`, for example:

```python
def get_scheduler_runtime_snapshot() -> dict:
    ...
```

The snapshot should include whether APScheduler is importable, whether the in-process scheduler is running, job count, scheduler timezone, and per-job next run times keyed by schedule id where possible.

## API Design

Add `GET /api/schedules/stats?days=30` in `api/routes/schedules.py`. Define this route before `/{schedule_id}` routes so the literal `stats` path cannot be captured as an id.

`days` validation:

- Default: `30`.
- Accepted range: `1..365`.
- Invalid values return FastAPI validation errors.

Example response:

```json
{
  "window_days": 30,
  "generated_at": "2026-07-18T03:29:00Z",
  "scheduler": {
    "available": true,
    "running": true,
    "job_count": 4,
    "timezone": "UTC"
  },
  "summary": {
    "total_schedules": 5,
    "enabled_schedules": 4,
    "disabled_schedules": 1,
    "runs_triggered": 42,
    "passed": 36,
    "failed": 4,
    "error": 1,
    "cancelled": 1,
    "blocked": 0,
    "success_rate": 85.71,
    "average_duration_seconds": 128.4
  },
  "schedules": [
    {
      "id": 1,
      "name": "nightly-recon",
      "enabled": true,
      "cron_expr": "0 6 * * *",
      "registered": true,
      "next_run_at": "2026-07-19T06:00:00Z",
      "last_run_at": "2026-07-18T06:00:00Z",
      "last_status": "PASSED",
      "runs_triggered": 30,
      "passed": 28,
      "failed": 2,
      "error": 0,
      "cancelled": 0,
      "blocked": 0,
      "success_rate": 93.33,
      "average_duration_seconds": 91.2
    }
  ],
  "gate": {
    "status": "passed",
    "exit_code": 0,
    "reasons": []
  }
}
```

The endpoint is read-only and uses the existing router/middleware authentication behavior.

## CLI and CI/CD Design

Extend `etl_framework/runner/cli.py` with scheduler statistics flags:

```powershell
python -m etl_framework.runner.cli --scheduler-stats --output json
python -m etl_framework.runner.cli --scheduler-stats --days 30 --output text
python -m etl_framework.runner.cli --scheduler-stats --fail-on-stopped --min-success-rate 95 --output json
```

Behavior:

- Default report-only mode exits `0` when stats are computed successfully.
- `--fail-on-stopped` exits non-zero if the scheduler process is unavailable or not running.
- `--min-success-rate N` exits non-zero if aggregate success rate is below `N`.
- JSON output includes `gate.status`, `gate.exit_code`, and `gate.reasons`.
- Text output prints a concise summary suitable for logs.
- Database/API access failures exit non-zero and emit an error in the selected output format.

## Web UI Design

Add a Scheduler Statistics area at the top of the existing Schedules sub-tab.

Frontend behavior:

- Add state for `schedulerStats`, `schedulerStatsLoading`, and `schedulerStatsError` in `frontend/features/launch.js`.
- Load stats from `GET /api/schedules/stats?days=30` when the Schedules tab opens.
- Refresh stats after schedule create, update, delete, and manual run trigger.
- Keep the existing Tailwind/Alpine visual language.

UI content:

- Aggregate cards for scheduler state, enabled schedules, runs in the last 30 days, success rate, and average duration.
- Per-schedule indicators for next run time, last status, last run time, 30-day success rate, and recent outcome counts.
- Clear fallback text for no recent runs or unavailable scheduler runtime state.

## Error Handling

- If APScheduler is not installed, return `scheduler.available=false`, `scheduler.running=false`, and still report database-backed execution stats.
- If the scheduler service is not started, return `available=true`, `running=false`, `job_count=0`, and still report database-backed execution stats.
- If a schedule has no runs in the selected window, return zero counts, `success_rate=null`, and `average_duration_seconds=null`.
- If an enabled schedule is missing a live scheduler job, return `registered=false` so the UI can highlight a registration issue.
- Ignore missing durations in averages and return `null` when no completed run has usable duration data.

## Testing Plan

- Unit-test the stats service with in-memory SQLite for empty data, enabled/disabled schedules, mixed statuses, no recent runs, runtime snapshots, and gate decisions.
- Unit-test `GET /api/schedules/stats` for validation and payload shape.
- Unit-test CLI report-only behavior and gated exit behavior.
- Verify the Schedules UI loads stats and refreshes after schedule mutations.

## Out of Scope

- Persisting scheduler process events or misfire history in a new table.
- Alerting or notification rules based on scheduler statistics.
- New visual design system or separate statistics page.
- Historical charting beyond the current summary and per-schedule report.
