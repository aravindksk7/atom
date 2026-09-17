import { test, expect } from './fixtures';
import { authedContext, createFileJob, createConfig } from './api-helpers';

// Job Selection modal coverage. Regression-driven: the modal shows Live
// Connections + Saved Config the same way regardless of whether "Jobs come
// from" is an inline job list or a saved execution sequence, but
// saveSelection() used to only send run_settings/config_id for the inline
// branch (fixed in a7f6c24). This file exercises both source modes end to
// end through the real UI, plus the save-blocking negative cases the modal
// relies on.
//
// Everything is scoped to the modal itself ([aria-labelledby=
// "selectionModalTitle"]) rather than page-wide locators: "Name", "Live
// Connections", and "Save" all also appear in the Jobs sub-tab, the
// Schedule modal, and/or the Sequence editor, which stay mounted (just
// hidden via x-show) while this modal is open.

let jobA: string;
let jobB: string;
let configId: number;
let sequenceId: number;
let sequenceName: string;

test.beforeAll(async ({ adminToken }) => {
  const ctx = await authedContext(adminToken);
  try {
    const stamp = Date.now();
    jobA = (await createFileJob(ctx, `e2e-sel-a-${stamp}`)).name;
    jobB = (await createFileJob(ctx, `e2e-sel-b-${stamp}`)).name;
    configId = (await createConfig(ctx, `e2e-sel-cfg-${stamp}`, 'prod', { bo_url: 'https://bo.example.com' })).id;

    sequenceName = `e2e-sel-seq-${stamp}`;
    const seqResp = await ctx.post('/api/sequences', {
      data: { name: sequenceName, steps: [{ step_id: 'a', job_name: jobA }] },
    });
    if (!seqResp.ok()) throw new Error(`sequence create failed: ${seqResp.status()} ${await seqResp.text()}`);
    sequenceId = (await seqResp.json()).id;
  } finally {
    await ctx.dispose();
  }
});

