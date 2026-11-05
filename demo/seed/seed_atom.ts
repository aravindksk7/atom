// Playwright global setup: creates everything the demo needs in ATOM through its
// public API, before recording starts. The audience sees the finished sequence.
import { APIRequestContext, request as pwRequest } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

export const BUSINESS_DATE = '2026-06-03';
export const SEQUENCE_NAME = 'Nightly Regression';
export const IMPACTED_FIELD = 'fee_amount';
export const GITLAB_SIM_URL = 'http://gitlab-sim:8090';

export const ETLS: Record<1 | 2 | 3 | 4, string[]> = {
  1: etlRange(1, 1, 3),
  2: etlRange(2, 4, 9),
  3: etlRange(3, 10, 14),
  4: etlRange(4, 15, 18),
};

export const EXEC_JOBS = {
  watchTrigger: 'WATCH_INBOUND_TRIGGER_CRUSHFTP',
  fetchFlatFile: 'FETCH_POSITIONS_FROM_CRUSHFTP',
  validateSet1: 'VALIDATE_SET1_OUTPUTS',
  validateSet2: 'VALIDATE_SET2_OUTPUTS',
  validateSet3: 'VALIDATE_SET3_OUTPUTS',
  publishS3: 'PUBLISH_OUTPUTS_TO_S3',
  validateS3: 'VALIDATE_S3_PUBLISH',
} as const;

export const RECON_JOBS = {
  csv: 'RECON_CSV_OUTPUTS_VS_S3_BASELINE',
  xml: 'RECON_XML_OUTPUTS_VS_S3_BASELINE',
  published: 'RECON_S3_PUBLISHED_VS_LOCAL_OUTPUTS',
  trades: 'COMPARE_TRADES_POSTGRES_VS_ORACLE',
  balances: 'COMPARE_BALANCES_POSTGRES_VS_ORACLE',
  positionsFile: 'COMPARE_POSITIONS_POSTGRES_VS_FLAT_FILE',
  oracleFile: 'COMPARE_POSITIONS_ORACLE_VS_FLAT_FILE',
  boOrders: 'BO_SALES_ORDERS_VS_PROD',
  boDaily: 'BO_DAILY_SALES_VS_PROD',
  boSummary: 'BO_SALES_SUMMARY_VS_PROD',
} as const;

const BASE_URL = process.env.DEMO_BASE_URL || 'http://atom:8055';
const S3_PROFILE = 'AWS S3 Production';
const CRUSHFTP_PROFILE = 'CrushFTP Production';
const POSTGRES_URL = 'postgresql+psycopg://atom:demo_only@postgres:5432/qa';
const ORACLE_SIM_URL = 'postgresql+psycopg://atom:demo_only@oracle-sim:5432/prod';
const DATE = '{{run_date}}';
// The comparisons take seconds, too quick to see or talk through. See "Limits" in demo/README.md.
const RECON_WAIT_SECONDS = 12;
const OUTPUTS = `/demo-data/out/${DATE}`;

function etlRange(set: number, first: number, last: number): string[] {
  const names: string[] = [];
  for (let number = first; number <= last; number++) {
    names.push(`SET${set}_ETL_${String(number).padStart(3, '0')}`);
  }
  return names;
}

export function allEtls(): string[] {
  return [...ETLS[1], ...ETLS[2], ...ETLS[3], ...ETLS[4]];
}

async function post(ctx: APIRequestContext, url: string, data: unknown) {
  const response = await ctx.post(url, { data });
  if (!response.ok()) {
    throw new Error(`POST ${url} failed: ${response.status()} ${await response.text()}`);
  }
  return response.json();
}

