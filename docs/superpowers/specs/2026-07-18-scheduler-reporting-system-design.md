# Scheduler Reporting System Design

Date: 2026-07-18
Status: Approved for implementation planning

## Purpose

Add a Scheduler Reporting System that gives users two complementary ways to monitor, analyze, and manage scheduled jobs:

- A feature-rich visual dashboard for live status, timelines, analytics, exports, and management actions.
- A low-overhead command-line reporting interface for quick summaries, filters, and machine-readable exports without starting the web server.

The reporting system must share one underlying reporting engine across UI, API, and CLI surfaces. Existing core scheduling behavior must remain unchanged except for narrow, best-effort telemetry listener calls.

## Scope

This release targets the full feature set:

- Dedicated Scheduler Reports dashboard tab.
- Live status grid for active, completed, and failed scheduled jobs.
- Gantt-style historical execution timeline.
- Visual analytics for success rate, runtime trends, and database/reporting performance metrics.
- Responsive layout for desktop and mobile.
- CLI command surface for summaries, filtering, and JSON/CSV/text output.
- Shared reporting service consumed by both API/UI and CLI.
- Best-effort database telemetry hooks with 30-day retention.
- Full schedule management actions from the dashboard: enable/disable, edit cron, delete, and run-now.

Out of scope:

- Rewriting APScheduler orchestration.
- Changing reconciliation execution semantics.
- Replacing existing schedule creation/editing APIs.
- Long-term warehouse-style historical analytics beyond the 30-day telemetry window.

## Recommended Architecture

Use a telemetry-backed shared reporting engine.

### Components

1. **Telemetry model and repository**
   - Add scheduler reporting tables for execution events and optional aggregate/query timing metadata.
   - Store schedule id, schedule name, job or selection identity, run id when available, event state, timestamps, duration, normalized status, exit code, error summary, and metadata JSON.
   - Keep telemetry separate from `ScheduledRun` and `TestRun` so reporting changes do not alter scheduler semantics.

2. **Telemetry listener layer**
   - Add narrow listener calls around scheduled execution start and terminal outcomes.
   - Capture events such as `queued`, `started`, `completed`, `failed`, `cancelled`, and `missed` where the scheduler can observe them.
   - Listener failures are logged and swallowed. Telemetry must never fail, retry, skip, or otherwise change a scheduled job.

3. **SchedulerReportingService**
   - Aggregate telemetry with existing `ScheduledRun`, `TestRun`, and `get_scheduler_runtime_snapshot()` data.
   - Provide normalized methods for summary, grid rows, timeline segments, metric series, exports, and pruning.
   - Measure reporting query durations and include them in database/reporting performance metrics.

4. **Reporting API routes**
   - Add API endpoints under the existing FastAPI structure for scheduler reports.
   - Support shared filters: date range, schedule id, job name, status, and exit code.
   - Return consistent payloads for dashboard cards, live grid, timeline, analytics, and export downloads.

5. **Dashboard UI**
   - Add a dedicated top-level Scheduler Reports tab.
   - Reuse existing Alpine.js and frontend module patterns.
   - Poll lightweight summary/grid endpoints for near-real-time status.
   - Fetch heavier timeline and analytics data when filters change or the user opens those sections.

6. **CLI command surface**
   - Add a scheduler reporting command path such as `scheduler report --summary` through the project CLI entry points.
   - Use the same reporting service through a local database session factory.
   - Support text summaries and `--format json|csv` exports without launching the web server.

7. **Schedule management integration**
   - Dashboard management actions reuse existing schedule repository/API validation paths.
   - Run-now, enable/disable, cron edit, and delete operations must retain audit logging and current validation behavior.

## Data Flow

1. A scheduled job is triggered by existing scheduler orchestration.
2. A best-effort telemetry listener records a `started` event with schedule/job identity and timestamp.
3. Existing run orchestration proceeds normally.
4. On terminal outcome, a best-effort listener records status, duration, exit code if available, run id if available, and any error summary.
5. Reporting API and CLI requests pass filters to `SchedulerReportingService`.
6. The service queries telemetry, joins or correlates with existing schedule/run records, computes summaries and timelines, and records reporting query timing.
7. The dashboard displays summary cards, live grid rows, Gantt timeline segments, analytics charts, export controls, and management actions.
8. The CLI prints text summaries or emits JSON/CSV data directly.
9. A retention pruning function removes telemetry older than 30 days.

## Dashboard Design

The dashboard lives in a new dedicated Scheduler Reports tab.

### Desktop Layout

- Top summary cards:
  - Scheduler availability/running state.
  - Success rate for selected window.
  - Failed/error/cancelled count.
  - Runtime p50/p95 or average duration.
  - Reporting database/query timing indicator.
- Live status grid:
  - Schedule name, enabled state, cron expression, next run, last run, last outcome, current state, duration, exit code, and actions.
  - Inline management actions for enable/disable, run-now, edit cron, and delete.
