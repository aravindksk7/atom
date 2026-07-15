import { defineConfig, devices } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const PORT = 8055;
export const BASE_URL = `http://127.0.0.1:${PORT}`;

// Playwright evaluates `webServer.env` when this config module loads — before
// `globalSetup` runs — so the throwaway DB path must be computed here, not in
// global-setup.ts, or the server would boot against ETL_DATABASE_URL=''
// (falling back to the real on-disk DB). global-setup.ts and global-teardown.ts
// read process.env.E2E_DATABASE_URL / E2E_DB_DIR set here (same Node process).
const dbDir = mkdtempSync(path.join(tmpdir(), 'atom-e2e-'));
const dbPath = path.join(dbDir, 'e2e.db');
process.env.E2E_DATABASE_URL = `sqlite:///${dbPath.replace(/\\/g, '/')}`;
process.env.E2E_DB_DIR = dbDir;

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false, // spec files share one backend/DB — run serially across files
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  globalSetup: require.resolve('./tests/e2e/global-setup.ts'),
  globalTeardown: require.resolve('./tests/e2e/global-teardown.ts'),
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  // Playwright does NOT guarantee alphabetical/filename-order execution across spec
  // files — confirmed empirically in this repo: even with fullyParallel:false and
  // workers:1, `--list` ordered a later-created file ahead of `00-auth-setup.spec.ts`.
  // 00-auth-setup.spec.ts's first test consumes the backend's one-time, unauthenticated
  // token-bootstrap slot (see api/routes/tokens.py's `count() == 0` check) — every other
  // spec file needs that to have already happened. The `dependencies` mechanism below is
  // Playwright's actual supported way to guarantee this (the standard "auth setup"
  // pattern): the `setup` project always runs to completion before `chromium` starts,
  // regardless of file-discovery order.
  projects: [
    { name: 'setup', testMatch: /00-auth-setup\.spec\.ts/ },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      dependencies: ['setup'],
      testIgnore: /00-auth-setup\.spec\.ts/,
    },
  ],
  webServer: {
    command: `python -m uvicorn api.main:app --host 127.0.0.1 --port ${PORT}`,
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      ETL_DATABASE_URL: process.env.E2E_DATABASE_URL,
    },
  },
});