async function createConfigs(ctx: APIRequestContext) {
  const regression = await post(ctx, '/api/configs', {
    name: 'Regression QA',
    env_name: 'qa',
    config_data: {
      db_host: 'unused', db_password: 'unused',
      ds_url: 'https://sapds:8444', ds_user: 'administrator', ds_password: 'Password1',
      ds_repository: 'DS_REPO', ds_cms_system: 'mock-cms', ds_verify_ssl: false,
      bo_url: 'https://sapbo:8443', bo_user: 'administrator', bo_password: 'Password1',
      bo_verify_ssl: false,
    },
  });
  const postgres = await post(ctx, '/api/configs', {
    name: 'POSTGRES_QA',
    env_name: 'qa',
    config_data: {
      db_type: 'postgresql', db_host: 'postgres', db_port: 5432, db_name: 'qa',
      db_user: 'atom', db_password: 'demo_only', db_sslmode: 'disable',
    },
  });
  // Simulated Oracle. See "Limits" in demo/README.md.
  const oracle = await post(ctx, '/api/configs', {
    name: 'ORACLE_PROD',
    env_name: 'prod',
    config_data: {
      db_type: 'postgresql', db_host: 'oracle-sim', db_port: 5432, db_name: 'prod',
      db_user: 'atom', db_password: 'demo_only', db_sslmode: 'disable',
    },
  });
  return { regression: regression.id, postgres: postgres.id, oracle: oracle.id };
}

async function createFileServers(ctx: APIRequestContext) {
  await post(ctx, '/api/file-servers', {
    name: S3_PROFILE,
    kind: 's3',
    aws_access_key_id: 'demo-s3-access',
    aws_secret_access_key: 'demo-s3-secret',
    endpoint_url: 'http://s3-sim:4566',
    region_name: 'us-east-1',
  });
  await post(ctx, '/api/file-servers', {
    name: CRUSHFTP_PROFILE,
    kind: 'crushftp',
    protocol: 'https',
    auth_method: 'password',
    endpoint_url: 'http://crushftp-sim:8080',
    username: 'atom',
    password: 'demo_only',
  });
}

function fileWatcherJob(
  name: string,
  description: string,
  location: Record<string, string>,
  maxTries = 5,
) {
  return {
    name,
    job_type: 'file_watcher',
    description,
    params: { location, poll_interval_seconds: 1, max_tries: maxTries },
  };
}

function multiFileJob(
  name: string,
  description: string,
  source: Record<string, string>,
  target: Record<string, string>,
) {
  return {
    name,
    job_type: 'reconciliation',
    description,
    key_columns: ['trade_id'],
    params: {
      source_mode: 'multi_file',
      file_mapping: { match_on: ['feed'], source, target, unmatched_policy: 'warn' },
    },
  };
}

function baselineJob(name: string, extension: 'csv' | 'xml') {
  return multiFileJob(
    name,
    `ATOM-generated ${extension.toUpperCase()} outputs vs Production baselines in S3`,
    { kind: 'local', root: OUTPUTS, pattern: `{feed:any}.${extension}` },
    {
      kind: 's3',
      root: `s3://prod-baselines/${DATE}/${extension}`,
      pattern: `{feed:any}.${extension}`,
      credentials_ref: S3_PROFILE,
    },
  );
}

function boJob(name: string, docId: string, reportId: string, baseline: string, key: string) {
  return {
    name,
    job_type: 'reconciliation',
    description: `Live QA report ${reportId} vs Production copy`,
    key_columns: [key],
    params: {
      source_mode: 'bo_live',
      report_id: docId,
      bo_report_id: reportId,
      format: 'csv',
      // Answers the report's date prompt with the business date chosen at launch.
      bo_parameters: [{ id: 0, type: 'DateTime', value: DATE }],
      target_file_path: `/demo-data/bo/${DATE}/${baseline}.csv`,
      source_file_label: 'QA BO report',
      target_file_label: 'Production BO report',
    },
  };
}

function sqlCompareJob(
  name: string,
  description: string,
  configs: { postgres: number; oracle: number },
  table: string,
  columns: string,
  key: string,
) {
  const query = (owner: string) =>
    `SELECT ${columns} FROM ${owner}.${table} WHERE BUSINESS_DATE = DATE '${DATE}'`;
  return {
    name,
    job_type: 'compare',
    description,
    params: {
      compare_type: 'sql',
      request: {
        config_id_a: configs.postgres,
        config_id_b: configs.oracle,
        label_a: 'QA Postgres',
        label_b: 'Production Oracle',
        key_columns: [key],
        query_a: query('QA_OWNER'),
        query_b: query('PROD_OWNER'),
      },
    },
  };
}

