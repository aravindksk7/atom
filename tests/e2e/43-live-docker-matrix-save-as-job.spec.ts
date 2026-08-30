// tests/e2e/43-live-docker-matrix-save-as-job.spec.ts
import { test, expect } from './fixtures';
import path from 'node:path';
import { authedContext, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');
const JOB_NAME = 'e2e_saved_matrix_compare';
const REPORT_JOB_NAME = 'e2e_saved_matrix_compare_report';

// POST /api/runs/{run_id}/exports (api/routes/runs.py) returns 202 and runs the
// export as a BackgroundTasks job, so it is not necessarily COMPLETED by the
// time the response comes back. 26-compare-save-as-job.spec.ts and
// 04-history.spec.ts sidestep this by driving the UI download button first
// (which polls to completion), so their follow-up API POST reuses an
// already-COMPLETED job (see 04-history.spec.ts's comment on that call).
// This spec calls the export API directly with no prior UI download, so it
// needs its own wait -- mirrors 17-multi-file-reconciliation.spec.ts's
// waitForExportCompleted().
async function waitForExportCompleted(ctx: Awaited<ReturnType<typeof authedContext>>, runId: string, exportId: string) {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    const resp = await ctx.get(`/api/runs/${runId}/exports/${exportId}`);
    expect(resp.ok()).toBeTruthy();
    const job = await resp.json();
    if (job.status === 'COMPLETED') return job;
    if (job.status === 'FAILED') throw new Error(`export ${exportId} failed: ${job.error_message || 'unknown error'}`);
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`export ${exportId} did not complete within timeout`);
}

async function openMatrixCompare(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-matrix"]').click();
}

test.describe('43 compare / matrix save as job', () => {
  test.afterEach(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    await deleteJob(ctx, JOB_NAME);
    await deleteJob(ctx, REPORT_JOB_NAME);
    await ctx.dispose();
  });

  test('saves and launches a path-vs-path Matrix compare job', async ({ authedPage, adminToken }) => {
    await openMatrixCompare(authedPage);

    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'source.csv'));
    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'target.csv'));
    await authedPage.locator('[data-testid="matrix-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="compare-matrix-save-job-btn"]').click();
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

  test('refuses to save a Matrix compare whose source is an upload', async ({ authedPage }) => {
    await openMatrixCompare(authedPage);

    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'source.csv'));
    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-upload-input"]')
      .setInputFiles(path.join(FIXTURE_DIR, 'target.csv'));

    await authedPage.locator('[data-testid="compare-matrix-save-job-btn"]').click();
    await authedPage.locator('[data-testid="compare-save-job-name"]').fill(JOB_NAME);
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();

    await expect(authedPage.locator('[data-testid="compare-save-job-error"]')).toContainText('Source B is an upload');
  });

  test('editing a saved matrix compare job reflects and persists key/exclude columns', async ({ authedPage, adminToken }) => {
    await openMatrixCompare(authedPage);

    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'source.csv'));
    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'target.csv'));
    await authedPage.locator('[data-testid="matrix-key-columns-input"]').fill('id');
    await expect(authedPage.locator('[data-testid="matrix-exclude-columns-input"]')).toHaveValue('');

    await authedPage.locator('[data-testid="compare-matrix-save-job-btn"]').click();
    await authedPage.locator('[data-testid="compare-save-job-name"]').fill(JOB_NAME);
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeHidden();

    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await expect(authedPage.locator(`[data-testid="job-row-${JOB_NAME}"]`)).toBeVisible();

    await authedPage.locator(`[data-testid="job-row-${JOB_NAME}-edit-btn"]`).click();
    await expect(authedPage.locator('[data-testid="compare-subtab-matrix"]')).toHaveClass(/active/);
    await expect(authedPage.locator('[data-testid="compare-matrix-source-a-path-input"]')).toHaveValue(path.join(FIXTURE_DIR, 'source.csv'));
    await expect(authedPage.locator('[data-testid="compare-matrix-source-b-path-input"]')).toHaveValue(path.join(FIXTURE_DIR, 'target.csv'));
    await expect(authedPage.locator('[data-testid="matrix-key-columns-input"]')).toHaveValue('id');
    await expect(authedPage.locator('[data-testid="matrix-exclude-columns-input"]')).toHaveValue('');

    await authedPage.locator('[data-testid="matrix-exclude-columns-input"]').fill('amount');
    await authedPage.locator('[data-testid="compare-matrix-save-job-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-editing-note"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="compare-save-job-name"]')).toHaveValue(JOB_NAME);
    await expect(authedPage.locator('[data-testid="compare-save-job-name"]')).toBeDisabled();
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeHidden();

    const ctx = await authedContext(adminToken);
    try {
      const jobsResp = await ctx.get('/api/jobs');
      expect(jobsResp.ok()).toBeTruthy();
      const jobs = await jobsResp.json();
      const job = jobs.find((j: { name: string }) => j.name === JOB_NAME);
      expect(job).toBeTruthy();
      expect(job.key_columns).toEqual(['id']);
      expect(job.exclude_columns).toEqual(['amount']);

      // Functional proof: source.csv/target.csv differ on `amount` for id=2 --
      // excluding that column must make the value_diff disappear while id=3/id=4
      // (missing rows, unaffected by exclude_columns) remain.
      const { run_id } = await triggerRun(ctx, [JOB_NAME]);
      await waitForTerminal(ctx, run_id, 60_000);
      const runResp = await ctx.get(`/api/runs/${run_id}`);
      const run = await runResp.json();
      const result = run.results.find((r: { query_name: string }) => r.query_name === JOB_NAME);
      expect(result).toBeTruthy();
      expect(result.value_mismatch_count).toBe(0);
      expect(result.missing_in_target_count).toBe(1);
      expect(result.missing_in_source_count).toBe(1);
    } finally {
      await ctx.dispose();
    }
  });

  test('a matrix job saved via Save as Job downloads a non-empty Full HTML Report', async ({ authedPage, adminToken }) => {
    await openMatrixCompare(authedPage);
    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'source.csv'));
    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'target.csv'));
    await authedPage.locator('[data-testid="matrix-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="compare-matrix-save-job-btn"]').click();
    await authedPage.locator('[data-testid="compare-save-job-name"]').fill(REPORT_JOB_NAME);
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeHidden();

    const ctx = await authedContext(adminToken);
    try {
      const { run_id } = await triggerRun(ctx, [REPORT_JOB_NAME]);
      await waitForTerminal(ctx, run_id, 60_000);

      const jobResp = await ctx.post(`/api/runs/${run_id}/exports`, { data: { format: 'html' } });
      let exportJob = await jobResp.json();
      exportJob = await waitForExportCompleted(ctx, run_id, exportJob.export_id);
      expect(exportJob.status).toBe('COMPLETED');
      expect(exportJob.row_count).toBeGreaterThan(0);
      const artifactResp = await ctx.get(`/api/runs/${run_id}/exports/${exportJob.export_id}/download`);
      expect(artifactResp.ok()).toBeTruthy();
      const html = await artifactResp.text();
      expect(html).toContain('data-mismatch');
    } finally {
      await ctx.dispose();
    }
  });
});
