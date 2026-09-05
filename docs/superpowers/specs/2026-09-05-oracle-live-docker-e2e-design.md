# Oracle Live-Docker E2E & AWS Live-Suite Verification Design Specification

**Date:** 2026-09-05
**Status:** Approved
**Topic:** Live Docker Oracle Reconciliation Compare + Verification of Existing Live-Docker AWS Playwright Suite

---

## 1. Overview

Oracle has database-engine support in `etl_framework` (`db_type: "oracle"`, `oracledb` SQLAlchemy driver in `etl_framework/db/engine.py`) and is exercised by unit tests (`tests/test_oracle_integration.py`), but has no live Docker container and no live Playwright E2E coverage — unlike SQL Server, which has a live container in `docker-compose.integration.yml`, a seed step in `tests/e2e/global-setup.ts`, and a live compare spec (`tests/e2e/08c-compare-sql.spec.ts`).

AWS (S3, Glue, Athena, Airflow) already has full live-docker coverage via LocalStack and `tests/e2e/18-21`, `38`, `41-43`. This effort does not add new AWS surface — it brings up the full stack and verifies that suite still passes.

Scope of this spec:
1. Add a live Oracle container to `docker-compose.integration.yml`.
2. Seed two Oracle schemas (src/tgt) with deterministic diff data.
3. Add a live Oracle SQL-compare Playwright spec, mirroring the SQL Server one.
4. Run the full live-docker Playwright suite (Oracle + existing AWS specs) and fix any real failures found.

---

## 2. Docker Integration Architecture

### 2.1 New Service (`docker-compose.integration.yml`)

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

- `gvenzl/oracle-free` is a free, unlicensed-pull community image (no Oracle account / license-acceptance wall, unlike `container-registry.oracle.com`), widely used in CI, thin-driver (`oracledb`) compatible.
- Port `1521:1521` matches the port already hardcoded in `tests/e2e/41-live-docker-config-schema-browse.spec.ts`'s `analytics_dw` stub connection (that spec creates a *config* pointing at Oracle but never had a live container behind it — schema browse assertions there are scoped to the SQL Server side only).
- `APP_USER=e2e_src` has the image auto-create the first schema/user in the default pluggable database `FREEPDB1` on first boot. The second schema (`e2e_tgt`) is created by the seed step (2.2), the same way SQL Server's two databases are both created by `seedSqlServer()` rather than by compose.
- Healthcheck uses the image's built-in `healthcheck.sh`. Retry budget (40 × 10s = ~400s) is larger than SQL Server's (30 × 5s = 150s) because Oracle's first boot (running `catalog.sql`/datafile creation) is slower, commonly 1-3 minutes even on the "free" image.
- `docker-compose.integration.yml`'s `up -d --wait` (in `global-setup.ts`) already blocks on all service healthchecks, so no separate wait logic is needed — but the `execSync` call's own `timeout: 180_000` in `global-setup.ts` must be raised (to `420_000`) to not kill the compose command itself before Oracle finishes booting.

### 2.2 Seed Step (`tests/e2e/global-setup.ts`)

New `seedOracle()`, called from `globalSetup()` alongside `seedSqlServer()` / `seedMinio()`, gated the same way (`E2E_LIVE_BACKENDS === '1'`):

- Connects as `SYSTEM` (password `ORACLE_PASSWORD`) to `FREEPDB1` via `python-oracledb` (thin mode — no Oracle Instant Client needed, consistent with `etl_framework/db/engine.py`'s `oracle+oracledb://` URL).
- `CREATE USER e2e_tgt IDENTIFIED BY "Oracle_Test_12345"` + `GRANT CONNECT, RESOURCE, UNLIMITED TABLESPACE TO e2e_tgt` if not already present (idempotent, same `IF ... IS NULL`-style guard pattern as `seedSqlServer()`'s `IF DB_ID(...) IS NULL`).
- In both `e2e_src` (the image-created app user) and `e2e_tgt`, drop-if-exists and recreate an `orders` table, then seed rows with the same deterministic mismatch shape as SQL Server's seed: one row with a differing `amount`, one row missing from each side. Oracle has no `dbo` schema concept — the table is simply owned by the connecting user, so the compare spec queries `SELECT id, sku, amount FROM orders` unqualified.
- Because there are two separate database *users* rather than two databases on one login (SQL Server's model), the compare Configs differ by `db_user`/`db_password` (both pointing at the same `db_host`/`db_port`/`db_name=FREEPDB1`) rather than by `db_name`.

### 2.3 Prerequisite: `oracledb` Package

Not currently installed in the dev/test environment (only declared as `pyproject.toml`'s `oracle` extra, unused elsewhere in this repo — same situation as Netezza's `nzpy`, which is already installed ad hoc but not tracked in `requirements.txt`). Running the live Oracle suite requires `pip install oracledb` (or `pip install -e .[oracle]`) first. This is a one-time local/CI environment setup step, not a code change — `requirements.txt` stays as-is, matching the existing (undocumented) Netezza precedent.

---

## 3. Test Scenarios & Playwright E2E Coverage

### 3.1 Oracle Compare Spec (`tests/e2e/45-oracle-compare.spec.ts`)

Direct structural mirror of `tests/e2e/08c-compare-sql.spec.ts`:

- `test.skip(!liveBackends, ...)` gate on `E2E_LIVE_BACKENDS === '1'`.
- `beforeAll`: create two Configs via `api-helpers.ts`'s `createConfig()`, `db_type: 'oracle'`, `db_driver: 'oracledb'`, `db_host: '127.0.0.1'`, `db_port: 1521`, `db_name: 'FREEPDB1'`, differing `db_user`/`db_password` (`e2e_src` / `e2e_tgt`).
- `afterAll`: delete both Configs.
- Test: real compare produces the seeded deterministic diff count (`1 differ`), same assertion style as the SQL Server spec's `.compare-chip.chip-regressed` check.
- Test: diff row expansion renders real source/target values (mirrors the SQL Server spec's page-error-free row-expansion check).
- Test: malformed SQL surfaces a backend `ERROR` badge.

No changes to `41-live-docker-config-schema-browse.spec.ts` are in scope — its Oracle stub config becomes a real, connectable container as a side effect, but that spec's own assertions are unaffected (it already treats the Oracle option as just a dropdown entry, not something it queries schema from).

### 3.2 AWS Verification (no new specs)

Bring up the full `docker-compose.integration.yml` stack (now including `oracle`) and run the existing live-docker suite (`18-aws-s3-tab-live`, `19-aws-glue-tab`, `20-aws-athena-tab`, `21-aws-airflow-tab`, `38-live-docker-aws-compare`, `41-live-docker-config-schema-browse`, `42-live-docker-matrix-reconciliation`, `43-live-docker-matrix-save-as-job`) with `E2E_LIVE_BACKENDS=1`. Any real failures found get fixed as part of this work (root cause, not skip/xfail).

---

## 4. Verification Criteria

- `docker compose -f docker-compose.integration.yml up -d --wait` succeeds and Oracle's healthcheck passes.
- `45-oracle-compare.spec.ts` passes against the live Oracle container.
- The existing live-docker AWS specs (listed in 3.2) all pass unchanged (or with genuine bug fixes, not test loosening).
- Unit/integration/transform pytest suites remain green (`tests/test_oracle_integration.py`'s mocked tests are unaffected by the new live container).
