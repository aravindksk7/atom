import { test, expect } from './fixtures';
import path from 'node:path';

const dataFile = (name: string) => path.join(__dirname, 'fixtures', 'data', name);

async function openRecon(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-recon"]').click();
}

async function uploadPair(page: import('@playwright/test').Page) {
  await page.locator('[data-testid="compare-recon-mode-file"]').click();
  await page.locator('[data-testid="compare-file-source-a-mode-upload"]').click();
  await page.locator('[data-testid="compare-file-source-a-upload-input"]').setInputFiles(dataFile('source.csv'));
  await page.locator('[data-testid="compare-file-source-b-mode-upload"]').click();
  await page.locator('[data-testid="compare-file-source-b-upload-input"]').setInputFiles(dataFile('target.csv'));
  await page.locator('[data-testid="compare-file-run-btn"]').click();
}

test.describe('08b compare / reconciliation', () => {
  test('Run/File vs Report: two uploaded files produce the known mismatch set', async ({ authedPage }) => {
    await openRecon(authedPage);
    await uploadPair(authedPage);
    await expect(authedPage.locator('[data-testid="compare-file-results"]')).toContainText('Results', { timeout: 20_000 });
    await expect(authedPage.getByText('Differs', { exact: true })).toBeVisible();
  });

  test('expanding a differing row renders real source/target values', async ({ authedPage }) => {
    // Was a KNOWN BUG: renderSrc/renderTgt were referenced by index.html but defined in
    // no loaded script (the modularization left them behind in app.js.bak), so Alpine's
    // x-html evaluator swallowed a ReferenceError and wrote the literal string
    // "undefined" into every value cell. They now live in features/diff-render.js.
    const pageErrors: string[] = [];
    authedPage.on('pageerror', (err) => pageErrors.push(err.message));

    await openRecon(authedPage);
    await uploadPair(authedPage);
    await expect(authedPage.locator('[data-testid="compare-file-results"]')).toContainText('Results', { timeout: 20_000 });

    await authedPage.locator('[data-testid^="compare-file-row-"]').first().click();
    const firstValueCell = authedPage.locator('td.text-slate-700 span:visible').first();
    await expect(firstValueCell).not.toHaveText('undefined');
    await expect(firstValueCell).not.toBeEmpty();
    expect(pageErrors.some((e) => e.includes('renderSrc is not defined'))).toBe(false);
    expect(pageErrors.some((e) => e.includes('renderTgt is not defined'))).toBe(false);
  });

  test('negative: Launch Dual-Env with no config selected shows guard toast', async ({ authedPage }) => {
    await openRecon(authedPage);
    await authedPage.locator('[data-testid="compare-recon-mode-stored"]').click();
    await authedPage.locator('[data-testid="compare-recon-dualenv-launch-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Missing config');
  });

  test('negative: refreshing past pairs with none existing shows empty state', async ({ authedPage }) => {
    await openRecon(authedPage);
    await authedPage.locator('[data-testid="compare-recon-mode-stored"]').click();
    await authedPage.locator('[data-testid="compare-recon-dualenv-refresh-pairs-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-recon-dualenv-pairs-empty"]')).toBeVisible();
  });

  test('negative: mixing a report source with a tabular file is blocked before any run is created', async ({ authedPage }) => {
    // Regression: this pair used to POST, create a run, and fail in the background
    // with a bare 422 ("both sources must be the same type").
    await openRecon(authedPage);
    await authedPage.locator('[data-testid="compare-recon-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-file-source-a-mode-path"]').click();
    await authedPage.locator('input[placeholder="C:\\\\reports\\\\run_a.html"]').fill('C:\\reports\\run_a.html');
    await authedPage.locator('[data-testid="compare-file-source-b-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-file-source-b-upload-input"]').setInputFiles(dataFile('target.csv'));

    await expect(authedPage.locator('[data-testid="compare-file-kind-warning"]')).toContainText('same kind');
    await expect(authedPage.locator('[data-testid="compare-file-run-btn"]')).toBeDisabled();
  });

  test('negative: Compare Files with no source chosen surfaces error', async ({ authedPage }) => {
    await openRecon(authedPage);
    await authedPage.locator('[data-testid="compare-recon-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-file-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('File compare failed');
  });
});
