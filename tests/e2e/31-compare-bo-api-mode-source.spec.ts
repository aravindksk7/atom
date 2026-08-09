import { test, expect } from './fixtures';
import path from 'node:path';
import { createConfig, deleteConfig, authedContext } from './api-helpers';

const dataFile = (name: string) => path.join(__dirname, 'fixtures', 'data', name);
const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

// The BO sub-tab's "API" source mode (compare-bo-source-a-mode-api) had zero coverage --
// 08a-compare-bo-report.spec.ts only exercises live/upload. _buildBOSource('api', src)
// (frontend/features/compare.js) sends {source_type:'api', config_id, api_endpoint_name}
// with none of the doc/report fields the 'live' mode uses, so this is a distinct code
// path worth its own payload-wiring test (same "posted" pattern as 08a's live-prompts
// test -- no real REST endpoint is contacted since the launch itself is mocked).
test.describe('31 compare / BO report: API source mode', () => {
  test('selecting a config API endpoint for Source A posts source_type "api" with the endpoint name', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let cfgId: number;
    try {
      const cfg = await createConfig(ctx, `e2e-bo-api-source-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        api_endpoints: { orders_api: { base_url: 'http://127.0.0.1:1/orders' } },
      });
      cfgId = cfg.id;
    } finally {
      await ctx.dispose();
    }

    let posted: any = null;
    await authedPage.route('**/api/compare/bo-report', (r) => {
      posted = JSON.parse(r.request().postData() || '{}');
      return r.fulfill(json({ run_id: 'stub-run', status: 'PENDING' }));
    });
    await authedPage.route('**/api/runs/stub-run/status', (r) =>
      r.fulfill(json({ run_id: 'stub-run', status: 'ERROR' })));

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-compare"]').click();
    await authedPage.locator('[data-testid="compare-subtab-bo"]').click();

    await authedPage.locator('[data-testid="compare-bo-source-a-mode-api"]').click();
    await authedPage.getByLabel('bosourcea configid').selectOption(String(cfgId!));
    await authedPage.getByLabel('bosourcea endpointname').selectOption('orders_api');

    await authedPage.locator('[data-testid="compare-bo-source-b-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-b-upload-input"]').setInputFiles(dataFile('target.csv'));
    await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();

    await expect.poll(() => posted !== null).toBe(true);
    expect(posted.source_a).toEqual({
      source_type: 'api',
      config_id: cfgId,
      api_endpoint_name: 'orders_api',
    });

    const cleanup = await authedContext(adminToken);
    try { await deleteConfig(cleanup, cfgId!); } finally { await cleanup.dispose(); }
  });
});
