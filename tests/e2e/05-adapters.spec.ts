import { test, expect } from './fixtures';
import { createConfig, deleteConfig, authedContext } from './api-helpers';

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';

// Only the two tests below actually need the real SAP BO mock container
// (docker-compose.integration.yml's `sapbo` service, reachable at
// https://127.0.0.1:18443) — they depend on the mock's real accept/reject
// behavior for a login attempt. The "unreachable host" and Automic tests in
// the second describe block below never talk to a mock at all (Automic has
// none in this repo, and a DNS failure doesn't need one either), so they run
// unconditionally against the app server that's always up.
test.describe('05 adapters - live SAP BO mock', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml sapbo-mock)');

  let boConfigId: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const cfg = await createConfig(ctx, `e2e-adapters-bo-${Date.now()}`, 'dev', {
        db_host: 'unused', db_password: 'unused',
        bo_url: 'https://127.0.0.1:18443', bo_user: 'administrator', bo_password: 'Password1',
        bo_verify_ssl: false,
      });
      boConfigId = cfg.id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    if (!boConfigId) return; // beforeAll never got past createConfig() -- nothing to clean up
    const ctx = await authedContext(adminToken);
    try {
      await deleteConfig(ctx, boConfigId);
    } finally {
      await ctx.dispose();
    }
  });

  test('Test Connection against the real SAP BO mock succeeds', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
    await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(boConfigId));
    await authedPage.locator('[data-testid="bo-test-connection-btn"]').click();
    // test_bo_connection() (api/services/adapter_service.py) never raises -- it always
    // returns a 200 AdapterTestOut body, ok:true on success -- so boTestResult is always
    // populated and the result box renders '✓ ' + message ('Connection successful').
    await expect(authedPage.locator('[data-testid="bo-test-result"]')).toContainText('✓');
  });

  test('negative: bad BO credentials surface the mock\'s 401 body', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    const badCfg = await createConfig(ctx, `e2e-adapters-bo-bad-${Date.now()}`, 'dev', {
      db_host: 'unused', db_password: 'unused',
      bo_url: 'https://127.0.0.1:18443', bo_user: 'administrator', bo_password: 'WRONG-password',
      bo_verify_ssl: false,
    });
    try {
      await authedPage.goto('/');
      await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
      await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(badCfg.id));
      await authedPage.locator('[data-testid="bo-test-connection-btn"]').click();
      // Verified against the real mock (POST /biprws/logon/long with a wrong password
      // returns 401 {"error": "invalid credentials"}): BORestClient.authenticate() wraps
      // any >=400 response in a BOAPIError, and _friendly_error() (adapter_service.py)
      // special-cases BOAPIError -- returning "SAP BO API error {status}: {body}" --
      // BEFORE its generic "Unauthorized"/"401" string check that would otherwise produce
      // "Authentication failed - check username and password". That friendlier copy is
      // effectively dead code for BORestClient's own errors; it only fires for a 401
      // surfaced through some other exception path. Confirmed via a direct curl against a
      // real backend + real mock instance during development of this test.
      await expect(authedPage.locator('[data-testid="bo-test-result"]')).toContainText('SAP BO API error 401');
      await expect(authedPage.locator('[data-testid="bo-test-result"]')).toContainText('invalid credentials');
    } finally {
      await deleteConfig(ctx, badCfg.id);
      await ctx.dispose();
    }
  });
});

test.describe('05 adapters - no live backend required', () => {
  test('negative: unreachable BO host surfaces a DNS resolution error', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    // .invalid is reserved by RFC 2606 to never resolve, so this is a deterministic DNS
    // failure with no dependency on Docker/the SAP BO mock -- BORestClient.authenticate()
    // makes a single unretried request, so this fails fast (confirmed <200ms locally).
    const unreachable = await createConfig(ctx, `e2e-adapters-bo-unreachable-${Date.now()}`, 'dev', {
      db_host: 'unused', db_password: 'unused',
      bo_url: 'https://this-host-does-not-exist.invalid:8443', bo_user: 'x', bo_password: 'x',
    });
    try {
      await authedPage.goto('/');
      await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
      await authedPage.locator('[data-testid="bo-config-select"]').selectOption(String(unreachable.id));
      await authedPage.locator('[data-testid="bo-test-connection-btn"]').click();
      // DNS resolution for a nonexistent host takes ~12s on this platform's resolver
      // before it gives up and returns NXDOMAIN, well past the default 5s assertion timeout.
      await expect(authedPage.locator('[data-testid="bo-test-result"]')).toContainText('Cannot resolve', { timeout: 20_000 });
    } finally {
      await deleteConfig(ctx, unreachable.id);
      await ctx.dispose();
    }
  });

  test('negative: Automic lookup against a fake config surfaces an error (no mock exists)', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    // Automic isn't mocked anywhere in this repo, so any real lookup attempt against a
    // config pointed at a non-resolving host must error -- automic_max_retries:1 keeps
    // AutomicClient's tenacity retry loop (min=2s/max=30s exponential backoff between
    // attempts) from adding several seconds of wall-clock time to this test; a single
    // failed attempt is enough to exercise the failure path.
    const cfg = await createConfig(ctx, `e2e-adapters-automic-bad-${Date.now()}`, 'dev', {
      db_host: 'unused', db_password: 'unused',
      automic_url: 'https://this-host-does-not-exist.invalid:8443', automic_user: 'x', automic_password: 'x',
      automic_max_retries: 1,
    });
    try {
      await authedPage.goto('/');
      await authedPage.locator('[data-testid="nav-tab-adapters"]').click();
      await authedPage.locator('[data-testid="automic-config-select"]').selectOption(String(cfg.id));
      await authedPage.locator('[data-testid="automic-identifier-input"]').fill('ETL_NIGHTLY');
      await authedPage.locator('[data-testid="automic-lookup-btn"]').click();
      // Unlike SAP BO's test-connection endpoint, lookup_automic_job()
      // (api/services/adapter_service.py) always raises HTTPException(502, ...) on
      // failure rather than returning a 200 body -- so the frontend's api() helper throws,
      // and lookupAutomic()'s catch block (frontend/app.js) only shows a toast; it never
      // sets automicResult, so the automic-result testid box never renders on this path.
      // Assert on the toast instead. Verified via a direct curl against a real backend:
      // AutomicClient wraps the underlying connection failure in AutomicTimeoutError,
      // whose type name ("...TimeoutError") matches _friendly_error()'s "Timeout" check,
      // producing "Connection timed out from the application server - ...".
      // Same platform DNS-resolution latency (~12s for a nonexistent host) as the BO
      // unreachable-host test above, past the default 5s assertion timeout.
      await expect(authedPage.locator('.toast-title')).toContainText('Lookup failed', { timeout: 20_000 });
      await expect(authedPage.locator('.toast-msg')).toContainText('Connection timed out');
    } finally {
      await deleteConfig(ctx, cfg.id);
      await ctx.dispose();
    }
  });
});
