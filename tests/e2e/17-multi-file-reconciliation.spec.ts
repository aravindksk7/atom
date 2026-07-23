import { test, expect } from './fixtures';
import { authedContext, createMultiFileJob, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

test.describe('17 multi-file reconciliation', () => {
  // adminToken is worker-scoped (fixtures.ts), so it's available to beforeAll/afterAll
  // hooks directly -- see 04-history.spec.ts for the full rationale.
  let jobName: string;
  let runId: string;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      jobName = `e2e-multi-file-job-${Date.now()}`;
      await createMultiFileJob(ctx, jobName);
      const { run_id } = await triggerRun(ctx, [jobName]);
      await waitForTerminal(ctx, run_id);
      runId = run_id;
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

  test('HTML report renders the per-pair breakdown for a multi_file job', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-reports"]').click();
    await authedPage.locator('[data-testid="reports-run-select"]').selectOption(runId);
    await authedPage.locator('[data-testid="reports-load-btn"]').click();

    // Same iframe-blob-URL pattern as 06-reports.spec.ts's report test.
    const frame = authedPage.frameLocator('[data-testid="reports-iframe"]');
    await expect(frame.locator('h1')).toHaveText('ETL Framework Execution Report');

    // createMultiFileJob's fixtures deterministically produce 2 pairs: region=east
    // (byte-identical source/target -> PASSED) and region=west (changed amount ->
    // FAILED) -- see api-helpers.ts and fixtures/data/multi_source|multi_target.
    const details = frame.locator('[data-testid="file-pairs-details"]').first();
    await expect(details).toBeVisible();
    await details.locator('summary').click(); // open the collapsed <details>

    const pairRows = frame.locator('[data-testid="file-pair-row"]');
    await expect(pairRows).toHaveCount(2);

    const passedRow = frame.locator('[data-testid="file-pair-row"][data-status="PASSED"]');
    await expect(passedRow).toContainText('region=east');
    await expect(passedRow).toContainText('sales_east.csv');
    await expect(passedRow).toContainText('financials_east.csv');

    const failedRow = frame.locator('[data-testid="file-pair-row"][data-status="FAILED"]');
    await expect(failedRow).toContainText('region=west');
    await expect(failedRow).toContainText('sales_west.csv');
    await expect(failedRow).toContainText('financials_west.csv');
    await expect(failedRow).toContainText('mismatches: 1');
  });

  test('run status API exposes the per-pair breakdown as first-class fields', async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const resp = await ctx.get(`/api/runs/${runId}`);
      expect(resp.ok()).toBeTruthy();
      const body = await resp.json();
      const result = body.results.find((r: { query_name: string }) => r.query_name === jobName);

      expect(result).toBeTruthy();
      expect(result.status).toBe('FAILED');
      expect(result.file_pairs).toHaveLength(2);

      const byRegion: Record<string, any> = {};
      for (const pair of result.file_pairs) byRegion[pair.key.region] = pair;

      expect(byRegion.east.status).toBe('PASSED');
      expect(byRegion.west.status).toBe('FAILED');
      expect(byRegion.west.value_mismatch_count).toBe(1);
    } finally {
      await ctx.dispose();
    }
  });
});
