import { appendFileSync } from 'node:fs';
import path from 'node:path';
import { test as base, expect, Page } from '@playwright/test';
import { bootstrapAdminToken } from './api-helpers';

// Opt-in override that forces every compare request onto one comparison backend
// (e.g. E2E_COMPARE_BACKEND=polars). Only 08a-compare-bo-report.spec.ts ever opens
// the Advanced Options accordion and picks a backend explicitly; the other compare
// specs use the frontend default (frontend/features/compare.js's boBackend /
// fileBackend / sqlBackend = 'pandas') and several run through a *saved job*, where
// the backend travels as run_settings.comparison_backend rather than
// advanced.comparison_backend and has no per-run select at all. Patching the JSON
// body on the way out is the single choke point that covers all of those paths
// without editing any spec, the frontend, or the API. Unset => not installed at all,
// so default runs are byte-for-byte what they were before.
const COMPARE_BACKEND_OVERRIDE = process.env.E2E_COMPARE_BACKEND;

// Guards against a silent no-op: if the key paths below ever stop matching the
// payload shape, the suite would quietly run on pandas and still go green. Each
// patched request is appended here so the run can be audited afterwards — an
// in-memory counter would be useless because Playwright runs specs in a worker
// process separate from globalSetup/globalTeardown.
const OVERRIDE_LOG = path.join(__dirname, '..', '..', 'test-results', 'compare-backend-override.log');

function recordOverride(url: string, outcome: string) {
  try {
    appendFileSync(OVERRIDE_LOG, `${new Date().toISOString()}\t${outcome}\t${url}\n`);
  } catch {
    // test-results/ is created by Playwright before specs run; if it somehow
    // isn't there yet, losing an audit line must not fail the test itself.
  }
}

// Endpoints whose request schema declares `advanced: AdvancedCompareOptions` but
// whose frontend payload builder omits it, so the server silently falls back to
// AdvancedCompareOptions' own `comparison_backend="pandas"` default. /multi-file is
// the case in this repo: api/schemas.py's MultiFileCompareRequest accepts `advanced`
// and api/services/compare_service.py's run_multi_file_compare passes it straight to
// _build_engine, but frontend/features/compare.js's _buildMultiFilePayload never
// builds one. Injecting the object here is safe *because* the schema defaults it —
// every other field lands on exactly the value the server would have used anyway.
const ADVANCED_CAPABLE_PATHS = ['/api/compare/multi-file'];

function patchComparisonBackend(body: unknown, backend: string, url: string): boolean {
  if (!body || typeof body !== 'object') return false;
  const record = body as Record<string, unknown>;
  let patched = false;
  for (const key of ['advanced', 'run_settings'] as const) {
    const section = record[key];
    // Only rewrite when the object is already there — blindly inventing it would
    // send advanced options to endpoints whose schema rejects them.
    if (section && typeof section === 'object' && 'comparison_backend' in section) {
      (section as Record<string, unknown>).comparison_backend = backend;
      patched = true;
    }
  }
  if (!patched && ADVANCED_CAPABLE_PATHS.some((p) => url.includes(p))) {
    const existing = record.advanced;
    const advanced = existing && typeof existing === 'object' ? (existing as Record<string, unknown>) : {};
    advanced.comparison_backend = backend;
    record.advanced = advanced;
    patched = true;
  }
  return patched;
}

async function installCompareBackendOverride(page: Page, backend: string) {
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const raw = request.postData();
    if (!raw) return route.continue();
    let body: unknown;
    try {
      body = JSON.parse(raw);
    } catch {
      // Multipart uploads and form posts aren't compare payloads — pass through.
      return route.continue();
    }
    if (!patchComparisonBackend(body, backend, request.url())) {
      // A compare endpoint that carried no backend key is worth recording too:
      // it means that scenario cannot be steered onto the override at all, which
      // is exactly the blind spot this audit trail exists to surface.
      if (request.url().includes('/api/compare/')) recordOverride(request.url(), 'NO-BACKEND-KEY');
      return route.continue();
    }
    recordOverride(request.url(), backend);
    return route.continue({ postData: JSON.stringify(body) });
  });
}

// Worker-scoped: reads the admin token once per worker, not once per test — this
// fixture is only ever used by the `chromium` project (see playwright.config.ts's
// `dependencies: ['setup']`), which always starts after the `setup` project
// (00-auth-setup.spec.ts) has already bootstrapped the token and written it to
// tests/e2e/.admin-token.json via primeAdminToken(). bootstrapAdminToken() reads
// that file rather than racing to POST /api/tokens itself — the backend only
// force-admins the very first unauthenticated token creation ever made against an
// empty DB, so by the time any test using this fixture runs, that one-time window
// is already spent by 00-auth-setup.spec.ts, on purpose.
export const test = base.extend<{ authedPage: Page }, { adminToken: string }>({
  adminToken: [
    async ({}, use) => {
      const token = await bootstrapAdminToken();
      await use(token);
    },
    { scope: 'worker' },
  ],
  authedPage: async ({ page, adminToken }, use) => {
    await page.addInitScript((token) => {
      window.sessionStorage.setItem('etl_token', token);
    }, adminToken);
    if (COMPARE_BACKEND_OVERRIDE) {
      await installCompareBackendOverride(page, COMPARE_BACKEND_OVERRIDE);
    }
    await page.goto('/');
    await expect(page.locator('[data-testid="auth-status-connected"]')).toBeVisible();
    await use(page);
  },
});

export { expect };
