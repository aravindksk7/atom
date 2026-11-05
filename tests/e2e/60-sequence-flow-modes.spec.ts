import { test, expect } from './fixtures';
import { authedContext, createFileJob } from './api-helpers';

let jobs: string[];

test.beforeAll(async ({ adminToken }) => {
  const ctx = await authedContext(adminToken);
  try {
    jobs = [];
    for (let i = 0; i < 4; i++) {
      jobs.push((await createFileJob(ctx, `e2e-flow-${i}-${Date.now()}`)).name);
    }
  } finally {
    await ctx.dispose();
  }
});

test.describe('Sequence flow modes', () => {
  test('build a hybrid with stages, reopen it, and launch with an override', async ({ authedPage: page, adminToken }) => {
    const name = `e2e-hybrid-${Date.now()}`;
    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill(name);
    await page.getByTestId('sequence-flow-custom').check();

    // Stage 1: one step.
    await page.getByTestId('sequence-step-job-0').selectOption(jobs[0]);
    await page.getByTestId('sequence-step-id-0').fill('extract');

    // Stage 2: two steps in parallel.
    await page.getByTestId('sequence-add-stage').click();
    await page.getByTestId('sequence-stage-mode-1').selectOption('parallel');
    await page.getByTestId('sequence-step-job-1').selectOption(jobs[1]);
    await page.getByTestId('sequence-step-id-1').fill('load_x');
    await page.getByTestId('sequence-stage-add-step-1').click();
    await page.getByTestId('sequence-step-job-2').selectOption(jobs[2]);
    await page.getByTestId('sequence-step-id-2').fill('load_y');

    // Stage 3: hand back to serial.
    await page.getByTestId('sequence-add-stage').click();
    await page.getByTestId('sequence-step-job-3').selectOption(jobs[3]);
    await page.getByTestId('sequence-step-id-3').fill('publish');
    await page.getByTestId('sequence-stage-rule-2').selectOption('all_done');
    await page.getByTestId('sequence-max-workers').fill('3');

    await expect(page.getByTestId('sequence-preview-together-1')).toBeVisible();
    await expect(page.getByTestId('sequence-save-btn')).toBeEnabled();
    await page.getByTestId('sequence-save-btn').click();

    await page.getByTestId(`sequence-row-${name}`).click();
    await expect(page.getByTestId('sequence-flow-badge')).toHaveText('Custom');
    const detail = page.getByTestId('sequence-detail');
    await expect(detail.locator('tbody tr').last()).toContainText('load_x, load_y');

    // Reopen: the stages come back from the saved edges.
    await page.getByTestId('sequence-edit-btn').click();
    await expect(page.getByTestId('sequence-flow-custom')).toBeChecked();
    await expect(page.getByTestId('sequence-stage-mode-1')).toHaveValue('parallel');
    await expect(page.getByTestId('sequence-stage-rule-2')).toHaveValue('all_done');
    await expect(page.getByTestId('sequence-max-workers')).toHaveValue('3');
    await page.getByTestId('sequence-editor').getByRole('button', { name: 'Cancel' }).click();

    await page.getByTestId('sequence-launch-btn').click();
    await page.getByTestId('launch-sequence-target-env').selectOption('dev');
    await page.getByTestId('launch-sequence-max-workers').fill('2');
    const launch = page.waitForResponse(
      (r) => /\/api\/sequences\/\d+\/launch$/.test(r.url()) && r.request().method() === 'POST'
    );
    await page.getByTestId('launch-sequence-submit-btn').click();
    const response = await launch;
    expect(response.ok()).toBeTruthy();
    expect(response.request().postDataJSON().max_workers).toBe(2);

    const ctx = await authedContext(adminToken);
    try {
      const { run_id } = await response.json();
      // The fixture jobs fail, so the first step fails and the run ends BLOCKED, which
      // waitForTerminal does not treat as final.
      await expect.poll(async () => String((await (await ctx.get(`/api/runs/${run_id}/status`)).json()).status), { timeout: 30_000 })
        .toMatch(/^(PASSED|FAILED|SLOW|ERROR|COMPLETED|CANCELLED|BLOCKED)$/);
      const steps = await (await ctx.get(`/api/runs/${run_id}/steps`)).json();
      const byId = Object.fromEntries(steps.map((s: any) => [s.step_id, s]));
      expect(byId.publish.depends_on).toEqual(['load_x', 'load_y']);
      expect(byId.load_x.depends_on).toEqual(['extract']);
    } finally {
      await ctx.dispose();
    }
  });

  test("the monitor shows each job's progress and a timeline", async ({ authedPage: page, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let runId = '';
    const sequenceName = `e2e-monitor-${Date.now()}`;
    try {
      const created = await ctx.post('/api/sequences', {
        data: {
          name: sequenceName,
          stages: [
            { mode: 'parallel', steps: [
              { step_id: 'left', job_name: jobs[0] },
              // The hold keeps the run active while the monitor is checked.
              { step_id: 'right', job_name: jobs[1], hold_after: true },
            ] },
            { mode: 'serial', steps: [{ step_id: 'join', job_name: jobs[2] }] },
          ],
        },
      });
      expect(created.ok()).toBe(true);
      const { id } = await created.json();
      const launched = await ctx.post(`/api/sequences/${id}/launch`, {
        data: { source_env: 'dev', target_env: 'dev' },
      });
      expect(launched.ok()).toBe(true);
      runId = (await launched.json()).run_id;

      await page.goto('/');
      await page.getByTestId('nav-tab-monitor').click();
      const run = page.getByTestId(`monitor-run-${runId}`);
      await expect(run).toBeVisible({ timeout: 30_000 });
      // Progress tab: every job has a row, and the held job offers Release.
      await expect(run.getByTestId('run-progress-rows')).toBeVisible({ timeout: 30_000 });
      await expect(run.getByTestId('monitor-step-left')).toBeVisible();
      const right = run.getByTestId('monitor-step-right');
      await expect(right.getByRole('button', { name: 'Release' })).toBeVisible({ timeout: 30_000 });
      await expect(run.getByTestId('monitor-step-join')).toContainText('queued');
      await expect(run.getByTestId('run-elapsed')).not.toHaveText('—');

      // The header names the sequence and shows Started once.
      await expect(run.getByTestId('monitor-run-name')).toHaveText(sequenceName);
      expect((await run.innerText()).match(/Started/g)).toHaveLength(1);

      // held_step only comes from /stream, so this proves the stream gets through auth.
      await expect(run.getByText('Sequence held at step')).toBeVisible({ timeout: 30_000 });

      // Bars and tabs need their CSS to mean anything, and losing it fails nothing else.
      const transparent = 'rgba(0, 0, 0, 0)';
      const rightFill = right.locator('.rt-bar-fill');
      await expect(rightFill).toBeVisible();
      await expect(rightFill).not.toHaveCSS('background-color', transparent);
      expect((await rightFill.boundingBox())!.height).toBeGreaterThan(0);
      await expect(run.getByTestId('run-tab-progress')).not.toHaveCSS('border-bottom-color', transparent);
      await expect(run.getByTestId('run-tab-timeline')).toHaveCSS('border-bottom-color', transparent);

      // Timeline tab: jobs that ran have bars, and the waiting job has none yet.
      await run.getByTestId('run-tab-timeline').click();
      await expect(run.getByTestId('run-progress-rows')).toBeHidden();
      await expect(run.getByTestId('run-tab-timeline')).not.toHaveCSS('border-bottom-color', transparent);
      await expect(run.getByTestId('run-tab-progress')).toHaveCSS('border-bottom-color', transparent);
      const leftBar = run.getByTestId('run-gantt-row-left').getByTestId('run-gantt-bar');
      await expect(leftBar).toBeVisible();
      await expect(leftBar).not.toHaveCSS('background-color', transparent);
      const box = (await leftBar.boundingBox())!;
      expect(box.height).toBeGreaterThan(0);
      expect(box.width).toBeGreaterThan(0);
      await expect(run.getByTestId('run-gantt-row-right').getByTestId('run-gantt-bar')).toBeVisible();
      await expect(run.getByTestId('run-gantt-row-right').getByTestId('run-gantt-hold')).toBeVisible();
      await expect(run.getByTestId('run-gantt-row-join').getByTestId('run-gantt-bar')).toHaveCount(0);
      // The status shows next to the job type, not only when the type is missing.
      await expect(run.getByTestId('run-gantt-row-right').locator('.rt-job-sub')).toContainText('HELD');

      // The color toggle swaps the legend and survives a reload.
      const legend = run.getByTestId('run-gantt-legend');
      await expect(legend).toBeVisible();
      await expect(legend).toContainText('Passed');
      await run.getByTestId('gantt-color-family').click();
      await expect(legend).toContainText('Reconciliation');
      await page.reload();
      await page.getByTestId('nav-tab-monitor').click();
      const reloaded = page.getByTestId(`monitor-run-${runId}`);
      await expect(reloaded).toBeVisible({ timeout: 30_000 });
      await reloaded.getByTestId('run-tab-timeline').click();
      await expect(reloaded.getByTestId('run-gantt-legend')).toContainText('Reconciliation');
    } finally {
      if (runId) await ctx.post(`/api/runs/${runId}/cancel`);
      await ctx.dispose();
    }
  });

  test('a graph that does not fit stages opens in advanced mode and warns before rewriting', async ({ authedPage: page, adminToken }) => {
    const ctx = await authedContext(adminToken);
    const name = `e2e-graph-${Date.now()}`;
    try {
      const created = await ctx.post('/api/sequences', {
        data: {
          name,
          steps: [
            { step_id: 'a', job_name: jobs[0], depends_on: [] },
            { step_id: 'b', job_name: jobs[1], depends_on: ['a'] },
            { step_id: 'c', job_name: jobs[2], depends_on: [] },
            { step_id: 'd', job_name: jobs[3], depends_on: ['c'] },
          ],
        },
      });
      expect(created.ok()).toBe(true);

      await page.goto('/');
      await page.getByRole('button', { name: 'Sequences' }).click();
      await page.getByTestId(`sequence-row-${name}`).click();
      await expect(page.getByTestId('sequence-flow-badge')).toHaveText('Custom');
      await page.getByTestId('sequence-edit-btn').click();

      const advanced = page.getByTestId('sequence-advanced-graph');
      await expect(advanced).toBeChecked();
      await expect(page.getByTestId('sequence-step-deps-1')).toBeVisible();

      // Dismissing keeps the graph as it is.
      page.once('dialog', (d) => d.dismiss());
      await advanced.click();
      await expect(advanced).toBeChecked();
      await expect(page.getByTestId('sequence-step-deps-1')).toBeVisible();

      // Accepting rewrites it to a serial flow.
      page.once('dialog', (d) => d.accept());
      await advanced.click();
      await expect(advanced).not.toBeChecked();
      await expect(page.getByTestId('sequence-flow-serial')).toBeChecked();
      await expect(page.getByTestId('sequence-step-deps-1')).toBeHidden();
    } finally {
      await ctx.dispose();
    }
  });

  // rerun_downstream: the checkbox reaches the API only when ticked. The run is
  // stubbed because this checks the control, and the behaviour is proved in
  // tests/integration/test_sequence_hybrid_flow.py.
  test('the History restart control sends rerun_downstream only when ticked', async ({ authedPage: page }) => {
    const runId = '00000000-0000-0000-0000-000000000060';
    const bodies: (string | null)[] = [];
    const run = {
      run_id: runId, status: 'FAILED', source_env: 'dev', target_env: 'dev',
      total_tests: 2, passed: 1, failed: 1, slow: 0, error: 0, results: [],
    };
    await page.route(`**/api/runs/${runId}`, (route) => route.fulfill({ json: run }));
    await page.route(`**/api/runs/${runId}/steps`, (route) => route.fulfill({
      json: [{ step_id: 'a', step_index: 0, job_name: 'a', status: 'FAILED', depends_on: [] }],
    }));
    await page.route(`**/api/runs/${runId}/restart`, async (route) => {
      if (route.request().method() === 'GET') return route.fulfill({ json: null });
      bodies.push(route.request().postData());
      return route.fulfill({ status: 404, json: { detail: 'stubbed' } });
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'History' }).click();
    await page.evaluate((id) => {
      const root = document.querySelector('[x-data]') as any;
      return (window as any).Alpine.$data(root).viewRunDetail(id);
    }, runId);

    const restart = page.getByRole('button', { name: 'Restart from failure' });
    await expect(restart).toBeVisible();
    const option = page.getByTestId('history-restart-rerun-downstream');
    const note = page.getByTestId('history-restart-rerun-downstream-note');
    await expect(option).not.toBeChecked();
    await expect(note).toBeHidden();

    await restart.click();
    await expect.poll(() => bodies.length).toBe(1);
    expect(bodies[0]).toBeFalsy();

    await option.check();
    await expect(note).toBeVisible();

    // Viewing the same run again (as Set baseline does) keeps the choice.
    await page.evaluate((id) => {
      const root = document.querySelector('[x-data]') as any;
      return (window as any).Alpine.$data(root).viewRunDetail(id);
    }, runId);
    await expect(option).toBeChecked();
    await expect(note).toBeVisible();

    await restart.click();
    await expect.poll(() => bodies.length).toBe(2);
    expect(JSON.parse(bodies[1]!)).toEqual({ rerun_downstream: true });
  });

  test('the Launch tab explains job order instead of offering a mode', async ({ authedPage: page }) => {
    await page.goto('/');
    await page.getByTestId('nav-tab-jobs').click();
    await expect(page.getByTestId('launch-order-note')).toBeVisible();
    await expect(page.locator('#a11y-launch-execution-mode')).toHaveCount(0);
    await page.getByTestId('launch-order-sequences-link').click();
    await expect(page.getByTestId('sequences-panel')).toBeVisible();
  });
});
