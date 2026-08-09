import { test, expect } from './fixtures';
import { authedContext, deleteJob } from './api-helpers';

// Non-reconciliation job types (automic_job, ds_job, bo_job, dbt_artifact, freshness,
// schema_snapshot, profile, cross_job_assertion, api_reconciliation) had zero Launch-tab
// editor coverage -- every existing 02-launch-jobs.spec.ts test only exercises
// 'reconciliation' (sql/files/bo_live/multi_file source modes) or 'bo_report'.
// automic_job is the simplest of the remaining types (two plain text fields, no file
// upload / live backend needed) so it's the cheapest representative to cover the
// job-type-select branch of openNewJobModal()/canSaveJob()/_buildJobRequestBody().
test.describe('28 launch: automic_job editor', () => {
  test('a new automic_job job can be created and its fields persist through the edit modal', async ({ authedPage, adminToken }) => {
    const name = `e2e-automic-job-${Date.now()}`;

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('automic_job');

    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('#a11y-launch-automic-job-name').fill('ETL_NIGHTLY_LOAD');
    await authedPage.locator('#a11y-launch-automic-run-id').fill('RUN_42');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    try {
      await expect(authedPage.locator(`[data-testid="job-row-${name}"]`)).toBeVisible();
      await authedPage.locator(`[data-testid="job-row-${name}-edit-btn"]`).click();
      await expect(authedPage.locator('[data-testid="job-modal"]')).toBeVisible();
      await expect(authedPage.locator('[data-testid="job-modal-type-select"]')).toHaveValue('automic_job');

      await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
      await expect(authedPage.locator('#a11y-launch-automic-job-name')).toHaveValue('ETL_NIGHTLY_LOAD');
      await expect(authedPage.locator('#a11y-launch-automic-run-id')).toHaveValue('RUN_42');

      await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
    } finally {
      const ctx = await authedContext(adminToken);
      try {
        await deleteJob(ctx, name);
      } finally {
        await ctx.dispose();
      }
    }
  });

  test('negative: an automic_job with neither job name nor run ID cannot be saved', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(`e2e-automic-incomplete-${Date.now()}`);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('automic_job');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeDisabled();

    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();
  });
});
