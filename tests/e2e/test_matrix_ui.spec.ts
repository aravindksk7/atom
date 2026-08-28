import { test, expect } from './fixtures';
import path from 'node:path';

const dataFile = (name: string) => path.join(__dirname, 'fixtures', 'data', name);

test.describe('Cross-Datasource Matrix UI', () => {
  test('navigate to matrix compare subtab, check form elements, and submit comparison', async ({ authedPage }) => {
    await authedPage.locator('[data-testid="nav-tab-compare"]').click();
    await authedPage.locator('[data-testid="compare-subtab-matrix"]').click();

    await expect(authedPage.locator('#matrix-compare-container')).toBeVisible();

    await expect(authedPage.locator('[data-testid="compare-matrix-source-a-mode-file"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="compare-matrix-source-b-mode-sql"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="matrix-key-columns-input"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="btn-run-matrix-compare"]')).toBeVisible();

    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-path-input"]').fill(dataFile('source.csv'));

    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-path-input"]').fill(dataFile('target.csv'));

    await authedPage.locator('[data-testid="matrix-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="btn-run-matrix-compare"]').click();

    await expect(authedPage.locator('[data-testid="matrix-compare-results"]')).toBeVisible({ timeout: 15_000 });
    await expect(authedPage.locator('[data-testid="matrix-compare-status-badge"]')).toBeVisible();
  });
});
