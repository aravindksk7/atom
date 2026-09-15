# Floci AWS Live Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace LocalStack with floci for integration tests and add real live Glue/Athena AWS-tab coverage.

**Architecture:** `docker-compose.integration.yml` exposes floci on the same `4566` endpoint used by existing AWS config plumbing. `tests/e2e/global-setup.ts` seeds Glue Catalog tables and S3 CSV data against floci, then new Playwright live specs exercise AWS Glue and Athena UI operations plus tracked jobs through the real backend.

**Tech Stack:** Docker Compose, floci, TypeScript, Playwright, Python seed scripts, boto3, FastAPI backend routes, existing AWS tab Alpine slice.

## Global Constraints

- Keep endpoint `http://127.0.0.1:4566`, dummy creds `test`/`test`, and region `us-east-1` for floci-backed Glue/Athena tests.
- Leave existing mocked `tests/e2e/19-aws-glue-tab.spec.ts` and `tests/e2e/20-aws-athena-tab.spec.ts` untouched.
- Leave S3/MinIO live coverage unchanged.
- Do not change product AWS config/session/runtime plumbing; `aws_endpoint_url` is already sufficient.
- Fail hard when Glue/Athena seeding fails under `E2E_LIVE_BACKENDS=1`.
- Gate new live specs with `E2E_LIVE_BACKENDS=1`.

---

## File Structure

- Modify `docker-compose.integration.yml`: replace the `localstack` service with `floci` at port `4566`.
- Modify `tests/e2e/global-setup.ts`: rewrite `seedGlueAthena()` to seed floci S3/Glue/Athena live fixtures and throw on failure.
- Create `tests/e2e/19-aws-glue-tab-live.spec.ts`: live Glue compare and tracked Glue catalog job coverage.
- Create `tests/e2e/20-aws-athena-tab-live.spec.ts`: live Athena query, failure, empty result, malformed SQL, and tracked job coverage.
- Modify docs that still describe LocalStack-backed Glue/Athena live coverage: `docs/superpowers/specs/2026-08-16-live-docker-compare-testing-design.md`, `docs/superpowers/specs/2026-09-05-oracle-live-docker-e2e-design.md`, `docs/superpowers/plans/2026-09-06-aws-tab-live-e2e-reconcile-matrix.md`.

### Task 1: Swap Compose Service

**Files:**
- Modify: `docker-compose.integration.yml`

**Interfaces:**
- Consumes: Docker Compose service name used only by compose orchestration.
- Produces: floci listening on `http://127.0.0.1:4566`.

- [ ] **Step 1: Confirm old service exists**

Read `docker-compose.integration.yml` and verify it contains:

```yaml
  localstack:
    image: localstack/localstack:3
    container_name: atom-localstack-integration
```

- [ ] **Step 2: Replace the service block**

Change the block to:

```yaml
  floci:
    # Free local AWS emulator used for Glue/Athena live e2e coverage. LocalStack
    # Community does not implement Glue/Athena without a Pro auth token, while
    # floci exposes these APIs on the same edge port used by the app's
    # aws_endpoint_url plumbing.
    image: floci/floci:latest
    container_name: atom-floci-integration
    ports:
      - "4566:4566"
    environment:
      - AWS_DEFAULT_REGION=us-east-1
```

- [ ] **Step 3: Validate compose syntax**

Run: `docker compose -f docker-compose.integration.yml config`

Expected: exit code 0 and rendered output includes `floci`, `floci/floci:latest`, and `4566:4566`.

### Task 2: Seed floci Glue/Athena Fixtures

**Files:**
- Modify: `tests/e2e/global-setup.ts`

**Interfaces:**
- Consumes: floci endpoint at `http://127.0.0.1:4566`.
- Produces: S3 bucket `atom-e2e-glue`, database `e2e_raw`, matched tables `orders` and `orders_copy`, mismatched tables `orders_source_mismatch` and `orders_target_mismatch`, and Athena output prefix `s3://atom-e2e-glue/athena-output/`.

- [ ] **Step 1: Write the failure-first assertion for seed behavior**

Before editing the seed function, run live setup against LocalStack/floci absence to observe the current behavior:

```powershell
$env:E2E_LIVE_BACKENDS='1'; npx playwright test tests/e2e/19-aws-glue-tab-live.spec.ts --list
```

Expected before the spec exists: Playwright reports no matching test file. This confirms Task 3 will add the failing coverage.

- [ ] **Step 2: Replace the seed script**

Implement `seedGlueAthena()` with a Python script that:

