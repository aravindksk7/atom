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
