import { test, expect } from './fixtures';

test.describe('11 help', () => {
  test('sidebar lists sections from window.ETL_HELP', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-help"]').click();
    const firstTitle = await authedPage.evaluate(() => (window as any).ETL_HELP.sections[0].title);
    await expect(authedPage.locator(`text=${firstTitle}`).first()).toBeVisible();
  });

  test('negative: search matching no topic shows the no-match message', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-help"]').click();
    await authedPage.locator('[data-testid="help-search-input"]').fill('zzz_no_such_help_topic_zzz');
    await expect(authedPage.locator('text=No help topics match')).toBeVisible();
  });
});
