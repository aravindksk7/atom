import { test, expect } from './fixtures';

test.describe('10 logs', () => {
  test('navigating to Logs starts auto-refresh polling', async ({ authedPage }) => {
    await authedPage.goto('/');
    const pollPromise = authedPage.waitForRequest((req) => req.url().includes('/api/logs'), { timeout: 8000 });
    await authedPage.locator('[data-testid="nav-tab-logs"]').click();
    await pollPromise;
  });

  test('level chip filters and the counter reflects filtered/total', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-logs"]').click();
    await expect(authedPage.locator('[data-testid="logs-counter"]')).toContainText('/');
    await authedPage.locator('[data-testid="logs-level-chip-ERROR"]').click();
    await expect(authedPage.locator('[data-testid="logs-level-chip-ERROR"]')).toHaveClass(/chip-active-ERROR/);
    await authedPage.locator('[data-testid="logs-level-chip-ERROR"]').click();
    await expect(authedPage.locator('[data-testid="logs-level-chip-ALL"]')).toHaveClass(/chip-active-ALL/);
  });

  test('negative: search matching nothing shows empty filter state', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-logs"]').click();
    await authedPage.locator('[data-testid="logs-search-input"]').fill('xyz_definitely_not_in_any_log_line_zzz');
    await expect(authedPage.locator('text=No events match the current filter.')).toBeVisible();
  });
});
