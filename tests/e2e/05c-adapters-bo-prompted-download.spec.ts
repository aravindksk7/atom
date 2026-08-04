import { test, expect } from './fixtures';
import type { Download, Page } from '@playwright/test';
import { createConfig, deleteConfig, authedContext } from './api-helpers';
import { inflateRawSync } from 'node:zlib';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';

/**
 * The prompted SAP BO download, driven through the browser.
 *
 * tests/integration/test_sapbo_ui_download_flow.py already proves the API
 * route → AdapterService → BORestClient → server chain. This adds the only
 * layer that test cannot reach: frontend/features/adapters.js — the prompt
 * discovery on expand, the date picker, the values it collects, and the POST
 * it builds from them.
 *
 * The assertion is on the workbook's cell text, never on the download
 * succeeding: the 2026-08-04 failure downloaded a perfectly valid xlsx that
 * contained the report layout and no data rows.
 *
 * Document 1003 in docker/sapbo-mock serves different rows per answered date,
 * so a download that dropped or ignored the prompt cannot pass this.
 *
 * If every assertion here fails with an empty download (bytes=0) or EPERM,
 * something on the machine is locking Playwright's artifact directory inside
 * the repo — rerun with artifacts elsewhere: `--output C:/tmp/pw-artifacts`.
 */

/** Read one file out of a zip (stored or deflated), without a zip library. */
function readZipEntry(zip: Buffer, name: string): string {
  const target = Buffer.from(name, 'utf-8');
  for (let i = 0; i + 30 < zip.length; i++) {
    if (zip.readUInt32LE(i) !== 0x04034b50) continue;      // local file header
    const method = zip.readUInt16LE(i + 8);
    const compressedSize = zip.readUInt32LE(i + 18);
    const nameLength = zip.readUInt16LE(i + 26);
    const extraLength = zip.readUInt16LE(i + 28);
    const nameStart = i + 30;
    if (!zip.subarray(nameStart, nameStart + nameLength).equals(target)) continue;
    const dataStart = nameStart + nameLength + extraLength;
    const data = zip.subarray(dataStart, dataStart + compressedSize);
    return (method === 0 ? data : inflateRawSync(data)).toString('utf-8');
  }
  throw new Error(
    `zip entry not found: ${name} (bytes=${zip.length}, ` +
    `head=${zip.subarray(0, 8).toString('hex')}, ` +
    `text=${JSON.stringify(zip.subarray(0, 200).toString('utf-8'))})`);
}

/** The workbook the browser actually downloaded.
 *
 *  Streamed, not copied: Chromium does not keep a download's body available to
 *  response.body(), and saveAs()/path() hit EPERM against Playwright's own
 *  artifact directory on this platform. */
async function sheetOf(download: Download): Promise<string> {
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(chunk as Buffer);
  return readZipEntry(Buffer.concat(chunks), 'xl/worksheets/sheet1.xml');
}

/** Matches whichever download endpoint the UI chose — POST …/download for a
 *  prompted report, GET …/download for one with no prompts. */
const downloadResponse = (page: Page, reportId: string) =>
  page.waitForResponse(r => r.url().includes(`/reports/${reportId}/download`));

/** Both prompt inputs share one id — Alpine's x-show only hides one of them —
 *  so the type has to disambiguate. */
const dateInput = (docId: string, idx: number) => `input#bo-report-prompt-${docId}-${idx}[type="date"]`;
const textInput = (docId: string, idx: number) => `input#bo-report-prompt-${docId}-${idx}[type="text"]`;

