import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig, deleteJob } from './api-helpers';

const stamp = Date.now();
const SRC = `e2e-bulk-src-${stamp}`;
const TGT = `e2e-bulk-tgt-${stamp}`;
const JOB_A = `e2e_bulk_a_${stamp}`;
const JOB_B = `e2e_bulk_b_${stamp}`;
const SELECTION = `e2e-bulk-sel-${stamp}`;

function sheet(rows: string[]): { name: string; mimeType: string; buffer: Buffer } {
  const header = 'job_name,source_config,source_table,target_config,target_table,key_columns,compare_columns';
  return { name: 'map.csv', mimeType: 'text/csv', buffer: Buffer.from([header, ...rows].join('\n') + '\n') };
}

test.describe('59 postgres / aurora config and bulk compare import', () => {
  let srcId: number;
  let tgtId: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    const base = { db_host: '127.0.0.1', db_name: 'd', db_user: 'u', db_password: 'p' };
    try {
      srcId = (await createConfig(ctx, SRC, 'dev', { ...base, db_type: 'mssql' })).id;
      tgtId = (await createConfig(ctx, TGT, 'dev', { ...base, db_type: 'aurora_postgresql' })).id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      await deleteJob(ctx, JOB_A);
      await deleteJob(ctx, JOB_B);
      const selections = await (await ctx.get('/api/selections')).json();
      for (const s of selections.filter((x: { name: string }) => x.name === SELECTION)) {
        await ctx.delete(`/api/selections/${s.id}`);
      }
      if (srcId) await deleteConfig(ctx, srcId);
      if (tgtId) await deleteConfig(ctx, tgtId);
    } finally {
      await ctx.dispose();
    }
  });

  test('aurora db type sets postgres defaults and shows ssl mode', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-config"]').click();
    await authedPage.locator('[data-testid="config-new-btn"]').click();
    await authedPage.locator('[data-testid="config-modal-db-type-select"]').selectOption('aurora_postgresql');
    await expect(authedPage.locator('[data-testid="config-modal-db-port-input"]')).toHaveValue('5432');
    await expect(authedPage.locator('[data-testid="config-modal-db-driver-input"]')).toHaveValue('psycopg');
    await expect(authedPage.locator('[data-testid="config-modal-db-sslmode-select"]')).toHaveValue('require');
    await expect(authedPage.locator('[data-testid="config-modal-aurora-hint"]')).toBeVisible();
    await authedPage.locator('[data-testid="config-modal-db-type-select"]').selectOption('mssql');
    await expect(authedPage.locator('[data-testid="config-modal-db-sslmode-select"]')).toBeHidden();
  });

  test('invalid row blocks import', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="bulk-compare-open-btn"]').click();
    await authedPage.locator('[data-testid="bulk-compare-file-input"]').setInputFiles(sheet([
      `${JOB_A},${SRC},dbo.customer,${TGT},public.customer,id,`,
      `${JOB_B},NO_SUCH_CONFIG,dbo.orders,${TGT},public.orders,id,`,
    ]));
    await expect(authedPage.locator('[data-testid="bulk-compare-summary"]')).toHaveText('1 valid, 1 invalid');
    await expect(authedPage.locator('[data-testid="bulk-compare-import-btn"]')).toBeDisabled();
  });

  test('valid sheet imports jobs and a selection', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="bulk-compare-open-btn"]').click();
    await authedPage.locator('[data-testid="bulk-compare-file-input"]').setInputFiles(sheet([
      `${JOB_A},${SRC},dbo.customer,${TGT},public.customer,CUST_ID:customer_id,NAME:full_name`,
      `${JOB_B},${SRC},dbo.orders,${TGT},public.orders,id,`,
    ]));
    await expect(authedPage.locator('[data-testid="bulk-compare-summary"]')).toHaveText('2 valid, 0 invalid');
    await authedPage.locator('[data-testid="bulk-compare-row-2"]').getByRole('button', { name: 'Details' }).click();
    await expect(authedPage.locator('[data-testid="bulk-compare-row-2"]')).toContainText('CUST_ID AS [customer_id]');
    await authedPage.locator('[data-testid="bulk-compare-create-selection"]').check();
    await authedPage.locator('[data-testid="bulk-compare-selection-name"]').fill(SELECTION);
    await authedPage.locator('[data-testid="bulk-compare-import-btn"]').click();
    await expect(authedPage.locator('[data-testid="bulk-compare-modal"]')).toBeHidden();
    await expect(authedPage.getByText(JOB_A).first()).toBeVisible();
  });
});
