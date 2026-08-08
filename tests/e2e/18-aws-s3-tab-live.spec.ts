// tests/e2e/18-aws-s3-tab-live.spec.ts
import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const MINIO_ENDPOINT = 'http://127.0.0.1:29000';
const MINIO_BUCKET = 'atom-e2e';
const CSV_KEY = 'source/sales_east.csv';
const CSV_PREFIX = 'source/';

test.describe('18 AWS S3 tab - live MinIO', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml minio service)');

  let configId: number;
  const createdJobs: string[] = [];

  test.afterAll(async ({ adminToken }) => {
    if (!configId && createdJobs.length === 0) return;
    const ctx = await authedContext(adminToken);
    try {
      for (const name of createdJobs) await deleteJob(ctx, name);
      if (configId) await deleteConfig(ctx, configId);
    } finally {
      await ctx.dispose();
    }
  });

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-aws-s3-cfg-${Date.now()}`, 'dev', {
        db_host: 'localhost',
        db_password: 'unused',
        aws_region: 'us-east-1',
        aws_access_key_id: 'minioadmin',
        aws_secret_access_key: 'minioadmin',
        aws_endpoint_url: MINIO_ENDPOINT,
        aws_verify_ssl: false,
      });
      configId = cfg.id;
    } catch (error) {
      if (configId) await deleteConfig(ctx, configId);
      throw error;
    } finally {
      await ctx.dispose();
    }
  });

  async function openAwsTab(page: Page) {
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
    await expect(authedPage.locator('[data-testid="aws-error"]')).toBeHidden({ timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="aws-result"] table.results-table')).toBeVisible({ timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="aws-result"]')).toContainText('objects');

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
    await expect(authedPage.getByText('S3 job created').last()).toBeVisible({ timeout: 20_000 });

    await authedPage.locator('[data-testid="aws-job-name-input"]').fill(formatJob);
    await authedPage.locator('[data-testid="aws-expected-schema-input"]').fill('{"id":"string","sku":"string","amount":"string"}');
    await authedPage.locator('[data-testid="aws-create-format-validation-job-btn"]').click();
    await expect(authedPage.getByText('S3 job created').last()).toBeVisible({ timeout: 20_000 });

    await authedPage.locator('[data-testid="aws-job-name-input"]').fill(partitionJob);
    await authedPage.locator('[data-testid="aws-min-partitions-input"]').fill('0');
    await authedPage.locator('[data-testid="aws-create-partition-check-job-btn"]').click();
    await expect(authedPage.getByText('S3 job created').last()).toBeVisible({ timeout: 20_000 });

    const ctx = await authedContext(adminToken);
    try {
      const rowRun = await triggerRun(ctx, [rowJob], configId);
      const rowStatus = await waitForTerminal(ctx, rowRun.run_id, 60_000);
      expect(rowStatus.status).toBe('PASSED');

      const formatRun = await triggerRun(ctx, [formatJob], configId);
      const formatStatus = await waitForTerminal(ctx, formatRun.run_id, 60_000);
      expect(formatStatus.status).toBe('PASSED');

      const partitionRun = await triggerRun(ctx, [partitionJob], configId);
      const partitionStatus = await waitForTerminal(ctx, partitionRun.run_id, 60_000);
      expect(partitionStatus.status).toBe('PASSED');
    } finally {
      await ctx.dispose();
    }
  });
});
