import { test, expect } from './fixtures';
import path from 'node:path';
import { createConfig, deleteConfig, authedContext } from './api-helpers';

const dataFile = (name: string) => path.join(__dirname, 'fixtures', 'data', name);

test.describe('34 netezza compare with CSV and Excel', () => {
  let netezzaConfigId: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-netezza-${Date.now()}`, 'dev', {
        db_type: 'netezza',
        db_driver: 'nzpy',
        db_host: '127.0.0.1',
        db_port: 5480,
        db_name: 'testdb',
        db_user: 'admin',
        db_password: 'password',
      });
      netezzaConfigId = cfg.id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      if (netezzaConfigId) await deleteConfig(ctx, netezzaConfigId);
    } finally {
      await ctx.dispose();
    }
  });

  test('config modal correctly renders and saves Netezza DB Type and port 5480', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-config"]').click();
    await authedPage.locator('[data-testid="config-new-btn"]').click();

    await authedPage.locator('[data-testid="config-modal-db-type-select"]').selectOption('netezza');
    await expect(authedPage.locator('[data-testid="config-modal-db-port-input"]')).toHaveValue('5480');
    await expect(authedPage.locator('[data-testid="config-modal-db-driver-input"]')).toHaveValue('nzpy');
  });

  test('reconciliation compare with CSV and Excel file options', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-compare"]').click();
    await authedPage.locator('[data-testid="compare-subtab-recon"]').click();

    await authedPage.locator('[data-testid="compare-recon-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-file-source-a-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-file-source-a-upload-input"]').setInputFiles(dataFile('source.csv'));
    await authedPage.locator('[data-testid="compare-file-source-b-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-file-source-b-upload-input"]').setInputFiles(dataFile('source.xlsx'));

    await authedPage.locator('[data-testid="compare-file-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-file-results"]')).toContainText('Results', { timeout: 20_000 });
  });

  test('SQL comparison tab accepts Netezza config source selection', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-compare"]').click();
    await authedPage.locator('[data-testid="compare-subtab-sql"]').click();

    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(netezzaConfigId));
    await expect(authedPage.locator('[data-testid="compare-sql-config-a-select"]')).toHaveValue(String(netezzaConfigId));
  });
});
