import { test, expect } from './fixtures';
import { seedBaselineRun, deleteJob, authedContext } from './api-helpers';
import type { Page } from '@playwright/test';

test.describe('07 differences', () => {
  let jobName: string;
  let runId: string;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      ({ jobName, runId } = await seedBaselineRun(ctx, 'e2e-diff'));
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    if (!jobName) return;
    const ctx = await authedContext(adminToken);
    try {
      await deleteJob(ctx, jobName);
    } finally {
      await ctx.dispose();
    }
  });

  async function openDiffForRun(page: Page) {
    await page.goto('/');
    await page.locator('[data-testid="nav-tab-differences"]').click();
    await page.locator('[data-testid="diff-run-select"]').selectOption(runId);
    await page.locator('[data-testid="diff-test-select"]').selectOption({ index: 1 });
    await expect(page.locator('[data-testid="diff-results-table"]')).toContainText('value_diff');
  }

  test('browse and filter the known mismatch set', async ({ authedPage }) => {
    await openDiffForRun(authedPage);
    await authedPage.locator('[data-testid="diff-type-select"]').selectOption('missing_in_target');
    await expect(authedPage.locator('[data-testid="diff-results-table"] tbody tr')).toHaveCount(1);
    await authedPage.locator('[data-testid="diff-clear-filters-btn"]').click();
    await expect(authedPage.locator('[data-testid="diff-results-table"]')).toContainText('value_diff');
  });

  test('accept-all-filtered with a reason succeeds', async ({ authedPage }) => {
    await openDiffForRun(authedPage);
    await authedPage.locator('[data-testid="diff-accept-all-btn"]').click();
    await authedPage.locator('[data-testid="decision-modal-note-textarea"]').fill('e2e: accepted for test');
    await authedPage.locator('[data-testid="decision-modal-confirm-btn"]').click();
    await expect(authedPage.locator('[data-testid="diff-results-table"]')).toContainText('Accepted');
  });

  test('negative: zero matching rows disables both bulk buttons', async ({ authedPage }) => {
    await openDiffForRun(authedPage);
    await authedPage.locator('[data-testid="diff-search-input"]').fill('this-value-will-never-match-anything-xyz');
    await expect(authedPage.locator('[data-testid="diff-accept-all-btn"]')).toBeDisabled();
    await expect(authedPage.locator('[data-testid="diff-reject-all-btn"]')).toBeDisabled();
  });

  test('negative: confirming a decision with an empty reason shows Reason required', async ({ authedPage }) => {
    await openDiffForRun(authedPage);
    await authedPage.locator('[data-testid="diff-reject-all-btn"]').click();
    await authedPage.locator('[data-testid="decision-modal-confirm-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Reason required');
    await expect(authedPage.locator('.toast-msg')).toContainText('Enter a reason before deciding these mismatches');
  });
});
