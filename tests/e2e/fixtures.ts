import { test as base, expect, Page } from '@playwright/test';
import { bootstrapAdminToken } from './api-helpers';

let cachedToken: string | null = null;

export const test = base.extend<{ authedPage: Page; adminToken: string }>({
  adminToken: async ({}, use) => {
    if (!cachedToken) cachedToken = await bootstrapAdminToken();
    await use(cachedToken);
  },
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
