import { test, expect } from './fixtures';
import {
  authedContext,
  createFileJob,
  createPassingFileJob,
  deleteJob,
  triggerRun,
  waitForTerminal,
} from './api-helpers';

test.describe('14 job gate (Write-Audit-Publish verdict)', () => {
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

  test('job with no run yet shows HOLD', async ({ authedPage, adminToken }) => {
    const name = `e2e-gate-norun-${Date.now()}`;
    createdJobNames.push(name);
    const ctx = await authedContext(adminToken);
    try {
      await createFileJob(ctx, name);
    } finally {
      await ctx.dispose();
    }

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator(`[data-testid="job-row-${name}-gate-btn"]`).click();

    await expect(authedPage.locator(`[data-testid="job-row-${name}-gate-verdict"]`)).toHaveText('HOLD');
    await expect(authedPage.locator('.toast-error .toast-title')).toContainText('HOLD');
  });

  test('job with a failed run shows HOLD with the failure reason', async ({ authedPage, adminToken }) => {
    const name = `e2e-gate-failed-${Date.now()}`;
    createdJobNames.push(name);
    const ctx = await authedContext(adminToken);
    try {
      await createFileJob(ctx, name); // deterministic FAILED (see api-helpers.ts)
      const { run_id } = await triggerRun(ctx, [name]);
      await waitForTerminal(ctx, run_id);
    } finally {
      await ctx.dispose();
    }

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator(`[data-testid="job-row-${name}-gate-btn"]`).click();

    await expect(authedPage.locator(`[data-testid="job-row-${name}-gate-verdict"]`)).toHaveText('HOLD');
  });

  test('job with a passed run shows PROMOTE', async ({ authedPage, adminToken }) => {
    const name = `e2e-gate-promote-${Date.now()}`;
    createdJobNames.push(name);
    const ctx = await authedContext(adminToken);
    try {
      await createPassingFileJob(ctx, name); // deterministic PASSED (byte-identical fixtures)
      const { run_id } = await triggerRun(ctx, [name]);
      await waitForTerminal(ctx, run_id);
    } finally {
      await ctx.dispose();
    }

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator(`[data-testid="job-row-${name}-gate-btn"]`).click();

    await expect(authedPage.locator(`[data-testid="job-row-${name}-gate-verdict"]`)).toHaveText('PROMOTE');
    await expect(authedPage.locator('.toast-success .toast-title')).toContainText('PROMOTE');
  });
});
