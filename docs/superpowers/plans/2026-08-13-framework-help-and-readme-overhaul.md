# ETL Test Framework Help & README Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Overhaul the ETL Test Framework documentation in both `README.md` and the Web UI Help Center (`frontend/help-content.js` / `tab-help.html`) with a "For Dummies" plain-language guide, tab-by-tab field reference, task scenarios, and option decision matrices while eliminating visual clutter.

**Architecture:** Update `frontend/partials/tab-help.html` to render visual category badges and `when` usage blocks, update `frontend/help-content.js` with structured data, and rewrite `README.md` with collapsible details blocks and formatted markdown decision tables.

**Tech Stack:** JavaScript (Alpine.js), HTML5, Markdown, Playwright E2E / Pytest.

## Global Constraints

- **Help Data Model**: `window.ETL_HELP.sections[]` with `{ id, title, category, intro, steps }`.
- **Step Data Model**: `{ title, text, where?, when?, tip?, warn? }`.
- **Clean Layout**: Category badges, collapsible `<details>` blocks for field references in README, markdown decision tables.

---

### Task 1: Overhaul Web UI Help Center (`frontend/partials/tab-help.html` & `frontend/help-content.js`)

**Files:**
- Modify: `frontend/partials/tab-help.html`
- Modify: `frontend/help-content.js`
- Test: `tests/e2e/11-help.spec.ts`

**Interfaces:**
- Consumes: `window.ETL_HELP.sections` data model in Alpine.js component.
- Produces: Enhanced Help tab UI with visual category badges (`[Primer]`, `[Tab Reference]`, `[Scenario]`, `[Decision Matrix]`), `when` usage callouts, and comprehensive "For Dummies" content.

- [ ] **Step 1: Update `frontend/partials/tab-help.html` for category badges and `when` callouts**

Add `<template x-if="s.category">` badge rendering and `<template x-if="step.when">` block inside step cards.

```html
<template x-if="s.category">
  <span class="help-badge-cat" x-text="s.category"></span>
</template>
```

- [ ] **Step 2: Update `frontend/help-content.js` with comprehensive "For Dummies" content**

Expand `sections` in `frontend/help-content.js` to cover:
1. `getting-started-primer`: Core ETL concepts (Source vs Target, Reconcile, DQ Rules, Baseline Pinning, Contracts, WAP Gates).
2. `config-tab-ref`: Config Tab complete field reference (Saved Configs, Named Connections `hr_db`/`finance_db`, REST API Endpoints, Webhooks, Security Tokens).
3. `launch-tab-ref`: Launch Tab & Job Editor reference (Job types: `reconciliation`, `bo_report`, `sql_direct`, `multi_file_reconciliation`, `api_reconciliation`; 20 DQ Rule types; Run Settings: Parallel/Sequential, Retries, Tolerance, Schema Mismatch Policies, Chunking).
4. `monitor-history-ref`: Monitor & History reference (SSE streaming, Cancel run, Pytest runner, Baseline compare, Lineage DAG, Audit log).
5. `compare-differences-ref`: Compare & Differences reference (BO Compare, Dual-Env Compare, File Compare, SQL Direct, Mismatch Diff, Column Stats, Bulk Accept/Reject).
6. `contracts-gates-ref`: Contracts & Gates reference (SLA tracking, Breach alerts, Rules-as-Code).
7. `reports-adapters-ref`: Reports, Adapters & Logs reference (HTML reports, PDF export, Metric drift, SAP BO / Automic adapters, Global logs).
8. `scenarios-task-guides`: Task-based scenario walkthroughs (SQL Reconcile, BO Report Compare, REST API endpoints, Multi-File reconciliation, CI/CD pipeline quality gates).
9. `decision-matrices`: Option decision tables ("Which Job Type When?", "Which Compare Mode When?", "Which Schema Mismatch Policy When?", "Which DQ Rule Category When?").

- [ ] **Step 3: Run Playwright E2E tests to verify Help UI**

Run: `npx playwright test tests/e2e/11-help.spec.ts`
Expected: PASS

- [ ] **Step 4: Commit Task 1**

```bash
git add frontend/partials/tab-help.html frontend/help-content.js
git commit -m "docs(ui): overhaul web UI help center with for dummies guide and decision matrices"
```

---

### Task 2: Overhaul README.md ("For Dummies" Guide & Decision Tables)

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Project capabilities, API endpoints, CLI parameters, job types, and UI workflow specifications.
- Produces: Clean, structured, uncluttered `README.md` with Table of Contents, Dummies Primer, Tab-by-Tab Reference with collapsible `<details>` option tables, Task Scenario guides, and Markdown decision tables.

- [ ] **Step 1: Read current `README.md` structure and prepare updated layout**

Ensure Table of Contents links to all major sections:
- Executive Summary & Quick Start
- ETL Testing for Dummies — Core Concepts
- Tab-by-Tab & Option-by-Option Deep Dive
- Task-Based Scenario Walkthroughs
- "Which Option When?" Decision Matrices
- API Reference & CLI Options

- [ ] **Step 2: Update `README.md` with complete "For Dummies" content and layout refresh**

Add collapsible `<details><summary>Field & Option Breakdown</summary>...</details>` blocks for Config, Launch, Compare, and Run Settings to eliminate clutter while providing full option documentation. Include formatted markdown decision matrices for Job Types, Compare Modes, Schema Mismatch Policies, and DQ Rules.

- [ ] **Step 3: Run existing unit test suite to verify no regressions**

Run: `python run_tests.py`
Expected: PASS

- [ ] **Step 4: Commit Task 2**

```bash
git add README.md
git commit -m "docs: overhaul README with for dummies guide, task scenarios, and option decision matrices"
```
