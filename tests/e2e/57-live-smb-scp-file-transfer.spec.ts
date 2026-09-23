import * as fs from 'fs';
import * as path from 'path';
import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { authedContext, deleteJob, deleteFileServerByName, waitForTerminal } from './api-helpers';

// Live file_transfer round trips through the Web UI against real servers from
// docker-compose.integration.yml: the `sftp` service (atmoz/sftp) reached as an
// SCP profile, and the `smb` service (Samba) reached as \\127.0.0.1\share on
// port 1445 -- a Windows host's own SMB server owns 445, so the smb profile
// carries an alternative port (net use /TCPPORT, Windows 11 24H2+). Profiles,
// jobs, and runs are all created and launched from the UI; files are checked
// on the host through each service's bind-mounted seed directory.
const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');
const LOCAL_SOURCE = path.join(FIXTURE_DIR, 'multi_source');
const SFTP_HOST_DIR = path.join(FIXTURE_DIR, 'sftp_seed', 'target'); // mounted at /upload
const SMB_HOST_DIR = path.join(FIXTURE_DIR, 'smb_seed'); // mounted as \\127.0.0.1\share
const EXPECTED_FILES = ['sales_east.csv', 'sales_west.csv'];

async function createProfileInUi(page: Page, fill: () => Promise<void>, name: string) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-file-servers"]').click();
  await page.locator('[data-testid="file-server-add-btn"]').click();
  await page.locator('[data-testid="file-server-name-input"]').fill(name);
  await fill();
  await page.locator('[data-testid="file-server-save-btn"]').click();
  await expect(page.locator('.font-semibold', { hasText: name })).toBeVisible();
}

function profileCard(page: Page, name: string) {
  return page.locator('[data-testid^="file-server-"].card', { has: page.locator('.font-semibold', { hasText: name }) });
}

type Location = { kind: string; root: string; pattern?: string; profile?: string };

async function createTransferJobInUi(page: Page, jobName: string, source: Location, dest: Location) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-jobs"]').click();
  await page.locator('[data-testid="job-new-btn"]').click();
  await page.locator('[data-testid="job-modal-name-input"]').fill(jobName);
  await page.locator('[data-testid="job-modal-type-select"]').selectOption('file_transfer');
  await page.locator('[data-testid="job-modal-tab-settings"]').click();

  await page.locator('[data-testid="job-modal-ft-source-kind-select"]').selectOption(source.kind);
  await page.locator('[data-testid="job-modal-ft-source-root-input"]').fill(source.root);
  await page.locator('[data-testid="job-modal-ft-source-pattern-input"]').fill(source.pattern ?? '*');
  if (source.profile) await page.locator('[data-testid="job-modal-ft-source-credentials-ref-select"]').selectOption(source.profile);

  await page.locator('[data-testid="job-modal-ft-dest-kind-select"]').selectOption(dest.kind);
  await page.locator('[data-testid="job-modal-ft-dest-root-input"]').fill(dest.root);
  if (dest.profile) await page.locator('[data-testid="job-modal-ft-dest-credentials-ref-select"]').selectOption(dest.profile);

  await expect(page.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
  await page.locator('[data-testid="job-modal-save-btn"]').click();
  await expect(page.locator('[data-testid="job-modal"]')).toBeHidden();
}

// Selects the job on the Jobs tab and clicks Run Tests; returns the run id
// from the UI's own POST /api/runs response.
async function runJobInUi(page: Page, jobName: string): Promise<string> {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-jobs"]').click();
  await page.locator('[data-testid="job-search-input"]').fill(jobName);
  await page.locator(`[data-testid="job-row-${jobName}-checkbox"]`).click();
  const [response] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/api/runs') && r.request().method() === 'POST'),
    page.locator('[data-testid="run-tests-btn"]').click(),
  ]);
  expect(response.ok()).toBeTruthy();
  await expect(page.locator('.toast-title')).toContainText('Run started');
  return (await response.json()).run_id as string;
}

