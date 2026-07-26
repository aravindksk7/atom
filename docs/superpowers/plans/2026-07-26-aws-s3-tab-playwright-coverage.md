# AWS S3 Tab Playwright Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live Playwright e2e coverage for the AWS S3 tab's ad-hoc actions and tracked-job creation buttons.

**Architecture:** Add one focused Playwright spec gated by `E2E_LIVE_BACKENDS=1`. The spec reuses the existing e2e auth fixtures, API helpers, Docker Compose MinIO service, and seeded `atom-e2e` bucket; it drives the real AWS tab UI and uses existing API helpers to trigger created jobs. No production code changes are expected unless the test exposes a real bug.

**Tech Stack:** Playwright `@playwright/test`, TypeScript, FastAPI e2e web server from `playwright.config.ts`, Docker Compose live backends, MinIO S3-compatible service.

## Global Constraints

- Add Playwright e2e coverage for the AWS tab S3 panel.
- Exercise ad-hoc Metadata, Row Count, Partitions, and Validate Format UI actions against live MinIO when live backends are enabled.
- Exercise all three tracked-job creation buttons: row count, format validation, and partition check.
- Assert created tracked jobs can be triggered through the existing run API and reach terminal states.
- Keep the test gated behind `E2E_LIVE_BACKENDS=1` so normal e2e runs do not require Docker.
- Do not add new production behavior, Docker services, runtime dependencies, or visual regression tooling.
- If local Node dependencies are absent, run `npm ci` before Playwright commands; do not commit `node_modules`.

---

## File Structure

**Create:**
- `tests/e2e/18-aws-s3-tab-live.spec.ts` — live MinIO-backed AWS tab e2e coverage.

**Modify only if test discovery requires it:**
- `tests/e2e/api-helpers.ts` — add a small helper only if existing `createConfig`, `deleteConfig`, `deleteJob`, `triggerRun`, and `waitForTerminal` are insufficient. Prefer no change.

---

### Task 1: Add Live AWS S3 Tab Playwright Spec

**Files:**
- Create: `tests/e2e/18-aws-s3-tab-live.spec.ts`

**Interfaces:**
- Consumes: `test`, `expect` from `tests/e2e/fixtures.ts`.
- Consumes: `authedContext(adminToken)`, `createConfig(ctx, name, envName, configData)`, `deleteConfig(ctx, id)`, `deleteJob(ctx, name)`, `triggerRun(ctx, jobSequence, configId)`, `waitForTerminal(ctx, runId, timeoutMs)` from `tests/e2e/api-helpers.ts`.
- Produces: a gated e2e spec with tests for AWS-tab ad-hoc S3 actions, tracked job creation, and type-mismatch rendering.

- [ ] **Step 1: Install local Node dependencies if missing**

Run: `npm ls @playwright/test --depth=0`

Expected if already installed: exits 0 and lists `@playwright/test`.

If it fails with empty tree or missing module, run:

```powershell
npm ci
```

Expected: exits 0 and installs local dev dependencies. Do not stage or commit `node_modules`.

- [ ] **Step 2: Write the failing Playwright spec**

Create `tests/e2e/18-aws-s3-tab-live.spec.ts`:

