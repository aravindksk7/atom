import { test, expect } from './fixtures';
import { seedBaselineRun, deleteJob, authedContext } from './api-helpers';

test.describe('04 history', () => {
  // adminToken is worker-scoped (fixtures.ts), so it's available to beforeAll/afterAll
  // hooks directly -- Playwright only makes worker-scoped fixtures visible there,
  // unlike authedPage (test-scoped), which beforeAll/afterAll cannot request. With
  // workers:1 (playwright.config.ts) there's exactly one adminToken value for the
  // whole run, read from the file 00-auth-setup.spec.ts wrote.
  let jobName: string;
  let runId: string;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      ({ jobName, runId } = await seedBaselineRun(ctx, 'e2e-history'));
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    if (!jobName) return; // beforeAll never got past seedBaselineRun() — nothing to clean up
    const ctx = await authedContext(adminToken);
    try {
      await deleteJob(ctx, jobName);
    } finally {
      await ctx.dispose();
    }
  });

  test('run appears in Run History with the expected FAILED status', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-history"]').click();
    await authedPage.locator('[data-testid="history-subtab-runs"]').click();
    const row = authedPage.locator(`[data-testid="history-run-row-${runId}"]`);
    await expect(row).toBeVisible();
    await expect(row).toContainText('FAILED');
  });

  test('status filter narrows the list and Clear resets it', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-history"]').click();
    await authedPage.locator('[data-testid="history-subtab-runs"]').click();
    const row = authedPage.locator(`[data-testid="history-run-row-${runId}"]`);
    await expect(row).toBeVisible();

    // historyFilterStatus/@change="loadRuns()" re-fetches from /api/runs?status=... --
    // the <option> values in index.html are uppercase, matching /api/runs' status enum.
    await authedPage.locator('[data-testid="history-status-filter"]').selectOption('FAILED');
    await expect(row).toBeVisible();

    await authedPage.locator('[data-testid="history-status-filter"]').selectOption('COMPLETED');
    await expect(row).toBeHidden();

    await authedPage.locator('[data-testid="history-clear-btn"]').click();
    await expect(row).toBeVisible();
  });

  test('Run Detail shows the run and its mismatch breakdown', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-history"]').click();
    await authedPage.locator('[data-testid="history-subtab-runs"]').click();
    // The whole row is clickable (@click="viewRunDetail(run.run_id)" on the <tr>), so
    // clicking the row itself (rather than the separate "View ->" button, which calls
    // the same handler with @click.stop) is enough to land on Run Detail.
    await authedPage.locator(`[data-testid="history-run-row-${runId}"]`).click();
    await expect(authedPage.locator('[data-testid="run-detail-back-btn"]')).toBeVisible();

    // createFileJob's fixtures deterministically produce a single failed query with 1
    // value_diff (id=2), 1 missing_in_target (id=3), 1 missing_in_source (id=4) -- 3
    // mismatches total (etl_framework's totalMismatches()/mismatchBreakdownText() in
    // app.js sum value_mismatch_count/missing_in_target_count/missing_in_source_count).
    // The stat cards summarize at the run level (1 test, 0 passed, 1 failed); the
    // breakdown text is rendered directly in the results table's Mismatches column,
    // no row-expand needed.
    const statCards = authedPage.locator('.grid-stat .stat-card');
    await expect(statCards.filter({ hasText: 'Total' }).locator('.stat-card-value')).toHaveText('1');
    await expect(statCards.filter({ hasText: 'Passed' }).locator('.stat-card-value')).toHaveText('0');
    await expect(statCards.filter({ hasText: 'Failed' }).locator('.stat-card-value')).toHaveText('1');

    await expect(authedPage.locator('.data-table').getByText('1 value / 1 missing in target / 1 missing in source')).toBeVisible();
  });
});
