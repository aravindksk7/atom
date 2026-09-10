import { APIRequestContext, request as pwRequest } from '@playwright/test';
import { spawnSync, SpawnSyncReturns } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { BASE_URL } from '../../playwright.config';

// File-based (not in-memory) cache: the `setup` Playwright project (00-auth-setup.spec.ts)
// and the `chromium` project (everything else, via `dependencies: ['setup']` in
// playwright.config.ts) run in SEPARATE worker processes even though each project itself
// uses workers:1 — an in-memory module-level variable would not survive that process
// boundary. The backend also only allows ONE unauthenticated bootstrap POST /api/tokens per
// DB (api/routes/tokens.py, `count() == 0` check); 00-auth-setup.spec.ts's first test
// deliberately consumes that one-time bootstrap through the real UI (that's the behavior
// under test) and calls primeAdminToken() with the token it captured from the page, writing
// it to this file so bootstrapAdminToken() — called from every other spec file via
// fixtures.ts's `adminToken` fixture — returns the already-known token instead of attempting
// a second, doomed bootstrap request.
const TOKEN_CACHE_FILE = path.join(__dirname, '.admin-token.json');

/** See the cache comment above bootstrapAdminToken(). Call this after obtaining an admin
 * token through a path other than bootstrapAdminToken() itself (e.g. the UI bootstrap flow). */
export function primeAdminToken(token: string): void {
  fs.writeFileSync(TOKEN_CACHE_FILE, JSON.stringify({ token }));
}

export async function bootstrapAdminToken(): Promise<string> {
  if (fs.existsSync(TOKEN_CACHE_FILE)) {
    return (JSON.parse(fs.readFileSync(TOKEN_CACHE_FILE, 'utf-8')).token) as string;
  }
  const ctx = await pwRequest.newContext({ baseURL: BASE_URL });
  try {
    const resp = await ctx.post('/api/tokens', {
      data: { name: 'e2e-admin', is_admin: true },
    });
    if (!resp.ok()) {
      throw new Error(`bootstrap token creation failed: ${resp.status()} ${await resp.text()}`);
    }
    const body = await resp.json();
    const token = body.raw_token as string;
    primeAdminToken(token);
    return token;
  } finally {
    await ctx.dispose();
  }
}

export function authedContext(token: string): Promise<APIRequestContext> {
  return pwRequest.newContext({
    baseURL: BASE_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
  });
}

const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');

/**
 * Creates a file-mode reconciliation job comparing fixtures/data/source.csv vs
 * target.csv on key column `id`. Running this job is deterministic: it always
 * reaches status FAILED with exactly 1 value_diff (id=2, amount 50.00->55.00),
 * 1 missing_in_target (id=3), 1 missing_in_source (id=4) — see those two CSVs.
 * Requires playwright.config.ts's SERVER_FILE_ALLOWED_DIRS to include
 * fixtures/data, or the run errors with "Invalid file path" instead.
 */
export async function createFileJob(ctx: APIRequestContext, name: string) {
  const resp = await ctx.post('/api/jobs', {
    data: {
      name,
      job_type: 'reconciliation',
      key_columns: ['id'],
      params: {
        source_mode: 'files',
        source_file_path: path.join(FIXTURE_DIR, 'source.csv'),
        target_file_path: path.join(FIXTURE_DIR, 'target.csv'),
      },
    },
  });
  if (!resp.ok()) throw new Error(`createFileJob(${name}) failed: ${resp.status()} ${await resp.text()}`);
  return resp.json();
}

/**
 * Creates a file-mode reconciliation job comparing fixtures/data/gate_ok_source.csv vs
 * gate_ok_target.csv on key column `id`. The two files are byte-identical, so running
 * this job always reaches status PASSED with zero mismatches — the counterpart to
 * createFileJob's deterministic FAILED case, used where a test needs a PROMOTE-eligible
 * job (e.g. WAP gate evaluation).
 */
export async function createPassingFileJob(ctx: APIRequestContext, name: string) {
  const resp = await ctx.post('/api/jobs', {
    data: {
      name,
      job_type: 'reconciliation',
      key_columns: ['id'],
      params: {
        source_mode: 'files',
        source_file_path: path.join(FIXTURE_DIR, 'gate_ok_source.csv'),
        target_file_path: path.join(FIXTURE_DIR, 'gate_ok_target.csv'),
      },
    },
  });
  if (!resp.ok()) throw new Error(`createPassingFileJob(${name}) failed: ${resp.status()} ${await resp.text()}`);
  return resp.json();
}

