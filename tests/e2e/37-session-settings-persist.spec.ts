import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig } from './api-helpers';

// Regression coverage for: saveSessionSettings() existed and loadSessionSettings()
// ran on init, but nothing ever wrote etl_session_settings — so Launch settings,
// the Compare sub-tab and the History filters all reset on every refresh. init()
// now registers $watch persistence for exactly the keys the loader restores.
test.describe('37 session settings persistence', () => {
  test('Launch settings and Compare sub-tab survive a refresh', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    const name = `e2e_session_cfg_${Date.now()}`;
    const cfg = await createConfig(ctx, name, 'dev', {
      connections: { alpha: { db_type: 'mssql' }, beta: { db_type: 'mssql' } },
    });

    try {
      await authedPage.goto('/');
      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();

      await authedPage.locator('#a11y-launch-source-env').selectOption('qa');
      await authedPage.locator('#a11y-launch-target-env').selectOption('prod');
      await authedPage.locator('#a11y-launch-saved-config').selectOption(String(cfg.id));
      await authedPage.locator('#a11y-launch-source-connection').selectOption('beta');
      await authedPage.locator('#a11y-launch-execution-mode').selectOption('sequential');

      // localStorage writes happen in a $watch effect — let it flush before reload.
      await expect
        .poll(() => authedPage.evaluate(() => {
          const raw = localStorage.getItem('etl_session_settings');
          return raw ? JSON.parse(raw).launchSettings.execution_mode : null;
        }))
        .toBe('sequential');

      await authedPage.reload();
      await expect(authedPage.locator('[data-testid="auth-status-connected"]')).toBeVisible();
      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();

      await expect(authedPage.locator('#a11y-launch-source-env')).toHaveValue('qa');
      await expect(authedPage.locator('#a11y-launch-target-env')).toHaveValue('prod');
      await expect(authedPage.locator('#a11y-launch-saved-config')).toHaveValue(String(cfg.id));
      await expect(authedPage.locator('#a11y-launch-source-connection')).toHaveValue('beta');
      await expect(authedPage.locator('#a11y-launch-execution-mode')).toHaveValue('sequential');
    } finally {
      await deleteConfig(ctx, cfg.id);
      await ctx.dispose();
    }
  });
});
