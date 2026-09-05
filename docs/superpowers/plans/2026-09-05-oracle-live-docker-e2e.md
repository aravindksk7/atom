# Oracle Live-Docker E2E & AWS Suite Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live Oracle container to the integration Docker stack, seed two comparable schemas, add a Playwright E2E spec that runs a real Oracle-to-Oracle compare, then run the full live-docker suite (Oracle + the existing live AWS specs) and fix any real failures found.

**Architecture:** Mirrors the existing SQL Server live-docker pattern exactly: a new `oracle` service in `docker-compose.integration.yml`, a `seedOracle()` step in `tests/e2e/global-setup.ts` (two schemas standing in for SQL Server's two databases), and a new Playwright spec (`tests/e2e/45-oracle-compare.spec.ts`) structurally identical to `tests/e2e/08c-compare-sql.spec.ts`.

**Tech Stack:** Docker Compose, `gvenzl/oracle-free:23` image, Python `oracledb` (thin mode, no Oracle Instant Client), Playwright, `@playwright/test`.

**Reference spec:** `docs/superpowers/specs/2026-09-05-oracle-live-docker-e2e-design.md`

---

### Task 0: Prerequisites

- [x] **Step 1: Confirm Docker is available and running**

Run: `docker --version`
Expected: prints a Docker version (e.g. `Docker version 29.6.1, build ...`). If this fails, Docker Desktop must be started before continuing — stop and tell the user.

- [x] **Step 2: Install the `oracledb` Python package into the active environment**

Run: `pip install oracledb`
Expected: `Successfully installed oracledb-<version>`

This package is declared as `pyproject.toml`'s `oracle` extra but not installed by default (same situation as Netezza's `nzpy`) — without it, both the seed script and the FastAPI backend's `oracle+oracledb://` SQLAlchemy dialect fail to load.

- [x] **Step 3: Verify the import works**

Run: `python -c "import oracledb; print(oracledb.__version__)"`
Expected: prints a version string, e.g. `2.4.1`, no `ModuleNotFoundError`.

---

### Task 1: Add the Oracle Service to `docker-compose.integration.yml`

**Files:**
- Modify: `docker-compose.integration.yml:44-61` (insert a new `oracle` service between the existing `sqlserver` and `minio` blocks)

- [x] **Step 1: Add the `oracle` service block**

Insert immediately after the `sqlserver` service's closing block (after the line `start_period: 20s` that ends `sqlserver`, before the `minio:` line) in `docker-compose.integration.yml`:

```yaml
  oracle:
    image: gvenzl/oracle-free:23
    container_name: atom-oracle-integration
    environment:
      ORACLE_PASSWORD: "Oracle_Test_12345"
      APP_USER: "e2e_src"
      APP_USER_PASSWORD: "Oracle_Test_12345"
    ports:
      - "1521:1521"
    healthcheck:
      test: ["CMD", "healthcheck.sh"]
      interval: 10s
      timeout: 5s
      retries: 40
      start_period: 30s
```

`gvenzl/oracle-free` is a free, unlicensed-pull community image (no Oracle account/license wall), and auto-creates the `e2e_src` user in its default pluggable database `FREEPDB1` on first boot via `APP_USER`/`APP_USER_PASSWORD`. The healthcheck uses the image's own bundled `healthcheck.sh`; the larger retry budget (40 × 10s = ~400s) accounts for Oracle's slower first boot compared to SQL Server's.

- [x] **Step 2: Bring up just the Oracle service and confirm it becomes healthy**

Run: `docker compose -f docker-compose.integration.yml up -d oracle --wait`
Expected: command completes (does not time out) and prints something ending in `oracle Healthy` or exits 0. First boot commonly takes 1-3 minutes — this is expected, not a bug. If it times out, run `docker compose -f docker-compose.integration.yml logs oracle` and check for `DATABASE IS READY TO USE!` near the end of the log.

- [x] **Step 3: Tear down**

