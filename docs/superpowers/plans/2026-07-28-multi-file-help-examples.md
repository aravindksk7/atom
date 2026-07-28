# Multi-File Help Examples Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update markdown help and Web UI Help Center content with detailed multi-file comparison steps and copyable filename/regex examples.

**Architecture:** Keep the existing help rendering and navigation unchanged. Add detailed source-of-truth guidance to `docs/multi_file_reconciliation.md`, add condensed Help Center steps in `frontend/help-content.js`, then rebuild `frontend/index.html` from partial/content sources.

**Tech Stack:** Markdown documentation, Alpine.js help content, Playwright help E2E, Node HTML build.

## Global Constraints

- Do not change backend comparison behavior.
- Preserve existing Help Center data shape in `frontend/help-content.js`.
- Keep examples aligned with supported token formats: `{name}`, `{batch:num}`, `{code:alpha}`, `{id:alnum}`, `{anything:any}`, `{date:%Y%m%d}`, `{custom:regex(...)}`, `*`, and `?`.
- Keep Compare tab local-only wording for ad-hoc multi-file runs.

---

### Task 1: Update Markdown Help

**Files:**
- Modify: `docs/multi_file_reconciliation.md`

**Interfaces:**
- Consumes: existing multi-file token and Compare tab sections.
- Produces: expanded walkthrough and examples for users.

- [ ] Add a "Filename pattern examples" subsection after token docs.
- [ ] Expand Compare tab ad-hoc section into numbered steps.
- [ ] Include examples for region/batch, alphanumeric ID + custom regex, date/region, no-region shared batch, and automated fallback.

### Task 2: Update Web UI Help Center

**Files:**
- Modify: `frontend/help-content.js`

**Interfaces:**
- Consumes: existing `window.ETL_HELP.sections` data shape.
- Produces: searchable Help Center entries for multi-file examples and regex formats.

- [ ] Add token-format examples to Multi-File Reconciliation section.
- [ ] Expand Compare section's ad-hoc multi-file step with a concrete workflow.

### Task 3: Build and Verify

**Files:**
- Modify generated: `frontend/index.html`
- Test: `tests/e2e/11-help.spec.ts` if needed.

- [ ] Run `npm run build:html`.
- [ ] Run `npx playwright test tests/e2e/11-help.spec.ts --grep "help"`.
- [ ] Run `git diff --check`.

## Plan Self-Review

- Spec coverage: markdown help, Web UI help, examples, and verification are covered.
- Placeholder scan: no placeholders remain.
- Type consistency: help content keeps existing `title`, `text`, `where`, `tip`, and `warn` keys.
