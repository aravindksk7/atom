# E2E Compare Scenarios on the Polars Backend

Date: 2026-08-30

## Problem

The Playwright suite has 17 spec files that exercise the Compare feature, but only
`tests/e2e/08a-compare-bo-report.spec.ts:35` ever selects the Polars comparison
backend. Every other compare scenario runs on the `pandas` default declared in
`frontend/features/compare.js:150,161,172` and `api/schemas.py:82,1111`.

There is no supported way to run the whole compare surface against
`PolarsBackend`, so a Polars regression that only appears in reconciliation,
SQL, column stats, mismatch diff, multi-file, matrix, or saved-job paths would go
undetected.

## Goal

Run every compare scenario against the Polars backend, against the live Docker
integration stack, without permanently changing what those tests assert and
without altering application code.

## Approach

Add an opt-in request interceptor to the shared Playwright fixture.

When `E2E_COMPARE_BACKEND` is set, `tests/e2e/fixtures.ts` installs a
`page.route()` handler on `**/api/**`. For any request carrying a JSON body, the
handler rewrites `advanced.comparison_backend` and
`run_settings.comparison_backend` to the configured value before the request
reaches FastAPI, then continues the request with the patched body.

When the variable is unset the handler is not installed at all, so existing runs
behave exactly as they do today.

### Why the request boundary rather than the UI

Three properties of the suite rule out driving the Advanced Options select in
each spec:

1. Only 08a opens the Advanced Options accordion. The other 16 compare specs
   never render it, so `fillAdvancedOptions` has nothing to act on there.
2. Several scenarios run a compare through a *saved job*, where the backend
   travels as `run_settings.comparison_backend` (`api/services/run_executor.py:2022`)
   and there is no per-run select in the compare tab at all.
3. Matrix and multi-file endpoints (`api/routes/compare.py:230,434`) build their
   payload server-side from the request snapshot, so the only common choke point
   across all of them is the HTTP request itself.

Interception covers every path with no edits to the 17 spec files, no change to
`frontend/`, and no change to `api/`.

### Interaction with existing options

`tests/e2e/compare-helpers.ts` keeps its `backend?: 'pandas' | 'polars'` option
unchanged. The interceptor runs after the UI has serialised the payload, so when
both are present the override wins. 08a's explicit `backend: 'polars'` therefore
stays consistent under the override rather than conflicting with it.

## Components

| Component | Change |
|---|---|
| `tests/e2e/fixtures.ts` | Install the conditional `page.route` override in the `authedPage` fixture; count patched requests. |
| `tests/e2e/compare-helpers.ts` | Unchanged. |
| compare spec files | Unchanged. |
| `frontend/`, `api/` | Unchanged. |

## Data flow

```
spec -> authedPage fixture -> browser builds compare payload
     -> page.route handler patches comparison_backend
     -> POST /api/compare/*  -> compare_service._build_engine
     -> PolarsBackend (api/services/compare_service.py:215)
```

## Error handling

- A request whose body is absent or not valid JSON is forwarded untouched.
- The handler never invents the `advanced` or `run_settings` objects; it only
  rewrites the key when the object already exists. This keeps request schemas
  valid for endpoints that do not accept advanced options.

## Testing and verification

Run with the live Docker stack:

```
$env:E2E_LIVE_BACKENDS='1'
$env:E2E_COMPARE_BACKEND='polars'
npx playwright test <compare specs>
```

`E2E_LIVE_BACKENDS=1` triggers `tests/e2e/global-setup.ts:12`, which brings up
`docker-compose.integration.yml` (sapds, sftp, sqlserver, gitlab, localstack,
minio, sapbo) and seeds SQL Server and MinIO.

The run is only meaningful if the override actually fired, so the interceptor
tracks how many requests it patched. A run where the count is zero is reported as
a failure to exercise Polars rather than as a pass, preventing a silent no-op
(for example a wrong key path) from masquerading as green.

## Out of scope

- Making Polars the default backend for the application.
- Adding Polars coverage to the pytest unit/integration suites; `tests/unit/test_polars_backend.py`
  and `tests/unit/test_run_executor.py:170` already cover that layer.