- Interactive timeline:
  - Gantt-style rows by schedule/job.
  - Bars show execution start/end and status coloring.
  - Tooltips show duration, run id, status, and error summary.
- Analytics panel:
  - Success-rate trend.
  - Runtime trend and p95 duration.
  - Outcome breakdown.
  - Reporting query/database timing.
- Filters and exports:
  - Date range, schedule/job name, status, and exit code.
  - Export current filtered data as JSON or CSV.

### Mobile Layout

- Summary cards collapse to a two-column or single-column stack.
- Grid becomes card rows with primary status first and actions behind compact controls.
- Timeline supports horizontal scrolling while preserving readable labels.
- Filters collapse into a panel.

## CLI Design

The CLI provides a low-overhead reporting interface independent of the web server.

Expected command capabilities:

```powershell
scheduler report --summary
scheduler report --from 2026-07-01 --to 2026-07-18 --job daily_load
scheduler report --status failed --exit-code 1 --format json
scheduler report --from 2026-07-01 --format csv --output scheduler-report.csv
```

If the repository keeps reporting under the existing Python module CLI instead of a separate executable, equivalent arguments can be exposed through `python -m etl_framework.runner.cli` while preserving the user-facing behavior documented above.

### CLI Output

- `text`: concise health summary with counts, success rate, duration metrics, and recent failures.
- `json`: structured object containing filters, summary, rows, timelines, metrics, and warnings.
- `csv`: flat row export suitable for spreadsheets and automation.

### CLI Exit Behavior

- Reporting commands return `0` when data is queried successfully, even if jobs failed.
- Invalid arguments return argparse validation errors.
- Database/reporting failures return non-zero and print a clear error.
- Future CI gate behavior can be layered on top but is not part of the basic report command semantics.

## API Design

Add endpoints that map directly to shared service methods. Exact route names can follow existing project conventions, but the functional surface should include:

- `GET /api/scheduler-reports/summary`
- `GET /api/scheduler-reports/grid`
- `GET /api/scheduler-reports/timeline`
- `GET /api/scheduler-reports/metrics`
- `GET /api/scheduler-reports/export?format=json|csv`
- `POST /api/scheduler-reports/prune` or an internal retention utility invoked from startup/maintenance paths

All read endpoints accept shared filters:

- `from` / `to` date range.
- `days` convenience window, defaulting to a recent short window.
- `schedule_id`.
- `job` or schedule name substring.
- `status`.
- `exit_code`.

Responses include a `warnings` array when runtime scheduler state or telemetry data is incomplete.

## Error Handling And Safety

- Telemetry capture is best-effort and isolated from scheduler execution.
- Listener exceptions are logged with context and swallowed.
- Reporting endpoints degrade gracefully if telemetry is empty, returning schedule/runtime data and warnings.
- Management actions use existing validation and audit logging.
- Delete/edit actions require confirmation in the UI to reduce accidental destructive operations.
- API validation rejects invalid date ranges, unsupported formats, invalid exit codes, and malformed cron expressions.
- Reporting queries use bounded date windows to avoid expensive unbounded scans.
- Retention pruning deletes only telemetry older than 30 days and never deletes `ScheduledRun` or `TestRun` records.

## Testing Strategy

### Unit Tests

- Telemetry repository create/list/filter/prune behavior.
- Telemetry listener best-effort failure isolation.
- Reporting aggregation for summaries, grid rows, timelines, metrics, and warnings.
- Filter behavior for date range, job name, status, and exit code.
- JSON and CSV export formatting.
- CLI summary, filters, output formats, argument validation, and error handling.

### API Tests

- Scheduler report endpoints return expected payload shapes.
- Filters produce consistent API results.
- Export endpoints return correct media/content.
- Management actions route through existing schedule behavior and audit logging.

### Frontend Tests

- Dedicated tab loads and renders summary cards, status grid, timeline, analytics, filters, exports, and actions.
- Dashboard handles empty telemetry and partial-warning states.
- Responsive layout remains usable on mobile widths.
- Management action confirmations and success/error states are visible.

### Regression Tests

- Existing scheduler run orchestration tests continue to pass.
- Existing schedule create/update/delete/run-now behavior is unchanged.
- Telemetry listener failures do not fail scheduled runs.

## Implementation Constraints

- Do not rewrite core scheduler orchestration.
- Keep telemetry hooks narrow and non-blocking from a scheduling semantics perspective.
- Reuse existing database/session/repository conventions.
- Reuse existing frontend module and partial conventions.
- Keep the reporting service as the single source of aggregation logic for API, UI, and CLI.
- Default telemetry retention is 30 days.

## Open Decisions Resolved

- Release scope: full feature set.
- Dashboard location: new dedicated Scheduler Reports tab.
- Dashboard management level: full management actions.
- Telemetry retention: short 30-day window.
- Architecture: telemetry-backed shared reporting engine.
