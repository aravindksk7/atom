import { test, expect } from './fixtures';
import { createConfig, deleteConfig, authedContext } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';

async function openSQL(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-sql"]').click();
}

test.describe('45 compare / Oracle', () => {
  test.skip(!liveBackends, 'Oracle sub-tab requires E2E_LIVE_BACKENDS=1');

  let srcConfigId: number;
  let tgtConfigId: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    const base = { db_type: 'oracle', db_driver: 'oracledb', db_host: '127.0.0.1', db_port: 1521, db_name: 'FREEPDB1' };
    try {
      srcConfigId = (await createConfig(ctx, `e2e-oracle-src-${Date.now()}`, 'dev', {
        ...base,
        db_user: 'e2e_src',
        db_password: 'Oracle_Test_12345',
      })).id;
      tgtConfigId = (await createConfig(ctx, `e2e-oracle-tgt-${Date.now()}`, 'dev', {
        ...base,
        db_user: 'e2e_tgt',
        db_password: 'Oracle_Test_12345',
      })).id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      if (srcConfigId) await deleteConfig(ctx, srcConfigId);
      if (tgtConfigId) await deleteConfig(ctx, tgtConfigId);
    } finally {
      await ctx.dispose();
    }
  });

  test('real Oracle compare produces deterministic differences', async ({ authedPage }) => {
    await openSQL(authedPage);
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(srcConfigId));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(tgtConfigId));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELECT id, sku, amount FROM orders');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id, sku, amount FROM orders');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-sql-results"]')).toContainText('Results', { timeout: 20_000 });
    await expect(authedPage.locator('.compare-chip.chip-regressed')).toHaveText('1 differ');
  });

  test('Oracle diff row expansion renders real source/target values', async ({ authedPage }) => {
    const pageErrors: string[] = [];
    authedPage.on('pageerror', (err) => pageErrors.push(err.message));

    await openSQL(authedPage);
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(srcConfigId));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(tgtConfigId));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELECT id, sku, amount FROM orders');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id, sku, amount FROM orders');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-sql-results"]')).toContainText('Results', { timeout: 20_000 });

    await authedPage.locator('[data-testid^="compare-sql-row-"]').first().click();
    const firstValueCell = authedPage.locator('td.text-slate-700 span:visible').first();
    await expect(firstValueCell).not.toHaveText('undefined');
    await expect(firstValueCell).not.toBeEmpty();
    expect(pageErrors.some((e) => e.includes('renderSrc is not defined'))).toBe(false);
    expect(pageErrors.some((e) => e.includes('renderTgt is not defined'))).toBe(false);
  });

  test('negative: malformed SQL surfaces backend error', async ({ authedPage }) => {
    await openSQL(authedPage);
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(srcConfigId));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(tgtConfigId));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELEKT this is not sql');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id FROM orders');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await expect(authedPage.locator('.badge:visible', { hasText: 'ERROR' })).toBeVisible({ timeout: 20_000 });
  });
});
