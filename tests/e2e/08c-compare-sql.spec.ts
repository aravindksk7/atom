import { test, expect } from './fixtures';
import { createConfig, deleteConfig, authedContext } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';

async function openSQL(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-sql"]').click();
}

test.describe('08c compare / SQL', () => {
  test.skip(!liveBackends, 'SQL sub-tab requires E2E_LIVE_BACKENDS=1');

  let srcConfigId: number;
  let tgtConfigId: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    // Overridable the same way global-setup.ts's seed script is — installed ODBC driver
    // versions vary by machine, and the app defaults to Driver 17 (EnvironmentConfig.db_driver).
    const driver = process.env.LIVE_SQLSERVER_ODBC_DRIVER || 'ODBC Driver 17 for SQL Server';
    const base = { db_host: '127.0.0.1', db_port: 14333, db_user: 'sa', db_password: 'Atom_Test_12345!', db_driver: driver };
    try {
      srcConfigId = (await createConfig(ctx, `e2e-sql-src-${Date.now()}`, 'dev', { ...base, db_name: 'atom_e2e_src' })).id;
      tgtConfigId = (await createConfig(ctx, `e2e-sql-tgt-${Date.now()}`, 'dev', { ...base, db_name: 'atom_e2e_tgt' })).id;
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

  test('real SQL Server compare produces deterministic differences', async ({ authedPage }) => {
    await openSQL(authedPage);
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(srcConfigId));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(tgtConfigId));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELECT id, sku, amount FROM dbo.orders');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id, sku, amount FROM dbo.orders');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-sql-results"]')).toContainText('Results', { timeout: 20_000 });
    await expect(authedPage.locator('.compare-chip.chip-regressed')).toHaveText('1 differ');
  });

  test('KNOWN BUG: SQL diff row expansion also hits the undefined renderSrc/renderTgt', async ({ authedPage }) => {
    // Same underlying bug as 08b's recon-file test — see the plan header's
    // "Known pre-existing bug encountered during research" section. renderSrc/renderTgt
    // throw as uncaught page errors, not console.error calls.
    const pageErrors: string[] = [];
    authedPage.on('pageerror', (err) => pageErrors.push(err.message));

    await openSQL(authedPage);
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(srcConfigId));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(tgtConfigId));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELECT id, sku, amount FROM dbo.orders');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id, sku, amount FROM dbo.orders');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-sql-results"]')).toContainText('Results', { timeout: 20_000 });

    await authedPage.locator('[data-testid^="compare-sql-row-"]').first().click();
    await expect(authedPage.locator('td.text-emerald-700 span:visible').first()).toHaveText('undefined');
    await expect.poll(() => pageErrors.some((e) => e.includes('renderSrc is not defined'))).toBe(true);
    expect(pageErrors.some((e) => e.includes('renderTgt is not defined'))).toBe(true);
  });

  test('negative: malformed SQL surfaces backend error', async ({ authedPage }) => {
    // The compare runs as a background task (POST returns 202 immediately) — a query
    // error only surfaces once polling picks up the terminal ERROR status; there is no
    // 'SQL compare failed' toast for this path (that toast is only for exceptions thrown
    // by the initial POST /api/compare/sql request itself, e.g. a network error).
    await openSQL(authedPage);
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(srcConfigId));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(tgtConfigId));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELEKT this is not sql');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id FROM dbo.orders');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await expect(authedPage.locator('.badge:visible', { hasText: 'ERROR' })).toBeVisible({ timeout: 20_000 });
  });
});

test.describe('08c compare / SQL client-side guards', () => {
  test('negative: submitting empty shows Config A required first', async ({ authedPage }) => {
    await openSQL(authedPage);
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Config A required');
  });
});
