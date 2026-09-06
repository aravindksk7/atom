// tests/e2e/48-aws-airflow-tab-live.spec.ts
import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const AIRFLOW_URL = 'http://127.0.0.1:18085';

test.describe('48 AWS Airflow tab - live standalone Airflow', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml airflow service, seeded DAG etl_orders_daily)');

  let configId: number;
  const createdJobs: string[] = [];

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-aws-airflow-cfg-${Date.now()}`, 'dev', {
        db_host: 'localhost',
        db_password: 'unused',
        airflow_url: AIRFLOW_URL,
        airflow_username: 'admin',
        airflow_password: 'admin',
      });
      configId = cfg.id;
    } finally {
      await ctx.dispose();
    }
  });

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

  async function openAirflowTab(page: Page) {
    await page.goto('/');
    await page.locator('[data-testid="nav-tab-aws"]').click();
    await page.locator('[data-testid="aws-service-airflow"]').click();
    await page.locator('[data-testid="aws-config-select"]').selectOption(String(configId));
  }

  test('lists the real seeded DAG and runs it to completion', async ({ authedPage }) => {
    // Playwright's default per-test timeout (30s) is shorter than the assertion's
    // 60s timeout below -- the outer test timeout would otherwise kill the test
    // while POST /api/aws/airflow/dags/etl_orders_daily/run is still synchronously
    // polling Airflow for the run to finish, before the assertion ever gets a
    // chance to see the resolved result.
    test.setTimeout(90_000);
    await openAirflowTab(authedPage);
    await authedPage.locator('[data-testid="aws-airflow-load-dags-btn"]').click();
    await authedPage.locator('[data-testid="aws-airflow-dag-select"]').selectOption('etl_orders_daily');
    await authedPage.locator('[data-testid="aws-airflow-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-airflow-result"]')).toContainText('success', { timeout: 60_000 });
    await expect(authedPage.locator('[data-testid="aws-airflow-result"]')).toContainText('extract_orders');
    await expect(authedPage.locator('[data-testid="aws-airflow-result"]')).toContainText('transform_orders');
  });

  test('creates a tracked Airflow DAG-run job and runs it through the backend', async ({ authedPage, adminToken }) => {
    await openAirflowTab(authedPage);
    await authedPage.locator('[data-testid="aws-airflow-dag-input"]').fill('etl_orders_daily');
    await authedPage.locator('[data-testid="aws-airflow-expected-status-select"]').selectOption('success');

    // The Airflow "Create Job" button (awsCreateAirflowRunJob in frontend/features/aws.js)
    // sanitizes the entered name via `.replace(/[^a-z0-9_]+/gi, '_').toLowerCase()` before
    // sending it to the backend — unlike the S3 tab's job creation, which sends the name
    // as-is. Use underscores only so the sanitized name matches exactly what we look up
    // via triggerRun/deleteJob below.
    const jobName = `e2e_airflow_orders_${Date.now()}`;
    createdJobs.push(jobName);
    await authedPage.locator('[data-testid="aws-airflow-job-name-input"]').fill(jobName);
    await authedPage.locator('[data-testid="aws-airflow-create-job-btn"]').click();

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
