# Multi-File Reconciliation — Phase 9: Live Docker S3/SFTP Playwright Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the multi-file reconciliation feature genuine, live-infrastructure Playwright coverage for S3 and SFTP — not just hand-rolled fake clients (unit tests) or `page.route()` network mocks (existing e2e tests) — by wiring real S3-compatible (MinIO) and real SFTP (`atmoz/sftp`) containers into the existing `docker-compose.integration.yml` + `E2E_LIVE_BACKENDS=1` live-backend convention already used for SQL Server and the SAP BO mock.

**Architecture:** Follow-on to the completed 8-phase multi-file reconciliation roadmap (`docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md`, all 8 phases merged). This phase adds no new product feature — it proves the existing S3/SFTP code paths (`api/services/multi_file_remote.py`'s `build_s3_client`/`build_sftp_client`/`RemoteFileSourceSession`, the job editor's multi-file UI, Phase 8's Preview Mapping credential fields) actually work against real S3/SFTP servers, using the same live-backend gating pattern `tests/e2e/05-adapters.spec.ts` already established for the SAP BO mock.

**A real gap found during research, fixed as a prerequisite (Task 1):** `boto3` and `paramiko` are imported (lazily, inside `build_s3_client`/`build_sftp_client`) by the S3/SFTP multi-file code, but **neither package is in `requirements.txt`, and neither is installed in this repo's venv** (`pip show boto3 paramiko` → "Package(s) not found"). Every S3/SFTP multi-file code path built across Phases 3–8 has only ever been exercised through unit tests with hand-rolled fake clients (`monkeypatch.setattr(".../build_s3_client", ...)`) — meaning the real `import boto3` / `import paramiko` lines have **never actually executed** in this codebase. This phase is the first to run these code paths for real, which is exactly why this gap surfaced only now. Fixed by adding both to `requirements.txt` (Task 1).

**A second real gap found during research, fixed as part of Task 1:** `build_s3_client` passes `endpoint_url` straight to `boto3.client(...)` with no way to select path-style bucket addressing. Real AWS works fine without it (boto3's default), but S3-compatible-but-not-AWS targets (MinIO here; on-prem object stores like Ceph RGW/NetApp StorageGRID in the wild) commonly reject the virtual-hosted-style addressing boto3 defaults to for a custom endpoint, because `bucket-name.127.0.0.1` doesn't resolve to anything on `localhost`. Fixed by forcing path-style addressing whenever `endpoint_url` is set (real AWS never sets `endpoint_url`, so this is a no-op for the existing, already-working real-AWS path) — no new credential field, no frontend change needed, since `endpoint_url` already exists as a Phase-8 preview-credential input.

**Scope decisions made for this phase** (each deliberate, not oversights):
- **New live e2e spec covers S3 and SFTP for multi-file reconciliation only** — not SQL Server or SAP BO (already covered by `05-adapters.spec.ts` / `tests/integration/test_sqlserver_live_reconciliation.py`), and not the Compare tab's ad-hoc Multi-File sub-tab (Phase 7 deliberately keeps that `local`-only; there is nothing remote to test live there).
- **Each new live test exercises ONE remote side, not both** — the S3 test uses `s3` source + `local` target; the SFTP test uses `local` source + `sftp` target. Both remote kinds get proven end-to-end (real discovery, real file read, real pairing, real reconciliation) with half the container seeding work a both-sides-remote test would need, and the underlying mechanism (`RemoteFileSourceSession` dispatching per-side) doesn't care which side is remote — proving it once per kind is sufficient, not a coverage gap.
- **SFTP seed data uses a static bind-mounted fixture directory, not a runtime paramiko upload.** `atmoz/sftp` supports mounting a host directory straight into a configured user's home directory; the container starts with the fixture files already in place, so no seed script, no extra paramiko dependency in `global-setup.ts`, no race with the container finishing its user-setup entrypoint before an upload could succeed.
- **MinIO seeding is a small `boto3` script in `global-setup.ts`** (mirroring the existing `seedSqlServer()` pattern in the same file) — S3 has no equivalent to a bind-mounted directory (objects must be `PUT`, there's no "start with these objects already in the bucket" option), so a real seed step is unavoidable there, unlike SFTP.
- **No Docker-level healthcheck for the new `minio`/`sftp` services.** The official `minio/minio` image is distroless (no shell, no `curl`/`wget` to run a `CMD`-style healthcheck with), and `atmoz/sftp` is a minimal Alpine image without bash's `/dev/tcp` trick. `docker compose up -d --wait` only blocks on services that declare a healthcheck — omitting one here just means these two services are considered "ready" as soon as they're running, not necessarily accepting connections yet. `seedMinio()`'s own retry-with-backoff loop (Task 3) is the real readiness gate for MinIO; the SFTP container's own paramiko connection attempt inside the new e2e test's `beforeAll` similarly retries.

**Tech Stack:** Docker Compose, MinIO (S3-compatible object storage, for local/CI-friendly S3 testing), `atmoz/sftp` (the de facto standard lightweight test SFTP server image), boto3, paramiko, `@playwright/test`.

---

### Task 1: Fix real S3/SFTP dependencies and MinIO addressing-style compatibility

**Files:**
- Modify: `requirements.txt`
- Modify: `api/services/multi_file_remote.py`
- Test: `tests/unit/test_multi_file_remote.py`

**Verified current `build_s3_client`** (`api/services/multi_file_remote.py:41-54`):

```python
def build_s3_client(config_snapshot: dict[str, Any], spec: FileSourceSpec):
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required for multi_file S3 sources") from exc
    creds = resolve_file_source_credentials(config_snapshot, spec)
    return boto3.client(
        "s3",
        aws_access_key_id=creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("aws_secret_access_key"),
        aws_session_token=creds.get("aws_session_token"),
        region_name=creds.get("region_name"),
        endpoint_url=creds.get("endpoint_url"),
    )
```

- [ ] **Step 1: Write the failing test**

Read `tests/unit/test_multi_file_remote.py` first to confirm its current imports and style. Append this test:

```python
def test_build_s3_client_forces_path_style_addressing_for_custom_endpoint(monkeypatch) -> None:
    """A custom endpoint_url means a non-AWS S3-compatible target (MinIO, on-prem
    object storage) -- these commonly reject virtual-hosted-style bucket addressing,
    which boto3 otherwise defaults to whenever endpoint_url is set. Real AWS never
    sets endpoint_url, so this must not fire for the existing real-AWS path."""
    import boto3
    from api.services.multi_file_remote import build_s3_client
    from etl_framework.reconciliation.file_mapping import FileSourceSpec

    captured_kwargs: dict = {}
    real_client = boto3.client

    def _capture(service_name, **kwargs):
        captured_kwargs.update(kwargs)
        return object()  # build_s3_client only returns this; no real network call happens

    monkeypatch.setattr(boto3, "client", _capture)

    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="*.csv", credentials_ref="minio")
    config_snapshot = {
        "file_source_credentials": {
            "minio": {
                "aws_access_key_id": "minioadmin",
                "aws_secret_access_key": "minioadmin",
                "endpoint_url": "http://127.0.0.1:19000",
                "region_name": "us-east-1",
            },
        },
    }

    build_s3_client(config_snapshot, spec)

    assert captured_kwargs["endpoint_url"] == "http://127.0.0.1:19000"
    assert captured_kwargs["config"].s3["addressing_style"] == "path"


def test_build_s3_client_does_not_force_path_style_without_custom_endpoint(monkeypatch) -> None:
    """Real AWS (no endpoint_url set) must keep boto3's default addressing --
    this is the existing, already-working production path; it must not regress."""
    import boto3
    from api.services.multi_file_remote import build_s3_client
    from etl_framework.reconciliation.file_mapping import FileSourceSpec

    captured_kwargs: dict = {}

    def _capture(service_name, **kwargs):
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr(boto3, "client", _capture)

    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="*.csv", credentials_ref="aws_prod")
    config_snapshot = {
        "file_source_credentials": {
            "aws_prod": {"aws_access_key_id": "AKIA...", "aws_secret_access_key": "s3cr3t"},
        },
    }

    build_s3_client(config_snapshot, spec)

    assert captured_kwargs.get("endpoint_url") is None
    assert "config" not in captured_kwargs
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_multi_file_remote.py -k path_style -v`
Expected: FAIL — `boto3` isn't installed yet (`ModuleNotFoundError: No module named 'boto3'`), and even once installed, `build_s3_client` doesn't yet pass a `config=` kwarg for the first test.

- [ ] **Step 3: Add `boto3`/`paramiko` to `requirements.txt` and install them**

In `requirements.txt`, add a new section (read the file first to match its existing section-comment style, e.g. `# API`, `# Dev / test`):

```
# Multi-file reconciliation (S3/SFTP sources)
boto3>=1.34
paramiko>=3.4
```

Then install them into the project's venv:

```powershell
pip install boto3 paramiko
```

- [ ] **Step 4: Fix `build_s3_client`**

Replace `build_s3_client` in `api/services/multi_file_remote.py` with:

```python
def build_s3_client(config_snapshot: dict[str, Any], spec: FileSourceSpec):
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as exc:
        raise RuntimeError("boto3 is required for multi_file S3 sources") from exc
    creds = resolve_file_source_credentials(config_snapshot, spec)
    client_kwargs: dict[str, Any] = {
        "aws_access_key_id": creds.get("aws_access_key_id"),
        "aws_secret_access_key": creds.get("aws_secret_access_key"),
        "aws_session_token": creds.get("aws_session_token"),
        "region_name": creds.get("region_name"),
        "endpoint_url": creds.get("endpoint_url"),
    }
    if creds.get("endpoint_url"):
        # A custom endpoint_url means a non-AWS, S3-compatible target (MinIO,
        # on-prem object storage) -- these commonly reject the virtual-hosted-
        # style bucket addressing boto3 otherwise defaults to whenever a
        # custom endpoint is set. Real AWS never sets endpoint_url, so this
        # never affects the existing real-AWS path.
        client_kwargs["config"] = BotoConfig(s3={"addressing_style": "path"})
    return boto3.client("s3", **client_kwargs)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_multi_file_remote.py -v`
Expected: all PASS (including the two new tests and every pre-existing one in this file — pre-existing tests monkeypatch `build_s3_client` itself, not `boto3.client`, so they're unaffected by this change).

- [ ] **Step 6: Run the broader multi-file test suites to confirm no regression**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py tests/unit/test_multi_file_remote.py tests/unit/test_file_mapping.py tests/unit/test_compare_service_multi_file.py tests/unit/test_api.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add requirements.txt api/services/multi_file_remote.py tests/unit/test_multi_file_remote.py
git commit -m "fix(multi-file): add missing boto3/paramiko deps; force path-style S3 addressing for custom endpoints"
```

---

### Task 2: Add MinIO and SFTP services to `docker-compose.integration.yml`; commit static SFTP seed fixtures

**Files:**
- Modify: `docker-compose.integration.yml`
- Create: `tests/e2e/fixtures/data/sftp_seed/target/financials_east.csv`
- Create: `tests/e2e/fixtures/data/sftp_seed/target/financials_west.csv`

**Verified current file** (`docker-compose.integration.yml`, full content):

```yaml
services:
  sapbo:
    build:
      context: ./docker/sapbo-mock
    image: atom-sapbo-mock:latest
    container_name: atom-sapbo-integration
    environment:
      SAPBO_MOCK_USER: "administrator"
      SAPBO_MOCK_PASSWORD: "Password1"
    ports:
      - "18443:8443"
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - >-
          import ssl, urllib.request; ctx=ssl._create_unverified_context(); urllib.request.urlopen('https://127.0.0.1:8443/health', timeout=2, context=ctx).read()
      interval: 5s
      timeout: 3s
      retries: 20

  sqlserver:
    image: mcr.microsoft.com/mssql/server:2022-latest
    container_name: atom-sqlserver-integration
    environment:
      ACCEPT_EULA: "Y"
      MSSQL_SA_PASSWORD: "Atom_Test_12345!"
    ports:
      - "14333:1433"
    healthcheck:
      test:
        - CMD-SHELL
        - >-
          timeout 1 bash -c '</dev/tcp/127.0.0.1/1433' || exit 1
      interval: 5s
      timeout: 3s
      retries: 30
      start_period: 20s
```

- [ ] **Step 1: Add the `minio` and `sftp` services**

Append two new services after `sqlserver`:

```yaml
  minio:
    image: minio/minio:latest
    container_name: atom-minio-integration
    command: server /data
    environment:
      MINIO_ROOT_USER: "minioadmin"
      MINIO_ROOT_PASSWORD: "minioadmin"
    ports:
      - "19000:9000"
    # No healthcheck: the official image is distroless (no shell/curl to run a
    # CMD healthcheck with). global-setup.ts's seedMinio() does its own
    # retry-with-backoff wait for the API to accept requests before seeding.

  sftp:
    image: atmoz/sftp:latest
    container_name: atom-sftp-integration
    volumes:
      - ./tests/e2e/fixtures/data/sftp_seed/target:/home/e2euser/upload
    command: ["e2euser:e2epass:1001:::upload"]
    ports:
      - "12222:22"
    # No healthcheck: this minimal Alpine image has no bash (so no /dev/tcp
    # trick) and no netcat. The new live e2e spec's own paramiko connection
    # attempt (Task 5) retries, which is a sufficient readiness gate here --
    # unlike MinIO, there's no seed *script* to gate a real readiness check on.
```

`atmoz/sftp`'s `user:pass:uid:::folder` command form creates a chrooted SFTP-only user `e2euser` (password `e2epass`) whose home is `/home/e2euser`, and mounts the bind-mounted `upload` subdirectory read-write for that user — this is the image's documented "provide your own files" pattern. The bind mount is a host-path mount, not a named Docker volume, so `docker compose down -v` never deletes `tests/e2e/fixtures/data/sftp_seed/target/`'s contents.

- [ ] **Step 2: Create the static SFTP seed fixtures**

These must be byte-identical to the existing local multi-file target fixtures (`tests/e2e/fixtures/data/multi_target/financials_east.csv` and `financials_west.csv`), so the new live SFTP e2e test (Task 5) produces the exact same deterministic PASSED/FAILED-per-region outcome the existing local test already relies on. Read those two files first, then create identical copies at:

- `tests/e2e/fixtures/data/sftp_seed/target/financials_east.csv`
- `tests/e2e/fixtures/data/sftp_seed/target/financials_west.csv`

- [ ] **Step 3: Bring the new services up once, by hand, to sanity-check the compose file parses and both containers start**

Run:
```powershell
docker compose -f docker-compose.integration.yml up -d minio sftp
docker compose -f docker-compose.integration.yml ps
```
Expected: both `atom-minio-integration` and `atom-sftp-integration` show `Up`/`running`. Then tear down:
```powershell
docker compose -f docker-compose.integration.yml down -v
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.integration.yml tests/e2e/fixtures/data/sftp_seed/target/financials_east.csv tests/e2e/fixtures/data/sftp_seed/target/financials_west.csv
git commit -m "test(e2e): add MinIO and SFTP services for live multi-file S3/SFTP coverage"
```

---

### Task 3: Seed MinIO in `global-setup.ts`

**Files:**
- Modify: `tests/e2e/global-setup.ts`

**Verified current file** (full content, confirmed by direct read):

```typescript
import { execSync, spawnSync } from 'node:child_process';
import path from 'node:path';
import type { FullConfig } from '@playwright/test';

const REPO_ROOT = path.resolve(__dirname, '../..');

export default async function globalSetup(_config: FullConfig) {
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
  // ... (unchanged, full body already in the file)
}
```

- [ ] **Step 1: Add `seedMinio()` and call it from `globalSetup`**

Change:
```typescript
    execSync('docker compose -f docker-compose.integration.yml up -d --wait', {
      cwd: REPO_ROOT,
      stdio: 'inherit',
      timeout: 180_000,
    });
    seedSqlServer();
  }
}
```
to:
```typescript
    execSync('docker compose -f docker-compose.integration.yml up -d --wait', {
      cwd: REPO_ROOT,
      stdio: 'inherit',
      timeout: 180_000,
    });
    seedSqlServer();
    seedMinio();
  }
}

