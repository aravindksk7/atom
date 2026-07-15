import { APIRequestContext, request as pwRequest } from '@playwright/test';
import fs from 'node:fs';
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

// Intentionally fire-and-forget (unlike the create* helpers above): this runs from
// afterAll/afterEach cleanup blocks, where throwing on a failed delete would mask
// the actual test failure that's already being reported. A failed cleanup here
// leaves an orphaned e2e-prefixed job, which is harmless noise, not silent data loss.
export async function deleteJob(ctx: APIRequestContext, name: string) {
  await ctx.delete(`/api/jobs/${encodeURIComponent(name)}`);
}

export async function triggerRun(ctx: APIRequestContext, jobNames: string[]) {
  const resp = await ctx.post('/api/runs', {
    data: { source_env: 'dev', target_env: 'dev', job_names: jobNames },
  });
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
