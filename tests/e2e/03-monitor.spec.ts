import { test, expect } from './fixtures';
import { authedContext, createFileJob, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

test.describe('03 monitor', () => {
  // Names of jobs created by the test that's about to run/just ran -- cleaned up in
  // afterEach so a failed assertion mid-test still doesn't leak a job. Mirrors
  // 02-launch-jobs.spec.ts's pattern.
  const createdJobNames: string[] = [];

  test.afterEach(async ({ adminToken }) => {
    if (createdJobNames.length === 0) return;
    const ctx = await authedContext(adminToken);
    try {
      while (createdJobNames.length) {
        await deleteJob(ctx, createdJobNames.pop()!);
      }
    } finally {
      await ctx.dispose();
    }
  });

  test('trigger a run from the UI and see it appear with a terminal status', async ({ authedPage, adminToken }) => {
    // /api/jobs sits behind BearerTokenMiddleware -- use authedContext(adminToken)
    // (the established pattern from 02-launch-jobs.spec.ts), not the plain `request`
    // fixture, which carries no Authorization header.
    const ctx = await authedContext(adminToken);
    const jobName = `e2e-monitor-job-${Date.now()}`;
    try {
      await createFileJob(ctx, jobName);
      createdJobNames.push(jobName);
    } finally {
      await ctx.dispose();
    }

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-search-input"]').fill(jobName);
    await authedPage.locator(`[data-testid="job-row-${jobName}-checkbox"]`).click();
    await authedPage.locator('[data-testid="run-tests-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Run started');

    // runTests() (frontend/app.js) sets this.currentView = 'monitor' itself right
    // after the POST /api/runs succeeds -- no need to click nav-tab-monitor here.
    const runCard = authedPage.locator('[data-testid^="monitor-run-"]').first();
    await expect(runCard).toBeVisible();
    // A file-mode job (createFileJob) always completes in well under a second
    // server-side (no live DB involved) and always ends FAILED (1 value_diff, 1
    // missing_in_target, 1 missing_in_source -- see api-helpers.ts's createFileJob
    // doc). The UI picks this up either via the SSE 'done' event (startRunStream) or
    // the 5s poll fallback (pollActiveRuns), so 30s is a generous timeout.
    await expect(runCard).toContainText('FAILED', { timeout: 30_000 });
  });

  test('negative: Run Tests stays disabled with zero jobs selected', async ({ authedPage }) => {
    // runTests() itself is a silent no-op when selectedJobs is empty (`if
    // (!this.selectedJobs.length) return;`), but the run-tests-btn is also
    // `:disabled="selectedJobs.length === 0"` in the markup, so with zero jobs
    // selected the click never even reaches runTests() -- this is the only
    // observable assertion for "trigger with nothing selected".
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-search-input"]').fill('__no_such_job__');
    await expect(authedPage.locator('[data-testid="run-tests-btn"]')).toBeDisabled();
  });

  test('negative: cancelling an already-terminal run via the API returns 202 with cancel_requested:false', async ({ adminToken }) => {
    // The Cancel button (x-show="!isTerminalStatus(run.status)") is hidden entirely
    // once a run reaches a terminal status, so this can only be exercised via a
    // direct API call, not through the UI. runs.py's cancel_run() always returns
    // HTTP 202 (status_code=202 on the route decorator) with cancel_requested
    // reflecting RunRepository.request_cancel()'s return value, which is False when
    // the run is already in TERMINAL_STATUSES (etl_framework/repository/repository.py) --
    // never a 4xx/5xx for this case.
    const ctx = await authedContext(adminToken);
    const jobName = `e2e-monitor-cancel-${Date.now()}`;
    try {
      await createFileJob(ctx, jobName);
      createdJobNames.push(jobName);
      const { run_id } = await triggerRun(ctx, [jobName]);
      const terminalStatus = await waitForTerminal(ctx, run_id);
      expect(['PASSED', 'FAILED', 'SLOW', 'ERROR', 'COMPLETED', 'CANCELLED']).toContain(
        String(terminalStatus.status).toUpperCase()
      );

      const cancelResp = await ctx.post(`/api/runs/${run_id}/cancel`);
      expect(cancelResp.status()).toBe(202);
      expect((await cancelResp.json()).cancel_requested).toBe(false);
    } finally {
      await ctx.dispose();
    }
  });
});
