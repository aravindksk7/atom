// tests/e2e/02b-launch-jobs-remote-preview.spec.ts
//
// Every other e2e spec in this suite (17-multi-file-reconciliation.spec.ts,
// 08g-compare-multi-file.spec.ts) exercises the real backend against real local
// fixture files. There is no real S3 bucket or SFTP server available in this test
// environment, so this spec instead mocks POST /api/jobs/preview-file-mapping via
// page.route() -- asserting on the OUTGOING REQUEST BODY (proving the frontend
// correctly builds file_source_credentials with the __preview_source__/
// __preview_target__ sentinel keys) and returning a canned response (proving the
// result renders). This is a deliberate, one-time deviation from this suite's usual
// convention, scoped to exactly this file -- the backend unit tests already
// committed in Task 2 (hand-rolled fake S3/SFTP clients) are what actually prove
// discovery/pairing works end-to-end against s3/sftp; this e2e test only proves the
// UI wiring (button enablement, credential field visibility, request payload shape,
// result rendering).
import { test, expect } from './fixtures';

test.describe('02b launch jobs / remote preview credentials', () => {
  test('s3 kind: preview is enabled, sends inline credentials, and renders the result', async ({ authedPage }) => {
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
    await authedPage.locator('[data-testid="job-modal-mf-source-s3-access-key-input"]').fill('AKIA_TEST');
    await authedPage.locator('[data-testid="job-modal-mf-source-s3-secret-key-input"]').fill('test-secret');

    await authedPage.locator('[data-testid="job-modal-mf-target-root-input"]').fill('/baseline');
    await authedPage.locator('[data-testid="job-modal-mf-target-pattern-input"]').fill('financials_{region}.csv');

    // Preview must be enabled even though source kind is 's3', not 'local'.
    await expect(authedPage.locator('[data-testid="job-modal-mf-preview-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-mf-preview-btn"]').click();

    await expect(authedPage.locator('[data-testid="job-modal-mf-preview-result"]')).toContainText('1 pair(s) matched');
    await expect(authedPage.locator('[data-testid="job-modal-mf-preview-pair"]')).toHaveCount(1);

    expect(capturedBody.file_mapping.source.kind).toBe('s3');
    expect(capturedBody.file_mapping.source.credentials_ref).toBe('__preview_source__');
    expect(capturedBody.file_source_credentials.__preview_source__).toEqual({
      aws_access_key_id: 'AKIA_TEST',
      aws_secret_access_key: 'test-secret',
    });
  });

  test('sftp kind: preview credential fields appear and are sent', async ({ authedPage }) => {
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

    await expect(authedPage.locator('[data-testid="job-modal-mf-target-sftp-host-input"]')).toBeVisible();
    await authedPage.locator('[data-testid="job-modal-mf-target-sftp-host-input"]').fill('sftp.internal');
    await authedPage.locator('[data-testid="job-modal-mf-target-sftp-password-input"]').fill('sftp-secret');

    await authedPage.locator('[data-testid="job-modal-mf-preview-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal-mf-preview-result"]')).toContainText('0 pair(s) matched');

    expect(capturedBody.file_mapping.target.kind).toBe('sftp');
    expect(capturedBody.file_mapping.target.credentials_ref).toBe('__preview_target__');
    expect(capturedBody.file_source_credentials.__preview_target__.host).toBe('sftp.internal');
    expect(capturedBody.file_source_credentials.__preview_target__.password).toBe('sftp-secret');
  });
});
