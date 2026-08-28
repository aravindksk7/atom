// tests/e2e/26-compare-save-as-job.spec.ts
import { test, expect } from './fixtures';
import path from 'node:path';
import { authedContext, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

// resolve_allowed_path() (api/services/file_source.py) resolves paths against
// its allowed base dirs, so build absolute fixture paths the same way
// 08g-compare-multi-file.spec.ts does.
const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');
const JOB_NAME = 'e2e_saved_bo_compare';
const RECON_JOB_NAME = 'e2e_saved_recon_file_compare';
const REPORT_JOB_NAME = 'e2e_saved_recon_file_compare_report';

async function openBOCompare(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-bo"]').click();
}

async function openFileCompare(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-recon"]').click();
  await page.locator('[data-testid="compare-recon-mode-file"]').click();
}

test.describe('26 compare / save as job', () => {
  test.afterEach(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    await deleteJob(ctx, JOB_NAME);
    await deleteJob(ctx, RECON_JOB_NAME);
    await deleteJob(ctx, REPORT_JOB_NAME);
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

  test('editing a saved recon_file compare job reflects and persists key/exclude columns', async ({ authedPage, adminToken }) => {
    // Regression coverage for: "the Key column field is optional but that is
    // not reflected when it is saved as a job and in edit job" (and the same
    // gap for Exclude/Ignore columns) -- job_type 'compare' jobs used to be
    // invisible to the Job Catalog's Edit flow (no Key/Exclude Columns field
    // ever rendered for that job_type, and the Compare button that *would*
    // show them was hidden for it too), so re-saving after an edit silently
    // failed or dropped the fields.
    await openFileCompare(authedPage);

    await authedPage.locator('[data-testid="compare-file-source-a-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-file-source-a-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'source.csv'));
    await authedPage.locator('[data-testid="compare-file-source-b-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-file-source-b-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'target.csv'));
    await authedPage.locator('[data-testid="compare-file-key-columns-input"]').fill('id');
    // Exclude Columns deliberately left blank -- key/exclude columns are both
    // optional for a file-backed compare (the backend infers a key and skips
    // nothing by default), and the edit round trip below must show that
    // blank state back, not silently substitute a placeholder default.
    await expect(authedPage.locator('[data-testid="compare-file-exclude-columns-input"]')).toHaveValue('');

    await authedPage.locator('[data-testid="compare-file-save-job-btn"]').click();
    await authedPage.locator('[data-testid="compare-save-job-name"]').fill(RECON_JOB_NAME);
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeHidden();

    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await expect(authedPage.locator(`[data-testid="job-row-${RECON_JOB_NAME}"]`)).toBeVisible();

    // Edit must reopen the Compare tab (job_type 'compare' has no generic-modal
    // support) with the saved config -- including the file paths and the
    // key/exclude columns -- faithfully reflected, not blank/hidden.
    await authedPage.locator(`[data-testid="job-row-${RECON_JOB_NAME}-edit-btn"]`).click();
    await expect(authedPage.locator('[data-testid="compare-subtab-recon"]')).toHaveClass(/active/);
    await expect(authedPage.locator('[data-testid="compare-file-source-a-path-input"]')).toHaveValue(path.join(FIXTURE_DIR, 'source.csv'));
    await expect(authedPage.locator('[data-testid="compare-file-source-b-path-input"]')).toHaveValue(path.join(FIXTURE_DIR, 'target.csv'));
    await expect(authedPage.locator('[data-testid="compare-file-key-columns-input"]')).toHaveValue('id');
    await expect(authedPage.locator('[data-testid="compare-file-exclude-columns-input"]')).toHaveValue('');

    // Now actually set an exclude column and save the edit back -- this is
    // the part that used to be impossible (either invisible, or dropped on
    // save) for a compare-type job.
    await authedPage.locator('[data-testid="compare-file-exclude-columns-input"]').fill('amount');
    await authedPage.locator('[data-testid="compare-file-save-job-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-editing-note"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="compare-save-job-name"]')).toHaveValue(RECON_JOB_NAME);
    await expect(authedPage.locator('[data-testid="compare-save-job-name"]')).toBeDisabled();
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeHidden();

    const ctx = await authedContext(adminToken);
    try {
      // The Job Catalog is the source of truth after the edit -- assert what
      // actually persisted, not just what the form showed. There is no
      // GET /api/jobs/{name}, only the list endpoint.
      const jobsResp = await ctx.get('/api/jobs');
      expect(jobsResp.ok()).toBeTruthy();
      const jobs = await jobsResp.json();
      const job = jobs.find((j: { name: string }) => j.name === RECON_JOB_NAME);
      expect(job).toBeTruthy();
      expect(job.key_columns).toEqual(['id']);
      expect(job.exclude_columns).toEqual(['amount']);

      // Functional proof, not just a UI/API round trip: source.csv/target.csv
      // (see api-helpers.ts's createFileJob doc comment) differ on `amount`
      // for id=2 -- excluding that column must make the value_diff disappear
      // while id=3/id=4 (missing rows, unaffected by exclude_columns) remain.
      const { run_id } = await triggerRun(ctx, [RECON_JOB_NAME]);
      await waitForTerminal(ctx, run_id, 60_000);
      const runResp = await ctx.get(`/api/runs/${run_id}`);
      const run = await runResp.json();
      const result = run.results.find((r: { query_name: string }) => r.query_name === RECON_JOB_NAME);
      expect(result).toBeTruthy();
      expect(result.value_mismatch_count).toBe(0);
      expect(result.missing_in_target_count).toBe(1);
      expect(result.missing_in_source_count).toBe(1);
    } finally {
      await ctx.dispose();
    }
  });

  test('a job saved via Save as Job downloads a non-empty Full HTML Report', async ({ authedPage, adminToken }) => {
    // Regression coverage for: "compare in all options using save as Job
    // button, jobs executed from this option doesn't generate or download
    // the full html report" -- the recompute behind the Full HTML Report
    // download used to skip any job_type 'compare' entry in a run's job
    // sequence entirely (it only recognized job_type 'reconciliation'),
    // producing a report with zero mismatch rows even for a run that failed
    // with real mismatches.
    await openFileCompare(authedPage);
    await authedPage.locator('[data-testid="compare-file-source-a-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-file-source-a-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'source.csv'));
    await authedPage.locator('[data-testid="compare-file-source-b-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-file-source-b-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'target.csv'));
    await authedPage.locator('[data-testid="compare-file-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="compare-file-save-job-btn"]').click();
    await authedPage.locator('[data-testid="compare-save-job-name"]').fill(REPORT_JOB_NAME);
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeHidden();

    const ctx = await authedContext(adminToken);
    try {
      const { run_id } = await triggerRun(ctx, [REPORT_JOB_NAME]);
      await waitForTerminal(ctx, run_id, 60_000);

      await authedPage.goto('/');
      await authedPage.locator('[data-testid="nav-tab-history"]').click();
      await authedPage.locator('[data-testid="history-subtab-runs"]').click();
      await authedPage.locator(`[data-testid="history-run-row-${run_id}"]`).click();
      await expect(authedPage.locator('[data-testid="run-detail-back-btn"]')).toBeVisible();

      authedPage.once('dialog', (d) => d.accept());
      const downloadPromise = authedPage.waitForEvent('download');
      await authedPage.locator('[data-testid="history-download-full-report-btn"]').click();
      const download = await downloadPromise;
      expect(download.suggestedFilename()).toContain('.html');

      // Reading the browser's download artifact EPERMs on Windows (AV scan
      // lock) -- verify content through the export API instead, same as
      // 04-history.spec.ts's equivalent assertion, which reuses the same
      // COMPLETED export job the button above just created.
      const jobResp = await ctx.post(`/api/runs/${run_id}/exports`, { data: { format: 'html' } });
      const exportJob = await jobResp.json();
      expect(exportJob.status).toBe('COMPLETED');
      expect(exportJob.row_count).toBeGreaterThan(0);
      const artifactResp = await ctx.get(`/api/runs/${run_id}/exports/${exportJob.export_id}/download`);
      expect(artifactResp.ok()).toBeTruthy();
      const html = await artifactResp.text();
      expect(html).toContain('data-mismatch');
      expect(html).not.toContain('load-all-btn-global');
    } finally {
      await ctx.dispose();
    }
  });
});
