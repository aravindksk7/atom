# Frontend WCAG AA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated WCAG 2.1 AA accessibility coverage and fix the frontend markup/behavior issues called out in `docs/superpowers/specs/2026-08-03-frontend-wcag-aa-design.md`.

**Architecture:** Apply layout-inert semantic markup changes first, then add shared dialog focus management in `frontend/app.js`, then add Playwright/axe coverage. Keep the committed label codemod as executable documentation and rebuild `frontend/index.html` from partials.

**Tech Stack:** Static HTML partials, Alpine.js, CSS, Playwright, `@axe-core/playwright`, Python smoke tests.

## Global Constraints

- Implement all nine scope items from the approved design.
- Accessible names use `id`/`for`; use `aria-label` only where there is no visible label.
- Skip `id`/`for` codemod changes inside `<template x-for>`; hand-write dynamic `:id`/`:for` there.
- Table headers need `scope="col"`; each table needs a source-level `aria-label`.
- Dialogs need `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, shared focus trap, Escape close, focus restore.
- Claim earned by tests is "No automated WCAG AA violations", not full WCAG conformance.
- Do not change Alpine state shape, method signatures, or API calls.

---

### Task 1: Markup Semantics

**Files:**
- Create: `scripts/a11y-label-ids.js`
- Modify: `frontend/index.template.html`
- Modify: `frontend/partials/*.html`
- Modify: `tests/integration/test_api_frontend_smoke.py`

**Interfaces:**
- Consumes: existing partial include build via `npm run build:html`.
- Produces: labelled form controls, scoped table headers, labelled tables, sequential headings, no `sr-only` smoke-test shim.

- [ ] Write `scripts/a11y-label-ids.js` that adds file-scoped `for`/`id` pairs for immediate `.field-label` + input/select/textarea siblings and reports skipped `x-for` sections.
- [ ] Run the codemod against `frontend/index.template.html` and all `frontend/partials/*.html`.
- [ ] Hand-edit remaining controls in `template x-for` with index-based `:id`/`:for` pairs and add `aria-label` to unlabeled search/filter/checkbox controls.
- [ ] Add `scope="col"` to all table header cells and `aria-label` to all source tables.
- [ ] Replace the top-nav `.page-title` span with an `h1` retaining the same class.
- [ ] Correct skipped heading levels and update the frontend smoke test assertions to visible strings.
- [ ] Delete the hardcoded `sr-only` shim and run `npm run build:html`.

### Task 2: Dialogs, Live Regions, Focus, Keyboard

**Files:**
- Modify: `frontend/index.template.html`
- Modify: `frontend/partials/tab-config.html`
- Modify: `frontend/partials/tab-launch.html`
- Modify: `frontend/partials/tab-adapters.html`
- Modify: `frontend/app.js`
- Modify: `frontend/app-help.js`
- Modify: `frontend/styles.css`

**Interfaces:**
- Consumes: existing modal booleans and drawer state.
- Produces: `syncDialogFocus()` and `setupDialogFocusTrap(dialog, closeFn)` methods in Alpine app object.

- [ ] Add dialog roles/labels to auth, drawer, contract, help, bulk decision, mismatch decision, config, hook, step release, job, schedule, and BO job modal containers.
- [ ] Add `x-ref` names for dialog panels and title IDs matching `aria-labelledby`.
- [ ] Add `syncDialogFocus()` watchers in `init()` for each dialog open state.
- [ ] Implement shared focus trapping in `app.js`, including no-focusable fallback and vanished-trigger fallback.
- [ ] Convert non-focusable click controls to buttons or add `tabindex="0" role="button" @keydown.enter` and `@keydown.space.prevent`.
- [ ] Add `role="status" aria-live="polite"` to the toast stack and dynamic `role="alert"` for error toasts.
- [ ] Extend focus-visible CSS for nav items, sub-tabs, links, sortable headers, checkboxes, and close buttons.
- [ ] Run `npm run build:html`.

### Task 3: Automated Accessibility Gate

**Files:**
- Modify: `package.json`
- Modify: `package-lock.json`
- Create: `tests/e2e/21-accessibility.spec.ts`

**Interfaces:**
- Consumes: `authedPage` fixture and tab `data-testid="nav-tab-*"` convention.
- Produces: axe checks across all 14 tabs and targeted assertions for focus rings, live regions, focus trap, and keyboard reachability.

- [ ] Add `@axe-core/playwright` as a devDependency.
- [ ] Write the accessibility spec using `AxeBuilder` with tags `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`.
- [ ] Add tests for focus ring presence, toast live region, focus trap/Escape/restore, and keyboard activation.
- [ ] Verify red where possible before production fixes, then run the full e2e gate.

### Task 4: Verification

**Files:**
- All modified files.

**Interfaces:**
- Consumes: implemented code.
- Produces: passing verification evidence.

- [ ] Run `npm run build:html`.
- [ ] Run `pytest tests/integration/test_api_frontend_smoke.py -q`.
- [ ] Run `npx playwright test tests/e2e/21-accessibility.spec.ts`.
- [ ] Run `npm run test:e2e` if targeted tests are green.
- [ ] Inspect `git status --short` and summarize changed files.