Run: `docker compose -f docker-compose.integration.yml down -v`
Expected: exits 0, removes the container and its anonymous volume.

- [x] **Step 4: Commit**

```bash
git add docker-compose.integration.yml
git commit -m "feat(docker): add live Oracle container to integration compose stack

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Seed Two Oracle Schemas in `global-setup.ts`

**Files:**
- Modify: `tests/e2e/global-setup.ts`

- [x] **Step 1: Bring the Oracle container back up standalone to develop the seed script against**

Run: `docker compose -f docker-compose.integration.yml up -d oracle --wait`
Expected: same as Task 1 Step 2 — `oracle Healthy`.

- [x] **Step 2: Prove the raw seed logic works before embedding it**

Run this exact Python script (it is the same script that gets embedded into `seedOracle()` in Step 4 below) via `python -c "<script>"` or by saving it to a scratch `.py` file and running it — confirm it succeeds first:

```python
import oracledb

sys_conn = oracledb.connect(user="system", password="Oracle_Test_12345", dsn="127.0.0.1:1521/FREEPDB1")
sys_conn.autocommit = True
cur = sys_conn.cursor()
cur.execute("""
DECLARE
  user_count NUMBER;
BEGIN
  SELECT COUNT(*) INTO user_count FROM all_users WHERE username = 'E2E_TGT';
  IF user_count = 0 THEN
    EXECUTE IMMEDIATE 'CREATE USER e2e_tgt IDENTIFIED BY "Oracle_Test_12345"';
    EXECUTE IMMEDIATE 'GRANT CONNECT, RESOURCE, UNLIMITED TABLESPACE TO e2e_tgt';
  END IF;
END;
""")
sys_conn.close()

def seed(user, password, rows):
    conn = oracledb.connect(user=user, password=password, dsn="127.0.0.1:1521/FREEPDB1")
    conn.autocommit = True
    c = conn.cursor()
    try:
        c.execute("DROP TABLE orders")
    except oracledb.DatabaseError:
        pass
    c.execute("CREATE TABLE orders (id NUMBER PRIMARY KEY, sku VARCHAR2(50) NOT NULL, amount NUMBER(10,2) NOT NULL)")
    c.executemany("INSERT INTO orders (id, sku, amount) VALUES (:1, :2, :3)", rows)
    conn.close()