function positionsVsFlatFileJob(name: string, description: string, url: string, label: string) {
  return {
    name,
    job_type: 'compare',
    description,
    params: {
      compare_type: 'matrix',
      request: {
        label_a: label,
        label_b: 'Production flat file',
        key_columns: ['position_id'],
        source_a: {
          source_type: 'sql',
          connection_string: url,
          query_or_table:
            'SELECT position_id, book, currency, quantity, market_value '
            + `FROM positions WHERE business_date = DATE '${DATE}'`,
        },
        source_b: { source_type: 'file', file_path: `/demo-data/fetched/${DATE}/positions.csv` },
      },
    },
  };
}

async function createExecutionJobs(ctx: APIRequestContext) {
  // Parallel ETLs run longer, so the audience sees several RUNNING at the same moment.
  const parallelEtls = new Set([...ETLS[2], ...ETLS[3].slice(1, 4)]);
  for (const name of allEtls()) {
    await post(ctx, '/api/jobs', {
      name,
      job_type: 'ds_job',
      description: `SAP Data Services batch job ${name}`,
      params: {
        job_name: name,
        repository: 'DS_REPO',
        job_params: { $G_BUSINESS_DATE: DATE },
        poll_interval_s: parallelEtls.has(name) ? 15 : 3,
      },
    });
  }
  await post(ctx, '/api/jobs', fileWatcherJob(
    EXEC_JOBS.watchTrigger,
    'Wait for the upstream arrival signal on CrushFTP',
    { kind: 'crushftp', root: `/inbound/${DATE}`, pattern: 'trigger.done', credentials_ref: CRUSHFTP_PROFILE },
    // Long enough for the presenter to reach this point, however slow the pace.
    1800,
  ));
  await post(ctx, '/api/jobs', {
    name: EXEC_JOBS.fetchFlatFile,
    job_type: 'file_transfer',
    description: 'Download the Production positions extract from CrushFTP',
    params: {
      source: {
        kind: 'crushftp', root: `/prod/${DATE}`, pattern: 'positions.csv',
        credentials_ref: CRUSHFTP_PROFILE,
      },
      destination: { kind: 'local', root: `/demo-data/fetched/${DATE}` },
      on_exists: 'overwrite',
    },
  });
  await post(ctx, '/api/jobs', fileWatcherJob(
    EXEC_JOBS.validateSet1, 'Step validation: Set 1 wrote its output',
    { kind: 'local', root: OUTPUTS, pattern: 'gl_003.*' },
  ));
  await post(ctx, '/api/jobs', fileWatcherJob(
    EXEC_JOBS.validateSet2, 'Step validation: Set 2 wrote its output',
    { kind: 'local', root: OUTPUTS, pattern: 'gl_009.*' },
  ));
  await post(ctx, '/api/jobs', fileWatcherJob(
    EXEC_JOBS.validateSet3, 'Step validation: Set 3 wrote its output',
    { kind: 'local', root: OUTPUTS, pattern: 'gl_014.*' },
  ));
  await post(ctx, '/api/jobs', {
    name: EXEC_JOBS.publishS3,
    job_type: 'file_transfer',
    description: 'Publish the generated CSV outputs to AWS S3',
    params: {
      source: { kind: 'local', root: OUTPUTS, pattern: '*.csv' },
      destination: { kind: 's3', root: `s3://published-outputs/${DATE}`, credentials_ref: S3_PROFILE },
      on_exists: 'overwrite',
    },
  });
  await post(ctx, '/api/jobs', fileWatcherJob(
    EXEC_JOBS.validateS3, 'Step validation: the outputs reached AWS S3',
    { kind: 's3', root: `s3://published-outputs/${DATE}`, pattern: 'gl_016.*', credentials_ref: S3_PROFILE },
  ));
}

