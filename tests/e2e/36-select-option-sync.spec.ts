import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig } from './api-helpers';

// Regression coverage for: a <select> whose <option>s come from a nested x-for
// shows the first option instead of the value x-model holds. Alpine writes the
// select's value while walking the element -- before the x-for has rendered any
// option -- so the write no-ops and the browser falls back to index 0, leaving
// the control lying about the component state. The Launch tab lives inside
// `x-if="currentView === 'jobs'"`, so every trip away and back remounts these
// selects and re-triggers it. See the resync observer in frontend/app.js.
test.describe('36 select option sync', () => {
  test('Launch tab selects still show their picks after the tab remounts', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    const name = `e2e_selsync_cfg_${Date.now()}`;
    const cfg = await createConfig(ctx, name, 'dev', {
      connections: { alpha: { db_type: 'mssql' }, beta: { db_type: 'mssql' } },
    });

    try {
      await authedPage.goto('/');
      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();

      const savedConfig = authedPage.locator('#a11y-launch-saved-config');
      await expect(savedConfig).toBeVisible();
      await savedConfig.selectOption(String(cfg.id));

      const sourceConn = authedPage.locator('#a11y-launch-source-connection');
      await expect(sourceConn).toBeVisible();
      await sourceConn.selectOption('beta');

      // Leave the tab and come back — x-if tears the subtree down and rebuilds it.
      await authedPage.locator('[data-testid="nav-tab-home"]').click();
      await expect(savedConfig).toHaveCount(0);
      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();

      await expect(savedConfig).toHaveValue(String(cfg.id));
      await expect(sourceConn).toHaveValue('beta');
    } finally {
      await deleteConfig(ctx, cfg.id);
      await ctx.dispose();
    }
  });
});