seed("e2e_src", "Oracle_Test_12345", [(1, "A100", 25.50), (2, "B200", 50.00), (3, "C300", 75.00)])
seed("e2e_tgt", "Oracle_Test_12345", [(1, "A100", 25.50), (2, "B200", 55.00), (4, "D400", 99.00)])
print("seeded")
```

Expected: prints `seeded` with no traceback. If `ORA-01017: invalid username/password` appears, the container hasn't finished creating `APP_USER=e2e_src` yet — re-run after waiting another 30s.

- [x] **Step 3: Tear down the standalone container**

Run: `docker compose -f docker-compose.integration.yml down -v`
Expected: exits 0.

- [x] **Step 4: Add `seedOracle()` to `tests/e2e/global-setup.ts`**

Add this function after `seedSqlServer()` (after its closing `}` on line 134):

```typescript
function seedOracle() {
  // Oracle has no direct equivalent of SQL Server's "two databases on one login" --
  // schema-per-user is the analog, so the src/tgt split here is two Oracle users
  // rather than two databases (see seedSqlServer() above for the SQL Server shape
  // this mirrors). e2e_src is auto-created by the gvenzl/oracle-free image itself
  // via the APP_USER/APP_USER_PASSWORD env vars in docker-compose.integration.yml;
  // e2e_tgt is created here, the same way seedSqlServer() creates both of its
  // databases itself rather than relying on compose.
  const script = `
import oracledb

sys_conn = oracledb.connect(user="system", password="Oracle_Test_12345", dsn="127.0.0.1:1521/FREEPDB1")
sys_conn.autocommit = True
cur = sys_conn.cursor()
cur.execute("""
DECLARE
  user_count NUMBER;
BEGIN
  SELECT COUNT(*) INTO user_count FROM all_users WHERE username = 'E2E_TGT';
  IF user_count = 0 THEN
    EXECUTE IMMEDIATE 'CREATE USER e2e_tgt IDENTIFIED BY "Oracle_Test_12345"';
    EXECUTE IMMEDIATE 'GRANT CONNECT, RESOURCE, UNLIMITED TABLESPACE TO e2e_tgt';
  END IF;
END;
""")
sys_conn.close()

def seed(user, password, rows):
    conn = oracledb.connect(user=user, password=password, dsn="127.0.0.1:1521/FREEPDB1")
    conn.autocommit = True
    c = conn.cursor()
    try:
        c.execute("DROP TABLE orders")
    except oracledb.DatabaseError:
        pass
    c.execute("CREATE TABLE orders (id NUMBER PRIMARY KEY, sku VARCHAR2(50) NOT NULL, amount NUMBER(10,2) NOT NULL)")
    c.executemany("INSERT INTO orders (id, sku, amount) VALUES (:1, :2, :3)", rows)
    conn.close()

seed("e2e_src", "Oracle_Test_12345", [(1, "A100", 25.50), (2, "B200", 50.00), (3, "C300", 75.00)])
seed("e2e_tgt", "Oracle_Test_12345", [(1, "A100", 25.50), (2, "B200", 55.00), (4, "D400", 99.00)])
print("seeded")
`;
  const result = spawnSync('python', ['-c', script], { encoding: 'utf-8' });
  if (result.status !== 0) {
    throw new Error(`Oracle seed failed:\n${result.stdout}\n${result.stderr}`);
  }
  console.log('[global-setup] Oracle seeded:', result.stdout.trim());
}
```

- [x] **Step 5: Call `seedOracle()` from `globalSetup()` and raise the compose timeout**

In `tests/e2e/global-setup.ts`, change:

```typescript
    execSync('docker compose -f docker-compose.integration.yml up -d --wait', {
      cwd: REPO_ROOT,
      stdio: 'inherit',
      timeout: 180_000,
    });
    seedSqlServer();
    seedMinio();
```

to:

```typescript
    execSync('docker compose -f docker-compose.integration.yml up -d --wait', {
      cwd: REPO_ROOT,
      stdio: 'inherit',
      // Raised from 180_000: Oracle's first boot (gvenzl/oracle-free's initial
      // datafile/catalog creation) commonly takes 1-3 minutes on top of the other
      // services, longer than the previous budget allowed for.
      timeout: 420_000,
    });
    seedSqlServer();
    seedOracle();
    seedMinio();
```

- [x] **Step 6: Commit**

```bash
git add tests/e2e/global-setup.ts
git commit -m "feat(e2e): seed live Oracle src/tgt schemas in global-setup

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Add the Oracle Compare Playwright Spec

**Files:**
- Create: `tests/e2e/45-oracle-compare.spec.ts`

- [x] **Step 1: Write the spec**