```ts
// tests/e2e/18-aws-s3-tab-live.spec.ts
import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const MINIO_ENDPOINT = 'http://127.0.0.1:19000';
const MINIO_BUCKET = 'atom-e2e';
const CSV_KEY = 'source/sales_east.csv';
const CSV_PREFIX = 'source/';

test.describe('18 AWS S3 tab - live MinIO', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml minio service)');

  let configId: number;
  const createdJobs: string[] = [];

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      for (const name of createdJobs) await deleteJob(ctx, name);
      if (configId) await deleteConfig(ctx, configId);
    } finally {
      await ctx.dispose();
    }
  });

  test.beforeEach(async ({ adminToken }) => {
    if (configId) return;
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-aws-s3-cfg-${Date.now()}`, 'dev', {
        aws_region: 'us-east-1',
        aws_access_key_id: 'minioadmin',
        aws_secret_access_key: 'minioadmin',
        aws_endpoint_url: MINIO_ENDPOINT,
        aws_verify_ssl: false,
      });
      configId = cfg.id;
    } finally {
      await ctx.dispose();
    }
  });

  async function openAwsTab(page: any) {
    await page.goto('/');
    await page.getByRole('button', { name: 'AWS' }).click();
    await expect(page.locator('[data-testid="aws-service-s3"]')).toBeVisible();
    await page.locator('[data-testid="aws-config-select"]').selectOption(String(configId));
    await page.locator('[data-testid="aws-bucket-input"]').fill(MINIO_BUCKET);
    await page.locator('[data-testid="aws-key-input"]').fill(CSV_KEY);
    await page.locator('[data-testid="aws-prefix-input"]').fill(CSV_PREFIX);
    await page.locator('[data-testid="aws-fmt-select"]').selectOption('csv');
  }

  test('runs ad-hoc metadata, row count, partitions, and format validation', async ({ authedPage }) => {
    await openAwsTab(authedPage);

    await authedPage.locator('[data-testid="aws-run-metadata-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-result"]')).toContainText('Size (bytes)', { timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="aws-result"]')).toContainText('ETag');

    await authedPage.locator('[data-testid="aws-run-row-count-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-result"]')).toContainText('Rows:', { timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="aws-result"]')).toContainText('s3_select');

    await authedPage.locator('[data-testid="aws-run-partitions-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-result"], [data-testid="aws-error"]')).toBeVisible({ timeout: 20_000 });

    await authedPage.locator('[data-testid="aws-expected-schema-input"]').fill('{"id":"string","sku":"string","amount":"string"}');
    await authedPage.locator('[data-testid="aws-run-validate-format-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-result"]')).toContainText('Parsed:', { timeout: 20_000 });
  });

  test('renders schema type mismatches from ad-hoc validation', async ({ authedPage }) => {
    await openAwsTab(authedPage);
    await authedPage.route('**/api/aws/s3/validate-format', async (route) => {
      await route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: {
            error_type: 'schema_validation',
            message: 'Schema mismatch in s3://atom-e2e/source/sales_east.csv',
            missing_in_target: [],
            extra_in_target: [],
            type_mismatches: [{ column: 'amount', expected_type: 'decimal(12,2)', actual_type: 'string' }],
          },
        }),
      });
    });

    await authedPage.locator('[data-testid="aws-expected-schema-input"]').fill('{"amount":"decimal(12,2)"}');
    await authedPage.locator('[data-testid="aws-run-validate-format-btn"]').click();

    await expect(authedPage.locator('[data-testid="aws-error"]')).toContainText('Type mismatches:', { timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="aws-error"]')).toContainText('amount: expected decimal(12,2), actual string');
  });

  test('creates tracked S3 jobs and runs them through the backend', async ({ authedPage, adminToken }) => {
    await openAwsTab(authedPage);

    const suffix = Date.now();
    const rowJob = `e2e-s3-row-${suffix}`;
    const formatJob = `e2e-s3-format-${suffix}`;
    const partitionJob = `e2e-s3-partition-${suffix}`;
    createdJobs.push(rowJob, formatJob, partitionJob);

    await authedPage.locator('[data-testid="aws-job-name-input"]').fill(rowJob);
    await authedPage.locator('[data-testid="aws-min-rows-input"]').fill('1');
    await authedPage.locator('[data-testid="aws-create-row-count-job-btn"]').click();
    await expect(authedPage.getByText('S3 job created')).toBeVisible({ timeout: 20_000 });

    await authedPage.locator('[data-testid="aws-job-name-input"]').fill(formatJob);
    await authedPage.locator('[data-testid="aws-expected-schema-input"]').fill('{"id":"string","sku":"string","amount":"string"}');
    await authedPage.locator('[data-testid="aws-create-format-validation-job-btn"]').click();
    await expect(authedPage.getByText('S3 job created')).toBeVisible({ timeout: 20_000 });

    await authedPage.locator('[data-testid="aws-job-name-input"]').fill(partitionJob);
    await authedPage.locator('[data-testid="aws-min-partitions-input"]').fill('0');
    await authedPage.locator('[data-testid="aws-create-partition-check-job-btn"]').click();
    await expect(authedPage.getByText('S3 job created')).toBeVisible({ timeout: 20_000 });

    const ctx = await authedContext(adminToken);
    try {
      const rowRun = await triggerRun(ctx, [rowJob], configId);
      const rowStatus = await waitForTerminal(ctx, rowRun.run_id, 60_000);
      expect(rowStatus.status).not.toBe('ERROR');

      const formatRun = await triggerRun(ctx, [formatJob], configId);
      const formatStatus = await waitForTerminal(ctx, formatRun.run_id, 60_000);
      expect(formatStatus.status).not.toBe('ERROR');

      const partitionRun = await triggerRun(ctx, [partitionJob], configId);
      const partitionStatus = await waitForTerminal(ctx, partitionRun.run_id, 60_000);
      expect(['PASSED', 'FAILED', 'SLOW', 'COMPLETED']).toContain(partitionStatus.status);
    } finally {
      await ctx.dispose();
    }
  });
});
```

- [ ] **Step 3: Run list command to verify test is discovered**

Run: `npx playwright test tests/e2e/18-aws-s3-tab-live.spec.ts --list`

Expected: lists the new spec and three tests. If `@playwright/test` is missing, run `npm ci` and retry.

- [ ] **Step 4: Run without live flag to verify skip behavior**

Run: `npx playwright test tests/e2e/18-aws-s3-tab-live.spec.ts --project=chromium`

Expected: tests are skipped because `E2E_LIVE_BACKENDS` is not `1`; the app server may still start because of Playwright config.

- [ ] **Step 5: Run live Playwright test**

Run:

```powershell
$env:E2E_LIVE_BACKENDS = "1"; npx playwright test tests/e2e/18-aws-s3-tab-live.spec.ts
```

Expected: Docker Compose starts live services, MinIO is seeded, FastAPI starts on port 8055, and the three tests pass.

If the partition test fails because existing seeded paths are not Hive-style, adjust only the partition assertion to expect a visible controlled error or seed a Hive-style object in the test setup using the same MinIO credentials. Do not change production code for fixture shape.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/18-aws-s3-tab-live.spec.ts
git commit -m "test(aws-ui): cover S3 tab with Playwright"
```

