import { test, expect } from './fixtures';
import { authedContext, createFileJob, deleteJob } from './api-helpers';

test.describe('13 run profile (shadow sampling)', () => {
  const createdJobNames: string[] = [];

  test.afterEach(async ({ adminToken }) => {
    if (createdJobNames.length === 0) return;
    const ctx = await authedContext(adminToken);
    try {
      while (createdJobNames.length) {
        await deleteJob(ctx, createdJobNames.pop()!);
      }
    } finally {
      await ctx.dispose();
    }
  });

  test('defaults to Full and hides the sample-fraction input', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();

    await expect(authedPage.locator('[data-testid="launch-run-profile-select"]')).toHaveValue('full');
    await expect(authedPage.locator('[data-testid="launch-shadow-sample-frac-input"]')).toBeHidden();
  });

  test('selecting Shadow reveals the sample-fraction input', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();

    await authedPage.locator('[data-testid="launch-run-profile-select"]').selectOption('shadow');
    await expect(authedPage.locator('[data-testid="launch-shadow-sample-frac-input"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="launch-shadow-sample-frac-input"]')).toHaveValue('0.02');
  });

  test('launching with Shadow profile sends run_profile and shadow_sample_frac to POST /api/runs', async ({ authedPage, adminToken }) => {
    const name = `e2e-shadow-${Date.now()}`;
    createdJobNames.push(name);
    const ctx = await authedContext(adminToken);
    try {
      await createFileJob(ctx, name);
    } finally {
      await ctx.dispose();
    }

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator(`[data-testid="job-row-${name}-checkbox"]`).click();

    await authedPage.locator('[data-testid="launch-run-profile-select"]').selectOption('shadow');
    await authedPage.locator('[data-testid="launch-shadow-sample-frac-input"]').fill('0.5');

    const [runRequest] = await Promise.all([
      authedPage.waitForRequest((req) => req.url().includes('/api/runs') && req.method() === 'POST'),
      authedPage.locator('[data-testid="run-tests-btn"]').click(),
    ]);
    const payload = runRequest.postDataJSON();
    expect(payload.run_settings.run_profile).toBe('shadow');
    expect(payload.run_settings.shadow_sample_frac).toBe(0.5);
  });
});