async function expectRunPassedInUi(page: Page, adminToken: string, runId: string, destBasename: string) {
  const ctx = await authedContext(adminToken);
  try {
    const status = await waitForTerminal(ctx, runId, 90_000);
    expect(String(status.status).toUpperCase(), JSON.stringify(status)).toBe('PASSED');
  } finally {
    await ctx.dispose();
  }
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-history"]').click();
  await page.locator('[data-testid="history-subtab-runs"]').click();
  await page.locator(`[data-testid="history-run-row-${runId}"]`).click();
  const summary = page.locator('[data-testid="run-result-file-transfer-summary"]');
  await expect(summary).toContainText('Copied 2');
  await expect(summary).toContainText(destBasename);
}

function expectSameFiles(dir: string) {
  expect(fs.readdirSync(dir).sort()).toEqual(EXPECTED_FILES);
  for (const f of EXPECTED_FILES) {
    expect(fs.readFileSync(path.join(dir, f)).equals(fs.readFileSync(path.join(LOCAL_SOURCE, f))), f).toBeTruthy();
  }
}

function cleanupFactory() {
  const jobs: string[] = [];
  const profiles: string[] = [];
  const dirs: string[] = [];
  return {
    jobs, profiles, dirs,
    async run(adminToken: string) {
      const ctx = await authedContext(adminToken);
      try {
        // Jobs first: a profile still referenced by a saved job 409s on delete.
        while (jobs.length) await deleteJob(ctx, jobs.pop()!);
        while (profiles.length) await deleteFileServerByName(ctx, profiles.pop()!);
      } finally {
        await ctx.dispose();
      }
      while (dirs.length) fs.rmSync(dirs.pop()!, { recursive: true, force: true });
    },
  };
}

test.describe('57 live SCP file_transfer (docker sftp service)', () => {
  test.describe.configure({ mode: 'serial' });
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml sftp service)');

  const stamp = Date.now();
  const profileName = `e2e-live-scp-${stamp}`;
  const remoteDir = `scp_${stamp}`;
  const cleanup = cleanupFactory();

  test.afterAll(async ({ adminToken }) => cleanup.run(adminToken));

  test('SCP profile: create, test connection, and pin the host key from the UI', async ({ authedPage }) => {
    cleanup.profiles.push(profileName);
    await createProfileInUi(authedPage, async () => {
      await authedPage.locator('[data-testid="file-server-kind-select"]').selectOption('scp');
      await authedPage.locator('[data-testid="file-server-host-input"]').fill('127.0.0.1');
      await authedPage.locator('[data-testid="file-server-port-input"]').fill('12222');
      await authedPage.locator('[data-testid="file-server-username-input"]').fill('e2euser');
      await authedPage.locator('[data-testid="file-server-password-input"]').fill('e2epass');
    }, profileName);

    const card = profileCard(authedPage, profileName);
    await expect(card).toContainText('scp · 127.0.0.1');
    await card.locator('[data-testid^="file-server-test-"]').click();
    await expect(card).toContainText('Server presented fingerprint');
    await card.locator('[data-testid^="file-server-accept-fingerprint-"]').click();
    await expect(card).toContainText('Connection OK');
    // A re-test after pinning is a clean OK, not another unpinned prompt.
    await card.locator('[data-testid^="file-server-test-"]').click();
    await expect(card).toContainText('Connection OK');
    await expect(card.locator('[data-testid^="file-server-accept-fingerprint-"]')).toHaveCount(0);
  });

  test('local -> SCP upload lands the files on the server', async ({ authedPage, adminToken }) => {
    const jobName = `e2e-live-scp-upload-${stamp}`;
    cleanup.jobs.push(jobName);
    cleanup.dirs.push(path.join(SFTP_HOST_DIR, remoteDir));

    await createTransferJobInUi(
      authedPage, jobName,
      { kind: 'local', root: LOCAL_SOURCE, pattern: 'sales_*.csv' },
      { kind: 'scp', root: `/upload/${remoteDir}`, profile: profileName },
    );
    const runId = await runJobInUi(authedPage, jobName);
    await expectRunPassedInUi(authedPage, adminToken, runId, remoteDir);
    expectSameFiles(path.join(SFTP_HOST_DIR, remoteDir));
  });

  test('SCP -> local download brings the uploaded files back byte-equal', async ({ authedPage, adminToken }) => {
    const jobName = `e2e-live-scp-download-${stamp}`;
    const outDir = path.join(FIXTURE_DIR, `scp_download_${stamp}`);
    cleanup.jobs.push(jobName);
    cleanup.dirs.push(outDir);

    await createTransferJobInUi(
      authedPage, jobName,
      { kind: 'scp', root: `/upload/${remoteDir}`, pattern: 'sales_*.csv', profile: profileName },
      { kind: 'local', root: outDir },
    );
    const runId = await runJobInUi(authedPage, jobName);
    await expectRunPassedInUi(authedPage, adminToken, runId, path.basename(outDir));
    expectSameFiles(outDir);
  });
});

