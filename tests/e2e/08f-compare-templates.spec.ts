import { test, expect } from './fixtures';

async function openCompare(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
}

test.describe('08f compare / templates', () => {
  test('built-in template is listed', async ({ authedPage }) => {
    await openCompare(authedPage);
    const templateNames = await authedPage.locator('[data-testid="compare-template-load-select"] option').allTextContents();
    expect(templateNames).toContain('Daily BO Report Compare');
  });

  test('save a custom template and reload persists it via localStorage', async ({ authedPage }) => {
    await openCompare(authedPage);
    const name = `e2e-template-${Date.now()}`;
    await authedPage.locator('[data-testid="compare-template-save-toggle-btn"]').click();
    await authedPage.locator('[data-testid="compare-template-name-input"]').fill(name);
    await authedPage.locator('[data-testid="compare-template-save-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Compare template saved');

    await authedPage.reload();
    await authedPage.locator('[data-testid="nav-tab-compare"]').click();
    await expect(authedPage.locator('[data-testid="compare-template-load-select"] option', { hasText: name })).toHaveCount(1);
  });

  test('negative: saving with an empty name shows the exact warn toast', async ({ authedPage }) => {
    await openCompare(authedPage);
    await authedPage.locator('[data-testid="compare-template-save-toggle-btn"]').click();
    await authedPage.locator('[data-testid="compare-template-save-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Template name required');
    await expect(authedPage.locator('.toast-msg')).toContainText('Enter a name for the compare template');
  });

  test('negative: "My Templates" optgroup is absent on a fresh session with no saved templates', async ({ page, adminToken }) => {
    await page.addInitScript((token) => window.sessionStorage.setItem('etl_token', token), adminToken);
    await page.goto('/');
    await page.evaluate(() => window.localStorage.removeItem('etl_compare_templates'));
    await page.reload();
    await openCompare(page);
    await expect(page.locator('optgroup[label="My Templates"]')).toBeHidden();
  });
});
