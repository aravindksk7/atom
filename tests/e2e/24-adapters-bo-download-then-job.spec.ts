import { test, expect } from './fixtures';
import { createConfig, deleteConfig, deleteJob, authedContext, waitForTerminal } from './api-helpers';

// Live SAP BO docker mock (docker-compose.integration.yml's `sapbo` service).
// The Adapters tab's "browse -> download -> + Job" path (openAddBOJobModal() /
// saveBOJob() in frontend/features/adapters.js, POSTing
// /api/adapters/jobs/from-bo-report) previously only had coverage for creating
// the job (05c-adapters-bo-prompted-download.spec.ts's "All tabs + Job" case) --
// nothing then ran it. This proves the whole chain end to end: a report
// download that actually succeeds, added as a job through that same UI, then
// launched and passing against the live mock.
const liveBackends = process.env.E2E_LIVE_BACKENDS === '1' || process.env.E2E_LIVE_SAPBO === '1';

const BO_DOC_NAME = 'Inventory Snapshot';
const BO_REPORT_NAME = 'Inventory';
const JOB_NAME = 'bo_1002_rpt-inventory';

test.describe('24 adapters - browse, download, add as job, run', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 or E2E_LIVE_SAPBO=1 (docker SAP BO mock)');

  let boConfigId: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-adapters-bo-job-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        bo_url: 'https://127.0.0.1:18443', bo_user: 'administrator', bo_password: 'Password1',
        bo_verify_ssl: false,
      });
      boConfigId = cfg.id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      await deleteJob(ctx, JOB_NAME);
      await deleteConfig(ctx, boConfigId);
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

  test('download succeeds, then Add as Job runs against the live mock', async ({ authedPage, adminToken }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(boConfigId));
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();

    const doc = authedPage.locator('.bo-doc-item', { hasText: BO_DOC_NAME });
    await expect(doc).toBeVisible({ timeout: 20_000 });
    await doc.click();

    const report = authedPage.locator('.bo-report-item', { hasText: BO_REPORT_NAME });
    await expect(report).toBeVisible();

    // Step 1: the download itself has to actually succeed before the job is
    // worth adding -- a broken report shouldn't get promoted to a scheduled job.
    const downloadPromise = authedPage.waitForEvent('download');
    await report.getByRole('button', { name: 'XLSX' }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe('report_1002_rpt-inventory.xlsx');

    // Step 2: add that same report as a job through the Adapters tab's own
    // modal (distinct from the Jobs tab's "New Job" modal used elsewhere).
    await report.getByRole('button', { name: '+ Job' }).click();
    await authedPage.locator('#a11y-adapters-job-name').fill(JOB_NAME);
    await authedPage.getByRole('button', { name: 'Save Job' }).click();
    await expect(authedPage.locator('.toast-title').filter({ hasText: 'Job added' })).toBeVisible();

    // Step 3: run it, live, from the Jobs tab, and prove it passes.
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('select[x-model="launchSettings.config_id"]').selectOption(String(boConfigId));

    const before = await listRunIds(adminToken);
    await authedPage.locator(`[data-testid="job-row-${JOB_NAME}-checkbox"]`).click();
    if (await authedPage.locator('.toggle-track.on').count() === 0) {
      await authedPage.locator('span.toggle-label', { hasText: 'Live Connections' }).click();
    }
    await authedPage.locator('[data-testid="run-tests-btn"]').click();

    let runId = '';
    await expect.poll(async () => {
      runId = (await listRunIds(adminToken)).find((id) => !before.includes(id)) || '';
      return Boolean(runId);
    }, { timeout: 20_000 }).toBe(true);

    const ctx = await authedContext(adminToken);
    try {
      const status = await waitForTerminal(ctx, runId, 60_000);
      expect(String(status.status).toUpperCase()).toBe('PASSED');
    } finally {
      await ctx.dispose();
    }
  });
});
