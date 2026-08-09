import { test, expect } from './fixtures';
import { createConfig, deleteConfig, createFileJob, deleteJob, authedContext } from './api-helpers';

// Both sub-tabs here need a real external system (a second SAP-BO-less recon backend
// pair / a live SQL Server) to exercise a genuine positive path end to end, so — same
// convention as 08a-compare-bo-report.spec.ts's "live source prompts" test — the
// launch/poll network calls are stubbed with page.route() instead of gating the whole
// describe block behind E2E_LIVE_BACKENDS. That still proves the frontend wiring
// (payload assembly, polling, result rendering) without needing Docker.

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

test.describe('26 compare / dual-env recon (mocked, non-live)', () => {
  test('launching a pair against two configs renders improved/regressed/unchanged results', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let cfgAId: number, cfgBId: number, jobName: string;
    try {
      cfgAId = (await createConfig(ctx, `e2e-dualenv-a-${Date.now()}`, 'dev', { db_host: 'unused', db_password: 'unused' })).id;
      cfgBId = (await createConfig(ctx, `e2e-dualenv-b-${Date.now()}`, 'prod', { db_host: 'unused', db_password: 'unused' })).id;
      jobName = (await createFileJob(ctx, `e2e-dualenv-job-${Date.now()}`)).name;
    } finally {
      await ctx.dispose();
    }

    // launchDualEnv() POSTs, then immediately awaits one poll of
    // /api/compare/pairs/{pair_id} before its setInterval backup ever fires — so a
    // terminal status on the very first mocked response is enough; no need to wait
    // out a real 3s tick.
    await authedPage.route('**/api/compare/dual-env', (r) =>
      r.fulfill(json({ pair_id: 'stub-pair', run_id_a: 'run-a', run_id_b: 'run-b' })));
    await authedPage.route('**/api/compare/pairs/stub-pair', (r) =>
      r.fulfill(json({
        pair_id: 'stub-pair',
        run_a: { run_id: 'run-a', status: 'PASSED' },
        run_b: { run_id: 'run-b', status: 'FAILED' },
      })));
    await authedPage.route('**/api/runs/compare**', (r) =>
      r.fulfill(json({
        summary: { improved: 1, regressed: 2, unchanged: 3 },
        tests: [{ test_name: 'orders_recon', status_a: 'PASSED', status_b: 'FAILED' }],
      })));

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-compare"]').click();
    await authedPage.locator('[data-testid="compare-subtab-recon"]').click();
    await authedPage.locator('[data-testid="compare-recon-mode-stored"]').click();
    await authedPage.locator('[data-testid="compare-recon-dualenv-config-a-select"]').selectOption(String(cfgAId!));
    await authedPage.locator('[data-testid="compare-recon-dualenv-config-b-select"]').selectOption(String(cfgBId!));
    await authedPage.locator('[data-testid="compare-recon-dualenv-jobs-select"]').selectOption([jobName!]);
    await authedPage.locator('[data-testid="compare-recon-dualenv-launch-btn"]').click();

    await expect(authedPage.locator('.compare-chip.chip-improved')).toContainText('1');
    await expect(authedPage.locator('.compare-chip.chip-regressed')).toContainText('2');
    await expect(authedPage.locator('.compare-chip.chip-unchanged')).toContainText('3');
    await expect(authedPage.getByText('orders_recon')).toBeVisible();

    const cleanup = await authedContext(adminToken);
    try {
      await deleteConfig(cleanup, cfgAId!);
      await deleteConfig(cleanup, cfgBId!);
      await deleteJob(cleanup, jobName!);
    } finally {
      await cleanup.dispose();
    }
  });
});

test.describe('26b compare / SQL launch payload wiring (mocked, non-live)', () => {
  test('SQL compare launch posts the selected configs, queries, and key columns', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let cfgAId: number, cfgBId: number;
    try {
      cfgAId = (await createConfig(ctx, `e2e-sql-wire-a-${Date.now()}`, 'dev', { db_host: 'unused', db_password: 'unused' })).id;
      cfgBId = (await createConfig(ctx, `e2e-sql-wire-b-${Date.now()}`, 'dev', { db_host: 'unused', db_password: 'unused' })).id;
    } finally {
      await ctx.dispose();
    }

    let posted: any = null;
    await authedPage.route('**/api/compare/sql', (r) => {
      posted = JSON.parse(r.request().postData() || '{}');
      return r.fulfill(json({ run_id: 'stub-sql-run', status: 'PENDING' }));
    });
    await authedPage.route('**/api/runs/stub-sql-run/status', (r) =>
      r.fulfill(json({ run_id: 'stub-sql-run', status: 'ERROR' })));

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-compare"]').click();
    await authedPage.locator('[data-testid="compare-subtab-sql"]').click();
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(cfgAId!));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(cfgBId!));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELECT id, amount FROM orders');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id, amount FROM orders_v2');
    await authedPage.locator('#a11y-compare-key-columns-comma-separated-auto-inferred-if-blank').fill('id');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();

    await expect.poll(() => posted !== null).toBe(true);
    expect(posted.config_id_a).toBe(cfgAId);
    expect(posted.config_id_b).toBe(cfgBId);
    expect(posted.query_a).toBe('SELECT id, amount FROM orders');
    expect(posted.query_b).toBe('SELECT id, amount FROM orders_v2');
    expect(posted.key_columns).toEqual(['id']);

    const cleanup = await authedContext(adminToken);
    try {
      await deleteConfig(cleanup, cfgAId!);
      await deleteConfig(cleanup, cfgBId!);
    } finally {
      await cleanup.dispose();
    }
  });
});
