import { test, expect } from './fixtures';
import { seedBaselineRun, deleteJob, authedContext } from './api-helpers';

test.describe('06 reports', () => {
  // adminToken is worker-scoped (fixtures.ts), so it's available to beforeAll/afterAll
  // hooks directly -- see 04-history.spec.ts for the full rationale.
  let jobName: string;
  let runId: string;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      ({ jobName, runId } = await seedBaselineRun(ctx, 'e2e-reports'));
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    if (!jobName) return; // beforeAll never got past seedBaselineRun() -- nothing to clean up
    const ctx = await authedContext(adminToken);
    try {
      await deleteJob(ctx, jobName);
    } finally {
      await ctx.dispose();
    }
  });

  test('loads the HTML report for the seeded run and renders real report content', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-reports"]').click();
    await authedPage.locator('[data-testid="reports-run-select"]').selectOption(runId);
    await authedPage.locator('[data-testid="reports-load-btn"]').click();

    // loadReport() fetches /api/runs/{id}/report as a blob and points the iframe at a
    // blob: URL (frontend/app.js loadReport()) -- frameLocator() reaches into that
    // frame's document the same way it would for a same-origin http(s) src.
    const frame = authedPage.frameLocator('[data-testid="reports-iframe"]');
    await expect(frame.locator('h1')).toHaveText('ETL Framework Execution Report');
    await expect(frame.locator('#header p', { hasText: 'Run ID:' })).toContainText(runId);

    // The report template (etl_framework/reporting/templates/report.html.j2) embeds
    // each stored mismatch as a <tr data-mismatch> row inside the query's collapsed
    // <details> section. createFileJob's fixtures deterministically produce exactly 3:
    // 1 value_diff, 1 missing_in_target, 1 missing_in_source (see api-helpers.ts).
    await expect(frame.locator('tr[data-mismatch]')).toHaveCount(3);

    // Rows are hidden by the browser's native collapsed-<details> behavior until
    // opened -- open the section and confirm a row is genuinely visible, not just
    // present in the DOM.
    await frame.locator('details summary').first().click();
    await expect(frame.locator('tr[data-mismatch]').first()).toBeVisible();
  });

  test('Metrics sub-tab shows the seeded run\'s pass rate and counts', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-reports"]').click();
    await authedPage.locator('[data-testid="reports-run-select"]').selectOption(runId);
    await authedPage.locator('[data-testid="reports-load-btn"]').click();
    await authedPage.locator('[data-testid="reports-subtab-metrics"]').click();

    // createFileJob's fixtures run a single query that FAILS (api-helpers.ts) --
    // build_run_report_snapshot().to_metrics() (api/services/run_report.py) reports
    // total_tests=1, passed=0, failed=1 for this run, and metricsPassRate() (app.js)
    // computes 0/1*100 = 0%.
    const metricCards = authedPage.locator('.metric-card');
    await expect(metricCards.filter({ hasText: 'Pass Rate' }).locator('.metric-value')).toHaveText('0%');
    await expect(metricCards.filter({ hasText: 'Passed' }).locator('.metric-value')).toHaveText('0');
    await expect(metricCards.filter({ hasText: 'Failed' }).locator('.metric-value')).toHaveText('1');

    // Refresh re-fetches the same /api/runs/{id}/metrics?format=json endpoint --
    // confirm the button round-trips without breaking the rendered numbers.
    await authedPage.locator('[data-testid="reports-metrics-refresh-btn"]').click();
    await expect(metricCards.filter({ hasText: 'Failed' }).locator('.metric-value')).toHaveText('1');
  });

  test('negative: loading a report for a non-existent run shows the exact 404 error toast', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-reports"]').click();

    // The run <select> only ever lists real runs (x-for="run in runs" in index.html),
    // so there's no way to pick a non-existent run through the UI. Set reportRunId
    // directly via Alpine's documented public API for reading/writing a component's
    // reactive state from outside -- the same pattern 02-launch-jobs.spec.ts uses for
    // state that isn't otherwise exposed in the DOM.
    const bogusRunId = '00000000-0000-0000-0000-000000000000';
    await authedPage.evaluate((id) => {
      const root = document.querySelector('[x-data]') as HTMLElement;
      (window as any).Alpine.$data(root).reportRunId = id;
    }, bogusRunId);

    await authedPage.locator('[data-testid="reports-load-btn"]').click();

    // loadReport() catches the GET /api/runs/{id}/report 404 and toasts
    // ('error', 'Failed to load report', e.message) -- e.message is apiBlob()'s
    // apiErrorMessage(err.detail, ...), and ArtifactService.generate_html_report()
    // (api/services/artifact_service.py) raises HTTPException(404, detail=f"Run
    // {run_id} not found.") verbatim, including the trailing period.
    await expect(authedPage.locator('.toast-error .toast-title')).toContainText('Failed to load report');
    await expect(authedPage.locator('.toast-error .toast-msg')).toContainText(`Run ${bogusRunId} not found.`);
  });
});
