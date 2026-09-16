import { test, expect } from './fixtures';

test.describe('11 help', () => {
  test('sidebar and content list all 14 sections from window.ETL_HELP', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-help"]').click();
    await expect.poll(() => authedPage.evaluate(() => (window as any).ETL_HELP?.sections?.length)).toBe(14);

    const titles = await authedPage.evaluate(() => (window as any).ETL_HELP.sections.map((section: any) => section.title));
    await expect(authedPage.locator('.help-nav-item')).toHaveCount(14);
    await expect(authedPage.locator('.help-section')).toHaveCount(14);
    for (const title of titles) {
      await expect(authedPage.locator('.help-nav-item', { hasText: title })).toBeVisible();
      await expect(authedPage.locator('.help-section-title', { hasText: title })).toBeVisible();
    }
  });

  test('covers all 14 functional domains with UI and executable equivalents', async ({ authedPage }) => {
    await authedPage.goto('/?tab=help');
    await expect.poll(() => authedPage.evaluate(() => Boolean((window as any).ETL_HELP?.sections?.length))).toBe(true);
    const coverage = await authedPage.evaluate(() => {
      const sections = (window as any).ETL_HELP.sections;
      return {
        ids: sections.map((section: any) => section.id),
        complete: sections.every((section: any) => section.steps.some((step: any) =>
          step.where && step.when && step.uiMockup?.elements?.length && step.cli?.command
        )),
        searchable: [
          'Write-Audit-Publish', 'CI Trigger', 'Repeat execution', 'topological',
          'Column Stats', 'Mismatch Inspector', 'Server-Sent Events', 'Restart from failure',
          'SLA breach', 'Athena', 'SFTP', 'Automic', 'scheduler statistics', 'GitLab commit status',
        ].map((query) => sections.some((section: any) => {
          const haystack = JSON.stringify(section).toLowerCase();
          return haystack.includes(query.toLowerCase());
        })),
      };
    });

    expect(coverage.ids).toEqual([
      'primer', 'config-security', 'launch-jobs', 'sequences', 'compare', 'differences',
      'monitor', 'history', 'contracts', 'aws', 'file-servers', 'adapters',
      'reports-scheduler', 'cicd-logs',
    ]);
    expect(coverage.complete).toBe(true);
    expect(coverage.searchable).toEqual(Array(14).fill(true));
  });

  test('documents verified API payloads and exact CLI output contracts', async ({ authedPage }) => {
    await authedPage.goto('/?tab=help');
    await expect.poll(() => authedPage.evaluate(() => Boolean((window as any).ETL_HELP?.sections?.length))).toBe(true);
    const content = await authedPage.evaluate(() => {
      const sections = (window as any).ETL_HELP.sections;
      const commands = sections.flatMap((section: any) => section.steps.map((step: any) => step.cli?.command || ''));
      return { json: JSON.stringify(sections), commands };
    });

    expect(content.commands.some((command: string) => command.includes('/api/sequences/17/launch-batch') && command.includes('"batch":{"variable_name":"BUSINESS_DATE","start_value":"2026-09-01","iterations":10,"step_days":1,"weekend_policy":"skip","stop_on_failure":true}'))).toBe(true);
    const serialized = content.json;
    expect(serialized).toContain('/api/compare/mismatch-diff');
    expect(content.commands.some((command: string) => command.includes('/api/compare/mismatch-diff') && command.includes('"run_id_a":"run-100","run_id_b":"run-104","run_a_label":"Baseline","run_b_label":"Current"'))).toBe(true);
    expect(serialized).toContain('\\"summary\\":{\\"new\\":1,\\"resolved\\":0,\\"persistent\\":0},\\"has_regressions\\":true');
    expect(serialized).toContain('PASSED run=run-7f2c passed=8 failed=0 error=0 exit=0');
    expect(serialized).not.toContain('Wrote run.json');
    expect(serialized).not.toContain('Wrote run-104.csv');
    expect(serialized).not.toContain('Wrote report.html');
    expect(serialized).toContain('/api/runs/run-104/restart');
    expect(serialized).toContain('/api/tokens');
    expect(serialized).toContain('/api/aws/s3/validate-format');
    expect(serialized).toContain('/api/aws/glue/compare-tables');
    expect(serialized).toContain('/api/aws/glue/jobs/daily-orders/run');
    expect(serialized).toContain('/api/aws/athena/run-query');
    expect(serialized).toContain('/api/aws/airflow/dags/publish_orders/run');
  });

  test('all 14 domains are visibly searchable by domain-specific terms', async ({ authedPage }) => {
    await authedPage.goto('/?tab=help');
    const search = authedPage.locator('[data-testid="help-search-input"]');
    const cases = [
      ['Write-Audit-Publish', 'ETL Testing Primer & Fundamental Concepts'],
      ['CI Trigger token', 'Config, Connections & Security'],
      ['Repeat execution', 'Launch Tab: Job Design, Scheduling & Automation Reference'],
      ['topological DAG', 'Execution Sequences (DAGs)'],
      ['Column Stats', 'Compare & Data Verification Engines'],
      ['Mismatch Inspector', 'Differences & Mismatch Inspector'],
      ['Server-Sent Events', 'Monitor & Live Execution Streaming'],
      ['schema history', 'History, Lineage & Recovery'],
      ['SLA breach', 'Data Contracts & Promotion Quality Gates'],
      ['Athena DQ metrics', 'AWS Tab: S3, Glue Catalog, Athena & Airflow Reference'],
      ['host fingerprints', 'File Servers & Storage'],
      ['Automic UC4', 'Enterprise Adapters (SAP DS, SAP BO, Automic & Airflow)'],
      ['scheduler statistics', 'Reports, Scheduler Statistics & Analytics'],
      ['GitLab commit status', 'CLI & CI/CD Automation, GitLab Native Signals & Global Logs'],
    ];
    for (const [query, title] of cases) {
      await search.fill(query);
      await expect(authedPage.getByRole('heading', { name: title })).toBeVisible();
    }
  });

  test('sample JSON and CLI tables match source contracts', async ({ authedPage }) => {
    await authedPage.goto('/?tab=help');
    await expect.poll(() => authedPage.evaluate(() => Boolean((window as any).ETL_HELP?.sections?.length))).toBe(true);
    const samples = await authedPage.evaluate(() => (window as any).ETL_HELP.sections
      .flatMap((section: any) => section.steps)
      .map((step: any) => ({ title: step.title, sample: step.cli?.sampleOutput })));
    const sample = (title: string) => samples.find((item: any) => item.title === title)?.sample;

    const batch = JSON.parse(sample('Repeat a multi-day sequence safely'));
    expect(batch).toMatchObject({ status: 'PENDING', created_at: '2026-09-16T04:00:00Z' });
    expect(batch.runs).toEqual(expect.arrayContaining([
      expect.objectContaining({ run_id: null, status: 'PENDING', iteration_index: 0, business_date: '2026-09-01' }),
    ]));
    expect(JSON.parse(sample('Restart from failure')).status).toBe('PENDING');
    expect(JSON.parse(sample('Glue Job: run Spark ETL to completion'))).toMatchObject({ job_run_state: 'SUCCEEDED' });
    expect(sample('Explore run history and export evidence')).toBe([
      'run_id    status      passed    failed    error  started',
      '--------  --------  --------  --------  -------  -------------------------',
      'run-104   FAILED           7         1        0  2026-09-16T03:00:00+00:00',
      'run-103   PASSED           8         0        0  2026-09-15T03:00:00+00:00',
    ].join('\n'));
  });

  test('contract and Glue samples are internally schema-consistent', async ({ authedPage }) => {
    await authedPage.goto('/?tab=help');
    await expect.poll(() => authedPage.evaluate(() => Boolean((window as any).ETL_HELP?.sections?.length))).toBe(true);
    const samples = await authedPage.evaluate(() => (window as any).ETL_HELP.sections
      .flatMap((section: any) => section.steps)
      .reduce((result: any, step: any) => ({ ...result, [step.title]: step.cli?.sampleOutput }), {}));

    const contract = JSON.parse(samples['Create and test a Data Contract']);
    expect(contract.summary.total).toBe(contract.checks.length);
    expect(contract.summary.passed).toBe(contract.checks.filter((check: any) => check.status === 'PASS').length);
    expect(contract.summary.failed).toBe(contract.checks.filter((check: any) => check.status === 'FAIL').length);
    expect(contract.summary.warnings).toBe(contract.checks.filter((check: any) => check.status === 'WARN').length);
    expect(contract.checks[0]).toEqual(expect.objectContaining({
      id: expect.any(String), category: expect.any(String), name: expect.any(String),
      status: 'PASS', target: expect.any(String), message: expect.any(String),
    }));
    const mismatch = JSON.parse(samples['Compare mismatches across runs']);
    expect(mismatch.summary).toEqual({
      new: mismatch.new.length,
      resolved: mismatch.resolved.length,
      persistent: mismatch.persistent.length,
    });
    expect(mismatch.has_regressions).toBe(mismatch.new.length > 0);
    const glue = JSON.parse(samples['Glue Catalog: compare two tables']);
    expect(glue.diff.location_mismatch).toEqual({
      source: 's3://lake/raw/orders/',
      target: 's3://lake/curated/orders/',
    });
  });

  test('every JSON sample output parses', async ({ authedPage }) => {
    await authedPage.goto('/?tab=help');
    await expect.poll(() => authedPage.evaluate(() => Boolean((window as any).ETL_HELP?.sections?.length))).toBe(true);
    const outputs = await authedPage.evaluate(() => (window as any).ETL_HELP.sections
      .flatMap((section: any) => section.steps)
      .map((step: any) => ({ title: step.title, output: step.cli?.sampleOutput }))
      .filter((item: any) => item.output?.trim().startsWith('{') || item.output?.trim().startsWith('[')));
    expect(outputs.length).toBeGreaterThanOrEqual(18);
    for (const item of outputs) expect(() => JSON.parse(item.output), item.title).not.toThrow();
  });

  test('file-server and notification samples satisfy response contracts', async ({ authedPage }) => {
    await authedPage.goto('/?tab=help');
    await expect.poll(() => authedPage.evaluate(() => Boolean((window as any).ETL_HELP?.sections?.length))).toBe(true);
    const samples = await authedPage.evaluate(() => (window as any).ETL_HELP.sections
      .flatMap((section: any) => section.steps)
      .reduce((result: any, step: any) => ({ ...result, [step.title]: step.cli?.sampleOutput }), {}));

    const profile = JSON.parse(samples['Configure and test storage profiles'])[0];
    expect(['sftp', 'scp', 's3']).toContain(profile.kind);
    expect(Object.keys(profile).sort()).toEqual([
      'auth_method', 'aws_access_key_id', 'aws_secret_access_key', 'aws_session_token',
      'created_at', 'description', 'endpoint_url', 'host', 'host_key_fingerprint', 'id',
      'key_passphrase', 'kind', 'name', 'password', 'port', 'private_key', 'region_name',
      'updated_at', 'username',
    ].sort());
    const hook = JSON.parse(samples['Configure notifications and signed webhooks'])[0];
    expect(hook).toEqual({
      id: 2,
      name: 'quality-alerts',
      url: 'https://hooks.example.test/atom',
      events: ['run.failed', 'contract.breached'],
      enabled: true,
      channel: 'generic',
      subject_template: null,
      body_template: null,
      created_at: '2026-09-16T03:30:00Z',
    });
  });

  test('restored scenarios and decision guides stay searchable and rendered', async ({ authedPage }) => {
    await authedPage.goto('/?tab=help');
    const search = authedPage.locator('[data-testid="help-search-input"]');
    const guides = [
      'Scenario 1: Reconciling PostgreSQL vs Snowflake (SQL Reconcile)',
      'Scenario 2: Validating SAP BO Reports During Migration',
      'Scenario 3: Testing REST API Microservices Data Ingestion',
      'Scenario 4: Multi-File S3 Batch Reconciliation',
      'Scenario 5: Setting Up CI/CD Quality Gates',
      'Scenario 6: File Watcher Gating a Sequence on a Nightly Drop',
      'Which Job Type When?', 'Which Compare Mode When?',
      'Which Schema Mismatch Policy When?', 'Which DQ Rule Category When?',
    ];
    for (const guide of guides) {
      await search.fill(guide);
      await expect(authedPage.getByText(guide, { exact: true })).toBeVisible();
    }
  });

  test('domains retain meaningful multi-guide coverage', async ({ authedPage }) => {
    await authedPage.goto('/?tab=help');
    await expect.poll(() => authedPage.evaluate(() => Boolean((window as any).ETL_HELP?.sections?.length))).toBe(true);
    const coverage = await authedPage.evaluate(() => (window as any).ETL_HELP.sections.map((section: any) => ({
      id: section.id,
      count: section.steps.length,
      complete: section.steps.filter((step: any) => step.where && step.when && step.uiMockup?.elements?.length && step.cli?.command).length,
    })));
    expect(coverage.every((domain: any) => domain.count >= 2 && domain.complete >= 2)).toBe(true);
    expect(coverage.find((domain: any) => domain.id === 'launch-jobs')?.count).toBeGreaterThanOrEqual(7);
    expect(coverage.find((domain: any) => domain.id === 'compare')?.count).toBeGreaterThanOrEqual(7);
  });

  test('negative: search matching no topic shows the no-match message', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-help"]').click();
    await authedPage.locator('[data-testid="help-search-input"]').fill('zzz_no_such_help_topic_zzz');
    await expect(authedPage.locator('text=No help topics match')).toBeVisible();
  });

  test('job automation guide is visible and searchable', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-help"]').click();

    await expect(authedPage.locator('text=Job Design, Scheduling & Automation').first()).toBeVisible();

    await authedPage.locator('[data-testid="help-search-input"]').fill('pytest');
    await expect(authedPage.getByText('Repeat execution and run from external pytest', { exact: true })).toBeVisible();
    await authedPage.locator('[data-testid="help-search-input"]').fill('ci/cd');
    await expect(authedPage.getByText('GitLab CI complete workflow', { exact: true })).toBeVisible();
  });

  test('search visibly finds representative UI controls and exact CLI flags', async ({ authedPage }) => {
    await authedPage.goto('/?tab=help');
    const search = authedPage.locator('[data-testid="help-search-input"]');
    const cases = [
      ['DAG builder', 'Design and launch a topological DAG'],
      ['Mismatch Inspector', 'Search and resolve mismatches'],
      ['Data Contracts', 'Data Contracts & Promotion Quality Gates'],
      ['atom run', 'Launch and gate with atom run'],
      ['--target-type sequence', 'Design and launch a topological DAG'],
      ['--junit-out', 'Collect JUnit and run artifacts'],
      ['--var NAME=VALUE', 'Repeat execution and run from external pytest'],
    ];

    for (const [query, result] of cases) {
      await search.fill(query);
      await expect(authedPage.getByText(result, { exact: true }).first()).toBeVisible();
    }
  });

  test('final review guidance is complete, searchable, and copyable', async ({ authedPage }) => {
    await authedPage.goto('/?tab=help');
    await expect.poll(() => authedPage.evaluate(() => Boolean((window as any).ETL_HELP?.sections?.length))).toBe(true);
    const content = await authedPage.evaluate(() => {
      const steps = (window as any).ETL_HELP.sections.flatMap((section: any) => section.steps);
      const byTitle = (title: string) => steps.find((step: any) => step.title === title);
      return {
        gitlab: byTitle('GitLab CI complete workflow')?.cli?.command,
        github: byTitle('GitHub Actions complete workflow')?.cli?.command,
        wrapper: byTitle('GitLab wrapper semantics')?.cli?.command,
        schedule: byTitle('Cron expressions and recurring schedules'),
        rules: byTitle('Rules-as-Code and Expectations Sync'),
        configOverride: byTitle('Per-Config Custom Variable overrides'),
        launchOverride: byTitle('Launch-time Custom Variable overrides'),
      };
    });

    expect(content.gitlab).toContain('atom-tests:');
    expect(content.gitlab).toContain('ATOM_API_URL');
    expect(content.gitlab).toContain('ATOM_API_TOKEN');
    expect(content.gitlab).toContain('--junit-out atom-junit.xml');
    expect(content.gitlab).toContain('reports:\n      junit: atom-junit.xml');
    expect(content.github).toContain('name: Atom quality gate');
    expect(content.github).toContain('secrets.ATOM_API_TOKEN');
    expect(content.github).toContain('vars.ATOM_API_URL');
    expect(content.github).toContain('actions/upload-artifact@v4');
    expect(content.wrapper).toBe('bash scripts/ci/run-atom-target.sh selection "Nightly Regression" dev qa');
    expect(content.schedule.cli.command).toContain("$ATOM_API_URL/api/schedules");
    expect(content.schedule.cli.command).toContain('"cron_expr":"0 6 * * 1-5"');
    expect(content.rules.cli.command).toContain('/api/expectations/export');
    expect(content.rules.cli.command).toContain('/api/expectations/sync');
    expect(content.configOverride.cli.command).toContain('"variables":{"BUSINESS_DATE":"today-1","BATCH_ID":""}');
    expect(content.launchOverride.cli.command).toContain('--var BUSINESS_DATE=2026-09-16');

    const search = authedPage.locator('[data-testid="help-search-input"]');
    for (const [query, title] of [
      ['cron expression', 'Cron expressions and recurring schedules'],
      ['Expectations Sync', 'Rules-as-Code and Expectations Sync'],
      ['per-Config override', 'Per-Config Custom Variable overrides'],
      ['actions/upload-artifact@v4', 'GitHub Actions complete workflow'],
    ]) {
      await search.fill(query);
      await expect(authedPage.getByText(title, { exact: true })).toBeVisible();
    }
  });

  test('Compare help is visible and searchable', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-help"]').click();

    await expect(authedPage.locator('text=Compare').first()).toBeVisible();

    await authedPage.locator('[data-testid="help-search-input"]').fill('All tabs');
    await expect(authedPage.locator('text=Compare all tabs in a BO document')).toBeVisible();

    await authedPage.locator('[data-testid="help-search-input"]').fill('Column Stats');
    await expect(authedPage.locator('text=Use Column Stats for large tables')).toBeVisible();

    await authedPage.locator('[data-testid="help-search-input"]').fill('Mismatch Diff');
    await expect(authedPage.locator('text=Compare mismatches across runs')).toBeVisible();
  });

  test('AWS help is visible and searchable', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-help"]').click();

    await expect(authedPage.locator('text=S3, Glue Catalog, Athena & Airflow').first()).toBeVisible();

    await authedPage.locator('[data-testid="help-search-input"]').fill('metric_assertions');
    await expect(authedPage.locator('text=Athena: run a query and assert on the results')).toBeVisible();

    await authedPage.locator('[data-testid="help-search-input"]').fill('location mismatch');
    await expect(authedPage.locator('text=Glue Catalog: compare two tables')).toBeVisible();
  });

  test('search includes section, CLI, and UI mockup metadata', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-help"]').click();

    const matches = await authedPage.evaluate(() => {
      const app = (window as any).ETL_HELP_METHODS;
      const context = {
        helpSearch: '',
        helpSections: [{
          id: 'advanced-help',
          title: 'Advanced help',
          intro: 'section introduction',
          category: 'operations category',
          steps: [{
            title: 'Normal title',
            text: 'normal text',
            where: 'normal location',
            when: 'normal timing',
            tip: 'normal tip',
            warn: 'normal warning',
            cli: {
              command: 'atom special-command',
              description: 'CLI description',
              params: [{ flag: '--special-flag', desc: 'parameter details' }],
              sampleOutput: 'sample terminal output',
            },
            uiMockup: {
              title: 'Mockup title',
              badge: 'Beta badge',
              elements: [{ label: 'Field label', value: 'Field value' }],
            },
          }],
        }],
        ...app,
      };
      const queries = [
        'operations category', 'normal title', 'normal text', 'normal location',
        'normal timing', 'normal tip', 'normal warning', 'atom special-command',
        'cli description', '--special-flag', 'parameter details',
        'sample terminal output', 'mockup title', 'beta badge', 'field label',
        'field value',
      ];
      const section = context.helpSections[0];
      return {
        matches: queries.map((query) => {
          context.helpSearch = query;
          return context.helpFilteredSections().length === 1;
        }),
        metadataShowsStep: context.helpStepMatches(section.steps[0], 'operations category', section),
        ordinaryMatchShowsStep: context.helpStepMatches(section.steps[0], 'normal title', section),
        ordinaryMatchHidesOtherStep: context.helpStepMatches({ title: 'Other step' }, 'normal title', section),
      };
    });

    expect(matches).toEqual({
      matches: Array(16).fill(true),
      metadataShowsStep: true,
      ordinaryMatchShowsStep: true,
      ordinaryMatchHidesOtherStep: false,
    });
  });

  test('app construction applies external help methods', async ({ authedPage }) => {
    await authedPage.goto('/');
    const result = await authedPage.evaluate(() => {
      const instance = (window as any).eval('app()');
      const methods = (window as any).ETL_HELP_METHODS;
      return {
        filtered: instance.helpFilteredSections === methods.helpFilteredSections,
        step: instance.helpStepMatches === methods.helpStepMatches,
        copy: instance.copyCliCommand === methods.copyCliCommand,
      };
    });

    expect(result).toEqual({ filtered: true, step: true, copy: true });
  });

  test('copyCliCommand safely reports transient copied state', async ({ authedPage }) => {
    await authedPage.goto('/');
    const result = await authedPage.evaluate(async () => {
      let copied = '';
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async (value: string) => { copied = value; } },
      });
      const button = document.createElement('button');
      button.setAttribute('aria-label', 'Copy command');
      button.innerHTML = '<svg data-preserved="true"></svg><span>Copy</span>';
      document.body.appendChild(button);
      await (window as any).ETL_HELP_METHODS.copyCliCommand('atom run demo', { currentTarget: button });
      return {
        copied,
        label: button.querySelector('span')?.textContent,
        preserved: Boolean(button.querySelector('[data-preserved="true"]')),
        state: button.dataset.copyState,
        ariaLabel: button.getAttribute('aria-label'),
      };
    });

    expect(result).toEqual({
      copied: 'atom run demo',
      label: 'Copied!',
      preserved: true,
      state: 'copied',
      ariaLabel: 'Command copied',
    });
  });

  test('copyCliCommand restores feedback and handles clipboard failures', async ({ authedPage }) => {
    await authedPage.goto('/');
    const result = await authedPage.evaluate(async () => {
      const methods = (window as any).ETL_HELP_METHODS;
      const makeButton = () => {
        const button = document.createElement('button');
        button.setAttribute('aria-label', 'Copy command');
        button.innerHTML = '<svg data-preserved="true"></svg><span>Copy</span>';
        document.body.appendChild(button);
        return button;
      };
      const successButton = makeButton();
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async () => undefined },
      });
      await methods.copyCliCommand('atom run demo', { currentTarget: successButton });
      await new Promise((resolve) => setTimeout(resolve, 1900));

      const rejectedButton = makeButton();
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async () => { throw new Error('denied'); } },
      });
      await methods.copyCliCommand('atom run denied', { currentTarget: rejectedButton });

      const unavailableButton = makeButton();
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
      await methods.copyCliCommand('atom run unavailable', { currentTarget: unavailableButton });

      return {
        restored: {
          label: successButton.querySelector('span')?.textContent,
          preserved: Boolean(successButton.querySelector('[data-preserved="true"]')),
          state: successButton.dataset.copyState || null,
          ariaLabel: successButton.getAttribute('aria-label'),
        },
        rejected: {
          label: rejectedButton.querySelector('span')?.textContent,
          state: rejectedButton.dataset.copyState || null,
        },
        unavailable: {
          label: unavailableButton.querySelector('span')?.textContent,
          state: unavailableButton.dataset.copyState || null,
        },
      };
    });

    expect(result).toEqual({
      restored: { label: 'Copy', preserved: true, state: null, ariaLabel: 'Copy command' },
      rejected: { label: 'Copy', state: null },
      unavailable: { label: 'Copy', state: null },
    });
  });

  test('help component theme tokens reference defined project variables', async ({ authedPage }) => {
    const stylesheet = await authedPage.evaluate(async () => fetch('/styles.css').then((response) => response.text()));
    const normalizedStylesheet = stylesheet.replace(/\r\n/g, '\n');
    const helpBlockStart = normalizedStylesheet.indexOf('.help-mockup,\n.help-cli {');
    const helpBlock = normalizedStylesheet.slice(helpBlockStart, normalizedStylesheet.indexOf('@media (max-width: 880px)', helpBlockStart));
    expect(helpBlock).not.toContain('var(--surface-2');
    expect(helpBlock).not.toContain('var(--green');
    expect(helpBlock).toContain('--help-surface-muted: var(--panel-3, #171d24);');
  });

  test('renders accessible UI mockups and CLI panels for every supported element type', async ({ authedPage }) => {
    await authedPage.goto('/?tab=help');
    await expect(authedPage.locator('.help-nav-item').first()).toBeVisible();

    await authedPage.evaluate(() => {
      const body = document.querySelector('[x-data="app()"]') as any;
      body._x_dataStack[0].helpSections = [{
        id: 'rendering-fixture',
        title: 'Rendering fixture',
        intro: 'Exercises every mockup element.',
        steps: [{
          title: 'All controls',
          text: 'A complete native mockup and command example.',
          uiMockup: {
            title: 'Run configuration',
            badge: 'Preview',
            elements: [
              { type: 'input', label: 'Job name', value: 'daily-check', highlight: true },
              { type: 'select', label: 'Environment', value: 'production' },
              { type: 'button', label: 'Run now', highlight: true },
              { type: 'badge', label: 'Ready', status: 'passed' },
              { type: 'status', value: 'Running', status: 'running' },
              { type: 'status', value: 'Queued', status: 'queued' },
              { type: 'status', value: 'Informational', status: 'info' },
              { type: 'dag', label: 'Extract', value: 'Validate' },
              { type: 'table', label: 'Recent runs', columns: ['Run', 'Result'], rows: [['42', 'Passed']] },
              { type: 'code', label: 'Query', value: 'select 1' },
              { type: 'metric', label: 'Pass rate', value: '99.5%' },
            ],
          },
          cli: {
            command: 'atom run daily-check --env production',
            description: 'Runs the same check from a terminal.',
            params: [{ flag: '--env', desc: 'Selects the environment.' }],
            sampleOutput: 'Run 42 passed',
            exitCodes: [{ code: 0, meaning: 'Passed' }],
          },
        }],
      }];
    });

    const mockup = authedPage.getByRole('region', { name: 'Run configuration' });
    await expect(mockup).toBeVisible();
    await expect(mockup.getByText('Job name')).toBeVisible();
    await expect(mockup.getByText('production')).toBeVisible();
    await expect(mockup.getByRole('button', { name: 'Run now' })).toBeDisabled();
    const passedStatus = mockup.getByRole('status', { name: 'Ready' });
    await expect(passedStatus).toHaveCSS('color', 'rgb(34, 197, 94)');
    await expect(mockup.getByRole('status', { name: 'Running' })).toBeVisible();
    const queuedStatus = mockup.getByRole('status', { name: 'Queued' });
    const infoStatus = mockup.getByRole('status', { name: 'Informational' });
    await expect(queuedStatus).toHaveCSS('color', 'rgb(251, 191, 36)');
    await expect(infoStatus).toHaveCSS('color', 'rgb(34, 211, 238)');
    await expect(mockup.getByLabel('Workflow from Extract to Validate')).toBeVisible();
    await expect(mockup.getByRole('table', { name: 'Recent runs' })).toBeVisible();
    await expect(mockup.getByRole('columnheader', { name: 'Result' })).toBeVisible();
    await expect(mockup.getByText('select 1')).toBeVisible();
    await expect(mockup.getByText('99.5%')).toBeVisible();

    const terminal = authedPage.getByRole('region', { name: 'All controls CLI execution equivalent' });
    await expect(terminal).toBeVisible();
    await expect(terminal.getByText('atom run daily-check --env production')).toBeVisible();
    await expect(terminal.getByRole('button', { name: 'Copy All controls command' })).toBeVisible();
    await expect(terminal.getByText('--env', { exact: true })).toBeVisible();
    await expect(terminal.getByText('Run 42 passed')).toBeVisible();
    await expect(terminal.getByText('Exit codes')).toBeVisible();
    await expect(terminal.getByText('Passed', { exact: true })).toBeVisible();

    await authedPage.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async (value: string) => { (window as any).__copiedHelpCommand = value; } },
      });
    });
    const copyButton = terminal.locator('.help-cli-copy');
    await expect(copyButton).toHaveAccessibleName('Copy All controls command');
    await copyButton.click();
    await expect(copyButton).toHaveAccessibleName('All controls command copied');
    await expect(copyButton.getByText('Copied!')).toHaveAttribute('role', 'status');
    await expect.poll(() => authedPage.evaluate(() => (window as any).__copiedHelpCommand)).toBe('atom run daily-check --env production');
  });

  test('help mockups and CLI panels remain usable at mobile width', async ({ authedPage }) => {
    await authedPage.setViewportSize({ width: 390, height: 844 });
    await authedPage.goto('/?tab=help');
    await expect(authedPage.locator('.help-nav-item').first()).toBeVisible();
    await authedPage.evaluate(() => {
      const body = document.querySelector('[x-data="app()"]') as any;
      body._x_dataStack[0].helpSections = [{
        id: 'mobile-fixture',
        title: 'Mobile fixture',
        intro: 'Responsive content.',
        steps: [{
          title: 'Mobile rendering',
          text: 'Responsive mockup and CLI.',
          uiMockup: { title: 'Mobile preview', elements: [{ type: 'input', label: 'Name', value: 'daily-check' }] },
          cli: { command: 'atom run daily-check', params: [{ flag: '--verbose', desc: 'Show details.' }] },
        }],
      }];
    });

    const mockup = authedPage.getByRole('region', { name: 'Mobile preview' });
    const terminal = authedPage.getByRole('region', { name: 'Mobile rendering CLI execution equivalent' });
    await expect(mockup).toBeInViewport();
    await expect(terminal).toBeInViewport();
    await expect.poll(async () => authedPage.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await expect(mockup.locator('.help-mockup-element')).toHaveCSS('flex-basis', '100%');
    await expect(terminal.locator('.help-cli-param')).toHaveCSS('grid-template-columns', /\d+(\.\d+)?px/);
  });

  test('Help query and hash deep-links load content on first paint and after reload', async ({ authedPage }) => {
    for (const url of ['/?tab=help', '/#help']) {
      await authedPage.goto(url);
      await expect(authedPage.locator('.help-nav-item').first()).toBeVisible();
      await expect(authedPage.locator('[data-testid="help-search-input"]')).toBeVisible();

      await authedPage.reload();
      await expect(authedPage.locator('.help-nav-item').first()).toBeVisible();
      await expect(authedPage.locator('[data-testid="help-search-input"]')).toBeVisible();
    }
  });
});
