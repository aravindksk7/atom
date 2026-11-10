#!/usr/bin/env node
// Tear down every Docker resource a test run starts and hand the memory back to
// Windows.
//
//   node scripts/docker-test-cleanup.js                 clean up now
//   node scripts/docker-test-cleanup.js run <service>   run a docker-compose.test.yml
//                                                       service, then clean up even
//                                                       when the tests fail
//
// tests/e2e/global-teardown.ts calls cleanup() after every Playwright run.
// Set ATOM_KEEP_DOCKER=1 to leave everything running, e.g. to inspect a failure.
'use strict';

const { spawnSync } = require('node:child_process');
const path = require('node:path');

const REPO_ROOT = path.resolve(__dirname, '..');
const COMPOSE_FILES = ['docker-compose.integration.yml', 'docker-compose.test.yml'];
// Pinned so the reclaim step never pulls a moving tag.
const RECLAIM_IMAGE = 'alpine:3.20.3';

function docker(args, { quiet = false, timeout = 120_000 } = {}) {
  const result = spawnSync('docker', args, {
    cwd: REPO_ROOT,
    encoding: 'utf-8',
    stdio: quiet ? 'pipe' : 'inherit',
    timeout,
  });
  return { ok: result.status === 0, stdout: result.stdout || '', stderr: result.stderr || '' };
}

function step(label, fn) {
  // Each step is independent: one failing must not skip the rest.
  try {
    fn();
  } catch (err) {
    console.warn(`[docker-cleanup] ${label} failed: ${err.message}`);
  }
}

function cleanup() {
  if (process.env.ATOM_KEEP_DOCKER === '1') {
    console.log('[docker-cleanup] ATOM_KEEP_DOCKER=1, leaving containers running');
    return;
  }
  for (const file of COMPOSE_FILES) {
    step(`down ${file}`, () => {
      docker(['compose', '-f', file, 'down', '-v', '--remove-orphans', '--timeout', '10'], { quiet: true });
    });
  }
  step('remove floci-spawned containers', () => {
    // floci starts sibling containers itself (e.g. floci-aws-duck for Athena), outside
    // compose, so `down` never sees them. It labels each one floci=true.
    const ids = docker(['ps', '-aq', '--filter', 'label=floci=true'], { quiet: true }).stdout.split(/\s+/).filter(Boolean);
    if (ids.length) docker(['rm', '-f', '-v', ...ids], { quiet: true });
  });
  step('release memory', () => {
    // Docker Desktop's WSL VM keeps freed container memory as page cache, so vmmemWSL
    // stays large after the containers are gone. Dropping the cache lets WSL's free
    // page reporting return it to Windows.
    const r = docker([
      'run', '--rm', '--privileged', '--pid=host', RECLAIM_IMAGE,
      'nsenter', '-t', '1', '-m', '--', 'sh', '-c', 'sync && echo 3 > /proc/sys/vm/drop_caches',
    ], { quiet: true });
    if (!r.ok) throw new Error(r.stderr.trim() || 'reclaim container failed');
  });
  console.log('[docker-cleanup] test containers removed and memory released');
}

module.exports = { cleanup };

if (require.main === module) {
  const [mode, service, ...rest] = process.argv.slice(2);
  if (mode === 'run') {
    if (!service) {
      console.error('usage: node scripts/docker-test-cleanup.js run <service> [args...]');
      process.exit(2);
    }
    const result = spawnSync('docker', ['compose', '-f', 'docker-compose.test.yml', 'run', '--rm', service, ...rest], {
      cwd: REPO_ROOT,
      stdio: 'inherit',
    });
    cleanup();
    process.exit(result.status ?? 1);
  }
  cleanup();
}
