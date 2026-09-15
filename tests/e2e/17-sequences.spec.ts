import { test, expect } from './fixtures';
import { authedContext, createFileJob, waitForTerminal } from './api-helpers';

let firstJobName: string;
let secondJobName: string;

test.beforeAll(async ({ adminToken }) => {
  const ctx = await authedContext(adminToken);
  try {
    firstJobName = (await createFileJob(ctx, `e2e-sequence-first-${Date.now()}`)).name;
    secondJobName = (await createFileJob(ctx, `e2e-sequence-second-${Date.now()}`)).name;
  } finally {
    await ctx.dispose();
  }
});

test.describe('Execution sequences', () => {
  test('build a two-branch sequence and attach it to a schedule', async ({ authedPage: page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await expect(page.getByTestId('sequences-panel')).toBeVisible();

    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill('e2e-pipeline');

    // Step 1 — the root.
    await page.getByTestId('sequence-step-job-0').selectOption(firstJobName);
    await expect(page.getByTestId('sequence-step-id-0')).not.toHaveValue('');

    // Step 2 — depends on step 1.
    await page.getByTestId('sequence-add-step').click();
    await page.getByTestId('sequence-step-job-1').selectOption(secondJobName);
    await page.getByTestId('sequence-step-id-1').fill('second');
    await page.getByTestId('sequence-step-deps-1').getByRole('checkbox').first().check();

    await expect(page.getByTestId('sequence-graph-preview')).toBeVisible();
    await page.getByTestId('sequence-save-btn').click();

    await expect(page.getByTestId('sequence-row-e2e-pipeline')).toBeVisible();

    await page.getByRole('button', { name: 'Launch' }).click();
    await page.getByRole('button', { name: 'Schedules' }).click();
    await page.getByRole('button', { name: 'New Schedule' }).click();
    await page.getByLabel('Schedule Name *').fill('e2e-sequence-schedule');
    await page.getByRole('radio', { name: 'an execution sequence' }).check();
    await page.getByTestId('schedule-sequence-picker').selectOption({ label: 'e2e-pipeline' });
    await page.getByRole('dialog').getByRole('button', { name: 'Save' }).click();

    const schedule = page.getByRole('main').getByText('e2e-sequence-schedule', { exact: true });
    await expect(schedule).toBeVisible();
    await schedule.locator('xpath=ancestor::div[contains(@class, "card")][1]').getByRole('button', { name: 'Edit' }).click();
    await expect(page.getByRole('radio', { name: 'an execution sequence' })).toBeChecked();
    await expect(page.getByTestId('schedule-sequence-picker')).toHaveValue(/\d+/);
  });

  test('a cycle is rejected before saving', async ({ authedPage: page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill('e2e-cycle');

    await page.getByTestId('sequence-step-job-0').selectOption(firstJobName);
    await page.getByTestId('sequence-step-id-0').fill('a');
    await page.getByTestId('sequence-add-step').click();
    await page.getByTestId('sequence-step-job-1').selectOption(secondJobName);
    await page.getByTestId('sequence-step-id-1').fill('b');

    // b depends on a, then a depends on b — a cycle.
    await page.getByTestId('sequence-step-deps-1').getByRole('checkbox').first().check();
    await page.getByTestId('sequence-step-deps-0').getByRole('checkbox').first().check();

    await expect(page.getByTestId('sequence-global-error')).toContainText(/cycle/i);
    await expect(page.getByTestId('sequence-save-btn')).toBeDisabled();
  });

  test('preconditions round-trip through the editor', async ({ authedPage: page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill('e2e-gated');

    await page.getByTestId('sequence-step-job-0').selectOption({ index: 1 });

    await page.getByTestId('sequence-preconditions').click();   // open the details
    await page.getByTestId('precondition-window-toggle').check();
    await page.getByTestId('precondition-window-start').fill('01:00');
    await page.getByTestId('precondition-window-end').fill('05:00');
    await page.getByTestId('precondition-weekdays-toggle').check();
    await page.getByTestId('precondition-weekdays').getByRole('checkbox').first().check();

    await page.getByTestId('sequence-save-btn').click();
    await expect(page.getByTestId('sequence-row-e2e-gated')).toBeVisible();

    // Reopen and confirm the values survived the round trip.
    await page.getByTestId('sequence-row-e2e-gated').click();
    await page.getByTestId('sequence-edit-btn').click();
    await page.getByTestId('sequence-preconditions').click();
    await expect(page.getByTestId('precondition-window-start')).toHaveValue('01:00');
    await expect(page.getByTestId('precondition-window-end')).toHaveValue('05:00');
  });

  // A saved sequence was previously only runnable indirectly (wrapped in a Job
  // Selection, or attached to a Schedule) -- these two cover the direct
  // "Run sequence" launch path added to close that gap.
  test('run a sequence directly from its detail panel', async ({ authedPage: page, adminToken }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill('e2e-run-single');
    await page.getByTestId('sequence-step-job-0').selectOption(firstJobName);
    await page.getByTestId('sequence-save-btn').click();
    await expect(page.getByTestId('sequence-row-e2e-run-single')).toBeVisible();

    await page.getByTestId('sequence-launch-btn').click();
    // A plain reconciliation job needs a target_env (see SINGLE_ENV_JOB_TYPES
    // in api/services/job_env_validation.py) -- leaving it blank 422s.
    await page.getByTestId('launch-sequence-target-env').selectOption('dev');
    const responsePromise = page.waitForResponse(
      (r) => /\/api\/sequences\/\d+\/launch$/.test(r.url()) && r.request().method() === 'POST'
    );
    await page.getByTestId('launch-sequence-submit-btn').click();
    const response = await responsePromise;
    expect(response.ok()).toBeTruthy();
    const { run_id } = await response.json();
    expect(run_id).toBeTruthy();

    const ctx = await authedContext(adminToken);
    try {
      const terminal = await waitForTerminal(ctx, run_id);
      expect(terminal.status).toBeTruthy();
    } finally {
      await ctx.dispose();
    }
  });

  test('restores batch loop progress and lazily expands launched run steps', async ({ authedPage: page }) => {
    const batchId = 'batch-progress-e2e';
    const runId = '00000000-0000-0000-0000-000000000017';
    let batchFetches = 0;
    let stepFetches = 0;
    let batchLaunches = 0;

    await page.route('**/api/run-batches/**', async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      batchFetches += 1;
      await route.fulfill({
        json: {
          batch_id: batchId,
          status: 'RUNNING',
          variable_name: 'business_date',
          iterations: 2,
          completed: 0,
          runs: [
            { run_id: runId, status: 'RUNNING', iteration_index: 1, business_date: '2026-09-15', started_at: '2026-09-15T09:00:00Z', completed_at: null },
            { run_id: null, status: 'PENDING', iteration_index: 2, business_date: '2026-09-16', started_at: null, completed_at: null },
          ],
        },
      });
    });
    await page.route(`**/api/runs/${runId}/steps`, async (route) => {
      stepFetches += 1;
      if (stepFetches === 1) {
        await route.fulfill({ status: 503, json: { detail: 'temporarily unavailable' } });
        return;
      }
      await route.fulfill({
        json: [
          { step_id: 'extract', step_index: 0, job_name: 'Extract customers', status: 'PASSED' },
          { step_id: 'reconcile', step_index: 1, job_name: 'Reconcile customers', status: 'RUNNING' },
        ],
      });
    });
    await page.route('**/api/sequences/*/launch-batch', async (route) => {
      batchLaunches += 1;
      await route.fulfill({ status: 500, json: { detail: 'batch must not relaunch' } });
    });

    await page.evaluate(([key, value]) => localStorage.setItem(key, value), [
      'etl_recent_batches',
      JSON.stringify([batchId]),
    ]);
    await page.reload();
    await page.getByTestId('nav-tab-jobs').click();

    await expect(page.getByTestId('batch-progress-status')).toContainText('status RUNNING');
    const launchedLoop = page.getByTestId('batch-loop-1');
    await expect(launchedLoop).toContainText('Loop 1');
    await expect(launchedLoop).toContainText('2026-09-15');
    await expect(launchedLoop).toContainText('RUNNING');
    const pendingLoop = page.getByTestId('batch-loop-2');
    await expect(pendingLoop).toContainText('Loop 2');
    await expect(pendingLoop).toContainText('2026-09-16');
    await expect(pendingLoop).toContainText('PENDING');
    await expect(pendingLoop.getByRole('button')).toHaveCount(0);
    expect(stepFetches).toBe(0);

    await launchedLoop.getByRole('button', { name: 'Toggle loop 1 steps' }).click();
    const unavailable = launchedLoop.getByText('steps unavailable', { exact: true });
    await expect(unavailable).toBeVisible();
    await expect.poll(() => stepFetches).toBeGreaterThan(1);
    await expect(unavailable).toBeHidden();
    await expect(launchedLoop).toContainText('Extract customers');
    await expect(launchedLoop).toContainText('PASSED');
    await expect(launchedLoop).toContainText('Reconcile customers');

    const batchFetchesBeforeRemount = batchFetches;
    await page.getByTestId('nav-tab-home').click();
    await page.getByTestId('nav-tab-jobs').click();
    await expect(page.getByTestId('batch-progress-status')).toContainText('status RUNNING');
    await expect.poll(() => batchFetches).toBeGreaterThan(batchFetchesBeforeRemount);
    expect(batchLaunches).toBe(0);
  });

  test('repeat-launch a sequence across incrementing business dates', async ({ authedPage: page, adminToken }) => {
    const varName = `e2e_seq_batch_date_${Date.now()}`;
    const ctx = await authedContext(adminToken);
    let varId: number;
    try {
      const varResp = await ctx.post('/api/variables', {
        data: { name: varName, var_type: 'date', default_value: '2026-01-01', description: 'e2e sequence batch date' },
      });
      if (!varResp.ok()) throw new Error(`create variable failed: ${varResp.status()} ${await varResp.text()}`);
      varId = (await varResp.json()).id;

      await page.goto('/');
      await page.getByRole('button', { name: 'Sequences' }).click();
      await page.getByTestId('sequence-new-btn').click();
      await page.getByTestId('sequence-name-input').fill('e2e-run-batch');
      await page.getByTestId('sequence-step-job-0').selectOption(firstJobName);
      await page.getByTestId('sequence-save-btn').click();
      await expect(page.getByTestId('sequence-row-e2e-run-batch')).toBeVisible();

      await page.getByTestId('sequence-launch-btn').click();
      await page.getByTestId('launch-sequence-target-env').selectOption('dev');
      await page.getByTestId('sequence-repeat-toggle').check();
      await page.getByTestId('sequence-batch-variable-select').selectOption(varName);
      await page.getByTestId('sequence-batch-iterations').fill('2');
      // 'ignore' sidesteps weekend-dependent flakiness in this test -- the
      // weekend-skip/shift arithmetic itself is covered by
      // tests/unit/test_business_calendar.py, not re-verified here.
      await page.getByTestId('sequence-batch-weekend-policy').selectOption('ignore');

      const responsePromise = page.waitForResponse(
        (r) => /\/api\/sequences\/\d+\/launch-batch$/.test(r.url()) && r.request().method() === 'POST'
      );
      await page.getByTestId('launch-sequence-submit-btn').click();
      const response = await responsePromise;
      expect(response.ok()).toBeTruthy();
      const { batch_id, iterations } = await response.json();
      expect(batch_id).toBeTruthy();
      expect(iterations).toBe(2);

      await expect(page.getByTestId('batch-progress-panel')).toBeVisible();
      await expect
        .poll(async () => (await (await ctx.get(`/api/run-batches/${batch_id}`)).json()).status, { timeout: 30_000 })
        .toBe('COMPLETED');
    } finally {
      await ctx.delete(`/api/variables/${varId!}`);
      await ctx.dispose();
    }
  });
});
