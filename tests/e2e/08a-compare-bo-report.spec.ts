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

  test('live source prompts load into the UI and reach the compare request', async ({ authedPage, adminToken }) => {
    // Proves the whole prompt path is wired in the browser: picking a document
    // fetches the document's parameters, renders them as editable rows, and the
    // edited answers ride along in POST /api/compare/bo-report. Previously the
    // live source sent no bo_parameters at all, so a run-date prompt was ignored.
    const ctx = await authedContext(adminToken);
    let cfgId: number;
    try {
      const cfg = await createConfig(ctx, `e2e-bo-prompts-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        bo_url: 'http://127.0.0.1:1', bo_user: 'u', bo_password: 'p',
      });
      cfgId = cfg.id;
    } finally {
      await ctx.dispose();
    }

    const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    await authedPage.route('**/api/adapters/sap-bo/documents?**', (r) =>
      r.fulfill(json([{ id: '9001', name: 'Sales Orders', folder: 'Public' }])));
    await authedPage.route('**/api/adapters/sap-bo/documents/9001/reports**', (r) =>
      r.fulfill(json([{ id: '2', name: 'Orders', reportIndex: 0 }])));
    await authedPage.route('**/api/adapters/sap-bo/documents/9001/parameters**', (r) =>
      r.fulfill(json([
        { id: 5, name: 'Run Date', type: 'DateTime', mandatory: true, default: '' },
        { id: 6, name: 'Region', type: 'Text', mandatory: false, default: 'EMEA' },
      ])));

    let posted: any = null;
    await authedPage.route('**/api/compare/bo-report', (r) => {
      posted = JSON.parse(r.request().postData() || '{}');
      return r.fulfill(json({ run_id: 'stub-run', status: 'PENDING' }));
    });
    await authedPage.route('**/api/runs/stub-run/status', (r) =>
      r.fulfill(json({ run_id: 'stub-run', status: 'PASSED', total_tests: 0 })));

    await openBO(authedPage);
    await authedPage.locator('[data-testid="compare-bo-source-a-mode-live"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-a-config-select"]').selectOption(String(cfgId!));
    await authedPage.locator('[data-testid="compare-bo-source-a-doc-select"]').selectOption('9001');
    await authedPage.locator('[data-testid="compare-bo-source-a-report-select"]').selectOption('2');

    // Discovered prompts render, named, with a date picker for the DateTime one.
    const promptRows = authedPage.locator('[data-testid="compare-bo-source-a-params"]');
    await expect(promptRows).toBeVisible();
    await expect(promptRows).toContainText('Run Date');
    await expect(promptRows).toContainText('Region');
    await promptRows.locator('input[type="date"]').fill('2026-06-02');
    await expect(promptRows.locator('input[type="text"]')).toHaveValue('EMEA');

    await authedPage.locator('[data-testid="compare-bo-source-b-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-b-upload-input"]').setInputFiles(dataFile('target.csv'));
    await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();

    await expect.poll(() => posted !== null).toBe(true);
    expect(posted.source_a.bo_parameters).toEqual([
      { id: 5, type: 'DateTime', value: '2026-06-02' },
      { id: 6, type: 'Text', value: 'EMEA' },
    ]);

    const cleanup = await authedContext(adminToken);
    try { await deleteConfig(cleanup, cfgId!); } finally { await cleanup.dispose(); }
  });

  /** A live BO source wired to stubs, so the report select's own behaviour can
   *  be asserted without a BO server. Returns the config to clean up. */
  async function stubbedLiveSource(page: import('@playwright/test').Page, adminToken: string) {
    const ctx = await authedContext(adminToken);
    let cfgId: number;
    try {
      const cfg = await createConfig(ctx, `e2e-bo-alltabs-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        bo_url: 'http://127.0.0.1:1', bo_user: 'u', bo_password: 'p',
      });
      cfgId = cfg.id;
    } finally {
      await ctx.dispose();
    }

    const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    await page.route('**/api/adapters/sap-bo/documents?**', (r) =>
      r.fulfill(json([{ id: '9001', name: 'Sales Orders', folder: 'Public' }])));
    await page.route('**/api/adapters/sap-bo/documents/9001/reports**', (r) =>
      r.fulfill(json([
        { id: '2', name: 'Orders', reportIndex: 0 },
        { id: '3', name: 'Summary', reportIndex: 1 },
      ])));
    await page.route('**/api/adapters/sap-bo/documents/9001/parameters**', (r) => r.fulfill(json([])));
    return cfgId!;
  }

  test('All tabs sends an empty report_id, not the "*" the select holds', async ({ authedPage, adminToken }) => {
    // '*' is a UI sentinel: the API expresses "whole document" as an empty
    // report_id, and leaking the sentinel through would name a tab called '*'.
    const cfgId = await stubbedLiveSource(authedPage, adminToken);

    const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    let posted: any = null;
    await authedPage.route('**/api/compare/bo-report', (r) => {
      posted = JSON.parse(r.request().postData() || '{}');
      return r.fulfill(json({ run_id: 'stub-run', status: 'PENDING' }));
    });
    await authedPage.route('**/api/runs/stub-run/status', (r) =>
      r.fulfill(json({ run_id: 'stub-run', status: 'PASSED', total_tests: 0 })));

    await openBO(authedPage);
    await authedPage.locator('[data-testid="compare-bo-source-a-mode-live"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-a-config-select"]').selectOption(String(cfgId));
    await authedPage.locator('[data-testid="compare-bo-source-a-doc-select"]').selectOption('9001');
    await authedPage.locator('[data-testid="compare-bo-source-a-report-select"]').selectOption('*');

    await authedPage.locator('[data-testid="compare-bo-source-b-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-b-upload-input"]').setInputFiles(dataFile('target.csv'));
    await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();

    await expect.poll(() => posted !== null).toBe(true);
    expect(posted.source_a.report_id).toBe('');

    const cleanup = await authedContext(adminToken);
    try { await deleteConfig(cleanup, cfgId); } finally { await cleanup.dispose(); }
  });

  test('an untouched report select is rejected rather than pulling the whole document', async ({ authedPage, adminToken }) => {
    // Allowing a missing report to mean "whole document" server-side made
    // forgetting to pick one silently export every tab. The blank select has to
    // stay an error the user is told about.
    const cfgId = await stubbedLiveSource(authedPage, adminToken);

    let posted = false;
    await authedPage.route('**/api/compare/bo-report', (r) => {
      posted = true;
      return r.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });

    await openBO(authedPage);
    await authedPage.locator('[data-testid="compare-bo-source-a-mode-live"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-a-config-select"]').selectOption(String(cfgId));
    await authedPage.locator('[data-testid="compare-bo-source-a-doc-select"]').selectOption('9001');
    // Report select deliberately left alone.

    await authedPage.locator('[data-testid="compare-bo-source-b-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-b-upload-input"]').setInputFiles(dataFile('target.csv'));
    await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();

    await expect(authedPage.getByText('Select a report')).toBeVisible();
    expect(posted).toBe(false);

    const cleanup = await authedContext(adminToken);
    try { await deleteConfig(cleanup, cfgId); } finally { await cleanup.dispose(); }
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

    test('live Source A vs path Source B', async ({ authedPage }) => {
      // Source B as a server-side file path (api/services/compare_service.py's
      // _load_bo_source() falls through to read_tabular(path=...) for anything
      // that isn't 'live'/'api') was untested against a real live BO pull --
      // every other live-BO-mock case here goes through upload instead. Requires
      // playwright.config.ts's SERVER_FILE_ALLOWED_DIRS to include fixtures/data.
      await openBO(authedPage);
      await authedPage.locator('[data-testid="compare-bo-source-a-mode-live"]').click();
      await authedPage.locator('[data-testid="compare-bo-source-a-config-select"]').selectOption(String(boConfigId));
      await authedPage.locator('[data-testid="compare-bo-source-a-doc-select"]').selectOption({ label: 'Sales Orders' });
      await authedPage.locator('[data-testid="compare-bo-source-a-report-select"]').selectOption({ label: 'Orders' });
      await authedPage.locator('[data-testid="compare-bo-source-b-mode-path"]').click();
      await authedPage.getByPlaceholder('C:\\reports\\b.csv').fill(dataFile('source.csv'));
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
