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
});