function seedMinio() {
  // MinIO has no bind-mount equivalent of the SFTP service's static seed
  // directory (Task 2) -- objects must be PUT over the S3 API, so a real
  // seed step is unavoidable here. The retry loop below is this service's
  // actual readiness gate (see docker-compose.integration.yml's comment on
  // why the minio service itself has no Docker healthcheck).
  const fixturesDir = path.join(REPO_ROOT, 'tests', 'e2e', 'fixtures', 'data', 'multi_source').replace(/\\/g, '/');
  const script = `
import time
import boto3
from pathlib import Path

client = boto3.client(
    "s3",
    endpoint_url="http://127.0.0.1:19000",
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
```

This uploads `tests/e2e/fixtures/data/multi_source/sales_east.csv` and `sales_west.csv` into bucket `atom-e2e` under the `source/` prefix — the same fixture content already used by every other multi-file test, now also reachable over real S3.

- [ ] **Step 2: Manually verify the seed step works against a live container**

Run:
```powershell
docker compose -f docker-compose.integration.yml up -d minio
python -c "
import boto3
c = boto3.client('s3', endpoint_url='http://127.0.0.1:19000', aws_access_key_id='minioadmin', aws_secret_access_key='minioadmin', region_name='us-east-1')
print('waiting...')
import time
for _ in range(30):
    try:
        c.list_buckets(); break
    except Exception:
        time.sleep(1)
print('ready')
"
docker compose -f docker-compose.integration.yml down -v
```
Expected: prints `ready` without raising. (This just proves MinIO itself is reachable before wiring the full seed script through `globalSetup`, which Task 5's live test run will exercise for real.)

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/global-setup.ts
git commit -m "test(e2e): seed MinIO bucket with multi-file fixtures in global-setup"
```

---

### Task 4: Extend `triggerRun()` with an optional `configId`

**Files:**
- Modify: `tests/e2e/api-helpers.ts`

**Verified current function** (`api-helpers.ts:149-155`):

```typescript
export async function triggerRun(ctx: APIRequestContext, jobNames: string[]) {
  const resp = await ctx.post('/api/runs', {
    data: { source_env: 'dev', target_env: 'dev', job_names: jobNames },
  });
  if (!resp.ok()) throw new Error(`triggerRun failed: ${resp.status()} ${await resp.text()}`);
  return resp.json(); // { run_id, status }
}
```

A real (non-mocked) saved S3/SFTP `multi_file` job run needs `config_id` on the trigger request, so `RunExecutor` resolves `credentials_ref` against a real `config_snapshot["file_source_credentials"]` (populated from a `SavedConfig`'s `config_data`) instead of an empty one. `RunTrigger.config_id: int | None = None` (`api/schemas.py:211`) already supports this — no backend change needed, just a way to pass it from e2e tests.

- [ ] **Step 1: Add the optional parameter**

Change:
```typescript
export async function triggerRun(ctx: APIRequestContext, jobNames: string[]) {
  const resp = await ctx.post('/api/runs', {
    data: { source_env: 'dev', target_env: 'dev', job_names: jobNames },
  });
  if (!resp.ok()) throw new Error(`triggerRun failed: ${resp.status()} ${await resp.text()}`);
  return resp.json(); // { run_id, status }
}
```
to:
```typescript
export async function triggerRun(ctx: APIRequestContext, jobNames: string[], configId?: number) {
  const data: Record<string, unknown> = { source_env: 'dev', target_env: 'dev', job_names: jobNames };
  if (configId !== undefined) data.config_id = configId;
  const resp = await ctx.post('/api/runs', { data });
  if (!resp.ok()) throw new Error(`triggerRun failed: ${resp.status()} ${await resp.text()}`);
  return resp.json(); // { run_id, status }
}
```

This is backward compatible — every existing call site omits the third argument, so `data.config_id` is never added for them, producing the exact same request body as before.

- [ ] **Step 2: Run the existing e2e suite's helper-consuming specs to confirm no regression**

Run: `npx playwright test tests/e2e/17-multi-file-reconciliation.spec.ts tests/e2e/02-launch-jobs.spec.ts --reporter=list`
Expected: all PASS (these are the specs that call `triggerRun` today, without the new argument).

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/api-helpers.ts
git commit -m "test(e2e): let triggerRun optionally pass config_id for live-credential runs"
```

---

### Task 5: Live e2e spec — real S3 and real SFTP multi-file reconciliation

**Files:**
- Create: `tests/e2e/17b-multi-file-live-remote.spec.ts`

This spec is gated behind `E2E_LIVE_BACKENDS=1`, exactly like `05-adapters.spec.ts`. It proves two things per remote kind, end-to-end, against real containers: (1) the job editor's Preview Mapping (Phase 8) actually connects and pairs real files, and (2) a saved job actually executes for real (discovery → read → reconcile → persist), producing the exact same deterministic PASSED/FAILED-per-region result every other multi-file test in this repo relies on.

- [ ] **Step 1: Write the spec**

```typescript
// tests/e2e/17b-multi-file-live-remote.spec.ts
import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig, deleteJob, triggerRun, waitForTerminal } from './api-helpers';
import path from 'node:path';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');
const MINIO_ENDPOINT = 'http://127.0.0.1:19000';
const MINIO_BUCKET = 'atom-e2e';
const SFTP_HOST = '127.0.0.1';
const SFTP_PORT = '12222';
const SFTP_USER = 'e2euser';
const SFTP_PASS = 'e2epass';

test.describe('17b multi-file reconciliation - live S3 (MinIO)', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml minio service)');

  let jobName: string;
  let configId: number;

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      if (jobName) await deleteJob(ctx, jobName);
      if (configId) await deleteConfig(ctx, configId);
    } finally {
      await ctx.dispose();
    }
  });

  test('creates, previews, saves, and runs a multi_file job with a real S3 source through the job editor UI', async ({ authedPage, adminToken }) => {
    jobName = `e2e-live-s3-${Date.now()}`;

    // The SAVED job's real run (later in this test) resolves `credentials_ref`
    // against config_snapshot["file_source_credentials"] -- which comes from
    // this SavedConfig's config_data, not from anything typed into the job
    // editor's preview-only credential fields (those are ephemeral, Phase 8).
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-live-s3-cfg-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        file_source_credentials: {
          minio_live: {
            aws_access_key_id: 'minioadmin',
            aws_secret_access_key: 'minioadmin',
            endpoint_url: MINIO_ENDPOINT,
            region_name: 'us-east-1',
          },
        },
      });
      configId = cfg.id;
    } finally {
      await ctx.dispose();
    }

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeVisible();

    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(jobName);
    await authedPage.locator('[data-testid="job-modal-source-mode-select"]').selectOption('multi_file');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="job-modal-mf-match-on-input"]').fill('region');
    await authedPage.locator('[data-testid="job-modal-mf-source-kind-select"]').selectOption('s3');
    await authedPage.locator('[data-testid="job-modal-mf-source-root-input"]').fill(`s3://${MINIO_BUCKET}/source`);
    await authedPage.locator('[data-testid="job-modal-mf-source-pattern-input"]').fill('sales_{region}.csv');
    // Real job execution later resolves creds through config_snapshot via this
    // credentials_ref (see the SavedConfig created above) -- NOT through the
    // preview-only fields filled in below, which are a separate, ephemeral path.
    // This plain input has no dedicated data-testid (confirmed by reading
    // frontend/partials/tab-launch.html directly) -- select by its x-model.
    await authedPage.locator('input[x-model="jobModal.mf_source_credentials_ref"]').fill('minio_live');

    await authedPage.locator('[data-testid="job-modal-mf-target-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_target'));
    await authedPage.locator('[data-testid="job-modal-mf-target-pattern-input"]').fill('financials_{region}.csv');

    // Preview against the REAL MinIO container -- uses Phase 8's preview-only
    // credential fields (never persisted with the job). region_name/endpoint_url
    // also have no dedicated data-testid -- select by x-model, same as above.
    await authedPage.locator('[data-testid="job-modal-mf-source-s3-access-key-input"]').fill('minioadmin');
    await authedPage.locator('[data-testid="job-modal-mf-source-s3-secret-key-input"]').fill('minioadmin');
    await authedPage.locator('input[x-model="jobModal.mf_source_preview_creds.region_name"]').fill('us-east-1');
    await authedPage.locator('input[x-model="jobModal.mf_source_preview_creds.endpoint_url"]').fill(MINIO_ENDPOINT);

    await authedPage.locator('[data-testid="job-modal-mf-preview-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal-mf-preview-result"]')).toContainText('2 pair(s) matched', { timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="job-modal-mf-preview-pair"]')).toHaveCount(2);

    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    const runCtx = await authedContext(adminToken);
    try {
      const { run_id } = await triggerRun(runCtx, [jobName], configId);
      const status = await waitForTerminal(runCtx, run_id, 60_000);
      // Same deterministic fixtures as every other multi-file test: region=east
      // byte-identical (PASSED), region=west amount changed (FAILED) -- so the
      // aggregate job status is always FAILED, this time via a REAL S3 read.
      expect(status.status).toBe('FAILED');
    } finally {
      await runCtx.dispose();
    }
  });
});

