import { test, expect } from './fixtures';
import path from 'node:path';

const dataFile = (name: string) => path.join(__dirname, 'fixtures', 'data', name);

async function openColumnStats(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-colstats"]').click();
}

test.describe('08d compare / column stats', () => {
  test('upload-vs-upload produces a drift table', async ({ authedPage }) => {
    await openColumnStats(authedPage);
    await authedPage.locator('[data-testid="compare-colstats-source-a-upload-input"]').setInputFiles(dataFile('source.csv'));
    await authedPage.locator('[data-testid="compare-colstats-source-b-upload-input"]').setInputFiles(dataFile('target.csv'));
    await authedPage.locator('[data-testid="compare-colstats-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-colstats-result"]')).toContainText('drift(s) detected');
  });

  test('negative: computing with no source selected surfaces an error', async ({ authedPage }) => {
    await openColumnStats(authedPage);
    await authedPage.evaluate(() => {
      const root = document.querySelector('[x-data]') as HTMLElement;
      const data = (window as any).Alpine.$data(root);
      data.colStatsSourceA = { label: 'Source A' };
      data.colStatsSourceB = { label: 'Source B' };
    });
    await authedPage.locator('[data-testid="compare-colstats-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Column stats failed');
  });

  test('negative: Live mode with a non-numeric Document ID is rejected', async ({ authedPage }) => {
    await openColumnStats(authedPage);
    await authedPage.locator('[data-testid="compare-colstats-source-a-type-select"]').selectOption('live');
    await authedPage.locator('[data-testid="compare-colstats-source-a-docid-input"]').fill('not-a-number');
    await authedPage.locator('[data-testid="compare-colstats-source-b-upload-input"]').setInputFiles(dataFile('source.csv'));
    await authedPage.locator('[data-testid="compare-colstats-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Column stats failed');
  });

  test('negative: negative Row Count Tolerance is surfaced as a non-silent error', async ({ authedPage }) => {
    await openColumnStats(authedPage);
    await authedPage.locator('[data-testid="compare-colstats-source-a-upload-input"]').setInputFiles(dataFile('source.csv'));
    await authedPage.locator('[data-testid="compare-colstats-source-b-upload-input"]').setInputFiles(dataFile('target.csv'));
    await authedPage.locator('[data-testid="compare-colstats-row-count-tol-input"]').fill('-1');
    await authedPage.locator('[data-testid="compare-colstats-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Column stats failed');
  });
});
