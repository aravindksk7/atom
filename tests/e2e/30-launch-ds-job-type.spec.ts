import { test, expect } from './fixtures';
import { authedContext, deleteJob } from './api-helpers';

// Second non-reconciliation job-type editor covered (see 28-launch-automic-job-type.spec.ts
// for the first, and its rationale). ds_job requires only ds_job_name to be saveable
// (canSaveJob(), frontend/features/launch.js), same shape as automic_job.
test.describe('30 launch: ds_job editor', () => {
  test('a new ds_job job can be created and its fields persist through the edit modal', async ({ authedPage, adminToken }) => {
    const name = `e2e-ds-job-${Date.now()}`;

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('ds_job');

    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-ds-job-name-input"]').fill('DS_NIGHTLY_LOAD');
    await authedPage.locator('#a11y-launch-repository-optional-falls-back-to-config').fill('DS_REPO');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    try {
      await expect(authedPage.locator(`[data-testid="job-row-${name}"]`)).toBeVisible();
      await authedPage.locator(`[data-testid="job-row-${name}-edit-btn"]`).click();
      await expect(authedPage.locator('[data-testid="job-modal"]')).toBeVisible();
      await expect(authedPage.locator('[data-testid="job-modal-type-select"]')).toHaveValue('ds_job');

      await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
      await expect(authedPage.locator('[data-testid="job-modal-ds-job-name-input"]')).toHaveValue('DS_NIGHTLY_LOAD');
      await expect(authedPage.locator('#a11y-launch-repository-optional-falls-back-to-config')).toHaveValue('DS_REPO');

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

  test('negative: a ds_job with no DS Job Name cannot be saved', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(`e2e-ds-job-incomplete-${Date.now()}`);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('ds_job');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeDisabled();

    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();
  });
});
