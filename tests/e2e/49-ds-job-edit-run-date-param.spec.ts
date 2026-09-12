import { test, expect } from './fixtures';
import { deleteJob, authedContext } from './api-helpers';

// Covers editing an existing SAP DS job (job_type: 'ds_job') through the Jobs tab's
// Edit Job modal -- Settings sub-tab -- Job Params (JSON) field, adding a custom
// runtime variable ($G_RUN_DATE) the way a user would when a DS batch job's global
// variable needs a per-run date. Distinct from 44-adapters-ds-save-job-modal.spec.ts
// (which drives DS job *creation* via the Adapters lookup -> "+ Add to Job Catalog"
// flow): this starts from a job that already exists in the Job Catalog and edits it
// directly, matching the "edit an existing job" path rather than the "discover and
// import" path.

test.describe('49 launch: edit SAP DS job adds a run_date param on the Settings tab', () => {
  test('editing Job Params (JSON) on an existing ds_job persists the new variable', async ({ authedPage, adminToken }) => {
    const jobName = `e2e_ds_run_date_${Date.now()}`;
    const ctx = await authedContext(adminToken);
    try {
      const resp = await ctx.post('/api/jobs', {
        data: {
          name: jobName,
          job_type: 'ds_job',
          params: { job_name: 'JOB_FINANCE_EXTRACT', repository: 'FINANCE_REPO' },
        },
      });
      if (!resp.ok()) throw new Error(`create ds_job failed: ${resp.status()} ${await resp.text()}`);
    } finally {
      await ctx.dispose();
    }

    try {
      await authedPage.goto('/');
      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
      await authedPage.locator(`[data-testid="job-row-${jobName}-edit-btn"]`).click();

      const modal = authedPage.locator('[data-testid="job-modal"]');
      await expect(modal).toBeVisible();

      // job-modal-ds-job-name-input etc. live on the Settings sub-tab, not the
      // modal's default Basic tab.
      await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();

      const jobParamsInput = authedPage.locator('#a11y-launch-job-params-json-optional');
      await expect(jobParamsInput).toBeVisible();
      await expect(jobParamsInput).toHaveValue('');
      await jobParamsInput.fill('{"$G_RUN_DATE": "2026-09-11"}');

      await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
      await expect(modal).toBeHidden();

      // Verify persistence via the API (source of truth for RunExecutor substitution).
      // There's no GET /api/jobs/{name} route (only list/{name}/runs/{name} PUT/DELETE
      // -- see api/routes/jobs.py), so fetch the list and find this job by name.
      const verifyCtx = await authedContext(adminToken);
      try {
        const jobs = await (await verifyCtx.get('/api/jobs')).json();
        const job = jobs.find((j: { name: string }) => j.name === jobName);
        expect(job).toBeTruthy();
        expect(job.params.job_params).toEqual({ $G_RUN_DATE: '2026-09-11' });
      } finally {
        await verifyCtx.dispose();
      }

      // Verify the UI reflects the same value on re-open (round-trip through the
      // modal's own hydration logic in openEditJobModal(), not just the API).
      await authedPage.locator(`[data-testid="job-row-${jobName}-edit-btn"]`).click();
      await expect(modal).toBeVisible();
      await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
      await expect(authedPage.locator('#a11y-launch-job-params-json-optional'))
        .toHaveValue('{"$G_RUN_DATE":"2026-09-11"}');

      await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
    } finally {
      const cleanupCtx = await authedContext(adminToken);
      try {
        await deleteJob(cleanupCtx, jobName);
      } finally {
        await cleanupCtx.dispose();
      }
    }
  });

  test('invalid Job Params JSON surfaces a "Validation failed" toast instead of silently no-opping', async ({ authedPage, adminToken }) => {
    // Guards existing behavior: saveJob() always runs validateJobDefinition() first
    // (launch.js), which calls the same _buildJobRequestBody() that parses Job Params
    // JSON inside its own try/catch -- a malformed value throws there and is caught,
    // producing a 'Validation failed' toast and returning {ok: false} before saveJob()
    // ever attempts a PUT. Locks this in so a future refactor of saveJob()/
    // validateJobDefinition() can't reintroduce a silent no-op (an unhandled promise
    // rejection from a JSON.parse() throw with no try/catch around it) for this field.
    const jobName = `e2e_ds_bad_json_${Date.now()}`;
    const ctx = await authedContext(adminToken);
    try {
      const resp = await ctx.post('/api/jobs', {
        data: {
          name: jobName,
          job_type: 'ds_job',
          params: { job_name: 'JOB_FINANCE_EXTRACT', repository: 'FINANCE_REPO' },
        },
      });
      if (!resp.ok()) throw new Error(`create ds_job failed: ${resp.status()} ${await resp.text()}`);
    } finally {
      await ctx.dispose();
    }

    try {
      await authedPage.goto('/');
      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
      await authedPage.locator(`[data-testid="job-row-${jobName}-edit-btn"]`).click();

      const modal = authedPage.locator('[data-testid="job-modal"]');
      await expect(modal).toBeVisible();
      await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();

      const jobParamsInput = authedPage.locator('#a11y-launch-job-params-json-optional');
      await expect(jobParamsInput).toBeVisible();
      // Missing the opening quote before the key -- invalid JSON.
      await jobParamsInput.fill('{$G_BUSINESS_DATE":"10-Jun-2026"}');

      let putFired = false;
      authedPage.on('request', (req) => {
        if (req.method() === 'PUT' && req.url().includes(`/api/jobs/${jobName}`)) putFired = true;
      });

      await authedPage.locator('[data-testid="job-modal-save-btn"]').click();

      await expect(authedPage.locator('.toast-error .toast-title')).toContainText('Validation failed');
      await expect(authedPage.locator('.toast-error .toast-msg')).toContainText('Job Params must be valid JSON');
      // The modal stays open (save never went through) and no request was sent --
      // the fix is about surfacing the error, not silently retrying or partially saving.
      await expect(modal).toBeVisible();
      expect(putFired).toBe(false);

      await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
    } finally {
      const cleanupCtx = await authedContext(adminToken);
      try {
        await deleteJob(cleanupCtx, jobName);
      } finally {
        await cleanupCtx.dispose();
      }
    }
  });
});
