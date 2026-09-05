import { test, expect } from './fixtures';
import { createConfig, deleteConfig, authedContext } from './api-helpers';

test.describe('41 Live Docker Config Schema Browse per Data Source', () => {
  let configId: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      // Create a config pointing to live Docker SQL Server with a named Oracle/Netezza connection entry
      const cfg = await createConfig(ctx, `e2e-live-docker-schema-${Date.now()}`, 'dev', {
        db_type: 'mssql',
        // Honour the same override global-setup.ts uses to seed SQL Server. Hardcoding
        // "ODBC Driver 17" made GET /api/configs/{id}/schema fail with IM002 on hosts
        // that only ship Driver 18; openSchemaExplorer() resets schemaExplorerId to
        // null on a schema-load error, so the panel silently closed and every
        // subsequent locator in this spec resolved to nothing.
        db_driver: process.env.LIVE_SQLSERVER_ODBC_DRIVER || 'ODBC Driver 17 for SQL Server',
        db_host: '127.0.0.1',
        db_port: 14333,
        db_name: 'master',
        db_user: 'sa',
        db_password: 'Atom_Test_12345!',
        connections: {
          analytics_dw: {
            db_type: 'oracle',
            db_host: '127.0.0.1',
            db_port: 1521,
            db_name: 'ORCLPDB1',
            db_user: 'sys',
            db_password: 'password',
            db_driver: 'oracledb',
          },
          netezza_dw: {
            db_type: 'netezza',
            db_host: '127.0.0.1',
            db_port: 5480,
            db_name: 'testdb',
            db_user: 'admin',
            db_password: 'password',
            db_driver: 'nzpy',
          },
        },
      });
      configId = cfg.id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      if (configId) await deleteConfig(ctx, configId);
    } finally {
      await ctx.dispose();
    }
  });

  test('opens Schema Explorer on Config tab and lists live SQL Server schemas and tables', async ({ authedPage }) => {
    await authedPage.goto('/#config');
    await authedPage.locator('[data-testid="nav-tab-config"]').click();

    // Find the config card row for our created config
    const configRow = authedPage.locator(`[data-testid="config-row-${configId}-edit-btn"]`).locator('xpath=ancestor::div[contains(@class, "card")]');
    await expect(configRow).toBeVisible();

    // Click Browse Schema
    const browseBtn = configRow.locator('button:has-text("Browse Schema")');
    await browseBtn.click();

    // Verify Schema Explorer Panel opens
    const panel = authedPage.locator('[data-testid="schema-explorer-panel"]');
    await expect(panel).toBeVisible({ timeout: 15_000 });

    // Verify Data Source connection selector is visible and has options for each data source connection
    const connSelect = authedPage.locator('[data-testid="schema-explorer-connection-select"]');
    await expect(connSelect).toBeVisible();

    const options = await connSelect.locator('option').allInnerTexts();
    expect(options.some(o => o.includes('Main DB') && o.includes('mssql'))).toBe(true);
    expect(options.some(o => o.includes('analytics_dw') && o.includes('oracle'))).toBe(true);
    expect(options.some(o => o.includes('netezza_dw') && o.includes('netezza'))).toBe(true);

    // Verify schemas are rendered for live SQL Server (e.g. dbo or sys or INFORMATION_SCHEMA)
    await expect(panel).toContainText('tables', { timeout: 15_000 });
  });

  test('can switch between data source connections in Schema Explorer dropdown', async ({ authedPage }) => {
    await authedPage.goto('/#config');
    await authedPage.locator('[data-testid="nav-tab-config"]').click();

    const configRow = authedPage.locator(`[data-testid="config-row-${configId}-edit-btn"]`).locator('xpath=ancestor::div[contains(@class, "card")]');
    await configRow.locator('button:has-text("Browse Schema")').click();

    const connSelect = authedPage.locator('[data-testid="schema-explorer-connection-select"]');
    await expect(connSelect).toBeVisible();

    // Switch connection to oracle analytics_dw
    await connSelect.selectOption('analytics_dw');
    await authedPage.waitForTimeout(500);

    // Expect dropdown selection to stay on analytics_dw
    await expect(connSelect).toHaveValue('analytics_dw');

    // Close schema explorer
    await authedPage.locator('[data-testid="schema-explorer-close-btn"]').click();
    await expect(authedPage.locator('[data-testid="schema-explorer-panel"]')).toBeHidden();
  });
});