test.describe('Job Selections', () => {
  test.beforeEach(async ({ authedPage: page }) => {
    await page.goto('/');
    await page.getByTestId('nav-tab-jobs').click();
    await page.getByRole('button', { name: 'Job Selections' }).click();
    await expect(page.getByTestId('job-selections-panel')).toBeVisible();
  });

  function dialogOf(page: import('@playwright/test').Page) {
    return page.locator('[aria-labelledby="selectionModalTitle"]');
  }

  async function liveConnectionsIsOn(dialog: ReturnType<typeof dialogOf>): Promise<boolean> {
    const cls = await dialog.locator('.toggle-track').getAttribute('class');
    return /(^|\s)on(\s|$)/.test(cls || '');
  }

  // ---- Positive: inline job list ----------------------------------------

  test('create an inline selection, then enable Live Connections + config on edit and it survives a reload', async ({ authedPage: page }) => {
    const name = 'e2e-inline-selection';
    await page.getByRole('button', { name: '+ New Selection' }).click();
    let dialog = dialogOf(page);
    await dialog.getByLabel('Name *').fill(name);
    await dialog.locator('label', { hasText: jobA }).getByRole('checkbox').check();
    await dialog.locator('label', { hasText: jobB }).getByRole('checkbox').check();

    await expect(dialog.getByRole('button', { name: 'Save' })).toBeEnabled();
    await dialog.getByRole('button', { name: 'Save' }).click();

    // Edit: turn Live Connections on and pick the saved config.
    const row = page.locator('[data-testid^="job-selection-"]', { hasText: name });
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: 'Edit' }).click();
    dialog = dialogOf(page);
    await dialog.getByRole('button', { name: 'Live Connections' }).click();
    await dialog.getByLabel('Saved Config').selectOption(String(configId));
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(row).toBeVisible();

    // Reopen fresh (simulates a reload -- the modal always re-fetches the
    // selection via GET /api/selections/{id} on open, so this reads back
    // exactly what the server persisted, not stale client state).
    await row.getByRole('button', { name: 'Edit' }).click();
    dialog = dialogOf(page);
    expect(await liveConnectionsIsOn(dialog)).toBe(true);
    await expect(dialog.getByLabel('Saved Config')).toHaveValue(String(configId));
    await expect(dialog.locator('label', { hasText: jobA }).getByRole('checkbox')).toBeChecked();
    await expect(dialog.locator('label', { hasText: jobB }).getByRole('checkbox')).toBeChecked();
  });

  // ---- Positive: sequence-sourced selection (the actual regression) -----

  test('create a sequence-sourced selection, enable Live Connections + config on edit, and it survives a reload', async ({ authedPage: page }) => {
    const name = 'e2e-sequence-selection';
    await page.getByRole('button', { name: '+ New Selection' }).click();
    let dialog = dialogOf(page);
    await dialog.getByLabel('Name *').fill(name);
    await dialog.getByRole('radio', { name: 'saved execution sequence' }).check();
    await dialog.getByTestId('selection-sequence-picker').selectOption({ label: sequenceName });

    await expect(dialog.getByRole('button', { name: 'Save' })).toBeEnabled();
    await dialog.getByRole('button', { name: 'Save' }).click();

    const row = page.locator('[data-testid^="job-selection-"]', { hasText: name });
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: 'Edit' }).click();
    dialog = dialogOf(page);
    // Bug reproduced here: before the fix, flipping this on and saving a
    // sequence-sourced selection had no effect on the PUT body at all.
    await dialog.getByRole('button', { name: 'Live Connections' }).click();
    await dialog.getByLabel('Saved Config').selectOption(String(configId));
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(row).toBeVisible();

    await row.getByRole('button', { name: 'Edit' }).click();
    dialog = dialogOf(page);
    expect(await liveConnectionsIsOn(dialog)).toBe(true);
    await expect(dialog.getByLabel('Saved Config')).toHaveValue(String(configId));
    // The source mode itself must have round-tripped too, not silently
    // fallen back to inline.
    await expect(dialog.getByRole('radio', { name: 'saved execution sequence' })).toBeChecked();
    await expect(dialog.getByTestId('selection-sequence-picker')).toHaveValue(String(sequenceId));
  });

  test('turning Live Connections back off on a sequence-sourced selection also persists', async ({ authedPage: page }) => {
    const name = 'e2e-sequence-selection-off';
    await page.getByRole('button', { name: '+ New Selection' }).click();
    let dialog = dialogOf(page);
    await dialog.getByLabel('Name *').fill(name);
    await dialog.getByRole('radio', { name: 'saved execution sequence' }).check();
    await dialog.getByTestId('selection-sequence-picker').selectOption({ label: sequenceName });
    await dialog.getByRole('button', { name: 'Live Connections' }).click(); // on at creation
    await dialog.getByRole('button', { name: 'Save' }).click();

    const row = page.locator('[data-testid^="job-selection-"]', { hasText: name });
    await expect(row).toBeVisible();

    await row.getByRole('button', { name: 'Edit' }).click();
    dialog = dialogOf(page);
    expect(await liveConnectionsIsOn(dialog)).toBe(true);
    await dialog.getByRole('button', { name: 'Live Connections' }).click(); // flip off
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(row).toBeVisible();

    await row.getByRole('button', { name: 'Edit' }).click();
    dialog = dialogOf(page);
    expect(await liveConnectionsIsOn(dialog)).toBe(false);
  });

  // ---- Positive: switching source mode persists cleanly -----------------

  test('switching an inline selection to a sequence source persists the new source, not both', async ({ authedPage: page }) => {
    const name = 'e2e-mode-switch';
    await page.getByRole('button', { name: '+ New Selection' }).click();
    let dialog = dialogOf(page);
    await dialog.getByLabel('Name *').fill(name);
    await dialog.locator('label', { hasText: jobA }).getByRole('checkbox').check();
    await dialog.getByRole('button', { name: 'Save' }).click();

    const row = page.locator('[data-testid^="job-selection-"]', { hasText: name });
    await expect(row).toBeVisible();

    await row.getByRole('button', { name: 'Edit' }).click();
    dialog = dialogOf(page);
    await expect(dialog.getByRole('radio', { name: 'inline job list' })).toBeChecked();
    await dialog.getByRole('radio', { name: 'saved execution sequence' }).check();
    await dialog.getByTestId('selection-sequence-picker').selectOption({ label: sequenceName });
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(row).toBeVisible();

    await row.getByRole('button', { name: 'Edit' }).click();
    dialog = dialogOf(page);
    await expect(dialog.getByRole('radio', { name: 'saved execution sequence' })).toBeChecked();
    await expect(dialog.getByTestId('selection-sequence-picker')).toHaveValue(String(sequenceId));
  });

  // ---- Negative: save is blocked without a job source --------------------

  test('save is disabled with no jobs checked in inline mode', async ({ authedPage: page }) => {
    await page.getByRole('button', { name: '+ New Selection' }).click();
    const dialog = dialogOf(page);
    await dialog.getByLabel('Name *').fill('e2e-no-jobs');
    await expect(dialog.getByRole('radio', { name: 'inline job list' })).toBeChecked();
    await expect(dialog.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  test('save is disabled with no sequence picked in sequence mode', async ({ authedPage: page }) => {
    await page.getByRole('button', { name: '+ New Selection' }).click();
    const dialog = dialogOf(page);
    await dialog.getByLabel('Name *').fill('e2e-no-sequence');
    await dialog.getByRole('radio', { name: 'saved execution sequence' }).check();
    // Alpine's :value="null" on the placeholder <option> renders as the
    // literal string "null" -- that's the untouched/no-selection state.
    await expect(dialog.getByTestId('selection-sequence-picker')).toHaveValue('null');
    await expect(dialog.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  // ---- Negative: duplicate name is rejected, modal stays open ------------

  test('duplicate name is rejected and the modal stays open', async ({ authedPage: page }) => {
    const name = 'e2e-dup-selection';
    await page.getByRole('button', { name: '+ New Selection' }).click();
    let dialog = dialogOf(page);
    await dialog.getByLabel('Name *').fill(name);
    await dialog.locator('label', { hasText: jobA }).getByRole('checkbox').check();
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(page.locator('[data-testid^="job-selection-"]', { hasText: name })).toBeVisible();

    await page.getByRole('button', { name: '+ New Selection' }).click();
    dialog = dialogOf(page);
    await dialog.getByLabel('Name *').fill(name);
    await dialog.locator('label', { hasText: jobB }).getByRole('checkbox').check();
    await dialog.getByRole('button', { name: 'Save' }).click();

    // 409 -> toast, modal must not silently close on failure.
    await expect(dialog).toBeVisible();
    await expect(page.getByText(/already exists/i)).toBeVisible();
  });
});
