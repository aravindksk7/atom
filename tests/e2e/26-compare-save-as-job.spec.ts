// tests/e2e/26-compare-save-as-job.spec.ts
import { test, expect } from './fixtures';
import path from 'node:path';
import { authedContext, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

// resolve_allowed_path() (api/services/file_source.py) resolves paths against
// its allowed base dirs, so build absolute fixture paths the same way
// 08g-compare-multi-file.spec.ts does.
const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');
const JOB_NAME = 'e2e_saved_bo_compare';

async function openBOCompare(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-bo"]').click();
}

test.describe('26 compare / save as job', () => {
  test.afterEach(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    await deleteJob(ctx, JOB_NAME);
    await ctx.dispose();
  });

  test('saves and launches a path-vs-path BO compare job', async ({ authedPage, adminToken }) => {
    await openBOCompare(authedPage);

    await authedPage.locator('[data-testid="compare-bo-source-a-mode-path"]').click();
    await authedPage.getByTestId('compare-bo-source-a-path-input')
      .fill(path.join(FIXTURE_DIR, 'multi_source', 'sales_east.csv'));
    await authedPage.locator('[data-testid="compare-bo-source-b-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-b-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'multi_target', 'financials_east.csv'));

    await authedPage.locator('[data-testid="compare-bo-save-job-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeVisible();
    await authedPage.locator('[data-testid="compare-save-job-name"]').fill(JOB_NAME);
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeHidden();

    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await expect(authedPage.locator(`[data-testid="job-row-${JOB_NAME}"]`)).toBeVisible();

    const ctx = await authedContext(adminToken);
    try {
      const { run_id } = await triggerRun(ctx, [JOB_NAME]);
      const terminal = await waitForTerminal(ctx, run_id, 60_000);
      expect(String(terminal.status).toUpperCase()).not.toBe('ERROR');
    } finally {
      await ctx.dispose();
    }
  });

  test('refuses to save a compare whose source is an upload', async ({ authedPage }) => {
    await openBOCompare(authedPage);

    await authedPage.locator('[data-testid="compare-bo-source-a-mode-path"]').click();
    await authedPage.getByTestId('compare-bo-source-a-path-input')
      .fill(path.join(FIXTURE_DIR, 'multi_source', 'sales_east.csv'));
    await authedPage.locator('[data-testid="compare-bo-source-b-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-b-upload-input"]')
      .setInputFiles(path.join(FIXTURE_DIR, 'multi_target', 'financials_east.csv'));

    await authedPage.locator('[data-testid="compare-bo-save-job-btn"]').click();
    await authedPage.locator('[data-testid="compare-save-job-name"]').fill(JOB_NAME);
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();

    await expect(authedPage.locator('[data-testid="compare-save-job-error"]')).toContainText('Source B is an upload');
  });
});
