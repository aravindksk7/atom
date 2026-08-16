import { test, expect } from './fixtures';

test.describe('Files & SAP BO Live Docker Compare Functionality', () => {
  test('executes tabular multi-file reconciliation', async ({ authedPage }) => {
    await authedPage.goto('/#compare');
    await authedPage.click('#tab-multi-file-compare');
    await expect(authedPage.locator('.multi-file-mapping-panel')).toBeVisible();
  });

  test('triggers SAP BO live report compare with dynamic date prompts', async ({ authedPage }) => {
    await authedPage.goto('/#compare');
    await authedPage.selectOption('#compare-source-type', 'sap_bo_report');
    await expect(authedPage.locator('.sapbo-prompt-fields')).toBeVisible();
  });
});
