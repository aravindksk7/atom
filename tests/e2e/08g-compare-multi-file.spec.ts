// tests/e2e/08g-compare-multi-file.spec.ts
import { test, expect } from './fixtures';
import path from 'node:path';

// Mirrors 17-multi-file-reconciliation.spec.ts's FIXTURE_DIR construction --
// resolve_allowed_path() (api/services/file_source.py) resolves a relative
// root against its allowed base dir itself, not the server's cwd, so an
// absolute path built the same way as the job-editor e2e test is required.
const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');

async function openMultiFile(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-multifile"]').click();
}

test.describe('08g compare / multi-file', () => {
  test('previews and runs an ad-hoc multi-file comparison, showing the per-pair breakdown', async ({ authedPage }) => {
    await openMultiFile(authedPage);

    await authedPage.locator('[data-testid="compare-mf-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="compare-mf-match-on-input"]').fill('region');
    await authedPage.locator('[data-testid="compare-mf-source-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_source'));
    await authedPage.locator('[data-testid="compare-mf-source-pattern-input"]').fill('sales_{region}.csv');
    await authedPage.locator('[data-testid="compare-mf-target-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_target'));
    await authedPage.locator('[data-testid="compare-mf-target-pattern-input"]').fill('financials_{region}.csv');

    await authedPage.locator('[data-testid="compare-mf-preview-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-mf-preview-result"]')).toContainText('2 pair(s) matched');
    await expect(authedPage.locator('[data-testid="compare-mf-preview-pair"]')).toHaveCount(2);

    await authedPage.locator('[data-testid="compare-mf-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-mf-results"]')).toBeVisible({ timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="compare-mf-results"]')).toContainText('FAILED');

    const resultPairs = authedPage.locator('[data-testid="compare-mf-result-pair"]');
    await expect(resultPairs).toHaveCount(2);
    await expect(authedPage.locator('[data-testid="compare-mf-result-pair"][data-status="PASSED"]')).toContainText('region=east');
    await expect(authedPage.locator('[data-testid="compare-mf-result-pair"][data-status="FAILED"]')).toContainText('region=west');
  });

  test('negative: running with no source root shows an error toast', async ({ authedPage }) => {
    await openMultiFile(authedPage);
    await authedPage.locator('[data-testid="compare-mf-match-on-input"]').fill('region');
    await authedPage.locator('[data-testid="compare-mf-target-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_target'));
    await authedPage.locator('[data-testid="compare-mf-target-pattern-input"]').fill('financials_{region}.csv');
    // source root/pattern left empty
    await authedPage.locator('[data-testid="compare-mf-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Multi-file compare failed');
  });
});
