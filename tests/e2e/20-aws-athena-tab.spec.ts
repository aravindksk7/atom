import { test, expect } from './fixtures';

test.describe('20 AWS Athena tab', () => {
  test('runs mocked Athena query and creates tracked job with expected payload', async ({ authedPage }) => {
    await authedPage.route('**/api/configs', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 7, name: 'aws-dev', env_name: 'dev' }]),
      });
    });
    await authedPage.route('**/api/aws/athena/run-query', async (route) => {
      const request = route.request();
      expect(request.method()).toBe('POST');
      expect(request.postDataJSON()).toMatchObject({
        config_id: 7,
        database: 'curated',
        query: 'select id, amount from orders',
        output_location: 's3://athena-out/',
        max_rows: 100,
      });
      expect(request.postDataJSON()).not.toHaveProperty('min_rows');
      expect(request.postDataJSON()).not.toHaveProperty('max_rows_assert');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          query_execution_id: 'qid-1',
          status: {
            query_execution_id: 'qid-1',
            state: 'SUCCEEDED',
            engine_execution_time_ms: 42,
            data_scanned_bytes: 1024,
          },
          results: { columns: ['id', 'amount'], rows: [{ id: '1', amount: '10.5' }] },
          dq_metrics: {
            row_count: 1,
            columns: ['id', 'amount'],
            null_counts: { id: 0, amount: 0 },
            distinct_counts: { id: 1, amount: 1 },
            numeric: { amount: { min: 10.5, max: 10.5, avg: 10.5 } },
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
    await authedPage.locator('[data-testid="aws-service-athena"]').click();
    await authedPage.locator('[data-testid="aws-config-select"]').selectOption('7');
    await authedPage.locator('[data-testid="aws-athena-database-input"]').fill(' curated ');
    await authedPage.locator('[data-testid="aws-athena-query-input"]').fill('  select id, amount from orders  ');
    await authedPage.locator('[data-testid="aws-athena-output-location-input"]').fill(' s3://athena-out/ ');
    await authedPage.locator('[data-testid="aws-athena-min-rows-input"]').fill('1');

    await authedPage.locator('[data-testid="aws-athena-run-query-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-athena-result"]')).toContainText('SUCCEEDED');
    await expect(authedPage.locator('[data-testid="aws-athena-result"]')).toContainText('Rows: 1');
    await expect(authedPage.locator('[data-testid="aws-athena-result"]')).toContainText('amount');

    await authedPage.locator('[data-testid="aws-athena-job-name-input"]').fill('e2e-athena-orders');

    await authedPage.locator('[data-testid="aws-athena-add-assertion-btn"]').click();
    await authedPage.locator('[data-testid="aws-athena-assertion-path"]').nth(0).fill('row_count');
    await authedPage.locator('[data-testid="aws-athena-assertion-operator"]').nth(0).selectOption('==');
    await authedPage.locator('[data-testid="aws-athena-assertion-value"]').nth(0).fill('1');

    await authedPage.locator('[data-testid="aws-athena-add-assertion-btn"]').click();
    await authedPage.locator('[data-testid="aws-athena-assertion-path"]').nth(1).fill('null_counts.amount');
    await authedPage.locator('[data-testid="aws-athena-assertion-operator"]').nth(1).selectOption('<=');
    await authedPage.locator('[data-testid="aws-athena-assertion-value"]').nth(1).fill('0');

    await authedPage.locator('[data-testid="aws-athena-add-assertion-btn"]').click();
    await authedPage.locator('[data-testid="aws-athena-assertion-path"]').nth(2).fill('numeric.amount.avg');
    await authedPage.locator('[data-testid="aws-athena-assertion-operator"]').nth(2).selectOption('between');
    await authedPage.locator('[data-testid="aws-athena-assertion-min"]').nth(0).fill('10');
    await authedPage.locator('[data-testid="aws-athena-assertion-max"]').nth(0).fill('20');

    await authedPage.locator('[data-testid="aws-athena-add-assertion-btn"]').click();
    await authedPage.locator('[data-testid="aws-athena-assertion-path"]').nth(3).fill('numeric.amount.max');
    await authedPage.locator('[data-testid="aws-athena-assertion-operator"]').nth(3).selectOption('==');
    await authedPage.locator('[data-testid="aws-athena-assertion-value"]').nth(2).fill('10.5');
    await authedPage.locator('[data-testid="aws-athena-assertion-tolerance"]').nth(1).fill('5%');

    await authedPage.locator('[data-testid="aws-athena-add-assertion-btn"]').click();
    await authedPage.locator('[data-testid="aws-athena-assertion-path"]').nth(4).fill('temp_to_remove');
    await authedPage.locator('[data-testid="aws-athena-remove-assertion-btn"]').nth(4).click();

    await authedPage.locator('[data-testid="aws-athena-create-job-btn"]').click();
    await expect.poll(() => jobBody).not.toBeNull();
    expect(jobBody.job_type).toBe('aws_athena_query');
    expect(jobBody.params).toMatchObject({
      config_id: 7,
      database: 'curated',
      query: 'select id, amount from orders',
      output_location: 's3://athena-out/',
      min_rows: 1,
      metric_assertions: {
        row_count: 1,
        'null_counts.amount': {
          operator: '<=',
          value: 0,
        },
        'numeric.amount.avg': {
          operator: 'between',
          min: 10,
          max: 20,
        },
        'numeric.amount.max': {
          operator: '==',
          value: 10.5,
          tolerance: '5%',
        },
      },
    });
    expect(jobBody.params.metric_assertions).not.toHaveProperty('temp_to_remove');
  });
});
