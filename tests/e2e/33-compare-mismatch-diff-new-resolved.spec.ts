import { test, expect } from './fixtures';
import {
  seedBaselineRun, createPassingFileJob, triggerRun, waitForTerminal,
  deleteJob, authedContext,
} from './api-helpers';

// 08e-compare-mismatch-diff.spec.ts only ever diffs two runs of the SAME deterministic
// fixture pair (source.csv vs target.csv) against each other, so mismatchDiffResult.new
// and .resolved are always empty -- only the "persistent" branch (and its own Load More
// button) is reachable there. Pairing a mismatched run against a byte-identical
// (zero-mismatch) run gives both the "resolved" direction (mismatched -> clean) and the
// "new" direction (clean -> mismatched) their first coverage.
test.describe('33 compare / mismatch diff: new & resolved branches', () => {
  let mismatchedJob: string;
  let mismatchedRunId: string;
  let cleanJob: string;
  let cleanRunId: string;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      ({ jobName: mismatchedJob, runId: mismatchedRunId } = await seedBaselineRun(ctx, 'e2e-mmdiff-nr-mismatched'));
      cleanJob = `e2e-mmdiff-nr-clean-${Date.now()}`;
      await createPassingFileJob(ctx, cleanJob);
      const { run_id } = await triggerRun(ctx, [cleanJob]);
      await waitForTerminal(ctx, run_id);
      cleanRunId = run_id as string;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      if (mismatchedJob) await deleteJob(ctx, mismatchedJob);
      if (cleanJob) await deleteJob(ctx, cleanJob);
    } finally {
      await ctx.dispose();
    }
  });

  test('mismatched run A vs clean run B: all 3 mismatches show as resolved', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-compare"]').click();
    await authedPage.locator('[data-testid="compare-subtab-mmdiff"]').click();
    await authedPage.locator('[data-testid="compare-mmdiff-run-a-input"]').fill(mismatchedRunId);
    await authedPage.locator('[data-testid="compare-mmdiff-run-b-input"]').fill(cleanRunId);
    await authedPage.locator('[data-testid="compare-mmdiff-run-btn"]').click();

    await expect(authedPage.locator('[data-testid="compare-mmdiff-new-count"]')).toHaveText('0');
    await expect(authedPage.locator('[data-testid="compare-mmdiff-resolved-count"]')).toHaveText('3');
    await expect(authedPage.locator('[data-testid="compare-mmdiff-persistent-count"]')).toHaveText('0');
    await expect(authedPage.getByText('Resolved Mismatches')).toBeVisible();
    await expect(authedPage.getByText('New Regressions')).toBeHidden();
  });

  test('clean run A vs mismatched run B: all 3 mismatches show as new', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-compare"]').click();
    await authedPage.locator('[data-testid="compare-subtab-mmdiff"]').click();
    await authedPage.locator('[data-testid="compare-mmdiff-run-a-input"]').fill(cleanRunId);
    await authedPage.locator('[data-testid="compare-mmdiff-run-b-input"]').fill(mismatchedRunId);
    await authedPage.locator('[data-testid="compare-mmdiff-run-btn"]').click();

    await expect(authedPage.locator('[data-testid="compare-mmdiff-new-count"]')).toHaveText('3');
    await expect(authedPage.locator('[data-testid="compare-mmdiff-resolved-count"]')).toHaveText('0');
    await expect(authedPage.locator('[data-testid="compare-mmdiff-persistent-count"]')).toHaveText('0');
    await expect(authedPage.getByText('New Regressions')).toBeVisible();
    await expect(authedPage.getByText('Resolved Mismatches')).toBeHidden();
  });
});