/**
 * Creates a multi_file reconciliation job pairing fixtures/data/multi_source/*.csv
 * against fixtures/data/multi_target/*.csv on filename token {region}, key column
 * `id`. Deterministic: region=east is byte-identical (PASSED pair), region=west has
 * a changed `amount` (FAILED pair) — so the job's aggregate status is always FAILED
 * with mismatch_summary.pairs_total=2, pairs_passed=1, pairs_failed=1. See those
 * four CSVs under fixtures/data/multi_source and multi_target.
 */
export async function createMultiFileJob(ctx: APIRequestContext, name: string) {
  const resp = await ctx.post('/api/jobs', {
    data: {
      name,
      job_type: 'reconciliation',
      key_columns: ['id'],
      params: {
        source_mode: 'multi_file',
        file_mapping: {
          strategy: 'explicit',
          match_on: ['region'],
          source: {
            kind: 'local',
            root: path.join(FIXTURE_DIR, 'multi_source'),
            pattern: 'sales_{region}.csv',
          },
          target: {
            kind: 'local',
            root: path.join(FIXTURE_DIR, 'multi_target'),
            pattern: 'financials_{region}.csv',
          },
        },
      },
    },
  });
  if (!resp.ok()) throw new Error(`createMultiFileJob(${name}) failed: ${resp.status()} ${await resp.text()}`);
  return resp.json();
}

// Intentionally fire-and-forget (unlike the create* helpers above): this runs from
// afterAll/afterEach cleanup blocks, where throwing on a failed delete would mask
// the actual test failure that's already being reported. A failed cleanup here
// leaves an orphaned e2e-prefixed job, which is harmless noise, not silent data loss.
export async function deleteJob(ctx: APIRequestContext, name: string) {
  await ctx.delete(`/api/jobs/${encodeURIComponent(name)}`);
}

export async function triggerRun(ctx: APIRequestContext, jobNames: string[], configId?: number) {
  const data: Record<string, unknown> = { source_env: 'dev', target_env: 'dev', job_names: jobNames };
  if (configId !== undefined) data.config_id = configId;
  const resp = await ctx.post('/api/runs', { data });
  if (!resp.ok()) throw new Error(`triggerRun failed: ${resp.status()} ${await resp.text()}`);
  return resp.json(); // { run_id, status }
}

export async function waitForTerminal(ctx: APIRequestContext, runId: string, timeoutMs = 30_000) {
  const terminal = new Set(['PASSED', 'FAILED', 'SLOW', 'ERROR', 'COMPLETED', 'CANCELLED']);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const resp = await ctx.get(`/api/runs/${runId}/status`);
    const body = await resp.json();
    if (terminal.has(String(body.status).toUpperCase())) return body;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`run ${runId} did not reach a terminal status within ${timeoutMs}ms`);
}

/** Creates a job, runs it, waits for completion. Returns the terminal run_id. */
export async function seedBaselineRun(ctx: APIRequestContext, namePrefix: string) {
  const jobName = `${namePrefix}-job-${Date.now()}`;
  await createFileJob(ctx, jobName);
  const { run_id } = await triggerRun(ctx, [jobName]);
  await waitForTerminal(ctx, run_id);
  return { jobName, runId: run_id as string };
}

export async function createConfig(ctx: APIRequestContext, name: string, envName: string, configData: Record<string, unknown>) {
  const resp = await ctx.post('/api/configs', { data: { name, env_name: envName, config_data: configData } });
  if (!resp.ok()) throw new Error(`createConfig(${name}) failed: ${resp.status()} ${await resp.text()}`);
  return resp.json(); // includes .id
}

// Fire-and-forget cleanup — see deleteJob's comment above for the rationale.
export async function deleteConfig(ctx: APIRequestContext, id: number) {
  await ctx.delete(`/api/configs/${id}`);
}

// --- scripts/ci/run-atom-target.sh scratch-repo helpers -------------------
//
// Shared by 41-live-run-atom-target.spec.ts and 42-ci-trigger-token.spec.ts —
// both actually shell out to the real run-atom-target.sh as a child process
// against the live backend Playwright's own webServer boots. Extracted here
// so there is exactly one scratch-git-repo implementation instead of two
// copies drifting apart.

