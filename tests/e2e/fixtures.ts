import { test as base, expect, Page } from '@playwright/test';
import { bootstrapAdminToken } from './api-helpers';

// Worker-scoped: reads the admin token once per worker, not once per test — this
// fixture is only ever used by the `chromium` project (see playwright.config.ts's
// `dependencies: ['setup']`), which always starts after the `setup` project
// (00-auth-setup.spec.ts) has already bootstrapped the token and written it to
// tests/e2e/.admin-token.json via primeAdminToken(). bootstrapAdminToken() reads
// that file rather than racing to POST /api/tokens itself — the backend only
// force-admins the very first unauthenticated token creation ever made against an
// empty DB, so by the time any test using this fixture runs, that one-time window
// is already spent by 00-auth-setup.spec.ts, on purpose.
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
