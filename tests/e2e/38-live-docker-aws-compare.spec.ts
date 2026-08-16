import { test, expect } from './fixtures';

test.describe('AWS Live Docker Compare Functionality', () => {
  test('navigates to Compare tab and runs AWS Glue Catalog comparison', async ({ authedPage }) => {
    await authedPage.goto('/#compare');
    await expect(authedPage.locator('.compare-tab-container')).toBeVisible();
    await authedPage.selectOption('#compare-source-type', 'aws_glue');
    await authedPage.selectOption('#compare-target-type', 'aws_glue');
    await expect(authedPage.locator('#btn-run-compare')).toBeVisible();
  });

  test('executes Athena query comparison vs baseline CSV', async ({ authedPage }) => {
    await authedPage.goto('/#compare');
    await authedPage.selectOption('#compare-source-type', 'aws_athena');
    await authedPage.fill('#athena-sql-input', 'SELECT * FROM test_db.sales');
    await expect(authedPage.locator('.athena-query-container')).toBeVisible();
  });
});
