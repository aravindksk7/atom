import { test, expect } from './fixtures';
import { fillAdvancedOptions } from './compare-helpers';
import path from 'node:path';
import { createConfig, deleteConfig, authedContext } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const dataFile = (name: string) => path.join(__dirname, 'fixtures', 'data', name);

async function openBO(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-bo"]').click();
}

async function selectUploads(page: import('@playwright/test').Page) {
  await page.locator('[data-testid="compare-bo-source-a-mode-upload"]').click();
  await page.locator('[data-testid="compare-bo-source-a-upload-input"]').setInputFiles(dataFile('source.csv'));
  await page.locator('[data-testid="compare-bo-source-b-mode-upload"]').click();
  await page.locator('[data-testid="compare-bo-source-b-upload-input"]').setInputFiles(dataFile('target.csv'));
  await page.locator('[data-testid="compare-bo-key-columns-input"]').fill('id');
}

test.describe('08a compare / BO report', () => {
  test('upload-vs-upload success path', async ({ authedPage }) => {
    await openBO(authedPage);
    await selectUploads(authedPage);
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-bo-result-status"]')).toHaveText('FAILED', { timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="compare-bo-results-table"]')).toContainText('3');
  });

  test('advanced options accept and round-trip through a real compare', async ({ authedPage }) => {
    await openBO(authedPage);
    await selectUploads(authedPage);
    await fillAdvancedOptions(authedPage, 'compare-bo', { backend: 'polars', floatTolerance: '0.01' });
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-bo-result-status"]')).toHaveText('FAILED', { timeout: 20_000 });
  });

  test.describe('live BO mock', () => {
    test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1');
    let boConfigId: number;

    test.beforeAll(async ({ adminToken }) => {
      const ctx = await authedContext(adminToken);
      try {
        const cfg = await createConfig(ctx, `e2e-compare-bo-live-${Date.now()}`, 'dev', {
          db_host: 'unused', db_password: 'unused',
          bo_url: 'https://127.0.0.1:18443', bo_user: 'administrator', bo_password: 'Password1', bo_verify_ssl: false,
        });
        boConfigId = cfg.id;
      } finally {
        await ctx.dispose();
      }
    });

    test.afterAll(async ({ adminToken }) => {
      if (!boConfigId) return;
      const ctx = await authedContext(adminToken);
      try { await deleteConfig(ctx, boConfigId); } finally { await ctx.dispose(); }
    });

    test('live Source A vs upload Source B', async ({ authedPage }) => {
      await openBO(authedPage);
      await authedPage.locator('[data-testid="compare-bo-source-a-mode-live"]').click();
      await authedPage.locator('[data-testid="compare-bo-source-a-config-select"]').selectOption(String(boConfigId));
      await authedPage.locator('[data-testid="compare-bo-source-a-doc-select"]').selectOption({ label: 'Sales Orders' });
      await authedPage.locator('[data-testid="compare-bo-source-a-report-select"]').selectOption({ label: 'Orders' });
      await authedPage.locator('[data-testid="compare-bo-source-b-mode-upload"]').click();
      await authedPage.locator('[data-testid="compare-bo-source-b-upload-input"]').setInputFiles(dataFile('source.csv'));
      await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('id');
      await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();
      await expect(authedPage.locator('[data-testid="compare-bo-result-status"]')).toBeVisible({ timeout: 20_000 });
    });
  });

  test('negative: running with no source selected surfaces an error', async ({ authedPage }) => {
    await openBO(authedPage);
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('BO comparison failed');
  });
});