export const RUN_ATOM_TARGET_SCRIPT_PATH = path.resolve(__dirname, '..', '..', 'scripts', 'ci', 'run-atom-target.sh');
export const SPLICE_START_MARKER = '<!-- ATOM:JOB-STATUS:START -->';
export const SPLICE_END_MARKER = '<!-- ATOM:JOB-STATUS:END -->';

/**
 * Creates a brand-new, throwaway git repository (NOT this worktree) seeded with a
 * README.md carrying the same ATOM:JOB-STATUS markers as the real repo root README
 * (see scripts/ci/splice_readme.py's START_MARKER/END_MARKER constants). This is
 * deliberately isolated from the worktree's own .git — run-atom-target.sh does real
 * `git commit`/`git push` as part of its normal flow, and must never be allowed to
 * touch this actual repo. The scratch repo has no `origin` remote, so the script's
 * push (and its retry-after-rebase) fail cleanly and non-fatally, which is the
 * script's own designed degradation path, not a bypass of it.
 */
export function makeScratchGitRepo(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'atom-e2e-scratch-repo-'));
  const readme =
    '# Scratch Repo\n\n' +
    '## CI/CD Job Status\n\n' +
    `${SPLICE_START_MARKER}\n` +
    '_No CI-triggered run yet._\n' +
    `${SPLICE_END_MARKER}\n`;
  fs.writeFileSync(path.join(dir, 'README.md'), readme, 'utf-8');

  const git = (args: string[]) => {
    const res = spawnSync('git', args, { cwd: dir, encoding: 'utf-8' });
    if (res.status !== 0) {
      throw new Error(`git ${args.join(' ')} failed (scratch repo setup): ${res.stderr}`);
    }
  };
  git(['init', '-q', '-b', 'main']);
  git(['config', 'user.email', 'atom-e2e-scratch@localhost']);
  git(['config', 'user.name', 'atom-e2e-scratch']);
  git(['add', 'README.md']);
  git(['commit', '-q', '-m', 'seed scratch README']);
  return dir;
}

export function readScratchReadme(dir: string): string {
  return fs.readFileSync(path.join(dir, 'README.md'), 'utf-8');
}

/**
 * Runs the real run-atom-target.sh as a child process, cwd'd into the scratch git
 * repo so its git config/add/commit/push operate on that repo only. CI_COMMIT_REF_NAME
 * is deliberately set (unlike CI_COMMIT_SHA/CI_PIPELINE_URL, which the script falls
 * back to "unknown"/"" for via ${VAR:-...}): the script's git push line references
 * ${CI_COMMIT_REF_NAME} directly with no :- fallback, and the script runs under
 * `set -euo pipefail` (nounset), so leaving it completely unset would abort the
 * script on an "unbound variable" error rather than exercising the push-failure
 * degradation path this test wants to exercise.
 *
 * targetEnv is passed as the script's new optional 4th positional argument
 * (target_env). A Job Selection has no stored env default of its own (unlike an
 * Execution Sequence's SequenceDefaults.target_env), so its launch 422s for any
 * job type outside SINGLE_ENV_JOB_TYPES -- e.g. createFileJob's "reconciliation"
 * type -- unless target_env is passed explicitly here.
 *
 * `token` is deliberately a plain parameter rather than always the admin token:
 * 42-ci-trigger-token.spec.ts passes a freshly-minted ci_trigger token here to
 * prove the whole launch -> poll -> gate chain works end to end through a scoped
 * token against the real script, not just the admin token 41's tests use.
 */
export function runAtomTargetScript(
  targetType: 'selection' | 'sequence',
  targetId: number,
  cwd: string,
  token: string,
  targetEnv: string,
): SpawnSyncReturns<string> {
  const result = spawnSync('bash', [RUN_ATOM_TARGET_SCRIPT_PATH, targetType, String(targetId), 'dev', targetEnv], {
    cwd,
    env: {
      ...process.env,
      ATOM_API_URL: BASE_URL,
      ATOM_API_TOKEN: token,
      CI_COMMIT_REF_NAME: 'e2e-scratch-branch',
    },
    encoding: 'utf-8',
  });
  if (result.error && (result.error as NodeJS.ErrnoException).code === 'ENOENT') {
    throw new Error(
      `bash was not found on PATH when spawning run-atom-target.sh (spawnSync ENOENT): ${result.error.message}`,
    );
  }
  return result;
}
