# AWS S3 Tab Playwright Coverage — Design

**Date:** 2026-07-26
**Status:** Approved (design), pending implementation plan
**Author:** Principal data engineer (via brainstorming)
**Depends on:** `docs/superpowers/specs/2026-07-26-aws-s3-job-types-design.md`

## Context

The AWS S3 tracked-job implementation has backend/unit/smoke coverage, but no Playwright coverage for the dedicated AWS tab. Existing e2e S3 tests cover multi-file S3 preview and live MinIO-backed job-editor flow, not the AWS tab's ad-hoc S3 actions or new tracked-job buttons.

Docker and Compose are available locally, and `docker-compose.integration.yml` includes MinIO on `http://127.0.0.1:19000`. `playwright.config.ts` already starts the FastAPI app and, when `E2E_LIVE_BACKENDS=1`, global setup starts live services and seeds MinIO bucket `atom-e2e`.

## Goals

- Add Playwright e2e coverage for the AWS tab S3 panel.
- Exercise ad-hoc Metadata, Row Count, Partitions, and Validate Format UI actions against live MinIO when live backends are enabled.
- Exercise all three tracked-job creation buttons: row count, format validation, and partition check.
- Assert created tracked jobs can be triggered through the existing run API and reach terminal states.
- Keep the test gated behind `E2E_LIVE_BACKENDS=1` so normal e2e runs do not require Docker.

## Non-Goals

- Adding new production behavior.
- Adding new Docker services or runtime dependencies.
- Replacing existing multi-file S3 e2e tests.
- Full visual regression testing.

## Test Design

Create `tests/e2e/18-aws-s3-tab-live.spec.ts`.

The spec is skipped unless `process.env.E2E_LIVE_BACKENDS === '1'`.

Setup:

- Use `createConfig()` to create a saved config with AWS fields:
  - `aws_region: "us-east-1"`
  - `aws_access_key_id: "minioadmin"`
  - `aws_secret_access_key: "minioadmin"`
  - `aws_endpoint_url: "http://127.0.0.1:19000"`
  - `aws_verify_ssl: false`
- Use existing MinIO bucket `atom-e2e` seeded by `tests/e2e/global-setup.ts`.
- Reuse seeded object paths where possible from `tests/e2e/fixtures/data/multi_source` and the MinIO seeding convention under the `source/` prefix.

UI flow:

1. Open the app with `authedPage`.
2. Navigate to the AWS tab.
3. Select the MinIO-backed config.
4. Fill bucket `atom-e2e`, format `csv`, a seeded key such as `source/sales_apac.csv`, and prefix `source/`.
5. Click Metadata and assert the result table renders size/etag/last-modified content.
6. Click Row Count and assert a row count appears.
7. Click Partitions and assert the result area renders a partition result or a controlled no-partition response; if seeded paths are not Hive-style, the assertion should verify the UI handles the backend response without crashing.
8. Click Validate Format with a matching CSV schema and assert parsed/schema status renders.
9. Click Validate Format with an intercepted schema-drift response containing `type_mismatches`, and assert the AWS error panel renders the type mismatch details. This keeps type-mismatch UI coverage deterministic even if seeded MinIO CSV columns change.

Tracked job flow:

- Fill the job-name field before each creation to avoid name collisions.
- Click `Create Row Count Job`, `Create Format Validation Job`, and `Create Partition Check Job`.
- For each created job, use existing API helper `triggerRun()` with the saved config id and `waitForTerminal()` to assert the job reaches a terminal status.
- Expected terminal status can be `PASSED` or `FAILED` depending on deterministic inputs; it must not be `ERROR` for the happy-path row-count and format-validation jobs. Partition check may be asserted according to the seeded prefix behavior chosen during implementation.

Cleanup:

- Delete created jobs through `deleteJob()` in `afterAll`.
- Delete the saved config through `deleteConfig()` in `afterAll`.

## Validation Commands

After installing Node dependencies with `npm ci` if needed:

```powershell
$env:E2E_LIVE_BACKENDS = "1"; npx playwright test tests/e2e/18-aws-s3-tab-live.spec.ts
```

Focused non-browser checks:

```powershell
npx playwright test tests/e2e/18-aws-s3-tab-live.spec.ts --list
```

## Risks

- The isolated worktree currently lacks local Node dependencies; `npm ci` is required before Playwright can load `@playwright/test`.
- Live MinIO object shape depends on global setup seeding. The implementation must inspect seeded fixture paths and choose deterministic keys/prefixes.
- Partition discovery may require Hive-style paths; if existing seed data is not Hive-style, the e2e should either seed an additional object through existing setup patterns or assert controlled UI behavior without treating non-Hive data as a production failure.
