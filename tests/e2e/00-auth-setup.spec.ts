import { test, expect } from '@playwright/test';
import { primeAdminToken } from './api-helpers';

// NOTE on ordering: this file runs in Playwright's `setup` project
// (playwright.config.ts), which the `chromium` project (every other spec file)
// declares as a `dependencies: ['setup']` — Playwright guarantees `setup` runs
// to completion before `chromium` starts. That guarantee is *required* here:
// plain filename prefixes (00-, 01-, ...) do NOT reliably control execution
// order on their own (confirmed empirically — Playwright's file-discovery order
// is not guaranteed alphabetical), so don't rely on renumbering files to fix
// ordering bugs; use `dependencies` instead. Within this file, tests still run
// in declaration order (see `mode: 'serial'` below).
//
// The backend only allows the *very first* POST /api/tokens ever made against
// an empty DB to bootstrap an admin token unauthenticated (see
// api/routes/tokens.py: is_bootstrap = repo.count() == 0). The first test below
// consumes that one-time bootstrap via the UI and captures the resulting admin
// token in this module-scoped variable; every later test in this file that
// needs an admin token reuses it instead of trying to bootstrap again (a second
// bootstrap attempt would 401, since the DB is no longer empty once the first
// test creates a token).
//
// It also calls api-helpers.ts's primeAdminToken() with that same token, which
// writes it to tests/e2e/.admin-token.json — otherwise every test in the
// `chromium` project using fixtures.ts's `adminToken` fixture (which calls
// bootstrapAdminToken()) would attempt its own bootstrap POST against a DB
// that's no longer empty, and 401. (A plain in-memory cache wouldn't survive
// the process boundary between the `setup` and `chromium` projects, which is
// why this goes through a file, not a shared module variable.)
let adminToken: string;

test.describe('00 auth setup', () => {
  // Serial mode: later tests depend on the admin token captured by the first
  // test (module-scoped `adminToken` above). Serial mode keeps them in the
  // same worker and skips the rest of the block on a failure instead of
  // letting Playwright recycle the worker (which would silently reset
  // `adminToken` and cause confusing cascading failures downstream).
  test.describe.configure({ mode: 'serial' });

  test('bootstrap creates the first admin token and auto-connects', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('[data-testid="auth-modal"]')).toBeVisible();
    await page.locator('[data-testid="auth-bootstrap-name-input"]').fill('e2e-first-admin');
    await page.locator('[data-testid="auth-bootstrap-submit-btn"]').click();

    const created = page.locator('[data-testid="auth-created-token-value"]');
    await expect(created).toBeVisible();
    const rawToken = (await created.textContent())!.trim();
    expect(rawToken.length).toBeGreaterThan(10);
    adminToken = rawToken;
    primeAdminToken(rawToken);

    await page.locator('[data-testid="auth-done-btn"]').click();
    await expect(page.locator('[data-testid="auth-modal"]')).toBeHidden();
    await expect(page.locator('[data-testid="auth-status-connected"]')).toContainText('Administrator');
  });

  test('paste-token connect works for a second (non-admin) token created via the API', async ({ page, request }) => {
    const resp = await request.post('/api/tokens', {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: { name: 'e2e-standard-user' },
    });
    const { raw_token: standardToken } = await resp.json();

    await page.goto('/');
    // A token already exists in the DB (from the previous test), so
    // authInitialized is true and the modal no longer auto-opens on load —
    // it must be opened explicitly via the "Set up access" button.
    await page.locator('[data-testid="auth-status-open-btn"]').click();
    await page.locator('[data-testid="auth-paste-input"]').fill(standardToken);
    await page.locator('[data-testid="auth-activate-btn"]').click();
    await expect(page.locator('[data-testid="auth-status-connected"]')).toContainText('Standard user');
  });

  test('negative: malformed/garbage token is rejected with the exact backend error', async ({ page }) => {
    await page.goto('/');
    await page.locator('[data-testid="auth-status-open-btn"]').click();
    await page.locator('[data-testid="auth-paste-input"]').fill('not-a-real-token-at-all');
    await page.locator('[data-testid="auth-activate-btn"]').click();
    await expect(page.locator('[data-testid="auth-error-text"]')).toHaveText(
      'Your API token was rejected. Paste a valid raw token.'
    );
  });

  test('negative: unauthenticated API call to an admin route returns 401 with the exact detail', async ({ request }) => {
    const resp = await request.get('/api/tokens');
    expect(resp.status()).toBe(401);
    expect((await resp.json()).detail).toBe('Missing or invalid Authorization header');
  });

  test('negative: non-admin token hitting an admin-only route returns 403', async ({ request }) => {
    const created = await request.post('/api/tokens', {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: { name: 'e2e-non-admin-403-check' },
    });
    const { raw_token: standardToken } = await created.json();

    const resp = await request.get('/api/tokens', {
      headers: { Authorization: `Bearer ${standardToken}` },
    });
    expect(resp.status()).toBe(403);
    expect((await resp.json()).detail).toBe('Admin token required');
  });

  test('disconnect clears the session and re-shows the auth modal on next load', async ({ page }) => {
    // Note: deliberately NOT page.addInitScript() here — that re-runs on every
    // navigation in this page (including the page.reload() below), which
    // would silently re-inject the token right after we remove it. Instead,
    // set sessionStorage via evaluate() after the first goto, then reload.
    await page.goto('/');
    await page.evaluate((token) => window.sessionStorage.setItem('etl_token', token), adminToken);
    await page.reload();
    await expect(page.locator('[data-testid="auth-status-connected"]')).toBeVisible();

    await page.evaluate(() => window.sessionStorage.removeItem('etl_token'));
    await page.reload();
    // With no token and authInitialized already true, the modal no longer
    // auto-opens (that only happens for the zero-token bootstrap case) — open
    // it explicitly, then confirm it shows the paste path, not bootstrap.
    await page.locator('[data-testid="auth-status-open-btn"]').click();
    await expect(page.locator('[data-testid="auth-paste-input"]')).toBeVisible();
    await expect(page.locator('[data-testid="auth-bootstrap-name-input"]')).toBeHidden();
  });
});
