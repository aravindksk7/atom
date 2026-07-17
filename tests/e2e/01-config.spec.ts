import { test, expect } from './fixtures';

test.describe('01 config', () => {
  test.afterEach(async ({ authedPage }) => {
    // Clean up any config created by this file's tests (named with an e2e- prefix)
    // via the UI's own delete flow, so afterEach exercises real app behavior rather
    // than reaching around it through the API. deleteConfig() in app.js gates the
    // delete on a native confirm() dialog, so we register an auto-accept handler
    // before each click.
    // The row container itself carries data-testid="config-row-{id}" (no suffix);
    // its Edit/Delete buttons carry "-edit-btn"/"-delete-btn" on top of that same
    // prefix. Tag-qualifying with `div` (the row) vs `button` (its children)
    // disambiguates the two without an XPath ancestor walk.
    const rows = authedPage.locator('div[data-testid^="config-row-"]');
    let count = await rows.count();
    for (let i = count - 1; i >= 0; i--) {
      const row = rows.nth(i);
      const rowText = await row.textContent().catch(() => '');
      if (rowText && rowText.includes('e2e-')) {
        authedPage.once('dialog', (d) => d.accept());
        await row.locator('button:has-text("Delete")').click();
        await expect(row).toBeHidden();
      }
    }
  });

  test('create, validate, and save a new config', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-config"]').click();
    await authedPage.locator('[data-testid="config-new-btn"]').click();
    const modal = authedPage.locator('[data-testid="config-modal"]');
    await expect(modal).toContainText('New Configuration');

    const name = `e2e-config-${Date.now()}`;
    await authedPage.locator('[data-testid="config-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="config-modal-db-host-input"]').fill('127.0.0.1');
    await authedPage.locator('[data-testid="config-modal-db-port-input"]').fill('14333');
    await authedPage.locator('[data-testid="config-modal-db-name-input"]').fill('atom_e2e_src');
    await authedPage.locator('[data-testid="config-modal-db-user-input"]').fill('sa');
    await authedPage.locator('[data-testid="config-modal-db-password-input"]').fill('Atom_Test_12345!');

    await authedPage.locator('[data-testid="config-modal-validate-btn"]').click();
    const result = authedPage.locator('[data-testid="config-validation-result"]');
    await expect(result).toBeVisible();
    await expect(result).toContainText('Configuration is valid.');

    await authedPage.locator('[data-testid="config-modal-save-btn"]').click();
    await expect(modal).toBeHidden();
    // Scope to a config list row rather than a bare text locator — the config name
    // also appears in unrelated <select><option> elements (e.g. compare-source
    // pickers) and transiently in the "Config saved" toast, both of which would
    // otherwise make this locator ambiguous under strict mode.
    await expect(authedPage.locator('[data-testid^="config-row-"]').filter({ hasText: name })).toBeVisible();
  });

  test('negative: validating with an out-of-range DB port shows a field-level error', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-config"]').click();
    await authedPage.locator('[data-testid="config-new-btn"]').click();
    await authedPage.locator('[data-testid="config-modal-name-input"]').fill(`e2e-invalid-${Date.now()}`);
    // db_port is validated server-side (EnvironmentConfig.validate_db_port: must be 1-65535).
    // Leaving DB fields blank isn't enough to trigger an error — the frontend fills in
    // defaults (db_host -> 'localhost', db_password -> '') before sending, both of which
    // are valid per the pydantic model. An out-of-range port is a real, deterministic
    // validation failure.
    await authedPage.locator('[data-testid="config-modal-db-port-input"]').fill('99999999');
    await authedPage.locator('[data-testid="config-modal-validate-btn"]').click();

    const errorRow = authedPage.locator('[data-testid="config-validation-error-row"]').first();
    await expect(errorRow).toBeVisible();
    await expect(errorRow).toContainText('db_port');

    await authedPage.locator('[data-testid="config-modal-cancel-btn"]').click();
    await expect(authedPage.locator('[data-testid="config-modal"]')).toBeHidden();
  });

  test('negative: import-yaml with invalid YAML surfaces an error toast, does not create a config', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-config"]').click();
    const countBefore = await authedPage.locator('[data-testid^="config-row-"][data-testid$="-edit-btn"]').count();

    // The Import YAML card is collapsed by default (yamlImportOpen starts false in
    // app.js) — the textarea only renders once the card header is clicked to expand it.
    await authedPage.getByText('Import YAML', { exact: true }).click();
    await authedPage.locator('[data-testid="config-yaml-textarea"]').fill('not: [valid: yaml: at: all');
    await authedPage.locator('[data-testid="config-yaml-import-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Import failed');

    // Confirm the test's own name: a failed import must not silently persist a config.
    await expect(authedPage.locator('[data-testid^="config-row-"][data-testid$="-edit-btn"]')).toHaveCount(countBefore);
  });
});
