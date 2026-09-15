# Floci AWS Emulator Swap + Live Glue/Athena Test Coverage — Design

**Date:** 2026-09-15
**Status:** Approved (design), pending implementation plan
**Author:** Principal data engineer (via brainstorming)
**Related:** `docker-compose.integration.yml`, `tests/e2e/global-setup.ts`, `docs/superpowers/specs/2026-07-26-aws-glue-catalog-compare-design.md`, `docs/superpowers/specs/2026-07-26-aws-athena-query-runner-design.md`, `docs/superpowers/specs/2026-09-05-aws-glue-spark-jobs-design.md`

## Context

`docker-compose.integration.yml` currently runs `localstack/localstack:3` (pinned to the last free-tier-capable image) with `SERVICES=s3,glue,athena`. `tests/e2e/global-setup.ts`'s `seedGlueAthena()` documents a **known gap**: LocalStack Community does not implement the Glue or Athena APIs at all — both require a paid `LOCALSTACK_AUTH_TOKEN`. The seed step is written to fail and is caught/logged rather than thrown, specifically because of this.

As a result, every AWS Glue and Athena test today (`19-aws-glue-tab.spec.ts`, `20-aws-athena-tab.spec.ts`, `38-live-docker-aws-compare.spec.ts`) runs entirely against `page.route()` mocks — no test ever exercises a real Glue Catalog or a real Athena query execution. S3 is the exception: `18-aws-s3-tab-live.spec.ts` already runs live against a MinIO container and is not affected by this gap.

[floci](https://github.com/floci-io/floci) is a free, MIT-licensed AWS local emulator that implements Glue, Athena, and S3 (among 60+ services) without requiring an auth token, with a smaller footprint and faster startup than LocalStack. Swapping it in closes the documented gap and lets Glue/Athena tests assert against a real backend for the first time.

The app's AWS client plumbing already supports this with no product code changes: `AWSConfig.endpoint_url` (`etl_framework/aws/config.py`) flows through `AWSSession` into every service client (S3, Glue, Athena, Airflow uses its own `aws_endpoint_url` field) via each service's `*Runtime.client()`. This is the exact mechanism `18-aws-s3-tab-live.spec.ts` already uses against MinIO.

## Goals

- Replace the `localstack` service in `docker-compose.integration.yml` with a `floci` service, closing the documented Glue/Athena gap.
- Rewrite `seedGlueAthena()` in `tests/e2e/global-setup.ts` to seed real Glue Catalog data (a matched table pair and a deliberately mismatched pair) and fail hard (not warn-and-continue) if seeding fails.
- Add `19-aws-glue-tab-live.spec.ts`: live Glue Catalog compare (match + mismatch: missing/extra columns, type mismatch, partition mismatch, location mismatch) and tracked-job execution against the real backend.
- Add `20-aws-athena-tab-live.spec.ts`: live Athena query execution (happy path, row-count assertion breach, empty result, malformed SQL) and tracked-job execution against the real backend.
- Leave existing mocked `19-aws-glue-tab.spec.ts` / `20-aws-athena-tab.spec.ts` untouched as fast, non-live payload/UI-shape checks.

## Non-Goals

- No new AWS services or product surface beyond what the AWS tab already exposes (S3, Glue, Athena, Airflow) — confirmed with the user as out of scope.
- No changes to S3/MinIO live coverage (`18-aws-s3-tab-live.spec.ts`) — already real, no gap to close.
- No changes to `etl_framework/aws/config.py`, `AWSSession`, or any `*Runtime` client — the existing `endpoint_url` plumbing is sufficient and already proven by the S3 live spec.

## Architecture & Components

1. **`docker-compose.integration.yml`**: replace the `localstack` service block with:
   ```yaml
   floci:
     image: floci/floci:latest
     container_name: atom-floci-integration
     ports:
       - "4566:4566"
     environment:
       - AWS_DEFAULT_REGION=us-east-1
   ```
   No `LOCALSTACK_AUTH_TOKEN` (not needed). No Docker healthcheck, matching the existing `minio` service's pattern in this file — readiness is gated by the seed script's own retry loop, not a compose-level probe.

2. **`tests/e2e/global-setup.ts`**: rewrite `seedGlueAthena()`:
   - Keep endpoint `http://127.0.0.1:4566`, dummy creds `test`/`test` (floci needs no real auth per its docs; boto3 still requires non-empty strings).
   - Keep the existing `e2e-e2e-glue` bucket + `e2e_raw.orders` table (matched pair, used by the happy-path assertions already written against mocks).
   - Add a second table pair with deliberate mismatches (missing/extra column, a type mismatch, differing partition keys, differing S3 location) for the new live mismatch-path assertions.
   - Remove the try/catch-and-warn wrapper around the seed script's `spawnSync` result; failure now throws, same as `seedMinio()`, since floci is expected to genuinely implement these APIs.

3. **`tests/e2e/19-aws-glue-tab-live.spec.ts`** (new): mirrors `18-aws-s3-tab-live.spec.ts`'s structure (`api-helpers.ts`'s `createConfig`/`deleteConfig`/`deleteJob`/`triggerRun`/`waitForTerminal`, `test.skip(!liveBackends, ...)` gate).
   - Config: `aws_endpoint_url: 'http://127.0.0.1:4566'`, `aws_access_key_id: 'test'`, `aws_secret_access_key: 'test'`, `aws_region: 'us-east-1'`.
   - Glue Catalog compare: matched pair passes with no diff; mismatched pair produces the same diff shape (`missing_columns`, `extra_columns`, `type_mismatches`, `partition_key_mismatches`, `location_mismatch`) currently only exercised via `page.route()` mocks in `19-aws-glue-tab.spec.ts`.
   - Tracked job creation + `triggerRun`/`waitForTerminal`, asserting `PASSED`.
   - Glue Spark job-run (`start_job_run`/`get_job_run`) coverage is **conditional**: implementation must first verify empirically whether floci executes something real for these calls or returns a stub. If real, add live job-run assertions matching the existing mocked spec's shape (`job_run_state: 'SUCCEEDED'`, etc). If it's a stub/no-op, skip this slice in the live spec and leave a comment explaining why — do not assert against a fake result.

4. **`tests/e2e/20-aws-athena-tab-live.spec.ts`** (new): same structural pattern.
   - Real `SELECT * FROM e2e_raw.orders`-style query against the seeded table, asserting real `results.rows` and `dq_metrics` (not the mocked fixed payload used today).
   - Row-count assertion breach case (min/max assertion configured to fail against the real row count).
   - Empty-result-set query case.
   - Malformed-SQL case, asserting the error surfaces via `aws-athena-error`.
   - Tracked job creation + `triggerRun`/`waitForTerminal`, asserting `PASSED`.

5. **Docs**: update the `docker-compose.integration.yml` comment block and any spec/plan docs that reference the LocalStack Glue/Athena gap to describe the floci swap and the reason for it.

## Testing / Validation Strategy

Before writing spec assertions, implementation must bring up the `floci` service standalone and run the rewritten seed script directly against it, to confirm its actual Glue/Athena behavior (schema shape, error types, whether `start_job_run` does anything real) rather than assuming the floci README's claims hold exactly as documented. This determines whether the Glue Spark job-run live slice (item 3 above) is included or explicitly skipped.

Both new live specs follow the existing `E2E_LIVE_BACKENDS=1` gate and run as part of `docker compose -f docker-compose.integration.yml up -d --wait` in CI/local live-backend runs, same as the S3/SQL Server/Oracle/Airflow live specs already do.
