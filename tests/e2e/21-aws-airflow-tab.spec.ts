import { test, expect } from './fixtures';

test.describe('21 AWS Airflow tab', () => {
  test('loads DAGs, runs Airflow DAG, and creates tracked job with expected payload', async ({ authedPage }) => {
    await authedPage.route('**/api/configs', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 8, name: 'aws-mwaa', env_name: 'prod' }]),
      });
    });

    await authedPage.route('**/api/aws/airflow/dags*', async (route) => {
      const request = route.request();
      expect(request.method()).toBe('GET');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          dags: [
            {
              dag_id: 'etl_orders_daily',
              description: 'Daily orders ETL',
              is_paused: false,
              schedule_interval: '@daily',
            },
          ],
        }),
      });
    });

    await authedPage.route('**/api/aws/airflow/dags/etl_orders_daily/run', async (route) => {
      const request = route.request();
      expect(request.method()).toBe('POST');
      expect(request.postDataJSON()).toMatchObject({
        config_id: 8,
        conf: { date: '2026-09-05' },
      });
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          dag_run_id: 'manual__2026-09-05',
          dag_id: 'etl_orders_daily',
          state: 'success',
          task_instances: [
            { task_id: 'extract_orders', state: 'success', duration: 1.2 },
            { task_id: 'transform_orders', state: 'success', duration: 2.5 },
          ],
        }),
      });
    });

    let triggerBody: any = null;
    await authedPage.route('**/api/aws/airflow/dags/etl_orders_daily/trigger', async (route) => {
      const request = route.request();
      expect(request.method()).toBe('POST');
      triggerBody = request.postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          dag_run_id: 'triggered__2026-09-05',
          dag_id: 'etl_orders_daily',
          state: 'queued',
          logical_date: '2026-09-05T00:00:00+00:00',
        }),
      });
    });

    let jobBody: any = null;
    await authedPage.route('**/api/jobs**', async (route) => {
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
    await authedPage.locator('[data-testid="aws-service-airflow"]').click();
    await authedPage.locator('[data-testid="aws-config-select"]').selectOption('8');

    await authedPage.locator('[data-testid="aws-airflow-load-dags-btn"]').click();
    await authedPage.locator('[data-testid="aws-airflow-dag-select"]').selectOption('etl_orders_daily');
    await expect(authedPage.locator('[data-testid="aws-airflow-dag-input"]')).toHaveValue('etl_orders_daily');

    await authedPage.locator('[data-testid="aws-airflow-conf-input"]').fill('{"date": "2026-09-05"}');

    await authedPage.locator('[data-testid="aws-airflow-trigger-btn"]').click();
    await expect.poll(() => triggerBody).not.toBeNull();
    expect(triggerBody.config_id).toBe(8);
    await expect(authedPage.locator('[data-testid="aws-airflow-result"]')).toContainText('queued');

    await authedPage.locator('[data-testid="aws-airflow-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-airflow-result"]')).toContainText('success');
    await expect(authedPage.locator('[data-testid="aws-airflow-result"]')).toContainText('extract_orders');
    await expect(authedPage.locator('[data-testid="aws-airflow-result"]')).toContainText('transform_orders');

    await authedPage.locator('[data-testid="aws-airflow-expected-status-select"]').selectOption('success');

    await authedPage.locator('[data-testid="aws-airflow-add-task-assertion-btn"]').click();
    await authedPage.locator('[data-testid="aws-airflow-task-assertion-id"]').nth(0).fill('extract_orders');
    await authedPage.locator('[data-testid="aws-airflow-task-assertion-state"]').nth(0).selectOption('success');

    await authedPage.locator('[data-testid="aws-airflow-add-task-assertion-btn"]').click();
    await authedPage.locator('[data-testid="aws-airflow-task-assertion-id"]').nth(1).fill('temp_task');
    await authedPage.locator('[data-testid="aws-airflow-remove-task-assertion-btn"]').nth(1).click();

    await authedPage.locator('[data-testid="aws-airflow-job-name-input"]').fill('e2e-airflow-orders');
    await authedPage.locator('[data-testid="aws-airflow-create-job-btn"]').click();

    await expect.poll(() => jobBody).not.toBeNull();
    expect(jobBody.job_type).toBe('airflow_dag_run');
    expect(jobBody.params).toMatchObject({
      config_id: 8,
      dag_id: 'etl_orders_daily',
      conf: { date: '2026-09-05' },
      expected_status: 'success',
      task_assertions: {
        extract_orders: 'success',
      },
    });
    expect(jobBody.params.task_assertions).not.toHaveProperty('temp_task');
  });

  test('validates required fields and invalid JSON config in Airflow panel', async ({ authedPage }) => {
    await authedPage.route('**/api/configs', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 8, name: 'aws-mwaa', env_name: 'prod' }]),
      });
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-aws"]').click();
    await authedPage.locator('[data-testid="aws-service-airflow"]').click();

    await expect(authedPage.locator('[data-testid="aws-airflow-load-dags-btn"]')).toBeDisabled();
    await expect(authedPage.locator('[data-testid="aws-airflow-run-btn"]')).toBeDisabled();

    await authedPage.locator('[data-testid="aws-config-select"]').selectOption('8');
    await expect(authedPage.locator('[data-testid="aws-airflow-load-dags-btn"]')).toBeEnabled();

    await authedPage.locator('[data-testid="aws-airflow-dag-input"]').fill('my_dag');
    await expect(authedPage.locator('[data-testid="aws-airflow-run-btn"]')).toBeEnabled();

    await authedPage.locator('[data-testid="aws-airflow-conf-input"]').fill('{invalid_json');
    await authedPage.locator('[data-testid="aws-airflow-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="aws-airflow-error"]')).toContainText('Config JSON must be valid JSON');
  });
});
