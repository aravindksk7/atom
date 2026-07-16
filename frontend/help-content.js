/*
 * Help center content for the ETL Test Framework UI.
 * Consumed by the app's Help tab (see index.html / app.js).
 * Structure: window.ETL_HELP.sections[] -> { id, title, intro, steps[] }
 * Each step: { title, text, where?, tip?, warn? }
 */
(function (global) {
  const sections = [
    {
      id: 'getting-started',
      title: 'Getting Started',
      intro: 'Get the app running and sign in. Everything below assumes the server is up and reachable at its base URL (e.g. http://127.0.0.1:8000).',
      steps: [
        {
          title: 'Start the server',
          text: 'Run the FastAPI app with uvicorn. In development use --reload; in production drop it. The UI is served automatically at the root URL.',
          where: 'Terminal: python -m uvicorn api.main:app --host 127.0.0.1 --port 8000',
        },
        {
          title: 'Open the web UI',
          text: 'Open the base URL in a browser. On first load an auth modal appears because the database starts empty and no token is set yet.',
          where: 'Browser: http://127.0.0.1:8000',
        },
        {
          title: 'Create your first token',
          text: 'In the auth modal, type a name (e.g. admin) and click Create Token. The raw token is shown once — copy it immediately and store it in a password manager. Then paste it into "Use existing token" and click Activate.',
          where: 'Auth modal -> Create Token',
          tip: 'The first token is always an admin token. Later token creation requires an admin Authorization header.',
        },
        {
          title: 'Confirm you are connected',
          text: 'After activation the top bar shows "Connected as <name>" and the auth status bar turns green. If you ever switch tabs or close the window, the token lives in sessionStorage and clears on tab close — re-activate it then.',
          where: 'Top bar / auth status bar',
        },
        {
          title: 'Pick a tab and begin',
          text: 'The app has tabs: Config, Launch, Monitor, History, Adapters, Reports, Differences, Compare, Contracts, Logs. Start in Config to store connection details, then Launch to run tests.',
        },
      ],
    },
    {
      id: 'config',
      title: 'Configuration',
      intro: 'Config stores reusable environment connection details (SQL Server, SAP BO, Automic) plus API tokens and webhook notifications. Without a config, runs use safe simulation data.',
      steps: [
        {
          title: 'Open the Config tab',
          text: 'The Configuration Editor lists all saved environment configs. Each config bundles host, database, BO, and Automic settings under one name.',
          where: 'Tabs -> Config',
        },
        {
          title: 'Create a new config',
          text: 'Click + New Config. Enter a Name and Env Name (e.g. "dev-sql" / "dev"), then fill db_host, db_port, db_name, db_user, db_password, db_driver. Add bo_url/bo_user/bo_password and automic_url/automic_user/automic_password as needed.',
          where: 'Config -> + New Config',
          tip: 'bo_auth_type must be secWinAD (not secEnterprise) for Active Directory-only SAP BO accounts, or you get HTTP 401.',
        },
        {
          title: 'Add named connections',
          text: 'Inside a config, expand Named Connections to add multiple DB connections (e.g. hr_db, finance_db) sharing the config\'s BO/Automic settings. Each entry overrides only the DB fields it needs. Pick one at launch time via the Source/Target Connection dropdown.',
          where: 'Config modal -> Named Connections',
          tip: 'Leaving both connection pickers unset in Launch uses the config top-level defaults.',
        },
        {
          title: 'Add REST API endpoints',
          text: 'Expand API Endpoints to register named HTTP sources (auth: none / api_key / bearer / basic; JSON with dot-path extract or CSV; cursor or page pagination). Reuse them as job sources, comparison sides, or both.',
          where: 'Config modal -> API Endpoints',
        },
        {
          title: 'Import configs from YAML',
          text: 'Expand the Import YAML card, paste a block defining one or more named environments, and click Import to create them all at once. Keep secrets out of source control.',
          where: 'Config -> Import YAML',
        },
        {
          title: 'Manage API tokens',
          text: 'Use the Security section to create and manage tokens. Standard user tokens can be scoped with is_admin=false and an expires_at. Verify any token with GET /api/auth/verify.',
          where: 'Config -> Security',
        },
        {
          title: 'Set up webhook notifications',
          text: 'Use the Notifications section to register webhook endpoints for run events (run.failed, run.passed, …). Optionally add an HMAC-SHA256 secret for signed payloads and use the test ping button to verify delivery.',
          where: 'Config -> Notifications',
        },
      ],
    },
    {
      id: 'launch',
      title: 'Jobs & Launch',
      intro: 'The Launch tab is where you define jobs (the tests to run), tune run settings, start runs, and schedule recurring execution.',
      steps: [
        {
          title: 'Set environment labels',
          text: 'Enter Source Env and Target Env labels (e.g. dev / prod). These are arbitrary strings that identify the pair in History and reports — they are NOT live credentials.',
          where: 'Launch -> env label inputs',
        },
        {
          title: 'Select a saved config',
          text: 'Pick a config from the dropdown. With no config the run uses simulation data (safe for development). To hit live systems, select a config AND enable Use Live Connections in Run Settings.',
          where: 'Launch -> config dropdown',
        },
        {
          title: 'Create a new job',
          text: 'In the Job Catalog card click + New Job. Enter a unique Name, optional Description and comma-separated Tags, then choose a Job Type. Fill the type-specific fields that appear.',
          where: 'Launch -> Job Catalog -> + New Job',
        },
        {
          title: 'Choose the right job type',
          text: 'reconciliation compares two SQL queries/files row-by-row; bo_report monitors a SAP BO report; automic_job checks an Automic job run; api_reconciliation reconciles two REST endpoints; dbt_artifact maps dbt tests; freshness, profile, schema_snapshot, and cross_job_assertion cover freshness, column profiling, schema drift, and cross-job metric checks.',
          where: 'Job editor -> Job Type',
          tip: 'For reconciliation the minimum is a query and one or more key_columns that uniquely identify a row.',
        },
        {
          title: 'Add dependencies',
          text: 'Set Depends On to one or more job names that must pass first. The executor resolves order with a topological sort and auto-skips a job when an upstream fails.',
          where: 'Job editor -> Depends On',
        },
        {
          title: 'Add Data Quality rules',
          text: 'Click + Add Rule to stack DQ checks on a job (not_null, unique, row_count bounds, match_regex, completeness_ratio, custom_sql, pii_mask_check, and more). Each violation becomes a typed mismatch with error/warn severity.',
          where: 'Job editor -> DQ Rules',
        },
        {
          title: 'Set a pass condition',
          text: 'Optionally override pass/fail with thresholds (min/max row count, max mismatches, require_status) or a custom pass_sql assertion that returns rows = pass (or fail).',
          where: 'Job editor -> Pass Condition',
        },
        {
          title: 'Validate and preview before saving',
          text: 'Click Validate Query for a dry-run EXPLAIN against both environments (no data fetched). Click Preview to execute the query against a chosen config and view up to 200 sample rows — use a smaller dev DB to sanity-check first.',
          where: 'Job editor -> Validate Query / Preview',
          warn: 'Preview actually executes the SQL. Avoid destructive or expensive queries.',
        },
        {
          title: 'Select jobs and start a run',
          text: 'Check the jobs to include (filter by tag if needed), drag rows or use up/down controls to set execution order, then click Run Tests. The UI jumps to Monitor and streams progress live.',
          where: 'Launch -> Job Catalog -> Run Tests',
        },
        {
          title: 'Tune Run Settings',
          text: 'Expand Run Settings to set execution mode (parallel/sequential), retry policy, float tolerance, null handling, hash precheck, chunk size, max workers, schema mismatch policy, and Use Live Connections.',
          where: 'Launch -> Run Settings',
        },
        {
          title: 'Schedule recurring runs',
          text: 'On the Schedules sub-tab, create a cron schedule (e.g. 0 6 * * 1-5) storing the full run config. Enable/disable without deleting, or click Run Now to fire immediately outside the normal schedule.',
          where: 'Launch -> Schedules sub-tab',
        },
        {
          title: 'Edit or delete jobs',
          text: 'Use the pencil icon to edit a catalog row and the trash icon to delete (permanent — historical results are kept). Bulk-import many jobs with POST /api/jobs/import.',
          where: 'Job Catalog -> pencil / trash',
        },
      ],
    },
    {
      id: 'monitor',
      title: 'Monitor',
      intro: 'The Monitor tab shows live progress for the active run via Server-Sent Events (SSE), with automatic fallback to 5-second polling if the stream drops.',
      steps: [
        {
          title: 'Watch live progress',
          text: 'After clicking Run Tests you land here automatically. Per-job status, progress, and messages stream in real time as each step executes.',
          where: 'Monitor',
        },
        {
          title: 'Read status badges',
          text: 'Each job shows PASSED / FAILED / RUNNING / QUEUED / SKIPPED. Jobs whose upstream failed show SKIPPED automatically.',
          where: 'Monitor -> job rows',
        },
        {
          title: 'Cancel a running job',
          text: 'Click Cancel to send a cooperative stop. The executor finishes its current step, cancels remaining steps, and marks the run CANCELLED. Safe to call on already-finished runs.',
          where: 'Monitor -> Cancel',
          tip: 'Cancellation is cooperative — it honors the cancel_requested flag checked between steps, so a long step completes first.',
        },
        {
          title: 'Release held sequence steps',
          text: 'If a run was triggered with a hold_after step, it pauses here until you release it manually before the next step runs.',
          where: 'Monitor -> Release',
        },
      ],
    },
    {
      id: 'history',
      title: 'History',
      intro: 'History holds every run, its results, mismatches, baselines, lineage, coverage, and audit trail. This is your main review surface.',
      steps: [
        {
          title: 'Open a run',
          text: 'Click any run in the list to expand its per-job results. Each result shows status, counts, and matched/missing/value-mismatch detail.',
          where: 'History -> run row',
        },
        {
          title: 'Compare two runs',
          text: 'Select two runs and compare to see improved, regressed, unchanged, added, and removed tests at a glance.',
          where: 'History -> Compare runs',
        },
        {
          title: 'Pin a baseline',
          text: 'Pin any run as the baseline for its environment pair, then compare any later run against that baseline in one click to measure drift.',
          where: 'History -> Pin baseline',
        },
        {
          title: 'Review mismatch distribution',
          text: 'Each result shows the top-N column/source/target value triples so you can spot where differences cluster.',
          where: 'History -> result -> distribution',
        },
        {
          title: 'Accept known mismatches',
          text: 'Mark a known/expected mismatch as accepted with a note and optional user. Accepted mismatches are tracked separately and suppress noise on future reviews.',
          where: 'History -> mismatch -> Accept',
        },
        {
          title: 'Drill into segments',
          text: 'If a job has segment_columns (or the framework auto-picks low-cardinality ones), each failed result stores a per-segment summary. Use the drilldown action to re-query live per-segment row counts on both sides.',
          where: 'History -> result -> Drilldown',
        },
        {
          title: 'View job lineage',
          text: 'The lineage DAG shows job-to-job dependency edges so you can see what must pass before what.',
          where: 'History -> Lineage sub-tab',
        },
        {
          title: 'Browse profiles and schema history',
          text: 'Profile sub-tab lists per-column statistics (null rate, distinct count, percentiles). Schema sub-tab lists snapshot diffs — added, removed, or type-changed columns.',
          where: 'History -> Profile / Schema sub-tabs',
        },
        {
          title: 'Check coverage and flaky tests',
          text: 'Coverage maps every table/column to the jobs and DQ rules covering it (tested / observed / untested) with a gap filter. Flaky-test detection flags jobs that flip pass/fail too often (score ≥ 0.3).',
          where: 'History -> Coverage sub-tab',
        },
        {
          title: 'Inspect the audit log',
          text: 'Every create/update/delete and mismatch-accept is recorded with actor, action, resource, and a JSON diff. Filter or export from the Audit sub-tab.',
          where: 'History -> Audit sub-tab',
        },
      ],
    },
    {
      id: 'reports',
      title: 'Reports & Logs',
      intro: 'Generated HTML reports, metrics dashboards, and searchable logs, all in the dark-themed UI.',
      steps: [
        {
          title: 'Open a run report',
          text: 'From History or Compare, click Open in Reports to load the generated HTML report for a run (report_<run_id>.html).',
          where: 'History / Compare -> Open in Reports',
        },
        {
          title: 'Browse metrics',
          text: 'The metrics dashboard shows run-level charts and trends. Metric drift is detected with σ-based analysis across a rolling window.',
          where: 'Reports -> metrics',
        },
        {
          title: 'Search global logs',
          text: 'The Logs tab streams and searches application logs. Use filters to narrow by level or keyword. SSE-backed, with polling fallback.',
          where: 'Tabs -> Logs',
        },
      ],
    },
    {
      id: 'differences',
      title: 'Differences',
      intro: 'The Differences tab lists every row-level mismatch across runs with filters and expandable detail, so you can triage issues fast.',
      steps: [
        {
          title: 'Filter mismatches',
          text: 'Filter by run, job, status, or column. Use the expand control to see the exact source vs target values for each row.',
          where: 'Differences -> filters',
        },
        {
          title: 'Accept or dismiss',
          text: 'Accept a known difference with a note, or keep it open. Accepted items are visually separated so new issues stand out.',
          where: 'Differences -> accept',
        },
      ],
    },
    {
      id: 'compare',
      title: 'Compare',
      intro: 'The Compare tab runs on-demand comparisons outside the normal run flow: BO reports, dual-environment reconciliation, file compare, and SQL direct compare.',
      steps: [
        {
          title: 'Compare SAP BO reports',
          text: 'On the BO Report Compare card, compare two BO report sources from live BO, file paths, or uploads. Pick each side type and reference a config + report/document ID.',
          where: 'Compare -> BO Report Compare',
        },
        {
          title: 'Reconcile two environments directly',
          text: 'On the Reconciliation Dual-Environment card, run a one-off reconciliation of a query across source/target configs with key columns and tolerance, without saving a job.',
          where: 'Compare -> Reconciliation',
        },
        {
          title: 'Compare reconciliation files',
          text: 'On the Recon File Compare card, compare two stored runs or an HTML report against a production HTML report.',
          where: 'Compare -> Recon File Compare',
        },
        {
          title: 'Run a SQL direct compare',
          text: 'On the SQL Direct Compare card, pick config A / config B and a named connection for either side, then compare two queries or files directly. Save frequent setups as templates.',
          where: 'Compare -> SQL Direct Compare',
          tip: 'API endpoints registered in a config can be used as either side (source_type: "api").',
        },
        {
          title: 'Save and reuse templates',
          text: 'Save a comparison configuration as a template (source settings, key columns, options) so you can repeat common comparisons with one click.',
          where: 'Compare -> Save Template',
        },
      ],
    },
    {
      id: 'contracts',
      title: 'Data Contracts',
      intro: 'Contracts formalize expectations for a source job: ownership, SLA, and data quality. Breaches open and auto-resolve as the source job passes or fails.',
      steps: [
        {
          title: 'Open the Contracts tab',
          text: 'The Contracts tab lists every contract with a live OK / BREACHED / OVERDUE status badge, breach history, and inline version controls.',
          where: 'Tabs -> Contracts',
        },
        {
          title: 'Create a contract',
          text: 'Click + New Contract. Set a name, the source_job it enforces, an owner, sla_hours, consumers, and breach_severity. The contract derives the source job\'s DQ rules and latest schema snapshot automatically.',
          where: 'Contracts -> + New Contract',
        },
        {
          title: 'Start from an example',
          text: 'Click Examples to browse ready-made templates (orders, payments, user signups, inventory) aligned to industry data-contract standards. Each shows schema with examples, quality rules, and how to create & use it. Click Use example to prefill the form.',
          where: 'Contracts -> Examples',
        },
        {
          title: 'Understand breach lifecycle',
          text: 'When the source job FAILED, a breach opens and a contract.breached webhook fires. When it PASSES, open breaches auto-resolve with duration_hours and a contract.resolved webhook fires. Breaches past sla_hours are escalated every 15 minutes with a contract.escalated webhook.',
          where: 'Contracts -> status',
        },
        {
          title: 'Bump versions',
          text: 'Use inline version bump to raise minor or major semantic version (1.0 default). The version history is immutable and visible per contract.',
          where: 'Contracts -> version bump',
        },
      ],
    },
    {
      id: 'gates-rules-shadow',
      title: 'Gates, Rules-As-Code & Shadow Runs',
      intro: 'A Write-Audit-Publish promotion gate, versioned DQ rules, schema-compatibility grading, isolated transform testing, and cheap sampled "shadow" runs for CI. The first two below live in the UI; the rest are API/CLI/pytest features with no dedicated screen.',
      steps: [
        {
          title: 'Run a job in Shadow profile for a cheap check',
          text: 'In Run Settings, set Run Profile to Shadow (default is Full) and adjust Shadow Sample Fraction (default 0.02 = 2% of rows). Shadow runs sample rows per key via the same comparison backend; rows missing on either side are always kept, never sampled away. Use Shadow for fast per-PR checks and Full for the nightly authoritative run.',
          where: 'Launch -> Run Settings -> Run Profile',
        },
        {
          title: 'Check whether a job is safe to promote',
          text: 'Click Gate on any job row in the Job Catalog. This calls the Write-Audit-Publish gate, which returns PROMOTE only if the job\'s latest run PASSED and no open Data Contract breach exists for it — otherwise HOLD, with the specific reason(s) shown in the toast and badge next to the button.',
          where: 'Launch -> Job Catalog -> Gate button',
          tip: 'Wire the same check into an orchestrator with POST /api/gates/{job}/evaluate, or from the CLI with --gate-run <run_id> (exit codes 0=passed 1=failed 2=cancelled 3=error 4=not found) once a run has completed.',
        },
        {
          title: 'Keep DQ rules as versioned YAML (rules-as-code)',
          text: 'Export a job\'s current DQ rules to a YAML file under expectations/ with POST /api/expectations/export, review/edit them like any other source file, then POST /api/expectations/sync to push the YAML back into the job — the file\'s rules list fully replaces what was there. A suite naming a job that does not exist yet is reported, not treated as an error.',
          where: 'API -> POST /api/expectations/export and /sync',
        },
        {
          title: 'Read the schema-compatibility verdict',
          text: 'Schema snapshot diffs (History -> Schema sub-tab, or GET /api/jobs/{job}/schema-history) now include a compatibility grade: full (no change), non_breaking (new column, or a numeric type widening), risky (anything to/from object, datetime unit/tz changes), or breaking (column removed, or a numeric type narrowing). The overall grade on a diff is always its worst individual change.',
          where: 'History -> Schema sub-tab',
        },
        {
          title: 'Test a transform in isolation with TransformCase',
          text: 'For business logic that is not a straight source-vs-target reconciliation, write a pytest test using etl_framework.transform_testing.harness.TransformCase: it runs your transform SQL against in-memory DuckDB fixture tables (plain pandas DataFrames, no live DB) and reconciles the output against an expected DataFrame with the same comparison engine production jobs use. See tests/transforms/test_example_daily_revenue.py.',
          where: 'pytest -> tests/transforms/*.py',
        },
        {
          title: 'Use a base overlay and secret providers in the CLI config file',
          text: 'The standalone CLI runner (python -m etl_framework.runner.cli --config file.yml ...) supports an environments.base block merged under every named environment (its own keys win), and secret://<provider>/<name> values resolved at load time instead of read literally -- the built-in env provider reads an environment variable, and you can register others (Vault, Azure Key Vault, ...) via etl_framework.config.secrets.register_provider.',
          where: 'CLI config YAML -> environments.base / secret://',
        },
      ],
    },
    {
      id: 'adapters',
      title: 'Adapters',
      intro: 'Adapters connect to external systems: SAP BusinessObjects, Automic (UC4), and REST APIs. Browse, test, and import directly from the UI.',
      steps: [
        {
          title: 'Browse SAP BO reports',
          text: 'On the SAP BO section, browse documents, expand report tabs, and click Add to Catalog to create a bo_report job without typing IDs. Requires BO credentials in a saved config.',
          where: 'Adapters -> SAP BO',
        },
        {
          title: 'Check Automic jobs',
          text: 'On the Automic section, import jobs from a .json/.csv file, or browse & import by filter pattern (e.g. ETL_*). Each becomes an automic_job in the catalog.',
          where: 'Adapters -> Automic',
        },
        {
          title: 'Test and preview REST API endpoints',
          text: 'On the REST API section, test connectivity (one page) and preview a sample of parsed rows before wiring an endpoint into a job or comparison. Auth, headers, and pagination are per-endpoint.',
          where: 'Adapters -> REST API',
        },
      ],
    },
    {
      id: 'etl-testing-expansion',
      title: 'ETL Testing Expansion Features',
      intro: 'Use these steps after the five new feature branches are merged. They cover statistical validation, environment parameterization, policy-as-code, orchestrator integration, and business rule DSL.',
      steps: [
        {
          title: 'Add statistical validation rules',
          text: 'In Job Catalog, create or edit a job and open DQ Rules. Pick outlier_zscore, outlier_iqr, outlier_grubbs, distribution_ks_test, distribution_chi_square, distribution_anderson_darling, hypothesis_test_proportion, or anomaly_detection_sigma. Fill rule-specific inputs such as threshold, alpha, IQR multiplier, distribution, bins, expected frequencies, condition, expected proportion, or rolling window.',
          where: 'Launch -> Job Catalog -> DQ Rules',
          tip: 'Use outlier_zscore or outlier_iqr for simple numeric spikes. Use KS, chi-square, or Anderson-Darling when a column must match a known distribution.',
        },
        {
          title: 'Preview statistical checks from profile history',
          text: 'Run a profile job first so column metric history exists. Then call POST /api/jobs/{job_name}/profile/preview-rule with column, metric, and rule payload to test a statistical rule against historical null_rate, distinct_count, mean_val, std_val, p25, p50, p75, or p95.',
          where: 'API -> /api/jobs/{job_name}/profile/preview-rule',
          tip: 'Use mean_val with anomaly_detection_sigma to catch metric spikes across recent profile runs.',
        },
        {
          title: 'Run one selection across environments',
          text: 'Open a Job Selection launch modal and choose Run Across Environments. Enter environments as a comma-separated list such as dev, qa, prod. Set target_env_template such as {{ env_name }}_target, then provide optional Environment Overrides JSON for per-environment variables.',
          where: 'Launch -> Job Selections -> Launch -> Run Across Environments',
          tip: 'Each run receives variables.env_name and variables.current_env. Use those variables in SQL templates and params.',
        },
        {
          title: 'Use environment query templating',
          text: 'Add variables or templates to a saved config. In job SQL or params, reference them with {{ variable_name }} or dotted paths such as {{ source.env }}. Named connections inherit top-level variables/templates and override matching keys.',
          where: 'Config -> saved config; Launch -> job SQL/params',
          tip: 'Example: SELECT * FROM {{ table_prefix }}orders WHERE batch_env = {{ source.env }}.',
        },
        {
          title: 'Load and evaluate policy-as-code',
          text: 'Place YAML policies in ETL_POLICY_DIR or ./policies. Open Policies tab to view loaded rules. Use Evaluate Resource to paste a job, run, or config JSON payload and see pass, warn, or error verdicts before applying changes.',
          where: 'Policies tab',
          tip: 'error blocks job/config/run mutations. warn records policy_violations in run config_snapshot without blocking.',
        },
        {
          title: 'Create policy gates for jobs, runs, and configs',
          text: 'Use scope: job, run, or config. Add an optional condition and a required rule. Supported operators include equals, not_equals, not_empty, empty, contains, one_of, greater_than, and less_than. Use on_violation: error for hard gates and warn for advisory checks.',
          where: 'ETL_POLICY_DIR/*.yml or ./policies/*.yml',
          warn: 'Use error policies carefully on production configs; invalid policy logic can block launches until corrected.',
        },
        {
          title: 'Create orchestrator-backed jobs',
          text: 'Create a job with type airflow_dag, prefect_flow, or dagster_job. Set params.repo_path to the Python file containing the DAG, flow, or job. Save the job, then run it like any other catalog job.',
          where: 'Launch -> Job Catalog -> Job Type',
          tip: 'The parser uses Python AST and does not require Airflow, Prefect, or Dagster imports to execute.',
        },
        {
          title: 'Validate orchestrator task graphs',
          text: 'Call POST /api/jobs/{name}/orchestrator-validate to parse the definition file and return task_count plus extracted tasks. Airflow parser detects common operator task_id values. Prefect parser detects @task and @flow. Dagster parser detects @op and @job.',
          where: 'API -> /api/jobs/{name}/orchestrator-validate',
          tip: 'Simple task_a >> task_b dependency edges become upstream_task_ids.',
        },
        {
          title: 'Create reusable business rule DSL entries',
          text: 'Open Rules tab. Click + New Rule. Enter a name, category, tags, and DSL text. DSL rules wrap existing DQ rule types in a named reusable block, then save to the rule registry.',
          where: 'Rules tab -> + New Rule',
          tip: 'Example DSL: rule "orders_amount_positive" { type: positive_values column: amount severity: error }.',
        },
        {
          title: 'Evaluate DSL rules with sample rows',
          text: 'Select a saved rule in the Rules tab. Paste Evaluation Rows JSON such as [{"amount":10},{"amount":-1}], then click Evaluate Saved Rule. The UI calls POST /api/rules/{name}/evaluate and shows violations JSON.',
          where: 'Rules tab -> Evaluate Saved Rule',
          tip: 'DSL compiles to the existing DQRule model, so violations use the same DQEngine behavior as inline rules.',
        },
      ],
    },
    {
      id: 'tips',
      title: 'Tips & Troubleshooting',
      intro: 'Common gotchas and quick fixes.',
      steps: [
        {
          title: 'Blank or unstyled UI after deploy',
          text: 'Check the browser Network tab for 404s on vendor/tailwind.css, vendor/alpine.min.js, vendor/chart.umd.min.js. Verify frontend/vendor/ shipped with the deployment.',
          where: 'Browser DevTools -> Network',
        },
        {
          title: 'Live progress not streaming',
          text: 'If Monitor events lag, a reverse proxy is buffering SSE. Set proxy_buffering off (nginx) or disable IIS response buffering. The UI falls back to polling automatically.',
          where: 'Reverse proxy config',
        },
        {
          title: 'SQL Server connection fails',
          text: 'Install Microsoft ODBC Driver 17 or 18 for SQL Server and ensure db_driver matches (default "ODBC Driver 17 for SQL Server").',
        },
        {
          title: 'SAP BO 401 on AD account',
          text: 'Set bo_auth_type to secWinAD for Active Directory-only accounts. Logging in with secEnterprise returns HTTP 401 even with correct credentials.',
        },
        {
          title: 'Token lost after closing tab',
          text: 'Tokens live in sessionStorage and clear when the tab closes. Re-open the auth modal and re-activate. Consider a longer-lived standard token for automation.',
        },
        {
          title: 'Use keyboard shortcuts',
          text: 'Ctrl/Cmd+S saves (job modal or compare template). Enter launches (jobs) or runs the active compare. Escape closes any open modal or the field help popup.',
        },
      ],
    },
  ];

  global.ETL_HELP = { sections };
})(window);
