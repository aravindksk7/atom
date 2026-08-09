import { test, expect } from './fixtures';
import { createConfig, deleteConfig, authedContext } from './api-helpers';

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

test.describe('29 adapters: missing-mandatory-prompt client-side guard', () => {
  test('downloading a report with a blank mandatory prompt is blocked with a named toast, not sent to the server', async ({ authedPage, adminToken }) => {
    // downloadBOReport() (frontend/features/adapters.js) checks params.filter(p =>
    // p.mandatory && !value) BEFORE calling the parameterized download endpoint --
    // previously untested, so a regression here would only surface as SAP BO's own
    // generic 502 for a missing prompt instead of this named client-side guard.
    const ctx = await authedContext(adminToken);
    let cfgId: number;
    try {
      const cfg = await createConfig(ctx, `e2e-missing-prompt-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        bo_url: 'http://127.0.0.1:1', bo_user: 'u', bo_password: 'p',
      });
      cfgId = cfg.id;
    } finally {
      await ctx.dispose();
    }

    await authedPage.route('**/api/adapters/sap-bo/documents?**', (r) =>
      r.fulfill(json([{ id: '9001', name: 'Sales Orders', folder: 'Public' }])));
    await authedPage.route('**/api/adapters/sap-bo/documents/9001/reports**', (r) =>
      r.fulfill(json([{ id: '2', name: 'Orders', reportIndex: 0 }])));
    // A DateTime prompt auto-seeds to today's date (defaultBOParamValues()), so a
    // Text-type prompt is used here to actually reproduce a blank mandatory value.
    await authedPage.route('**/api/adapters/sap-bo/documents/9001/parameters**', (r) =>
      r.fulfill(json([{ id: 5, name: 'Region', type: 'Text', mandatory: true, default: '' }])));

    let downloadRequested = false;
    await authedPage.route('**/api/adapters/sap-bo/documents/9001/reports/2/download**', (r) => {
      downloadRequested = true;
      return r.continue();
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(cfgId!));
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();

    const doc = authedPage.locator('.bo-doc-item', { hasText: 'Sales Orders' });
    await expect(doc).toBeVisible();
    await doc.click();

    // Leave the mandatory "Run Date" prompt blank and try to download anyway.
    const repRow = authedPage.locator('.bo-report-item', { hasText: 'Orders' });
    await expect(repRow).toBeVisible();
    await repRow.getByText('XLSX', { exact: true }).click();

    // loadBODocuments() already fired its own (toast-success) "N documents loaded"
    // toast, which may still be visible -- scope to .toast-error to avoid a
    // strict-mode violation on the ambiguous plain .toast-title locator.
    await expect(authedPage.locator('.toast-error .toast-title')).toContainText('Missing required prompts');
    await expect(authedPage.locator('.toast-error .toast-msg')).toContainText('Region');
    expect(downloadRequested).toBe(false);

    const cleanup = await authedContext(adminToken);
    try { await deleteConfig(cleanup, cfgId!); } finally { await cleanup.dispose(); }
  });
});

test.describe('29b adapters: ran-on date filter', () => {
  test('a supported server narrows the document list to those that ran on the picked date', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let cfgId: number;
    try {
      const cfg = await createConfig(ctx, `e2e-ran-on-${Date.now()}`, 'dev', {
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
    await authedPage.route('**/api/adapters/sap-bo/documents/ran-on**', (r) =>
      r.fulfill(json({ supported: true, document_ids: ['9001'] })));

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(cfgId!));
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();

    await expect(authedPage.locator('.bo-doc-item', { hasText: 'Sales Orders' })).toBeVisible();
    await expect(authedPage.locator('.bo-doc-item', { hasText: 'Inventory Snapshot' })).toBeVisible();

    await authedPage.locator('[data-testid="bo-doc-ran-on-input"]').fill('2026-06-02');

    await expect(authedPage.locator('.bo-doc-item', { hasText: 'Sales Orders' })).toBeVisible();
    await expect(authedPage.locator('.bo-doc-item', { hasText: 'Inventory Snapshot' })).toBeHidden();

    const cleanup = await authedContext(adminToken);
    try { await deleteConfig(cleanup, cfgId!); } finally { await cleanup.dispose(); }
  });

  test('a server that does not support run-date filtering shows the unsupported message', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let cfgId: number;
    try {
      const cfg = await createConfig(ctx, `e2e-ran-on-unsupported-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        bo_url: 'http://127.0.0.1:1', bo_user: 'u', bo_password: 'p',
      });
      cfgId = cfg.id;
    } finally {
      await ctx.dispose();
    }

    await authedPage.route('**/api/adapters/sap-bo/documents?**', (r) =>
      r.fulfill(json([{ id: '9001', name: 'Sales Orders', folder: 'Public' }])));
    await authedPage.route('**/api/adapters/sap-bo/documents/ran-on**', (r) =>
      r.fulfill(json({ supported: false, document_ids: [] })));

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(cfgId!));
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();
    await authedPage.locator('[data-testid="bo-doc-ran-on-input"]').fill('2026-06-02');

    await expect(authedPage.getByText("Run-date filtering isn't available against this SAP BO server.")).toBeVisible();
    // Unsupported means the filter doesn't narrow anything -- the document stays visible.
    await expect(authedPage.locator('.bo-doc-item', { hasText: 'Sales Orders' })).toBeVisible();

    const cleanup = await authedContext(adminToken);
    try { await deleteConfig(cleanup, cfgId!); } finally { await cleanup.dispose(); }
  });
});
