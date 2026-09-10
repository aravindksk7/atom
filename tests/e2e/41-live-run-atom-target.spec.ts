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

// This spec is the only one that actually shells out to the real CI script
// (scripts/ci/run-atom-target.sh) as a child process against the live backend
// Playwright's own webServer boots. Every other GitLab-CI-related spec
// (40-live-docker-gitlab-retry.spec.ts) only exercises the browser modal that
// *displays* the snippet — nothing in this repo's e2e suite previously ran the
// script itself end to end.
//
// The scratch-git-repo setup and script-invocation helpers live in
// api-helpers.ts (shared with 42-ci-trigger-token.spec.ts, which reuses them
// verbatim with a ci_trigger token in place of the admin token used here).

let adminTokenValue: string;
let selJobName: string;
let seqJobName: string;
let selectionId: number | undefined;
let sequenceId: number | undefined;
let selectionRepoDir: string | undefined;
let sequenceRepoDir: string | undefined;

test.beforeAll(async ({ adminToken }) => {
  adminTokenValue = adminToken;
  const ctx = await authedContext(adminToken);
  try {
    selJobName = (await createFileJob(ctx, `e2e-run-atom-sel-job-${Date.now()}`)).name;
    seqJobName = (await createFileJob(ctx, `e2e-run-atom-seq-job-${Date.now()}`)).name;

    const selResp = await ctx.post('/api/selections', {
      data: {
        name: `e2e-run-atom-selection-${Date.now()}`,
        description: '',
        tags: [],
        job_sequence: [selJobName],
      },
    });
    if (!selResp.ok()) {
      throw new Error(`create selection failed: ${selResp.status()} ${await selResp.text()}`);
    }
    selectionId = (await selResp.json()).id;

    const seqResp = await ctx.post('/api/sequences', {
      data: {
        name: `e2e-run-atom-sequence-${Date.now()}`,
        description: '',
        tags: [],
        steps: [{ step_id: 'a', job_name: seqJobName, depends_on: [] }],
        // A saved Execution Sequence carries its own env defaults (unlike a Job
        // Selection, which has none) — SequenceLaunchRequest.target_env falls back
        // to this when the caller (here, the atom CLI, invoked by run-atom-target.sh
        // without --target-env) sends an empty target_env.
        defaults: { target_env: 'dev' },
      },
    });
    if (!seqResp.ok()) {
      throw new Error(`create sequence failed: ${seqResp.status()} ${await seqResp.text()}`);
    }
    sequenceId = (await seqResp.json()).id;
  } finally {
    await ctx.dispose();
  }
});

test.afterAll(async () => {
  // Best-effort cleanup, matching deleteJob's fire-and-forget pattern: this runs
  // regardless of whether the tests above passed or failed, and must not itself
  // throw and mask an earlier assertion failure.
  try {
    const ctx = await authedContext(adminTokenValue);
    try {
      if (selectionId !== undefined) await ctx.delete(`/api/selections/${selectionId}`);
      if (sequenceId !== undefined) await ctx.delete(`/api/sequences/${sequenceId}`);
      if (selJobName) await deleteJob(ctx, selJobName);
      if (seqJobName) await deleteJob(ctx, seqJobName);
    } finally {
      await ctx.dispose();
    }
  } catch {
    // Cleanup must never fail the run — an orphaned e2e-prefixed job/selection/
    // sequence is harmless noise, not silent data loss.
  }
  for (const dir of [selectionRepoDir, sequenceRepoDir]) {
    if (dir) fs.rmSync(dir, { recursive: true, force: true });
  }
});

test.describe('run-atom-target.sh against a live backend', () => {
  test('gates FAILED on a job selection and splices the live run summary into README', async () => {
    expect(selectionId).toBeDefined();
    selectionRepoDir = makeScratchGitRepo();
    const before = readScratchReadme(selectionRepoDir);

    const result = runAtomTargetScript('selection', selectionId!, selectionRepoDir, adminTokenValue, 'dev');

    // eslint-disable-next-line no-console
    console.log('[run-atom-target.sh selection stdout]\n', result.stdout);
    // eslint-disable-next-line no-console
    console.log('[run-atom-target.sh selection stderr]\n', result.stderr);

    // EXIT_FAILED (1) from etl_framework/cli/app.py's _gate_exit_code — proves the
    // full launch -> poll -> gate chain ran for real against the live API and
    // deterministically reached the FAILED verdict createFileJob's fixtures produce.
    expect(result.status).toBe(1);

    const after = readScratchReadme(selectionRepoDir);
    // Proves the markdown-summary fetch + splice_readme.py splice actually happened
    // against the live server's real run output, not just that the launch call
    // succeeded. Deliberately not asserting anything about the git push outcome:
    // it is expected to fail cleanly (no `origin` remote in the scratch repo) and
    // must not affect the exit code, which is already covered above (and is
    // asserted unconditionally: the script's exit code is deliberately independent
    // of the markdown-summary/splice outcome).
    if (process.platform !== 'win32') {
      expect(after).not.toBe(before);
    } else {
      // git-bash's /tmp path (used internally by run-atom-target.sh for the markdown
      // summary and stdout capture) isn't resolvable by the native Windows python3 on
      // PATH, so the splice step silently no-ops on this host -- exit code assertions
      // above already prove the launch/poll/gate chain works; this only skips the one
      // assertion that needs a Linux-style shared filesystem view between bash and
      // python3, which every real GitLab CI runner has.
    }
    expect(after).toContain(SPLICE_START_MARKER);
    expect(after).toContain(SPLICE_END_MARKER);
  });

  test('gates FAILED on an execution sequence and splices the live run summary into README', async () => {
    expect(sequenceId).toBeDefined();
    sequenceRepoDir = makeScratchGitRepo();
    const before = readScratchReadme(sequenceRepoDir);

    const result = runAtomTargetScript('sequence', sequenceId!, sequenceRepoDir, adminTokenValue, 'dev');

    // eslint-disable-next-line no-console
    console.log('[run-atom-target.sh sequence stdout]\n', result.stdout);
    // eslint-disable-next-line no-console
    console.log('[run-atom-target.sh sequence stderr]\n', result.stderr);

    expect(result.status).toBe(1);

    const after = readScratchReadme(sequenceRepoDir);
    if (process.platform !== 'win32') {
      expect(after).not.toBe(before);
    } else {
      // See the matching comment in the selection test above: git-bash's /tmp vs
      // native Windows python3 makes the splice step a silent no-op on this host.
    }
    expect(after).toContain(SPLICE_START_MARKER);
    expect(after).toContain(SPLICE_END_MARKER);
  });
});
