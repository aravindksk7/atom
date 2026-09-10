import { test, expect } from './fixtures';
import { authedContext, createFileJob } from './api-helpers';

let seqJobName: string;

// The sequences panel never fetches its list on page load (frontend/features/
// sequences.js's loadSequences() only runs after a create/edit/archive action
// taken through the UI, not from app.js's loadAll() at boot) — so a sequence
// created purely via a direct API call would never show up as a
// `[data-testid^="sequence-row-"]` for the second test below to click. This
// mirrors 17-sequences.spec.ts's seeding: create the job via the API in
// beforeAll, then build the sequence itself through the same UI flow that
// suite uses, which is what actually populates the list.
test.beforeAll(async ({ adminToken }) => {
  const ctx = await authedContext(adminToken);
  try {
    seqJobName = (await createFileJob(ctx, `e2e-gitlab-ci-seq-job-${Date.now()}`)).name;
  } finally {
    await ctx.dispose();
  }
});

test.describe('GitLab CI Integration and Retry', () => {
  test('renders GitLab CI modal with snippet for a job selection', async ({ authedPage }) => {
    await authedPage.goto('/#launch');
    await expect(authedPage.locator('#btn-gitlab-ci-snippet')).toBeVisible();
    await authedPage.click('#btn-gitlab-ci-snippet');
    await expect(authedPage.locator('.gitlab-ci-modal')).toBeVisible();
    await expect(authedPage.locator('.gitlab-ci-snippet-code')).toContainText('run-atom-target.sh selection');
    await expect(authedPage.locator('.gitlab-signals-note')).toContainText('commit status');
    await expect(authedPage.locator('.gitlab-signals-note')).toContainText('CI_JOB_TOKEN');
  });

  test('renders GitLab CI modal with snippet for a sequence', async ({ authedPage }) => {
    const sequenceName = `e2e-gitlab-ci-seq-${Date.now()}`;

    await authedPage.goto('/#sequences');
    await expect(authedPage.getByTestId('sequences-panel')).toBeVisible();
    await authedPage.getByTestId('sequence-new-btn').click();
    await authedPage.getByTestId('sequence-name-input').fill(sequenceName);
    await authedPage.getByTestId('sequence-step-job-0').selectOption(seqJobName);
    await authedPage.getByTestId('sequence-save-btn').click();
    await expect(authedPage.getByTestId(`sequence-row-${sequenceName}`)).toBeVisible();

    await authedPage.getByTestId(`sequence-row-${sequenceName}`).click();
    await authedPage.getByTestId('sequence-ci-btn').click();
    await expect(authedPage.locator('.gitlab-ci-modal')).toBeVisible();
    await expect(authedPage.locator('.gitlab-ci-snippet-code')).toContainText('run-atom-target.sh sequence');
  });
});