async function createReconciliationJobs(
  ctx: APIRequestContext,
  configs: { postgres: number; oracle: number },
) {
  await post(ctx, '/api/jobs', baselineJob(RECON_JOBS.csv, 'csv'));
  await post(ctx, '/api/jobs', baselineJob(RECON_JOBS.xml, 'xml'));
  await post(ctx, '/api/jobs', multiFileJob(
    RECON_JOBS.published,
    'The files in AWS S3 match what the ETLs generated',
    {
      kind: 's3', root: `s3://published-outputs/${DATE}`, pattern: '{feed:any}.csv',
      credentials_ref: S3_PROFILE,
    },
    { kind: 'local', root: OUTPUTS, pattern: '{feed:any}.csv' },
  ));
  await post(ctx, '/api/jobs', sqlCompareJob(
    RECON_JOBS.trades, 'Trades in QA Postgres vs Production Oracle', configs, 'GL_TRADES',
    'TRADE_ID, BOOK, CURRENCY, NOTIONAL, FEE_AMOUNT, TRADER', 'trade_id',
  ));
  await post(ctx, '/api/jobs', sqlCompareJob(
    RECON_JOBS.balances, 'Balances in QA Postgres vs Production Oracle', configs, 'GL_BALANCES',
    'ACCOUNT_ID, CURRENCY, BALANCE', 'account_id',
  ));
  await post(ctx, '/api/jobs', positionsVsFlatFileJob(
    RECON_JOBS.positionsFile, 'Positions in QA Postgres vs the Production flat file from CrushFTP',
    POSTGRES_URL, 'QA Postgres',
  ));
  await post(ctx, '/api/jobs', positionsVsFlatFileJob(
    RECON_JOBS.oracleFile, 'Positions in Production Oracle vs the Production flat file from CrushFTP',
    ORACLE_SIM_URL, 'Production Oracle',
  ));
  await post(ctx, '/api/jobs', boJob(RECON_JOBS.boOrders, '1001', 'rpt-sales', 'sales_orders', 'id'));
  await post(ctx, '/api/jobs', boJob(RECON_JOBS.boDaily, '1003', 'rpt-daily-sales', 'daily_sales', 'id'));
  await post(ctx, '/api/jobs', boJob(RECON_JOBS.boSummary, '1001', 'rpt-sales-summary', 'sales_summary', 'metric'));
}

type StartRule = 'all_success' | 'all_done';

function serialStage(jobNames: string[], startRule: StartRule = 'all_success') {
  return {
    mode: 'serial',
    steps: jobNames.map((job, index) => ({
      step_id: job,
      job_name: job,
      trigger_rule: index === 0 ? startRule : 'all_success',
    })),
  };
}

function parallelStage(jobNames: string[], startRule: StartRule = 'all_success', waitSeconds = 0) {
  return {
    mode: 'parallel',
    steps: jobNames.map((job) => ({
      step_id: job, job_name: job, trigger_rule: startRule, wait_seconds: waitSeconds,
    })),
  };
}

async function createSequence(ctx: APIRequestContext, configId: number) {
  const r = RECON_JOBS;
  const sequence = await post(ctx, '/api/sequences', {
    name: SEQUENCE_NAME,
    description: 'Business date, 4 ETL sets in series, parallel and hybrid flows, reconciliation against Production',
    tags: ['regression', 'nightly'],
    defaults: {
      source_env: 'qa',
      target_env: 'prod',
      config_id: configId,
      run_settings: { use_live_connections: true, max_workers: 6 },
    },
    stages: [
      // Set 1, in series: 6 jobs.
      serialStage([EXEC_JOBS.watchTrigger, EXEC_JOBS.fetchFlatFile, ...ETLS[1], EXEC_JOBS.validateSet1]),
      // Set 2, in parallel: 6 ETLs together, then a validation step: 7 jobs.
      parallelStage(ETLS[2]),
      serialStage([EXEC_JOBS.validateSet2]),
      // Set 3, hybrid: series, then parallel, then series again: 6 jobs.
      serialStage([ETLS[3][0]]),
      parallelStage(ETLS[3].slice(1, 4)),
      serialStage([ETLS[3][4], EXEC_JOBS.validateSet3]),
      // Set 4, in series, publishing to AWS S3: 6 jobs.
      serialStage([...ETLS[4], EXEC_JOBS.publishS3, EXEC_JOBS.validateS3]),
      // Reconcile, 7 file and database comparisons side by side.
      parallelStage(
        [r.csv, r.xml, r.published, r.trades, r.balances, r.positionsFile, r.oracleFile],
        'all_success',
        RECON_WAIT_SECONDS,
      ),
      // A data difference is a real result, so BO comparison still runs after one. Within
      // this stage each report waits for the one before it, so an error stops what follows.
      serialStage([r.boOrders, r.boDaily, r.boSummary], 'all_done'),
    ],
  });
  return sequence.id as number;
}

