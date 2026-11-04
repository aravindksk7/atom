import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { createFileJob, triggerRun, waitForTerminal, deleteJob, authedContext } from './api-helpers';

const NOW = '2026-11-04T10:00:00+00:00';

function job(name: string, state: string, extra: Record<string, unknown> = {}) {
  return { name, state, rows: 0, started_at: null, finished_at: null, error: null, ...extra };
}

const RUNNING_JOBS = [
  job('curves', 'done', { rows: 8, started_at: NOW, finished_at: '2026-11-04T10:00:12+00:00' }),
  job('rates', 'failed', { rows: 2, started_at: NOW, finished_at: NOW, error: 'ORA-12541: no listener' }),
  job('prices', 'running', { started_at: NOW }),
  job('volumes', 'waiting'),
];

test.describe('Difference export progress', () => {
  let jobName = '';
  let runId = '';

  function exportBody(status: string, metadata: Record<string, unknown>) {
    return {
      export_id: 'exp-1', run_id: runId, format: 'csv', status, row_count: 0,
      error_message: null, artifact_path: null, created_at: NOW, started_at: NOW,
      completed_at: null, recomputed_at: null, metadata,
    };
  }

  test.beforeEach(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    jobName = `e2e-export-progress-${Date.now()}`;
    await createFileJob(ctx, jobName);
    ({ run_id: runId } = await triggerRun(ctx, [jobName]));
    await waitForTerminal(ctx, runId);
    await ctx.dispose();
  });

  test.afterEach(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    await deleteJob(ctx, jobName);
    await ctx.dispose();
  });

  async function mockExport(page: Page, finishWhen: () => boolean) {
    let polls = 0;
    await page.route(`**/api/runs/${runId}/differences/download*`, (route) =>
      route.fulfill({ status: 202, json: { requires_export_job: true } }));
    await page.route(`**/api/runs/${runId}/exports`, (route) =>
      route.fulfill({ status: 202, json: exportBody('RUNNING', { progress: { jobs: RUNNING_JOBS } }) }));
    await page.route(`**/api/runs/${runId}/exports/exp-1`, (route) => {
      polls += 1;
      if (!finishWhen()) {
        return route.fulfill({ json: exportBody('RUNNING', { progress: { jobs: RUNNING_JOBS } }) });
      }
      return route.fulfill({
        json: exportBody('COMPLETED', {
          recomputed_at: NOW,
          failed_jobs: ['rates'],
          progress: { jobs: RUNNING_JOBS.map((entry) => (entry.state === 'failed' ? entry : { ...entry, state: 'done' })) },
        }),
      });
    });
    await page.route(`**/api/runs/${runId}/exports/exp-1/download`, (route) =>
      route.fulfill({ contentType: 'text/csv', body: 'test_name\n' }));
    return () => polls;
  }

  async function openRun(page: Page) {
    await page.goto('/');
    await page.locator('[data-testid="nav-tab-history"]').click();
    await page.locator('[data-testid="history-subtab-runs"]').click();
    await page.locator(`[data-testid="history-run-row-${runId}"]`).click();
  }

  test('shows each job while running, then collapses to a summary with its gaps', async ({ authedPage }) => {
    let finished = false;
    await mockExport(authedPage, () => finished);
    await openRun(authedPage);

    await authedPage.getByRole('button', { name: /All differences CSV/ }).click();

    const panel = authedPage.locator('[data-testid="export-panel-csv"]');
    await expect(panel).toBeVisible();
    await expect(panel.locator('[data-testid="export-progress-bar"]')).toHaveAttribute('aria-valuenow', '50');
    const rows = panel.locator('[data-testid="export-job-row"]');
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(0)).toContainText('8 rows');
    await expect(rows.nth(1)).toContainText('ORA-12541');
    await expect(rows.nth(2)).toContainText('running for');
    await expect(rows.nth(3)).toContainText('waiting');

    const downloadPromise = authedPage.waitForEvent('download');
    finished = true;
    await downloadPromise;

    await expect(rows.first()).toBeHidden();
    await expect(panel.locator('[data-testid="export-rebuilt-notice"]')).toContainText('may differ from the original run');
    await expect(panel.locator('[data-testid="export-gap-warning"]')).toContainText('1 job');
  });

  test('keeps waiting past the old 8 minute limit', async ({ authedPage }) => {
    test.setTimeout(180_000);
    await authedPage.clock.install();
    let finished = false;
    const polls = await mockExport(authedPage, () => finished);
    await openRun(authedPage);

    await authedPage.getByRole('button', { name: /All differences CSV/ }).click();

    for (let done = 1; done <= 245; done += 1) {
      await expect.poll(polls).toBeGreaterThanOrEqual(done);
      await authedPage.clock.fastForward(2000);
    }
    await expect(authedPage.locator('[data-testid="export-panel-csv"]')).toBeVisible();
    await expect(authedPage.getByText('Export job timed out')).toHaveCount(0);

    const downloadPromise = authedPage.waitForEvent('download');
    finished = true;
    await authedPage.clock.fastForward(2000);
    await downloadPromise;
  });

  test('a brief API outage while polling is not reported as a failure', async ({ authedPage }) => {
    let outage = 3;
    await mockExport(authedPage, () => true);
    await authedPage.route(`**/api/runs/${runId}/exports/exp-1`, (route) => {
      if (outage > 0) {
        outage -= 1;
        return route.abort();
      }
      return route.fallback();
    });
    await openRun(authedPage);

    const downloadPromise = authedPage.waitForEvent('download');
    await authedPage.getByRole('button', { name: /All differences CSV/ }).click();
    await downloadPromise;

    await expect(authedPage.getByText('Full export failed')).toHaveCount(0);
  });
});