test.describe('57 live SMB file_transfer (docker smb service)', () => {
  test.describe.configure({ mode: 'serial' });
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml smb service)');
  test.skip(process.platform !== 'win32', 'smb transfers use Windows net use -- the atom server must run on Windows');

  const stamp = Date.now();
  const profileName = `e2e-live-smb-${stamp}`;
  const shareDir = `smb_out_${stamp}`;
  const cleanup = cleanupFactory();

  test.afterAll(async ({ adminToken }) => cleanup.run(adminToken));

  test('SMB profile: create with an alternative port and test connection from the UI', async ({ authedPage }) => {
    cleanup.profiles.push(profileName);
    await createProfileInUi(authedPage, async () => {
      await authedPage.locator('[data-testid="file-server-kind-select"]').selectOption('smb');
      // Switching to smb swaps the sftp default port for SMB's standard 445.
      await expect(authedPage.locator('[data-testid="file-server-smb-port-input"]')).toHaveValue('445');
      await authedPage.locator('[data-testid="file-server-smb-host-input"]').fill('127.0.0.1');
      await authedPage.locator('[data-testid="file-server-smb-port-input"]').fill('1445');
      await authedPage.locator('[data-testid="file-server-smb-username-input"]').fill('e2euser');
      await authedPage.locator('[data-testid="file-server-smb-password-input"]').fill('e2epass');
    }, profileName);

    const card = profileCard(authedPage, profileName);
    await expect(card).toContainText('smb · 127.0.0.1');
    await card.locator('[data-testid^="file-server-test-"]').click();
    await expect(card).toContainText('Connection OK', { timeout: 30_000 });
  });

  test('SMB -> local download copies the seeded share files', async ({ authedPage, adminToken }) => {
    const jobName = `e2e-live-smb-download-${stamp}`;
    const outDir = path.join(FIXTURE_DIR, `smb_download_${stamp}`);
    cleanup.jobs.push(jobName);
    cleanup.dirs.push(outDir);

    await createTransferJobInUi(
      authedPage, jobName,
      { kind: 'smb', root: '\\\\127.0.0.1\\share\\inbound', pattern: 'sales_*.csv', profile: profileName },
      { kind: 'local', root: outDir },
    );
    const runId = await runJobInUi(authedPage, jobName);
    await expectRunPassedInUi(authedPage, adminToken, runId, path.basename(outDir));
    expectSameFiles(outDir);
  });

  test('local -> SMB upload lands the files on the share', async ({ authedPage, adminToken }) => {
    const jobName = `e2e-live-smb-upload-${stamp}`;
    cleanup.jobs.push(jobName);
    cleanup.dirs.push(path.join(SMB_HOST_DIR, shareDir));

    await createTransferJobInUi(
      authedPage, jobName,
      { kind: 'local', root: LOCAL_SOURCE, pattern: 'sales_*.csv' },
      { kind: 'smb', root: `\\\\127.0.0.1\\share\\${shareDir}`, profile: profileName },
    );
    const runId = await runJobInUi(authedPage, jobName);
    await expectRunPassedInUi(authedPage, adminToken, runId, shareDir);
    expectSameFiles(path.join(SMB_HOST_DIR, shareDir));
  });
});
