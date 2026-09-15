// tests/e2e/17b-multi-file-live-remote.spec.ts
import path from 'node:path';
import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig, deleteFileServerByName, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');
const MINIO_ENDPOINT = 'http://127.0.0.1:29000';
const MINIO_BUCKET = 'atom-e2e';
const SFTP_HOST = '127.0.0.1';
const SFTP_PORT = '12222';
const SFTP_USER = 'e2euser';
const SFTP_PASS = 'e2epass';

test.describe('17b multi-file reconciliation - live S3 (MinIO)', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml minio service)');

  let jobName: string;
  let configId: number;
  let profileName: string;

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      // Job must go first: the profile delete 409s while a saved job still
      // references it by credentials_ref.
      if (jobName) await deleteJob(ctx, jobName);
      if (configId) await deleteConfig(ctx, configId);
      if (profileName) await deleteFileServerByName(ctx, profileName);
    } finally {
      await ctx.dispose();
    }
  });

  test('creates, previews, saves, and runs a multi_file job with a real S3 source through the job editor UI', async ({ authedPage, adminToken }) => {
    jobName = `e2e-live-s3-${Date.now()}`;
    profileName = `e2e-live-minio-${Date.now()}`;

    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-live-s3-cfg-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
      });
      configId = cfg.id;

      const profileResp = await ctx.post('/api/file-servers', {
        data: {
          name: profileName,
          kind: 's3',
          aws_access_key_id: 'minioadmin',
          aws_secret_access_key: 'minioadmin',
          endpoint_url: MINIO_ENDPOINT,
          region_name: 'us-east-1',
        },
      });
      if (!profileResp.ok()) throw new Error(`file-server profile creation failed: ${profileResp.status()} ${await profileResp.text()}`);
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

    await authedPage.locator('[data-testid="job-modal-mf-target-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_target'));
    await authedPage.locator('[data-testid="job-modal-mf-target-pattern-input"]').fill('financials_{region}.csv');

    const sourceSelect = authedPage.locator('[data-testid="job-modal-mf-source-credentials-ref-select"]');
    await expect(sourceSelect.locator('option', { hasText: profileName })).toHaveCount(1);
    await sourceSelect.selectOption(profileName);

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
  let profileName: string;

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      // Job must go first: the profile delete 409s while a saved job still
      // references it by credentials_ref.
      if (jobName) await deleteJob(ctx, jobName);
      if (configId) await deleteConfig(ctx, configId);
      if (profileName) await deleteFileServerByName(ctx, profileName);
    } finally {
      await ctx.dispose();
    }
  });

  test('creates, previews, saves, and runs a multi_file job with a real SFTP target through the job editor UI', async ({ authedPage, adminToken }) => {
    jobName = `e2e-live-sftp-${Date.now()}`;
    profileName = `e2e-live-sftp-target-${Date.now()}`;

    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-live-sftp-cfg-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
      });
      configId = cfg.id;

      const profileResp = await ctx.post('/api/file-servers', {
        data: {
          name: profileName,
          kind: 'sftp',
          host: SFTP_HOST,
          port: Number(SFTP_PORT),
          username: SFTP_USER,
          auth_method: 'password',
          password: SFTP_PASS,
        },
      });
      if (!profileResp.ok()) throw new Error(`file-server profile creation failed: ${profileResp.status()} ${await profileResp.text()}`);
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

    const targetSelect = authedPage.locator('[data-testid="job-modal-mf-target-credentials-ref-select"]');
    await expect(targetSelect.locator('option', { hasText: profileName })).toHaveCount(1);
    await targetSelect.selectOption(profileName);

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
