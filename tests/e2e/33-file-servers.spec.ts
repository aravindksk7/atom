import { test, expect } from './fixtures';

// Covers the File Servers tab (frontend/partials/tab-file-servers.html) added for
// SFTP/SCP/S3 authentication profiles used by file_watcher and multi-file jobs.
// Confirms the full CRUD round-trip through the real UI: create, secret masking on
// Edit (api/routes/file_servers.py's _mask() replaces any set secret field with
// "********" in every GET response, and _preserve_masked_secrets() on PUT drops a
// field back out of the update payload when it still equals that mask, so
// re-saving the masked value is a no-op against the stored ciphertext), and delete.
//
// Note: unlike deleteFileServer() has no confirm() dialog in file-servers.js -- the
// Delete button calls the DELETE endpoint immediately -- so this test needs no
// dialog-handling step before asserting the row disappears.
test.describe('33 file servers: profile CRUD', () => {
  test('create, mask secret, edit, delete an SFTP profile', async ({ authedPage }) => {
    const name = `e2e-sftp-${Date.now()}`;

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-file-servers"]').click();
    await authedPage.locator('[data-testid="file-server-add-btn"]').click();
    await authedPage.locator('[data-testid="file-server-name-input"]').fill(name);
    await authedPage.locator('[data-testid="file-server-kind-select"]').selectOption('sftp');
    await authedPage.locator('[data-testid="file-server-password-input"]').fill('hunter2');
    await authedPage.locator('[data-testid="file-server-save-btn"]').click();

    // Card testid is `'file-server-' + fs.id` (numeric id, unknown up front), and the
    // card also contains a `file-server-test-<id>` button -- so match on the id-prefixed
    // testid AND the profile name text to land on the card itself, not its descendants.
    const row = authedPage.locator('[data-testid^="file-server-"]', { hasText: name });
    await expect(row).toBeVisible();

    await row.getByRole('button', { name: 'Edit' }).click();
    await expect(authedPage.locator('[data-testid="file-server-password-input"]')).toHaveValue('********');

    // Re-saving the masked value must not corrupt the stored secret (see the
    // _preserve_masked_secrets comment above) -- this also proves the Edit modal
    // reuses the same data-testid="file-server-save-btn" as Add.
    await authedPage.locator('[data-testid="file-server-save-btn"]').click();
    await expect(row).toBeVisible();

    await row.getByRole('button', { name: 'Delete' }).click();
    await expect(row).toBeHidden();
  });
});
