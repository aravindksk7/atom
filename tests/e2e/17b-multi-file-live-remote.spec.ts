// tests/e2e/17b-multi-file-live-remote.spec.ts
import path from 'node:path';
import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

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
    await authedPage.locator('input[x-model="jobModal.mf_source_credentials_ref"]').fill('minio_live');

    await authedPage.locator('[data-testid="job-modal-mf-target-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_target'));
    await authedPage.locator('[data-testid="job-modal-mf-target-pattern-input"]').fill('financials_{region}.csv');

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
      expect(status.status).toBe('FAILED');
    } finally {
      await runCtx.dispose();
    }
  });
});
