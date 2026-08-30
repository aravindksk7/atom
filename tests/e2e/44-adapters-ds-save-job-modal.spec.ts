import { test, expect } from './fixtures';
import { createConfig, deleteConfig, deleteJob, authedContext } from './api-helpers';

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

test.describe('44 adapters -> launch: SAP DS save job modal and wiring', () => {
  test('DS lookup opens modal, customizes parameters/poll/timeout/tags, saves and persists to Job Catalog', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let cfgId: number;
    try {
      const cfg = await createConfig(ctx, `e2e-adapters-ds-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        ds_url: 'https://sapds.example.invalid:8443', ds_user: 'admin', ds_password: 'pwd',
      });
      cfgId = cfg.id;
    } finally {
      await ctx.dispose();
    }

    const jobName = `e2e_ds_job_${Date.now()}`;

    // Stub the lookup endpoint to return a valid DS job status
    await authedPage.route('**/api/adapters/sap-ds/lookup', (r) =>
      r.fulfill(json({
        identifier: 'JOB_FINANCE_EXTRACT',
        identifier_type: 'job_name',
        repository: 'FINANCE_REPO',
        status: 'PASSED',
        environment: 'dev',
        checked_at: '2026-08-30T00:00:00Z',
      }))
    );

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="ds-config-select"]').selectOption(String(cfgId!));
    await authedPage.locator('input[x-model="dsIdentifier"]').fill('JOB_FINANCE_EXTRACT');
    await authedPage.locator('[data-testid="ds-lookup-btn"]').click();

    // Verify lookup result card renders
    const resultCard = authedPage.locator('[data-testid="ds-result"]');
    await expect(resultCard).toBeVisible();
    await expect(resultCard).toContainText('JOB_FINANCE_EXTRACT');
    await expect(resultCard).toContainText('FINANCE_REPO');

    // Click "+ Add to Job Catalog" button -> should open the new SAP DS Job modal
    await resultCard.getByRole('button', { name: '+ Add to Job Catalog' }).click();

    const modal = authedPage.locator('[x-ref="dsJobDialog"]');
    await expect(modal).toBeVisible();
    await expect(authedPage.locator('#dsJobDialogTitle')).toHaveText('Add SAP Data Services Job');

    // Check pre-filled values
    await expect(authedPage.locator('#a11y-adapters-ds-job-name')).toHaveValue('ds_job_finance_extract');
    await expect(authedPage.locator('#a11y-adapters-ds-target-job')).toHaveValue('JOB_FINANCE_EXTRACT');
    await expect(authedPage.locator('#a11y-adapters-ds-repository')).toHaveValue('FINANCE_REPO');

    // Customize fields
    await authedPage.locator('#a11y-adapters-ds-job-name').fill(jobName);
    await authedPage.locator('#a11y-adapters-ds-description').fill('Finance Nightly Extraction Job');
    await authedPage.locator('#a11y-adapters-ds-job-params').fill('{"$G_RUN_DATE": "2026-08-30", "$G_BATCH_LIMIT": 1000}');
    await authedPage.locator('#a11y-adapters-ds-poll-interval').fill('8');
    await authedPage.locator('#a11y-adapters-ds-timeout').fill('480');
    await authedPage.locator('#a11y-adapters-ds-tags').fill('ds_job, finance, nightly');

    // Save job
    await modal.getByRole('button', { name: 'Save Job' }).click();

    // Modal should close on success
    await expect(modal).toBeHidden();

    try {
      // Navigate to Jobs tab and verify persisted job definition
      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
      const jobRow = authedPage.locator(`[data-testid="job-row-${jobName}"]`);
      await expect(jobRow).toBeVisible();

      // Open Edit Job modal
      await authedPage.locator(`[data-testid="job-row-${jobName}-edit-btn"]`).click();
      await expect(authedPage.locator('[data-testid="job-modal"]')).toBeVisible();
      await expect(authedPage.locator('[data-testid="job-modal-type-select"]')).toHaveValue('ds_job');
      await expect(authedPage.locator('[data-testid="job-modal-name-input"]')).toHaveValue(jobName);

      // Check Settings tab
      await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
      await expect(authedPage.locator('[data-testid="job-modal-ds-job-name-input"]')).toHaveValue('JOB_FINANCE_EXTRACT');
      await expect(authedPage.locator('#a11y-launch-repository-optional-falls-back-to-config')).toHaveValue('FINANCE_REPO');
      await expect(authedPage.locator('#a11y-launch-job-params-json-optional')).toHaveValue('{"$G_RUN_DATE":"2026-08-30","$G_BATCH_LIMIT":1000}');
      await expect(authedPage.locator('#a11y-launch-poll-interval-seconds-2')).toHaveValue('8');
      await expect(authedPage.locator('#a11y-launch-timeout-seconds-2')).toHaveValue('480');

      await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
    } finally {
      const cleanupCtx = await authedContext(adminToken);
      try {
        await deleteJob(cleanupCtx, jobName);
        await deleteConfig(cleanupCtx, cfgId!);
      } finally {
        await cleanupCtx.dispose();
      }
    }
  });

  test('negative: invalid JSON in Job Parameters shows error and does not submit', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let cfgId: number;
    try {
      const cfg = await createConfig(ctx, `e2e-adapters-ds-neg-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        ds_url: 'https://sapds.example.invalid:8443', ds_user: 'admin', ds_password: 'pwd',
      });
      cfgId = cfg.id;
    } finally {
      await ctx.dispose();
    }

    await authedPage.route('**/api/adapters/sap-ds/lookup', (r) =>
      r.fulfill(json({
        identifier: 'JOB_TEST_INVALID_JSON',
        identifier_type: 'job_name',
        repository: '',
        status: 'PASSED',
        environment: 'dev',
        checked_at: '2026-08-30T00:00:00Z',
      }))
    );

    let fromSapDsCalled = false;
    await authedPage.route('**/api/adapters/jobs/from-sap-ds', (r) => {
      fromSapDsCalled = true;
      return r.fulfill(json({
        name: 'test',
        job_type: 'ds_job',
        params: { job_name: 'JOB_TEST_INVALID_JSON' },
      }));
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="ds-config-select"]').selectOption(String(cfgId!));
    await authedPage.locator('input[x-model="dsIdentifier"]').fill('JOB_TEST_INVALID_JSON');
    await authedPage.locator('[data-testid="ds-lookup-btn"]').click();

    const resultCard = authedPage.locator('[data-testid="ds-result"]');
    await expect(resultCard).toBeVisible();
    await resultCard.getByRole('button', { name: '+ Add to Job Catalog' }).click();

    const modal = authedPage.locator('[x-ref="dsJobDialog"]');
    await expect(modal).toBeVisible();

    try {
      // Fill invalid JSON
      await authedPage.locator('#a11y-adapters-ds-job-params').fill('{ invalid json }');
      await modal.getByRole('button', { name: 'Save Job' }).click();

      // Modal remains open and endpoint is not called
      await expect(modal).toBeVisible();
      expect(fromSapDsCalled).toBe(false);

      // Test disabling when job name is empty
      await authedPage.locator('#a11y-adapters-ds-job-name').fill('');
      await expect(modal.getByRole('button', { name: 'Save Job' })).toBeDisabled();
      await authedPage.locator('#a11y-adapters-ds-job-name').fill('ds_valid_name');

      // Test Escape key closes modal
      await authedPage.keyboard.press('Escape');
      await expect(modal).toBeHidden();
    } finally {
      const cleanupCtx = await authedContext(adminToken);
      try {
        await deleteConfig(cleanupCtx, cfgId!);
      } finally {
        await cleanupCtx.dispose();
      }
    }
  });
});
