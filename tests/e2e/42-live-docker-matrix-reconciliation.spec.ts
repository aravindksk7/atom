import { test, expect } from './fixtures';
import path from 'node:path';

const dataFile = (name: string) => path.join(__dirname, 'fixtures', 'data', name);

test.describe('Live Docker Cross-Source Matrix Reconciliation Web UI', () => {
  test('navigates to Cross-Source Matrix subtab and verifies UI elements', async ({ authedPage }) => {
    await authedPage.goto('/#compare');
    await authedPage.locator('[data-testid="compare-subtab-matrix"]').click();

    await expect(authedPage.locator('#matrix-compare-container')).toBeVisible();
    await expect(authedPage.locator('[data-testid="compare-matrix-source-a-mode-sql"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="matrix-key-columns-input"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="btn-run-matrix-compare"]')).toBeVisible();
  });

  test('configures File vs File matrix reconciliation and verifies live run execution', async ({ authedPage }) => {
    await authedPage.goto('/#compare');
    await authedPage.locator('[data-testid="compare-subtab-matrix"]').click();

    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-path-input"]').fill(dataFile('source.csv'));

    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-path-input"]').fill(dataFile('target.csv'));

    await authedPage.locator('[data-testid="matrix-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="btn-run-matrix-compare"]').click();

    await expect(authedPage.locator('[data-testid="matrix-compare-results"]')).toBeVisible({ timeout: 15_000 });
    await expect(authedPage.locator('[data-testid="matrix-compare-status-badge"]')).toBeVisible();
  });

  test('configures SQL vs File matrix reconciliation with live docker backend engine', async ({ authedPage }) => {
    await authedPage.goto('/#compare');
    await authedPage.locator('[data-testid="compare-subtab-matrix"]').click();

    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-sql"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-query-textarea"]').fill('SELECT 1 as id, "Alpha" as val');

    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-path-input"]').fill(dataFile('target.csv'));

    await authedPage.locator('[data-testid="matrix-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="btn-run-matrix-compare"]').click();

    await expect(authedPage.locator('[data-testid="matrix-compare-results"]')).toBeVisible({ timeout: 15_000 });
  });

  test('configures AWS Athena vs API matrix reconciliation', async ({ authedPage }) => {
    await authedPage.goto('/#compare');
    await authedPage.locator('[data-testid="compare-subtab-matrix"]').click();

    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-athena"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-athena-query-textarea"]').fill('SELECT * FROM athena_db.sales');

    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-api"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-api-url-input"]').fill('https://api.example.com/data');

    await authedPage.locator('[data-testid="matrix-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="btn-run-matrix-compare"]').click();

    await expect(authedPage.locator('[data-testid="matrix-compare-results"]')).toBeVisible({ timeout: 15_000 });
  });
});
