import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig } from './api-helpers';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import type { Page } from '@playwright/test';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';

async function withTempDir<T>(prefix: string, fn: (dir: string) => Promise<T>): Promise<T> {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), prefix));
  try {
    return await fn(dir);
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
}

test.describe('23 BO download server-side copy settings', () => {
  test.beforeEach(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const resp = await ctx.put('/api/settings', { data: { bo_download_dir: '' } });
      expect(resp.ok()).toBeTruthy();
    } finally {
      await ctx.dispose();
    }
  });

  const downloadDirCard = (page: Page) => page.locator('[data-testid="bo-download-dir-card"]');

  test('Config tab saves, reloads, and clears the SAP BO download directory', async ({ authedPage }) => {
    await withTempDir('atom-bo-copy-ui-', async (downloadDir) => {
      await authedPage.goto('/');
      await authedPage.locator('[data-testid="nav-tab-config"]').click();

      const card = downloadDirCard(authedPage);
      await card.locator('[data-testid="bo-download-dir-toggle"]').click();
      const input = card.locator('[data-testid="bo-download-dir-input"]');
      const save = card.locator('[data-testid="bo-download-dir-save-btn"]');

      await expect(input).toHaveValue('');
      await input.fill(downloadDir);
      await save.click();
      await expect(authedPage.locator('.toast-title')).toContainText('Download directory updated');
      await expect(authedPage.locator('.toast-msg')).toContainText(downloadDir);
      await expect(input).toHaveValue(downloadDir);

      await authedPage.reload();
      await expect(authedPage.locator('[data-testid="auth-status-connected"]')).toBeVisible();
      await authedPage.locator('[data-testid="nav-tab-config"]').click();
      const reloadedCard = downloadDirCard(authedPage);
      await reloadedCard.locator('[data-testid="bo-download-dir-toggle"]').click();
      await expect(reloadedCard.locator('[data-testid="bo-download-dir-input"]')).toHaveValue(downloadDir);

      await reloadedCard.locator('[data-testid="bo-download-dir-input"]').fill('');
      await reloadedCard.locator('[data-testid="bo-download-dir-save-btn"]').click();
      await expect(authedPage.locator('.toast-msg')).toContainText('browser only');
      await expect(reloadedCard.locator('[data-testid="bo-download-dir-input"]')).toHaveValue('');
    });
  });

  test('Config tab rejects a relative SAP BO download directory without losing the draft', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-config"]').click();
    const card = downloadDirCard(authedPage);
    await card.locator('[data-testid="bo-download-dir-toggle"]').click();

    const input = card.locator('[data-testid="bo-download-dir-input"]');
    await input.fill('reports/sapbo');
    await card.locator('[data-testid="bo-download-dir-save-btn"]').click();

    await expect(authedPage.locator('.toast-title')).toContainText('Failed to update download directory');
    await expect(authedPage.locator('.toast-msg')).toContainText('absolute');
    await expect(input).toHaveValue('reports/sapbo');
  });

  test('download success toast includes the server-side saved path', async ({ authedPage }) => {
    await authedPage.route('**/api/configs', async (route) => {
      await route.fulfill({ json: [{ id: 901, name: 'e2e-bo-copy', env_name: 'dev', config_data: {} }] });
    });
    await authedPage.route('**/api/adapters/sap-bo/documents?config_id=901', async (route) => {
      await route.fulfill({ json: [{ id: 'doc-1', name: 'Archive Smoke', folder: 'E2E' }] });
    });
    await authedPage.route('**/api/adapters/sap-bo/documents/doc-1/parameters?config_id=901', async (route) => {
      await route.fulfill({ json: [] });
    });
    await authedPage.route('**/api/adapters/sap-bo/documents/doc-1/reports?config_id=901', async (route) => {
      await route.fulfill({ json: [{ id: 'tab-1', name: 'Archive Tab', reportIndex: 0 }] });
    });
    await authedPage.route('**/api/adapters/sap-bo/documents/doc-1/reports/tab-1/download?config_id=901&format=xlsx', async (route) => {
      await route.fulfill({
        status: 200,
        body: 'xlsx bytes',
        headers: {
          'content-type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          'content-disposition': 'attachment; filename="archive-tab.xlsx"',
          'x-saved-path': encodeURIComponent('C:\\archive\\archive-tab.xlsx'),
        },
      });
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption('901');
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();
    await authedPage.locator('.bo-doc-item', { hasText: 'Archive Smoke' }).click();

    const downloadPromise = authedPage.waitForEvent('download');
    await authedPage.locator('.bo-report-item', { hasText: 'Archive Tab' }).getByRole('button', { name: 'XLSX' }).click();
    expect((await downloadPromise).suggestedFilename()).toBe('archive-tab.xlsx');

    await expect(authedPage.locator('.toast-title').filter({ hasText: 'Download started' })).toBeVisible();
    await expect(authedPage.locator('.toast-msg').filter({ hasText: 'Also saved to C:\\archive\\archive-tab.xlsx' })).toBeVisible();
  });

  test('download still starts and shows a separate error when the server-side copy fails', async ({ authedPage }) => {
    await authedPage.route('**/api/configs', async (route) => {
      await route.fulfill({ json: [{ id: 902, name: 'e2e-bo-copy-failure', env_name: 'dev', config_data: {} }] });
    });
    await authedPage.route('**/api/adapters/sap-bo/documents?config_id=902', async (route) => {
      await route.fulfill({ json: [{ id: 'doc-2', name: 'Archive Failure Smoke', folder: 'E2E' }] });
    });
    await authedPage.route('**/api/adapters/sap-bo/documents/doc-2/parameters?config_id=902', async (route) => {
      await route.fulfill({ json: [] });
    });
    await authedPage.route('**/api/adapters/sap-bo/documents/doc-2/reports?config_id=902', async (route) => {
      await route.fulfill({ json: [{ id: 'tab-2', name: 'Archive Failure Tab', reportIndex: 0 }] });
    });
    await authedPage.route('**/api/adapters/sap-bo/documents/doc-2/reports/tab-2/download?config_id=902&format=xlsx', async (route) => {
      await route.fulfill({
        status: 200,
        body: 'xlsx bytes',
        headers: {
          'content-type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          'content-disposition': 'attachment; filename="archive-failure-tab.xlsx"',
          'x-save-error': encodeURIComponent('network share unavailable'),
        },
      });
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption('902');
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();
    await authedPage.locator('.bo-doc-item', { hasText: 'Archive Failure Smoke' }).click();

    const downloadPromise = authedPage.waitForEvent('download');
    await authedPage.locator('.bo-report-item', { hasText: 'Archive Failure Tab' }).getByRole('button', { name: 'XLSX' }).click();
    expect((await downloadPromise).suggestedFilename()).toBe('archive-failure-tab.xlsx');

    await expect(authedPage.locator('.toast-title').filter({ hasText: 'Download started' })).toBeVisible();
    await expect(authedPage.locator('.toast-title').filter({ hasText: 'Server copy failed' })).toBeVisible();
    await expect(authedPage.locator('.toast-msg').filter({ hasText: 'network share unavailable' })).toBeVisible();
  });
});

test.describe('23 BO download server-side copy with SAP BO mock', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml sapbo)');

  let boConfigId: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-bo-server-copy-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        bo_url: 'https://127.0.0.1:18443', bo_user: 'administrator', bo_password: 'Password1',
        bo_verify_ssl: false,
      });
      boConfigId = cfg.id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      await deleteConfig(ctx, boConfigId);
      await ctx.put('/api/settings', { data: { bo_download_dir: '' } });
    } finally {
      await ctx.dispose();
    }
  });

  test('SAP BO browser download is also written to the configured server directory', async ({ authedPage, adminToken }) => {
    await withTempDir('atom-bo-copy-live-', async (downloadDir) => {
      const ctx = await authedContext(adminToken);
      try {
        const settings = await ctx.put('/api/settings', { data: { bo_download_dir: downloadDir } });
        expect(settings.ok()).toBeTruthy();

        await authedPage.goto('/');
        await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
        await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(boConfigId));
        await authedPage.getByRole('button', { name: 'Browse Documents' }).click();
        await authedPage.locator('.bo-doc-item', { hasText: 'Inventory Snapshot' }).click();

        const responsePromise = authedPage.waitForResponse(r => r.url().includes('/documents/1002/reports/rpt-inventory/download'));
        const downloadPromise = authedPage.waitForEvent('download');
        await authedPage.locator('.bo-report-item', { hasText: 'Inventory' }).getByRole('button', { name: 'XLSX' }).click();
        const response = await responsePromise;
        const download = await downloadPromise;

        expect(response.status()).toBe(200);
        expect(download.suggestedFilename()).toBe('report_1002_rpt-inventory.xlsx');
        const savedPath = decodeURIComponent(response.headers()['x-saved-path'] || '');
        expect(path.dirname(savedPath)).toBe(downloadDir);
        expect(path.basename(savedPath)).toMatch(/report_1002_rpt-inventory_\d{8}T\d{6}Z\.xlsx$/);
        await expect(authedPage.locator('.toast-msg').filter({ hasText: `Also saved to ${savedPath}` })).toBeVisible();

        const archived = await fs.readFile(savedPath);
        expect(archived.length).toBeGreaterThan(0);
        expect(await fs.readdir(downloadDir)).toHaveLength(1);
      } finally {
        await ctx.put('/api/settings', { data: { bo_download_dir: '' } }).catch(() => undefined);
        await ctx.dispose();
      }
    });
  });
});
