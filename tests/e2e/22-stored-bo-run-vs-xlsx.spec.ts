import { test, expect } from './fixtures';
import path from 'node:path';
import { authedContext, createConfig, deleteConfig, deleteJob, waitForTerminal } from './api-helpers';

// The originally-reported failure, end to end against the live SAP BO docker mock:
// run a bo_report job, then in Compare -> Reconciliation -> Run/File vs Report pick
// that already-PASSED run as Source A and a local xlsx as Source B. This used to
// 422 in a background task ("Both sources must be the same type") because a stored
// run only exposed per-test stats while the xlsx loaded as a frame; the run now
// keeps the report it downloaded, so the pair is genuinely row-diffable.
const liveBackends = process.env.E2E_LIVE_BACKENDS === '1' || process.env.E2E_LIVE_SAPBO === '1';

const BO_DOC_ID = '1003';
const BO_REPORT_ID = 'rpt-daily-sales';
// Doc 1003 exports 2 rows for this date; the xlsx fixture mirrors them with one
// deliberately different amount (id 5: 89.99 here vs 99.99 live).
const RUN_DATE = '2026-06-03';
const PROD_SNAPSHOT = path.join(__dirname, 'fixtures', 'data', 'bo_live_prod_snapshot.xlsx');

test.describe('22 stored BO run vs local xlsx', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 or E2E_LIVE_SAPBO=1 (docker SAP BO mock)');
  test.describe.configure({ mode: 'serial' });

  let configId: number;
  let storedRunId: string;
  const jobName = `e2e-bo-report-stored-${Date.now()}`;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-bo-stored-cfg-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        bo_url: 'https://127.0.0.1:18443',
        bo_user: 'administrator', bo_password: 'Password1',
        bo_verify_ssl: false,
      });
      configId = cfg.id;
      await ctx.put('/api/settings', { data: { timezone: 'UTC' } });
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      await deleteJob(ctx, jobName);
      if (configId) await deleteConfig(ctx, configId);
    } finally {
      await ctx.dispose();
    }
  });

  async function listRunIds(adminToken: string): Promise<string[]> {
    const ctx = await authedContext(adminToken);
    try {
      const runs = await (await ctx.get('/api/runs')).json();
      return runs.map((r: { run_id: string }) => r.run_id);
    } finally {
      await ctx.dispose();
    }
  }

  async function runOnce(page: import('@playwright/test').Page, adminToken: string) {
    const before = await listRunIds(adminToken);
    await page.locator(`[data-testid="job-row-${jobName}-checkbox"]`).click();
    if (await page.locator('.toggle-track.on').count() === 0) {
      await page.locator('span.toggle-label', { hasText: 'Live Connections' }).click();
    }
    await page.locator('[data-testid="run-tests-btn"]').click();
    let runId = '';
    await expect.poll(async () => {
      runId = (await listRunIds(adminToken)).find((id) => !before.includes(id)) || '';
      return Boolean(runId);
    }, { timeout: 20_000 }).toBe(true);
    const ctx = await authedContext(adminToken);
    try {
      const status = await waitForTerminal(ctx, runId, 60_000);
      return { runId, status: String(status.status).toUpperCase() };
    } finally {
      await ctx.dispose();
    }
  }

  test('a bo_report job run passes and keeps the report it downloaded', async ({ authedPage, adminToken }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('select[x-model="launchSettings.config_id"]').selectOption(String(configId));
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(jobName);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('bo_report');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('input.field-input[placeholder="101"]').fill(BO_DOC_ID);
    await authedPage.locator('input.field-input[x-model="jobModal.bo_page_id"]').fill(BO_REPORT_ID);

    const params = authedPage.locator('[data-testid="job-modal-bo-params"]');
    await params.locator('[data-testid="job-modal-bo-params-load-btn"]').click();
    await params.locator('input[type="date"]').fill(RUN_DATE);
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    const { runId, status } = await runOnce(authedPage, adminToken);
    storedRunId = runId;
    expect(status).toBe('PASSED');

    // has_data_artifact is what tells the Compare tab this run is row-diffable
    // rather than stats-only; without it the UI blocks the pair.
    const ctx = await authedContext(adminToken);
    try {
      const runs = await (await ctx.get('/api/runs')).json();
      const run = runs.find((r: { run_id: string }) => r.run_id === runId);
      expect(run.has_data_artifact).toBe(true);
    } finally {
      await ctx.dispose();
    }
  });

  test('that stored run compares against an xlsx without errors and shows the difference', async ({ authedPage, adminToken }) => {
    expect(storedRunId, 'previous test must have produced a run').toBeTruthy();

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-compare"]').click();
    await authedPage.locator('[data-testid="compare-subtab-recon"]').click();
    await authedPage.locator('[data-testid="compare-recon-mode-file"]').click();

    await authedPage.locator('[data-testid="compare-file-source-a-mode-run"]').click();
    await authedPage.locator('[data-testid="compare-file-source-a-run-select"]').selectOption(storedRunId);
    await authedPage.locator('[data-testid="compare-file-source-b-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-file-source-b-upload-input"]').setInputFiles(PROD_SNAPSHOT);

    // The kind guard must consider this pair valid — both sides resolve to tabular.
    await expect(authedPage.locator('[data-testid="compare-file-kind-warning"]')).toBeHidden();
    await expect(authedPage.locator('[data-testid="compare-file-run-btn"]')).toBeEnabled();

    const before = await listRunIds(adminToken);
    await authedPage.locator('[data-testid="compare-file-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-file-results"]')).toContainText('Results', { timeout: 30_000 });

    let compareRunId = '';
    await expect.poll(async () => {
      compareRunId = (await listRunIds(adminToken)).find((id) => !before.includes(id)) || '';
      return Boolean(compareRunId);
    }, { timeout: 20_000 }).toBe(true);

    const ctx = await authedContext(adminToken);
    try {
      const status = await waitForTerminal(ctx, compareRunId, 60_000);
      // The old behaviour: background 422 -> run ERROR with no result rows.
      expect(String(status.status).toUpperCase()).not.toBe('ERROR');
      const run = await (await ctx.get(`/api/runs/${compareRunId}`)).json();
      const result = run.results[0];
      expect(result.error_message).toBeFalsy();
      // Both sides are the 2-row 2026-06-03 dataset, differing in one cell.
      expect(result.source_row_count).toBe(2);
      expect(result.target_row_count).toBe(2);
      expect(result.value_mismatch_count).toBe(1);
    } finally {
      await ctx.dispose();
    }

    // Display the comparison report.
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-reports"]').click();
    await authedPage.locator('[data-testid="reports-run-select"]').selectOption(compareRunId);
    await authedPage.locator('[data-testid="reports-load-btn"]').click();
    const frame = authedPage.frameLocator('[data-testid="reports-iframe"]');
    await expect(frame.locator('h1')).toHaveText('ETL Framework Execution Report');
  });
});
