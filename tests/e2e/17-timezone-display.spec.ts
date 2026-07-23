import { test, expect } from './fixtures';

// Regression coverage for: app-wide timezone setting (saved to the DB via
// PUT /api/settings) not being reflected across every timestamp in the Web
// UI. Several call sites render a raw ISO string or use the browser's local
// timezone via `new Date().toLocaleString()` instead of routing through the
// `fmtDate()` helper, which is the only renderer that honors `appTimezone`.
//
// Both timestamps below are exact UTC hour boundaries; formatted in
// 'Pacific/Auckland' (UTC+12, no DST at this date) they land on a different
// calendar day and a 12-hour-shifted clock time than the raw UTC string, so
// any bypass of fmtDate() is unambiguous in the rendered text.
const TZ = 'Pacific/Auckland';
const NEXT_RUN_UTC = '2026-07-19T06:00:00Z';
const BREACH_OPENED_UTC = '2026-07-18T01:00:00Z';

// Match the browser context's default locale (Playwright/Chromium defaults to
// en-US regardless of host OS locale) rather than Node's `[]` system-default
// locale, which can pick a different field order/case on the machine running
// the test runner.
function expectedLocal(iso: string): string {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: TZ,
    year: 'numeric', month: 'numeric', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  }).format(new Date(iso));
}

test.describe('17 timezone display', () => {
  test('Scheduler report grid "Next Run" reflects the configured app timezone', async ({ authedPage }) => {
    await authedPage.route('**/api/settings', async (route) => {
      await route.fulfill({ json: { timezone: TZ, upload_retention_days: 30 } });
    });
    await authedPage.route('**/api/scheduler-reports/grid**', async (route) => {
      await route.fulfill({
        json: {
          rows: [{
            schedule_id: 101,
            schedule_name: 'nightly-recon',
            enabled: true,
            cron_expr: '0 6 * * *',
            last_status: 'PASSED',
            next_run_at: NEXT_RUN_UTC,
            last_duration_seconds: 12,
            last_exit_code: 0,
          }],
          warnings: [],
        },
      });
    });
    await authedPage.route('**/api/scheduler-reports/summary**', async (route) => {
      await route.fulfill({ json: { warnings: [] } });
    });
    await authedPage.route('**/api/scheduler-reports/timeline**', async (route) => {
      await route.fulfill({ json: { segments: [], warnings: [] } });
    });
    await authedPage.route('**/api/scheduler-reports/metrics**', async (route) => {
      await route.fulfill({ json: { warnings: [] } });
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-scheduler-reports"]').click();

    const grid = authedPage.locator('[data-testid="scheduler-reports-tab"]');
    await expect(grid).toContainText('nightly-recon');
    // Raw ISO must never leak into the DOM — that's the un-converted bypass.
    await expect(grid).not.toContainText(NEXT_RUN_UTC);
    await expect(grid).toContainText(expectedLocal(NEXT_RUN_UTC));
  });

  test('Contract breach history timestamp reflects the configured app timezone', async ({ authedPage }) => {
    const name = `e2e_tz_contract_${Date.now()}`;

    await authedPage.route('**/api/settings', async (route) => {
      await route.fulfill({ json: { timezone: TZ, upload_retention_days: 30 } });
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-contracts"]').click();
    await authedPage.locator('[data-testid="contracts-new-btn"]').click();
    await authedPage.locator('[data-testid="contract-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="contract-modal-source-job-input"]').fill('e2e_source_job');
    await authedPage.locator('[data-testid="contract-modal-owner-input"]').fill('e2e@test.local');
    await authedPage.locator('[data-testid="contract-modal-save-btn"]').click();
    await expect(authedPage.locator(`[data-testid="contract-row-${name}"]`)).toBeVisible();

    await authedPage.route(`**/api/contracts/${name}/breaches`, async (route) => {
      await route.fulfill({
        json: [{
          id: 1,
          breach_type: 'MISSING',
          opened_at: BREACH_OPENED_UTC,
          resolved_at: null,
          duration_hours: null,
          escalated: false,
        }],
      });
    });

    await authedPage.locator(`[data-testid="contract-row-${name}"]`).click();

    const breachHistory = authedPage.locator('.card').filter({ hasText: 'Breach History' });
    await expect(breachHistory).toContainText('MISSING');
    await expect(breachHistory).not.toContainText(BREACH_OPENED_UTC);
    await expect(breachHistory).toContainText(expectedLocal(BREACH_OPENED_UTC));

    authedPage.once('dialog', (d) => d.accept());
    await authedPage.locator('[data-testid="contract-delete-btn"]').click();
  });
});
