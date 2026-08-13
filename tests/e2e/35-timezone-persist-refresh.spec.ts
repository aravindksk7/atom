import { test, expect } from './fixtures';
import { authedContext } from './api-helpers';

// Regression coverage for: Config -> "Regional — Timezone" is saved, but after a
// full page refresh the card shows UTC again instead of the saved zone. The
// setting is app-wide and stored server-side (PUT/GET /api/settings), so a
// refresh must re-render the persisted value, not the 'UTC' seed baked into the
// Alpine component.
const TZ = 'Pacific/Auckland';

async function openTimezoneCard(page) {
  await page.locator('[data-testid="nav-tab-config"]').click();
  const card = page.locator('.card').filter({ hasText: 'Regional — Timezone' });
  await expect(card).toBeVisible();
  // Card renders collapsed; the select only exists once expanded.
  if (!(await card.locator('select[aria-label="timezonedraft"]').count())) {
    await card.locator('.card-toggle').click();
  }
  return card;
}

test.describe('35 regional timezone persistence', () => {
  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    await ctx.put('/api/settings', { data: { timezone: 'UTC' } });
    await ctx.dispose();
  });

  test('saved timezone survives a page refresh', async ({ authedPage }) => {
    await authedPage.goto('/');

    const card = await openTimezoneCard(authedPage);
    await card.locator('select[aria-label="timezonedraft"]').selectOption(TZ);
    await card.getByRole('button', { name: 'Save' }).click();
    await expect(authedPage.locator('body')).toContainText('Timezone updated');

    // Server-side truth: the PUT persisted.
    const settings = await authedPage.request.get('/api/settings');
    expect((await settings.json()).timezone).toBe(TZ);

    await authedPage.reload();
    await expect(authedPage.locator('[data-testid="auth-status-connected"]')).toBeVisible();

    const cardAfter = await openTimezoneCard(authedPage);
    await expect(cardAfter.locator('select[aria-label="timezonedraft"]')).toHaveValue(TZ);
  });
});
