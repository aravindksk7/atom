// Playwright global setup: creates everything the demo needs in ATOM through its
// public API, before recording starts. The audience sees the finished sequence.
import { APIRequestContext, request as pwRequest } from '@playwright/test';

export const BUSINESS_DATE = '2026-06-03';
export const SEQUENCE_NAME = 'Nightly Regression';
export const FAILING_ETL = 'SET3_ETL_075';
export const IMPACTED_FIELD = 'fee_amount';

export const JOBS = {
  fetchFlatFile: 'FETCH_POSITIONS_FROM_CRUSHFTP',
  reconCsv: 'RECON_CSV_OUTPUTS_VS_PROD',
  reconXml: 'RECON_XML_OUTPUTS_VS_PROD',
  oracleVsPostgres: 'COMPARE_POSTGRES_VS_ORACLE_PROD',
  postgresVsFlatFile: 'COMPARE_POSTGRES_VS_PROD_FLAT_FILE',
  boOrders: 'BO_SALES_ORDERS_VS_PROD',
  boSummary: 'BO_SALES_SUMMARY_VS_PROD',
  boDaily: 'BO_DAILY_SALES_VS_PROD',
} as const;

export const EMAIL_RECIPIENTS = [
  'qa-lead@atom-demo.example',
  'etl-support@atom-demo.example',
  'release-manager@atom-demo.example',
];

const BASE_URL = process.env.DEMO_BASE_URL || 'http://atom:8055';
const S3_PROFILE = 'AWS S3 Production Baselines';
const CRUSHFTP_PROFILE = 'CrushFTP Production';
const POSTGRES_URL = 'postgresql+psycopg://atom:demo_only@postgres:5432/qa';
const DATE = '{{run_date}}';

