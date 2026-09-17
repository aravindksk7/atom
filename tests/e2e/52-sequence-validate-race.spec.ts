import { test, expect } from './fixtures';
import { Page } from '@playwright/test';
import { authedContext, createFileJob } from './api-helpers';

// Regression coverage for the sequences.js validateSequenceSteps() race:
// every step/dependency edit fires its own POST /api/sequences/validate
// without waiting for the previous call to resolve. On a sequence with
// more than 10 jobs, wiring up dependencies means firing many of these in
// quick succession, and nothing guaranteed they'd resolve in request
// order. A stale response landing after a fresher one could overwrite
// sequenceIssues with outdated data, leaving the Save button disabled
// forever with no error shown.
//
// Real network timing won't reliably reproduce an inversion on a fast
// local API, so both tests below force one deterministically: intercept
// /api/sequences/validate and delay EARLIER requests longer than LATER
// ones, guaranteeing the first call's response arrives last.

const JOB_COUNT = 11; // "more than 10 different jobs"

let jobNames: string[];

test.beforeAll(async ({ adminToken }) => {
  const ctx = await authedContext(adminToken);
  try {
    jobNames = [];
    for (let i = 0; i < JOB_COUNT; i++) {
      jobNames.push((await createFileJob(ctx, `e2e-seq-race-${i}-${Date.now()}`)).name);
    }
  } finally {
    await ctx.dispose();
  }
});

/** Arms the inverted-delay interception described above on `page`. */
async function armOutOfOrderValidateResponses(page: Page): Promise<void> {
  let validateCalls = 0;
  await page.route('**/api/sequences/validate', async (route) => {
    const callIndex = validateCalls++;
    const delayMs = Math.max(0, 400 - callIndex * 60);
    const response = await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    await route.fulfill({ response });
  });
}

test.describe('Execution sequences — validate race', () => {
  test('save is not blocked when validate responses resolve out of order', async ({ authedPage: page }) => {
    await armOutOfOrderValidateResponses(page);

    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await expect(page.getByTestId('sequences-panel')).toBeVisible();

    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill('e2e-race-pipeline');

    // Step 0: the root, no deps.
    await page.getByTestId('sequence-step-job-0').selectOption(jobNames[0]);
    await expect(page.getByTestId('sequence-step-id-0')).not.toHaveValue('');

    // Steps 1..10: each depends on the one before it. Adding the step,
    // picking its job, and checking its "runs after" box each fire a
    // validate call -- with 11 jobs that's ~30 overlapping calls, more
    // than enough to guarantee an inversion even without the injected
    // delay skew above.
    for (let i = 1; i < JOB_COUNT; i++) {
      await page.getByTestId('sequence-add-step').click();
      await page.getByTestId(`sequence-step-job-${i}`).selectOption(jobNames[i]);
      await page.getByTestId(`sequence-step-id-${i}`).fill(`step-${i}`);
      // Depends on the immediately preceding step -- its checkbox is the
      // last one in this step's "runs after" list.
      await page.getByTestId(`sequence-step-deps-${i}`).getByRole('checkbox').last().check();
    }

    // Let every in-flight validate call (including the deliberately
    // slow, now-stale early ones) finish settling.
    await page.waitForTimeout(600);

    // The bug: a stale validate response overwrites sequenceIssues after
    // the real, final state was already validated clean, so the Save
    // button stays disabled and there's no visible error to explain why.
    await expect(page.getByTestId('sequence-global-error')).toHaveCount(0);
    await expect(page.getByTestId('sequence-save-btn')).toBeEnabled();

    await page.getByTestId('sequence-save-btn').click();
    await expect(page.getByTestId('sequence-row-e2e-race-pipeline')).toBeVisible();

    // Confirm it actually persisted server-side with all 11 steps and the
    // full dependency chain, not just that the modal closed.
    await page.getByTestId('sequence-row-e2e-race-pipeline').click();
    const detail = page.getByTestId('sequence-detail');
    await expect(detail).toBeVisible();
    await expect(detail.locator('tbody tr')).toHaveCount(JOB_COUNT);
    await expect(detail.locator('tbody tr').last()).toContainText(`step-${JOB_COUNT - 1}`);
    await expect(detail.locator('tbody tr').last()).toContainText(`step-${JOB_COUNT - 2}`);
  });

  // Negative counterpart: the token guard must only drop STALE responses,
  // never mask a genuinely invalid graph. A real cycle among the same 11
  // jobs, under the same out-of-order response injection, must still show
  // the cycle error and keep Save disabled -- proving the fix doesn't
  // achieve "never blocked" by ignoring real validation failures too.
  test('a real cycle still blocks save under the same out-of-order responses', async ({ authedPage: page }) => {
    await armOutOfOrderValidateResponses(page);

    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill('e2e-race-cycle');

    await page.getByTestId('sequence-step-job-0').selectOption(jobNames[0]);

    for (let i = 1; i < JOB_COUNT; i++) {
      await page.getByTestId('sequence-add-step').click();
      await page.getByTestId(`sequence-step-job-${i}`).selectOption(jobNames[i]);
      await page.getByTestId(`sequence-step-id-${i}`).fill(`step-${i}`);
      await page.getByTestId(`sequence-step-deps-${i}`).getByRole('checkbox').last().check();
    }

    // Close the loop: step 0 depends on the last step, turning the linear
    // chain into a cycle. Step 0's "runs after" list is every other step in
    // index order, so the last checkbox is the last step added.
    await page.getByTestId('sequence-step-deps-0').getByRole('checkbox').last().check();

    await page.waitForTimeout(600);

    await expect(page.getByTestId('sequence-global-error')).toContainText(/cycle/i);
    await expect(page.getByTestId('sequence-save-btn')).toBeDisabled();
  });
});
