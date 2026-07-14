import { execSync } from 'node:child_process';
import { rmSync } from 'node:fs';
import path from 'node:path';

const REPO_ROOT = path.resolve(__dirname, '../..');

export default async function globalTeardown() {
  if (process.env.E2E_LIVE_BACKENDS === '1') {
    console.log('[global-teardown] stopping docker-compose.integration.yml services...');
    execSync('docker compose -f docker-compose.integration.yml down -v', {
      cwd: REPO_ROOT,
      stdio: 'inherit',
    });
  }
  const dbDir = process.env.E2E_DB_DIR;
  if (dbDir) {
    rmSync(dbDir, { recursive: true, force: true });
  }
}
