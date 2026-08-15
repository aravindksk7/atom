/*
 * Help center content for the ETL Test Framework UI.
 * Consumed by the app's Help tab (see index.html / app.js).
 * Structure: window.ETL_HELP.sections[] -> { id, title, category, intro, steps[] }
 * Each step: { title, text, where?, when?, tip?, warn? }
 */
(function (global) {
  const sections = [
    {
      id: 'getting-started-primer',
      category: 'Primer',
      title: 'ETL Testing Primer (For Dummies)',
      intro: 'A plain-language guide to fundamental ETL testing concepts. Start here if you are new to data verification, source-vs-target comparisons, or automated quality gates.',
      steps: [
        {
          title: 'Source vs Target Systems',
          text: 'The Source system is where raw data originates (e.g. operational PostgreSQL, REST APIs, daily CSV files). The Target system is where data lands after transformation (e.g. Snowflake data warehouse, BI reporting tables). ETL testing proves that no data was lost, corrupted, or miscalculated between source and target.',
          where: 'Config -> Connections / Launch -> Job Catalog',
          when: 'Planning new data pipelines or validating data migrations.',
          tip: 'Always define clear primary keys or unique join columns so rows can be matched 1-to-1.',
        },
        {
          title: 'Data Reconciliation',
          text: 'Reconciliation is the automated row-by-row and cell-by-cell comparison of datasets between source and target. It checks row counts, schema alignment, and exact field value equality within defined tolerances.',
          where: 'Launch -> Job Editor (Job Type: reconciliation) / Compare tab',
          when: 'Validating ETL batch runs, migration projects, or scheduled data syncs.',
        },
        {
          title: 'Data Quality (DQ) Rules',
          text: 'DQ rules are assertions applied to datasets to ensure integrity. Examples include checking for nulls (not_null), uniqueness (unique), value range bounds, pattern matching (match_regex), and statistical distribution anomalies.',
          where: 'Launch -> Job Editor -> DQ Rules',
          when: 'Enforcing business rules and schema constraints on incoming data.',
        },
        {
          title: 'Baseline Pinning',
          text: 'Baseline pinning locks a known-good dataset profile or run result as an authoritative golden standard. Future runs are compared against this pinned baseline to immediately detect unexpected metric drift or schema changes.',
          where: 'History tab -> Pin Baseline button',
          when: 'Protecting critical financial models or regulatory reports against regressions.',
        },
        {
          title: 'Data Contracts & SLAs',
          text: 'A Data Contract formalizes expectations between data producers and data consumers. It defines ownership, SLA timelines, schema snapshots, and required DQ pass thresholds. If data breaks the contract, a breach is logged.',
          where: 'Contracts tab',
          when: 'Establishing clear governance across multi-team data architectures.',
        },
        {
          title: 'Write-Audit-Publish (WAP) Quality Gates',
          text: 'WAP is a deployment pattern: stage new data (Write), run automated reconciliation and DQ rules (Audit), and publish to production tables ONLY if tests pass (Publish). If tests fail, the gate holds the data to prevent bad data from reaching users.',
          where: 'Launch -> Job Catalog -> Gate button / API / CLI',
          when: 'Preventing corrupted batch loads from entering production databases.',
        },
      ],
    },
    {
      id: 'config-tab-ref',
      category: 'Tab Reference',
      title: 'Config Tab & Connection Reference',
      intro: 'Detailed field reference for environment configurations, database connections, API endpoints, webhooks, and security settings.',
      steps: [
        {
          title: 'Saved Configurations',
          text: 'Configurations store reusable environment parameters, database credentials, and global defaults under a named profile (e.g. dev_config, prod_config). Selecting a active config injects connection details into runs.',
          where: 'Config -> Saved Configs dropdown',
          when: 'Switching between Development, Staging, and Production test environments.',
        },
        {
          title: 'Named Connections (hr_db / finance_db)',
          text: 'Define alias connections for databases such as PostgreSQL, Snowflake, SQL Server, Oracle, and DuckDB. Reference these aliases (e.g. hr_db or finance_db) in jobs so connection strings stay centralized and secure.',
          where: 'Config -> Connections sub-tab',
          when: 'Setting up database credentials for source and target environments.',
          tip: 'Use standard connection strings or URI formats with driver parameters for SQL Server.',
        },
        {
          title: 'REST API Endpoints',
          text: 'Configure base URLs, default authentication (Bearer tokens, API keys, Basic Auth), custom headers, and pagination parameters for external API data sources.',
          where: 'Config -> REST API Endpoints sub-tab',
          when: 'Reconciling REST microservices or webhooks against backend databases.',
        },
        {
          title: 'Webhooks & Notifications',
          text: 'Register webhook URLs to receive real-time HTTP POST alerts on run events (run.started, run.passed, run.failed, contract.breached). Payloads can be signed with HMAC-SHA256 for verification.',
          where: 'Config -> Notifications sub-tab',
          when: 'Integrating test alerts into Slack, Microsoft Teams, PagerDuty, or Webhook receivers.',
        },
        {
          title: 'Security Tokens & Secret Encryption',
          text: 'All sensitive passwords, secret tokens, and API credentials are standardly masked in the UI and encrypted at rest in storage. Tokens live in session memory and reset on browser close.',
          where: 'Config -> Security / Settings',
          when: 'Managing user session security and encryption settings.',
          warn: 'Never commit hardcoded secrets in JSON configuration files.',
        },
      ],
    },
    {
      id: 'launch-tab-ref',
      category: 'Tab Reference',
      title: 'Launch Tab: Job Design, Scheduling & Automation Reference',
      intro: 'Complete field reference for defining saved jobs, selecting job types, configuring 20+ DQ rules, setting pass conditions, tuning execution settings, and scheduling automation.',
      steps: [
        {
          title: 'Job Catalog & Editor',
          text: 'The Job Catalog manages all saved test definitions. Click + New Job to open the editor. Jobs are saved idempotently and can be executed from UI, API, CLI, or schedules.',
          where: 'Launch -> Job Catalog',
          when: 'Creating, updating, or organizing test cases.',
        },
        {
          title: 'Job Types (reconciliation, bo_report, sql_direct, multi_file, api)',
          text: 'Choose the appropriate job type: reconciliation (SQL row-by-row compare), bo_report (SAP BO WebI/Crystal validation), sql_direct (single-query SQL assertion), multi_file_reconciliation (S3/SFTP/local batch file compare), or api_reconciliation (REST endpoint compare).',
          where: 'Job Editor -> Job Type',
          when: 'Matching the test engine to the data storage technology.',
        },
        {
          title: 'Data Quality (DQ) Rule Types',
          text: 'Stack up to 20+ built-in DQ rule types including not_null, unique, min_row_count, max_row_count, match_regex, completeness_ratio, outlier_zscore, distribution_ks_test, and custom_sql checks.',
          where: 'Job Editor -> DQ Rules -> + Add Rule',
          when: 'Enforcing strict column-level and dataset-level constraints.',
        },
        {
          title: 'Run Settings & Performance Tuning',
          text: 'Tune execution parameters: Parallel vs Sequential mode, Max Workers (concurrency), Retries on failure, Float Tolerance (e.g. 0.001), Null Handling, Hash Precheck (fast path), Chunk Size (for large tables), and Schema Mismatch Policy (Fail / Ignore / Coerce).',
          where: 'Launch -> Run Settings',
          when: 'Optimizing performance for multi-million row datasets.',
        },
        {
          title: 'Job Dependencies & Topological Sorting',
          text: 'Set Depends On to require upstream jobs to pass before dependent jobs execute. The execution engine performs topological sorting and automatically skips downstream jobs if upstream fails.',
          where: 'Job Editor -> Depends On',
          when: 'Orchestrating multi-stage pipeline verification workflows.',
        },
        {
          title: 'Run from external pytest',
          text: 'Execute saved jobs directly from external pytest test suites using python test runners. Pytest test fixtures instantiate the runner, invoke saved job definitions, and assert pass status.',
          where: 'Terminal / Pytest suite',
          when: 'Integrating ETL tests directly into Python test suites and test runners.',
        },
        {
          title: 'Gate CI/CD pipelines',
          text: 'Enforce quality gates in CI/CD pipelines by invoking atom CLI or API gate endpoints. If tests pass, CI/CD proceeds to deployment; if tests fail, CI/CD aborts automatically.',
          where: 'CI/CD pipeline scripts (GitHub Actions / Jenkins)',
          when: 'Blocking broken data pipelines from promoting to production.',
        },
        {
          title: 'Cron Expression & Recurring Schedules',
          text: 'Schedule recurring test runs using standard 5-field APScheduler cron expressions (min hour dom month dow — e.g. 0 6 * * * = 6am daily, 0 6 * * 1-5 = weekdays at 06:00, */15 * * * * = every 15 min). Runs fire according to the application timezone set in Config. Use Run Now to trigger immediately.',
          where: 'Launch -> Schedules sub-tab (+ New Schedule)',
          when: 'Automating recurring data quality checks and daily reconciliation jobs.',
        },
      ],
    },
    {
      id: 'monitor-history-ref',
      category: 'Tab Reference',
      title: 'Monitor, History & CLI & CI/CD Reference',
      intro: 'Monitor live execution, inspect historical run records, analyze lineage DAGs, run CLI tools, and parse CI/CD build artifacts.',
      steps: [
        {
          title: 'Live Progress Streaming (SSE)',
          text: 'The Monitor tab provides live progress feedback via Server-Sent Events (SSE). View status indicators (QUEUED, RUNNING, PASSED, FAILED, SKIPPED), percent complete, and live log output.',
          where: 'Monitor tab',
          when: 'Observing execution progress for long-running batch test suites.',
        },
        {
          title: 'Cancel Running Jobs',
          text: 'Request cooperative cancellation of queued or actively running test runs. The runner cleans up temporary tables and database connections gracefully.',
          where: 'Monitor tab -> Cancel Run button',
          when: 'Stopping misconfigured or runaway test runs.',
        },
        {
          title: 'History & Baseline Comparison',
          text: 'The History tab stores durable run execution logs, mismatch summaries, HTML/PDF report downloads, and baseline comparison tools for evaluating run-over-run diffs.',
          where: 'History tab',
          when: 'Reviewing past test results or auditing data quality history.',
        },
        {
          title: 'Lineage DAG & Segment Drilldown',
          text: 'Visualize data lineage graph dependencies and drill down into failed low-cardinality data segments to pinpoint exact failure buckets.',
          where: 'History tab -> Lineage / Segment Drilldown sub-tabs',
          when: 'Debugging root causes for mismatched data records.',
        },
        {
          title: 'Audit Log & Governance',
          text: 'Review system audit trails tracking job edits, configuration changes, user logins, and contract breach histories.',
          where: 'History tab -> Audit Log',
          when: 'Demonstrating regulatory compliance and security auditing.',
        },
        {
          title: 'Launch and gate with atom run',
          text: 'Use the standalone atom run CLI command to launch test runs directly from terminal or shell scripts. Supports parameters for config ID, job selection, environments, and quality gates.',
          where: 'Terminal: atom run --config 1 --jobs job_a,job_b',
          when: 'Executing tests from command line interface or automation wrappers.',
        },
        {
          title: 'Collect JUnit and run artifacts',
          text: 'Export test results in standard JUnit XML format using GET /api/runs/{run_id}/junit or atom report CLI. CI tools like Jenkins and GitHub Actions parse JUnit files to display test trends.',
          where: 'API / CLI: atom report RUN_ID --format junit',
          when: 'Integrating test output into CI/CD build reporting tabs.',
        },
        {
          title: 'Read gate exit codes',
          text: 'Understand CLI exit statuses: exit code 0 means passed, 1 failed, 2 cancelled, 3 run error, 4 selection/run not found, 5 auth/connection error, and exit code 6 timed out while waiting.',
          where: 'Terminal / CI script status checks',
          when: 'Writing shell scripts that handle pipeline status codes.',
        },
      ],
    },
    {
      id: 'compare-differences-ref',
      category: 'Tab Reference',
      title: 'Compare & Differences Reference',
      intro: 'Interactive comparison hub for ad-hoc dataset diffing: SAP BusinessObjects report compare, dual-environment SQL compare, local multi-file comparison, column statistics, and mismatch resolution.',
      steps: [
        {
          title: 'Compare all tabs in a BO document',
          text: 'Interactively compare all tabs in a SAP BusinessObjects document across environments or documents side-by-side with full prompt parameter passing.',
          where: 'Compare tab -> SAP BO Report Compare',
          when: 'Validating BI report formatting and metric values during SAP BO upgrades.',
        },
        {
          title: 'Dual-Environment & Direct SQL Compare',
          text: 'Execute direct SQL queries against Source (e.g. dev) and Target (e.g. prod) databases to compare results immediately without saving a permanent job definition.',
          where: 'Compare tab -> Dual-Env / SQL Direct',
          when: 'Sanity-checking ad-hoc SQL fixes or migration patches.',
        },
        {
          title: 'Local Multi-File Compare',
          text: 'Compare local directory trees of CSV/Parquet files using automated similarity matching or explicit token rules before building permanent jobs.',
          where: 'Compare tab -> Multi-File',
          when: 'Inspecting landed batch files on local disk.',
        },
        {
          title: 'Use Column Stats for large tables',
          text: 'Calculate statistical summaries (null count, distinct count, min/max, mean, stddev) for columns in large tables to verify data distributions without full row comparisons.',
          where: 'Differences tab -> Column Stats',
          when: 'Profiling huge datasets where row-by-row comparison is too costly.',
        },
        {
          title: 'Compare mismatches across runs',
          text: 'Use Mismatch Diff to compare mismatched rows across consecutive runs, highlighting newly introduced errors or resolved issues.',
          where: 'Differences tab -> Mismatch Diff sub-tab',
          when: 'Tracking regression resolution across multiple test executions.',
        },
        {
          title: 'Bulk Accept/Reject Mismatches',
          text: 'Review identified mismatches and apply bulk sign-offs (Accept with variance reason or Reject). Accepted variances are excluded from severity counts.',
          where: 'Differences tab -> Bulk Actions',
          when: 'Handling known business logic exceptions or rounding variances.',
        },
      ],
    },
    {
      id: 'contracts-gates-ref',
      category: 'Tab Reference',
      title: 'Contracts, Gates & Rules-as-Code Reference',
      intro: 'Data governance features: Data Contracts, SLA tracking, breach management, Write-Audit-Publish promotion gates, and version-controlled Rules-as-Code.',
      steps: [
        {
          title: 'Data Contract Management',
          text: 'Create and manage contracts specifying source job, ownership, SLA timeline in hours, consumers, and required quality rules. Incremental versioning tracks contract evolution.',
          where: 'Contracts tab -> + New Contract',
          when: 'Formalizing data sharing commitments between engineering teams.',
        },
        {
          title: 'SLA Breach Lifecycle & Alerts',
          text: 'Monitor contract status badges (OK, BREACHED, OVERDUE). Open breaches automatically fire webhooks, track duration hours, and auto-resolve when passing runs resume.',
          where: 'Contracts tab -> Breach History',
          when: 'Enforcing operational SLAs for critical business data feeds.',
        },
        {
          title: 'Write-Audit-Publish (WAP) Promotion Gates',
          text: 'Evaluate POST /api/gates/{job}/evaluate before publishing staging data. Gates check pass status, active contract breaches, and policy compliance before returning PROMOTE or HOLD.',
          where: 'Launch -> Job Catalog -> Gate button / API',
          when: 'Automating promotion decisions in data orchestration tools like Airflow or Prefect.',
        },
        {
          title: 'Rules-as-Code (Expectations Sync)',
          text: 'Export job DQ rules to versioned YAML files using POST /api/expectations/export, store them in Git, and sync changes back into jobs using POST /api/expectations/sync.',
          where: 'API -> /api/expectations/export & /sync',
          when: 'Managing data quality rules inside Git repositories alongside code.',
        },
      ],
    },
    {
      id: 'reports-adapters-ref',
      category: 'Tab Reference',
      title: 'Reports, Adapters & Logs Reference',
      intro: 'Reporting engines, external job adapters for SAP BO and Automic, metric drift tracking, and global server logs.',
      steps: [
        {
          title: 'Interactive HTML Reports & PDF Export',
          text: 'View rich standalone HTML execution reports complete with mismatch tables, summary charts, and SQL queries. Export clean PDF reports for executive presentation.',
          where: 'Reports tab / History tab -> Download HTML/PDF',
          when: 'Sharing data quality audit results with non-technical stakeholders.',
        },
        {
          title: 'Metric Drift Analysis',
          text: 'Track numeric metric trends (row counts, null rates, average values) over time to detect gradual data degradation or unexpected drops.',
          where: 'Reports tab -> Metric Drift',
          when: 'Monitoring long-term data health across daily batch runs.',
        },
        {
          title: 'SAP BusinessObjects Adapter',
          text: 'Browse SAP BO folder hierarchies, inspect WebI document structures, and import report definitions directly into job catalog.',
          where: 'Adapters tab -> SAP BO',
          when: 'Setting up automated testing for SAP BO reporting environments.',
        },
        {
          title: 'Automic (UC4) Workload Adapter',
          text: 'Import Automic batch job definitions and map execution statuses into framework monitoring.',
          where: 'Adapters tab -> Automic',
          when: 'Integrating with enterprise Automic workload automation schedulers.',
        },
        {
          title: 'Global Logs & Event Viewer',
          text: 'Inspect real-time system logs, FastAPI application events, database query logs, and exception stack traces.',
          where: 'Logs tab',
          when: 'Troubleshooting server errors or failed database connections.',
        },
      ],
    },
    {
      id: 'scenarios-task-guides',
      category: 'Scenario',
      title: 'Task-Based Scenario Walkthroughs',
      intro: 'Step-by-step instructions for completing common data engineering and validation tasks.',
      steps: [
        {
          title: 'Scenario 1: Reconciling PostgreSQL vs Snowflake (SQL Reconcile)',
          text: '1) Open Config -> Connections and add hr_pg (PostgreSQL) and hr_sf (Snowflake). 2) Open Launch -> + New Job and select type "reconciliation". 3) Set Source Query: SELECT emp_id, salary FROM hr.employees and Target Query: SELECT employee_id, base_salary FROM dw.emp_dim. 4) Set Key Column: emp_id / employee_id. 5) Click Validate Query then Save. 6) Select the job and click Run Tests.',
          where: 'Config -> Launch -> Monitor',
          when: 'Validating data warehouse ETL migrations.',
        },
        {
          title: 'Scenario 2: Validating SAP BO Reports During Migration',
          text: '1) Open Adapters -> SAP BO and locate document ID 12345. 2) Click Add to Catalog to generate job. 3) Configure prompt values for fiscal period in Report Parameters. 4) Set Source Env: bo_41 and Target Env: bo_43. 5) Execute job and open HTML report to verify table metrics.',
          where: 'Adapters -> Launch -> Reports',
          when: 'Testing SAP BusinessObjects platform upgrades.',
        },
        {
          title: 'Scenario 3: Testing REST API Microservices Data Ingestion',
          text: '1) Open Config -> REST API Endpoints and add /api/v1/orders endpoint with Bearer auth. 2) Create job type "api_reconciliation". 3) Set Source URL and Target Database query. 4) Add DQ rule "not_null" on order_id. 5) Execute run and inspect mismatch drawer.',
          where: 'Config -> Launch -> Differences',
          when: 'Verifying API data pipeline landing tables.',
        },
        {
          title: 'Scenario 4: Multi-File S3 Batch Reconciliation',
          text: '1) Create reconciliation job with Input Source set to "Multiple Files". 2) Set Source Kind: S3 and Target Kind: S3 with bucket roots. 3) Select Explicit matching strategy on date tokens. 4) Click Preview Mapping to verify pairs. 5) Save and run batch comparison.',
          where: 'Launch -> Job Editor -> Input Source: Multiple Files',
          when: 'Reconciling daily S3 file dumps across environments.',
        },
        {
          title: 'Scenario 5: Setting Up CI/CD Quality Gates',
          text: '1) Save reusable job in catalog. 2) In GitHub Actions pipeline step, run: atom run --config 1 --jobs order_recon --gate. 3) Check exit code: 0 = PASS (proceed to publish step), non-zero = FAILED (abort build).',
          where: 'CI/CD pipeline script / CLI',
          when: 'Automating quality gates in CI/CD deployment pipelines.',
        },
      ],
    },
    {
      id: 'decision-matrices',
      category: 'Decision Matrix',
      title: 'Option Decision Matrices',
      intro: 'Decision tables to help you pick the right job types, comparison modes, schema policies, and quality rules for your use case.',
      steps: [
        {
          title: 'Which Job Type When?',
          text: 'Use "reconciliation" for standard SQL table/file comparison. Use "bo_report" for SAP BO report validation. Use "sql_direct" for single-table assertion checks. Use "multi_file_reconciliation" for S3/SFTP/local batch folder comparisons. Use "api_reconciliation" for REST API endpoint verification.',
          where: 'Launch -> Job Editor -> Job Type',
          when: 'Selecting a job type during job creation.',
        },
        {
          title: 'Which Compare Mode When?',
          text: 'Use "Full Row-by-Row" for complete authoritative audit checks. Use "Shadow Sampled (2%)" for quick PR verification in CI/CD. Use "Column Stats" for initial profiling of massive multi-billion row tables. Use "Baseline Compare" for regression testing against golden snapshots.',
          where: 'Launch -> Run Settings / Differences tab',
          when: 'Choosing evaluation depth based on performance and dataset size.',
        },
        {
          title: 'Which Schema Mismatch Policy When?',
          text: 'Use "Fail" (default) when schema strictness is mandatory and missing columns are fatal errors. Use "Ignore" when source/target intentionally have different metadata columns. Use "Coerce" when data types differ slightly (e.g. INT vs BIGINT) but values are compatible.',
          where: 'Launch -> Run Settings -> Schema Mismatch Policy',
          when: 'Handling schema differences between source and target tables.',
        },
        {
          title: 'Which DQ Rule Category When?',
          text: 'Use "Completeness" (not_null) for required fields. Use "Uniqueness" (unique) for primary key columns. Use "Range/Regex" (min/max bounds, match_regex) for formatted codes (email, phone, SSN). Use "Statistical" (z-score, IQR, KS test) for anomaly detection on metrics. Use "Custom SQL" for complex multi-table domain logic.',
          where: 'Launch -> Job Editor -> DQ Rules',
          when: 'Designing dataset quality assertions.',
        },
      ],
    },
  ];

  global.ETL_HELP = { sections };
})(window);
