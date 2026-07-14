import { test as base, expect, Page } from '@playwright/test';
import { bootstrapAdminToken } from './api-helpers';

// Worker-scoped: bootstraps the admin token once per worker, not once per test.
// Correctness (not just performance) depends on this: POST /api/tokens only
// force-admins the *first* token ever created, and bootstrapAdminToken() always
// requests the same name ('e2e-admin') with no auth header. If this were
// test-scoped instead, or if playwright.config.ts's `workers` were ever raised
// above 1, multiple workers would race to bootstrap and only the first would win
// admin — every other worker would get a rejected/duplicate-name token. Worker
// scoping is what makes this safe regardless of the configured worker count.
export const test = base.extend<{ authedPage: Page }, { adminToken: string }>({
  adminToken: [
    async ({}, use) => {
      const token = await bootstrapAdminToken();
      await use(token);
    },
    { scope: 'worker' },
  ],
  authedPage: async ({ page, adminToken }, use) => {
    await page.addInitScript((token) => {
      window.sessionStorage.setItem('etl_token', token);
    }, adminToken);
    await page.goto('/');
    await expect(page.locator('[data-testid="auth-status-connected"]')).toBeVisible();
    await use(page);
  },
});

export { expect };
