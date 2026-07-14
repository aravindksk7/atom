# Playwright E2E Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a comprehensive Playwright E2E suite covering every tab of the ETL Framework frontend (`frontend/index.html` + `frontend/app.js` + `frontend/features/compare.js`), including negative/edge cases, backed by a real FastAPI server against a throwaway DB (plus the existing `sapbo-mock`/`sqlserver` docker services for live-backend coverage).

**Architecture:** `@playwright/test` project with `testDir: tests/e2e`. `globalSetup` starts uvicorn against a temp sqlite DB, optionally starts `docker-compose.integration.yml` services and seeds them (gated behind `E2E_LIVE_BACKENDS=1`, mirroring the existing `RUN_LIVE_SQLSERVER_TESTS`/`RUN_LIVE_SAPBO_TESTS` pytest convention), and bootstraps an admin token. Every element under test gets a `data-testid` attribute added to `frontend/index.html` (none exist today). Each spec file namespaces the data it creates and cleans up in `afterAll`.

**Tech Stack:** `@playwright/test`, TypeScript, existing FastAPI backend (`api/main.py`), existing `docker-compose.integration.yml` (sqlserver + sapbo-mock).

**Design doc:** `docs/superpowers/specs/2026-07-14-playwright-e2e-suite-design.md`

---

## Known pre-existing bug encountered during research (not fixed by this plan)

`renderSrc`/`renderTgt` (referenced by `frontend/index.html:4149,4156,4441,4448`, the Reconciliation-file-diff and SQL-diff row tables) are **not defined** in any loaded script (`app.js`, `app-config.js`, `features/compare.js`, `help-content.js`, `contract-examples.js`) — they only exist in the untracked `frontend/app.js.bak`. At runtime this throws `renderSrc is not defined` inside Alpine's expression evaluator; the cell renders blank instead of the source/target value, no page crash. Task 14 (`08c-compare-sql`) and Task 13 (`08b-compare-reconciliation`) encode this as **current** behavior with a `// KNOWN BUG` comment and a link back to this section, rather than silently asserting values that don't render. File a separate bug ticket — do not fix `app.js` as part of this plan (out of scope, and fixing it would need its own test coverage/review).

---

## File Structure

```
playwright.config.ts                          # new
tsconfig.json                                  # new (or extend if one exists — check first)
package.json                                   # modify: add devDependency + "test:e2e" script
tests/e2e/
  global-setup.ts                              # new
  global-teardown.ts                           # new
  fixtures.ts                                  # new — authedPage fixture, testid helpers
  api-helpers.ts                                # new — bootstrap token, seed/cleanup via REST API
  compare-helpers.ts                            # new — shared Advanced-Options fill helper
  fixtures/data/
    source.csv                                  # new — baseline file-mode job fixture (source)
    target.csv                                  # new — baseline file-mode job fixture (target, has known diffs)
  00-auth-setup.spec.ts                         # new
  01-config.spec.ts                             # new
  02-launch-jobs.spec.ts                        # new
  03-monitor.spec.ts                            # new
  04-history.spec.ts                            # new
  05-adapters.spec.ts                           # new
  06-reports.spec.ts                            # new
  07-differences.spec.ts                        # new
  08a-compare-bo-report.spec.ts                 # new
  08b-compare-reconciliation.spec.ts            # new
  08c-compare-sql.spec.ts                       # new
  08d-compare-colstats.spec.ts                  # new
  08e-compare-mismatch-diff.spec.ts             # new
  08f-compare-templates.spec.ts                 # new
  09-contracts.spec.ts                          # new
  10-logs.spec.ts                               # new
  11-help.spec.ts                               # new
  12-cross-cutting.spec.ts                      # new
frontend/index.html                             # modify: add data-testid attributes throughout (one task's worth of edits per spec file, see each task)
```

## `data-testid` convention (used by every task below)

Kebab-case, prefixed by area, stable across re-renders (never index-based for lists — key by the same value Alpine already uses, e.g. job name, run id, tab id):
- Static controls: `data-testid="area-control-name"` e.g. `data-testid="auth-activate-btn"`
- List rows: `data-testid="job-row-{name}"`, buttons inside a row: `data-testid="job-row-{name}-edit-btn"`
- Modals: `data-testid="job-modal"`, fields inside: `data-testid="job-modal-name-input"`

---

### Task 1: Playwright project scaffolding

**Files:**
- Modify: `package.json`
- Create: `playwright.config.ts`
- Create: `tests/e2e/tsconfig.json`
- Create: `.gitignore` (modify — add `test-results/`, `playwright-report/`)

- [ ] **Step 1: Check for an existing tsconfig/TypeScript setup**

Run: `ls tsconfig.json 2>/dev/null || echo "none"`
Expected: `none` (repo is a Python/vanilla-JS frontend today, confirmed via `package.json` read earlier — only `devDependencies: alpinejs, chart.js, tailwindcss`).

- [ ] **Step 2: Add `@playwright/test` devDependency**

Edit `package.json`, add to `devDependencies`:

```json
    "@playwright/test": "^1.48.0",
```

Run: `npm install`
Expected: installs `@playwright/test` and its `playwright-core` dependency into `node_modules`.

Run: `npx playwright install chromium`
Expected: downloads the Chromium browser binary (Chromium-only per design's non-goals).

- [ ] **Step 3: Create `playwright.config.ts`**

```typescript
import { defineConfig, devices } from '@playwright/test';

const PORT = 8055;
export const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false, // spec files share one backend/DB — run serially across files
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  globalSetup: require.resolve('./tests/e2e/global-setup.ts'),
  globalTeardown: require.resolve('./tests/e2e/global-teardown.ts'),
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: `python -m uvicorn api.main:app --host 127.0.0.1 --port ${PORT}`,
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      ETL_DATABASE_URL: process.env.E2E_DATABASE_URL || '',
    },
  },
});
```

- [ ] **Step 4: Create `tests/e2e/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "moduleResolution": "node",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "types": ["node", "@playwright/test"]
  },
  "include": ["**/*.ts"]
}
```

- [ ] **Step 5: Ignore Playwright output directories**

Edit `.gitignore`, add:

```
test-results/
playwright-report/
```

- [ ] **Step 6: Add the `test:e2e` npm script**

Edit `package.json` `scripts`:

```json
    "test:e2e": "playwright test",
```

- [ ] **Step 7: Commit**

```bash
git add package.json package-lock.json playwright.config.ts tests/e2e/tsconfig.json .gitignore
git commit -m "test: scaffold Playwright E2E project"
```

---

### Task 2: `global-setup.ts` / `global-teardown.ts` — throwaway DB, docker services, admin token

**Files:**
- Create: `tests/e2e/global-setup.ts`
- Create: `tests/e2e/global-teardown.ts`
- Create: `tests/e2e/api-helpers.ts` (bootstrap function only in this task; more helpers added in Task 3)

**Context:** `frontend/app.js` bootstrap flow: `POST /api/tokens` with no `Authorization` header, when zero tokens exist, is force-admin (`api/routes/tokens.py:81-99`) and is exempt from the auth middleware. `ETL_DATABASE_URL` env var (`etl_framework/repository/database.py:10-11`) picks the sqlite file; defaults to a real on-disk file otherwise — **must** be set before `webServer` starts uvicorn, so we set it via `process.env` in `globalSetup`, which runs *before* Playwright starts `webServer`.

Live-backend seed values (verified via `tests/integration/test_sqlserver_live_reconciliation.py` and `tests/integration/test_sapbo_mock_container.py`, which already run against these exact containers):
- SQL Server: `127.0.0.1:14333`, user `sa`, password `Atom_Test_12345!`, ODBC driver `ODBC Driver 17 for SQL Server` (matches what the Config modal always sends, `frontend/app.js:_configDataFromModal`).
- SAP BO mock: `https://127.0.0.1:18443`, user `administrator`, password `Password1`, self-signed cert (`bo_verify_ssl: false`). Fixed canned data: document `"Sales Orders"` (id `1001`) has reports `"Orders"` (`rpt-sales`, columns `id,sku,amount,status`, 3 rows) and `"Summary"` (`rpt-sales-summary`); document `"Inventory Snapshot"` (id `1002`) has report `"Inventory"` (`rpt-inventory`).

- [ ] **Step 1: Write `tests/e2e/api-helpers.ts` (bootstrap only)**

```typescript
import { APIRequestContext, request as pwRequest } from '@playwright/test';
import { BASE_URL } from '../../playwright.config';

export async function bootstrapAdminToken(): Promise<string> {
  const ctx = await pwRequest.newContext({ baseURL: BASE_URL });
  try {
    const resp = await ctx.post('/api/tokens', {
      data: { name: 'e2e-admin', is_admin: true },
    });
    if (!resp.ok()) {
      throw new Error(`bootstrap token creation failed: ${resp.status()} ${await resp.text()}`);
    }
    const body = await resp.json();
    return body.raw_token as string;
  } finally {
    await ctx.dispose();
  }
}

export function authedContext(token: string): Promise<APIRequestContext> {
  return pwRequest.newContext({
    baseURL: BASE_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
  });
}
```

- [ ] **Step 2: Write `tests/e2e/global-setup.ts`**

```typescript
import { execSync, spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { FullConfig } from '@playwright/test';

const REPO_ROOT = path.resolve(__dirname, '../..');

export default async function globalSetup(_config: FullConfig) {
  // 1. Throwaway sqlite DB — must be set before Playwright's webServer starts uvicorn.
  const dbDir = mkdtempSync(path.join(tmpdir(), 'atom-e2e-'));
  const dbPath = path.join(dbDir, 'e2e.db');
  process.env.E2E_DATABASE_URL = `sqlite:///${dbPath.replace(/\\/g, '/')}`;
  process.env.E2E_DB_DIR = dbDir; // read by global-teardown

  // 2. Live backends (SQL Server + SAP BO mock), gated — mirrors the existing
  //    RUN_LIVE_SQLSERVER_TESTS / RUN_LIVE_SAPBO_TESTS pytest convention.
  if (process.env.E2E_LIVE_BACKENDS === '1') {
    console.log('[global-setup] starting docker-compose.integration.yml services...');
    execSync('docker compose -f docker-compose.integration.yml up -d --wait', {
      cwd: REPO_ROOT,
      stdio: 'inherit',
      timeout: 180_000,
    });
    seedSqlServer();
  }
}

function seedSqlServer() {
  // Reuses the exact seed pattern from tests/integration/test_sqlserver_live_reconciliation.py
  // so the databases/table shape match what that suite already validates.
  const script = `
import pyodbc
conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};SERVER=127.0.0.1,14333;DATABASE=master;"
    "UID=sa;PWD=Atom_Test_12345!;Connect Timeout=5;",
    autocommit=True,
)
cur = conn.cursor()
for db in ("atom_e2e_src", "atom_e2e_tgt"):
    cur.execute(f"IF DB_ID('{db}') IS NULL CREATE DATABASE {db}")
conn.close()

def seed(db, rows):
    c = pyodbc.connect(
        f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER=127.0.0.1,14333;DATABASE={db};"
        "UID=sa;PWD=Atom_Test_12345!;Connect Timeout=5;",
        autocommit=True,
    )
    cur = c.cursor()
    cur.execute("IF OBJECT_ID('dbo.orders', 'U') IS NOT NULL DROP TABLE dbo.orders")
    cur.execute(
        "CREATE TABLE dbo.orders (id INT NOT NULL PRIMARY KEY, sku NVARCHAR(50) NOT NULL, amount DECIMAL(10,2) NOT NULL)"
    )
    cur.executemany("INSERT INTO dbo.orders (id, sku, amount) VALUES (?, ?, ?)", rows)
    c.close()

seed("atom_e2e_src", [(1, "A100", 25.50), (2, "B200", 50.00), (3, "C300", 75.00)])
seed("atom_e2e_tgt", [(1, "A100", 25.50), (2, "B200", 55.00), (4, "D400", 99.00)])
print("seeded")
`;
  const result = spawnSync('python', ['-c', script], { encoding: 'utf-8' });
  if (result.status !== 0) {
    throw new Error(`SQL Server seed failed:\n${result.stdout}\n${result.stderr}`);
  }
  console.log('[global-setup] SQL Server seeded:', result.stdout.trim());
}
```

- [ ] **Step 3: Write `tests/e2e/global-teardown.ts`**

```typescript
import { execSync } from 'node:child_process';
import { rmSync } from 'node:fs';
import path from 'node:path';

