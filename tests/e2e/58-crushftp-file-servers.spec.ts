import { test, expect } from './fixtures';
import { authedContext, deleteJob, deleteFileServerByName } from './api-helpers';

// Covers the crushftp kind added to File Servers (frontend/partials/tab-file-servers.html)
// and to the file_watcher/file_transfer/multi-file location pickers
// (frontend/partials/tab-launch.html). UI round-trip coverage only: Test
// Connection responses are mocked, as they are for the sftp fingerprint flow.
// Transfers against a real CrushFTP container are in
// tests/integration/test_crushftp_live.py.
test.describe('58 crushftp file server and job editor', () => {
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

  async function openAddModal(page: import('@playwright/test').Page, name: string) {
    await page.goto('/');
    await page.locator('[data-testid="nav-tab-file-servers"]').click();
    await page.locator('[data-testid="file-server-add-btn"]').click();
    await page.locator('[data-testid="file-server-name-input"]').fill(name);
    await page.locator('[data-testid="file-server-kind-select"]').selectOption('crushftp');
  }

  test('HTTPS profile with username and password: saved, URL shown, password masked on edit', async ({ authedPage }) => {
    const name = `e2e-crush-pw-${Date.now()}`;
    createdFileServerNames.push(name);

    await openAddModal(authedPage, name);
    // https + password are the defaults for a new CrushFTP profile
    await expect(authedPage.locator('[data-testid="file-server-crushftp-protocol-select"]')).toHaveValue('https');
    await expect(authedPage.locator('[data-testid="file-server-crushftp-auth-select"]')).toHaveValue('password');
    await authedPage.locator('[data-testid="file-server-crushftp-url-input"]').fill('https://files.internal.example');
    await authedPage.locator('[data-testid="file-server-crushftp-username-input"]').fill('svc-atom');
    await authedPage.locator('[data-testid="file-server-crushftp-password-input"]').fill('hunter2');
    await authedPage.locator('[data-testid="file-server-save-btn"]').click();

    const row = authedPage.locator('[data-testid^="file-server-"]', { hasText: name });
    await expect(row).toBeVisible();
    await expect(row).toContainText('https://files.internal.example');

    await row.getByRole('button', { name: 'Edit' }).click();
    await expect(authedPage.locator('[data-testid="file-server-crushftp-password-input"]')).toHaveValue('********');
    // re-saving the mask must be accepted (the stored secret counts as present)
    await authedPage.locator('[data-testid="file-server-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="file-server-save-btn"]')).toBeHidden();
  });

  test('HTTPS profile with an API key header: header kept in clear, key masked, mocked test OK', async ({ authedPage }) => {
    const name = `e2e-crush-key-${Date.now()}`;
    createdFileServerNames.push(name);

    await openAddModal(authedPage, name);
    await authedPage.locator('[data-testid="file-server-crushftp-url-input"]').fill('https://files.cloud.example');
    await authedPage.locator('[data-testid="file-server-crushftp-auth-select"]').selectOption('api_key');
    await authedPage.locator('[data-testid="file-server-crushftp-header-input"]').fill('X-Api-Key');
    await authedPage.locator('[data-testid="file-server-crushftp-key-input"]').fill('k3y-value');
    await authedPage.locator('[data-testid="file-server-save-btn"]').click();

    const row = authedPage.locator('[data-testid^="file-server-"]', { hasText: name });
    await expect(row).toBeVisible();

    await authedPage.route('**/api/file-servers/*/test', (route) =>
      route.fulfill({ json: { status: 'ok', presented_fingerprint: null, pinned_fingerprint: null, message: null } }),
    );
    await row.getByRole('button', { name: 'Test Connection' }).click();
    await expect(row).toContainText('Connection OK');

    await row.getByRole('button', { name: 'Edit' }).click();
    await expect(authedPage.locator('[data-testid="file-server-crushftp-header-input"]')).toHaveValue('X-Api-Key');
    await expect(authedPage.locator('[data-testid="file-server-crushftp-key-input"]')).toHaveValue('********');
  });

  test('HTTPS profile without a base URL is rejected by the server and stays unsaved', async ({ authedPage }) => {
    const name = `e2e-crush-bad-${Date.now()}`;

    await openAddModal(authedPage, name);
    await authedPage.locator('[data-testid="file-server-crushftp-username-input"]').fill('svc-atom');
    await authedPage.locator('[data-testid="file-server-crushftp-password-input"]').fill('hunter2');
    await authedPage.locator('[data-testid="file-server-save-btn"]').click();

    await expect(authedPage.getByText('endpoint_url', { exact: false }).first()).toBeVisible();
    // the modal stays open and nothing was created
    await expect(authedPage.locator('[data-testid="file-server-save-btn"]')).toBeVisible();
  });

  test('SFTP-protocol profile uses the fingerprint review step', async ({ authedPage }) => {
    const name = `e2e-crush-sftp-${Date.now()}`;
    createdFileServerNames.push(name);

    await openAddModal(authedPage, name);
    await authedPage.locator('[data-testid="file-server-crushftp-protocol-select"]').selectOption('sftp');
    await expect(authedPage.locator('[data-testid="file-server-crushftp-sftp-port-input"]')).toHaveValue('22');
    await authedPage.locator('[data-testid="file-server-crushftp-sftp-host-input"]').fill('files.internal.example');
    await authedPage.locator('[data-testid="file-server-crushftp-sftp-port-input"]').fill('2222');
    await authedPage.locator('[data-testid="file-server-crushftp-sftp-username-input"]').fill('svc-atom');
    await authedPage.locator('[data-testid="file-server-crushftp-sftp-password-input"]').fill('hunter2');
    await authedPage.locator('[data-testid="file-server-save-btn"]').click();

    const row = authedPage.locator('[data-testid^="file-server-"]', { hasText: name });
    await expect(row).toBeVisible();
    await expect(row).toContainText('files.internal.example');

    await authedPage.route('**/api/file-servers/*/test', (route) =>
      route.fulfill({ json: { status: 'unpinned', presented_fingerprint: 'abc123', pinned_fingerprint: null, message: null } }),
    );
    await row.getByRole('button', { name: 'Test Connection' }).click();
    await expect(row.getByRole('button', { name: 'Accept & Pin' })).toBeVisible();
  });

  test('file_transfer job: crushftp source offers only CrushFTP profiles and round-trips', async ({ authedPage, adminToken }) => {
    const stamp = Date.now();
    const jobName = `e2e-file-transfer-crush-${stamp}`;
    createdJobNames.push(jobName);
    const crushProfile = `e2e-crush-profile-${stamp}`;
    const sftpProfile = `e2e-crush-other-sftp-${stamp}`;
    createdFileServerNames.push(crushProfile, sftpProfile);

    const ctx = await authedContext(adminToken);
    try {
      const created = await ctx.post('/api/file-servers', {
        data: {
          name: crushProfile, kind: 'crushftp', protocol: 'https', endpoint_url: 'https://files.cloud.example',
          auth_method: 'api_key', api_key_header: 'X-Api-Key', api_key: 'k',
        },
      });
      expect(created.status()).toBe(201);
      await ctx.post('/api/file-servers', { data: { name: sftpProfile, kind: 'sftp', host: 'h', username: 'u', password: 'x' } });
    } finally {
      await ctx.dispose();
    }

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(jobName);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('file_transfer');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-ft-source-kind-select"]').selectOption('crushftp');

    const profileSelect = authedPage.locator('[data-testid="job-modal-ft-source-credentials-ref-select"]');
    await expect(profileSelect.locator('option', { hasText: crushProfile })).toHaveCount(1);
    await expect(profileSelect.locator('option', { hasText: sftpProfile })).toHaveCount(0);

    await authedPage.locator('[data-testid="job-modal-ft-source-root-input"]').fill('/data/inbound');
    await authedPage.locator('[data-testid="job-modal-ft-source-pattern-input"]').fill('SALES_*.csv');
    await profileSelect.selectOption(crushProfile);
    await authedPage.locator('[data-testid="job-modal-ft-dest-root-input"]').fill('/data/staged');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${jobName}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('[data-testid="job-modal-ft-source-kind-select"]')).toHaveValue('crushftp');
    await expect(authedPage.locator('[data-testid="job-modal-ft-source-root-input"]')).toHaveValue('/data/inbound');
    await expect(profileSelect).toHaveValue(crushProfile);
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });
});