/**
 * Earlier nightly runs, so the recorded run's bars have history to estimate from. Each job's
 * seconds are what it takes in this stack, so the percents move sensibly on screen. The file
 * watcher has none, because it waits for the presenter: its bar shows that ATOM has no estimate.
 */
function seedHistory(sequenceId: number): void {
  const r = RECON_JOBS;
  const jobs: Record<string, number> = {
    [EXEC_JOBS.watchTrigger]: 0,
    [EXEC_JOBS.fetchFlatFile]: 1,
    [EXEC_JOBS.validateSet1]: 1,
    [EXEC_JOBS.validateSet2]: 1,
    [EXEC_JOBS.validateSet3]: 1,
    [EXEC_JOBS.publishS3]: 4,
    [EXEC_JOBS.validateS3]: 3,
    [r.csv]: 9, [r.xml]: 9, [r.published]: 9, [r.trades]: 4, [r.balances]: 4,
    [r.positionsFile]: 4, [r.oracleFile]: 1, [r.boOrders]: 1, [r.boDaily]: 1, [r.boSummary]: 1,
  };
  const parallelEtls = new Set([...ETLS[2], ...ETLS[3].slice(1, 4)]);
  for (const name of allEtls()) jobs[name] = parallelEtls.has(name) ? 16 : 4;
  execFileSync('python', [path.join(__dirname, 'seed_history.py')], {
    input: JSON.stringify({
      sequence_id: sequenceId, sequence_name: SEQUENCE_NAME, nights: 5, run_seconds: 200, jobs,
    }),
    stdio: ['pipe', 'inherit', 'inherit'],
  });
}

export default async function seedAtom(): Promise<void> {
  const anonymous = await pwRequest.newContext({ baseURL: BASE_URL });
  const tokenResponse = await anonymous.post('/api/tokens', {
    data: { name: 'atom-demo', is_admin: true },
  });
  if (!tokenResponse.ok()) {
    throw new Error(
      `Could not create the demo token (${tokenResponse.status()}). ATOM is already seeded: `
      + 'reset with "docker compose -f docker-compose.demo.yml down -v" and record again.',
    );
  }
  const token = (await tokenResponse.json()).raw_token as string;
  await anonymous.dispose();
  // Workers inherit this process's environment.
  process.env.DEMO_TOKEN = token;

  const ctx = await pwRequest.newContext({
    baseURL: BASE_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
  });
  try {
    // The BO date prompt converts through this timezone.
    const settings = await ctx.put('/api/settings', { data: { timezone: 'UTC' } });
    if (!settings.ok()) throw new Error(`PUT /api/settings failed: ${await settings.text()}`);

    // A launch override only applies to a variable that exists. The default differs from
    // the demo date, so the first run proves the date typed at launch is the one used.
    const variable = await post(ctx, '/api/variables', {
      name: 'run_date',
      var_type: 'date',
      default_value: '2026-01-01',
      description: 'Business date for the regression run',
    });
    process.env.DEMO_RUN_DATE_VARIABLE_ID = String(variable.id);

    const configs = await createConfigs(ctx);
    await createFileServers(ctx);
    await createExecutionJobs(ctx);
    await createReconciliationJobs(ctx, configs);
    const sequenceId = await createSequence(ctx, configs.regression);
    seedHistory(sequenceId);

    // The simulated GitLab runner authenticates with a scoped CI token, as a real one would.
    const ciToken = await post(ctx, '/api/tokens', { name: 'gitlab-ci', role: 'ci_trigger' });
    const configured = await ctx.post(`${GITLAB_SIM_URL}/api/configure`, {
      data: { token: ciToken.raw_token, target_id: sequenceId },
    });
    if (!configured.ok()) throw new Error(`Configuring the GitLab simulator failed: ${await configured.text()}`);
  } finally {
    await ctx.dispose();
  }
}