```python
endpoint = "http://127.0.0.1:4566"
creds = dict(aws_access_key_id="test", aws_secret_access_key="test", region_name="us-east-1")
s3 = boto3.client("s3", endpoint_url=endpoint, **creds)
glue = boto3.client("glue", endpoint_url=endpoint, **creds)

bucket = "atom-e2e-glue"
database = "e2e_raw"
orders_csv = b"id,sku,amount,order_date\n1,A100,25.50,2026-09-15\n2,B200,50.00,2026-09-15\n3,C300,75.00,2026-09-16\n"
empty_csv = b"id,sku,amount,order_date\n"

# retry s3.list_buckets() for 30s, create bucket if absent, put raw/orders/part-0.csv,
# raw/orders_copy/part-0.csv, raw/orders_source_mismatch/part-0.csv,
# raw/orders_target_mismatch/part-0.csv, raw/empty_orders/part-0.csv.
# create database if absent, delete/recreate tables idempotently.
```

Use these exact table definitions:

```python
orders_columns = [
    {"Name": "id", "Type": "int"},
    {"Name": "sku", "Type": "string"},
    {"Name": "amount", "Type": "double"},
]
partition_keys = [{"Name": "order_date", "Type": "string"}]

tables = [
    ("orders", orders_columns, partition_keys, f"s3://{bucket}/raw/orders/"),
    ("orders_copy", orders_columns, partition_keys, f"s3://{bucket}/raw/orders_copy/"),
    ("orders_source_mismatch", orders_columns + [{"Name": "source_only", "Type": "string"}], partition_keys, f"s3://{bucket}/raw/orders_source_mismatch/"),
    ("orders_target_mismatch", [
        {"Name": "id", "Type": "string"},
        {"Name": "sku", "Type": "string"},
        {"Name": "target_only", "Type": "string"},
    ], [{"Name": "business_date", "Type": "string"}], f"s3://{bucket}/raw/orders_target_mismatch/"),
    ("empty_orders", orders_columns, partition_keys, f"s3://{bucket}/raw/empty_orders/"),
]
```

- [ ] **Step 3: Remove warn-and-continue**

Change TypeScript failure handling to:

```ts
if (result.status !== 0) {
  throw new Error(`Glue/Athena seed failed:\n${result.stdout}\n${result.stderr}`);
}
console.log('[global-setup] Glue/Athena seeded:', result.stdout.trim());
```

- [ ] **Step 4: Manually verify seed against floci**

Run: `docker compose -f docker-compose.integration.yml up -d floci`

Run the Python body from `seedGlueAthena()` with `python -c "<script>"`.

Expected: exit code 0 and stdout contains `seeded`.

### Task 3: Add Live Glue AWS Tab Coverage

**Files:**
- Create: `tests/e2e/19-aws-glue-tab-live.spec.ts`

**Interfaces:**
- Consumes: config created by `createConfig`, seeded Glue tables from Task 2.
- Produces: assertions proving real Glue compare and tracked `aws_glue_catalog_compare` jobs work against floci.

- [ ] **Step 1: Add a failing live spec skeleton**

Create a spec with:

```ts
import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const FLOCI_ENDPOINT = 'http://127.0.0.1:4566';

async function openGlueTab(page: Page, configId: number) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-aws"]').click();
  await page.locator('[data-testid="aws-service-glue"]').click();
  await page.locator('[data-testid="aws-config-select"]').selectOption(String(configId));
}
```

Run: `$env:E2E_LIVE_BACKENDS='1'; npx playwright test tests/e2e/19-aws-glue-tab-live.spec.ts --project=chromium`

Expected: fails until tests and seed data are implemented.

- [ ] **Step 2: Add config lifecycle**

Use `beforeAll` to create config data:

```ts
{
  db_host: 'localhost',
  db_password: 'unused',
  aws_region: 'us-east-1',
  aws_access_key_id: 'test',
  aws_secret_access_key: 'test',
  aws_endpoint_url: FLOCI_ENDPOINT,
  aws_verify_ssl: false,
}
```

Use `afterAll` to delete created jobs and config.

- [ ] **Step 3: Add match and mismatch test**

Fill inputs for `e2e_raw.orders` vs `e2e_raw.orders_copy`, click `aws-glue-compare-btn`, and assert `aws-glue-result` contains a matching indication without missing/extra/type mismatch text. Then compare `orders_source_mismatch` vs `orders_target_mismatch` and assert text contains `Missing columns`, `Extra columns`, `id: int64 -> string`, and location/partition mismatch evidence.

- [ ] **Step 4: Add tracked catalog job test**

