import { test, expect } from './fixtures';
import { authedContext, createConfig, createFileJob, deleteConfig, deleteJob, waitForTerminal } from './api-helpers';

// Launch tab -> Run Settings -> Comparison: the Large-table Strategy control
// (in_memory / auto / pushdown / streaming), its dependent inputs, the help
// popovers that explain when to use pandas, Polars or DuckDB, and a real run
// through the streaming path. File-mode jobs have no SQL dialect, so pushdown
// and auto fall back to streaming; the deterministic createFileJob fixture
// must still produce exactly 3 issues (1 value_diff, 1 missing each side).

const SELECT = '[data-testid="launch-recon-strategy-select"]';
const BATCH = '[data-testid="launch-stream-batch-size-input"]';
const MEMORY = '[data-testid="launch-duckdb-memory-limit-input"]';
const BUCKETS = '[data-testid="launch-pushdown-buckets-input"]';
const BACKEND = '[data-testid="launch-comparison-backend-select"]';

test.describe('61 large-table reconciliation strategy', () => {
  const createdJobNames: string[] = [];

  test.afterEach(async ({ adminToken }) => {
    if (createdJobNames.length === 0) return;
    const ctx = await authedContext(adminToken);
    try {
      while (createdJobNames.length) await deleteJob(ctx, createdJobNames.pop()!);
    } finally {
      await ctx.dispose();
    }
  });

  async function openLaunch(page) {
    await page.goto('/');
    await page.locator('[data-testid="nav-tab-jobs"]').click();
  }

  test('defaults to In-memory with the DuckDB inputs hidden', async ({ authedPage }) => {
    await openLaunch(authedPage);
    await expect(authedPage.locator(SELECT)).toHaveValue('in_memory');
    await expect(authedPage.locator(BACKEND)).toBeEnabled();
    for (const sel of [BATCH, MEMORY, BUCKETS]) await expect(authedPage.locator(sel)).toBeHidden();
  });

  test('each strategy shows only the inputs it uses', async ({ authedPage }) => {
    await openLaunch(authedPage);

    await authedPage.locator(SELECT).selectOption('streaming');
    await expect(authedPage.locator(BATCH)).toBeVisible();
    await expect(authedPage.locator(BATCH)).toHaveValue('100000');
    await expect(authedPage.locator(MEMORY)).toBeVisible();
    await expect(authedPage.locator(MEMORY)).toHaveAttribute('placeholder', /27% of RAM/);
    await expect(authedPage.locator(BUCKETS)).toBeHidden();
    // pandas vs Polars no longer applies once DuckDB does the comparison.
    await expect(authedPage.locator(BACKEND)).toBeDisabled();

    for (const strategy of ['pushdown', 'auto']) {
      await authedPage.locator(SELECT).selectOption(strategy);
      await expect(authedPage.locator(BUCKETS)).toBeVisible();
      await expect(authedPage.locator(BUCKETS)).toHaveValue('16384');
      await expect(authedPage.locator(BATCH)).toBeVisible();
    }

    await authedPage.locator(SELECT).selectOption('in_memory');
    for (const sel of [BATCH, MEMORY, BUCKETS]) await expect(authedPage.locator(sel)).toBeHidden();
    await expect(authedPage.locator(BACKEND)).toBeEnabled();
  });

  test('launch sends the strategy settings in run_settings', async ({ authedPage, adminToken }) => {
    const name = `e2e-strategy-payload-${Date.now()}`;
    createdJobNames.push(name);
    const ctx = await authedContext(adminToken);
    try { await createFileJob(ctx, name); } finally { await ctx.dispose(); }

    await openLaunch(authedPage);
    await authedPage.locator(`[data-testid="job-row-${name}-checkbox"]`).click();
    await authedPage.locator(SELECT).selectOption('pushdown');
    await authedPage.locator(BATCH).fill('20000');
    await authedPage.locator(MEMORY).fill('2GB');
    await authedPage.locator(BUCKETS).fill('4096');

    const [runRequest] = await Promise.all([
      authedPage.waitForRequest((req) => req.url().includes('/api/runs') && req.method() === 'POST'),
      authedPage.locator('[data-testid="run-tests-btn"]').click(),
    ]);
    const settings = runRequest.postDataJSON().run_settings;
    expect(settings.reconciliation_strategy).toBe('pushdown');
    expect(settings.stream_batch_size).toBe(20000);
    expect(settings.duckdb_memory_limit).toBe('2GB');
    expect(settings.pushdown_buckets).toBe(4096);
  });

  test('In-memory launch keeps the previous payload defaults', async ({ authedPage, adminToken }) => {
    const name = `e2e-strategy-default-${Date.now()}`;
    createdJobNames.push(name);
    const ctx = await authedContext(adminToken);
    try { await createFileJob(ctx, name); } finally { await ctx.dispose(); }

    await openLaunch(authedPage);
    await authedPage.locator(`[data-testid="job-row-${name}-checkbox"]`).click();
    const [runRequest] = await Promise.all([
      authedPage.waitForRequest((req) => req.url().includes('/api/runs') && req.method() === 'POST'),
      authedPage.locator('[data-testid="run-tests-btn"]').click(),
    ]);
    const settings = runRequest.postDataJSON().run_settings;
    expect(settings.reconciliation_strategy).toBe('in_memory');
    expect(settings.duckdb_memory_limit).toBe('');
  });

  for (const strategy of ['streaming', 'auto', 'pushdown']) {
    test(`a ${strategy} run from the UI finishes with the same differences as in-memory`, async ({ authedPage, adminToken }) => {
      const name = `e2e-strategy-${strategy}-${Date.now()}`;
      createdJobNames.push(name);
      const ctx = await authedContext(adminToken);
      try {
        await createFileJob(ctx, name);

        await openLaunch(authedPage);
        await authedPage.locator(`[data-testid="job-row-${name}-checkbox"]`).click();
        await authedPage.locator(SELECT).selectOption(strategy);
        await authedPage.locator(BATCH).fill('1000');

        const [response] = await Promise.all([
          authedPage.waitForResponse((r) => r.url().endsWith('/api/runs') && r.request().method() === 'POST'),
          authedPage.locator('[data-testid="run-tests-btn"]').click(),
        ]);
        const { run_id } = await response.json();

        const runCard = authedPage.locator('[data-testid^="monitor-run-"]').first();
        await expect(runCard).toContainText('FAILED', { timeout: 30_000 });

        const status = await waitForTerminal(ctx, run_id);
        expect(status.status).toBe('FAILED');
        expect(status.failed).toBe(1);
        const summary = await (await ctx.get(`/api/runs/${run_id}/differences/summary`)).json();
        expect(summary.total_issues).toBe(3);
      } finally {
        await ctx.dispose();
      }
    });
  }

  test('server rejects a malformed DuckDB memory limit', async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      for (const bad of ["4GB'; DROP TABLE x; --", 'lots', '-1GB']) {
        const resp = await ctx.post('/api/runs', {
          data: {
            source_env: 'dev', target_env: 'dev', job_names: ['__none__'],
            run_settings: { reconciliation_strategy: 'streaming', duckdb_memory_limit: bad },
          },
        });
        expect(resp.status(), bad).toBe(422);
      }
      const unknown = await ctx.post('/api/runs', {
        data: {
          source_env: 'dev', target_env: 'dev', job_names: ['__none__'],
          run_settings: { reconciliation_strategy: 'fastest' },
        },
      });
      expect(unknown.status()).toBe(422);
      const tooSmall = await ctx.post('/api/runs', {
        data: {
          source_env: 'dev', target_env: 'dev', job_names: ['__none__'],
          run_settings: { reconciliation_strategy: 'streaming', stream_batch_size: 10 },
        },
      });
      expect(tooSmall.status()).toBe(422);
    } finally {
      await ctx.dispose();
    }
  });

  test('help popovers explain when to use pandas, Polars or DuckDB', async ({ authedPage }) => {
    await openLaunch(authedPage);

    await authedPage.locator('[data-testid="launch-recon-strategy-help"]').click();
    const content = authedPage.locator('[data-testid="help-popover-content"]');
    await expect(content).toContainText('Streaming (DuckDB)');
    await expect(content).toContainText('Pushdown');
    await expect(content).toContainText('DBMS_CRYPTO');
    await expect(content).toContainText('16 GB');
    // Paragraphs render on separate lines rather than one run-on block.
    await expect(content).toHaveCSS('white-space', 'pre-line');
    await authedPage.keyboard.press('Escape');
    await authedPage.locator('body').click({ position: { x: 5, y: 5 } });

    await authedPage.locator('[data-testid="launch-comparison-backend-help"]').click();
    await expect(content).toContainText('Polars');
    await expect(content).toContainText('In-memory');
  });

  test('Help Center documents the strategies and is searchable', async ({ authedPage }) => {
    await authedPage.goto('/?tab=help');
    const search = authedPage.locator('[data-testid="help-search-input"]');
    for (const term of ['DuckDB', 'DBMS_CRYPTO', 'Large-table Strategy']) {
      await search.fill(term);
      await expect(authedPage.locator('.help-section-title', {
        hasText: 'Launch Tab: Job Design, Scheduling & Automation Reference',
      })).toBeVisible();
    }
    // Search narrows to matching steps, so each step is found by its own terms.
    await search.fill('Choose pandas, Polars or DuckDB');
    await expect(authedPage.locator('.help-step-title', { hasText: 'Choose pandas, Polars or DuckDB for the comparison' })).toBeVisible();
    await search.fill('BLOB / CLOB');
    await expect(authedPage.locator('.help-step-title', { hasText: 'BLOB / CLOB columns and memory on small servers' })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Compare tab -> SQL: the same strategies for ad-hoc / saved SQL compares
// ---------------------------------------------------------------------------

async function openSqlCompare(page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-sql"]').click();
  await page.locator('[data-testid="compare-sql-advanced-toggle"]').click();
}

const SQL_KEYS = '#a11y-compare-key-columns-comma-separated-auto-inferred-if-blank';

test.describe('61 large-table strategy / Compare tab SQL', () => {
  let cfgA: number;
  let cfgB: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    const stamp = Date.now();
    try {
      // Real Oracle / PostgreSQL credentials from docker-compose.integration.yml;
      // only the opt-in live test below actually connects.
      cfgA = (await createConfig(ctx, `e2e-lt-oracle-${stamp}`, 'dev', {
        db_type: 'oracle', db_driver: 'oracledb', db_host: '127.0.0.1', db_port: 1521,
        db_name: 'FREEPDB1', db_user: 'e2e_src', db_password: 'Oracle_Test_12345',
      })).id;
      cfgB = (await createConfig(ctx, `e2e-lt-pg-${stamp}`, 'dev', {
        db_type: 'postgresql', db_driver: 'psycopg', db_host: '127.0.0.1', db_port: 25432,
        db_name: 'atom_live', db_user: 'atom', db_password: 'Atom_Test_12345', db_sslmode: 'disable',
      })).id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      if (cfgA) await deleteConfig(ctx, cfgA);
      if (cfgB) await deleteConfig(ctx, cfgB);
    } finally {
      await ctx.dispose();
    }
  });

  test('strategy select reveals its inputs, disables the backend and warns about keys', async ({ authedPage }) => {
    await openSqlCompare(authedPage);
    const strategy = authedPage.locator('[data-testid="compare-sql-strategy-select"]');
    const warning = authedPage.locator('[data-testid="compare-sql-strategy-key-warning"]');
    await expect(strategy).toHaveValue('in_memory');
    await expect(authedPage.locator('[data-testid="compare-sql-stream-batch-size-input"]')).toBeHidden();
    await expect(warning).toBeHidden();

    await strategy.selectOption('auto');
    await expect(authedPage.locator('[data-testid="compare-sql-stream-batch-size-input"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="compare-sql-duckdb-memory-limit-input"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="compare-sql-pushdown-buckets-input"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="compare-sql-backend-select"]')).toBeDisabled();
    await expect(warning).toBeVisible();

    await authedPage.locator(SQL_KEYS).fill('id');
    await expect(warning).toBeHidden();

    await strategy.selectOption('streaming');
    await expect(authedPage.locator('[data-testid="compare-sql-pushdown-buckets-input"]')).toBeHidden();
  });

  test('SQL compare sends the strategy in advanced options', async ({ authedPage }) => {
    let body: any = null;
    await authedPage.route('**/api/compare/sql', async (route) => {
      body = route.request().postDataJSON();
      await route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ detail: 'stubbed' }) });
    });
    await openSqlCompare(authedPage);
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(cfgA));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(cfgB));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELECT ID, AMOUNT FROM T');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id, amount FROM t');
    await authedPage.locator(SQL_KEYS).fill('id');
    await authedPage.locator('[data-testid="compare-sql-strategy-select"]').selectOption('pushdown');
    await authedPage.locator('[data-testid="compare-sql-stream-batch-size-input"]').fill('25000');
    await authedPage.locator('[data-testid="compare-sql-duckdb-memory-limit-input"]').fill('3GB');
    await authedPage.locator('[data-testid="compare-sql-pushdown-buckets-input"]').fill('2048');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();

    await expect.poll(() => body).not.toBeNull();
    expect(body.advanced.reconciliation_strategy).toBe('pushdown');
    expect(body.advanced.stream_batch_size).toBe(25000);
    expect(body.advanced.duckdb_memory_limit).toBe('3GB');
    expect(body.advanced.pushdown_buckets).toBe(2048);
    expect(body.key_columns).toEqual(['id']);
  });

  test('a large-table SQL compare without key columns ends in ERROR', async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const resp = await ctx.post('/api/compare/sql', {
        data: {
          config_id_a: cfgA, config_id_b: cfgB,
          query_a: 'SELECT 1 AS id FROM dual', query_b: 'SELECT 1 AS id',
          advanced: { reconciliation_strategy: 'auto' },
        },
      });
      if (resp.ok()) {
        // The compare runs in the background; the run records the reason.
        const { run_id } = await resp.json();
        const status = await waitForTerminal(ctx, run_id);
        expect(status.status).toBe('ERROR');
      } else {
        expect(resp.status()).toBe(422);
      }
    } finally {
      await ctx.dispose();
    }
  });

  test('live: Oracle to PostgreSQL with Auto finds the differences', async ({ authedPage }) => {
    test.skip(process.env.E2E_LIVE_LARGE_TABLE !== '1',
      'needs oracle + postgres from docker-compose.integration.yml and E2E_LIVE_LARGE_TABLE=1');
    await openSqlCompare(authedPage);
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(cfgA));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(cfgB));
    // Rows are generated inside each database: 20,000 ids on Oracle; on
    // PostgreSQL id 42 has a different amount and ids 20001-20003 are extra.
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill(
      'SELECT LEVEL AS ID, MOD(LEVEL * 7, 1000) / 100 AS AMOUNT FROM dual CONNECT BY LEVEL <= 20000');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill(
      'SELECT id, CASE WHEN id = 42 THEN 9.99 ELSE (id * 7 % 1000) / 100.0 END AS amount FROM generate_series(1, 20003) AS id');
    await authedPage.locator(SQL_KEYS).fill('ID');
    await authedPage.locator('[data-testid="compare-sql-strategy-select"]').selectOption('auto');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    const results = authedPage.locator('[data-testid="compare-sql-results"]');
    await expect(results).toContainText('Results', { timeout: 60_000 });
    await expect(authedPage.locator('.compare-chip.chip-regressed')).toHaveText('1 differ');
  });
});
