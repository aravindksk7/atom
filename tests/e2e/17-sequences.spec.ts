import { test, expect } from './fixtures';

test.describe('Execution sequences', () => {
  test('build a two-branch sequence and attach it to a schedule', async ({ authedPage: page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await expect(page.getByTestId('sequences-panel')).toBeVisible();

    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill('e2e-pipeline');

    // Step 1 — the root.
    await page.getByTestId('sequence-step-job-0').selectOption({ index: 1 });
    await expect(page.getByTestId('sequence-step-id-0')).not.toHaveValue('');

    // Step 2 — depends on step 1.
    await page.getByTestId('sequence-add-step').click();
    await page.getByTestId('sequence-step-job-1').selectOption({ index: 1 });
    await page.getByTestId('sequence-step-id-1').fill('second');
    await page.getByTestId('sequence-step-deps-1').getByRole('checkbox').first().check();

    await expect(page.getByTestId('sequence-graph-preview')).toBeVisible();
    await page.getByTestId('sequence-save-btn').click();

    await expect(page.getByTestId('sequence-row-e2e-pipeline')).toBeVisible();
  });

  test('a cycle is rejected before saving', async ({ authedPage: page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill('e2e-cycle');

    await page.getByTestId('sequence-step-job-0').selectOption({ index: 1 });
    await page.getByTestId('sequence-step-id-0').fill('a');
    await page.getByTestId('sequence-add-step').click();
    await page.getByTestId('sequence-step-job-1').selectOption({ index: 1 });
    await page.getByTestId('sequence-step-id-1').fill('b');

    // b depends on a, then a depends on b — a cycle.
    await page.getByTestId('sequence-step-deps-1').getByRole('checkbox').first().check();
    await page.getByTestId('sequence-step-deps-0').getByRole('checkbox').first().check();

    await expect(page.getByTestId('sequence-global-error')).toContainText(/cycle/i);
    await expect(page.getByTestId('sequence-save-btn')).toBeDisabled();
  });
});
