import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const FLOCI_ENDPOINT = 'http://127.0.0.1:4566';
const ATHENA_OUTPUT = 's3://atom-e2e-glue/athena-output/';

async function openAthenaTab(page: Page, configId: number) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-aws"]').click();
  await page.locator('[data-testid="aws-service-athena"]').click();
  await page.locator('[data-testid="aws-config-select"]').selectOption(String(configId));
  await page.locator('[data-testid="aws-athena-database-input"]').fill('e2e_raw');
  await page.locator('[data-testid="aws-athena-output-location-input"]').fill(ATHENA_OUTPUT);
}

async function runAthenaQuery(page: Page, query: string) {
  await page.locator('[data-testid="aws-athena-query-input"]').fill(query);
  await page.locator('[data-testid="aws-athena-run-query-btn"]').click();
}

test.describe('20 AWS Athena tab - live floci', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml floci service)');

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

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-aws-athena-cfg-${Date.now()}`, 'dev', {
        db_host: 'localhost',
        db_password: 'unused',
        aws_region: 'us-east-1',
        aws_access_key_id: 'test',
        aws_secret_access_key: 'test',
        aws_endpoint_url: FLOCI_ENDPOINT,
        aws_verify_ssl: false,
      });
      configId = cfg.id;
    } finally {
      await ctx.dispose();
    }
  });

  test('runs a real Athena query against the seeded Glue table', async ({ authedPage }) => {
    await openAthenaTab(authedPage, configId);
    await runAthenaQuery(authedPage, 'select id, sku, amount from orders');

    await expect(authedPage.locator('[data-testid="aws-athena-result"]')).toContainText('SUCCEEDED', { timeout: 30_000 });
    await expect(authedPage.locator('[data-testid="aws-athena-result"]')).toContainText('Rows: 3');
    await expect(authedPage.locator('[data-testid="aws-athena-result"]')).toContainText('amount');
    await expect(authedPage.locator('[data-testid="aws-athena-result"]')).toContainText(/A100|B200/);
  });

  test('reports an empty Athena result set', async ({ authedPage }) => {
    await openAthenaTab(authedPage, configId);
    await runAthenaQuery(authedPage, 'select id, sku, amount from empty_orders');

    await expect(authedPage.locator('[data-testid="aws-athena-result"]')).toContainText('SUCCEEDED', { timeout: 30_000 });
    await expect(authedPage.locator('[data-testid="aws-athena-result"]')).toContainText('Rows: 0');
  });

  test('surfaces malformed SQL from Athena', async ({ authedPage }) => {
    await openAthenaTab(authedPage, configId);
    await runAthenaQuery(authedPage, 'select from');

    await expect(authedPage.locator('[data-testid="aws-athena-error"]')).toBeVisible({ timeout: 30_000 });
    await expect(authedPage.locator('[data-testid="aws-athena-error"]')).toContainText(/Athena|FAILED|query|syntax|error/i);
  });

  test('runs passing and row-count-failing tracked Athena jobs through the backend', async ({ authedPage, adminToken }) => {
    await openAthenaTab(authedPage, configId);

    // Underscores, not hyphens: awsCreateAthenaQueryJob() in aws.js sanitizes the
    // job name via .replace(/[^a-z0-9_]+/gi, '_') before creating it, so a
    // hyphenated name here would create a job under a different (sanitized) name
    // than the one triggerRun/deleteJob reference below, silently running 0 jobs
    // against a nonexistent name (which vacuously reports PASSED with total_tests: 0).
    const suffix = Date.now();
    const passingJob = `e2e_athena_live_pass_${suffix}`;
    const failingJob = `e2e_athena_live_row_${suffix}`;
    createdJobs.push(passingJob, failingJob);

    await authedPage.locator('[data-testid="aws-athena-query-input"]').fill('select id, sku, amount from orders');
    await authedPage.locator('[data-testid="aws-athena-job-name-input"]').fill(passingJob);
    await authedPage.locator('[data-testid="aws-athena-add-assertion-btn"]').click();
    await authedPage.locator('[data-testid="aws-athena-assertion-path"]').nth(0).fill('row_count');
    await authedPage.locator('[data-testid="aws-athena-assertion-operator"]').nth(0).selectOption('==');
    await authedPage.locator('[data-testid="aws-athena-assertion-value"]').nth(0).fill('3');
    await authedPage.locator('[data-testid="aws-athena-add-assertion-btn"]').click();
    await authedPage.locator('[data-testid="aws-athena-assertion-path"]').nth(1).fill('null_counts.amount');
    await authedPage.locator('[data-testid="aws-athena-assertion-operator"]').nth(1).selectOption('<=');
    await authedPage.locator('[data-testid="aws-athena-assertion-value"]').nth(1).fill('0');
    await authedPage.locator('[data-testid="aws-athena-add-assertion-btn"]').click();
    await authedPage.locator('[data-testid="aws-athena-assertion-path"]').nth(2).fill('numeric.amount.avg');
    await authedPage.locator('[data-testid="aws-athena-assertion-operator"]').nth(2).selectOption('between');
    await authedPage.locator('[data-testid="aws-athena-assertion-min"]').nth(0).fill('40');
    await authedPage.locator('[data-testid="aws-athena-assertion-max"]').nth(0).fill('60');
    await authedPage.locator('[data-testid="aws-athena-create-job-btn"]').click();
    await expect(authedPage.getByText('Athena job created').last()).toBeVisible({ timeout: 20_000 });

    await authedPage.locator('[data-testid="aws-athena-job-name-input"]').fill(failingJob);
    await authedPage.locator('[data-testid="aws-athena-min-rows-input"]').fill('4');
    await authedPage.locator('[data-testid="aws-athena-create-job-btn"]').click();
    await expect(authedPage.getByText('Athena job created').last()).toBeVisible({ timeout: 20_000 });

    const ctx = await authedContext(adminToken);
    try {
      const passingRun = await triggerRun(ctx, [passingJob], configId);
      const passingStatus = await waitForTerminal(ctx, passingRun.run_id, 60_000);
      expect(passingStatus.status).toBe('PASSED');

      const failingRun = await triggerRun(ctx, [failingJob], configId);
      const failingStatus = await waitForTerminal(ctx, failingRun.run_id, 60_000);
      expect(failingStatus.status).toBe('FAILED');
    } finally {
      await ctx.dispose();
    }
  });
});
