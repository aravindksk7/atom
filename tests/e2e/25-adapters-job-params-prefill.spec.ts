import { test, expect } from './fixtures';
import { createConfig, deleteConfig, authedContext, deleteJob } from './api-helpers';

// Covers two Adapters-tab flows that had zero e2e coverage as of this writing:
//
// 1. Creating a job from a SAP BO report via "+ Job" carries whatever prompt
//    answers were already collected in the Adapters tab (frontend/features/adapters.js
//    saveBOJob()) into the new job's params.bo_parameters -- and the Launch tab's Edit
//    Job modal (openEditJobModal(), frontend/features/launch.js) reads that same field
//    back into jobModal.bo_parameters, pre-filling the Report Parameters panel.
//
// 2. A successful Automic lookup renders the result card and "+ Add to Job Catalog"
//    button -- previously only the *failure* toast path (05-adapters.spec.ts) had
//    coverage; Automic has no mock server in this repo, so the success path is
//    exercised here by stubbing POST /api/adapters/automic/lookup instead.
//
// Both job-creation endpoints under test (/api/adapters/jobs/from-bo-report,
// /api/adapters/jobs/from-automic) only persist a JobDefinition -- they never call out
// to SAP BO/Automic themselves -- so stubbing just the *discovery* calls (documents/
// reports/parameters, lookup) is enough to run this without E2E_LIVE_BACKENDS.

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

test.describe('25 adapters -> launch: BO report prompts carry into the job editor', () => {
  test('DateTime prompt answered in Adapters pre-fills Report Parameters in the Launch job editor', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let cfgId: number;
    try {
      const cfg = await createConfig(ctx, `e2e-adapters-prefill-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        bo_url: 'http://127.0.0.1:1', bo_user: 'u', bo_password: 'p',
      });
      cfgId = cfg.id;
    } finally {
      await ctx.dispose();
    }

    await authedPage.route('**/api/adapters/sap-bo/documents?**', (r) =>
      r.fulfill(json([{ id: '9001', name: 'Sales Orders', folder: 'Public' }])));
    await authedPage.route('**/api/adapters/sap-bo/documents/9001/reports**', (r) =>
      r.fulfill(json([{ id: '2', name: 'Orders', reportIndex: 0 }])));
    await authedPage.route('**/api/adapters/sap-bo/documents/9001/parameters**', (r) =>
      r.fulfill(json([{ id: 5, name: 'Run Date', type: 'DateTime', mandatory: true, default: '' }])));

    const jobName = `e2e-bo-prefill-${Date.now()}`;

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(cfgId!));
    await authedPage.getByRole('button', { name: 'Browse Documents' }).click();

    const doc = authedPage.locator('.bo-doc-item', { hasText: 'Sales Orders' });
    await expect(doc).toBeVisible();
    await doc.click();

    // toggleBODoc() fires loadBOReportParams() and the report-list fetch in parallel
    // (both stubbed above); the DateTime prompt input only renders once params land.
    // Both the date and text variants of the prompt row share the same bound `:id`
    // (only x-show toggles which is visible), so disambiguate on [type="date"].
    await authedPage.locator('input#bo-report-prompt-9001-0[type="date"]').fill('2026-06-02');

    const repRow = authedPage.locator('.bo-report-item', { hasText: 'Orders' });
    await expect(repRow).toBeVisible();
    await repRow.getByText('+ Job', { exact: true }).click();

    await authedPage.locator('#a11y-adapters-job-name').fill(jobName);
    await authedPage.getByRole('button', { name: 'Save Job' }).click();

    try {
      // saveBOJob() awaits loadJobs() then closes the modal on success -- wait on the
      // modal closing (rather than the job row) so a save failure fails fast on the
      // right assertion instead of timing out waiting for a row that never appears.
      await expect(authedPage.getByRole('heading', { name: 'Add SAP BO Report as Job' })).toBeHidden();

      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
      await expect(authedPage.locator(`[data-testid="job-row-${jobName}"]`)).toBeVisible();
      await authedPage.locator(`[data-testid="job-row-${jobName}-edit-btn"]`).click();
      await expect(authedPage.locator('[data-testid="job-modal"]')).toBeVisible();
      // job-modal-bo-params lives on the Settings sub-tab, not the modal's default
      // (Basic) tab -- matches 02-launch-jobs.spec.ts's convention of switching tabs
      // before touching any Settings-scoped field.
      await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();

      const params = authedPage.locator('[data-testid="job-modal-bo-params"]');
      await expect(params).toBeVisible();
      await expect(params.locator('input[type="date"]')).toHaveValue('2026-06-02');

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
});

test.describe('25b adapters: Automic successful lookup', () => {
  test('a passing lookup renders the result card and "+ Add to Job Catalog" creates the job', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let cfgId: number;
    try {
      const cfg = await createConfig(ctx, `e2e-automic-ok-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        automic_url: 'http://127.0.0.1:1', automic_user: 'u', automic_password: 'p',
      });
      cfgId = cfg.id;
    } finally {
      await ctx.dispose();
    }

    const identifier = 'ETL_NIGHTLY';
    const jobName = `automic_${identifier}`.toLowerCase();

    await authedPage.route('**/api/adapters/automic/lookup', (r) =>
      r.fulfill(json({
        identifier,
        identifier_type: 'job_name',
        status: 'PASSED',
        environment: 'dev',
        checked_at: new Date().toISOString(),
      })));

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="automic-config-select"]').selectOption(String(cfgId!));
    await authedPage.locator('[data-testid="automic-identifier-input"]').fill(identifier);
    await authedPage.locator('[data-testid="automic-lookup-btn"]').click();

    const result = authedPage.locator('[data-testid="automic-result"]');
    await expect(result).toBeVisible();
    await expect(result).toContainText(identifier);
    await expect(result).toContainText('PASSED');

    try {
      // lookupAutomic() itself already fired a "Lookup complete" success toast, so
      // scope to the one whose title actually contains "Job added" rather than
      // asserting on .toast-success generically (ambiguous, matches both toasts).
      await result.getByRole('button', { name: '+ Add to Job Catalog' }).click();
      await expect(authedPage.locator('.toast-success .toast-title', { hasText: 'Job added' })).toBeVisible();

      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
      await expect(authedPage.locator(`[data-testid="job-row-${jobName}"]`)).toBeVisible();
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
});