test.describe('17b multi-file reconciliation - live SFTP', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml sftp service)');

  let jobName: string;
  let configId: number;

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      if (jobName) await deleteJob(ctx, jobName);
      if (configId) await deleteConfig(ctx, configId);
    } finally {
      await ctx.dispose();
    }
  });

  test('creates, previews, saves, and runs a multi_file job with a real SFTP target through the job editor UI', async ({ authedPage, adminToken }) => {
    jobName = `e2e-live-sftp-${Date.now()}`;

    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-live-sftp-cfg-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        file_source_credentials: {
          sftp_live: { host: SFTP_HOST, port: Number(SFTP_PORT), username: SFTP_USER, password: SFTP_PASS },
        },
      });
      configId = cfg.id;
    } finally {
      await ctx.dispose();
    }

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeVisible();

    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(jobName);
    await authedPage.locator('[data-testid="job-modal-source-mode-select"]').selectOption('multi_file');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="job-modal-mf-match-on-input"]').fill('region');
    await authedPage.locator('[data-testid="job-modal-mf-source-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_source'));
    await authedPage.locator('[data-testid="job-modal-mf-source-pattern-input"]').fill('sales_{region}.csv');

    await authedPage.locator('[data-testid="job-modal-mf-target-kind-select"]').selectOption('sftp');
    await authedPage.locator('[data-testid="job-modal-mf-target-root-input"]').fill('/upload');
    await authedPage.locator('[data-testid="job-modal-mf-target-pattern-input"]').fill('financials_{region}.csv');
    await authedPage.locator('input[x-model="jobModal.mf_target_credentials_ref"]').fill('sftp_live');

    // Preview against the REAL SFTP container.
    await authedPage.locator('[data-testid="job-modal-mf-target-sftp-host-input"]').fill(SFTP_HOST);
    await authedPage.locator('input[x-model="jobModal.mf_target_preview_creds.port"]').fill(SFTP_PORT);
    await authedPage.locator('input[x-model="jobModal.mf_target_preview_creds.username"]').fill(SFTP_USER);
    await authedPage.locator('[data-testid="job-modal-mf-target-sftp-password-input"]').fill(SFTP_PASS);

    await authedPage.locator('[data-testid="job-modal-mf-preview-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal-mf-preview-result"]')).toContainText('2 pair(s) matched', { timeout: 20_000 });

    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    const runCtx = await authedContext(adminToken);
    try {
      const { run_id } = await triggerRun(runCtx, [jobName], configId);
      const status = await waitForTerminal(runCtx, run_id, 60_000);
      expect(status.status).toBe('FAILED'); // same deterministic east/west fixtures, this time via a REAL SFTP read
    } finally {
      await runCtx.dispose();
    }
  });
});
```

**Selectors already verified against the real file** (grepped `frontend/partials/tab-launch.html` directly while writing this plan, not assumed): `job-modal-mf-source-kind-select`, `job-modal-mf-target-kind-select`, `job-modal-mf-source-s3-access-key-input`, `job-modal-mf-source-s3-secret-key-input`, `job-modal-mf-target-sftp-host-input`, `job-modal-mf-target-sftp-password-input`, `job-modal-mf-preview-btn`, `job-modal-mf-preview-result`, `job-modal-mf-preview-pair` all exist as real `data-testid`s. `mf_source_credentials_ref`, `mf_target_credentials_ref`, `mf_source_preview_creds.region_name`, `mf_source_preview_creds.endpoint_url`, `mf_target_preview_creds.port`, `mf_target_preview_creds.username` genuinely have NO `data-testid` (confirmed absent) — the spec above correctly selects those by `x-model` attribute instead. Still worth a final re-check against the file's current state immediately before running Step 2, in case something shifted since this plan was written.

- [ ] **Step 2: Bring up the full live stack and run the new spec for real**

```powershell
docker compose -f docker-compose.integration.yml up -d --wait
$env:E2E_LIVE_BACKENDS = "1"
npx playwright test tests/e2e/17b-multi-file-live-remote.spec.ts --reporter=list
```

Expected: both tests PASS, with real network calls to `127.0.0.1:19000` (MinIO) and `127.0.0.1:12222` (SFTP) — confirm this by checking the console output of `global-setup.ts`'s `seedMinio()` log line, and by tailing `docker compose -f docker-compose.integration.yml logs sftp` for a real SFTP login line during the test run.

If a selector doesn't match reality (see the note in Step 1), fix it against the actual DOM (Playwright's trace viewer / `--reporter=list` failure output shows the exact locator that failed) and re-run — do not guess a second time.

- [ ] **Step 3: Tear down and confirm the rest of the live-backend suite still passes**

```powershell
npx playwright test tests/e2e/05-adapters.spec.ts tests/e2e/17-multi-file-reconciliation.spec.ts tests/e2e/17b-multi-file-live-remote.spec.ts --reporter=list
docker compose -f docker-compose.integration.yml down -v
```
Expected: all PASS, confirming the new services/seed step don't interfere with the existing live-backend (`05-adapters.spec.ts`) or local-fixture (`17-multi-file-reconciliation.spec.ts`) coverage.

- [ ] **Step 4: Run the full e2e suite WITHOUT the live flag, to confirm nothing regressed for the default (non-Docker) path**

```powershell
npx playwright test --reporter=list
```
Expected: all PASS, and the two new `17b` tests show as `skipped` (not run), confirming the default `npx playwright test` experience (no Docker required) is unchanged.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/17b-multi-file-live-remote.spec.ts
git commit -m "test(e2e): add live S3 (MinIO) and SFTP coverage for multi-file reconciliation"
```

