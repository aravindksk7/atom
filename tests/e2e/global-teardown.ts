import { execSync } from 'node:child_process';
import { rmSync } from 'node:fs';
import path from 'node:path';

const REPO_ROOT = path.resolve(__dirname, '../..');
const TOKEN_CACHE_FILE = path.join(__dirname, '.admin-token.json');

export default async function globalTeardown() {
  // Docker teardown and temp-dir cleanup are independent concerns — a failure in one
  // (e.g. a container that won't stop) must not skip the other.
  try {
    if (process.env.E2E_LIVE_BACKENDS === '1') {
      console.log('[global-teardown] stopping docker-compose.integration.yml services...');
      execSync('docker compose -f docker-compose.integration.yml down -v', {
        cwd: REPO_ROOT,
        stdio: 'inherit',
        timeout: 60_000,
      });
    }
  } finally {
    const dbDir = process.env.E2E_DB_DIR;
    if (dbDir) {
      try {
        rmSync(dbDir, { recursive: true, force: true });
      } catch (err) {
        // Windows sometimes still holds a lock on the sqlite file for a moment
        // after the webServer process is killed (EBUSY). It's a temp dir either
        // way (OS temp cleanup reclaims it eventually) — don't fail the whole
        // test run over a leftover file we can't delete right now.
        console.warn(`[global-teardown] could not remove ${dbDir}: ${(err as Error).message}`);
      }
    }
    rmSync(TOKEN_CACHE_FILE, { force: true });
  }
}
