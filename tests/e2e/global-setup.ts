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
