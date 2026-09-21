import * as fs from 'fs';
import * as path from 'path';
import { test, expect } from './fixtures';
import {
  authedContext, deleteJob, deleteFileServerByName, triggerRun, waitForTerminal,
} from './api-helpers';

// file_transfer job type: (1) the Launch job modal round-trips source/destination/
// on_exists/recursive/preserve_structure through save and edit, including a File Server
// profile picked from the dropdown; (2) a real local -> local run copies the multi_source
// fixtures, refuses to overwrite by default, and the copies reconcile clean against the
// originals (proving the staged files are byte-equal and usable as a reconciliation input).
const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');

test.describe('55 file_transfer job', () => {
  const createdJobNames: string[] = [];
  const createdFileServerNames: string[] = [];
  const createdDirs: string[] = [];

  test.afterEach(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      // Jobs first: a profile still referenced by a saved job 409s on delete.
      while (createdJobNames.length) await deleteJob(ctx, createdJobNames.pop()!);
      while (createdFileServerNames.length) await deleteFileServerByName(ctx, createdFileServerNames.pop()!);
    } finally {
      await ctx.dispose();
    }
    while (createdDirs.length) fs.rmSync(createdDirs.pop()!, { recursive: true, force: true });
  });

  test('modal round-trips source, destination profile, on_exists and subfolder options', async ({ authedPage, adminToken }) => {
    const jobName = `e2e-file-transfer-${Date.now()}`;
    createdJobNames.push(jobName);
    const profileName = `e2e-ft-sftp-${Date.now()}`;
    createdFileServerNames.push(profileName);

    const ctx = await authedContext(adminToken);
    try {
      await ctx.post('/api/file-servers', {
        data: { name: profileName, kind: 'sftp', host: 'sftp.example.internal', port: 22, username: 'svc', auth_method: 'password', password: 'x' },
      });
    } finally {
      await ctx.dispose();
    }

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(jobName);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('file_transfer');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();

    await authedPage.locator('[data-testid="job-modal-ft-source-root-input"]').fill('/data/inbound');
    await authedPage.locator('[data-testid="job-modal-ft-source-pattern-input"]').fill('SALES_*.csv');
    // Save stays disabled until the destination is complete.
    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeDisabled();

    await authedPage.locator('[data-testid="job-modal-ft-dest-kind-select"]').selectOption('sftp');
    await authedPage.locator('[data-testid="job-modal-ft-dest-root-input"]').fill('/staged');
    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeDisabled();
    await authedPage.locator('[data-testid="job-modal-ft-dest-credentials-ref-select"]').selectOption(profileName);
    await authedPage.locator('[data-testid="job-modal-ft-on-exists-select"]').selectOption('skip');
    await authedPage.locator('[data-testid="job-modal-ft-recursive-checkbox"]').check();
    await authedPage.locator('[data-testid="job-modal-ft-preserve-structure-checkbox"]').check();

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${jobName}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('[data-testid="job-modal-ft-source-root-input"]')).toHaveValue('/data/inbound');
    await expect(authedPage.locator('[data-testid="job-modal-ft-source-pattern-input"]')).toHaveValue('SALES_*.csv');
    await expect(authedPage.locator('[data-testid="job-modal-ft-dest-kind-select"]')).toHaveValue('sftp');
    await expect(authedPage.locator('[data-testid="job-modal-ft-dest-root-input"]')).toHaveValue('/staged');
    await expect(authedPage.locator('[data-testid="job-modal-ft-dest-credentials-ref-select"]')).toHaveValue(profileName);
    await expect(authedPage.locator('[data-testid="job-modal-ft-on-exists-select"]')).toHaveValue('skip');
    await expect(authedPage.locator('[data-testid="job-modal-ft-recursive-checkbox"]')).toBeChecked();
    await expect(authedPage.locator('[data-testid="job-modal-ft-preserve-structure-checkbox"]')).toBeChecked();
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });

  test('runs local to local, refuses to overwrite by default, and the copies reconcile clean', async ({ adminToken }) => {
    const stamp = Date.now();
    const outDir = path.join(FIXTURE_DIR, `transfer_out_${stamp}`);
    createdDirs.push(outDir);
    const transferName = `e2e-file-transfer-run-${stamp}`;
    const reconName = `e2e-file-transfer-recon-${stamp}`;
    createdJobNames.push(reconName, transferName);

    const ctx = await authedContext(adminToken);
    try {
      const created = await ctx.post('/api/jobs', {
        data: {
          name: transferName,
          job_type: 'file_transfer',
          params: {
            source: { kind: 'local', root: path.join(FIXTURE_DIR, 'multi_source'), pattern: 'sales_{region}.csv' },
            destination: { kind: 'local', root: outDir },
          },
        },
      });
      expect(created.ok()).toBeTruthy();

      const first = await waitForTerminal(ctx, (await triggerRun(ctx, [transferName])).run_id);
      expect(String(first.status).toUpperCase()).toBe('PASSED');
      expect(fs.readdirSync(outDir).sort()).toEqual(['sales_east.csv', 'sales_west.csv']);

      // Default on_exists=fail: a second run must not silently replace staged inputs.
      const second = await waitForTerminal(ctx, (await triggerRun(ctx, [transferName])).run_id);
      expect(String(second.status).toUpperCase()).toBe('FAILED');

      // The staged copies are a valid reconciliation input: identical to the originals.
      const recon = await ctx.post('/api/jobs', {
        data: {
          name: reconName,
          job_type: 'reconciliation',
          key_columns: ['id'],
          params: {
            source_mode: 'multi_file',
            file_mapping: {
              strategy: 'explicit',
              match_on: ['region'],
              source: { kind: 'local', root: outDir, pattern: 'sales_{region}.csv' },
              target: { kind: 'local', root: path.join(FIXTURE_DIR, 'multi_source'), pattern: 'sales_{region}.csv' },
            },
          },
        },
      });
      expect(recon.ok()).toBeTruthy();
      const reconRun = await waitForTerminal(ctx, (await triggerRun(ctx, [reconName])).run_id);
      expect(String(reconRun.status).toUpperCase()).toBe('PASSED');
    } finally {
      await ctx.dispose();
    }
  });
});
