import { test, expect } from './fixtures';
import path from 'node:path';
import { authedContext, createConfig, deleteConfig, deleteJob, waitForTerminal } from './api-helpers';

// Live SAP BO docker mock (docker-compose.integration.yml's `sapbo` service).
// Exercises the whole run-date path against a real HTTP(S) server rather than a
// route stub: discover the document's prompts -> answer the DateTime prompt ->
// export the rows that day serves -> diff against a local xlsx -> render the
// report. The mock keys ("1001", "rpt-sales") rows off the answered UTC day
// (DATASETS_BY_DATE in docker/sapbo-mock/server.py), so a pull that ignored the
// prompt would return a different row count and fail these assertions.
// E2E_LIVE_BACKENDS=1 makes global-setup bring up the whole integration stack.
// This spec needs only the `sapbo` service, so E2E_LIVE_SAPBO=1 also enables it
// for a targeted run against an already-started mock:
//   docker compose -f docker-compose.integration.yml up -d --wait --build sapbo
const liveBackends = process.env.E2E_LIVE_BACKENDS === '1' || process.env.E2E_LIVE_SAPBO === '1';

const BO_DOC_ID = '1003';
const BO_REPORT_ID = 'rpt-daily-sales';
// Doc 1003 exports 2 rows for 2026-06-03, 3 rows for 2026-06-02, and a single
// sentinel row when its prompt was never answered — so the row-count assertions
// below distinguish all three cases rather than passing by coincidence.
const RUN_DATE = '2026-06-03';
const OTHER_RUN_DATE = '2026-06-02';
const PROD_SNAPSHOT = path.join(__dirname, 'fixtures', 'data', 'bo_live_prod_snapshot.xlsx');

test.describe('21 bo_live report for a date vs local xlsx', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker SAP BO mock)');
  test.describe.configure({ mode: 'serial' });

  let configId: number;
  const jobName = `e2e-bo-live-date-${Date.now()}`;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-bo-live-date-cfg-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        bo_url: 'https://127.0.0.1:18443',
        bo_user: 'administrator', bo_password: 'Password1',
        bo_verify_ssl: false,
      });
      configId = cfg.id;
      // The date prompt is converted from a calendar date to a UTC instant using
      // the app timezone, and the mock resolves the answer by its UTC day — pin
      // the timezone so the expected day is not environment-dependent.
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

  async function openJobsTab(page: import('@playwright/test').Page) {
    await page.goto('/');
    await page.locator('[data-testid="nav-tab-jobs"]').click();
    // Selected before opening the modal: openNewJobModal() seeds the modal's
    // config from launchSettings.config_id, which is what "Load from report"
    // uses to reach BO.
    await page.locator('select[x-model="launchSettings.config_id"]').selectOption(String(configId));
  }

  /** Selects the job, enables live connections, launches, and returns the run id. */
  async function launchJob(page: import('@playwright/test').Page, adminToken: string) {
    const before = await listRunIds(adminToken);
    await page.locator(`[data-testid="job-row-${jobName}-checkbox"]`).click();
    const liveToggle = page.locator('span.toggle-label', { hasText: 'Live Connections' });
    if (await page.locator('.toggle-track.on').count() === 0) await liveToggle.click();
    await page.locator('[data-testid="run-tests-btn"]').click();

    let runId = '';
    await expect.poll(async () => {
      const ids = await listRunIds(adminToken);
      runId = ids.find((id) => !before.includes(id)) || '';
      return Boolean(runId);
    }, { timeout: 20_000 }).toBe(true);
    return runId;
  }

  async function listRunIds(adminToken: string): Promise<string[]> {
    const ctx = await authedContext(adminToken);
    try {
      const runs = await (await ctx.get('/api/runs')).json();
      return runs.map((r: { run_id: string }) => r.run_id);
    } finally {
      await ctx.dispose();
    }
  }

  async function runResult(adminToken: string, runId: string) {
    const ctx = await authedContext(adminToken);
    try {
      await waitForTerminal(ctx, runId, 60_000);
      const run = await (await ctx.get(`/api/runs/${runId}`)).json();
      return run.results[0];
    } finally {
      await ctx.dispose();
    }
  }

  test('creates the job, pulls the report for the picked date, diffs the xlsx, and renders the report', async ({ authedPage, adminToken }) => {
    await openJobsTab(authedPage);
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(jobName);
    await authedPage.locator('[data-testid="job-modal-source-mode-select"]').selectOption('bo_live');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();

    await authedPage.locator('input.field-input[placeholder="101"]').fill(BO_DOC_ID);
    await authedPage.locator('input.field-input[x-model="jobModal.bo_page_id"]').fill(BO_REPORT_ID);
    await authedPage.locator('[data-testid="job-modal-key-columns-input"]').fill('id');

    // Prompts come from the live document, not hand-typed ids.
    const params = authedPage.locator('[data-testid="job-modal-bo-params"]');
    await params.locator('[data-testid="job-modal-bo-params-load-btn"]').click();
    await expect(params).toContainText('Start Date');
    await expect(params).toContainText('Region');
    await params.locator('input[type="date"]').fill(RUN_DATE);

    await authedPage.locator('[data-testid="job-modal-bo-live-target-mode-upload"]').click();
    await authedPage.locator('[data-testid="job-modal-bo-live-target-upload-input"]').setInputFiles(PROD_SNAPSHOT);
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    const runId = await launchJob(authedPage, adminToken);
    const result = await runResult(adminToken, runId);

    // 2 rows is RUN_DATE's dataset; an unanswered prompt would export the 3-row
    // default, so this is the assertion that the picked date drove the export.
    expect(result.source_row_count).toBe(2);
    expect(result.target_row_count).toBe(2);
    // The xlsx differs from the live pull in exactly one cell (id 5 amount).
    expect(result.value_mismatch_count).toBe(1);
    expect(result.status).toBe('FAILED');

    // Display the report.
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-reports"]').click();
    await authedPage.locator('[data-testid="reports-run-select"]').selectOption(runId);
    await authedPage.locator('[data-testid="reports-load-btn"]').click();
    const frame = authedPage.frameLocator('[data-testid="reports-iframe"]');
    await expect(frame.locator('h1')).toHaveText('ETL Framework Execution Report');
    await expect(frame.locator('body')).toContainText(jobName);
  });

  test('changing the date prompt changes the pulled data', async ({ authedPage, adminToken }) => {
    // Same job, different answer: proves the prompt is re-answered per run rather
    // than the document's stored answer being reused.
    await openJobsTab(authedPage);
    await authedPage.locator(`[data-testid="job-row-${jobName}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-bo-params"] input[type="date"]').fill(OTHER_RUN_DATE);
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    const runId = await launchJob(authedPage, adminToken);
    const result = await runResult(adminToken, runId);

    expect(result.source_row_count).toBe(3);
    expect(result.target_row_count).toBe(2);
  });
});
