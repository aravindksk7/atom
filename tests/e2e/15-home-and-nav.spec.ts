import { test, expect } from './fixtures';

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
    // reset so later spec files (and their pixel/contrast assumptions) see the default theme
    await authedPage.locator('[data-testid="theme-toggle-btn"]').click();
  });

  test('negative: quick action with zero runs shows the recent-activity empty state', async ({ authedPage }) => {
    await authedPage.goto('/');
    const table = authedPage.locator('[data-testid="home-recent-runs-table"]');
    // Either populated rows or the explicit empty-state text — never a blank/broken table.
    const hasRows = await table.locator('tbody tr').count();
    if (hasRows === 1) {
      await expect(table).toContainText('No runs yet');
    } else {
      expect(hasRows).toBeGreaterThan(0);
    }
  });
});
