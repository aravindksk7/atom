import { test, expect } from './fixtures';
import { seedBaselineRun, deleteJob, authedContext } from './api-helpers';

test.describe('15 home and navigation', () => {
  test('sidebar groups tabs and highlights the active item', async ({ authedPage }) => {
    await authedPage.goto('/');
    await expect(authedPage.locator('[data-testid="nav-tab-home"]')).toHaveClass(/active/);
    await authedPage.locator('[data-testid="nav-tab-config"]').click();
    await expect(authedPage.locator('[data-testid="nav-tab-config"]')).toHaveClass(/active/);
    await expect(authedPage.locator('[data-testid="nav-tab-home"]')).not.toHaveClass(/active/);
  });

  test('sidebar collapse toggles width state', async ({ authedPage }) => {
    await authedPage.goto('/');
    const sidebar = authedPage.locator('[data-testid="app-sidebar"]');
    await expect(sidebar).not.toHaveClass(/is-collapsed/);
    await authedPage.locator('[data-testid="sidebar-collapse-btn"]').click();
    await expect(sidebar).toHaveClass(/is-collapsed/);
  });

  test('Home is the default landing view with stat cards', async ({ authedPage }) => {
    await authedPage.goto('/');
    await expect(authedPage.locator('[data-testid="home-view"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="home-stat-active-runs"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="home-stat-environments"]')).toBeVisible();
  });

  test('stat card navigates to the corresponding tab', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="home-stat-environments"]').click();
    await expect(authedPage.locator('[data-testid="nav-tab-config"]')).toHaveClass(/active/);
  });

  test('quick action opens the new-config modal on the Config tab', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="home-quick-action-new-config"]').click();
    await expect(authedPage.locator('[data-testid="nav-tab-config"]')).toHaveClass(/active/);
    await expect(authedPage.locator('[data-testid="config-new-btn"]')).toBeVisible();
  });

  test('theme toggle persists across reload', async ({ authedPage }) => {
    await authedPage.goto('/');
    await expect(authedPage.locator('html')).toHaveAttribute('data-theme', 'dark');
    await authedPage.locator('[data-testid="theme-toggle-btn"]').click();
    await expect(authedPage.locator('html')).toHaveAttribute('data-theme', 'light');
    await authedPage.reload();
    await expect(authedPage.locator('html')).toHaveAttribute('data-theme', 'light');
    // Each test gets a fresh browser context (no storageState reuse configured in
    // playwright.config.ts/fixtures.ts), so this persisted 'light' value in
    // localStorage cannot leak into any other test — no reset needed here.
  });

  // This test needs to deterministically observe zero runs, but the suite shares one
  // backend/DB across all spec files serially (playwright.config.ts) and earlier files
  // (e.g. 02-launch-jobs, 04-history) already create real runs there — by the time this
  // file executes, this.runs is essentially guaranteed to be non-empty. Mocking the API
  // response (same technique as 12-cross-cutting.spec.ts's offline-indicator test) makes
  // the empty-state branch actually exercised, instead of silently no-op'ing into a
  // vacuous "there are some rows" check.
  test('negative: zero runs shows the recent-activity empty state', async ({ authedPage }) => {
    await authedPage.route('**/api/runs', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );
    await authedPage.goto('/');
    const table = authedPage.locator('[data-testid="home-recent-runs-table"]');
    await expect(table).toContainText('No runs yet');
    await expect(table.locator('tbody tr')).toHaveCount(1);
  });

  test.describe('recent activity row navigation', () => {
    // adminToken is worker-scoped (fixtures.ts), so it's available to beforeAll/afterAll
    // hooks directly, unlike authedPage (test-scoped) — see 04-history.spec.ts for the
    // same pattern. workers:1 means there's exactly one adminToken for the whole run.
    let jobName: string;
    let runId: string;

    test.beforeAll(async ({ adminToken }) => {
      const ctx = await authedContext(adminToken);
      try {
        ({ jobName, runId } = await seedBaselineRun(ctx, 'e2e-home-nav'));
      } finally {
        await ctx.dispose();
      }
    });

    test.afterAll(async ({ adminToken }) => {
      if (!jobName) return;
      const ctx = await authedContext(adminToken);
      try {
        await deleteJob(ctx, jobName);
      } finally {
        await ctx.dispose();
      }
    });

    test('clicking a recent-activity row navigates into History for that run', async ({ authedPage }) => {
      await authedPage.goto('/');
      const row = authedPage.locator(`[data-testid="home-recent-run-row-${runId}"]`);
      await expect(row).toBeVisible();
      await row.click();
      await expect(authedPage.locator('[data-testid="nav-tab-history"]')).toHaveClass(/active/);
    });
  });
});
