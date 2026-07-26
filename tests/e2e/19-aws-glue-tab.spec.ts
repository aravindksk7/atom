import { test, expect } from './fixtures';

test.describe('19 AWS Glue tab', () => {
  test('compares Glue catalog tables and creates tracked job with expected payload', async ({ authedPage }) => {
    await authedPage.route('**/api/configs', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 7, name: 'aws-dev', env_name: 'dev' }]),
      });
    });
    await authedPage.route('**/api/aws/glue/compare-tables', async (route) => {
      const request = route.request();
      expect(request.method()).toBe('POST');
      expect(request.postDataJSON()).toMatchObject({
        config_id: 7,
        source_database: 'raw',
        source_table: 'orders',
        target_database: 'curated',
        target_table: 'orders',
        compare_location: true,
        compare_formats: true,
        compare_partitions: true,
      });
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          match: false,
          source: {},
          target: {},
          diff: {
            missing_columns: ['amount'],
            extra_columns: ['status'],
            type_mismatches: [{ column: 'id', expected_type: 'int64', actual_type: 'string' }],
            partition_key_mismatches: [],
            location_mismatch: { source: 's3://raw', target: 's3://curated' },
            format_mismatch: null,
          },
        }),
      });
    });
    let jobBody: any = null;
    await authedPage.route('**/api/jobs', async (route) => {
      if (route.request().method() === 'POST') {
        jobBody = route.request().postDataJSON();
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ id: 1, name: jobBody.name }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      }
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-aws"]').click();
    await authedPage.locator('[data-testid="aws-service-glue"]').click();
    await authedPage.locator('[data-testid="aws-config-select"]').selectOption('7');
    await authedPage.locator('[data-testid="aws-glue-source-database-input"]').fill('raw');
    await authedPage.locator('[data-testid="aws-glue-source-table-input"]').fill('orders');
    await authedPage.locator('[data-testid="aws-glue-target-database-input"]').fill('curated');
    await authedPage.locator('[data-testid="aws-glue-target-table-input"]').fill('orders');

    await authedPage.locator('[data-testid="aws-glue-compare-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-glue-result"]')).toContainText('Missing columns: amount');
    await expect(authedPage.locator('[data-testid="aws-glue-result"]')).toContainText('Extra columns: status');
    await expect(authedPage.locator('[data-testid="aws-glue-result"]')).toContainText('id: int64 -> string');

    await authedPage.locator('[data-testid="aws-glue-job-name-input"]').fill('e2e-glue-orders');
    await authedPage.locator('[data-testid="aws-glue-create-job-btn"]').click();
    await expect.poll(() => jobBody).not.toBeNull();
    expect(jobBody.job_type).toBe('aws_glue_catalog_compare');
    expect(jobBody.params).toMatchObject({
      config_id: 7,
      source_database: 'raw',
      source_table: 'orders',
      target_database: 'curated',
      target_table: 'orders',
      compare_location: true,
      compare_formats: true,
      compare_partitions: true,
    });
  });
});
