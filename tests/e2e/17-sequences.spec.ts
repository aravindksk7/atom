import { test, expect } from './fixtures';
import { authedContext, createFileJob } from './api-helpers';

let firstJobName: string;
let secondJobName: string;

test.beforeAll(async ({ adminToken }) => {
  const ctx = await authedContext(adminToken);
  try {
    firstJobName = (await createFileJob(ctx, `e2e-sequence-first-${Date.now()}`)).name;
    secondJobName = (await createFileJob(ctx, `e2e-sequence-second-${Date.now()}`)).name;
  } finally {
    await ctx.dispose();
  }
});

test.describe('Execution sequences', () => {
  test('build a two-branch sequence and attach it to a schedule', async ({ authedPage: page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await expect(page.getByTestId('sequences-panel')).toBeVisible();

    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill('e2e-pipeline');

    // Step 1 — the root.
    await page.getByTestId('sequence-step-job-0').selectOption(firstJobName);
    await expect(page.getByTestId('sequence-step-id-0')).not.toHaveValue('');

    // Step 2 — depends on step 1.
    await page.getByTestId('sequence-add-step').click();
    await page.getByTestId('sequence-step-job-1').selectOption(secondJobName);
    await page.getByTestId('sequence-step-id-1').fill('second');
    await page.getByTestId('sequence-step-deps-1').getByRole('checkbox').first().check();

    await expect(page.getByTestId('sequence-graph-preview')).toBeVisible();
    await page.getByTestId('sequence-save-btn').click();

    await expect(page.getByTestId('sequence-row-e2e-pipeline')).toBeVisible();

    await page.getByRole('button', { name: 'Launch' }).click();
    await page.getByRole('button', { name: 'Schedules' }).click();
    await page.getByRole('button', { name: 'New Schedule' }).click();
    await page.getByLabel('Schedule Name *').fill('e2e-sequence-schedule');
    await page.getByRole('radio', { name: 'an execution sequence' }).check();
    await page.getByTestId('schedule-sequence-picker').selectOption({ label: 'e2e-pipeline' });
    await page.getByRole('dialog').getByRole('button', { name: 'Save' }).click();

    const schedule = page.getByRole('main').getByText('e2e-sequence-schedule', { exact: true });
    await expect(schedule).toBeVisible();
    await schedule.locator('xpath=ancestor::div[contains(@class, "card")][1]').getByRole('button', { name: 'Edit' }).click();
    await expect(page.getByRole('radio', { name: 'an execution sequence' })).toBeChecked();
    await expect(page.getByTestId('schedule-sequence-picker')).toHaveValue(/\d+/);
  });

  test('a cycle is rejected before saving', async ({ authedPage: page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill('e2e-cycle');

    await page.getByTestId('sequence-step-job-0').selectOption(firstJobName);
    await page.getByTestId('sequence-step-id-0').fill('a');
    await page.getByTestId('sequence-add-step').click();
    await page.getByTestId('sequence-step-job-1').selectOption(secondJobName);
    await page.getByTestId('sequence-step-id-1').fill('b');

    // b depends on a, then a depends on b — a cycle.
    await page.getByTestId('sequence-step-deps-1').getByRole('checkbox').first().check();
    await page.getByTestId('sequence-step-deps-0').getByRole('checkbox').first().check();

    await expect(page.getByTestId('sequence-global-error')).toContainText(/cycle/i);
    await expect(page.getByTestId('sequence-save-btn')).toBeDisabled();
  });
});