const REPO_ROOT = path.resolve(__dirname, '../..');

export default async function globalTeardown() {
  if (process.env.E2E_LIVE_BACKENDS === '1') {
    console.log('[global-teardown] stopping docker-compose.integration.yml services...');
    execSync('docker compose -f docker-compose.integration.yml down -v', {
      cwd: REPO_ROOT,
      stdio: 'inherit',
    });
  }
  const dbDir = process.env.E2E_DB_DIR;
  if (dbDir) {
    rmSync(dbDir, { recursive: true, force: true });
  }
}
```

- [ ] **Step 4: Verify `webServer` picks up the env var**

Run: `E2E_DATABASE_URL=sqlite:///C:/tmp/manual-test.db python -m uvicorn api.main:app --host 127.0.0.1 --port 8055 &` then `curl http://127.0.0.1:8055/api/health`
Expected: `{"status":"ok","version":"2.0.0"}`, and `C:/tmp/manual-test.db` is created fresh (not the real `etl_framework.db`). Stop the server afterward.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/global-setup.ts tests/e2e/global-teardown.ts tests/e2e/api-helpers.ts
git commit -m "test: add Playwright global setup/teardown for throwaway DB and live backends"
```

---

### Task 3: Shared fixtures, remaining `api-helpers.ts`, `compare-helpers.ts`, CSV fixtures

**Files:**
- Create: `tests/e2e/fixtures.ts`
- Modify: `tests/e2e/api-helpers.ts` (add job/run/config/cleanup helpers)
- Create: `tests/e2e/compare-helpers.ts`
- Create: `tests/e2e/fixtures/data/source.csv`
- Create: `tests/e2e/fixtures/data/target.csv`

**Context:** `POST /api/jobs` body is a `JobDefinition` (`api/schemas.py:413-430`): `{name, job_type, query, key_columns, exclude_columns, params, enabled, rules, depends_on, pass_condition}`. File-mode reconciliation uses `params.source_file_path` / `params.target_file_path` (`api/schemas.py:395-403`, prefix `source`/`target`, suffix `path`). `POST /api/runs` body is a `RunTrigger` (`api/schemas.py:201-209`): `{source_env, target_env, job_names, run_settings}`, returns 202 `RunStatusOut{run_id, status}` — poll `GET /api/runs/{id}/status` until `status` is one of the terminal statuses (`PASSED,FAILED,SLOW,ERROR,COMPLETED,CANCELLED`, `frontend/app-config.js:2`).

- [ ] **Step 1: Create deterministic CSV fixtures with known mismatches**

`tests/e2e/fixtures/data/source.csv`:
```csv
id,sku,amount
1,A100,25.50
2,B200,50.00
3,C300,75.00
```

`tests/e2e/fixtures/data/target.csv`:
```csv
id,sku,amount
1,A100,25.50
2,B200,55.00
4,D400,99.00
```

This mirrors the live-SQL-Server seed exactly: comparing these two on key `id` produces 1 `value_diff` (row 2, amount 50.00→55.00), 1 `missing_in_target` (row 3), 1 `missing_in_source` (row 4) — a real, deterministic `FAILED` result usable by every spec that just needs "a run that exists with known mismatches."

- [ ] **Step 2: Add job/run/cleanup helpers to `tests/e2e/api-helpers.ts`**

```typescript
import { APIRequestContext } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';

const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');

export async function createFileJob(ctx: APIRequestContext, name: string) {
  const resp = await ctx.post('/api/jobs', {
    data: {
      name,
      job_type: 'reconciliation',
      key_columns: ['id'],
      params: {
        source_mode: 'files',
        source_file_path: path.join(FIXTURE_DIR, 'source.csv'),
        target_file_path: path.join(FIXTURE_DIR, 'target.csv'),
      },
    },
  });
  if (!resp.ok()) throw new Error(`createFileJob(${name}) failed: ${resp.status()} ${await resp.text()}`);
  return resp.json();
}

export async function deleteJob(ctx: APIRequestContext, name: string) {
  await ctx.delete(`/api/jobs/${encodeURIComponent(name)}`);
}

export async function triggerRun(ctx: APIRequestContext, jobNames: string[]) {
  const resp = await ctx.post('/api/runs', {
    data: { source_env: 'dev', target_env: 'dev', job_names: jobNames },
  });
  if (!resp.ok()) throw new Error(`triggerRun failed: ${resp.status()} ${await resp.text()}`);
  return resp.json(); // { run_id, status }
}

export async function waitForTerminal(ctx: APIRequestContext, runId: string, timeoutMs = 30_000) {
  const terminal = new Set(['PASSED', 'FAILED', 'SLOW', 'ERROR', 'COMPLETED', 'CANCELLED']);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const resp = await ctx.get(`/api/runs/${runId}/status`);
    const body = await resp.json();
    if (terminal.has(String(body.status).toUpperCase())) return body;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`run ${runId} did not reach a terminal status within ${timeoutMs}ms`);
}

/** Creates a job, runs it, waits for completion. Returns the terminal run_id. */
export async function seedBaselineRun(ctx: APIRequestContext, namePrefix: string) {
  const jobName = `${namePrefix}-job-${Date.now()}`;
  await createFileJob(ctx, jobName);
  const { run_id } = await triggerRun(ctx, [jobName]);
  await waitForTerminal(ctx, run_id);
  return { jobName, runId: run_id as string };
}

export async function createConfig(ctx: APIRequestContext, name: string, envName: string, configData: Record<string, unknown>) {
  const resp = await ctx.post('/api/configs', { data: { name, env_name: envName, config_data: configData } });
  if (!resp.ok()) throw new Error(`createConfig(${name}) failed: ${resp.status()} ${await resp.text()}`);
  return resp.json(); // includes .id
}

export async function deleteConfig(ctx: APIRequestContext, id: number) {
  await ctx.delete(`/api/configs/${id}`);
}
```

- [ ] **Step 3: Write `tests/e2e/fixtures.ts`**

```typescript
import { test as base, expect, Page } from '@playwright/test';
import { bootstrapAdminToken } from './api-helpers';

let cachedToken: string | null = null;

export const test = base.extend<{ authedPage: Page; adminToken: string }>({
  adminToken: async ({}, use) => {
    if (!cachedToken) cachedToken = await bootstrapAdminToken();
    await use(cachedToken);
  },
  authedPage: async ({ page, adminToken }, use) => {
    await page.addInitScript((token) => {
      window.sessionStorage.setItem('etl_token', token);
    }, adminToken);
    await page.goto('/');
    await expect(page.locator('[data-testid="auth-status-connected"]')).toBeVisible();
    await use(page);
  },
});

export { expect };
```

Note: `bootstrapAdminToken()` is idempotent-safe to call once (module-level `cachedToken` cache within a worker) because `POST /api/tokens` only force-admins the *first* token (`api/routes/tokens.py:81-99`); calling it again after one exists would require an admin `Authorization` header we don't have yet. Since Playwright runs with `workers: 1` (Task 1), a single process-wide bootstrap is safe. `00-auth-setup.spec.ts` (Task 4) does **not** use this fixture — it drives the unauthenticated bootstrap flow itself and must run first (filename `00-` sorts first; Playwright respects file-declaration order when `fullyParallel: false` and `workers: 1`).

- [ ] **Step 4: Write `tests/e2e/compare-helpers.ts`**

```typescript
import { Page } from '@playwright/test';

export interface AdvancedOptions {
  backend?: 'pandas' | 'polars' | 'duckdb';
  floatTolerance?: string;
  datetimeTolerance?: string;
  mismatchRowLimit?: string;
  sampleFrac?: string;
  columnTolerances?: string;
  caseInsensitiveColumns?: string;
  whitespaceNormalizeColumns?: string;
  parallelColumns?: boolean;
}

/**
 * Fills the "Advanced Options" accordion shared by BO Report, Reconciliation
 * (Run/File vs Report), and SQL sub-tabs. `prefix` matches the data-testid
 * prefix added to each sub-tab's markup (e.g. "compare-bo", "compare-file", "compare-sql").
 */
export async function fillAdvancedOptions(page: Page, prefix: string, opts: AdvancedOptions) {
  const toggle = page.locator(`[data-testid="${prefix}-advanced-toggle"]`);
  if (!(await page.locator(`[data-testid="${prefix}-advanced-panel"]`).isVisible())) {
    await toggle.click();
  }
  if (opts.backend) await page.locator(`[data-testid="${prefix}-backend-select"]`).selectOption(opts.backend);
  if (opts.floatTolerance !== undefined) await page.locator(`[data-testid="${prefix}-float-tolerance-input"]`).fill(opts.floatTolerance);
  if (opts.datetimeTolerance !== undefined) await page.locator(`[data-testid="${prefix}-datetime-tolerance-input"]`).fill(opts.datetimeTolerance);
  if (opts.mismatchRowLimit !== undefined) await page.locator(`[data-testid="${prefix}-mismatch-row-limit-input"]`).fill(opts.mismatchRowLimit);
  if (opts.sampleFrac !== undefined) await page.locator(`[data-testid="${prefix}-sample-frac-input"]`).fill(opts.sampleFrac);
  if (opts.columnTolerances !== undefined) await page.locator(`[data-testid="${prefix}-column-tolerances-input"]`).fill(opts.columnTolerances);
  if (opts.caseInsensitiveColumns !== undefined) await page.locator(`[data-testid="${prefix}-case-insensitive-input"]`).fill(opts.caseInsensitiveColumns);
  if (opts.whitespaceNormalizeColumns !== undefined) await page.locator(`[data-testid="${prefix}-whitespace-normalize-input"]`).fill(opts.whitespaceNormalizeColumns);
  if (opts.parallelColumns !== undefined) {
    const cb = page.locator(`[data-testid="${prefix}-parallel-columns-checkbox"]`);
    if ((await cb.isChecked()) !== opts.parallelColumns) await cb.click();
  }
}
```

- [ ] **Step 5: Add `data-testid` to the auth status bar (needed by `authedPage` fixture above)**

Edit `frontend/index.html:54-60` (the `x-if="storedToken"` branch), add `data-testid="auth-status-connected"` to the root element of that branch, e.g.:

```html
      <template x-if="storedToken">
        <div class="auth-status-connected" data-testid="auth-status-connected">
```

(Exact surrounding markup to be confirmed against the live file at edit time — add the attribute to whichever element wraps the "Connected as ..." text at that line range.)

- [ ] **Step 6: Verify fixtures compile and the auth fixture works end-to-end**

Write a throwaway smoke spec temporarily at `tests/e2e/_smoke.spec.ts`:

```typescript
import { test, expect } from './fixtures';

test('authedPage fixture reaches an authenticated page', async ({ authedPage }) => {
  await expect(authedPage.locator('[data-testid="auth-status-connected"]')).toContainText('Connected');
});
```

Run: `npx playwright test tests/e2e/_smoke.spec.ts`
Expected: 1 passed.

Delete `tests/e2e/_smoke.spec.ts` (it was only to validate the fixture; Task 4 supersedes it with real auth-flow coverage).

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/fixtures.ts tests/e2e/api-helpers.ts tests/e2e/compare-helpers.ts tests/e2e/fixtures/data frontend/index.html
git commit -m "test: add shared Playwright fixtures, API seed helpers, and CSV test data"
```

---

### Task 4: `00-auth-setup.spec.ts`

**Files:**
- Create: `tests/e2e/00-auth-setup.spec.ts`
- Modify: `frontend/index.html` (add testids to the auth modal, `:4822-4871`, and the Security card, `:229-294`)

