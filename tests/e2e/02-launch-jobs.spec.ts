import { test, expect } from './fixtures';
import { authedContext, deleteJob } from './api-helpers';

test.describe('02 launch/jobs', () => {
  // Names of jobs created (via UI or API) by the test that's about to run/just ran —
  // cleaned up in afterEach so a failed assertion mid-test still doesn't leak a job.
  // deleteJob() is fire-and-forget (see api-helpers.ts) so calling it on a name that
  // was never actually created (e.g. because the create step itself failed) is a
  // harmless no-op, not a second failure.
  const createdJobNames: string[] = [];

  test.afterEach(async ({ adminToken }) => {
    if (createdJobNames.length === 0) return;
    // Both api-helpers.ts's deleteJob() and the raw `request` fixture used below need
    // an authenticated context: /api/jobs sits behind BearerTokenMiddleware
    // (api/middleware/auth.py), and Playwright's built-in `request`/`authedContext()`
    // fixtures carry no Authorization header by default.
    const ctx = await authedContext(adminToken);
    try {
      while (createdJobNames.length) {
        await deleteJob(ctx, createdJobNames.pop()!);
      }
    } finally {
      await ctx.dispose();
    }
  });

  test('create a SQL-mode reconciliation job with key columns', async ({ authedPage }) => {
    const name = `e2e-job-${Date.now()}`;
    createdJobNames.push(name);

    await authedPage.goto('/');
    // currentView defaults to 'config' (frontend/app.js) -- the Job Catalog only
    // renders once the Launch tab (id 'jobs') is active.
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeVisible();

    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-query-textarea"]').fill('SELECT * FROM dbo.orders');
    // openNewJobModal() pre-fills key_columns_raw with 'id' already, but fill it
    // explicitly so this test stays correct if that default ever changes.
    await authedPage.locator('[data-testid="job-modal-key-columns-input"]').fill('id');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();

    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();
    await expect(authedPage.locator(`[data-testid="job-row-${name}"]`)).toBeVisible();
  });

  test('negative: saving without a query is blocked client-side (Save stays disabled)', async ({ authedPage }) => {
    // canSaveJob() for a SQL-mode reconciliation job requires BOTH m.query?.trim()
    // and non-empty key_columns_raw (frontend/app.js canSaveJob()). openNewJobModal()
    // pre-fills key_columns_raw with 'id', so with only the name filled in, Save is
    // disabled purely because the query is empty -- not because key columns are
    // missing (they aren't, by default).
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(`e2e-incomplete-${Date.now()}`);

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeDisabled();

    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();
  });

  test('negative: duplicate job name is rejected with the exact backend message', async ({ authedPage, request, adminToken }) => {
    const name = `e2e-dup-${Date.now()}`;
    createdJobNames.push(name);

    // Pre-create the job via an authenticated API call (POST /api/jobs is behind
    // BearerTokenMiddleware -- the bare `request` fixture has no Authorization
    // header of its own, so it must be attached per-call, matching the pattern
    // already used in 00-auth-setup.spec.ts).
    const seedResp = await request.post('/api/jobs', {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: { name, job_type: 'reconciliation', query: 'SELECT 1', key_columns: ['id'] },
    });
    expect(seedResp.ok()).toBeTruthy();

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-query-textarea"]').fill('SELECT 1');
    await authedPage.locator('[data-testid="job-modal-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();

    // saveJob() first calls validateJobDefinition() (POST /api/jobs/validate), which
    // is a required-fields check only (etl_framework/runner/job_validation.py) and
    // succeeds here, firing its own "Job definition valid" *success* toast. The
    // subsequent POST /api/jobs 409 then fires a separate *error* toast ("Save
    // failed" / "Job already exists"). Both toasts can be visible in the stack at
    // once, so scope to .toast-error to avoid a strict-mode violation on the
    // ambiguous plain .toast-title/.toast-msg locators.
    await expect(authedPage.locator('.toast-error .toast-title')).toContainText('Save failed');
    await expect(authedPage.locator('.toast-error .toast-msg')).toContainText('Job already exists');

    // The failed save leaves the modal open (saveJob() only closes it on success).
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();
  });

  test('negative: invalid key-column name is caught by validateJobModal()', async ({ authedPage }) => {
    // validateJobModal() (frontend/app.js) computes jobModalValidation.keyColumns via
    // the key-columns input's @input handler and Playwright's .fill() dispatches a
    // real 'input' event, so no extra keypress/blur is needed to trigger it.
    //
    // Nothing in the UI currently renders jobModalValidation.keyColumns (verified: no
    // DOM element consumes it anywhere in frontend/index.html) — it's computed but
    // silently unused, a real gap in the app. Per this plan's convention (see the
    // "Known pre-existing bug" note at the top of the plan doc, re: renderSrc/renderTgt),
    // test-writing tasks document app gaps rather than fix them, so this asserts on
    // Alpine's internal component state directly instead of adding new markup.
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(`e2e-badcol-${Date.now()}`);
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-query-textarea"]').fill('SELECT * FROM t');
    await authedPage.locator('[data-testid="job-modal-key-columns-input"]').fill('1invalid col!');

    await expect
      .poll(() =>
        authedPage.evaluate(() => {
          const root = document.querySelector('[x-data]') as HTMLElement;
          // Alpine v3's documented public API for reading a component's reactive
          // data from outside is Alpine.$data(el) — not the undocumented el.__x
          // internal.
          const data = (window as any).Alpine.$data(root);
          return data?.jobModalValidation?.keyColumns ?? null;
        })
      )
      .toBe('Invalid column name(s): 1invalid col!');

    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();
  });
});
