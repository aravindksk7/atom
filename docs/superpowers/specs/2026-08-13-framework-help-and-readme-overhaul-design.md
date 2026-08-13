# Design Spec: ETL Test Framework Help & README Overhaul ("For Dummies" Guide & Layout Refresh)

**Date**: 2026-08-13  
**Status**: Draft (Approved in Brainstorming)  
**Target Files**:
- `README.md`
- `frontend/help-content.js`
- `frontend/partials/tab-help.html`

---

## 1. Executive Summary

This design provides a comprehensive overhaul of the documentation across the ETL Test Framework in both the `README.md` and the Web UI Help Center (`frontend/help-content.js`). It expands explanations across all functionalities and options in a "Framework for Dummies" format, providing step-by-step tab references, task-based scenario guides, and option-by-option decision matrices while refactoring layout structures to eliminate visual clutter.

---

## 2. Goals & Key Objectives

1. **"For Dummies" Plain-Language Explanations**:
   - Provide beginner-friendly definitions of core ETL testing concepts (Source/Target, Reconciliation, Data Quality rules, Hash Prechecks, Baseline Pinning, Data Contracts, Write-Audit-Publish gates).
   - Explain what every setting, dropdown, checkbox, and field does, when to use it, and what default value to choose.

2. **Tab-by-Tab & Option-by-Option Deep Dive**:
   - Cover all 11 UI tabs: Config, Launch, Monitor, History, Compare, Differences, Contracts, Reports, Adapters, Logs, and Sequences.
   - Detail job types (`reconciliation`, `bo_report`, `sql_direct`, `multi_file_reconciliation`, `api_reconciliation`) and all 20 Data Quality rule types.

3. **Task-Based Scenario Walkthroughs**:
   - Provide concrete step-by-step guides for common real-world tasks (SQL reconciliation, SAP BO report comparison, REST API data source reconciliation, multi-file data lake validation, CI/CD pipeline integration).

4. **"Which Option When?" Decision Matrices**:
   - Feature decision tables comparing Job Types, Compare Subtabs, Schema Mismatch Policies, DQ Rule Categories, and Authentication Modes.

5. **Visual Layout Improvements & Decluttering**:
   - **Web UI Help (`frontend/help-content.js` & `tab-help.html`)**: Category badges (`[Primer]`, `[Tab Reference]`, `[Scenario]`, `[Decision Matrix]`), structured step cards with `Where`, `When to use`, `Tip`, and `Caution` callouts.
   - **`README.md`**: Clean Table of Contents with jump links, formatted decision tables, visual code blocks, and collapsible `<details>` blocks for option tables.

---

## 3. Detailed Content Architecture

### 3.1 Web UI Help Data Structure (`frontend/help-content.js`)

`window.ETL_HELP.sections[]` entries follow this schema:

```json
{
  "id": "getting-started-primer",
  "title": "01. Getting Started — Primer for Beginners",
  "category": "Primer",
  "intro": "Beginner-friendly explanation of ETL testing concepts...",
  "steps": [
    {
      "title": "What is Reconciliation vs Direct Compare?",
      "text": "Detailed plain-language text...",
      "where": "Core Concepts",
      "when": "Use Reconciliation when comparing datasets row-by-row with primary keys...",
      "tip": "Start with simulation mode before connecting live DBs.",
      "warn": "Ensure primary key uniqueness to avoid false positive row mismatches."
    }
  ]
}
```

### 3.2 Sections Overview

1. **Getting Started — Primer for Beginners**: Core concepts, terminology, initial bootstrap token creation.
2. **Config Tab — Environments, Connections & Security**:
   - Config definition, live database connections.
   - Named DB Connections (`hr_db`, `finance_db`).
   - REST API Endpoints (Auth: none/API Key/Bearer/Basic; JSON dot-path, CSV; Cursor/Page pagination).
   - Security/Tokens & Webhook Notifications with HMAC-SHA256 signing.
3. **Launch Tab — Job Editor & Run Options**:
   - Environment labels & Config pickers.
   - Job Types (`reconciliation`, `bo_report`, `sql_direct`, `multi_file_reconciliation`, `api_reconciliation`).
   - 20 Data Quality Rules (Basic: `not_null`, `unique`, `row_count_min`... vs Advanced: `completeness_ratio`, `pii_mask_check`, `referential_check`, `custom_sql_assert`...).
   - Dependencies (`depends_on`), DAG sorting, upstream skip.
   - Explain Query dry-run check.
   - Run Settings reference (Parallel/Sequential, Max Retries & Backoff, Float Tolerance, Null Handling, Hash Precheck, Schema Mismatch Policies, Chunking, Live Connection Toggles).
   - Cron Scheduler expressions.
4. **Monitor & History Tabs — Real-Time Tracking & Telemetry**:
   - SSE streaming & 5s fallback polling.
   - Cooperative Run Cancellation (`POST /api/runs/{id}/cancel`).
   - Pytest Suite Runner (`POST /api/runs/test-suite`).
   - Baseline Pinning & Baseline Compare.
   - Job Lineage DAG & Telemetry Audit Log (`/api/audit`).
5. **Compare & Differences Tabs — In-Depth Analysis & Auditing**:
   - BO Report Compare (Live BO, File Path, Upload).
   - Dual-Environment Reconciliation Compare.
   - Recon File & Multi-File Compare.
   - SQL Direct Compare.
   - Mismatch Diff & Column Stats distribution.
   - Bulk Accept/Reject with user & note audit trail.
6. **Contracts, Gates & Schema Compatibility**:
   - Data Contracts & SLA tracking.
   - Write-Audit-Publish (WAP) pattern.
   - Rules-as-Code & Schema Mismatch Policies.
7. **Reports, Adapters & Logs**:
   - Themeable HTML reports & PDF exports.
   - Metric Drift analysis ($\sigma$-based rolling window).
   - SAP BO & Automic Adapters.
   - Searchable Global Logs & Scheduler Telemetry Reports.
8. **Task-Based Scenario Guides**:
   - Scenario 1: Reconciling SQL Databases.
   - Scenario 2: Comparing SAP BO Reports.
   - Scenario 3: Testing REST API Endpoints.
   - Scenario 4: Multi-File & Data Lake Reconciliation.
   - Scenario 5: CI/CD Pipeline Automation & Quality Gates.
9. **"Which Option When?" Decision Matrices**:
   - Job Type Matrix.
   - Compare Subtab Matrix.
   - Schema Mismatch Policy Matrix.
   - DQ Rule Type Matrix.

---

## 4. UI Layout & Visual Improvements

### 4.1 Web UI Help (`frontend/partials/tab-help.html`)
- Render category badges for section headers.
- Support `when` (When to use) property in step cards alongside `where`, `tip`, and `warn`.
- Streamlined flex layout and search highlighting.

### 4.2 README.md Formatting
- Comprehensive Table of Contents linking to all sections.
- Formatted decision matrices in Markdown tables.
- Collapsible `<details>` blocks for field-by-field reference to keep main body clean.

---

## 5. Verification Plan

1. **Unit & E2E Test Suite**:
   - Run `npx playwright test tests/e2e/11-help.spec.ts` (or equivalent test runner) to verify Help Center sections load, search filters work, and content renders cleanly.
2. **Web UI Manual Check**:
   - Verify layout rendering in browser (tab navigation, responsive cards, category badges, step search).
3. **README Formatting Check**:
   - Verify markdown rendering, table formatting, and table of contents links.
