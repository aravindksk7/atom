# Comprehensive Web UI & CLI Help Center Design

- **Date:** 2026-09-16
- **Status:** Approved
- **Topic:** Comprehensive Web UI Help Center with Interactive UI Mockups and Corresponding CLI Execution Examples

---

## 1. Overview & Goals

The Atom ETL Test Framework provides an extensive set of capabilities spanning data reconciliation, DAG execution sequences, SAP Data Services & BusinessObjects integration, AWS Cloud verification (S3, Glue, Athena, Airflow), file server workflows, data contracts, and CI/CD gating.

Currently, the Web UI Help Center (`frontend/help-content.js`, `frontend/partials/tab-help.html`) contains text-based steps and callouts. This design upgrades the Help Center into a comprehensive, multi-modal reference where **every Web UI feature** is paired with:
1. **Clear Web UI walkthrough steps** with breadcrumb navigation (`Where`) and use-case recommendations (`When to use`).
2. **Interactive UI Visual Mockups** (light/dark theme-responsive UI cards representing exact form inputs, buttons, tables, badges, and DAG canvases without external image dependencies).
3. **Exact Command Line (CLI) Execution Equivalents** (copyable CLI commands using `atom run`, `atom report`, `atom selections`, `atom runs`, script wrappers, curl/API calls, and python runner CLI).
4. **Sample Terminal Outputs & Exit Code Explanations** to guide automated pipeline scripting and debugging.

---

## 2. Architecture & Data Model

### 2.1 Help Data Schema (`frontend/help-content.js`)

The `window.ETL_HELP.sections[]` array will be expanded to support structured visual UI mockups and CLI command specifications on steps:

```typescript
interface HelpStepElement {
  type: 'input' | 'select' | 'button' | 'badge' | 'table' | 'dag' | 'status' | 'code' | 'metric';
  label?: string;
  value?: string;
  highlight?: boolean;
  status?: 'passed' | 'failed' | 'running' | 'queued' | 'warn' | 'info';
  columns?: string[];
  rows?: Array<string[]>;
}

interface HelpStepMockup {
  badge?: string;       // e.g. 'MODAL', 'TAB VIEW', 'DAG CANVAS', 'INSPECTOR'
  title?: string;       // Header title for the mockup window
  elements: HelpStepElement[];
}

interface HelpStepCliParam {
  flag: string;
  desc: string;
}

interface HelpStepExitCode {
  code: number;
  meaning: string;
}

interface HelpStepCli {
  command: string;           // Exact copyable command
  description?: string;      // Summary of CLI operation
  params?: HelpStepCliParam[]; // Flag definitions
  sampleOutput?: string;     // Simulated stdout/stderr
  exitCodes?: HelpStepExitCode[];
}

interface HelpStep {
  title: string;
  text: string;
  where?: string;
  when?: string;
  tip?: string;
  warn?: string;
  uiMockup?: HelpStepMockup;
  cli?: HelpStepCli;
}
```

### 2.2 Alpine.js Help Component Extensions (`frontend/app-help.js`)

- **`copyCliCommand(command, event)`:** Copies the clean shell command to clipboard and triggers a brief visual "Copied!" feedback state on the target button.
- **`helpStepMatches(step, search)`:** Enhanced to search not only `step.title` and `step.text`, but also `step.where`, `step.tip`, `step.warn`, `step.cli?.command`, `step.cli?.params`, and `step.uiMockup?.elements`.

---

## 3. UI Component Templates & Styling (`frontend/partials/tab-help.html` & `frontend/styles.css`)

### 3.1 UI Mockup Card (`.help-mockup`)
Rendered within the help step body:
- Header bar with window control dots and component type badge (e.g., `MODAL: Launch Sequence`, `INSPECTOR: Cell Difference`).
- Responsive visual elements:
  - Form inputs and dropdowns with pre-filled sample values.
  - Action buttons (e.g. `Run sequence`, `CI/CD`, `Restart from failure`).
  - Status badges (`PASSED`, `FAILED`, `BREACHED`, `OK`).
  - DAG node blocks showing upstream -> downstream relationships.
  - Mini difference comparison tables (Source vs Target mismatch highlights).

### 3.2 CLI Command Panel (`.help-cli-box`)
- Monospace command line bar with syntax highlighting cues.
- One-click **Copy** button with clipboard feedback.
- Clean flag-and-description summary grid.
- Expandable / styled terminal output box with simulated standard output, JSON payloads, or JUnit test output.

---

## 4. Comprehensive 14-Module Content Specifications

The Help Center will comprehensively cover all 14 major system domains:

1. **ETL Testing Primer & Fundamental Concepts:**
   - Source vs Target architectures, Write-Audit-Publish (WAP) pattern, Data Quality Pillars (Completeness, Uniqueness, Validity, Timeliness, Reconciliation).
   - CLI execution: `atom --help`, `python -m etl_framework.runner.cli --help`.