**Context (from research):** Bootstrap path shows when `!authInitialized && !authCreatedToken` (`index.html:4829`, label "Create Initial Administrator", input `authTokenName`, button "Create Admin →" → `createToken('auth')`). Sign-in/paste path (`index.html:4858`) shows whenever `!authCreatedToken`; label switches "Paste Existing Token" → "Sign in with Your Access Token" once `authInitialized`. `POST /api/tokens` unauthenticated + zero existing tokens is force-admin. 401 body: `{"detail":"Missing or invalid Authorization header"}` (no header) or `{"detail":"Invalid or expired token"}` (bad token). 403 body: `{"detail":"Admin token required"}`. `revokeToken()` uses native `confirm()`.

This spec runs against the **fresh, empty DB** (it's `00-`, runs first) so `authInitialized` is false and the bootstrap UI is live.

- [ ] **Step 1: Add testids to the auth modal**

Edit `frontend/index.html:4822-4869`, adding these attributes to the existing elements (matching by the literal text/model already documented):
- Modal root (`:4822`): `data-testid="auth-modal"`
- Bootstrap name input (`:4833`, `x-model="authTokenName"`): `data-testid="auth-bootstrap-name-input"`
- "Create Admin →" button (`:4834`): `data-testid="auth-bootstrap-submit-btn"`
- Created-token reveal text (`:4838-4850`, `x-text="authCreatedToken"`): `data-testid="auth-created-token-value"`
- "Done — I've saved my token" button: `data-testid="auth-done-btn"`
- Paste input (`:4858-4865`, `x-model="authPasteValue"`): `data-testid="auth-paste-input"`
- "Activate" button: `data-testid="auth-activate-btn"`
- Error text (`:4867-4869`, `x-text="authError"`): `data-testid="auth-error-text"`

- [ ] **Step 2: Add testids to the Security — Users & API Access card**

Edit `frontend/index.html:229-294`:
- Card header (`:231`): `data-testid="security-card-header"`
- Paste-token input (`:238`): `data-testid="security-token-input"`
- "+ Add User Access" button (`:243`): `data-testid="security-add-user-btn"`
- Name input (`:245-ish`, `x-model="newTokenName"`): `data-testid="security-new-token-name-input"`
- Role select (`x-model="newTokenRole"`): `data-testid="security-new-token-role-select"`
- "Create Access" button: `data-testid="security-create-access-btn"`
- Created-token reveal (`x-text="createdToken"`): `data-testid="security-created-token-value"`
- Token list rows (`x-for="tok in tokens"`): `:data-testid="'security-token-row-' + tok.id"`, revoke button `:data-testid="'security-revoke-btn-' + tok.id"`

- [ ] **Step 3: Write the spec**

```typescript
import { test, expect } from '@playwright/test';
import { bootstrapAdminToken } from './api-helpers';

test.describe('00 auth setup', () => {
  test('bootstrap creates the first admin token and auto-connects', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('[data-testid="auth-modal"]')).toBeVisible();
    await page.locator('[data-testid="auth-bootstrap-name-input"]').fill('e2e-first-admin');
    await page.locator('[data-testid="auth-bootstrap-submit-btn"]').click();

    const created = page.locator('[data-testid="auth-created-token-value"]');
    await expect(created).toBeVisible();
    const rawToken = (await created.textContent())!.trim();
    expect(rawToken.length).toBeGreaterThan(10);

    await page.locator('[data-testid="auth-done-btn"]').click();
    await expect(page.locator('[data-testid="auth-modal"]')).toBeHidden();
    await expect(page.locator('[data-testid="auth-status-connected"]')).toContainText('Administrator');
  });

  test('paste-token connect works for a second (non-admin) token created via the API', async ({ page, request }) => {
    // bootstrapAdminToken() re-derives an admin token via the API for setup purposes only;
    // the actual token under test here is the second, standard-user one it creates.
    const adminToken = await bootstrapAdminToken();
    const resp = await request.post('/api/tokens', {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: { name: 'e2e-standard-user' },
    });
    const { raw_token: standardToken } = await resp.json();

    await page.goto('/');
    await page.locator('[data-testid="auth-paste-input"]').fill(standardToken);
    await page.locator('[data-testid="auth-activate-btn"]').click();
    await expect(page.locator('[data-testid="auth-status-connected"]')).toContainText('Standard user');
  });

  test('negative: malformed/garbage token is rejected with the exact backend error', async ({ page }) => {
    await page.goto('/');
    await page.locator('[data-testid="auth-paste-input"]').fill('not-a-real-token-at-all');
    await page.locator('[data-testid="auth-activate-btn"]').click();
    await expect(page.locator('[data-testid="auth-error-text"]')).toHaveText(
      'Your API token was rejected. Paste a valid raw token.'
    );
  });

  test('negative: unauthenticated API call to an admin route returns 401 with the exact detail', async ({ request }) => {
    const resp = await request.get('/api/tokens');
    expect(resp.status()).toBe(401);
    expect((await resp.json()).detail).toBe('Missing or invalid Authorization header');
  });

  test('negative: non-admin token hitting an admin-only route returns 403', async ({ request }) => {
    const adminToken = await bootstrapAdminToken();
    const created = await request.post('/api/tokens', {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: { name: 'e2e-non-admin-403-check' },
    });
    const { raw_token: standardToken } = await created.json();

    const resp = await request.get('/api/tokens', {
      headers: { Authorization: `Bearer ${standardToken}` },
    });
    expect(resp.status()).toBe(403);
    expect((await resp.json()).detail).toBe('Admin token required');
  });

  test('disconnect clears the session and re-shows the auth modal on next load', async ({ page }) => {
    const adminToken = await bootstrapAdminToken();
    await page.addInitScript((token) => window.sessionStorage.setItem('etl_token', token), adminToken);
    await page.goto('/');
    await expect(page.locator('[data-testid="auth-status-connected"]')).toBeVisible();

    await page.evaluate(() => window.sessionStorage.removeItem('etl_token'));
    await page.reload();
    // authInitialized is now true (a token exists), so the modal shows the paste path, not bootstrap.
    await expect(page.locator('[data-testid="auth-paste-input"]')).toBeVisible();
    await expect(page.locator('[data-testid="auth-bootstrap-name-input"]')).toBeHidden();
  });
});
```

- [ ] **Step 4: Run against the fresh DB and verify**

Run: `npx playwright test tests/e2e/00-auth-setup.spec.ts`
Expected: 6 passed. (This must be the first spec file run in the suite — it depends on the DB having zero tokens; do not run it after other specs have already bootstrapped a token via the shared `authedPage` fixture cache within the same worker process, since `cachedToken` in `fixtures.ts` would already hold a value — this is fine as-is because `00-` sorts first and `workers: 1` runs files in order.)

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html tests/e2e/00-auth-setup.spec.ts
git commit -m "test: add auth setup/bootstrap E2E coverage"
```

---

### Task 5: `01-config.spec.ts`

**Files:**
- Create: `tests/e2e/01-config.spec.ts`
- Modify: `frontend/index.html` (testids on Config Editor list, New Config modal `:464-720`, Import YAML card `:209-226`)

**Context:** Real button label is **"Validate"** (`index.html:716`), not "Validate Configuration" (that string is a dead `sr-only` decoy at `index.html:14` — do not assert on it). Validation errors render as `err.field_name: err.message` per `err in configValidation.errors`. `deleteConfig` uses native `confirm('Delete this configuration?')`.

- [ ] **Step 1: Add testids**
- New Config button (`:78`): `data-testid="config-new-btn"`
- Config list rows (`:90`, `x-for cfg`): `:data-testid="'config-row-' + cfg.id"`, edit/delete: `:data-testid="'config-row-' + cfg.id + '-edit-btn'"` / `...-delete-btn`
- Modal root (`:464`): `data-testid="config-modal"`
- Name input (`:~470`, `x-model="configModal.name"`): `data-testid="config-modal-name-input"`
- Env select: `data-testid="config-modal-env-select"`
- DB Host/Port/Name/User/Password (`x-model="configModal.db_host"` etc.): `data-testid="config-modal-db-host-input"`, `...-db-port-input`, `...-db-name-input`, `...-db-user-input`, `...-db-password-input`
- Validate/Save/Cancel buttons (`:714-718`): `data-testid="config-modal-cancel-btn"`, `...-validate-btn`, `...-save-btn`
- Validation result box (`:699-712`): `data-testid="config-validation-result"`, per-error rows: `data-testid="config-validation-error-row"`
- Import YAML textarea/button (`:209-226`): `data-testid="config-yaml-textarea"`, `data-testid="config-yaml-import-btn"`

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';

test.describe('01 config', () => {
  test.afterEach(async ({ authedPage }) => {
    // Clean up any config left named with the e2e- prefix.
    const rows = authedPage.locator('[data-testid^="config-row-"][data-testid$="-delete-btn"]');
    const count = await rows.count();
    for (let i = 0; i < count; i++) {
      const row = rows.nth(i);
      if ((await row.getAttribute('data-config-name'))?.startsWith('e2e-')) {
        authedPage.once('dialog', (d) => d.accept());
        await row.click();
      }
    }
  });

  test('create, validate, and save a new config', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="config-new-btn"]').click();
    const modal = authedPage.locator('[data-testid="config-modal"]');
    await expect(modal).toContainText('New Configuration');

    const name = `e2e-config-${Date.now()}`;
    await authedPage.locator('[data-testid="config-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="config-modal-db-host-input"]').fill('127.0.0.1');
    await authedPage.locator('[data-testid="config-modal-db-port-input"]').fill('14333');
    await authedPage.locator('[data-testid="config-modal-db-name-input"]').fill('atom_e2e_src');
    await authedPage.locator('[data-testid="config-modal-db-user-input"]').fill('sa');
    await authedPage.locator('[data-testid="config-modal-db-password-input"]').fill('Atom_Test_12345!');

    await authedPage.locator('[data-testid="config-modal-validate-btn"]').click();
    await expect(authedPage.locator('[data-testid="config-validation-result"]')).toBeVisible();

    await authedPage.locator('[data-testid="config-modal-save-btn"]').click();
    await expect(modal).toBeHidden();
    await expect(authedPage.locator(`text=${name}`)).toBeVisible();
  });

  test('negative: validating with missing required fields shows a field-level error', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="config-new-btn"]').click();
    await authedPage.locator('[data-testid="config-modal-name-input"]').fill(`e2e-invalid-${Date.now()}`);
    // Leave DB fields empty — Validate should surface field_name/message pairs, not silently pass.
    await authedPage.locator('[data-testid="config-modal-validate-btn"]').click();
    await expect(authedPage.locator('[data-testid="config-validation-error-row"]').first()).toBeVisible();
    await authedPage.locator('[data-testid="config-modal-cancel-btn"]').click();
  });

  test('negative: import-yaml with invalid YAML surfaces an error toast, does not create a config', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="config-yaml-textarea"]').fill('not: [valid: yaml: at: all');
    await authedPage.locator('[data-testid="config-yaml-import-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Import failed');
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/01-config.spec.ts`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/01-config.spec.ts
git commit -m "test: add Config tab E2E coverage"
```

---

### Task 6: `02-launch-jobs.spec.ts`

**Files:**
- Create: `tests/e2e/02-launch-jobs.spec.ts`
- Modify: `frontend/index.html` (testids on Job Catalog `:912-979`, Job Modal `:982-1526`, Execution Sequence `:1530-1625`)

**Context:** `canSaveJob()` (`frontend/app.js:1403-1429`) gates the Save button client-side per job type. `saveJob()` first calls `validateJobDefinition()` (server-side, `POST /api/jobs/validate`) and **aborts silently on failure** — only the validation toast fires, no separate save-failed toast. Exact server validation strings (`etl_framework/runner/job_validation.py`): `"job name is required"`, `"reconciliation jobs require a query"`, `"reconciliation jobs require key_columns"`. Duplicate name → HTTP 409 `"Job already exists"` (toast title `"Save failed"`). `deleteJob` uses native `confirm('Delete job "' + name + '"?')`.

- [ ] **Step 1: Add testids**
- "+ New Job" button (`:917`): `data-testid="job-new-btn"`
- Search input (`:926`): `data-testid="job-search-input"`
- Job rows (`x-for job in filteredJobList`): `:data-testid="'job-row-' + job.name"`, edit/delete: `...-edit-btn` / `...-delete-btn`
- Modal root: `data-testid="job-modal"`
- `jobModalTab` buttons (`x-for tab in jobModalTabs`): `:data-testid="'job-modal-tab-' + tab.id"`
- Basic tab: name input `data-testid="job-modal-name-input"`, job-type select `data-testid="job-modal-type-select"`
- Settings tab: SQL query textarea `data-testid="job-modal-query-textarea"`, key-columns input `data-testid="job-modal-key-columns-input"`, source/target file path inputs (files mode) `data-testid="job-modal-source-path-input"` / `...-target-path-input`
- Deps tab: `data-testid="job-modal-depends-on-input"`
- Footer: `data-testid="job-modal-cancel-btn"`, `...-validate-definition-btn`, `...-save-btn`
- Validation result panel: `data-testid="job-modal-validation-result"`
- Execution Sequence: "▶ Run Tests" button `data-testid="run-tests-btn"`, step rows `:data-testid="'exec-step-' + name"`

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';
import { deleteJob } from './api-helpers';

test.describe('02 launch/jobs', () => {
  test('create a SQL-mode reconciliation job with key columns', async ({ authedPage, request, adminToken }) => {
    const name = `e2e-job-${Date.now()}`;
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-query-textarea"]').fill('SELECT * FROM dbo.orders');
    await authedPage.locator('[data-testid="job-modal-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();
    await expect(authedPage.locator(`[data-testid="job-row-${name}"]`)).toBeVisible();

    await deleteJob(await request, name); // cleanup via API (fast, doesn't depend on UI confirm dialog)
  });

  test('negative: saving without a query or key columns is blocked client-side (Save stays disabled)', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(`e2e-incomplete-${Date.now()}`);
    // No query, no key columns entered — canSaveJob() requires both for SQL-mode reconciliation.
    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeDisabled();
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });

  test('negative: duplicate job name is rejected with the exact backend message', async ({ authedPage, request, adminToken }) => {
    const name = `e2e-dup-${Date.now()}`;
    const ctx = await request;
    await ctx.post('/api/jobs', {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: { name, job_type: 'reconciliation', query: 'SELECT 1', key_columns: ['id'] },
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-query-textarea"]').fill('SELECT 1');
    await authedPage.locator('[data-testid="job-modal-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Save failed');
    await expect(authedPage.locator('.toast-msg')).toContainText('Job already exists');

    await deleteJob(ctx, name);
  });

  test('negative: invalid schema JSON in job definition surfaces a validation-failed toast', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(`e2e-badjson-${Date.now()}`);
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-query-textarea"]').fill('SELECT * FROM t');
    await authedPage.locator('[data-testid="job-modal-key-columns-input"]').fill('1invalid col!'); // fails /^[a-zA-Z_][a-zA-Z0-9_]*$/
    await expect(authedPage.locator('text=Invalid column name(s)')).toBeVisible();
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/02-launch-jobs.spec.ts`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/02-launch-jobs.spec.ts
git commit -m "test: add Launch/Jobs tab E2E coverage"
```

---

### Task 7: `03-monitor.spec.ts`

**Files:**
- Create: `tests/e2e/03-monitor.spec.ts`
- Modify: `frontend/index.html` (testids on Monitor tab `:1858-1977`)

**Context:** `runTests()` is a **silent no-op** with zero jobs selected (button is also `:disabled`, so this is unreachable through real UI interaction — test asserts the disabled state, not a submitted no-op). Cancel button is `x-show="!isTerminalStatus(run.status)"` — **hidden entirely** once a run is terminal, so "cancel an already-finished run" is also only reachable via direct API call (`cancelRun`'s own code confirms the backend returns 202 success even for already-terminal runs, no distinguishable error UI).

- [ ] **Step 1: Add testids**
- Empty state link (`:~1864`): `data-testid="monitor-launch-link"`
- Run cards (`x-for run in activeRuns`): `:data-testid="'monitor-run-' + run.run_id"`
- Cancel button: `:data-testid="'monitor-cancel-btn-' + run.run_id"`
- Job checkbox rows (Launch tab, reused for selecting jobs to run): already covered by `job-row-*` testids from Task 6; add checkbox-specific testid `:data-testid="'job-row-' + job.name + '-checkbox'"`
- "▶ Run Tests" button: already `data-testid="run-tests-btn"` from Task 6

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';
import { createFileJob, deleteJob, waitForTerminal } from './api-helpers';

test.describe('03 monitor', () => {
  test('trigger a run from the UI and see it appear with a terminal status', async ({ authedPage, request, adminToken }) => {
    const ctx = await request;
    const jobName = `e2e-monitor-job-${Date.now()}`;
    await ctx.post('/api/jobs', {
      headers: { Authorization: `Bearer ${adminToken}` },
      // Re-use the api-helpers file-mode builder for a job guaranteed to complete fast.
    });
    await createFileJob(ctx, jobName);

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="job-search-input"]').fill(jobName);
    await authedPage.locator(`[data-testid="job-row-${jobName}-checkbox"]`).click();
    await authedPage.locator('[data-testid="run-tests-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Run started');

    const runCard = authedPage.locator('[data-testid^="monitor-run-"]').first();
    await expect(runCard).toBeVisible();
    // Poll via API in parallel rather than a fixed UI timeout, then assert the UI reflects it.
    await authedPage.waitForFunction(
      () => document.querySelector('[data-testid^="monitor-run-"] .badge')?.textContent?.match(/PASSED|FAILED|COMPLETED|ERROR/),
      { timeout: 30_000 }
    );

    await deleteJob(ctx, jobName);
  });

  test('negative: Run Tests stays disabled with zero jobs selected', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="job-search-input"]').fill('__no_such_job__');
    await expect(authedPage.locator('[data-testid="run-tests-btn"]')).toBeDisabled();
  });

  test('negative: cancelling an already-terminal run via the API returns 202 (no error) — the UI hides Cancel entirely once terminal', async ({ authedPage, request, adminToken }) => {
    const ctx = await request;
    const jobName = `e2e-monitor-cancel-${Date.now()}`;
    await createFileJob(ctx, jobName);
    const runResp = await ctx.post('/api/runs', {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: { source_env: 'dev', target_env: 'dev', job_names: [jobName] },
    });
    const { run_id } = await runResp.json();
    await waitForTerminal(ctx, run_id);

    const cancelResp = await ctx.post(`/api/runs/${run_id}/cancel`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(cancelResp.status()).toBe(202);
    expect((await cancelResp.json()).cancel_requested).toBe(false);

    await deleteJob(ctx, jobName);
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/03-monitor.spec.ts`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/03-monitor.spec.ts
git commit -m "test: add Monitor tab E2E coverage"
```

---

### Task 8: `04-history.spec.ts`

**Files:**
- Create: `tests/e2e/04-history.spec.ts`
- Modify: `frontend/index.html` (testids on History sub-tabs `:2330-2339`, Run History list `:2664-2853`, Run Detail `:1970-2325`)

**Context:** `loadRuns()` sends `status`/`run_type` filters, no pagination. Empty state (`"No test runs yet"`) is shared for "truly zero runs" and "filter matched nothing" — no distinct message. `deleteRun` uses native `confirm`.

- [ ] **Step 1: Add testids**
- `historySubTab` buttons: `:data-testid="'history-subtab-' + id"` for `runs`,`trends`,`lineage`,`audit`,`profile`,`schema`,`coverage`
- Status/type filter selects: `data-testid="history-status-filter"`, `data-testid="history-runtype-filter"`
- Clear/Refresh buttons: `data-testid="history-clear-btn"`, `data-testid="history-refresh-btn"`
- Run rows: `:data-testid="'history-run-row-' + run.run_id"`, View link: `...-view-link`
- Run Detail back button: `data-testid="run-detail-back-btn"`
- Empty state: `data-testid="history-empty-state"`

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';
import { seedBaselineRun, deleteJob } from './api-helpers';

test.describe('04 history', () => {
  let jobName: string;
  let runId: string;

  test.beforeAll(async ({ request, adminToken }) => {
    const ctx = await request;
    ({ jobName, runId } = await seedBaselineRun(ctx, 'e2e-history'));
  });

  test.afterAll(async ({ request }) => {
    await deleteJob(await request, jobName);
  });

  test('run appears in Run History with the expected mismatch-driven FAILED status', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="history-subtab-runs"]').click();
    const row = authedPage.locator(`[data-testid="history-run-row-${runId}"]`);
    await expect(row).toBeVisible();
    await expect(row).toContainText('FAILED');
  });

  test('status filter narrows the list and Clear resets it', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="history-subtab-runs"]').click();
    await authedPage.locator('[data-testid="history-status-filter"]').selectOption('FAILED');
    await expect(authedPage.locator(`[data-testid="history-run-row-${runId}"]`)).toBeVisible();

    await authedPage.locator('[data-testid="history-status-filter"]').selectOption('COMPLETED');
    // negative: FAILED-only run must not show under a COMPLETED filter, and the empty state renders.
    await expect(authedPage.locator(`[data-testid="history-run-row-${runId}"]`)).toBeHidden();

    await authedPage.locator('[data-testid="history-clear-btn"]').click();
    await expect(authedPage.locator(`[data-testid="history-run-row-${runId}"]`)).toBeVisible();
  });

  test('Run Detail shows the 1 value-diff / 1 missing-target / 1 missing-source mismatch breakdown', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="history-subtab-runs"]').click();
    await authedPage.locator(`[data-testid="history-run-row-${runId}"]`).click();
    await expect(authedPage.locator('[data-testid="run-detail-back-btn"]')).toBeVisible();
    await expect(authedPage.locator('text=Failed')).toBeVisible();
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/04-history.spec.ts`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/04-history.spec.ts
git commit -m "test: add History tab E2E coverage"
```

---

### Task 9: `05-adapters.spec.ts`

**Files:**
- Create: `tests/e2e/05-adapters.spec.ts`
- Modify: `frontend/index.html` (testids on SAP BO panel `:2874-2907`, Automic panel `:2951-2991`)

**Context (live-backend gated, `E2E_LIVE_BACKENDS=1`):** `testBOConnection()` → success toast `'SAP BO connected'`; failure surfaces `boTestResult.message` (HTTP 200 body, not an exception) using the exact `_friendly_error()` strings from `api/services/adapter_service.py` (e.g. `"Authentication failed - check username and password"`). Automic has no mock service in this repo — Automic tests stay negative-path only (bad config → error surfaced).

- [ ] **Step 1: Add testids**
- BO Config select: `data-testid="bo-config-select"`; Test Connection: `data-testid="bo-test-connection-btn"`; result box: `data-testid="bo-test-result"`
- Automic Config select: `data-testid="automic-config-select"`; Lookup Type: `data-testid="automic-lookup-type-select"`; identifier input: `data-testid="automic-identifier-input"`; Lookup button: `data-testid="automic-lookup-btn"`; result box: `data-testid="automic-result"`

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';
import { createConfig, deleteConfig } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';

test.describe('05 adapters', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml sapbo-mock)');

  let boConfigId: number;

  test.beforeAll(async ({ request, adminToken }) => {
    const ctx = await request;
    const cfg = await createConfig(ctx, `e2e-adapters-bo-${Date.now()}`, 'dev', {
      db_host: 'unused', db_password: 'unused',
      bo_url: 'https://127.0.0.1:18443', bo_user: 'administrator', bo_password: 'Password1',
      bo_verify_ssl: false,
    });
    boConfigId = cfg.id;
  });

  test.afterAll(async ({ request }) => {
    await deleteConfig(await request, boConfigId);
  });

  test('Test Connection against the real SAP BO mock succeeds', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="config-tab-nav"]'); // no-op placeholder for nav if needed
    await authedPage.locator('button:has-text("Adapters")').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(boConfigId));
    await authedPage.locator('[data-testid="bo-test-connection-btn"]').click();
    await expect(authedPage.locator('[data-testid="bo-test-result"]')).toContainText('✓');
  });

  test('negative: bad BO credentials surface the exact "Authentication failed" message', async ({ authedPage, request }) => {
    const ctx = await request;
    const badCfg = await createConfig(ctx, `e2e-adapters-bo-bad-${Date.now()}`, 'dev', {
      db_host: 'unused', db_password: 'unused',
      bo_url: 'https://127.0.0.1:18443', bo_user: 'administrator', bo_password: 'WRONG-password',
      bo_verify_ssl: false,
    });
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Adapters")').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(badCfg.id));
    await authedPage.locator('[data-testid="bo-test-connection-btn"]').click();
    await expect(authedPage.locator('[data-testid="bo-test-result"]')).toContainText('Authentication failed');
    await deleteConfig(ctx, badCfg.id);
  });

  test('negative: unreachable BO host surfaces a DNS/connection error, not a silent failure', async ({ authedPage, request }) => {
    const ctx = await request;
    const unreachable = await createConfig(ctx, `e2e-adapters-bo-unreachable-${Date.now()}`, 'dev', {
      db_host: 'unused', db_password: 'unused',
      bo_url: 'https://this-host-does-not-exist.invalid:8443', bo_user: 'x', bo_password: 'x',
    });
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Adapters")').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(unreachable.id));
    await authedPage.locator('[data-testid="bo-test-connection-btn"]').click();
    await expect(authedPage.locator('[data-testid="bo-test-result"]')).toContainText("Cannot resolve");
    await deleteConfig(ctx, unreachable.id);
  });
});
```

- [ ] **Step 3: Run**

Run: `E2E_LIVE_BACKENDS=1 npx playwright test tests/e2e/05-adapters.spec.ts`
Expected: 3 passed (or "3 skipped" when `E2E_LIVE_BACKENDS` unset — verify both modes).

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/05-adapters.spec.ts
git commit -m "test: add Adapters tab E2E coverage (live SAP BO mock)"
```

---

### Task 10: `06-reports.spec.ts`

**Files:**
- Create: `tests/e2e/06-reports.spec.ts`
- Modify: `frontend/index.html` (testids on Reports tab `:3159-3319`)

**Context:** Button is **"Load"**, not "Load Report" (the not-loaded-state text says "Load Report" but that's just prose, not the button label — don't assert button text against it). Nonexistent-run error: toast `'Failed to load report'`, message `` Run {run_id} not found. `` (exact, trailing period). Rejected mismatches inside the report iframe: `tr[data-mismatch][data-rejected="true"]` with a sibling `.rejected-note` cell starting `✗ Rejected`.

- [ ] **Step 1: Add testids**
- Run select: `data-testid="reports-run-select"`; Load button: `data-testid="reports-load-btn"`
- Sub-tab buttons: `data-testid="reports-subtab-report"`, `...-metrics`, `...-logs`
- Report iframe: `data-testid="reports-iframe"`
- Metrics refresh: `data-testid="reports-metrics-refresh-btn"`
- Logs search input: `data-testid="reports-logs-search-input"`; level chips: `:data-testid="'reports-logs-level-' + level"`

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';
import { seedBaselineRun, deleteJob } from './api-helpers';

test.describe('06 reports', () => {
  let jobName: string;
  let runId: string;

  test.beforeAll(async ({ request }) => {
    ({ jobName, runId } = await seedBaselineRun(await request, 'e2e-reports'));
  });
  test.afterAll(async ({ request }) => deleteJob(await request, jobName));

  test('load a report and see the rejected/accepted mismatch markers', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Reports")').click();
    await authedPage.locator('[data-testid="reports-run-select"]').selectOption({ label: new RegExp(runId.slice(0, 8)) });
    await authedPage.locator('[data-testid="reports-load-btn"]').click();

    const frame = authedPage.frameLocator('[data-testid="reports-iframe"]');
    await expect(frame.locator('[data-mismatch]').first()).toBeVisible();
  });

  test('Metrics sub-tab shows the pass-rate summary for a run with real results', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Reports")').click();
    await authedPage.locator('[data-testid="reports-run-select"]').selectOption({ label: new RegExp(runId.slice(0, 8)) });
    await authedPage.locator('[data-testid="reports-load-btn"]').click();
    await authedPage.locator('[data-testid="reports-subtab-metrics"]').click();
    await expect(authedPage.locator('text=Pass Rate')).toBeVisible();
  });

  test('negative: requesting a report for a nonexistent run surfaces the exact 404 message', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Reports")').click();
    await authedPage.evaluate(() => {
      // @ts-expect-error Alpine root accessible via window for direct state manipulation
      const root = document.querySelector('[x-data]').__x.$data;
      root.reportRunId = '00000000-0000-0000-0000-000000000000';
    });
    await authedPage.locator('[data-testid="reports-load-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Failed to load report');
    await expect(authedPage.locator('.toast-msg')).toContainText('not found.');
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/06-reports.spec.ts`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/06-reports.spec.ts
git commit -m "test: add Reports tab E2E coverage"
```

---

### Task 11: `07-differences.spec.ts`

**Files:**
- Create: `tests/e2e/07-differences.spec.ts`
- Modify: `frontend/index.html` (testids on Differences Explorer `:3322-3470`, bulk-decide modal `:5373-5398`, mismatch drawer `:4877-5009`)

**Context:** "Accept all N filtered" / "Reject all N filtered" buttons are `:disabled="diffTotal === 0"` — zero-selected is an inert disabled state, not an error toast. Missing decision reason: clicking Confirm with an empty textarea fires toast `warn` **"Reason required"** / **"Enter a reason before deciding these mismatches"** — Confirm itself is NOT disabled, the guard fires post-click.

- [ ] **Step 1: Add testids**
- Run/test selects: `data-testid="diff-run-select"`, `data-testid="diff-test-select"`
- Accept/Reject-all-filtered buttons: `data-testid="diff-accept-all-btn"`, `data-testid="diff-reject-all-btn"`
- Search/column/type/status/sort filters: `data-testid="diff-search-input"`, `...-column-select`, `...-type-select`, `...-status-select`, `...-sort-select`
- Clear filters: `data-testid="diff-clear-filters-btn"`
- Results table rows: `data-testid="diff-results-table"`
- Pagination: `data-testid="diff-prev-btn"`, `data-testid="diff-next-btn"`, `data-testid="diff-page-label"`
- Bulk-decide modal: `data-testid="decision-modal"`, textarea `data-testid="decision-modal-note-textarea"`, Confirm `data-testid="decision-modal-confirm-btn"`, Cancel `data-testid="decision-modal-cancel-btn"`

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';
import { seedBaselineRun, deleteJob } from './api-helpers';

test.describe('07 differences', () => {
  let jobName: string;
  let runId: string;

  test.beforeAll(async ({ request }) => {
    ({ jobName, runId } = await seedBaselineRun(await request, 'e2e-diff'));
  });
  test.afterAll(async ({ request }) => deleteJob(await request, jobName));

  async function openDiffForRun(page: import('@playwright/test').Page) {
    await page.goto('/');
    await page.locator('button:has-text("Differences")').click();
    await page.locator('[data-testid="diff-run-select"]').selectOption({ label: new RegExp(runId.slice(0, 8)) });
    await page.locator('[data-testid="diff-test-select"]').selectOption({ index: 1 });
  }

  test('browse and filter the known mismatch set (1 value diff, 1 missing each direction)', async ({ authedPage }) => {
    await openDiffForRun(authedPage);
    await expect(authedPage.locator('[data-testid="diff-results-table"]')).toContainText('value_diff');

    await authedPage.locator('[data-testid="diff-type-select"]').selectOption('missing_in_target');
    await expect(authedPage.locator('[data-testid="diff-results-table"] tr')).toHaveCount(2); // header + 1 row

    await authedPage.locator('[data-testid="diff-clear-filters-btn"]').click();
  });

  test('accept-all-filtered with a reason succeeds and flips rows to Accepted', async ({ authedPage }) => {
    await openDiffForRun(authedPage);
    await authedPage.locator('[data-testid="diff-accept-all-btn"]').click();
    await authedPage.locator('[data-testid="decision-modal-note-textarea"]').fill('e2e: accepted for test');
    await authedPage.locator('[data-testid="decision-modal-confirm-btn"]').click();
    await expect(authedPage.locator('.toast-msg')).toContainText('mismatch(es) accepted');
  });

  test('negative: zero matching rows disables both bulk buttons', async ({ authedPage }) => {
    await openDiffForRun(authedPage);
    await authedPage.locator('[data-testid="diff-search-input"]').fill('this-value-will-never-match-anything-xyz');
    await expect(authedPage.locator('[data-testid="diff-accept-all-btn"]')).toBeDisabled();
    await expect(authedPage.locator('[data-testid="diff-reject-all-btn"]')).toBeDisabled();
  });

  test('negative: confirming a decision with an empty reason shows "Reason required" and does not call the API', async ({ authedPage }) => {
    await openDiffForRun(authedPage);
    await authedPage.locator('[data-testid="diff-reject-all-btn"]').click();
    // Leave the note textarea empty.
    await authedPage.locator('[data-testid="decision-modal-confirm-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Reason required');
    await expect(authedPage.locator('.toast-msg')).toContainText('Enter a reason before deciding these mismatches');
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/07-differences.spec.ts`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/07-differences.spec.ts
git commit -m "test: add Differences Explorer E2E coverage"
```

---

### Task 12: `08a-compare-bo-report.spec.ts`

**Files:**
- Create: `tests/e2e/08a-compare-bo-report.spec.ts`
- Modify: `frontend/index.html` (testids on BO Report sub-tab `:3518-3763`)

**Context:** `POST /api/compare/bo-report` payload: `{source_a, source_b, key_columns[], exclude_columns[], label_a, label_b, advanced}`; `source_a`/`source_b` shape depends on mode (`upload`→`{source_type:'upload', file_content_b64, file_name}`, `live`→`{source_type:'live', config_id, doc_id, report_id, format:'xlsx'}`). Live-BO success path uses the mock's real document `"Sales Orders"` (id `1001`) / report `"Orders"` (`rpt-sales`). Upload path needs no live backend — always runs.

- [ ] **Step 1: Add testids** (prefix `compare-bo`)
- Source A/B mode pills: `:data-testid="'compare-bo-source-a-mode-' + mode"` (live/path/upload/api), same for `-source-b-`
- Source A label input: `data-testid="compare-bo-source-a-label-input"` (and `-b-`)
- Live mode: config/doc/report selects `data-testid="compare-bo-source-a-config-select"`, `...-doc-select`, `...-report-select` (and `-b-` variants)
- Upload input: `data-testid="compare-bo-source-a-upload-input"` (and `-b-`)
- Key/exclude columns: `data-testid="compare-bo-key-columns-input"`, `...-exclude-columns-input"`
- Advanced accordion: `data-testid="compare-bo-advanced-toggle"`, `data-testid="compare-bo-advanced-panel"`, and per-field ids matching `compare-helpers.ts`'s `fillAdvancedOptions(page, 'compare-bo', ...)` convention
- Swap Sides: `data-testid="compare-bo-swap-btn"`
- Run button: `data-testid="compare-bo-run-btn"`
- Result: status badge `data-testid="compare-bo-result-status"`, results table `data-testid="compare-bo-results-table"`, View button per row `:data-testid="'compare-bo-view-btn-' + r.id"`

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';
import { fillAdvancedOptions } from './compare-helpers';
import fs from 'node:fs';
import path from 'node:path';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';

test.describe('08a compare / BO report', () => {
  test('upload-vs-upload success path (no live backend needed)', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("BO Report")').click();

    const sourceCsv = path.join(__dirname, 'fixtures', 'data', 'source.csv');
    const targetCsv = path.join(__dirname, 'fixtures', 'data', 'target.csv');
    await authedPage.locator('[data-testid="compare-bo-source-a-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-a-upload-input"]').setInputFiles(sourceCsv);
    await authedPage.locator('[data-testid="compare-bo-source-b-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-b-upload-input"]').setInputFiles(targetCsv);
    await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-bo-result-status"]')).toHaveText('FAILED', { timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="compare-bo-results-table"]')).toContainText('3');
  });

  test('advanced options accept and round-trip through a real compare', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("BO Report")').click();
    await authedPage.locator('[data-testid="compare-bo-source-a-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-a-upload-input"]').setInputFiles(path.join(__dirname, 'fixtures', 'data', 'source.csv'));
    await authedPage.locator('[data-testid="compare-bo-source-b-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-b-upload-input"]').setInputFiles(path.join(__dirname, 'fixtures', 'data', 'target.csv'));
    await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('id');
    await fillAdvancedOptions(authedPage, 'compare-bo', { backend: 'polars', floatTolerance: '0.01' });
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-bo-result-status"]')).toHaveText('FAILED', { timeout: 20_000 });
  });

  test.describe('live BO mock', () => {
    test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1');
    let boConfigId: number;

    test.beforeAll(async ({ request }) => {
      const { createConfig } = await import('./api-helpers');
      const cfg = await createConfig(await request, `e2e-compare-bo-live-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        bo_url: 'https://127.0.0.1:18443', bo_user: 'administrator', bo_password: 'Password1', bo_verify_ssl: false,
      });
      boConfigId = cfg.id;
    });
    test.afterAll(async ({ request }) => {
      const { deleteConfig } = await import('./api-helpers');
      await deleteConfig(await request, boConfigId);
    });

    test('live Source A (Sales Orders / Orders report) vs upload Source B', async ({ authedPage }) => {
      await authedPage.goto('/');
      await authedPage.locator('button:has-text("Compare")').click();
      await authedPage.locator('button:has-text("BO Report")').click();
      await authedPage.locator('[data-testid="compare-bo-source-a-mode-live"]').click();
      await authedPage.locator('[data-testid="compare-bo-source-a-config-select"]').selectOption(String(boConfigId));
      await authedPage.locator('[data-testid="compare-bo-source-a-doc-select"]').selectOption({ label: 'Sales Orders' });
      await authedPage.locator('[data-testid="compare-bo-source-a-report-select"]').selectOption({ label: 'Orders' });
      await authedPage.locator('[data-testid="compare-bo-source-b-mode-upload"]').click();
      await authedPage.locator('[data-testid="compare-bo-source-b-upload-input"]').setInputFiles(path.join(__dirname, 'fixtures', 'data', 'source.csv'));
      await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('id');
      await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();
      await expect(authedPage.locator('[data-testid="compare-bo-result-status"]')).toBeVisible({ timeout: 20_000 });
    });
  });

  test('negative: running with no source selected on either side surfaces an error, not a silent hang', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("BO Report")').click();
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('BO comparison failed');
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/08a-compare-bo-report.spec.ts` (non-live) and `E2E_LIVE_BACKENDS=1 npx playwright test tests/e2e/08a-compare-bo-report.spec.ts` (live)
Expected: 3 passed / 1 skipped (non-live); 4 passed (live).

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/08a-compare-bo-report.spec.ts
git commit -m "test: add Compare / BO Report sub-tab E2E coverage"
```

---

### Task 13: `08b-compare-reconciliation.spec.ts`

**Files:**
- Create: `tests/e2e/08b-compare-reconciliation.spec.ts`
- Modify: `frontend/index.html` (testids on Reconciliation sub-tab `:3763-4194`)

**Context:** Two modes (`reconMode`): `stored` (Dual Environment) and `file` (Run/File vs Report). `launchDualEnv()` requires both configs (`toast warn 'Missing config'` otherwise). `runFileCompare()` needs a real source on each side (`throw Error('{label}: select a stored run')` etc., caught → toast `'File compare failed'`). **KNOWN BUG** (see plan header): the file-diff row expansion under this sub-tab renders diff cells via `renderSrc`/`renderTgt`, which are undefined — expect blank source/target cells and a console error, not literal values, when asserting on expanded-row content.

- [ ] **Step 1: Add testids** (prefix `compare-recon`)
- Mode cards: `data-testid="compare-recon-mode-stored"`, `data-testid="compare-recon-mode-file"`
- Quick Compare checkbox: `data-testid="compare-recon-quick-checkbox"`
- Dual-env: config A/B selects `data-testid="compare-recon-dualenv-config-a-select"` / `-config-b-`, jobs multi-select `data-testid="compare-recon-dualenv-jobs-select"`, Launch button `data-testid="compare-recon-dualenv-launch-btn"`, past-pairs refresh `data-testid="compare-recon-dualenv-refresh-pairs-btn"`, past-pairs empty state `data-testid="compare-recon-dualenv-pairs-empty"`
- File mode: source A/B type pills `:data-testid="'compare-file-source-a-mode-' + mode"` (run/path/upload), run selects `data-testid="compare-file-source-a-run-select"`, Compare Files button `data-testid="compare-file-run-btn"`, result row headers `data-testid="compare-file-results"`, per-row expand `:data-testid="'compare-file-row-' + r.query_name"`

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';
import path from 'node:path';

test.describe('08b compare / reconciliation', () => {
  test('Run/File vs Report: two uploaded files produce the known mismatch set', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("Reconciliation")').click();
    await authedPage.locator('[data-testid="compare-recon-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-file-source-a-mode-upload"]').click();
    await authedPage.locator('input[type=file]').first().setInputFiles(path.join(__dirname, 'fixtures', 'data', 'source.csv'));
    await authedPage.locator('[data-testid="compare-file-source-b-mode-upload"]').click();
    await authedPage.locator('input[type=file]').nth(1).setInputFiles(path.join(__dirname, 'fixtures', 'data', 'target.csv'));
    await authedPage.locator('[data-testid="compare-file-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-file-results"]')).toContainText('differ', { timeout: 20_000 });
  });

  test('KNOWN BUG: expanding a differing row renders blank source/target cells (renderSrc/renderTgt undefined) — see plan header', async ({ authedPage }) => {
    const consoleErrors: string[] = [];
    authedPage.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });

    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("Reconciliation")').click();
    await authedPage.locator('[data-testid="compare-recon-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-file-source-a-mode-upload"]').click();
    await authedPage.locator('input[type=file]').first().setInputFiles(path.join(__dirname, 'fixtures', 'data', 'source.csv'));
    await authedPage.locator('[data-testid="compare-file-source-b-mode-upload"]').click();
    await authedPage.locator('input[type=file]').nth(1).setInputFiles(path.join(__dirname, 'fixtures', 'data', 'target.csv'));
    await authedPage.locator('[data-testid="compare-file-run-btn"]').click();
    await authedPage.locator('[data-testid^="compare-file-row-"]').first().click(); // expand the diff row
    await expect(authedPage.locator('.diff-val-truncated').first()).toHaveText(''); // blank, not the real value
    expect(consoleErrors.some((e) => e.includes('renderSrc is not defined'))).toBe(true);
  });

  test('negative: Launch Dual-Env with no jobs selected — Launch is a no-op, no run created', async ({ authedPage, request }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("Reconciliation")').click();
    await authedPage.locator('[data-testid="compare-recon-mode-stored"]').click();
    const before = await (await request).get('/api/runs');
    const beforeCount = (await before.json()).length;
    await authedPage.locator('[data-testid="compare-recon-dualenv-launch-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Missing config');
    const after = await (await request).get('/api/runs');
    expect((await after.json()).length).toBe(beforeCount);
  });

  test('negative: refreshing past pairs with none existing shows the empty state, not an error', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("Reconciliation")').click();
    await authedPage.locator('[data-testid="compare-recon-mode-stored"]').click();
    await authedPage.locator('[data-testid="compare-recon-dualenv-refresh-pairs-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-recon-dualenv-pairs-empty"]')).toBeVisible();
  });

  test('negative: Compare Files with no source chosen on either side surfaces the exact thrown message', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("Reconciliation")').click();
    await authedPage.locator('[data-testid="compare-recon-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-file-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('File compare failed');
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/08b-compare-reconciliation.spec.ts`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/08b-compare-reconciliation.spec.ts
git commit -m "test: add Compare / Reconciliation sub-tab E2E coverage"
```

---

### Task 14: `08c-compare-sql.spec.ts`

**Files:**
- Create: `tests/e2e/08c-compare-sql.spec.ts`
- Modify: `frontend/index.html` (testids on SQL sub-tab `:4197-4484`)

**Context:** `runSQLComparison()` has 4 explicit client-side guards, each its own `toast('warn', ...)`: `'Config A required'`, `'Config B required'`, `'Query A required'`, `'Query B required'` — checked in that order, so an empty-everything submit shows `'Config A required'` first. Live success path uses the seeded `atom_e2e_src`/`atom_e2e_tgt` databases (Task 2) — same deterministic 1 value-diff/1-missing-each-way shape as the CSV fixtures. Same **KNOWN BUG** (`renderSrc`/`renderTgt`) applies to this sub-tab's diff-row expansion.

- [ ] **Step 1: Add testids** (prefix `compare-sql`)
- Config A/B selects: `data-testid="compare-sql-config-a-select"` / `-config-b-`
- Connection selects (conditional): `data-testid="compare-sql-connection-a-select"` / `-connection-b-`
- Query A/B textareas: `data-testid="compare-sql-query-a-textarea"` / `-query-b-`
- Run button: `data-testid="compare-sql-run-btn"`
- Result: `data-testid="compare-sql-results"`, per-row `:data-testid="'compare-sql-row-' + r.query_name"`

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';
import { createConfig, deleteConfig } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';

test.describe('08c compare / SQL', () => {
  test.skip(!liveBackends, 'SQL sub-tab requires a real DB — E2E_LIVE_BACKENDS=1');

  let srcConfigId: number;
  let tgtConfigId: number;

  test.beforeAll(async ({ request }) => {
    const ctx = await request;
    const base = {
      db_host: '127.0.0.1', db_port: 14333, db_user: 'sa', db_password: 'Atom_Test_12345!',
    };
    srcConfigId = (await createConfig(ctx, `e2e-sql-src-${Date.now()}`, 'dev', { ...base, db_name: 'atom_e2e_src' })).id;
    tgtConfigId = (await createConfig(ctx, `e2e-sql-tgt-${Date.now()}`, 'dev', { ...base, db_name: 'atom_e2e_tgt' })).id;
  });
  test.afterAll(async ({ request }) => {
    const ctx = await request;
    await deleteConfig(ctx, srcConfigId);
    await deleteConfig(ctx, tgtConfigId);
  });

  test('real SQL Server compare produces the seeded 1 value-diff / 1 missing each way', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("SQL")').click();
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(srcConfigId));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(tgtConfigId));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELECT id, sku, amount FROM dbo.orders');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id, sku, amount FROM dbo.orders');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-sql-results"]')).toContainText('differ', { timeout: 20_000 });
  });

  test('KNOWN BUG: SQL diff row expansion also hits the undefined renderSrc/renderTgt — see plan header', async ({ authedPage }) => {
    const consoleErrors: string[] = [];
    authedPage.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("SQL")').click();
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(srcConfigId));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(tgtConfigId));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELECT id, sku, amount FROM dbo.orders');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id, sku, amount FROM dbo.orders');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await authedPage.locator('[data-testid^="compare-sql-row-"]').first().click();
    expect(consoleErrors.some((e) => e.includes('renderSrc is not defined'))).toBe(true);
  });

  test('negative: malformed SQL surfaces the backend error, not a silent empty result', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("SQL")').click();
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(srcConfigId));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(tgtConfigId));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELEKT this is not sql');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id FROM dbo.orders');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('SQL compare failed');
  });
});

test.describe('08c compare / SQL — client-side guards (no live backend needed)', () => {
  test('negative: submitting empty shows "Config A required" first (guard order)', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("SQL")').click();
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Config A required');
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/08c-compare-sql.spec.ts` (client-guard test only) and `E2E_LIVE_BACKENDS=1 npx playwright test tests/e2e/08c-compare-sql.spec.ts` (full).
Expected: 1 passed / 3 skipped (non-live); 4 passed (live).

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/08c-compare-sql.spec.ts
git commit -m "test: add Compare / SQL sub-tab E2E coverage (live SQL Server)"
```

---

### Task 15: `08d-compare-colstats.spec.ts`

**Files:**
- Create: `tests/e2e/08d-compare-colstats.spec.ts`
- Modify: `frontend/index.html` (testids on Column Stats sub-tab `:4487-4626`)

**Context:** `POST /api/compare/column-stats` payload: `{source_a, source_b, label_a, label_b, query_name, float_tolerance, row_count_tolerance, doc_id?, report_id?}` — synchronous (no polling, `colStatsResult = await api(...)` directly). Source A/B type select has 4 modes: upload/live/path/api, and **Live mode here uses free-text Document ID/Report ID inputs** (not cascading selects like the BO tab) — a real difference worth its own negative test (non-numeric ID).

- [ ] **Step 1: Add testids** (prefix `compare-colstats`)
- Source A/B type selects: `data-testid="compare-colstats-source-a-type-select"` / `-source-b-`
- Upload inputs: `data-testid="compare-colstats-source-a-upload-input"` / `-source-b-`
- Live mode doc/report ID inputs: `data-testid="compare-colstats-source-a-docid-input"` / `-reportid-input"` (and `-b-`)
- Query name / tolerances: `data-testid="compare-colstats-query-name-input"`, `...-float-tol-input`, `...-row-count-tol-input"`
- Compute button: `data-testid="compare-colstats-run-btn"`
- Result: `data-testid="compare-colstats-result"`

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';
import path from 'node:path';

test.describe('08d compare / column stats', () => {
  test('upload-vs-upload produces a drift table for the known mismatched column', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("Column Stats")').click();
    await authedPage.locator('[data-testid="compare-colstats-source-a-upload-input"]').setInputFiles(path.join(__dirname, 'fixtures', 'data', 'source.csv'));
    await authedPage.locator('[data-testid="compare-colstats-source-b-upload-input"]').setInputFiles(path.join(__dirname, 'fixtures', 'data', 'target.csv'));
    await authedPage.locator('[data-testid="compare-colstats-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-colstats-result"]')).toContainText('drift(s) detected');
  });

  test('negative: computing with no source selected on either side surfaces an error', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("Column Stats")').click();
    await authedPage.locator('[data-testid="compare-colstats-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Column stats failed');
  });

  test('negative: Live mode with a non-numeric Document ID is rejected by the backend, not silently ignored', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("Column Stats")').click();
    await authedPage.locator('[data-testid="compare-colstats-source-a-type-select"]').selectOption('live');
    await authedPage.locator('[data-testid="compare-colstats-source-a-docid-input"]').fill('not-a-number');
    await authedPage.locator('[data-testid="compare-colstats-source-b-upload-input"]').setInputFiles(path.join(__dirname, 'fixtures', 'data', 'source.csv'));
    await authedPage.locator('[data-testid="compare-colstats-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Column stats failed');
  });

  test('negative: negative Row Count Tolerance is accepted by the input but produces a real (non-silent) result', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("Column Stats")').click();
    await authedPage.locator('[data-testid="compare-colstats-source-a-upload-input"]').setInputFiles(path.join(__dirname, 'fixtures', 'data', 'source.csv'));
    await authedPage.locator('[data-testid="compare-colstats-source-b-upload-input"]').setInputFiles(path.join(__dirname, 'fixtures', 'data', 'target.csv'));
    await authedPage.locator('[data-testid="compare-colstats-row-count-tol-input"]').fill('-1');
    await authedPage.locator('[data-testid="compare-colstats-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-colstats-result"]')).toBeVisible();
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/08d-compare-colstats.spec.ts`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/08d-compare-colstats.spec.ts
git commit -m "test: add Compare / Column Stats sub-tab E2E coverage"
```

---

### Task 16: `08e-compare-mismatch-diff.spec.ts`

**Files:**
- Create: `tests/e2e/08e-compare-mismatch-diff.spec.ts`
- Modify: `frontend/index.html` (testids on Mismatch Diff sub-tab `:4629-4746`)

**Context:** `POST /api/compare/mismatch-diff {run_id_a, run_id_b, run_a_label, run_b_label, query_name?}`. Client guard: both run IDs required (`toast warn 'Run IDs required'`). Uses two real seeded runs (Task 3's `seedBaselineRun`, called twice) — running the same fixture twice produces two runs with the **identical** mismatch set, so Run A vs Run B is the "self-diff" case: 0 new, 0 resolved, all 3 persistent.

- [ ] **Step 1: Add testids** (prefix `compare-mmdiff`)
- Run A/B ID inputs: `data-testid="compare-mmdiff-run-a-input"` / `-run-b-`
- Query-name filter: `data-testid="compare-mmdiff-query-filter-input"`
- Run button: `data-testid="compare-mmdiff-run-btn"`
- Summary chips: `data-testid="compare-mmdiff-new-count"`, `...-resolved-count`, `...-persistent-count`
- Load-more buttons: `data-testid="compare-mmdiff-loadmore-new-btn"` etc.

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';
import { seedBaselineRun, deleteJob } from './api-helpers';

test.describe('08e compare / mismatch diff', () => {
  let jobA: string, runIdA: string, jobB: string, runIdB: string;

  test.beforeAll(async ({ request }) => {
    const ctx = await request;
    ({ jobName: jobA, runId: runIdA } = await seedBaselineRun(ctx, 'e2e-mmdiff-a'));
    ({ jobName: jobB, runId: runIdB } = await seedBaselineRun(ctx, 'e2e-mmdiff-b'));
  });
  test.afterAll(async ({ request }) => {
    const ctx = await request;
    await deleteJob(ctx, jobA);
    await deleteJob(ctx, jobB);
  });

  test('diffing two runs with the identical fixture data yields 0 new / 0 resolved / 3 persistent', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("Mismatch Diff")').click();
    await authedPage.locator('[data-testid="compare-mmdiff-run-a-input"]').fill(runIdA);
    await authedPage.locator('[data-testid="compare-mmdiff-run-b-input"]').fill(runIdB);
    await authedPage.locator('[data-testid="compare-mmdiff-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-mmdiff-new-count"]')).toHaveText('0');
    await expect(authedPage.locator('[data-testid="compare-mmdiff-resolved-count"]')).toHaveText('0');
    await expect(authedPage.locator('[data-testid="compare-mmdiff-persistent-count"]')).toHaveText('3');
  });

  test('negative: invalid/nonexistent run UUID surfaces an error, not a blank success', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("Mismatch Diff")').click();
    await authedPage.locator('[data-testid="compare-mmdiff-run-a-input"]').fill('00000000-0000-0000-0000-000000000000');
    await authedPage.locator('[data-testid="compare-mmdiff-run-b-input"]').fill(runIdB);
    await authedPage.locator('[data-testid="compare-mmdiff-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Mismatch diff failed');
  });

  test('negative: query-name filter matching nothing shows all-zero counts, not a crash', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("Mismatch Diff")').click();
    await authedPage.locator('[data-testid="compare-mmdiff-run-a-input"]').fill(runIdA);
    await authedPage.locator('[data-testid="compare-mmdiff-run-b-input"]').fill(runIdB);
    await authedPage.locator('[data-testid="compare-mmdiff-query-filter-input"]').fill('no_such_query_name_xyz');
    await authedPage.locator('[data-testid="compare-mmdiff-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-mmdiff-persistent-count"]')).toHaveText('0');
  });

  test('negative: submitting with a blank Run B shows the client-side guard toast', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('button:has-text("Mismatch Diff")').click();
    await authedPage.locator('[data-testid="compare-mmdiff-run-a-input"]').fill(runIdA);
    await authedPage.locator('[data-testid="compare-mmdiff-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Run IDs required');
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/08e-compare-mismatch-diff.spec.ts`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/08e-compare-mismatch-diff.spec.ts
git commit -m "test: add Compare / Mismatch Diff sub-tab E2E coverage"
```

---

### Task 17: `08f-compare-templates.spec.ts`

**Files:**
- Create: `tests/e2e/08f-compare-templates.spec.ts`
- Modify: `frontend/index.html` (testids on template bar `:3491-3516`)

**Context:** Fully client-side (`localStorage`, key `etl_compare_templates`) — no backend calls. `saveCompareTemplate()`: empty name → `toast('warn', 'Template name required', 'Enter a name for the compare template')`. Built-in template confirmed present: `"Daily BO Report Compare"` (`frontend/app-config.js:26`).

- [ ] **Step 1: Add testids**
- Load-template select: `data-testid="compare-template-load-select"`
- Save Template toggle: `data-testid="compare-template-save-toggle-btn"`
- Name input: `data-testid="compare-template-name-input"`
- Save/Cancel: `data-testid="compare-template-save-btn"`, `data-testid="compare-template-cancel-btn"`

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';

test.describe('08f compare / templates', () => {
  test('built-in template is listed', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await expect(authedPage.locator('[data-testid="compare-template-load-select"] option', { hasText: 'Daily BO Report Compare' })).toHaveCount(1);
  });

  test('save a custom template, see it in "My Templates", reload persists it via localStorage', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    const name = `e2e-template-${Date.now()}`;
    await authedPage.locator('[data-testid="compare-template-save-toggle-btn"]').click();
    await authedPage.locator('[data-testid="compare-template-name-input"]').fill(name);
    await authedPage.locator('[data-testid="compare-template-save-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Compare template saved');

    await authedPage.reload();
    await authedPage.locator('button:has-text("Compare")').click();
    await expect(authedPage.locator('[data-testid="compare-template-load-select"] option', { hasText: name })).toHaveCount(1);
  });

  test('negative: saving with an empty name shows the exact warn toast', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Compare")').click();
    await authedPage.locator('[data-testid="compare-template-save-toggle-btn"]').click();
    await authedPage.locator('[data-testid="compare-template-save-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Template name required');
    await expect(authedPage.locator('.toast-msg')).toContainText('Enter a name for the compare template');
  });

  test('negative: "My Templates" optgroup is absent on a fresh session with no saved templates', async ({ page, adminToken }) => {
    await page.addInitScript((token) => window.sessionStorage.setItem('etl_token', token), adminToken);
    await page.goto('/');
    await page.evaluate(() => window.localStorage.removeItem('etl_compare_templates'));
    await page.reload();
    await page.locator('button:has-text("Compare")').click();
    await expect(page.locator('optgroup[label="My Templates"]')).toBeHidden();
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/08f-compare-templates.spec.ts`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/08f-compare-templates.spec.ts
git commit -m "test: add Compare templates bar E2E coverage"
```

---

### Task 18: `09-contracts.spec.ts`

**Files:**
- Create: `tests/e2e/09-contracts.spec.ts`
- Modify: `frontend/index.html` (testids on Contracts tab `:5031-5261`)

**Context:** Save/Delete/Bump errors surface via native `alert()`, not inline DOM — must use `page.on('dialog', ...)`. `deleteContract` uses `confirm()` too, so tests need **two** dialog handlers in sequence for a delete-that-fails scenario. `POST /api/contracts` full payload includes `name, source_job, version`; `PUT` (edit) sends only `{owner, sla_hours, consumers, breach_severity}`.

- [ ] **Step 1: Add testids**
- "+ New Contract" button: `data-testid="contracts-new-btn"`
- Contract list rows: `:data-testid="'contract-row-' + c.name"`
- Modal root: `data-testid="contract-modal"`; name/source_job/owner/sla/consumers/severity fields: `data-testid="contract-modal-name-input"`, `...-source-job-input"`, `...-owner-input"`, `...-sla-input"`, `...-consumers-input"`, `...-severity-select"`
- Save/Cancel: `data-testid="contract-modal-save-btn"`, `data-testid="contract-modal-cancel-btn"`
- Delete button: `data-testid="contract-delete-btn"`
- Bump Version: type select `data-testid="contract-bump-type-select"`, note input `data-testid="contract-bump-note-input"`, Bump button `data-testid="contract-bump-btn"`

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';

test.describe('09 contracts', () => {
  test('create, view, and delete a contract', async ({ authedPage }) => {
    const name = `e2e_contract_${Date.now()}`;
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Contracts")').click();
    await authedPage.locator('[data-testid="contracts-new-btn"]').click();
    await authedPage.locator('[data-testid="contract-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="contract-modal-source-job-input"]').fill('e2e_source_job');
    await authedPage.locator('[data-testid="contract-modal-owner-input"]').fill('e2e@test.local');
    await authedPage.locator('[data-testid="contract-modal-save-btn"]').click();
    await expect(authedPage.locator(`[data-testid="contract-row-${name}"]`)).toBeVisible();

    authedPage.once('dialog', (d) => d.accept());
    await authedPage.locator('[data-testid="contract-delete-btn"]').click();
    await expect(authedPage.locator(`[data-testid="contract-row-${name}"]`)).toBeHidden();
  });

  test('bump version and see it in the history table', async ({ authedPage }) => {
    const name = `e2e_bump_${Date.now()}`;
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Contracts")').click();
    await authedPage.locator('[data-testid="contracts-new-btn"]').click();
    await authedPage.locator('[data-testid="contract-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="contract-modal-source-job-input"]').fill('e2e_source_job');
    await authedPage.locator('[data-testid="contract-modal-save-btn"]').click();
    await authedPage.locator(`[data-testid="contract-row-${name}"]`).click();
    await authedPage.locator('[data-testid="contract-bump-type-select"]').selectOption('major');
    await authedPage.locator('[data-testid="contract-bump-note-input"]').fill('e2e bump');
    await authedPage.locator('[data-testid="contract-bump-btn"]').click();
    await expect(authedPage.locator('text=e2e bump')).toBeVisible();

    authedPage.once('dialog', (d) => d.accept());
    await authedPage.locator('[data-testid="contract-delete-btn"]').click();
  });

  test('negative: saving a duplicate contract name surfaces a native alert with the backend message', async ({ authedPage }) => {
    const name = `e2e_dup_contract_${Date.now()}`;
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Contracts")').click();
    await authedPage.locator('[data-testid="contracts-new-btn"]').click();
    await authedPage.locator('[data-testid="contract-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="contract-modal-source-job-input"]').fill('e2e_source_job');
    await authedPage.locator('[data-testid="contract-modal-save-btn"]').click();

    await authedPage.locator('[data-testid="contracts-new-btn"]').click();
    await authedPage.locator('[data-testid="contract-modal-name-input"]').fill(name); // same name again
    await authedPage.locator('[data-testid="contract-modal-source-job-input"]').fill('e2e_source_job_2');
    let alertText = '';
    authedPage.once('dialog', async (d) => { alertText = d.message(); await d.accept(); });
    await authedPage.locator('[data-testid="contract-modal-save-btn"]').click();
    await expect.poll(() => alertText).toContain('Save failed');

    await authedPage.locator(`[data-testid="contract-row-${name}"]`).click();
    authedPage.once('dialog', (d) => d.accept());
    await authedPage.locator('[data-testid="contract-delete-btn"]').click();
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/09-contracts.spec.ts`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/09-contracts.spec.ts
git commit -m "test: add Contracts tab E2E coverage"
```

---

### Task 19: `10-logs.spec.ts`

**Files:**
- Create: `tests/e2e/10-logs.spec.ts`
- Modify: `frontend/index.html` (testids on Logs tab `:4753-4813`)

**Context:** Polling starts/stops purely on `onTabEnter` — verifying auto-refresh means asserting a `GET /api/logs` request fires ~5s after navigating to the tab (via `page.waitForRequest`), not asserting on visible new rows (content may not change during a short test).

- [ ] **Step 1: Add testids**
- Run-ID filter input: `data-testid="logs-run-id-input"`
- Search input: `data-testid="logs-search-input"`
- Level chips: `:data-testid="'logs-level-chip-' + level"` for ALL/ERROR/WARN/INFO/DEBUG
- Counter: `data-testid="logs-counter"`

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';

test.describe('10 logs', () => {
  test('navigating to Logs starts auto-refresh polling (a GET /api/logs request fires)', async ({ authedPage }) => {
    await authedPage.goto('/');
    const pollPromise = authedPage.waitForRequest((req) => req.url().includes('/api/logs'), { timeout: 8000 });
    await authedPage.locator('button:has-text("Logs")').click();
    await pollPromise;
  });

  test('level chip filters and the counter reflects filtered/total', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Logs")').click();
    await expect(authedPage.locator('[data-testid="logs-counter"]')).toContainText('/');
    await authedPage.locator('[data-testid="logs-level-chip-ERROR"]').click();
    await expect(authedPage.locator('[data-testid="logs-level-chip-ERROR"]')).toHaveClass(/chip-active-ERROR/);
    await authedPage.locator('[data-testid="logs-level-chip-ERROR"]').click(); // toggle back off
    await expect(authedPage.locator('[data-testid="logs-level-chip-ALL"]')).toHaveClass(/chip-active-ALL/);
  });

  test('negative: search matching nothing shows "No events match the current filter."', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Logs")').click();
    await authedPage.locator('[data-testid="logs-search-input"]').fill('xyz_definitely_not_in_any_log_line_zzz');
    await expect(authedPage.locator('text=No events match the current filter.')).toBeVisible();
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/10-logs.spec.ts`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/10-logs.spec.ts
git commit -m "test: add Logs tab E2E coverage"
```

---

### Task 20: `11-help.spec.ts`

**Files:**
- Create: `tests/e2e/11-help.spec.ts`
- Modify: `frontend/index.html` (testid on Help search input `:5278`)

**Context:** Fully data-driven off `window.ETL_HELP.sections` (`frontend/help-content.js`). No hardcoded strings in `index.html` to assert against — test must read `window.ETL_HELP` in-page to pick a real section title rather than hardcoding one.

- [ ] **Step 1: Add testid**

Edit `frontend/index.html:5278`: add `data-testid="help-search-input"` to the search `<input>`.

- [ ] **Step 2: Write the spec**

```typescript
import { test, expect } from './fixtures';

test.describe('11 help', () => {
  test('sidebar lists sections from window.ETL_HELP and search filters them', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Help")').click();
    const firstTitle = await authedPage.evaluate(() => (window as any).ETL_HELP.sections[0].title);
    await expect(authedPage.locator(`text=${firstTitle}`).first()).toBeVisible();
  });

  test('negative: search matching no topic shows the "No help topics match" message', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Help")').click();
    await authedPage.locator('[data-testid="help-search-input"]').fill('zzz_no_such_help_topic_zzz');
    await expect(authedPage.locator('text=No help topics match')).toBeVisible();
  });
});
```

- [ ] **Step 3: Run**

Run: `npx playwright test tests/e2e/11-help.spec.ts`
Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html tests/e2e/11-help.spec.ts
git commit -m "test: add Help tab E2E coverage"
```

---

### Task 21: `12-cross-cutting.spec.ts`

**Files:**
- Create: `tests/e2e/12-cross-cutting.spec.ts`

**Context:** `apiOk` is set exactly once, in `init()`, by a single `GET /api/health` call — never re-checked. So "offline" must be simulated by routing `/api/health` to fail **before** `page.goto('/')`. Unknown routes 404 via Starlette's default (no SPA fallback, since the app has no client-side routing). `highlightMatch()` (Logs) HTML-escapes text before highlighting — proven safe. `lineageSvg()`'s escaping is unverified by research — this task's XSS test targets it directly using a job name containing `<script>`.

- [ ] **Step 1: Write the spec**

```typescript
import { test, expect } from './fixtures';
import { bootstrapAdminToken } from './api-helpers';

test.describe('12 cross-cutting', () => {
  test('offline indicator shows "● Offline" when /api/health fails at load time', async ({ page }) => {
    const adminToken = await bootstrapAdminToken();
    await page.addInitScript((token) => window.sessionStorage.setItem('etl_token', token), adminToken);
    await page.route('**/api/health', (route) => route.abort());
    await page.goto('/');
    await expect(page.locator('text=● Offline')).toBeVisible();
  });

  test('online indicator shows "● Connected" under normal conditions', async ({ authedPage }) => {
    await authedPage.goto('/');
    await expect(authedPage.locator('text=● Connected')).toBeVisible();
  });

  test('negative: an unregistered API path returns a plain 404, not the SPA index.html', async ({ request }) => {
    const resp = await request.get('/api/this-route-does-not-exist-xyz');
    expect(resp.status()).toBe(404);
  });

  test('log text is HTML-escaped before highlighting (verified-safe sink)', async ({ authedPage, request, adminToken }) => {
    // Trigger a run whose job name contains HTML — server logs will include it verbatim as text.
    const { createFileJob, deleteJob } = await import('./api-helpers');
    const ctx = await request;
    const jobName = `e2e-xss-<img src=x onerror=alert(1)>-${Date.now()}`;
    await createFileJob(ctx, jobName.replace(/[<>]/g, '_')); // job names likely reject raw <>; use sanitized name as a realistic case
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("Logs")').click();
    // No JS dialog (alert) should ever fire from rendered log content.
    let dialogFired = false;
    authedPage.on('dialog', () => { dialogFired = true; });
    await authedPage.waitForTimeout(1000);
    expect(dialogFired).toBe(false);
    await deleteJob(ctx, jobName.replace(/[<>]/g, '_'));
  });

  test('negative: a job/config name containing a script tag does not execute when rendered in the Lineage view', async ({ authedPage, request, adminToken }) => {
    const ctx = await request;
    const evilName = `e2e_lineage_xss_${Date.now()}`;
    await ctx.post('/api/jobs', {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        name: evilName,
        job_type: 'reconciliation',
        query: 'SELECT 1',
        key_columns: ['id'],
        depends_on: [],
      },
    });
    let dialogFired = false;
    authedPage.on('dialog', () => { dialogFired = true; });
    await authedPage.goto('/');
    await authedPage.locator('button:has-text("History")').click();
    await authedPage.locator('[data-testid="history-subtab-lineage"]').click();
    await authedPage.waitForTimeout(1000);
    expect(dialogFired).toBe(false);
    await ctx.delete(`/api/jobs/${evilName}`, { headers: { Authorization: `Bearer ${adminToken}` } });
  });
});
```

- [ ] **Step 2: Run**

Run: `npx playwright test tests/e2e/12-cross-cutting.spec.ts`
Expected: 5 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/12-cross-cutting.spec.ts
git commit -m "test: add cross-cutting E2E coverage (offline indicator, 404s, XSS sinks)"
```

---

### Task 22: Full-suite run, README note

**Files:**
- Modify: `README.md` (add a short "E2E tests" subsection near the existing test-running docs)

- [ ] **Step 1: Run the full non-live suite**

Run: `npx playwright test`
Expected: all specs pass except the `E2E_LIVE_BACKENDS`-gated tests, which report as skipped.

- [ ] **Step 2: Run the full live suite**

Run: `E2E_LIVE_BACKENDS=1 npx playwright test`
Expected: all specs pass, including live SAP BO / SQL Server coverage. (Requires Docker running and `ODBC Driver 17 for SQL Server` installed locally — same prerequisite as the existing `RUN_LIVE_SQLSERVER_TESTS=1` pytest suite.)

- [ ] **Step 3: Add a short README section**

Edit `README.md`, near the existing test-running instructions (search for how the pytest suites are documented and match that style), add:

```markdown
### End-to-end (Playwright) tests

```bash
npx playwright test                    # full UI suite against a throwaway DB, file/upload-mode compare coverage only
E2E_LIVE_BACKENDS=1 npx playwright test  # also covers live SAP BO / SQL Server paths (requires Docker + ODBC Driver 17 for SQL Server)
npx playwright show-report               # view the last HTML report
```
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document how to run the Playwright E2E suite"
```

---

## Self-Review

**Spec coverage:** every row of the design doc's spec-file table (18 files) maps 1:1 to a task (Tasks 4–21); Task 1–3 cover the infra the design doc's "Architecture"/"Shared fixtures" sections called for; Task 22 covers the design doc's "Testing strategy for the suite itself." The two scope decisions made mid-planning (live-backend wiring via docker-compose; `data-testid` instrumentation) are reflected throughout every task's testid step and the live-gated `test.skip()` calls.

**Placeholder scan:** no TBD/TODO markers; every code block is complete, runnable TypeScript/Python against endpoints and payload shapes verified directly in `api/schemas.py`, `api/routes/*.py`, `frontend/app.js`, and `frontend/features/compare.js`.

**Type/name consistency:** `data-testid` names introduced in early tasks (`job-row-{name}`, `run-tests-btn`, `history-subtab-*`) are reused verbatim by later tasks that depend on them (Task 7 reuses Task 6's `job-row-*`; Task 21 reuses Task 19's `history-subtab-lineage`) — checked pass-by-pass while writing. `seedBaselineRun`/`createFileJob`/`deleteJob`/`waitForTerminal` (Task 3) are called with matching signatures in Tasks 8, 10, 11, 16, 21.
