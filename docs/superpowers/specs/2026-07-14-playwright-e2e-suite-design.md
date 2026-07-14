# Playwright E2E Suite — Design

**Date:** 2026-07-14
**Status:** Approved

## Purpose

The frontend (`frontend/index.html` + `frontend/app.js`, ~9,600 lines, Alpine.js SPA over a FastAPI backend) has no browser-driven end-to-end coverage today — only Python-level API/integration tests (`tests/integration/test_api_frontend_smoke.py` etc.) and unit tests. This adds a comprehensive Playwright suite covering every tab's core functionality plus negative/edge cases, so UI regressions are caught before manual QA.

## App shape (as discovered)

- Single-page app, tab-switching via Alpine `currentView`, tabs (`app.js` ~line 104): `config`, `jobs` (labelled "Launch"), `monitor`, `history`, `adapters`, `reports`, `differences`, `compare`, `contracts`, `logs`, `help`.
- Auth is bearer-token based, not a username/password form: an admin token is created once (bootstrap, when `GET /api/auth/setup-status` reports `initialized: false`), then subsequent sessions paste/store the raw token in `sessionStorage['etl_token']`. `require_admin` gates admin-only routes (403 for non-admin tokens); missing/invalid tokens get 401.
- Backend entrypoint: `python -m uvicorn api.main:app --host 127.0.0.1 --port 8000`.
- DB is swappable via `ETL_DATABASE_URL` env var (`etl_framework/repository/database.py`), defaulting to a local sqlite file — this lets tests run against a throwaway DB without touching `etl_framework.db`.

## Architecture

- Add `@playwright/test` as a devDependency; add `playwright.config.ts` at repo root with `testDir: './tests/e2e'`.
- `webServer` block starts uvicorn with `ETL_DATABASE_URL` pointed at a temp sqlite file (e.g. `os.tmpdir()/atom-e2e/<run-id>.db`); a `globalSetup` script deletes/recreates that path before the run starts, so every full run begins from an empty DB (schema created by the app's own startup migrations).
- **Isolation model**: one throwaway DB is shared by the whole Playwright run (restarting uvicorn per spec file is impractical — slow, and reruns startup migrations each time). Each spec file namespaces the data it creates (job/adapter/contract names prefixed `e2e-<spec-slug>-<timestamp>`) and cleans up what it created in `test.afterAll` via direct API calls. This keeps specs independent and rerunnable without polluting real data or depending on tab execution order.
- Tests run against real backend logic (no request mocking) — this is an integration-style E2E suite, matching how `test_api_frontend_smoke.py` already validates the served HTML.

## Spec files

One file per tab/concern; each is an independently completable, parallelizable unit.

| File | Covers | Negative / edge cases |
|---|---|---|
| `00-auth-setup.spec.ts` | Bootstrap first admin token, paste/connect token, disconnect | Malformed token, revoked/wrong token (401), non-admin token on admin-only action (403), unauthenticated API call |
| `01-config.spec.ts` | Validate Configuration, Run Health Check, DB password field | Bad DB credentials, missing required fields |
| `02-launch-jobs.spec.ts` | Add Job wizard (basic/schema/execution sub-tabs), job CRUD, Execution Sequence, Comparison Backend, Pass-with-actions | Missing required fields, duplicate job name, invalid schema JSON |
| `03-monitor.spec.ts` | Trigger run, live status polling, cancel run | Trigger with nothing selected, cancel an already-finished run |
| `04-history.spec.ts` | Run history list, `historySubTab` filters, pagination | Filter combination yielding empty state |
| `05-adapters.spec.ts` | Adapter CRUD, connectivity test | Bad credentials, unreachable host |
| `06-reports.spec.ts` | View/download report, rejected-mismatches HTML report | Report request for a nonexistent run |
| `07-differences.spec.ts` | Differences Explorer search/pagination/insights, bulk-decide bar + modal, mismatch drawer accept/reject | Bulk-decide with zero rows selected, missing decision reason |
| `08-compare.spec.ts` | `compareSubTab` mmdiff comparison flow | Mismatched/invalid file inputs |
| `09-contracts.spec.ts` | Contract CRUD | Validation errors on save |
| `10-logs.spec.ts` | Global Logs tab auto-refresh, search/level filter | — |
| `11-help.spec.ts` | Help tab renders | — |
| `12-cross-cutting.spec.ts` | Offline indicator (`apiOk`), unknown route/deep-link, XSS-safe rendering of user-entered text (job names, mismatch reasons), session expiry mid-action | — |

## Shared fixtures

- `tests/e2e/fixtures.ts`: a Playwright fixture that bootstraps (or reuses, if already initialized) an admin token once per worker and injects it into `sessionStorage` before each test, so most specs start already authenticated. `00-auth-setup.spec.ts` is the exception — it explicitly drives the unauthenticated/bootstrap flow itself.
- `tests/e2e/api-helpers.ts`: thin wrappers over the backend REST API (using the bootstrapped token) for setup/teardown data a spec needs but isn't itself testing (e.g. `02-launch-jobs` needing a pre-existing adapter to attach a job to).

## Error handling / non-goals

- No mocking of network responses — failures are induced by real invalid input (bad credentials, malformed JSON) rather than intercepted routes, keeping the suite honest about actual backend behavior.
- Not covering: cross-browser matrix (Chromium only), visual/screenshot regression, load/performance testing, mobile viewport — out of scope for this pass.

## Testing strategy for the suite itself

- `npx playwright test` runs the full suite headless in CI; `--ui`/`--headed` for local debugging.
- Each spec file's `afterAll` deletes the jobs/adapters/contracts/tokens it created, verified by re-fetching the relevant list endpoint and asserting the namespaced items are gone — so a partial run failure doesn't leave orphaned state for the next run.
