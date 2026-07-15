import { execSync, spawnSync } from 'node:child_process';
import path from 'node:path';
import type { FullConfig } from '@playwright/test';

const REPO_ROOT = path.resolve(__dirname, '../..');

export default async function globalSetup(_config: FullConfig) {
  // Live backends (SQL Server + SAP BO mock), gated — mirrors the existing
  // RUN_LIVE_SQLSERVER_TESTS / RUN_LIVE_SAPBO_TESTS pytest convention.
  // (The throwaway sqlite DB path itself is created in playwright.config.ts,
  // not here — see that file's comment on webServer.env evaluation timing.)
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
  // so the databases/table shape match what that suite already validates. The ODBC driver
  // name is overridable via LIVE_SQLSERVER_ODBC_DRIVER (same env var that pytest suite uses),
  // defaulting to "ODBC Driver 17 for SQL Server" to match what the app itself always sends
  // for configs created through the UI (frontend/app.js:_configDataFromModal) — but installed
  // driver versions vary by machine, so this must not be hardcoded.
  //
  // TrustServerCertificate=yes below is required for this seed connection specifically:
  // ODBC Driver 18 defaults to Encrypt=yes + strict certificate validation (a behavior
  // change from Driver 17), which rejects the mssql container's self-signed cert without
  // it. Safe here because this only ever talks to an ephemeral, local Docker container
  // for tests. NOTE: etl_framework/db/engine.py (the app's own SQL Server connection
  // builder, used by real Compare/SQL runs) does NOT set this — a real app-level gap for
  // any on-prem SQL Server + Driver 18 combination, not just this test seed script. Task
  // 14 (SQL Compare live tests) may hit the exact same TLS rejection when it runs real
  // comparisons against this same container; if so, that's a product fix (likely a new
  // EnvironmentConfig field, not something to silently bolt on here) — investigate then,
  // don't speculatively fix etl_framework/db/engine.py from this test-infra task.
  const driver = process.env.LIVE_SQLSERVER_ODBC_DRIVER || 'ODBC Driver 17 for SQL Server';
  const script = `
import pyodbc
DRIVER = ${JSON.stringify(driver)}
conn = pyodbc.connect(
    f"DRIVER={{{DRIVER}}};SERVER=127.0.0.1,14333;DATABASE=master;"
    "UID=sa;PWD=Atom_Test_12345!;Connect Timeout=5;TrustServerCertificate=yes;",
    autocommit=True,
)
cur = conn.cursor()
for db in ("atom_e2e_src", "atom_e2e_tgt"):
    cur.execute(f"IF DB_ID('{db}') IS NULL CREATE DATABASE {db}")
conn.close()

def seed(db, rows):
    c = pyodbc.connect(
        f"DRIVER={{{DRIVER}}};SERVER=127.0.0.1,14333;DATABASE={db};"
        "UID=sa;PWD=Atom_Test_12345!;Connect Timeout=5;TrustServerCertificate=yes;",
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
