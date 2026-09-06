import { execSync, spawnSync } from 'node:child_process';
import { mkdirSync, rmSync } from 'node:fs';
import path from 'node:path';
import type { FullConfig } from '@playwright/test';

const REPO_ROOT = path.resolve(__dirname, '../..');

export default async function globalSetup(_config: FullConfig) {
  // Truncate the previous run's comparison-backend audit trail (written by
  // tests/e2e/fixtures.ts when E2E_COMPARE_BACKEND is set) so a stale file from
  // an earlier run can't be mistaken for evidence that this run used the override.
  mkdirSync(path.join(REPO_ROOT, 'test-results'), { recursive: true });
  rmSync(path.join(REPO_ROOT, 'test-results', 'compare-backend-override.log'), { force: true });

  // Live backends (SQL Server + SAP BO mock), gated — mirrors the existing
  // RUN_LIVE_SQLSERVER_TESTS / RUN_LIVE_SAPBO_TESTS pytest convention.
  // (The throwaway sqlite DB path itself is created in playwright.config.ts,
  // not here — see that file's comment on webServer.env evaluation timing.)
  if (process.env.E2E_LIVE_BACKENDS === '1') {
    console.log('[global-setup] starting docker-compose.integration.yml services...');
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
    seedGlueAthena();
    waitForAirflow();
  }
}

function seedMinio() {
  // MinIO has no bind-mount equivalent of the SFTP service's static seed
  // directory -- objects must be PUT over the S3 API, so a real seed step is
  // unavoidable here. The retry loop below is this service's actual
  // readiness gate (the minio service itself has no Docker healthcheck --
  // see docker-compose.integration.yml's comment on why: the official image
  // is distroless, no shell/curl to run a CMD healthcheck with).
  const fixturesDir = path.join(REPO_ROOT, 'tests', 'e2e', 'fixtures', 'data', 'multi_source').replace(/\\/g, '/');
  const script = `
import time
import boto3
from pathlib import Path

client = boto3.client(
    "s3",
    endpoint_url="http://127.0.0.1:29000",
    aws_access_key_id="minioadmin",
    aws_secret_access_key="minioadmin",
    region_name="us-east-1",
)

for attempt in range(30):
    try:
        client.list_buckets()
        break
    except Exception:
        time.sleep(1)
else:
    raise RuntimeError("MinIO did not become ready within 30s")

bucket = "atom-e2e"
existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
if bucket not in existing:
    client.create_bucket(Bucket=bucket)

fixtures_dir = Path(${JSON.stringify(fixturesDir)})
for f in sorted(fixtures_dir.glob("*.csv")):
    client.upload_file(str(f), bucket, f"source/{f.name}")
print("seeded")
`;
  const result = spawnSync('python', ['-c', script], { encoding: 'utf-8' });
  if (result.status !== 0) {
    throw new Error(`MinIO seed failed:\n${result.stdout}\n${result.stderr}`);
  }
  console.log('[global-setup] MinIO seeded:', result.stdout.trim());
}

function seedGlueAthena() {
  // LocalStack backs s3+glue+athena on one endpoint (docker-compose.integration.yml's
  // `localstack` service, SERVICES=s3,glue,athena). Real AWS would need a Glue
  // crawler or DDL to register a table; here we just PUT a CSV and register its
  // location directly via create_table, matching how a real data lake table
  // looks once already cataloged (which is all _read_glue_table_rows in
  // compare_service.py or a direct AWS-tab Glue call ever needs to see).
  const script = `
import time
import boto3

endpoint = "http://127.0.0.1:4566"
creds = dict(aws_access_key_id="test", aws_secret_access_key="test", region_name="us-east-1")
s3 = boto3.client("s3", endpoint_url=endpoint, **creds)
glue = boto3.client("glue", endpoint_url=endpoint, **creds)

for attempt in range(30):
    try:
        s3.list_buckets()
        break
    except Exception:
        time.sleep(1)
else:
    raise RuntimeError("LocalStack did not become ready within 30s")

bucket = "atom-e2e-glue"
existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
if bucket not in existing:
    s3.create_bucket(Bucket=bucket)

csv_body = b"id,sku,amount\\n1,A100,25.50\\n2,B200,50.00\\n3,C300,75.00\\n"
s3.put_object(Bucket=bucket, Key="raw/orders/part-0.csv", Body=csv_body)

database = "e2e_raw"
try:
    glue.get_database(Name=database)
except glue.exceptions.EntityNotFoundException:
    glue.create_database(DatabaseInput={"Name": database})

table_input = {
    "Name": "orders",
    "TableType": "EXTERNAL_TABLE",
    "StorageDescriptor": {
        "Columns": [
            {"Name": "id", "Type": "int"},
            {"Name": "sku", "Type": "string"},
            {"Name": "amount", "Type": "double"},
        ],
        "Location": f"s3://{bucket}/raw/orders/",
        "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
        "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
        "SerdeInfo": {
            "SerializationLibrary": "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe",
            "Parameters": {"field.delim": ",", "skip.header.line.count": "1"},
        },
    },
}
try:
    glue.delete_table(DatabaseName=database, Name="orders")
except glue.exceptions.EntityNotFoundException:
    pass
glue.create_table(DatabaseName=database, TableInput=table_input)

print("seeded")
`;
  const result = spawnSync('python', ['-c', script], { encoding: 'utf-8' });
  if (result.status !== 0) {
    throw new Error(`Glue/Athena seed failed:\n${result.stdout}\n${result.stderr}`);
  }
  console.log('[global-setup] Glue/Athena seeded:', result.stdout.trim());
}

function waitForAirflow() {
  // `docker compose up -d --wait` already blocks on the airflow service's own
  // Docker healthcheck (docker-compose.integration.yml), so this is a belt-and-
  // suspenders check that the seeded DAG is actually visible through the REST
  // API (not just that the process is listening) before any spec tries to use it.
  const script = `
import time
import requests

for attempt in range(30):
    try:
        resp = requests.get("http://127.0.0.1:18085/api/v1/dags", auth=("admin", "admin"), timeout=3)
        if resp.status_code == 200 and any(d["dag_id"] == "etl_orders_daily" for d in resp.json().get("dags", [])):
            break
    except Exception:
        pass
    time.sleep(2)
else:
    raise RuntimeError("Airflow did not report the seeded etl_orders_daily DAG within 60s")
print("airflow ready")
`;
  const result = spawnSync('python', ['-c', script], { encoding: 'utf-8' });
  if (result.status !== 0) {
    throw new Error(`Airflow readiness check failed:\n${result.stdout}\n${result.stderr}`);
  }
  console.log('[global-setup] Airflow ready:', result.stdout.trim());
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
