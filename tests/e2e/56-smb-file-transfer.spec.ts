import { test, expect } from './fixtures';
import { authedContext, deleteJob, deleteFileServerByName } from './api-helpers';

// Covers the smb kind added to File Servers (frontend/partials/tab-file-servers.html)
// and to the file_watcher/file_transfer/multi-file location pickers
// (frontend/partials/tab-launch.html). UI-round-trip coverage only, with no
// live server (matching how the sftp/scp kinds are covered in
// 32-launch-remaining-job-types.spec.ts); live SMB transfers against the
// docker Samba service are in 57-live-smb-scp-file-transfer.spec.ts.
test.describe('56 smb file server and job editor', () => {
  const createdJobNames: string[] = [];
  const createdFileServerNames: string[] = [];

  test.afterEach(async ({ adminToken }) => {
    if (createdJobNames.length === 0 && createdFileServerNames.length === 0) return;
    const ctx = await authedContext(adminToken);
    try {
      while (createdJobNames.length) await deleteJob(ctx, createdJobNames.pop()!);
      while (createdFileServerNames.length) await deleteFileServerByName(ctx, createdFileServerNames.pop()!);
    } finally {
      await ctx.dispose();
    }
  });

  test('File Servers: smb profile round-trips host, username, password', async ({ authedPage }) => {
    const name = `e2e-smb-profile-${Date.now()}`;
    createdFileServerNames.push(name);

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-file-servers"]').click();
    await authedPage.locator('[data-testid="file-server-add-btn"]').click();
    await authedPage.locator('[data-testid="file-server-name-input"]').fill(name);
    await authedPage.locator('[data-testid="file-server-kind-select"]').selectOption('smb');
    await authedPage.locator('[data-testid="file-server-smb-host-input"]').fill('fileserver01');
    await authedPage.locator('[data-testid="file-server-smb-username-input"]').fill('CORP\\svc-atom');
    await authedPage.locator('[data-testid="file-server-smb-password-input"]').fill('s3cret');
    await authedPage.locator('[data-testid="file-server-save-btn"]').click();

    await expect(authedPage.locator(`text=${name}`)).toBeVisible();
  });

  test('file_transfer job: smb source and local destination round-trip', async ({ authedPage, adminToken }) => {
    const jobName = `e2e-file-transfer-smb-${Date.now()}`;
    createdJobNames.push(jobName);
    const profileName = `e2e-smb-profile-2-${Date.now()}`;
    createdFileServerNames.push(profileName);

    const ctx = await authedContext(adminToken);
    try {
      await ctx.post('/api/file-servers', {
        data: { name: profileName, kind: 'smb', host: 'fileserver01', username: 'svc', password: 'x' },
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
    await authedPage.locator('[data-testid="job-modal-ft-source-kind-select"]').selectOption('smb');
    await authedPage.locator('[data-testid="job-modal-ft-source-root-input"]').fill('\\\\fileserver01\\vendor\\inbound');
    await authedPage.locator('[data-testid="job-modal-ft-source-pattern-input"]').fill('SALES_*.csv');
    await authedPage.locator('[data-testid="job-modal-ft-source-credentials-ref-select"]').selectOption(profileName);
    await authedPage.locator('[data-testid="job-modal-ft-dest-root-input"]').fill('/data/staged');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${jobName}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('[data-testid="job-modal-ft-source-kind-select"]')).toHaveValue('smb');
    await expect(authedPage.locator('[data-testid="job-modal-ft-source-root-input"]')).toHaveValue('\\\\fileserver01\\vendor\\inbound');
    await expect(authedPage.locator('[data-testid="job-modal-ft-source-credentials-ref-select"]')).toHaveValue(profileName);
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });
});
