import path from 'node:path';
import { test, expect } from './fixtures';
import { authedContext, createMultiFileJob, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

// Mirrors api-helpers.ts's FIXTURE_DIR: the backend's server-side file-path allow-listing
// (api/services/file_source.py's resolve_allowed_path(), backed by SERVER_FILE_ALLOWED_DIRS
// in playwright.config.ts) resolves a *relative* root against its allowed base dir itself
// (base / candidate), not the server process's cwd or the allowed dir's parent -- so a
// relative path like 'tests/e2e/fixtures/data/multi_source' would resolve to a nonexistent
// nested path. Using the same absolute path construction as createMultiFileJob() keeps this
// UI-driven test aligned with the API-driven one above.
const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');

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

  test('creates, previews, and runs a multi_file job entirely through the job editor', async ({ authedPage, adminToken }) => {
    const uiJobName = `e2e-multi-file-ui-${Date.now()}`;

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeVisible();

    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(uiJobName);
    // source_mode lives on the Basic tab (the modal's default tab); the
    // mf_* fields and key_columns live on Settings -- select source_mode
    // first, then switch tabs, matching the existing files-mode job test
    // in 02-launch-jobs.spec.ts.
    await authedPage.locator('[data-testid="job-modal-source-mode-select"]').selectOption('multi_file');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="job-modal-mf-match-on-input"]').fill('region');
    await authedPage.locator('[data-testid="job-modal-mf-source-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_source'));
    await authedPage.locator('[data-testid="job-modal-mf-source-pattern-input"]').fill('sales_{region}.csv');
    await authedPage.locator('[data-testid="job-modal-mf-target-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_target'));
    await authedPage.locator('[data-testid="job-modal-mf-target-pattern-input"]').fill('financials_{region}.csv');

    // Preview before saving -- proves the preview endpoint and UI wiring both
    // work against the same deterministic fixtures used by the API-driven
    // test above (1 PASSED pair region=east, 1 FAILED pair region=west).
    await authedPage.locator('[data-testid="job-modal-mf-preview-btn"]').click();
    const previewResult = authedPage.locator('[data-testid="job-modal-mf-preview-result"]');
    await expect(previewResult).toContainText('2 pair(s) matched');
    await expect(authedPage.locator('[data-testid="job-modal-mf-preview-pair"]')).toHaveCount(2);

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();
    await expect(authedPage.locator(`[data-testid="job-row-${uiJobName}"]`)).toBeVisible();

    try {
      const ctx = await authedContext(adminToken);
      try {
        const { run_id } = await triggerRun(ctx, [uiJobName]);
        const status = await waitForTerminal(ctx, run_id);
        expect(status.status).toBe('FAILED'); // same deterministic fixtures as the API test: 1 passed pair, 1 failed pair
      } finally {
        await ctx.dispose();
      }
    } finally {
      const ctx = await authedContext(adminToken);
      try {
        await deleteJob(ctx, uiJobName);
      } finally {
        await ctx.dispose();
      }
    }
  });
});