---

### Task 6: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the new live-backend services and how to run them**

Find the "Run the SAP BO mock integration test with Docker Compose" block (README.md, in the testing-commands section near the "End-to-end (Playwright) tests" heading). Add a new paragraph right after the existing `$env:E2E_LIVE_BACKENDS = "1"; npx playwright test` line, documenting:
- `docker-compose.integration.yml` now also has `minio` (S3-compatible storage, API on `127.0.0.1:19000`, credentials `minioadmin`/`minioadmin`) and `sftp` (`atmoz/sftp`, `127.0.0.1:12222`, user `e2euser`/`e2epass`) services, both covered by the same `E2E_LIVE_BACKENDS=1` flag and `docker compose -f docker-compose.integration.yml up -d --wait`/`down -v` lifecycle as the existing SQL Server/SAP BO services.
- `tests/e2e/17b-multi-file-live-remote.spec.ts` is the new spec exercising real multi-file S3/SFTP job creation, Preview Mapping, and execution end-to-end.
- Update the "Current limitations" section of `docs/multi_file_reconciliation.md` if it still says S3/SFTP multi-file paths are only unit-tested with fake clients — it no longer is (this phase adds live coverage), though production still lacks a connections-registry admin UI (unchanged, out of scope here).

- [ ] **Step 2: Commit**