Create an `aws_glue_catalog_compare` job through the UI for `orders` vs `orders_copy`, run it with `triggerRun(ctx, [jobName], configId)`, wait with `waitForTerminal`, and assert `status === 'PASSED'`.

- [ ] **Step 5: Verify Glue live spec**

Run: `$env:E2E_LIVE_BACKENDS='1'; npx playwright test tests/e2e/19-aws-glue-tab-live.spec.ts --project=chromium`

Expected: all tests pass.

### Task 4: Add Live Athena AWS Tab Coverage

**Files:**
- Create: `tests/e2e/20-aws-athena-tab-live.spec.ts`

**Interfaces:**
- Consumes: config created by `createConfig`, seeded Glue/S3 tables from Task 2.
- Produces: assertions proving real Athena query execution, DQ metrics, query errors, and tracked `aws_athena_query` jobs work against floci.

- [ ] **Step 1: Add live spec setup**

Create the same config lifecycle as Task 3, using `FLOCI_ENDPOINT` and cleanup of created jobs/config.

- [ ] **Step 2: Add happy-path query test**

Open Athena tab, set database `e2e_raw`, query `select id, sku, amount from orders`, output location `s3://atom-e2e-glue/athena-output/`, run query, and assert `aws-athena-result` contains `SUCCEEDED`, `Rows: 3`, `amount`, and `A100` or `B200`.

- [ ] **Step 3: Add row-count breach tracked job test**

Create an Athena job with `min_rows` set to `4` for the real three-row table, run via API, and assert terminal status is `FAILED`.

- [ ] **Step 4: Add empty result test**

Run `select id, sku, amount from empty_orders` or a supported false-filter query against `orders`; assert `SUCCEEDED` and `Rows: 0`.

- [ ] **Step 5: Add malformed SQL test**

Run `select from` and assert `aws-athena-error` becomes visible and contains an Athena failure message.

- [ ] **Step 6: Add passing tracked query job test**

Create a UI job with metric assertions `row_count == 3`, `null_counts.amount <= 0`, and `numeric.amount.avg between 40 and 60`, trigger it through the backend, and assert terminal `PASSED`.

- [ ] **Step 7: Verify Athena live spec**

Run: `$env:E2E_LIVE_BACKENDS='1'; npx playwright test tests/e2e/20-aws-athena-tab-live.spec.ts --project=chromium`

Expected: all tests pass. If floci returns a stub/no-op for a specific Athena feature, document that exact behavior inline and skip only the unsupported slice.

### Task 5: Update Docs and Final Verification

**Files:**
- Modify: `docs/superpowers/specs/2026-08-16-live-docker-compare-testing-design.md`
- Modify: `docs/superpowers/specs/2026-09-05-oracle-live-docker-e2e-design.md`
- Modify: `docs/superpowers/plans/2026-09-06-aws-tab-live-e2e-reconcile-matrix.md`

**Interfaces:**
- Consumes: implementation from Tasks 1-4.
- Produces: docs that no longer claim LocalStack Community provides live Glue/Athena.

- [ ] **Step 1: Replace stale LocalStack claims**

Update stale docs to say floci now backs Glue/Athena live testing and LocalStack Community was removed because Glue/Athena require LocalStack Pro.

- [ ] **Step 2: Run static checks**

Run: `docker compose -f docker-compose.integration.yml config`

Expected: exit code 0.

Run: `npx playwright test tests/e2e/19-aws-glue-tab.spec.ts tests/e2e/20-aws-athena-tab.spec.ts --project=chromium`

Expected: existing mocked specs still pass.

- [ ] **Step 3: Run live checks**

Run: `$env:E2E_LIVE_BACKENDS='1'; npx playwright test tests/e2e/18-aws-s3-tab-live.spec.ts tests/e2e/19-aws-glue-tab-live.spec.ts tests/e2e/20-aws-athena-tab-live.spec.ts --project=chromium`

Expected: S3 live remains passing and new Glue/Athena live specs pass.

## Self-Review

- Spec coverage: compose swap is Task 1; hard-fail seeding and matched/mismatched fixture data are Task 2; live Glue compare/job coverage is Task 3; live Athena query/assertion/error/job coverage is Task 4; docs are Task 5.
- Placeholder scan: no TBD/TODO/fill-later language is present.
- Type consistency: all tests use existing helper signatures `createConfig(ctx, name, envName, configData)`, `deleteConfig(ctx, id)`, `deleteJob(ctx, name)`, `triggerRun(ctx, jobNames, configId)`, and `waitForTerminal(ctx, runId, timeoutMs)`.