test.describe('05c adapters - prompted SAP BO download from the UI', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml sapbo)');

  let boConfigId: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-adapters-bo-prompted-${Date.now()}`, 'dev', {
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
    } finally {
      await ctx.dispose();
    }
  });

  test('answering the date prompt exports a workbook containing that day\'s rows', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(boConfigId));
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();

    // Expand the prompted document — this is what triggers prompt discovery.
    // The document list arrives over a live BO session, so give the first one
    // more than the default assertion timeout.
    const doc = authedPage.locator('.bo-doc-item', { hasText: 'Daily Sales (prompted)' });
    await expect(doc).toBeVisible({ timeout: 20_000 });
    await doc.click();

    // Prompt 0 is the DateTime one — it must render as a date picker, which is
    // also what makes the local→UTC conversion fire server-side.
    const date = authedPage.locator(dateInput('1003', 0));
    await expect(date).toBeVisible();
    await date.fill('2026-06-03');
    await authedPage.locator(textInput('1003', 1)).fill('ASX');

    const report = authedPage.locator('.bo-report-item', { hasText: 'Daily Orders' });
    const downloadPromise = authedPage.waitForEvent('download');
    const responsePromise = downloadResponse(authedPage, 'rpt-daily-sales');
    await report.getByRole('button', { name: 'XLSX' }).click();
    const response = await responsePromise;

    expect(response.status()).toBe(200);
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe('report_1003_rpt-daily-sales.xlsx');
    const sheet = await sheetOf(download);

    // The answered day's rows, and not the other day's.
    expect(sheet).toContain('D400');
    expect(sheet).toContain('E500');
    expect(sheet).not.toContain('A100');
  });

  test('a different answered date exports different rows', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(boConfigId));
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();

    const doc = authedPage.locator('.bo-doc-item', { hasText: 'Daily Sales (prompted)' });
    await expect(doc).toBeVisible({ timeout: 20_000 });
    await doc.click();
    await authedPage.locator(dateInput('1003', 0)).fill('2026-06-02');
    await authedPage.locator(textInput('1003', 1)).fill('ASX');

    const downloadPromise = authedPage.waitForEvent('download');
    await authedPage.locator('.bo-report-item', { hasText: 'Daily Orders' })
      .getByRole('button', { name: 'XLSX' }).click();
    const sheet = await sheetOf(await downloadPromise);

    expect(sheet).toContain('A100');
    expect(sheet).not.toContain('D400');
  });

  test('All tabs exports every tab of the document in one workbook', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(boConfigId));
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();

    // Document 1001 is the multi-tab one (Orders + Summary). A per-tab export
    // can only ever carry one of those row sets, so asserting both is the only
    // thing that distinguishes a whole-document export from a single-tab one.
    const doc = authedPage.locator('.bo-doc-item', { hasText: 'Sales Orders' });
    await expect(doc).toBeVisible({ timeout: 20_000 });
    await doc.click();
    await authedPage.locator(dateInput('1001', 0)).fill('2026-06-03');

    const allTabs = authedPage.locator('.bo-report-item', { hasText: 'All tabs' });
    await expect(allTabs).toBeVisible();
    const downloadPromise = authedPage.waitForEvent('download');
    const responsePromise = authedPage.waitForResponse(
      r => /\/documents\/1001\/download/.test(r.url()));
    await allTabs.getByRole('button', { name: 'XLSX' }).click();
    const response = await responsePromise;

    expect(response.status()).toBe(200);
    // The whole-document route carries no /reports/ segment — that segment is
    // what names a single tab.
    expect(response.url()).not.toContain('/reports/');
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe('report_1001.xlsx');

    const sheet = await sheetOf(download);
    expect(sheet).toContain('A100');     // Orders tab
    expect(sheet).toContain('orders');   // Summary tab
  });

  test('All tabs is hidden while a report filter is active', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(boConfigId));
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();

    const doc = authedPage.locator('.bo-doc-item', { hasText: 'Sales Orders' });
    await expect(doc).toBeVisible({ timeout: 20_000 });
    await doc.click();

    const allTabs = authedPage.locator('.bo-report-item', { hasText: 'All tabs' });
    await expect(allTabs).toBeVisible();

    // "All tabs" ignores the filter by definition, so next to a filtered list
    // it would read as a claim about what is on screen.
    const filter = authedPage.getByPlaceholder('Search documents');
    await filter.fill('Summary');
    await expect(allTabs).toBeHidden();

    await filter.fill('');
    await expect(allTabs).toBeVisible();
  });

  test('All tabs + Job creates a job with no tab named', async ({ authedPage, adminToken }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(boConfigId));
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();

    const doc = authedPage.locator('.bo-doc-item', { hasText: 'Sales Orders' });
    await expect(doc).toBeVisible({ timeout: 20_000 });
    await doc.click();

    await authedPage.locator('.bo-report-item', { hasText: 'All tabs' })
      .getByRole('button', { name: '+ Job' }).click();
    await authedPage.getByRole('button', { name: 'Save Job' }).click();

    // The job's scope lives in its params, not its name: an empty bo_report_id
    // is what makes the run export every tab.
    const ctx = await authedContext(adminToken);
    try {
      const jobs = await (await ctx.get('/api/jobs')).json();
      const created = jobs.find((j: any) => j.name === 'bo_1001_all');
      expect(created).toBeTruthy();
      expect(created.params.report_id).toBe('1001');
      expect(created.params.bo_report_id).toBe('');
      await ctx.delete('/api/jobs/bo_1001_all');
    } finally {
      await ctx.dispose();
    }
  });

  test('a document with no prompts still downloads', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(boConfigId));
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();

    // Document 1002 has no prompts, so the UI takes the plain GET path with no
    // answer PUT before it — the export has no occurrence refresh to rely on.
    const doc = authedPage.locator('.bo-doc-item', { hasText: 'Inventory Snapshot' });
    await expect(doc).toBeVisible({ timeout: 20_000 });
    await doc.click();
    const downloadPromise = authedPage.waitForEvent('download');
    await authedPage.locator('.bo-report-item', { hasText: 'Inventory' })
      .getByRole('button', { name: 'XLSX' }).click();
    const sheet = await sheetOf(await downloadPromise);

    expect(sheet).toContain('A100');
  });
});