2. **Config, Connections & Security:**
   - Database connection strings, credentials encryption, SAP DS/BO connection endpoints, AWS profiles, Custom Runtime Variables (`{{VARIABLE_NAME}}`), CI Trigger Scoped Tokens.
   - CLI execution: Token generation, API config inspection via `curl`.

3. **Launch & Job Catalog:**
   - Creating table comparison jobs, custom SQL query jobs, parameterized queries with `{env}`, SAP DS / BO jobs, batch date iteration (`Repeat execution`).
   - CLI execution: `atom run "<job_name>" --source-env dev --target-env qa`, `atom selections`.

4. **Execution Sequences (DAGs):**
   - Drag-and-drop DAG builder, node dependencies (`Depends On`), topological execution, parallel branch handling, multi-day business date batch repetition.
   - CLI execution: `atom run "<sequence_name>" --target-type sequence --source-env prod --var BUSINESS_DATE=2026-09-16`.

5. **Compare & Data Verification Engines:**
   - Database reconciliation, SAP BO Web Intelligence doc compare, CSV/Parquet/ORC file compare, Column Stats mode for multi-million row tables, Hash prechecks, Null handling rules.
   - CLI execution: `atom run "<compare_job>" --source-env dev --target-env qa`.

6. **Differences & Mismatch Inspector:**
   - Granular cell-level differences view, diff search and column filtering, baseline setting, outcome overrides (approving known rounding variances), run-over-run mismatch diffing.
   - CLI execution: `atom report <RUN_ID> --format json | jq '.differences'`.

7. **Monitor & Live Execution Streaming:**
   - Live SSE progress updates, active worker threads, duration trackers, live log tailing, in-flight run cancellation.
   - CLI execution: `atom run "<target>" --poll-interval 5 --timeout 3600 --no-wait`.

8. **History, Lineage & Recovery:**
   - Run history search, low-cardinality failure segment drilldowns, automated DQ rule suggestions, full CSV run export, Restart-from-Failure (skipping passed steps).
   - CLI execution: `atom runs --limit 50`, `atom report <RUN_ID> --format csv --out run.csv`.

9. **Data Contracts & Promotion Quality Gates:**
   - Contract schema definitions, SLA breach detection (duration hours, escalation), owner notifications, automated webhook resolution, CI promotion blocker gates.
   - CLI execution: `atom run ... --junit-out gate.xml` with exit code evaluation.

10. **AWS Cloud Services Integration:**
    - S3 object metadata & pyarrow format validation, Glue Data Catalog schema comparison, Glue Spark job execution, Athena queries with DQ metric assertions, Airflow DAG trigger & polling.
    - CLI execution: `python -m etl_framework.runner.cli --config aws_prod --source-env s3_raw`.

11. **File Servers & Storage:**
    - Local directory, SFTP remote servers, S3 buckets, Windows SMB network shares, file browser, delimiter & header inspector.
    - CLI execution: Automated file ingestion triggering via CLI.

12. **Enterprise Adapters (SAP DS, SAP BO, Automic UC4, Airflow):**
    - SAP DS global variable substitution (`$G_BUSINESS_DATE`), SAP BO prompt answering, Automic batch job import, Airflow DAG monitoring.
    - CLI execution: `atom run "<sap_job>" --var RUN_DATE=2026-09-16`.

13. **Reports, Scheduler Statistics & Analytics:**
    - Failure trends, pass rate metrics, scheduler operational health, downloadable HTML / PDF summaries, aggregate success rate gating.
    - CLI execution: `python -m etl_framework.runner.cli --scheduler-stats --fail-on-stopped --min-success-rate 95.0`, `atom report <RUN_ID> --format html --out report.html`.

14. **CI/CD Automation, GitLab Native Signals & Global Logs:**
    - Direct `atom run` vs `scripts/ci/run-atom-target.sh` wrapper, GitLab commit status badges (`atom/selection/<slug>`), sticky MR markdown comments, exit code mappings (0-6), real-time system logs.
    - CLI execution: Full `.gitlab-ci.yml` and GitHub Actions workflow examples.

---

## 5. Verification & Testing

1. **HTML Compilation:** Run `node scripts/build-html.js` to ensure clean generation of `frontend/index.html` from `frontend/partials/tab-help.html`.
2. **Data Structure Validation:** Verify `frontend/help-content.js` syntax, structure, and property definitions.
3. **E2E Test Suite (`tests/e2e/11-help.spec.ts`):**
   - Verify sidebar lists all sections.
   - Verify search filters match both UI mockup terms and CLI commands (e.g. `atom run`, `--target-type sequence`, `scripts/ci/run-atom-target.sh`, `Restart from failure`).
   - Verify deep linking (`/?tab=help`) and responsive sidebar navigation.
