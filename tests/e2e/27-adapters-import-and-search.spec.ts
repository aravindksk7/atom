import { test, expect } from './fixtures';
import { createConfig, deleteConfig, deleteJob, authedContext } from './api-helpers';

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

test.describe('27 adapters / Import Jobs from File', () => {
  test('a valid JSON array parses, previews, and imports', async ({ authedPage, adminToken }) => {
    const jobName = `e2e-import-json-${Date.now()}`;

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.getByText('Import Jobs from File').click();

    const jobs = [{ name: jobName, job_type: 'automic_job', params: { job_name: 'ETL_NIGHTLY' }, tags: ['e2e'] }];
    await authedPage.locator('input[type="file"]').first().setInputFiles({
      name: 'jobs.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(jobs)),
    });

    await expect(authedPage.getByText('1 job(s) parsed')).toBeVisible();
    await expect(authedPage.getByRole('cell', { name: jobName })).toBeVisible();

    try {
      await authedPage.getByRole('button', { name: /Import 1 job\(s\)/ }).click();
      await expect(authedPage.locator('.toast-title')).toContainText('Import complete');

      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
      await expect(authedPage.locator(`[data-testid="job-row-${jobName}"]`)).toBeVisible();
    } finally {
      const ctx = await authedContext(adminToken);
      try {
        await deleteJob(ctx, jobName);
      } finally {
        await ctx.dispose();
      }
    }
  });

  test('a CSV file with a row missing "name" blocks import with an inline error', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.getByText('Import Jobs from File').click();

    const csv = 'name,job_type,job_name\n,automic_job,ETL_NIGHTLY\n';
    await authedPage.locator('input[type="file"]').first().setInputFiles({
      name: 'jobs.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(csv),
    });

    await expect(authedPage.getByText(/row\(s\) missing "name"/)).toBeVisible();
    await expect(authedPage.getByRole('button', { name: /Import 1 job\(s\)/ })).toBeDisabled();
  });

  test('an unparseable file surfaces a parse error and blocks import', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.getByText('Import Jobs from File').click();

    await authedPage.locator('input[type="file"]').first().setInputFiles({
      name: 'jobs.json',
      mimeType: 'application/json',
      buffer: Buffer.from('not valid json {{'),
    });

    await expect(authedPage.getByText(/Parse error/)).toBeVisible();
  });
});

test.describe('27b adapters / Browse & Import from Automic', () => {
  test('search renders results, select-all + Import Selected creates jobs', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let cfgId: number;
    try {
      const cfg = await createConfig(ctx, `e2e-browse-automic-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        automic_url: 'http://127.0.0.1:1', automic_user: 'u', automic_password: 'p',
      });
      cfgId = cfg.id;
    } finally {
      await ctx.dispose();
    }

    await authedPage.route('**/api/adapters/automic/search**', (r) =>
      r.fulfill(json([
        { name: 'ETL_JOB_A', status: 'ACTIVE' },
        { name: 'ETL_JOB_B', status: 'ACTIVE' },
      ])));

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.getByText('Browse & Import from Automic').click();
    await authedPage.locator('#a11y-adapters-config-2').selectOption(String(cfgId!));
    await authedPage.locator('#a11y-adapters-filter').fill('ETL_*');
    await authedPage.getByRole('button', { name: 'Search' }).click();

    await expect(authedPage.getByText('ETL_JOB_A')).toBeVisible();
    await expect(authedPage.getByText('ETL_JOB_B')).toBeVisible();

    const jobNames = ['etl_job_a', 'etl_job_b'];
    try {
      await authedPage.getByLabel('Select all browsed Automic jobs').click();
      await expect(authedPage.getByText('2 of 2 selected')).toBeVisible();
      await authedPage.getByRole('button', { name: /Import Selected/ }).click();
      await expect(authedPage.locator('.toast-title')).toContainText('Import complete');

      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
      for (const name of jobNames) {
        await expect(authedPage.locator(`[data-testid="job-row-${name}"]`)).toBeVisible();
      }
    } finally {
      const cleanup = await authedContext(adminToken);
      try {
        for (const name of jobNames) await deleteJob(cleanup, name);
        await deleteConfig(cleanup, cfgId!);
      } finally {
        await cleanup.dispose();
      }
    }
  });
});

test.describe('27c adapters / document search filter', () => {
  test('typing in the document search box narrows the visible document list', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let cfgId: number;
    try {
      const cfg = await createConfig(ctx, `e2e-doc-search-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        bo_url: 'http://127.0.0.1:1', bo_user: 'u', bo_password: 'p',
      });
      cfgId = cfg.id;
    } finally {
      await ctx.dispose();
    }

    await authedPage.route('**/api/adapters/sap-bo/documents?**', (r) =>
      r.fulfill(json([
        { id: '9001', name: 'Sales Orders', folder: 'Public' },
        { id: '9002', name: 'Inventory Snapshot', folder: 'Public' },
      ])));

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(cfgId!));
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();

    await expect(authedPage.locator('.bo-doc-item', { hasText: 'Sales Orders' })).toBeVisible();
    await expect(authedPage.locator('.bo-doc-item', { hasText: 'Inventory Snapshot' })).toBeVisible();

    await authedPage.locator('[data-testid="bo-doc-search-input"]').fill('Sales');

    await expect(authedPage.locator('.bo-doc-item', { hasText: 'Sales Orders' })).toBeVisible();
    await expect(authedPage.locator('.bo-doc-item', { hasText: 'Inventory Snapshot' })).toBeHidden();
    await expect(authedPage.getByText('1 of 2')).toBeVisible();

    const cleanup = await authedContext(adminToken);
    try { await deleteConfig(cleanup, cfgId!); } finally { await cleanup.dispose(); }
  });
});
