import { test, expect } from './fixtures';

const schedules = [
  {
    id: 101,
    name: 'nightly-recon',
    cron_expr: '0 6 * * *',
    job_sequence: ['orders'],
    source_env: 'dev',
    target_env: 'prod',
    enabled: true,
    last_run_at: '2026-07-18T06:00:00Z',
    next_run_at: '2026-07-19T06:00:00Z',
    selection_id: 201,
    selection_version: 1,
  },
];

const selections = [
  { id: 201, name: 'nightly-selection', description: '', job_count: 3, current_version: 1, tags: [] },
];

const statsPayload = {
  window_days: 30,
  generated_at: '2026-07-18T03:29:00Z',
  scheduler: { available: true, running: true, job_count: 1, timezone: 'UTC' },
  summary: {
    total_schedules: 1,
    enabled_schedules: 1,
    disabled_schedules: 0,
    runs_triggered: 2,
    passed: 1,
    failed: 1,
    error: 0,
    cancelled: 0,
    blocked: 0,
    success_rate: 50,
    average_duration_seconds: 90,
  },
  schedules: [
    {
      id: 101,
      name: 'nightly-recon',
      enabled: true,
      cron_expr: '0 6 * * *',
      registered: true,
      next_run_at: '2026-07-19T06:00:00Z',
      last_run_at: '2026-07-18T06:00:00Z',
      last_status: 'FAILED',
      runs_triggered: 2,
      passed: 1,
      failed: 1,
      error: 0,
      cancelled: 0,
      blocked: 0,
      success_rate: 50,
      average_duration_seconds: 90,
    },
  ],
  gate: { status: 'passed', exit_code: 0, reasons: [] },
};

test.describe('16 scheduler statistics', () => {
  test('Schedules tab renders scheduler statistics summary and per-schedule health', async ({ authedPage }) => {
    await authedPage.route('**/api/schedules/stats**', async (route) => {
      await route.fulfill({ json: statsPayload });
    });
    await authedPage.route('**/api/schedules', async (route) => {
      await route.fulfill({ json: schedules });
    });
    await authedPage.route('**/api/selections', async (route) => {
      await route.fulfill({ json: selections });
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.getByRole('button', { name: 'Schedules' }).click();

    const statsCard = authedPage.locator('.section-card').filter({ hasText: 'Scheduler Statistics' });
    await expect(statsCard).toContainText('Running');
    await expect(statsCard).toContainText('1');
    await expect(statsCard).toContainText('2');
    await expect(statsCard).toContainText('50.00%');
    await expect(statsCard).toContainText('1.5m');
    await expect(statsCard).toContainText('APScheduler jobs 1');

    const scheduleCard = authedPage.locator('.card').filter({ hasText: 'nightly-recon' });
    await expect(scheduleCard).toContainText('Last: FAILED');
    await expect(scheduleCard).toContainText('Rate: 50.00%');
    await expect(scheduleCard).toContainText('Registered: yes');
  });

  test('Schedules tab shows scheduler statistics load errors', async ({ authedPage }) => {
    await authedPage.route('**/api/schedules/stats**', async (route) => {
      await route.fulfill({ status: 500, json: { detail: 'stats failed' } });
    });
    await authedPage.route('**/api/schedules', async (route) => {
      await route.fulfill({ json: [] });
    });
    await authedPage.route('**/api/selections', async (route) => {
      await route.fulfill({ json: [] });
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.getByRole('button', { name: 'Schedules' }).click();

    const statsCard = authedPage.locator('.section-card').filter({ hasText: 'Scheduler Statistics' });
    await expect(statsCard).toContainText('stats failed');
  });
});
