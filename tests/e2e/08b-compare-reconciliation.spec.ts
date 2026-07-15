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

  test('KNOWN BUG: expanding a differing row renders blank source/target cells (renderSrc/renderTgt undefined)', async ({ authedPage }) => {
    // See "Known pre-existing bug encountered during research" in the plan header:
    // renderSrc/renderTgt are referenced by index.html but never defined in any loaded
    // script. Alpine's x-html evaluator catches the ReferenceError internally (reporting
    // it as an uncaught page error rather than a console.error) and falls back to
    // `undefined`, which x-html then coerces to the literal string "undefined" as the
    // cell's innerHTML — the sibling :class binding is unaffected and still applies.
    const pageErrors: string[] = [];
    authedPage.on('pageerror', (err) => pageErrors.push(err.message));

    await openRecon(authedPage);
    await uploadPair(authedPage);
    await expect(authedPage.locator('[data-testid="compare-file-results"]')).toContainText('Results', { timeout: 20_000 });

    await authedPage.locator('[data-testid^="compare-file-row-"]').first().click();
    await expect(authedPage.locator('td.text-emerald-700 span:visible').first()).toHaveText('undefined');
    await expect.poll(() => pageErrors.some((e) => e.includes('renderSrc is not defined'))).toBe(true);
    expect(pageErrors.some((e) => e.includes('renderTgt is not defined'))).toBe(true);
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

  test('negative: Compare Files with no source chosen surfaces error', async ({ authedPage }) => {
    await openRecon(authedPage);
    await authedPage.locator('[data-testid="compare-recon-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-file-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('File compare failed');
  });
});
