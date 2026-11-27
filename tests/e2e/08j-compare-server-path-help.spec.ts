// tests/e2e/08j-compare-server-path-help.spec.ts
//
// A server-side report path outside SERVER_FILE_ALLOWED_DIRS opens a persistent
// setup panel (folder, exact command, restart steps, upload alternative)
// instead of a toast that fades before it can be read.
import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';
import os from 'node:os';
import path from 'node:path';

// The e2e server only allows tests/e2e/fixtures/data (playwright.config.ts),
// C:\temp and reports/uploads; the OS temp folder is outside all of them.
const OUTSIDE_DIR = path.join(os.tmpdir(), 'atom-e2e-not-allowed', 'BO Reports');
const IS_WINDOWS = process.platform === 'win32';

async function openBO(page: Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-bo"]').click();
}

async function expectGuidance(page: Page, reportPath: string) {
  const panel = page.locator('[data-testid="server-path-help"]');
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('[data-testid="server-path-help-message"]')).toContainText(
    `Invalid file path: ${reportPath} is outside the folders this server may read`,
  );
  await expect(panel).toContainText(OUTSIDE_DIR);
  await expect(panel).toContainText('restarts the API');
  await expect(panel).toContainText('Upload');

  // The report folder comes first, followed by the folders the server already
  // has in SERVER_FILE_ALLOWED_DIRS, so applying the command loses nothing.
  const command = page.locator('[data-testid="server-path-help-command"]');
  const configured = path.join(__dirname, 'fixtures', 'data');
  const expected = IS_WINDOWS
    ? `$env:SERVER_FILE_ALLOWED_DIRS = '${OUTSIDE_DIR};${configured}'`
    : `export SERVER_FILE_ALLOWED_DIRS='${OUTSIDE_DIR}:${configured}'`;
  await expect(command).toHaveText(expected);

  // The raw error is not shown as a fading toast as well.
  await expect(page.locator('.toast-stack')).not.toContainText('Invalid file path');
  return panel;
}

test.describe('08j compare / server file path setup help', () => {
  test('BO compare with an xlsx path outside the allowed dirs shows setup steps', async ({ authedPage }) => {
    const reportA = path.join(OUTSIDE_DIR, 'qa_sales.xlsx');
    const reportB = path.join(OUTSIDE_DIR, 'prod_sales.xlsx');
    await openBO(authedPage);
    await authedPage.locator('[data-testid="compare-bo-source-a-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-a-path-input"]').fill(reportA);
    await authedPage.locator('[data-testid="compare-bo-source-b-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-b-path-input"]').fill(reportB);
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();

    const panel = await expectGuidance(authedPage, reportA);

    // Copy puts the exact command on the clipboard.
    await authedPage.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    await authedPage.locator('[data-testid="server-path-help-copy"]').click();
    await expect(authedPage.locator('[data-testid="server-path-help-copy"]')).toHaveText('Copied');
    const clip = await authedPage.evaluate(() => navigator.clipboard.readText());
    expect(clip).toContain('SERVER_FILE_ALLOWED_DIRS');
    expect(clip).toContain(OUTSIDE_DIR);

    // The panel stays until dismissed.
    await authedPage.waitForTimeout(4_500); // longer than a toast's 3.5 s life
    await expect(panel).toBeVisible();
    await authedPage.locator('[data-testid="server-path-help-close"]').click();
    await expect(panel).toBeHidden();
  });

  test('BO compare with a PDF path outside the allowed dirs shows setup steps', async ({ authedPage }) => {
    const reportA = path.join(OUTSIDE_DIR, 'qa_sales.pdf');
    const reportB = path.join(OUTSIDE_DIR, 'prod_sales.pdf');
    await openBO(authedPage);
    await authedPage.locator('[data-testid="compare-bo-source-a-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-a-path-input"]').fill(reportA);
    await authedPage.locator('[data-testid="compare-bo-source-b-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-b-path-input"]').fill(reportB);
    await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('Region');
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();

    await expectGuidance(authedPage, reportA);
    // Not mistaken for a PDF parsing problem.
    await expect(authedPage.locator('[data-testid="compare-bo-pdf-error"]')).toBeHidden();
  });

  test('a path inside the allowed dirs does not open the panel', async ({ authedPage }) => {
    const fixture = path.join(__dirname, 'fixtures', 'data', 'source.xlsx');
    await openBO(authedPage);
    await authedPage.locator('[data-testid="compare-bo-source-a-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-a-path-input"]').fill(fixture);
    await authedPage.locator('[data-testid="compare-bo-source-b-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-b-path-input"]').fill(fixture);
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();

    await expect(authedPage.locator('[data-testid="compare-bo-result-status"]')).toBeVisible({ timeout: 60_000 });
    await expect(authedPage.locator('[data-testid="server-path-help"]')).toBeHidden();
  });

  test('Help Center documents path setup and PDF compare fixes', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-help"]').click();
    const search = authedPage.locator('[data-testid="help-search-input"]');

    await search.fill('SERVER_FILE_ALLOWED_DIRS');
    const pathStep = authedPage.locator('.help-content', {
      hasText: 'Allow server-side report paths (SERVER_FILE_ALLOWED_DIRS)',
    });
    await expect(pathStep).toBeVisible();
    await expect(pathStep).toContainText('file path setup');
    await expect(pathStep).toContainText('restart the API');

    await search.fill('page furniture');
    const pdfStep = authedPage.locator('.help-content', { hasText: 'Fix BO PDF compare errors' });
    await expect(pdfStep).toBeVisible();
    await expect(pdfStep).toContainText('missing_in_source_b');
    await expect(pdfStep).toContainText('Table picker');
  });
});
