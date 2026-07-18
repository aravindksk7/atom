# Job Design, Scheduling, and Automation Help Update Design

## Goal

Update both the in-app Help Center and README so users can understand the full job lifecycle: design a job, model its inputs and rules, save it, execute it immediately, schedule it for recurring execution, run and schedule it through the API, integrate execution with external pytest frameworks, and gate CI/CD pipelines on results.

## Scope

- Add a task-oriented Help Center section in `frontend/help-content.js`.
- Update `README.md` with an end-to-end workflow guide and copy/paste-ready examples.
- Cover UI, API, pytest integration, and CI/CD pipeline usage in one coherent flow.
- Keep this as a documentation/help update only; no runtime behavior changes.
- Leave `frontend/partials/tab-help.html` unchanged unless the current renderer cannot display the new help content.

## Existing Context

The application is a FastAPI and Alpine.js ETL Test Framework. The Help tab renders structured content from `frontend/help-content.js` through `frontend/partials/tab-help.html`. README already contains detailed sections for job launching, API usage, schedule management, pytest suite runner, CI/CD status, and gate exit codes. Recent commits added scheduler statistics and reporting, so this update should align with the existing scheduler and automation documentation rather than introduce a separate documentation model.

## Recommended Approach

Use a full end-to-end workflow guide as the primary shape. This is the best fit because the user request describes a lifecycle that crosses UI, API, external pytest, and CI/CD boundaries. A workflow guide avoids scattering related steps across unrelated sections and makes it clear that saved jobs and schedules can be executed from every supported surface.

Alternatives considered:

- Separate UI/API/pytest/CI reference sections: easier to maintain as isolated topics, but less useful for users trying to complete the full lifecycle.
- Minimal additions to existing sections: lowest edit size, but likely leaves users without a clear model for how the pieces connect.

## Help Center Design

Add a new section to `frontend/help-content.js` titled `Job Design, Scheduling & Automation` or similar. The section should use concise Help tab steps:

- Design the job in the Launch tab by selecting job type, source/target connection or API endpoint, SQL/query/file/artifact parameters, key columns, thresholds, rules, and dependencies.
- Save the job to the catalog so the same definition can be reused by UI launches, schedules, API calls, pytest, and CI/CD.
- Execute the saved job immediately from the UI and monitor queued/running/passed/failed/skipped states.
- Create a schedule from the UI with a cron expression, timezone-aware execution, enabled/disabled state, and job sequence.
- Use API endpoints to create/update jobs, trigger runs, create schedules, inspect run status, and evaluate gates.
- Integrate external pytest by calling the API from fixtures/tests, polling run completion or using gate evaluation, and asserting pass/fail.
- Integrate CI/CD by starting a run, waiting for completion, using CLI gate exit codes or API gate endpoints, and publishing reports or scheduler stats as artifacts.

The existing help renderer supports `title`, `text`, `where`, `tip`, and `warn`, which is enough for this content. No visual companion or HTML template change is needed.

## README Design

Add a durable guide section near existing job launcher / scheduler / API documentation and link it from the table of contents. The guide should include:

- Conceptual model: a job is the reusable test definition; a run is an execution; a schedule is a recurring trigger for saved jobs or job sequences; pytest and CI/CD should call the same API/CLI surfaces used by the UI.
- UI workflow: create/model job, save, run now, schedule, monitor History/Reports/Logs.
- API workflow: authenticate, create/update a job, trigger execution, create a schedule, poll run status, evaluate a gate.
- Pytest integration: example fixture or test function that calls the running FastAPI service, triggers a saved job or test suite, waits for terminal status, and asserts success.
- CI/CD integration: example pipeline stage that starts or references a run and gates the pipeline using `python -m etl_framework.runner.cli --gate-run <run_id>`; include scheduler stats gate commands for recurring-job health.
- Notes and cautions: use scoped tokens, prefer saved jobs over ad hoc definitions in automation, keep secrets in CI variables, make scheduled jobs idempotent, and publish reports/logs as artifacts.

Examples should use placeholders like `<token>`, `<job-name>`, and `<run_id>` and should avoid implying new endpoints unless confirmed in the existing code/docs.

## Validation Design

- Parse-check the edited JavaScript with Node if available.
- Run targeted help tests when practical, especially `tests/e2e/11-help.spec.ts` if the project dependencies and browser setup are available.
- If full e2e execution is not practical, verify that the new Help section follows the existing `window.ETL_HELP.sections[]` structure and that README examples use existing documented endpoints or CLI commands.

## Out of Scope

- Adding new API endpoints, UI controls, scheduler behavior, or pytest runner functionality.
- Redesigning the Help tab layout or search behavior.
- Changing authentication, token scopes, CI status injection, or report generation.
- Creating a separate documentation site.