```typescript
import { test, expect } from './fixtures';
import { createConfig, deleteConfig, authedContext } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';

async function openSQL(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-sql"]').click();
}

test.describe('45 compare / Oracle', () => {
  test.skip(!liveBackends, 'Oracle sub-tab requires E2E_LIVE_BACKENDS=1');

  let srcConfigId: number;
  let tgtConfigId: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    const base = { db_type: 'oracle', db_driver: 'oracledb', db_host: '127.0.0.1', db_port: 1521, db_name: 'FREEPDB1' };
    try {
      srcConfigId = (await createConfig(ctx, `e2e-oracle-src-${Date.now()}`, 'dev', {
        ...base,
        db_user: 'e2e_src',
        db_password: 'Oracle_Test_12345',
      })).id;
      tgtConfigId = (await createConfig(ctx, `e2e-oracle-tgt-${Date.now()}`, 'dev', {
        ...base,
        db_user: 'e2e_tgt',
        db_password: 'Oracle_Test_12345',
      })).id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      if (srcConfigId) await deleteConfig(ctx, srcConfigId);
      if (tgtConfigId) await deleteConfig(ctx, tgtConfigId);
    } finally {
      await ctx.dispose();
    }
  });

  test('real Oracle compare produces deterministic differences', async ({ authedPage }) => {
    await openSQL(authedPage);
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(srcConfigId));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(tgtConfigId));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELECT id, sku, amount FROM orders');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id, sku, amount FROM orders');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-sql-results"]')).toContainText('Results', { timeout: 20_000 });
    await expect(authedPage.locator('.compare-chip.chip-regressed')).toHaveText('1 differ');
  });

  test('Oracle diff row expansion renders real source/target values', async ({ authedPage }) => {
    const pageErrors: string[] = [];
    authedPage.on('pageerror', (err) => pageErrors.push(err.message));

    await openSQL(authedPage);
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(srcConfigId));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(tgtConfigId));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELECT id, sku, amount FROM orders');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id, sku, amount FROM orders');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-sql-results"]')).toContainText('Results', { timeout: 20_000 });

    await authedPage.locator('[data-testid^="compare-sql-row-"]').first().click();
    const firstValueCell = authedPage.locator('td.text-slate-700 span:visible').first();
    await expect(firstValueCell).not.toHaveText('undefined');
    await expect(firstValueCell).not.toBeEmpty();
    expect(pageErrors.some((e) => e.includes('renderSrc is not defined'))).toBe(false);
    expect(pageErrors.some((e) => e.includes('renderTgt is not defined'))).toBe(false);
  });

  test('negative: malformed SQL surfaces backend error', async ({ authedPage }) => {
    await openSQL(authedPage);
    await authedPage.locator('[data-testid="compare-sql-config-a-select"]').selectOption(String(srcConfigId));
    await authedPage.locator('[data-testid="compare-sql-config-b-select"]').selectOption(String(tgtConfigId));
    await authedPage.locator('[data-testid="compare-sql-query-a-textarea"]').fill('SELEKT this is not sql');
    await authedPage.locator('[data-testid="compare-sql-query-b-textarea"]').fill('SELECT id FROM orders');
    await authedPage.locator('[data-testid="compare-sql-run-btn"]').click();
    await expect(authedPage.locator('.badge:visible', { hasText: 'ERROR' })).toBeVisible({ timeout: 20_000 });
  });
});
```

