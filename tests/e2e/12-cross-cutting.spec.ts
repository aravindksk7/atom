import { test, expect } from './fixtures';
import { bootstrapAdminToken, createFileJob, deleteJob, authedContext } from './api-helpers';

test.describe('12 cross-cutting', () => {
  test('offline indicator shows Offline when /api/health fails at load time', async ({ page }) => {
    const adminToken = await bootstrapAdminToken();
    await page.addInitScript((token) => window.sessionStorage.setItem('etl_token', token), adminToken);
    await page.route('**/api/health', (route) => route.abort());
    await page.goto('/');
    await expect(page.locator('text=Offline')).toBeVisible();
  });

  test('online indicator shows Connected under normal conditions', async ({ authedPage }) => {
    await authedPage.goto('/');
    await expect(authedPage.locator('[data-testid="auth-status-connected"]')).toContainText('Connected');
  });

  test('negative: an unregistered API path returns a plain 404', async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    const resp = await ctx.get('/api/this-route-does-not-exist-xyz');
    expect(resp.status()).toBe(404);
    await ctx.dispose();
  });

  test('log text is HTML-escaped before highlighting', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    const jobName = `e2e_xss_img_${Date.now()}`;
    await createFileJob(ctx, jobName);
    let dialogFired = false;
    authedPage.on('dialog', () => { dialogFired = true; });
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-logs"]').click();
    await authedPage.waitForTimeout(1000);
    expect(dialogFired).toBe(false);
    await deleteJob(ctx, jobName);
    await ctx.dispose();
  });

  test('negative: a job name does not execute when rendered in Lineage view', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    // Job names accept any string (JobDefinition.name has no character restriction),
    // so the real XSS payload goes straight into the name — createFileJob supplies
    // valid source/target file paths so the job actually gets created (a bare
    // params:{source_mode:'files'} POST 422s and silently never creates the job).
    const evilName = `e2e_lineage_xss_<img src=x onerror=alert(1)>_${Date.now()}`;
    await createFileJob(ctx, evilName);
    let dialogFired = false;
    authedPage.on('dialog', () => { dialogFired = true; });
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-history"]').click();
    await authedPage.locator('[data-testid="history-subtab-lineage"]').click();
    await authedPage.waitForTimeout(1000);
    expect(dialogFired).toBe(false);
    await deleteJob(ctx, evilName);
    await ctx.dispose();
  });
});
