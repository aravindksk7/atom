// tests/e2e/08g-compare-multi-file.spec.ts
import { test, expect } from './fixtures';
import path from 'node:path';
import { authedContext, createMultiFileJob, deleteJob } from './api-helpers';

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

  test('previews and runs non-CSV file sets with dynamic source and target names', async ({ authedPage }) => {
    await openMultiFile(authedPage);

    await authedPage.locator('[data-testid="compare-mf-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="compare-mf-match-on-input"]').fill('id');
    await authedPage.locator('[data-testid="compare-mf-source-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_source_dynamic'));
    await authedPage.locator('[data-testid="compare-mf-source-pattern-input"]').fill('extract_{id:alnum}.json');
    await authedPage.locator('[data-testid="compare-mf-target-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_target_dynamic'));
    await authedPage.locator('[data-testid="compare-mf-target-pattern-input"]').fill('prod_{id:regex([A-Z]{2}\\d{2})}.json');

    await authedPage.locator('[data-testid="compare-mf-preview-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-mf-preview-result"]')).toContainText('1 pair(s) matched');
    await expect(authedPage.locator('[data-testid="compare-mf-preview-pair"]')).toHaveCount(1);
    await expect(authedPage.locator('[data-testid="compare-mf-preview-pair"]')).toContainText('extract_AB12.json');
    await expect(authedPage.locator('[data-testid="compare-mf-preview-pair"]')).toContainText('prod_AB12.json');

    await authedPage.locator('[data-testid="compare-mf-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-mf-results"]')).toBeVisible({ timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="compare-mf-results"]')).toContainText('PASSED');

    const passedPair = authedPage.locator('[data-testid="compare-mf-result-pair"][data-status="PASSED"]');
    await expect(passedPair).toHaveCount(1);
    await expect(passedPair).toContainText('extract_AB12.json');
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

test.describe('08g compare / job catalog run-reference', () => {
  // adminToken is worker-scoped (fixtures.ts), so it's available to beforeAll/afterAll
  // hooks directly -- see 04-history.spec.ts for the full rationale.
  let jobName: string;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      jobName = `e2e-mf-run-ref-${Date.now()}`;
      await createMultiFileJob(ctx, jobName);
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    if (!jobName) return; // beforeAll never got past createMultiFileJob() -- nothing to clean up
    const ctx = await authedContext(adminToken);
    try {
      await deleteJob(ctx, jobName);
    } finally {
      await ctx.dispose();
    }
  });

  test('job catalog Compare button jumps to the multi-file sub-tab prefilled from the job', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-search-input"]').fill(jobName);
    await authedPage.locator(`[data-testid="job-row-${jobName}-compare-btn"]`).click();

    await expect(authedPage.locator('[data-testid="compare-subtab-multifile"]')).toHaveClass(/active/);
    await expect(authedPage.locator('[data-testid="compare-mf-key-columns-input"]')).toHaveValue('id');
  });
});
