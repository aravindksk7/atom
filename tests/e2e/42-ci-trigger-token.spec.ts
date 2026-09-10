import fs from 'node:fs';
import { test, expect } from './fixtures';
import {
  authedContext,
  createFileJob,
  deleteJob,
  makeScratchGitRepo,
  readScratchReadme,
  runAtomTargetScript,
  SPLICE_START_MARKER,
  SPLICE_END_MARKER,
} from './api-helpers';

// Proves a real `ci_trigger` token (Task 1-4's scoped-role token type) can drive
// scripts/ci/run-atom-target.sh end to end against the live backend Playwright's
// own webServer boots -- the same subprocess-invocation technique
// 41-live-run-atom-target.spec.ts uses for a `full` (admin) token, but with the
// script's ATOM_API_TOKEN swapped for a freshly-minted ci_trigger token. This is
// a stronger guarantee than tests/unit/test_auth.py's route-allowlist unit tests:
// it proves the whole launch -> poll -> gate chain (POST .../launch, GET
// runs/.../status, GET runs/.../markdown-summary) is actually reachable by a
// ci_trigger token in combination, against a real running server, not just that
// each route is allowlisted in isolation.

let adminTokenValue: string;
let jobName: string;
let selectionId: number | undefined;
let selectionRepoDir: string | undefined;
let ciTriggerTokenId: number | undefined;
let ciTriggerToken: string;

test.beforeAll(async ({ adminToken }) => {
  adminTokenValue = adminToken;
  const ctx = await authedContext(adminToken);
  try {
    jobName = (await createFileJob(ctx, `e2e-ci-trigger-job-${Date.now()}`)).name;

    const selResp = await ctx.post('/api/selections', {
      data: {
        name: `e2e-ci-trigger-selection-${Date.now()}`,
        description: '',
        tags: [],
        job_sequence: [jobName],
      },
    });
    if (!selResp.ok()) {
      throw new Error(`create selection failed: ${selResp.status()} ${await selResp.text()}`);
    }
    selectionId = (await selResp.json()).id;

    const tokenResp = await ctx.post('/api/tokens', {
      data: { name: `e2e-ci-trigger-${Date.now()}`, role: 'ci_trigger' },
    });
    if (!tokenResp.ok()) {
      throw new Error(`create ci_trigger token failed: ${tokenResp.status()} ${await tokenResp.text()}`);
    }
    const tokenBody = await tokenResp.json();
    ciTriggerTokenId = tokenBody.id;
    ciTriggerToken = tokenBody.raw_token;
  } finally {
    await ctx.dispose();
  }
});

test.afterAll(async () => {
  // Best-effort cleanup, matching deleteJob's fire-and-forget pattern: this runs
  // regardless of whether the tests above passed or failed, and must not itself
  // throw and mask an earlier assertion failure. The minted ci_trigger token IS
  // revoked here (unlike leaving it orphaned): nothing in the code enforces that
  // a future run of this suite always targets a throwaway DB, so an unconditional
  // revoke keeps this test from ever being the reason a live, non-expiring
  // ci_trigger credential survives a run against a longer-lived environment.
  try {
    const ctx = await authedContext(adminTokenValue);
    try {
      if (selectionId !== undefined) await ctx.delete(`/api/selections/${selectionId}`);
      if (jobName) await deleteJob(ctx, jobName);
      if (ciTriggerTokenId !== undefined) await ctx.delete(`/api/tokens/${ciTriggerTokenId}`);
    } finally {
      await ctx.dispose();
    }
  } catch {
    // Cleanup must never fail the run — an orphaned e2e-prefixed job/selection/
    // token is harmless noise, not silent data loss.
  }
  if (selectionRepoDir) fs.rmSync(selectionRepoDir, { recursive: true, force: true });
});

test.describe('a ci_trigger token against a live backend', () => {
  test('drives run-atom-target.sh end to end', async () => {
    expect(selectionId).toBeDefined();

    // Run the REAL script as a subprocess, authenticated as the ci_trigger token
    // -- not a direct API call, which would only prove the route allowlist works
    // in isolation.
    selectionRepoDir = makeScratchGitRepo();
    const before = readScratchReadme(selectionRepoDir);

    const result = runAtomTargetScript('selection', selectionId!, selectionRepoDir, ciTriggerToken, 'dev');

    // eslint-disable-next-line no-console
    console.log('[run-atom-target.sh ci_trigger stdout]\n', result.stdout);
    // eslint-disable-next-line no-console
    console.log('[run-atom-target.sh ci_trigger stderr]\n', result.stderr);

    // EXIT_FAILED (1) from etl_framework/cli/app.py's _gate_exit_code -- proves the
    // full launch -> poll -> gate chain ran for real, through a ci_trigger token,
    // against the live API, and deterministically reached the FAILED verdict
    // createFileJob's fixtures produce.
    expect(result.status).toBe(1);

    const after = readScratchReadme(selectionRepoDir);
    if (process.platform !== 'win32') {
      expect(after).not.toBe(before);
    } else {
      // See the matching comment in 41-live-run-atom-target.spec.ts: git-bash's
      // /tmp vs native Windows python3 makes the splice step a silent no-op on
      // this host -- the exit code assertion above already proves the
      // launch/poll/gate chain works through the ci_trigger token.
    }
    expect(after).toContain(SPLICE_START_MARKER);
    expect(after).toContain(SPLICE_END_MARKER);
  });

  test('is still denied outside its scope', async () => {
    // The SAME ci_trigger token minted in beforeAll, used directly (not through
    // the script), is still denied outside its allowlisted scope.
    const ciTriggerCtx = await authedContext(ciTriggerToken);
    try {
      const configsResp = await ciTriggerCtx.get('/api/configs');
      expect(configsResp.status()).toBe(403);
    } finally {
      await ciTriggerCtx.dispose();
    }
  });
});