export function etlNames(): string[] {
  const names: string[] = [];
  for (let number = 1; number <= 100; number++) {
    const set = Math.floor((number - 1) / 25) + 1;
    names.push(`SET${set}_ETL_${String(number).padStart(3, '0')}`);
  }
  return names;
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

function multiFileJob(name: string, extension: 'csv' | 'xml') {
  return {
    name,
    job_type: 'reconciliation',
    description: `ATOM-generated ${extension.toUpperCase()} outputs vs Production baselines in S3`,
    key_columns: ['trade_id'],
    params: {
      source_mode: 'multi_file',
      source_file_label: 'ATOM Test output',
      target_file_label: 'Production baseline',
      file_mapping: {
        match_on: ['feed'],
        source: { kind: 'local', root: `/demo-data/out/${DATE}`, pattern: `{feed:any}.${extension}` },
        target: {
          kind: 's3',
          root: `s3://prod-baselines/${DATE}/${extension}`,
          pattern: `{feed:any}.${extension}`,
          credentials_ref: S3_PROFILE,
        },
        unmatched_policy: 'warn',
      },
    },
  };
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

async function createJobs(ctx: APIRequestContext, configs: { postgres: number; oracle: number }) {
  for (const name of etlNames()) {
    await post(ctx, '/api/jobs', {
      name,
      job_type: 'ds_job',
      description: `SAP Data Services batch job ${name}`,
      params: {
        job_name: name,
        repository: 'DS_REPO',
        job_params: { $G_BUSINESS_DATE: DATE },
        poll_interval_s: 0.25,
      },
    });
  }

  await post(ctx, '/api/jobs', {
    name: JOBS.fetchFlatFile,
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

  await post(ctx, '/api/jobs', multiFileJob(JOBS.reconCsv, 'csv'));
  await post(ctx, '/api/jobs', multiFileJob(JOBS.reconXml, 'xml'));

  const tradeColumns = 'TRADE_ID, BOOK, CURRENCY, NOTIONAL, FEE_AMOUNT, TRADER';
  await post(ctx, '/api/jobs', {
    name: JOBS.oracleVsPostgres,
    job_type: 'compare',
    description: 'Trades in QA Postgres vs Production Oracle',
    params: {
      compare_type: 'sql',
      request: {
        config_id_a: configs.postgres,
        config_id_b: configs.oracle,
        label_a: 'QA Postgres',
        label_b: 'Production Oracle',
        key_columns: ['trade_id'],
        query_a: `SELECT ${tradeColumns} FROM QA_OWNER.GL_TRADES WHERE BUSINESS_DATE = DATE '${DATE}'`,
        query_b: `SELECT ${tradeColumns} FROM PROD_OWNER.GL_TRADES WHERE BUSINESS_DATE = DATE '${DATE}'`,
      },
    },
  });

  await post(ctx, '/api/jobs', {
    name: JOBS.postgresVsFlatFile,
    job_type: 'compare',
    description: 'Positions in QA Postgres vs the Production flat file from CrushFTP',
    params: {
      compare_type: 'matrix',
      request: {
        label_a: 'QA Postgres',
        label_b: 'Production flat file',
        key_columns: ['position_id'],
        source_a: {
          source_type: 'sql',
          connection_string: POSTGRES_URL,
          query_or_table:
            'SELECT position_id, book, currency, quantity, market_value '
            + `FROM positions WHERE business_date = DATE '${DATE}'`,
        },
        source_b: { source_type: 'file', file_path: `/demo-data/fetched/${DATE}/positions.csv` },
      },
    },
  });

  await post(ctx, '/api/jobs', boJob(JOBS.boOrders, '1001', 'rpt-sales', 'sales_orders', 'id'));
  await post(ctx, '/api/jobs', boJob(JOBS.boSummary, '1001', 'rpt-sales-summary', 'sales_summary', 'metric'));
  await post(ctx, '/api/jobs', boJob(JOBS.boDaily, '1003', 'rpt-daily-sales', 'daily_sales', 'id'));
}

function serialStage(jobNames: string[], startRule: 'all_success' | 'all_done') {
  return {
    mode: 'serial',
    steps: jobNames.map((job, index) => ({
      step_id: job,
      job_name: job,
      trigger_rule: index === 0 ? startRule : 'all_success',
    })),
  };
}

function parallelStage(jobNames: string[]) {
  return {
    mode: 'parallel',
    steps: jobNames.map((job) => ({ step_id: job, job_name: job, trigger_rule: 'all_done' })),
  };
}

async function createSequence(ctx: APIRequestContext, configId: number) {
  const etls = etlNames();
  // A step that never ran stops later stages under every start rule, so the failing
  // ETL is the last of Set 3 and Set 4 starts once it has finished, whatever the outcome.
  await post(ctx, '/api/sequences', {
    name: SEQUENCE_NAME,
    description: 'Business date, 4 ETL sets, ~100 ETLs, reconciliation against Production, BO reports',
    tags: ['regression', 'nightly'],
    defaults: {
      source_env: 'qa',
      target_env: 'prod',
      config_id: configId,
      run_settings: { use_live_connections: true, max_workers: 4 },
    },
    stages: [
      serialStage(etls.slice(0, 25), 'all_success'),
      serialStage(etls.slice(25, 50), 'all_success'),
      serialStage(etls.slice(50, 75), 'all_success'),
      serialStage(etls.slice(75, 100), 'all_done'),
      serialStage([JOBS.fetchFlatFile], 'all_done'),
      parallelStage([JOBS.reconCsv, JOBS.reconXml, JOBS.oracleVsPostgres, JOBS.postgresVsFlatFile]),
      parallelStage([JOBS.boOrders, JOBS.boSummary, JOBS.boDaily]),
    ],
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
    const settings = await ctx.put('/api/settings', {
      data: {
        // The BO date prompt converts through this timezone.
        timezone: 'UTC',
        smtp_host: 'mailpit',
        smtp_port: 1025,
        smtp_from: 'atom-regression@atom-demo.example',
        smtp_use_tls: false,
      },
    });
    if (!settings.ok()) throw new Error(`PUT /api/settings failed: ${await settings.text()}`);

    // A launch override only applies to a variable that exists. The default differs
    // from the demo date, so the run proves the date typed at launch is the one used.
    await post(ctx, '/api/variables', {
      name: 'run_date',
      var_type: 'date',
      default_value: '2026-01-01',
      description: 'Business date for the regression run',
    });

    const configs = await createConfigs(ctx);
    await createFileServers(ctx);
    await createJobs(ctx, configs);
    await createSequence(ctx, configs.regression);

    await post(ctx, '/api/notifications', {
      name: 'Regression summary',
      channel: 'email',
      url: `mailto:${EMAIL_RECIPIENTS.join(',')}`,
      events: ['run.completed'],
      subject_template: 'ATOM regression {{status}}: run {{run_id}}',
      body_template: [
        'ATOM regression finished.',
        '',
        'Run: {{run_id}}',
        'Result: {{status}}',
        'Passed: {{passed}}',
        'Failed: {{failed}}',
        'Errors: {{error}}',
        'Completed at: {{completed_at}}',
      ].join('\n'),
    });
  } finally {
    await ctx.dispose();
  }
}
