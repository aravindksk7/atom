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

async function fillGlueCompare(page: Page, sourceTable: string, targetTable: string) {
  await page.locator('[data-testid="aws-glue-source-database-input"]').fill('e2e_raw');
  await page.locator('[data-testid="aws-glue-source-table-input"]').fill(sourceTable);
  await page.locator('[data-testid="aws-glue-target-database-input"]').fill('e2e_raw');
  await page.locator('[data-testid="aws-glue-target-table-input"]').fill(targetTable);
}

test.describe('19 AWS Glue tab - live floci', () => {
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
      const cfg = await createConfig(ctx, `e2e-aws-glue-cfg-${Date.now()}`, 'dev', {
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

  test('compares matching and mismatched Glue catalog tables against floci', async ({ authedPage }) => {
    await openGlueTab(authedPage, configId);

    await fillGlueCompare(authedPage, 'orders', 'orders_copy');
    await authedPage.locator('[data-testid="aws-glue-compare-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-glue-result"]')).toContainText('Match: true', { timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="aws-glue-result"]')).toContainText('Missing columns:');
    await expect(authedPage.locator('[data-testid="aws-glue-result"]')).toContainText('Extra columns:');
    await expect(authedPage.locator('[data-testid="aws-glue-result"]')).toContainText('Type mismatches:');

    await fillGlueCompare(authedPage, 'orders_source_mismatch', 'orders_target_mismatch');
    await authedPage.locator('[data-testid="aws-glue-compare-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-glue-result"]')).toContainText('Match: false', { timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="aws-glue-result"]')).toContainText('Missing columns: amount, source_only');
    await expect(authedPage.locator('[data-testid="aws-glue-result"]')).toContainText('Extra columns: target_only');
    await expect(authedPage.locator('[data-testid="aws-glue-result"]')).toContainText('id: int64 -> string');
    await expect(authedPage.locator('[data-testid="aws-glue-result"]')).toContainText('Location mismatch');
    await expect(authedPage.locator('[data-testid="aws-glue-result"]')).toContainText('Partition mismatch');
  });

  test('creates tracked Glue catalog compare job and runs it through the backend', async ({ authedPage, adminToken }) => {
    await openGlueTab(authedPage, configId);

    const jobName = `e2e-glue-live-${Date.now()}`;
    createdJobs.push(jobName);

    await fillGlueCompare(authedPage, 'orders', 'orders_copy');
    await authedPage.locator('[data-testid="aws-glue-job-name-input"]').fill(jobName);
    await authedPage.locator('[data-testid="aws-glue-create-job-btn"]').click();
    await expect(authedPage.getByText('Glue job created').last()).toBeVisible({ timeout: 20_000 });

    const ctx = await authedContext(adminToken);
    try {
      const run = await triggerRun(ctx, [jobName], configId);
      const status = await waitForTerminal(ctx, run.run_id, 60_000);
      expect(status.status).toBe('PASSED');
    } finally {
      await ctx.dispose();
    }
  });
});
