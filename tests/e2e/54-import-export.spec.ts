import { test, expect } from './fixtures';
import { authedContext } from './api-helpers';

let token: string;
let configName: string;

test.beforeAll(async ({ adminToken }) => {
  token = adminToken;
  configName = `e2e-import-export-${Date.now()}`;
  const ctx = await authedContext(token);
  try {
    const resp = await ctx.post('/api/configs', {
      data: { name: configName, env_name: configName, config_data: { db_host: 'localhost' } },
    });
    if (!resp.ok()) throw new Error(`create config failed: ${resp.status()} ${await resp.text()}`);
  } finally { await ctx.dispose(); }
});

test.afterAll(async () => {
  const ctx = await authedContext(token);
  try {
    const list = await ctx.get('/api/configs');
    const configs = await list.json();
    const match = configs.find((c: any) => c.name === configName);
    if (match) await ctx.delete(`/api/configs/${match.id}`);
  } finally { await ctx.dispose(); }
});

test('exports a selected config and re-import reports it as skipped', async ({ authedPage }) => {
  await authedPage.goto('/#import-export');
  await expect(authedPage.locator('[data-testid="import-export-tab"]')).toBeVisible();

  const checkbox = authedPage.locator(`[data-testid="import-export-check-configs-${configName}"]`);
  await expect(checkbox).toBeVisible();
  await checkbox.check();

  const downloadPromise = authedPage.waitForEvent('download');
  await authedPage.locator('[data-testid="import-export-export-btn"]').click();
  const download = await downloadPromise;
  const bundlePath = await download.path();
  expect(bundlePath).toBeTruthy();

  const fs = require('fs');
  const bundleText = fs.readFileSync(bundlePath, 'utf-8');
  const bundle = JSON.parse(bundleText);
  expect(bundle.configs.some((c: any) => c.name === configName)).toBe(true);
  expect(bundle.configs.find((c: any) => c.name === configName).config_data.db_host).toBe('localhost');

  await authedPage.setInputFiles('[data-testid="import-export-file-input"]', {
    name: 'bundle.json',
    mimeType: 'application/json',
    buffer: Buffer.from(bundleText),
  });

  const resultRow = authedPage.locator(`[data-testid="import-export-result-configs-${configName}"]`);
  await expect(resultRow).toBeVisible();
  await expect(resultRow).toContainText('skipped');
});
