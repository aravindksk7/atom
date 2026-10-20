import { test, expect } from './fixtures';
import { authedContext, createPassingFileJob, deleteJob } from './api-helpers';

// A sequence step the app recorded as ERROR (stand-in for a SAP DS status poll
// that timed out while the job itself passed) is marked passed from Run
// History. The run continues as a new run: the marked step is carried over,
// not re-executed, and the downstream step that was BLOCKED now runs.
//
// The DS step errors deterministically: live connections are off by default,
// so a ds_job fails fast without contacting any server.

async function waitForStatus(
  ctx: Awaited<ReturnType<typeof authedContext>>,
  runId: string,
  wanted: string[],
  timeoutMs = 30_000,
) {
  const deadline = Date.now() + timeoutMs;
  let last = '';
  while (Date.now() < deadline) {
    const body = await (await ctx.get(`/api/runs/${runId}/status`)).json();
    last = String(body.status).toUpperCase();
    if (wanted.includes(last)) return body;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`run ${runId} stuck at ${last}, wanted ${wanted.join('/')}`);
}

test.describe('21 mark failed step passed & continue', () => {
  const stamp = Date.now();
  const extractJob = `e2e-mp-extract-${stamp}`;
  const dsJob = `e2e-mp-ds-load-${stamp}`;
  const publishJob = `e2e-mp-publish-${stamp}`;
  let sequenceId: number | undefined;
  let blockedRunId: string;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      await createPassingFileJob(ctx, extractJob);
      await createPassingFileJob(ctx, publishJob);
      const ds = await ctx.post('/api/jobs', {
        data: { name: dsJob, job_type: 'ds_job', params: { job_name: 'DS_NIGHTLY_LOAD' } },
      });
      expect(ds.ok(), await ds.text()).toBe(true);

      const seq = await ctx.post('/api/sequences', {
        data: {
          name: `e2e-mark-passed-${stamp}`,
          steps: [
            { step_id: 'extract', job_name: extractJob, depends_on: [] },
            { step_id: 'ds_load', job_name: dsJob, depends_on: ['extract'] },
            { step_id: 'publish', job_name: publishJob, depends_on: ['ds_load'] },
          ],
        },
      });
      expect(seq.ok(), await seq.text()).toBe(true);
      sequenceId = (await seq.json()).id;

      const launch = await ctx.post(`/api/sequences/${sequenceId}/launch`, {
        data: { source_env: 'dev', target_env: 'dev' },
      });
      expect(launch.ok(), await launch.text()).toBe(true);
      blockedRunId = (await launch.json()).run_id;
      await waitForStatus(ctx, blockedRunId, ['BLOCKED', 'ERROR', 'FAILED', 'PASSED']);
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      if (sequenceId !== undefined) await ctx.delete(`/api/sequences/${sequenceId}`);
      for (const name of [extractJob, dsJob, publishJob]) await deleteJob(ctx, name);
    } finally {
      await ctx.dispose();
    }
  });

  test('ERROR step is marked passed and the sequence continues without re-running it', async ({ authedPage: page, adminToken }) => {
    await page.goto('/');
    await page.locator('[data-testid="nav-tab-history"]').click();
    await page.locator('[data-testid="history-subtab-runs"]').click();
    await page.locator(`[data-testid="history-run-row-${blockedRunId}"]`).click();

    const steps = page.getByTestId('history-run-steps');
    await expect(steps).toBeVisible();
    const dsRow = steps.locator('tr[data-step-id="ds_load"]');
    const publishRow = steps.locator('tr[data-step-id="publish"]');
    await expect(steps.locator('tr[data-step-id="extract"]')).toContainText('PASSED');
    await expect(dsRow).toContainText('ERROR');
    await expect(publishRow).toContainText('BLOCKED');
    // Only the failed step offers the action.
    await expect(steps.getByTestId('mark-passed-btn').filter({ visible: true })).toHaveCount(1);

    await dsRow.getByTestId('mark-passed-btn').click();
    const modal = page.getByTestId('mark-passed-modal');
    await expect(modal).toBeVisible();
    await expect(modal).toContainText('will not be re-run');
    const submit = page.getByTestId('mark-passed-submit');
    await expect(submit).toBeDisabled();
    await page.getByTestId('mark-passed-by').fill('e2e-operator');
    await page.getByTestId('mark-passed-note').fill('Job succeeded in DS Management Console; poll read timed out');
    await expect(submit).toBeEnabled();

    const markResp = page.waitForResponse(
      (r) => r.url().includes('/steps/by-id/ds_load/mark-passed') && r.request().method() === 'POST',
    );
    await submit.click();
    const resp = await markResp;
    expect(resp.status()).toBe(202);
    const { run_id: newRunId, restarted_from_run_id } = await resp.json();
    expect(restarted_from_run_id).toBe(blockedRunId);
    expect(newRunId).not.toBe(blockedRunId);
    await expect(modal).toBeHidden();

    const ctx = await authedContext(adminToken);
    try {
      const terminal = await waitForStatus(ctx, newRunId, ['PASSED', 'FAILED', 'ERROR', 'BLOCKED', 'COMPLETED']);
      expect(['PASSED', 'COMPLETED']).toContain(String(terminal.status).toUpperCase());

      const newSteps = await (await ctx.get(`/api/runs/${newRunId}/steps`)).json();
      const byId = Object.fromEntries(newSteps.map((s: any) => [s.step_id, s]));
      expect(byId.ds_load.status).toBe('APPROVED');
      expect(byId.ds_load.carried_over).toBe(true);
      expect(byId.publish.status).toBe('PASSED');
      expect(byId.publish.carried_over).toBe(false);

      const original = await (await ctx.get(`/api/runs/${blockedRunId}/steps`)).json();
      const ds = original.find((s: any) => s.step_id === 'ds_load');
      expect(ds.status).toBe('APPROVED');
      expect(ds.release_note).toContain('poll read timed out');
    } finally {
      await ctx.dispose();
    }

    // UI reflects the continued run once it finishes (reload: the detail view
    // opened on submit showed it still PENDING).
    await page.goto('/');
    await page.locator('[data-testid="nav-tab-history"]').click();
    await page.locator('[data-testid="history-subtab-runs"]').click();
    await page.locator(`[data-testid="history-run-row-${newRunId}"]`).click();
    const newStepsCard = page.getByTestId('history-run-steps');
    await expect(newStepsCard.locator('tr[data-step-id="ds_load"]')).toContainText('APPROVED');
    await expect(newStepsCard.locator('tr[data-step-id="ds_load"]')).toContainText('carried over');
    await expect(newStepsCard.locator('tr[data-step-id="publish"]')).toContainText('PASSED');
    await expect(newStepsCard.getByTestId('mark-passed-btn').filter({ visible: true })).toHaveCount(0);
    await page.screenshot({ path: 'test-results/mark-step-passed-continued-run.png', fullPage: true });
  });
});
