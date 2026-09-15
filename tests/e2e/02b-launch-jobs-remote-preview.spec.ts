// tests/e2e/02b-launch-jobs-remote-preview.spec.ts
//
// Every other e2e spec in this suite (17-multi-file-reconciliation.spec.ts,
// 08g-compare-multi-file.spec.ts) exercises the real backend against real local
// fixture files. There is no real S3 bucket or SFTP server available in this test
// environment, so this spec instead mocks POST /api/jobs/preview-file-mapping via
// page.route() -- asserting on the OUTGOING REQUEST BODY (proving the frontend
// sends the real File Server profile's name as credentials_ref, selected via the
// job-modal-mf-{source,target}-credentials-ref-select dropdown) and returning a
// canned response (proving the result renders). This is a deliberate, one-time
// deviation from this suite's usual convention, scoped to exactly this file -- the
// backend unit tests already committed in Task 2 (hand-rolled fake S3/SFTP clients)
// are what actually prove discovery/pairing works end-to-end against s3/sftp; this
// e2e test only proves the UI wiring (button enablement, dropdown population from
// GET /api/file-servers, request payload shape, result rendering).
import { test, expect } from './fixtures';
import { authedContext } from './api-helpers';

test.describe('02b launch jobs / remote preview credentials', () => {
  test('s3 kind: preview is enabled, sends the selected profile name as credentials_ref, and renders the result', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    const profileName = `preview_s3_${Date.now()}`;
    let profileId: number | undefined;
    try {
      const createResp = await ctx.post('/api/file-servers', {
        data: {
          name: profileName,
          kind: 's3',
          aws_access_key_id: 'AKIA_TEST',
          aws_secret_access_key: 'test-secret',
        },
      });
      expect(createResp.ok()).toBeTruthy();
      profileId = (await createResp.json()).id as number;

      let capturedBody: any = null;
      await authedPage.route('**/api/jobs/preview-file-mapping', async (route) => {
        capturedBody = route.request().postDataJSON();
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            pairs_total: 1,
            pairs: [{ key: { region: 'east' }, source_files: ['sales_east.csv'], target_files: ['financials_east.csv'], similarity_score: null }],
            unmatched_sources: [],
            unmatched_targets: [],
          }),
        });
      });

      await authedPage.goto('/');
      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
      await authedPage.locator('[data-testid="job-new-btn"]').click();
      await expect(authedPage.locator('[data-testid="job-modal"]')).toBeVisible();

      await authedPage.locator('[data-testid="job-modal-name-input"]').fill(`e2e-remote-preview-${Date.now()}`);
      // source_mode lives on the Basic tab (the modal's default tab); mf_* fields
      // live on Settings -- select source_mode first, then switch tabs, matching
      // the established pattern in 17-multi-file-reconciliation.spec.ts.
      await authedPage.locator('[data-testid="job-modal-source-mode-select"]').selectOption('multi_file');
      await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();

      await authedPage.locator('[data-testid="job-modal-mf-source-kind-select"]').selectOption('s3');
      await authedPage.locator('[data-testid="job-modal-mf-source-root-input"]').fill('s3://finance/source');
      await authedPage.locator('[data-testid="job-modal-mf-source-pattern-input"]').fill('sales_{region}.csv');
      const sourceSelect = authedPage.locator('[data-testid="job-modal-mf-source-credentials-ref-select"]');
      await expect(sourceSelect.locator('option', { hasText: profileName })).toHaveCount(1);
      await sourceSelect.selectOption(profileName);

      await authedPage.locator('[data-testid="job-modal-mf-target-root-input"]').fill('/baseline');
      await authedPage.locator('[data-testid="job-modal-mf-target-pattern-input"]').fill('financials_{region}.csv');

      // Preview must be enabled even though source kind is 's3', not 'local'.
      await expect(authedPage.locator('[data-testid="job-modal-mf-preview-btn"]')).toBeEnabled();
      await authedPage.locator('[data-testid="job-modal-mf-preview-btn"]').click();

      await expect(authedPage.locator('[data-testid="job-modal-mf-preview-result"]')).toContainText('1 pair(s) matched');
      await expect(authedPage.locator('[data-testid="job-modal-mf-preview-pair"]')).toHaveCount(1);

      expect(capturedBody.file_mapping.source.kind).toBe('s3');
      expect(capturedBody.file_mapping.source.credentials_ref).toBe(profileName);
      expect(capturedBody.file_source_credentials).toBeUndefined();
    } finally {
      if (profileId !== undefined) await ctx.delete(`/api/file-servers/${profileId}`).catch(() => {});
      await ctx.dispose();
    }
  });

  test('sftp kind: profile dropdown is populated and the selected profile name is sent as credentials_ref', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    const profileName = `preview_sftp_target_${Date.now()}`;
    let profileId: number | undefined;
    try {
      const createResp = await ctx.post('/api/file-servers', {
        data: {
          name: profileName,
          kind: 'sftp',
          host: 'sftp.internal',
          port: 22,
          username: 'sftpuser',
          auth_method: 'password',
          password: 'sftp-secret',
        },
      });
      expect(createResp.ok()).toBeTruthy();
      profileId = (await createResp.json()).id as number;

      let capturedBody: any = null;
      await authedPage.route('**/api/jobs/preview-file-mapping', async (route) => {
        capturedBody = route.request().postDataJSON();
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ pairs_total: 0, pairs: [], unmatched_sources: [], unmatched_targets: [] }),
        });
      });

      await authedPage.goto('/');
      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
      await authedPage.locator('[data-testid="job-new-btn"]').click();
      await authedPage.locator('[data-testid="job-modal-name-input"]').fill(`e2e-remote-preview-sftp-${Date.now()}`);
      await authedPage.locator('[data-testid="job-modal-source-mode-select"]').selectOption('multi_file');
      await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();

      await authedPage.locator('[data-testid="job-modal-mf-target-kind-select"]').selectOption('sftp');
      await authedPage.locator('[data-testid="job-modal-mf-source-root-input"]').fill('/spool');
      await authedPage.locator('[data-testid="job-modal-mf-source-pattern-input"]').fill('sales_{region}.csv');
      await authedPage.locator('[data-testid="job-modal-mf-target-root-input"]').fill('/baseline');
      await authedPage.locator('[data-testid="job-modal-mf-target-pattern-input"]').fill('financials_{region}.csv');

      const targetSelect = authedPage.locator('[data-testid="job-modal-mf-target-credentials-ref-select"]');
      await expect(targetSelect).toBeVisible();
      await expect(targetSelect.locator('option', { hasText: profileName })).toHaveCount(1);
      await targetSelect.selectOption(profileName);

      await authedPage.locator('[data-testid="job-modal-mf-preview-btn"]').click();
      await expect(authedPage.locator('[data-testid="job-modal-mf-preview-result"]')).toContainText('0 pair(s) matched');

      expect(capturedBody.file_mapping.target.kind).toBe('sftp');
      expect(capturedBody.file_mapping.target.credentials_ref).toBe(profileName);
      expect(capturedBody.file_source_credentials).toBeUndefined();
    } finally {
      if (profileId !== undefined) await ctx.delete(`/api/file-servers/${profileId}`).catch(() => {});
      await ctx.dispose();
    }
  });
});
