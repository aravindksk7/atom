import { test, expect } from './fixtures';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';

const dataFile = (name: string) => path.join(__dirname, 'fixtures', 'data', name);
const repoRoot = path.resolve(__dirname, '..', '..');

function pythonExe(): string {
  if (process.env.PYTHON) return process.env.PYTHON;
  const venvPy = path.join(repoRoot, '.venv', 'Scripts', 'python.exe');
  if (existsSync(venvPy)) return venvPy;
  return 'python';
}

async function openBO(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-bo"]').click();
}

async function uploadSide(
  page: import('@playwright/test').Page,
  side: 'a' | 'b',
  filePath: string,
) {
  await page.locator(`[data-testid="compare-bo-source-${side}-mode-upload"]`).click();
  await page.locator(`[data-testid="compare-bo-source-${side}-upload-input"]`).setInputFiles(filePath);
}

test.describe('08h compare / BO PDF report', () => {
  let demoPdfPath: string;

  test.beforeAll(async ({}, testInfo) => {
    demoPdfPath = testInfo.outputPath('demo.pdf');
    execFileSync(
      pythonExe(),
      ['-m', 'tests.helpers.pdf_fixtures', 'demo', demoPdfPath],
      { cwd: repoRoot, stdio: 'pipe' },
    );
  });

  test('upload PDF preview shows suggested key', async ({ authedPage }) => {
    await openBO(authedPage);
    await uploadSide(authedPage, 'b', demoPdfPath);

    const previewBtn = authedPage.locator('[data-testid="compare-bo-source-b-pdf-preview-btn"]');
    await expect(previewBtn).toBeVisible();
    await previewBtn.click();

    const preview = authedPage.locator('[data-testid="compare-bo-source-b-pdf-preview"]');
    await expect(preview).toBeVisible({ timeout: 30_000 });
    await expect(preview).toContainText('Region');
    await expect(preview).toContainText('number:1dp');

    await authedPage.locator('[data-testid="compare-bo-source-b-pdf-use-key-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-bo-key-columns-input"]')).toHaveValue('Region');
  });

  test('PDF vs same PDF passes as printed', async ({ authedPage }) => {
    await openBO(authedPage);
    await uploadSide(authedPage, 'a', demoPdfPath);
    await uploadSide(authedPage, 'b', demoPdfPath);

    await expect(authedPage.locator('[data-testid="compare-bo-pdf-rewrap"]')).toBeVisible();
    await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('Region');
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();

    await expect(authedPage.locator('[data-testid="compare-bo-result-status"]')).toHaveText(
      'PASSED (as printed)',
      { timeout: 30_000 },
    );
    await expect(authedPage.locator('[data-testid="compare-bo-pdf-precision-note"]')).toContainText(
      'Value: 1 dp',
    );
  });

  test('missing key shows suggestion', async ({ authedPage }) => {
    await openBO(authedPage);
    await uploadSide(authedPage, 'a', demoPdfPath);
    await uploadSide(authedPage, 'b', demoPdfPath);

    await expect(authedPage.locator('[data-testid="compare-bo-pdf-rewrap"]')).toBeVisible();
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();

    const errBox = authedPage.locator('[data-testid="compare-bo-pdf-error"]');
    await expect(errBox).toBeVisible({ timeout: 30_000 });
    await expect(errBox).toContainText('set key columns for a PDF compare');

    await authedPage.locator('[data-testid="compare-bo-pdf-error-use-key-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-bo-key-columns-input"]')).toHaveValue('Region');
  });

  test('PDF vs xlsx is blocked', async ({ authedPage }) => {
    await openBO(authedPage);
    await uploadSide(authedPage, 'a', demoPdfPath);
    await uploadSide(authedPage, 'b', dataFile('source.xlsx'));

    await expect(authedPage.locator('[data-testid="compare-bo-source-a-pdf-preview-btn"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="compare-bo-run-btn"]')).toBeDisabled();
    const hint = authedPage.locator('[data-testid="compare-bo-pdf-mismatch-hint"]');
    await expect(hint).toBeVisible();
    await expect(hint).toContainText('a PDF compare needs a PDF on both sides');
  });
});
