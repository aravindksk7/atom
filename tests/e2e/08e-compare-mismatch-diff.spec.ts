import { test, expect } from './fixtures';
import { seedBaselineRun, deleteJob, authedContext } from './api-helpers';

async function openMismatchDiff(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-mmdiff"]').click();
}

test.describe('08e compare / mismatch diff', () => {
  let jobA: string;
  let runIdA: string;
  let jobB: string;
  let runIdB: string;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      ({ jobName: jobA, runId: runIdA } = await seedBaselineRun(ctx, 'e2e-mmdiff-a'));
      ({ jobName: jobB, runId: runIdB } = await seedBaselineRun(ctx, 'e2e-mmdiff-b'));
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      if (jobA) await deleteJob(ctx, jobA);
      if (jobB) await deleteJob(ctx, jobB);
    } finally {
      await ctx.dispose();
    }
  });

  test('diffing two identical fixture runs yields persistent mismatches', async ({ authedPage }) => {
    await openMismatchDiff(authedPage);
    await authedPage.locator('[data-testid="compare-mmdiff-run-a-input"]').fill(runIdA);
    await authedPage.locator('[data-testid="compare-mmdiff-run-b-input"]').fill(runIdB);
    await authedPage.locator('[data-testid="compare-mmdiff-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-mmdiff-new-count"]')).toHaveText('0');
    await expect(authedPage.locator('[data-testid="compare-mmdiff-resolved-count"]')).toHaveText('0');
    await expect(authedPage.locator('[data-testid="compare-mmdiff-persistent-count"]')).toHaveText('3');
  });

  test('negative: invalid run UUID surfaces an error', async ({ authedPage }) => {
    await openMismatchDiff(authedPage);
    await authedPage.locator('[data-testid="compare-mmdiff-run-a-input"]').fill('00000000-0000-0000-0000-000000000000');
    await authedPage.locator('[data-testid="compare-mmdiff-run-b-input"]').fill(runIdB);
    await authedPage.locator('[data-testid="compare-mmdiff-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Mismatch diff failed');
  });

  test('negative: query-name filter matching nothing shows all-zero counts', async ({ authedPage }) => {
    await openMismatchDiff(authedPage);
    await authedPage.locator('[data-testid="compare-mmdiff-run-a-input"]').fill(runIdA);
    await authedPage.locator('[data-testid="compare-mmdiff-run-b-input"]').fill(runIdB);
    await authedPage.locator('[data-testid="compare-mmdiff-query-filter-input"]').fill('no_such_query_name_xyz');
    await authedPage.locator('[data-testid="compare-mmdiff-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-mmdiff-persistent-count"]')).toHaveText('0');
  });

  test('negative: submitting with a blank Run B shows client-side guard toast', async ({ authedPage }) => {
    await openMismatchDiff(authedPage);
    await authedPage.locator('[data-testid="compare-mmdiff-run-a-input"]').fill(runIdA);
    await authedPage.locator('[data-testid="compare-mmdiff-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Run IDs required');
  });
});
