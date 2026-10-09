import { test, expect } from './fixtures';

async function openContracts(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-contracts"]').click();
}

async function createContract(page: import('@playwright/test').Page, name: string) {
  await page.locator('[data-testid="contracts-new-btn"]').click();
  await page.locator('[data-testid="contract-modal-name-input"]').fill(name);
  await page.locator('[data-testid="contract-modal-source-job-input"]').fill('e2e_source_job');
  await page.locator('[data-testid="contract-modal-owner-input"]').fill('e2e@test.local');
  await page.locator('[data-testid="contract-modal-save-btn"]').click();
  await expect(page.locator(`[data-testid="contract-row-${name}"]`)).toBeVisible();
}

test.describe('09 contracts', () => {
  test('create, view, and delete a contract', async ({ authedPage }) => {
    const name = `e2e_contract_${Date.now()}`;
    await openContracts(authedPage);
    await createContract(authedPage, name);
    await authedPage.locator(`[data-testid="contract-row-${name}"]`).click();
    authedPage.once('dialog', (d) => d.accept());
    await authedPage.locator('[data-testid="contract-delete-btn"]').click();
    await expect(authedPage.locator(`[data-testid="contract-row-${name}"]`)).toBeHidden();
  });

  test('bump version and see it in the history table', async ({ authedPage }) => {
    const name = `e2e_bump_${Date.now()}`;
    await openContracts(authedPage);
    await createContract(authedPage, name);
    await authedPage.locator(`[data-testid="contract-row-${name}"]`).click();
    await authedPage.locator('[data-testid="contract-bump-type-select"]').selectOption('major');
    await authedPage.locator('[data-testid="contract-bump-note-input"]').fill('e2e bump');
    await authedPage.locator('[data-testid="contract-bump-btn"]').click();
    await expect(authedPage.locator('text=e2e bump')).toBeVisible();
    authedPage.once('dialog', (d) => d.accept());
    await authedPage.locator('[data-testid="contract-delete-btn"]').click();
  });

  test('run contract test and view ODCS results', async ({ authedPage }) => {
    const name = `e2e_test_contract_${Date.now()}`;
    await openContracts(authedPage);
    await createContract(authedPage, name);
    await authedPage.locator(`[data-testid="contract-row-${name}"]`).click();

    await authedPage.locator('[data-testid="contract-run-test-btn"]').click();
    await expect(authedPage.locator('[data-testid="contract-test-status-badge"]')).toBeVisible({ timeout: 15000 });

    const statusText = (await authedPage.locator('[data-testid="contract-test-status-badge"]').textContent())?.trim();
    expect(['PASSED', 'FAILED', 'WARNING']).toContain(statusText);

    await expect(authedPage.locator('table[aria-label="contract test checks table"]')).toBeVisible();
    const rowCount = await authedPage.locator('table[aria-label="contract test checks table"] tbody tr').count();
    expect(rowCount).toBeGreaterThanOrEqual(1);

    authedPage.once('dialog', (d) => d.accept());
    await authedPage.locator('[data-testid="contract-delete-btn"]').click();
  });

  test('filter contract test checks by category', async ({ authedPage }) => {
    const name = `e2e_filter_contract_${Date.now()}`;
    await openContracts(authedPage);
    await createContract(authedPage, name);
    await authedPage.locator(`[data-testid="contract-row-${name}"]`).click();

    await authedPage.locator('[data-testid="contract-run-test-btn"]').click();
    await expect(authedPage.locator('[data-testid="contract-test-status-badge"]')).toBeVisible({ timeout: 15000 });

    const allCount = await authedPage.locator('table[aria-label="contract test checks table"] tbody tr').count();

    await authedPage.locator('[data-testid="contract-test-filter-sla"]').click();
    const slaCount = await authedPage.locator('table[aria-label="contract test checks table"] tbody tr').count();
    expect(slaCount).toBeLessThanOrEqual(allCount);

    await authedPage.locator('[data-testid="contract-test-filter-all"]').click();
    const backToAll = await authedPage.locator('table[aria-label="contract test checks table"] tbody tr').count();
    expect(backToAll).toBe(allCount);

    authedPage.once('dialog', (d) => d.accept());
    await authedPage.locator('[data-testid="contract-delete-btn"]').click();
  });

  test('export contract test report', async ({ authedPage }) => {
    const name = `e2e_export_contract_${Date.now()}`;
    await openContracts(authedPage);
    await createContract(authedPage, name);
    await authedPage.locator(`[data-testid="contract-row-${name}"]`).click();

    await authedPage.locator('[data-testid="contract-run-test-btn"]').click();
    await expect(authedPage.locator('[data-testid="contract-test-status-badge"]')).toBeVisible({ timeout: 15000 });

    const downloadPromise = authedPage.waitForEvent('download');
    await authedPage.locator('[data-testid="contract-export-report-btn"]').click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toContain('contract-test-');

    authedPage.once('dialog', (d) => d.accept());
    await authedPage.locator('[data-testid="contract-delete-btn"]').click();
  });

  test('negative: saving a duplicate contract name surfaces a native alert', async ({ authedPage }) => {
    const name = `e2e_dup_contract_${Date.now()}`;
    await openContracts(authedPage);
    await createContract(authedPage, name);
    await authedPage.locator('[data-testid="contracts-new-btn"]').click();
    await authedPage.locator('[data-testid="contract-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="contract-modal-source-job-input"]').fill('e2e_source_job_2');
    let alertText = '';
    authedPage.once('dialog', async (d) => { alertText = d.message(); await d.accept(); });
    await authedPage.locator('[data-testid="contract-modal-save-btn"]').click();
    await expect.poll(() => alertText).toContain('Save failed');
    await authedPage.locator('[data-testid="contract-modal-cancel-btn"]').click();
    await authedPage.locator(`[data-testid="contract-row-${name}"]`).click();
    authedPage.once('dialog', (d) => d.accept());
    await authedPage.locator('[data-testid="contract-delete-btn"]').click();
  });

  test('source job dropdown lists jobs, picks one, and warns on unknown name', async ({ authedPage }) => {
    await authedPage.route('**/api/jobs', (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({ json: [
            { name: 'e2e_listed_job', job_type: 'reconciliation' },
            { name: 'e2e_other_job', job_type: 'bo_report' },
          ] })
        : route.continue());
    await openContracts(authedPage);
    await authedPage.locator('[data-testid="contracts-new-btn"]').click();

    const input = authedPage.locator('[data-testid="contract-modal-source-job-input"]');
    const menu = authedPage.locator('[data-testid="contract-source-job-options"]');
    const options = authedPage.locator('[data-testid="contract-source-job-option"]');

    // Themed dropdown opens on focus and filters as you type.
    await input.click();
    await expect(menu).toBeVisible();
    await expect(options).toHaveCount(2);
    await expect(options.first()).toContainText('reconciliation');
    await input.fill('other');
    await expect(options).toHaveCount(1);

    // Keyboard pick: ArrowDown highlights, Enter selects and closes.
    await input.press('ArrowDown');
    await input.press('Enter');
    await expect(input).toHaveValue('e2e_other_job');
    await expect(menu).toBeHidden();

    // Escape closes only the dropdown, not the modal.
    await input.fill('');
    await expect(menu).toBeVisible();
    await input.press('Escape');
    await expect(menu).toBeHidden();
    await expect(authedPage.locator('[data-testid="contract-modal"]')).toBeVisible();

    // Mouse pick.
    await input.click();
    await authedPage.locator('[data-testid="contract-source-job-option"]', { hasText: 'e2e_listed_job' }).click();
    await expect(input).toHaveValue('e2e_listed_job');
    await expect(authedPage.locator('[data-testid="contract-modal-source-job-warning"]')).toBeHidden();
    await input.fill('not_a_job');
    await expect(authedPage.locator('[data-testid="contract-modal-source-job-warning"]')).toBeVisible();
    await authedPage.locator('[data-testid="contract-modal-cancel-btn"]').click();
  });

  test('consumers are entered as chips and persist', async ({ authedPage }) => {
    const name = `e2e_chips_${Date.now()}`;
    await openContracts(authedPage);
    await authedPage.locator('[data-testid="contracts-new-btn"]').click();
    await authedPage.locator('[data-testid="contract-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="contract-modal-source-job-input"]').fill('e2e_source_job');
    await authedPage.locator('[data-testid="contract-modal-owner-input"]').fill('e2e@test.local');
    const consumers = authedPage.locator('[data-testid="contract-modal-consumers-input"]');
    await consumers.fill('finance');
    await consumers.press('Enter');
    await consumers.fill('ops');
    await consumers.press('Enter');
    await consumers.fill('drop_me');
    await consumers.press('Enter');
    await expect(authedPage.locator('[data-testid="contract-consumer-chip"]')).toHaveCount(3);
    await authedPage.locator('[data-testid="contract-consumer-chip-remove"]').nth(2).click();
    await expect(authedPage.locator('[data-testid="contract-consumer-chip"]')).toHaveCount(2);
    await authedPage.locator('[data-testid="contract-modal-save-btn"]').click();

    await authedPage.locator(`[data-testid="contract-row-${name}"]`).click();
    await expect(authedPage.locator('text=Consumers: finance, ops')).toBeVisible();
    authedPage.once('dialog', (d) => d.accept());
    await authedPage.locator('[data-testid="contract-delete-btn"]').click();
  });

  test('resolve an open breach with a note', async ({ authedPage }) => {
    const name = `e2e_resolve_${Date.now()}`;
    let resolved = false;
    let sentBody: any = null;
    await authedPage.route(`**/api/contracts/${name}/breaches`, (route) => route.fulfill({
      json: [{
        id: 7, contract_id: 1, run_id: 'r1', breach_type: 'dq_violation',
        opened_at: '2026-10-10T08:00:00Z', escalated: false,
        resolved_at: resolved ? '2026-10-10T09:00:00Z' : null,
        duration_hours: resolved ? 1 : null,
        resolved_by: resolved ? 'e2e' : null,
        resolution_note: resolved ? 'false positive' : null,
      }],
    }));
    await authedPage.route(`**/api/contracts/${name}/breaches/7/resolve`, async (route) => {
      sentBody = route.request().postDataJSON();
      resolved = true;
      await route.fulfill({ json: {} });
    });

    await openContracts(authedPage);
    await createContract(authedPage, name);
    await authedPage.locator(`[data-testid="contract-row-${name}"]`).click();
    await authedPage.locator('[data-testid="contract-breach-resolve-btn"]').click();
    await expect(authedPage.locator('[data-testid="contract-resolve-confirm-btn"]')).toBeDisabled();
    await authedPage.locator('[data-testid="contract-resolve-note-input"]').fill('false positive');
    await authedPage.locator('[data-testid="contract-resolve-by-input"]').fill('e2e');
    await authedPage.locator('[data-testid="contract-resolve-confirm-btn"]').click();

    await expect(authedPage.locator('[data-testid="contract-resolve-modal"]')).toBeHidden();
    expect(sentBody).toEqual({ note: 'false positive', resolved_by: 'e2e' });
    await expect(authedPage.locator('text=resolved by e2e — false positive')).toBeVisible();
    await expect(authedPage.locator('[data-testid="contract-breach-resolve-btn"]')).toBeHidden();

    authedPage.once('dialog', (d) => d.accept());
    await authedPage.locator('[data-testid="contract-delete-btn"]').click();
  });

  test('export a contract as yaml and preview it as unchanged on import', async ({ authedPage }) => {
    const name = `e2e_yaml_${Date.now()}`;
    await openContracts(authedPage);
    await createContract(authedPage, name);
    await authedPage.locator(`[data-testid="contract-row-${name}"]`).click();

    const downloadPromise = authedPage.waitForEvent('download');
    await authedPage.locator('[data-testid="contract-export-btn"]').click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe(`${name}.contract.yaml`);
    const fs = await import('fs');
    const text = fs.readFileSync(await download.path() as string, 'utf-8');
    expect(text).toContain(`name: ${name}`);

    await authedPage.locator('[data-testid="contracts-import-btn"]').click();
    await authedPage.locator('[data-testid="contract-import-textarea"]').fill(text);
    await authedPage.locator('[data-testid="contract-import-preview-btn"]').click();
    await expect(authedPage.locator('[data-testid="contract-import-row"]')).toHaveCount(1);
    await expect(authedPage.locator('[data-testid="contract-import-row"]')).toContainText('unchanged');
    await expect(authedPage.locator('[data-testid="contract-import-apply-btn"]')).toBeDisabled();
    await authedPage.locator('[data-testid="contract-import-cancel-btn"]').click();

    authedPage.once('dialog', (d) => d.accept());
    await authedPage.locator('[data-testid="contract-delete-btn"]').click();
  });
});