```bash
git add README.md docs/multi_file_reconciliation.md
git commit -m "docs: document live S3/SFTP Docker Compose services and Playwright coverage"
```

---

## Self-review notes

- **Spec coverage:** Task 1 fixes two real, previously-undetected gaps (missing runtime deps, MinIO addressing incompatibility) that would otherwise make every later task fail against real infrastructure — found and fixed *before* writing the containers/tests that would have surfaced them at a much less convenient point. Task 2-3 stand up real S3 (MinIO) and real SFTP (`atmoz/sftp`) containers, seeded with the exact same fixture content every other multi-file test already uses (so PASSED/FAILED-per-region assertions stay consistent project-wide). Task 4 is a minimal, backward-compatible helper extension. Task 5 is the actual deliverable: two live, UI-driven Playwright tests (S3 source, SFTP target) proving Preview Mapping and real job execution both work against genuine remote infrastructure, run for real (not just claimed) as part of this plan's own steps. Task 6 documents it.
- **Why boto3/paramiko were never caught missing until now:** every existing test for S3/SFTP code paths (`test_multi_file_remote.py`, `test_multi_file_jobs.py`, `test_api.py`'s preview tests) monkeypatches `build_s3_client`/`build_sftp_client` themselves, so the `import boto3`/`import paramiko` lines inside those functions never actually executed in CI or locally until this phase's live tests call them for real. This plan calls this out explicitly rather than silently fixing it, since it's a legitimate production gap (the feature has shipped across 5 phases without its own runtime dependencies declared) worth the user knowing about.
- **Scope decisions stated up front:** one remote side per test (not both), static SFTP seed vs. scripted MinIO seed, no Docker healthchecks on the two new services (with the specific reason: distroless/minimal images), Compare-tab and SQL Server/SAP BO left untouched — each explained in the header.
- **Verification is real, not delegated on faith:** Task 2 Step 3, Task 3 Step 2, and Task 5 Steps 2-4 each require actually running `docker compose` and `npx playwright test` against live containers before committing — mirroring this project's established discipline (e.g. Phase 2's automated-pairing fix, Phase 7's operator-precedence fix) of empirically proving a fix/feature works rather than trusting that it should.
- **Type/name consistency:** `seedMinio()`, `MINIO_ENDPOINT`/`MINIO_BUCKET`/`SFTP_HOST`/`SFTP_PORT`/`SFTP_USER`/`SFTP_PASS`, `minio_live`/`sftp_live` (the `credentials_ref` values used consistently between the SavedConfig's `file_source_credentials` keys and the job's `credentials_ref` fields) are spelled identically at every definition and call site across Tasks 2-5.
