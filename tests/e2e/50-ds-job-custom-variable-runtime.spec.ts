import https from 'node:https';
import { test, expect } from './fixtures';
import { authedContext, deleteJob, deleteConfig, waitForTerminal } from './api-helpers';

// Exercises the full chain the custom-runtime-variables design deliberately left
// uncovered by a browser e2e test (docs/superpowers/specs/2026-09-09-custom-runtime-variables-design.md
// §8: "descoped rather than added"): a global Custom Variable, overridden per-Config,
// referenced as a `{{name}}` placeholder inside a SAP DS job's Job Params (JSON) field,
// resolved at launch time, and substituted into the actual RunBatchJobRequest the SAP DS
// SOAP mock receives -- not just persisted as a literal string on the job definition.
//
// The SAP DS mock (docker/sapds-mock/server.py) has been extended with a debug-only
// GET /debug/last-run endpoint that echoes back the globalVariables it parsed from the
// most recent Run_Batch_Job call, purely so this test can observe what DSRestClient.
// trigger_job actually sent over the wire (api/services/run_executor.py's
// _build_case_ds_job) without atom's own API otherwise exposing the DS-side rid.

const liveBackends = process.env.E2E_LIVE_BACKENDS === '1';
const DS_MOCK_URL = 'https://127.0.0.1:18444';

function dsDebugLastRun(): Promise<{ run_id: string; job_name: string; variables: Record<string, string> }> {
  return new Promise((resolve, reject) => {
    https.get(`${DS_MOCK_URL}/debug/last-run`, { rejectUnauthorized: false }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        if (res.statusCode !== 200) {
          reject(new Error(`GET /debug/last-run -> ${res.statusCode}: ${data}`));
          return;
        }
        resolve(JSON.parse(data));
      });
    }).on('error', reject);
  });
}

test.describe('50 launch: a Config-level custom variable flows into a SAP DS job as a runtime global variable', () => {
  test.skip(!liveBackends, 'requires E2E_LIVE_BACKENDS=1 (docker-compose.integration.yml sapds mock)');

  test('a {{run_date}} placeholder in Job Params resolves to the Config override and reaches the DS SOAP call', async ({ authedPage, adminToken }) => {
    const varName = `e2e_run_date_${Date.now()}`;
    const overrideValue = '2026-09-20';
    const jobName = `e2e_ds_custom_var_${Date.now()}`;

    const ctx = await authedContext(adminToken);
    let varId: number;
    let cfgId: number;
    try {
      // Global default is deliberately different from the per-Config override below --
      // a pass here proves override precedence (design doc §4: global default -> per-
      // config override -> launch override), not just that *some* value made it through.
      const varResp = await ctx.post('/api/variables', {
        data: { name: varName, var_type: 'date', default_value: '2026-01-01', description: 'e2e run date' },
      });
      if (!varResp.ok()) throw new Error(`create variable failed: ${varResp.status()} ${await varResp.text()}`);
      varId = (await varResp.json()).id;

      const cfgResp = await ctx.post('/api/configs', {
        data: {
          name: `e2e-ds-custom-var-${Date.now()}`,
          env_name: 'dev',
          config_data: {
            db_host: 'unused', db_password: 'unused',
            ds_url: DS_MOCK_URL, ds_user: 'administrator', ds_password: 'Password1',
            ds_repository: 'DS_REPO', ds_cms_system: 'mock-cms', ds_verify_ssl: false,
            variables: { [varName]: overrideValue },
          },
        },
      });
      if (!cfgResp.ok()) throw new Error(`create config failed: ${cfgResp.status()} ${await cfgResp.text()}`);
      cfgId = (await cfgResp.json()).id;

      const jobResp = await ctx.post('/api/jobs', {
        data: {
          name: jobName,
          job_type: 'ds_job',
          params: { job_name: 'DS_NIGHTLY_LOAD', repository: 'DS_REPO' },
        },
      });
      if (!jobResp.ok()) throw new Error(`create ds_job failed: ${jobResp.status()} ${await jobResp.text()}`);
    } finally {
      await ctx.dispose();
    }

    try {
      // Edit the job through the same Settings-tab Job Params field a user would use,
      // entering the placeholder rather than a literal date.
      await authedPage.goto('/');
      await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
      await authedPage.locator(`[data-testid="job-row-${jobName}-edit-btn"]`).click();
      const modal = authedPage.locator('[data-testid="job-modal"]');
      await expect(modal).toBeVisible();
      await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
      await authedPage.locator('#a11y-launch-job-params-json-optional')
        .fill(`{"$G_RUN_DATE": "{{${varName}}}"}`);
      await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
      await expect(modal).toBeHidden();

      const runCtx = await authedContext(adminToken);
      try {
        // ds_job execution is gated behind run_settings.use_live_connections (RunExecutor
        // otherwise fails it fast with "ds_job jobs require live connections to be
        // enabled") -- api-helpers.ts's triggerRun() doesn't expose that field, so post
        // directly here instead of going through it.
        const triggerResp = await runCtx.post('/api/runs', {
          data: {
            source_env: 'dev', target_env: 'dev', job_names: [jobName], config_id: cfgId,
            run_settings: { use_live_connections: true },
          },
        });
        if (!triggerResp.ok()) throw new Error(`trigger run failed: ${triggerResp.status()} ${await triggerResp.text()}`);
        const { run_id } = await triggerResp.json();
        const result = await waitForTerminal(runCtx, run_id, 30_000);
        expect(String(result.status).toUpperCase()).toBe('PASSED');
      } finally {
        await runCtx.dispose();
      }

      // The real assertion: the mock's own record of the RunBatchJobRequest it received
      // shows the *resolved* config-override value, not the literal `{{varName}}` template
      // -- proving substitution happened before DSRestClient.trigger_job serialized it.
      const lastRun = await dsDebugLastRun();
      expect(lastRun.job_name).toBe('DS_NIGHTLY_LOAD');
      expect(lastRun.variables).toEqual({ $G_RUN_DATE: overrideValue });
    } finally {
      const cleanupCtx = await authedContext(adminToken);
      try {
        await deleteJob(cleanupCtx, jobName);
        await deleteConfig(cleanupCtx, cfgId!);
        await cleanupCtx.delete(`/api/variables/${varId!}`);
      } finally {
        await cleanupCtx.dispose();
      }
    }
  });
});
