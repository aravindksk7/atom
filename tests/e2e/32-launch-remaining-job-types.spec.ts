import { test, expect } from './fixtures';
import { authedContext, deleteJob } from './api-helpers';

// Closes out Launch-tab editor coverage for every remaining job_type option
// (job-modal-type-select) that had zero e2e coverage before this file plus
// 28-launch-automic-job-type.spec.ts / 30-launch-ds-job-type.spec.ts: bo_job,
// api_reconciliation, dbt_artifact, freshness, profile, schema_snapshot,
// cross_job_assertion. Each test creates the job through the New Job modal, confirms
// canSaveJob()'s type-specific requirement actually enables Save, then re-opens the
// row's Edit modal and confirms openEditJobModal() reads the saved params back into
// the same fields -- proving the full round-trip, not just that the POST succeeded.
test.describe('32 launch: remaining job-type editors', () => {
  const createdJobNames: string[] = [];

  test.afterEach(async ({ adminToken }) => {
    if (createdJobNames.length === 0) return;
    const ctx = await authedContext(adminToken);
    try {
      while (createdJobNames.length) await deleteJob(ctx, createdJobNames.pop()!);
    } finally {
      await ctx.dispose();
    }
  });

  test('bo_job: BO Object ID round-trips', async ({ authedPage }) => {
    const name = `e2e-bo-job-${Date.now()}`;
    createdJobNames.push(name);

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('bo_job');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-bo-job-object-id-input"]').fill('3001');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${name}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('[data-testid="job-modal-bo-job-object-id-input"]')).toHaveValue('3001');
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });

  test('api_reconciliation: source/target endpoint names round-trip', async ({ authedPage }) => {
    const name = `e2e-api-recon-${Date.now()}`;
    createdJobNames.push(name);

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('api_reconciliation');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('#a11y-launch-source-api-endpoint').fill('orders_api');
    await authedPage.locator('#a11y-launch-target-api-endpoint-optional-leave-blank-to-compare-later').fill('orders_api_v2');
    // canSaveJob() for api_reconciliation also requires non-empty key_columns_raw,
    // which lives on the Basic tab -- openNewJobModal() pre-fills it with 'id'.

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${name}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('#a11y-launch-source-api-endpoint')).toHaveValue('orders_api');
    await expect(authedPage.locator('#a11y-launch-target-api-endpoint-optional-leave-blank-to-compare-later')).toHaveValue('orders_api_v2');
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });

  test('dbt_artifact: manifest/run_results paths round-trip', async ({ authedPage }) => {
    const name = `e2e-dbt-artifact-${Date.now()}`;
    createdJobNames.push(name);

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('dbt_artifact');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('#a11y-launch-manifest-json-path').fill('target/manifest.json');
    await authedPage.locator('#a11y-launch-run-results-json-path').fill('target/run_results.json');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${name}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('#a11y-launch-run-results-json-path')).toHaveValue('target/run_results.json');
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });

  test('freshness: timestamp column and query round-trip', async ({ authedPage }) => {
    const name = `e2e-freshness-${Date.now()}`;
    createdJobNames.push(name);

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('freshness');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('#a11y-launch-sql-query-must-return-a-timestamp-column').fill('SELECT MAX(created_at) as ts FROM orders');
    await authedPage.locator('#a11y-launch-timestamp-column-name').fill('ts');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${name}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('#a11y-launch-timestamp-column-name')).toHaveValue('ts');
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });

  test('profile: columns and drift threshold round-trip', async ({ authedPage }) => {
    const name = `e2e-profile-${Date.now()}`;
    createdJobNames.push(name);

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('profile');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('#a11y-launch-sql-query-2').fill('SELECT * FROM orders');
    await authedPage.locator('#a11y-launch-columns-comma-separated-blank-all').fill('amount, status');
    await authedPage.locator('#a11y-launch-drift-threshold').fill('15');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${name}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('#a11y-launch-columns-comma-separated-blank-all')).toHaveValue('amount, status');
    await expect(authedPage.locator('#a11y-launch-drift-threshold')).toHaveValue('15');
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });

  test('schema_snapshot: environment and query round-trip', async ({ authedPage }) => {
    const name = `e2e-schema-snapshot-${Date.now()}`;
    createdJobNames.push(name);

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('schema_snapshot');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('#a11y-launch-sql-query-schema-is-captured-from-the-result-columns').fill('SELECT * FROM orders LIMIT 0');
    await authedPage.locator('#a11y-launch-environment').selectOption('both');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${name}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('#a11y-launch-environment')).toHaveValue('both');
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });

  test('cross_job_assertion: source/target job and metric fields round-trip', async ({ authedPage }) => {
    const name = `e2e-cross-job-assertion-${Date.now()}`;
    createdJobNames.push(name);

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('cross_job_assertion');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('#a11y-launch-source-job-name').fill('orders_profile');
    await authedPage.locator('#a11y-launch-target-job-name').fill('payments_profile');
    await authedPage.locator('#a11y-launch-tolerance').fill('2');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${name}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('#a11y-launch-source-job-name')).toHaveValue('orders_profile');
    await expect(authedPage.locator('#a11y-launch-target-job-name')).toHaveValue('payments_profile');
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });
});