This is a structural mirror of `tests/e2e/08c-compare-sql.spec.ts:1-89`, substituting Oracle's `db_type`/`db_driver`/schema-per-user shape for SQL Server's `db_name`-per-database shape (see Task 2's `seedOracle()` for why the two Configs differ by `db_user` rather than `db_name`).

- [x] **Step 2: Run the full live stack (now including Oracle) and execute this spec**

Run (from repo root, PowerShell):
```
$env:E2E_LIVE_BACKENDS = "1"
npx playwright test tests/e2e/45-oracle-compare.spec.ts
```
Expected: all 3 tests in `45 compare / Oracle` pass. `00-auth-setup.spec.ts` runs first automatically (it's the `setup` project, a dependency of `chromium` — see `playwright.config.ts:41-49`). Global setup will bring up the full `docker-compose.integration.yml` stack including the new `oracle` service, so first run may take several minutes.

If a test fails, check `playwright-report/index.html` and `docker compose -f docker-compose.integration.yml logs oracle` before changing assertions — the goal is a real passing live compare, not a loosened one.

- [x] **Step 3: Tear down**

Global teardown runs automatically at the end of the Playwright run (`docker compose ... down -v`, from `tests/e2e/global-teardown.ts:14`). Confirm with:
Run: `docker ps --filter name=atom-oracle-integration`
Expected: no rows (container removed).

- [x] **Step 4: Commit**

```bash
git add tests/e2e/45-oracle-compare.spec.ts
git commit -m "test(e2e): add live Oracle compare spec

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Verify the Existing Live AWS Suite Still Passes Alongside Oracle

**Files:** none expected — this task is verification, with fixes only if a real failure is found (any fix goes in the specific file the bug lives in, following `superpowers:systematic-debugging`).

- [x] **Step 1: Run the full live-docker AWS + Oracle suite together**

Run (PowerShell):
```
$env:E2E_LIVE_BACKENDS = "1"
npx playwright test tests/e2e/18-aws-s3-tab-live.spec.ts tests/e2e/19-aws-glue-tab.spec.ts tests/e2e/20-aws-athena-tab.spec.ts tests/e2e/21-aws-airflow-tab.spec.ts tests/e2e/38-live-docker-aws-compare.spec.ts tests/e2e/41-live-docker-config-schema-browse.spec.ts tests/e2e/42-live-docker-matrix-reconciliation.spec.ts tests/e2e/43-live-docker-matrix-save-as-job.spec.ts tests/e2e/45-oracle-compare.spec.ts
```
Expected: every test passes.

- [x] **Step 2: If any test fails, diagnose with `superpowers:systematic-debugging` before touching assertions**

For each failure: read the Playwright trace (`playwright-report/`), check whether the new Oracle container changed compose startup ordering/timing (e.g. the raised `execSync` timeout in Task 2 Step 5, or LocalStack/SQL Server health racing against Oracle's slower boot), and fix the actual root cause (timing, selector, backend bug) in its own file. Do not weaken an assertion just to make it pass.

- [x] **Step 3: Run the full pytest suite to confirm the Oracle changes didn't regress mocked/unit coverage**

Run: `python -m pytest tests/ -x -q`
Expected: all tests pass, including the existing mocked `tests/test_oracle_integration.py` (unaffected by the new live container — it never connects to a real DB).

- [x] **Step 4: Commit any fixes made in Step 2** (skip if Step 1 was already all-green)

```bash
git add <fixed files>
git commit -m "fix(e2e): <describe the specific root cause fixed>

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** §2.1 (compose service) → Task 1; §2.2 (seed) → Task 2; §2.3 (prereq) → Task 0; §3.1 (spec) → Task 3; §3.2 (AWS verification) → Task 4; §4 (verification criteria) → Task 4 Steps 1 & 3.
- **Type/name consistency checked:** `db_user`/`db_password` values (`e2e_src`/`e2e_tgt`/`Oracle_Test_12345`) and `db_name: 'FREEPDB1'` are identical across Task 2's seed script and Task 3's spec `beforeAll`. The `orders` table/column names (`id`, `sku`, `amount`) match between the seed script and the spec's SQL queries.

## Execution Record

All 4 tasks executed via `superpowers:subagent-driven-development` in worktree `oracle-live-docker-e2e` (branch `worktree-oracle-live-docker-e2e`). Each task passed spec-compliance review and code-quality review with no blocking issues. Task 4 additionally found and fixed a genuine, previously-latent bug in the Schema Explorer connection-switch guard (`frontend/features/config.js`), surfaced only once a real, connectable Oracle container existed behind a pre-existing test's `analytics_dw` stub connection — see commit `f649bb7`.

Final commits:
- `e4c51fc` feat(docker): add live Oracle container to integration compose stack
- `75fbcbe` feat(e2e): seed live Oracle src/tgt schemas in global-setup
- `3eb3737` test(e2e): add live Oracle compare spec
- `f649bb7` fix(frontend): schema explorer connection-switch guard always matched, closing panel instead of switching

Verification: combined live-docker Playwright suite (18/19/20/21/38/41/42/43/45) — 38/38 passed. Full pytest suite — unit baseline (2226 passed, 2 skipped) unchanged; one pre-existing, unrelated failure in `tests/test_settings_routes.py` confirmed byte-identical to pre-branch baseline via `git diff`.