---

### Task 2: Final E2E Verification and Documentation

**Files:**
- Modify: `README.md` only if it already has an e2e/live-backend test section where the new command belongs.

**Interfaces:**
- Consumes: `tests/e2e/18-aws-s3-tab-live.spec.ts` from Task 1.
- Produces: final verified e2e command evidence and optional README command documentation.

- [ ] **Step 1: Check README for e2e command section**

Run: `rg -n "Playwright|test:e2e|E2E_LIVE_BACKENDS|docker-compose.integration" README.md`

Expected: existing docs show whether the new AWS S3 tab command belongs in README.

- [ ] **Step 2: Add README command only if there is an existing e2e section**

If README has an e2e/live-backend section, add:

```markdown
For the live AWS S3 tab flow against MinIO:

```powershell
$env:E2E_LIVE_BACKENDS = "1"; npx playwright test tests/e2e/18-aws-s3-tab-live.spec.ts
```
```

If no such section exists, do not edit README in this task.

- [ ] **Step 3: Run focused syntax and discovery checks**

Run: `npx playwright test tests/e2e/18-aws-s3-tab-live.spec.ts --list`

Expected: lists the three new tests.

- [ ] **Step 4: Run live e2e verification**

Run:

```powershell
$env:E2E_LIVE_BACKENDS = "1"; npx playwright test tests/e2e/18-aws-s3-tab-live.spec.ts
```

Expected: PASS. Include the exact test count in the implementation report.

- [ ] **Step 5: Check git status**

Run: `git status --short; git diff --stat`

Expected: only intended files are modified. Generated tracked `api/__pycache__/*.pyc` files may be dirty from Python test/server startup; do not stage them.

- [ ] **Step 6: Commit README only if changed**

If README changed:

```bash
git add README.md
git commit -m "docs(aws): document S3 tab e2e command"
```

If README did not change, do not create a commit.

---

## Self-Review Notes

- **Spec coverage:** Task 1 covers the live MinIO AWS tab spec, ad-hoc actions, tracked job buttons, type mismatch rendering, config setup, cleanup, and run triggering. Task 2 covers final command verification and optional README documentation.
- **Scope:** The plan adds e2e coverage only. Production code changes are not planned.
- **Type consistency:** The plan uses existing helper names from `tests/e2e/api-helpers.ts` and existing AWS tab test IDs from `frontend/partials/tab-aws.html`.
