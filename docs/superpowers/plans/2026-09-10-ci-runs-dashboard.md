# CI Runs Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only CI Runs dashboard that lists only runs carrying `ci_context`, exposes commit/pipeline/ref and target metadata, and summarizes recent CI outcomes.

**Architecture:** Extend the existing run repository and `/api/runs` projection rather than adding storage or duplicate list logic. Add a focused `CiRunsReportingService` for aggregate queries, then compose a new Alpine feature slice and partial into the existing frontend shell while reusing History's run-detail state and methods.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, pytest, Alpine.js 3, Playwright, Node HTML build script.

## Global Constraints

- This feature is additive and read-only; do not change launch endpoints, CLI commands, CI scripts, database columns, or database tables.
- A run is CI-triggered if and only if `TestRun.ci_context IS NOT NULL`.
- `days` defaults to `30` and is constrained to `1..365`.
- The dashboard includes list and summary views only; grid, timeline, export, prune, and a new run-detail UI are out of scope.
- Missing target metadata, pipeline URL, or ref renders as an em dash and must not break the view.
- Selection names must be fetched in one batched query per run-list page, never once per row.
- Pipeline links render only for safe absolute `http:` or `https:` URLs and open in a new tab with `rel="noopener noreferrer"`.
- Do not add dependencies.

## Tasks

1. Add `RunRepository.list_runs(ci_only=False)` and `JobSelectionRepository.names_by_ids(ids)` with repository tests.
2. Extend `RunStatusOut` and `/api/runs` with `ci_context`, resolved target metadata, and one batched selection-name lookup per page.
3. Add `CiRunsReportingService`, `CiRunsFilters`, and `GET /api/runs/ci-summary` with aggregation/filter/validation tests.
4. Add the CI Runs Alpine feature slice, partial, app registration, generated HTML, and frontend smoke coverage.
5. Add live-backend Playwright coverage for a CI selection run versus a manual run and History detail reuse.
6. Run focused/full tests, HTML build, Python compile, Playwright discovery, and diff checks.

## Verification

- `python -m pytest tests/unit/test_job_selections_repository.py tests/unit/test_ci_runs_reporting_service.py tests/unit/test_api.py tests/integration/test_api_frontend_smoke.py -q`
- `npm run build:html`
- `npx playwright test tests/e2e/46-ci-runs-dashboard.spec.ts --project=chromium`
- `python -m pytest -q`
- `python -m compileall api etl_framework`
- `npx playwright test --list`
- `git diff --check`
