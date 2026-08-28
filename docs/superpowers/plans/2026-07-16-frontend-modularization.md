# Frontend Modularization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the monolithic `frontend/app.js` (4,276 lines, one giant Alpine.js component) and `frontend/index.html` (5,438 lines, one giant template) into per-tab feature modules, without changing any runtime behavior.

**Architecture:** `frontend/features/compare.js` already establishes the pattern this plan replicates for every other tab: an IIFE assigning `global.ETL_FEATURE_<NAME> = function() { return { ...state, ...methods } }`, merged into the root Alpine component in `app.js` via `Object.defineProperties(...)` (never `Object.assign` — see the comment at `frontend/app.js:101-106` for why: `Object.assign` would freeze computed `get` properties as one-time snapshots). JS modularization (Tasks 1-9) is low-risk and fully precedented. HTML modularization (Task 10) has no existing precedent in this repo and no bundler is installed, so it uses a small Node build script — mirroring the existing `npm run build:css` pattern, whose *output* (`frontend/vendor/tailwind.css`) is committed to source control the same way `frontend/index.html` will be. Every task ends by running that tab's existing Playwright spec (`tests/e2e/`) as a behavior-preservation check — the suite already covers all nine tabs plus cross-cutting flows.

**Tech Stack:** Vanilla JS (no bundler, `<script>` tag loading), Alpine.js 3.14.1, Node.js (dev-time build script only, per README's "no Node.js required on the deployment server" constraint), Playwright.

---

## Scope Check

This is one subsystem (frontend structure), not several — kept as one plan. It is independent of `docs/superpowers/plans/2026-07-16-etl-modernization-phase6.md` (RBAC/scorecard/alerts/palette), which touches `frontend/index.html` and `frontend/app.js` in Tasks 3, 6, 8, 9. **Sequencing note:** run this modularization plan to completion (or at least through the tab that a phase6 task touches) before phase6's frontend tasks, or phase6's edits will target line ranges that this plan moves. If phase6 Task 6 (Scorecard UI) and Task 9 (Command palette) land first instead, redo their few `app.js`/`index.html` edits against the new file layout afterward — they are small (under 100 lines each) and easy to re-apply.

## Non-Goals (explicitly out of scope)

- No change to Alpine reactivity, state shape, method signatures, or API calls — this is a pure code-motion refactor.
- No introduction of a bundler (Vite/Webpack/Rollup). The README guarantees "no Node.js required on the deployment server" and "no build step" for the shipped app; that constraint stands. The Task 10 build script is dev-time only, exactly like `build:css`.
- `drawer` / `openMismatchDrawer` (state at `frontend/app.js:385`, method at `frontend/app.js:2068`, template at `frontend/index.html:4915+`) stay in the shared "core" shell as-is. They are called exclusively from the Compare tab (`frontend/index.html:2825`, `2831`, `3786`) and were simply never migrated when `compare.js` was extracted. Relocating them is legitimate follow-up cleanup but is **not** part of this plan — don't move them while extracting other tabs, and don't be surprised they're missing from every feature file below.
- Toasts, the auth-setup wizard, the Help-center search state, Regional/timezone settings, and the `tabs` navigation array stay in `core` — they are genuinely cross-tab shell concerns, same as in the existing `compare.js` split.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `frontend/features/config.js` | Create | Config tab: saved configs, YAML import, Named Connections, API Endpoints, Security (API tokens), Notifications (webhooks) |
| `frontend/features/launch.js` | Create | Launch tab: job catalog/CRUD, run launch, Diagnostics, Schedules sub-tab |
| `frontend/features/monitor.js` | Create | Monitor tab: active runs, SSE progress, cancellation |
| `frontend/features/history.js` | Create | History tab: runs list, Profile & Schema, Trends, Lineage, mismatch distribution, inline mismatch expand, Audit, Coverage, Scorecard (if phase6 landed first) |
| `frontend/features/adapters.js` | Create | Adapters tab: SAP BO browse/import, Automic browse/import |
| `frontend/features/reports.js` | Create | Reports tab |
| `frontend/features/differences.js` | Create | Differences Explorer tab |
| `frontend/features/contracts.js` | Create | Contracts tab |
| `frontend/features/logs.js` | Create | Global Logs tab |
| `frontend/app.js` | Modify (9x) | Remove migrated state/methods from `core`; extend the final merge chain |
| `frontend/index.html` | Modify (9x + Task 10) | Add `<script>` tags for new feature files; later, split into partials |
| `frontend/partials/*.html` | Create (Task 10) | One file per tab's template markup, plus `shell.html` for nav/modals/drawer |
| `frontend/index.template.html` | Create (Task 10) | Shell with `<!-- INCLUDE: partials/x.html -->` markers |
| `scripts/build-html.js` | Create (Task 10) | Concatenates template + partials into `frontend/index.html` |
| `package.json` | Modify (Task 10) | Add `build:html` script |

Run all commands from repo root `c:\atom`. Playwright must have a server running against a fresh/simulation DB — check `tests/e2e/00-auth-setup.spec.ts` for how the suite bootstraps its token before running any other spec; if specs depend on running in file order, use `npx playwright test tests/e2e/00-auth-setup.spec.ts tests/e2e/<tab>.spec.ts`.

---

## Extraction Methodology (read before Task 1)

Every JS extraction task (1-9) follows the same six steps. This section defines the method once; each task below states only the tab-specific inputs (feature name, state banner name(s), spec file).

**A. Find every identifier the tab's markup touches:**

```bash
# Example for the Adapters tab — replace the x-show/currentView value and output file
grep -n "activeTab === 'adapters'\|currentView === 'adapters'" frontend/index.html
```

Locate that tab's top-level container `<div>` in `index.html`, then within just that element's markup, collect every `x-model`, `x-text`, `x-show`, `x-if`, `:class`, `@click`, and plain method-call identifier referenced. These are the property/method names that must move.

**B. Find the matching state block(s) in `app.js`:**

```bash
grep -n "^    // -----------" frontend/app.js
```

This lists every banner-commented section (state is fully banner-organized, per the survey already done — banners exist for Navigation, Auth setup wizard, Help center, Config, Config – YAML import, Jobs / Launch, Diagnostics, Monitor, History, Profile & Schema, Trends, Lineage, Mismatch distribution, Adapters – SAP BO, Adapters – Automic, Reports tab, Differences Explorer tab, Global Logs tab, Mismatch drawer, Inline mismatch expand, Regional, Security – API tokens, Notifications – webhook hooks, Schedules, Toast, Contracts). Cut the banner section(s) matching step A's identifiers.

**C. Find the matching methods:**

Methods are not banner-organized as consistently as state. For each identifier from step A that is a function call (not a plain value), run:

```bash
grep -n "  <methodName>(" frontend/app.js
```

to find its definition (methods are defined as `methodName(args) { ... }` inside the `core` object literal). Cut each one.

**D. Assemble the feature file**, following `frontend/features/compare.js` exactly:

```javascript
(function (global) {
  'use strict';
  // <Tab> feature slice (<Tab> tab). Merged into the Alpine component via
  // Object.assign(ETL_FEATURE_<NAME>(), ...) in app.js.
  global.ETL_FEATURE_<NAME> = function () {
    return {
      // ===== STATE (extracted from app.js) =====
      <pasted state properties, unchanged>

      // ===== METHODS (extracted from app.js) =====
      <pasted methods, unchanged>
    };
  };
})(window);
```

Do not rename anything, reformat logic, or "clean up" while moving — this is a pure cut-and-paste refactor. Behavior must be byte-identical modulo whitespace.

**E. Wire it in:**

1. In `frontend/index.html`, add `<script src="features/<name>.js"></script>` immediately before `<script src="app.js"></script>` (alongside the existing `features/compare.js` tag at `frontend/index.html:5372`).
2. In `frontend/app.js`, change the final merge line (currently `return Object.defineProperties(ETL_FEATURE_COMPARE(), Object.getOwnPropertyDescriptors(core));` at line 4274) to fold in the new feature via `Object.assign`:
   ```javascript
   return Object.defineProperties(
     Object.assign({}, ETL_FEATURE_COMPARE(), ETL_FEATURE_<NAME>()),
     Object.getOwnPropertyDescriptors(core)
   );
   ```
   On the second and later extraction tasks, extend the same `Object.assign(...)` call with the additional `ETL_FEATURE_<NAME2>()` argument rather than nesting another `Object.defineProperties` call.

**F. Verify:** run the tab's Playwright spec, confirm `app.js` shrank by roughly the pasted line count, confirm no leftover references to the moved identifiers remain in `core` (`grep -n "<removed-identifier>" frontend/app.js` should only show call sites inside the new feature file's own logic, none inside `core`).

---

### Task 1: Config tab (incl. Security & Notifications) → `frontend/features/config.js`

**Files:**
- Create: `frontend/features/config.js`
- Modify: `frontend/app.js`, `frontend/index.html`
- Test: `tests/e2e/01-config.spec.ts`

- [ ] **Step 1: Enumerate identifiers**

```bash
grep -n "currentView === 'config'" frontend/index.html
```

Read the Config tab's container element (it spans the Config tab body, the Security/API-tokens card at `frontend/index.html:243`+, and the Notifications/webhooks card at `frontend/index.html:375`+ — confirmed these render inside the `currentView === 'config'` block). Collect all bound identifiers per the Extraction Methodology step A.

- [ ] **Step 2: Cut matching state**

Cut these banner sections wholesale from `app.js` (per methodology step B): `// Config` (`app.js:159`), `// Config – YAML import` (`app.js:167`), `// Security – API tokens` (`app.js:438`), `// Notifications – webhook hooks` (`app.js:451`). Also cut `configs`, `showConfigModal`, `configModal`, `configValidation`, `yamlImportOpen`, `yamlImportText`, `yamlImporting`, `tokens`, `hooks`, and any named-connection/API-endpoint sub-state you find nested near them via step A.

- [ ] **Step 3: Cut matching methods**

Run, for each identifier collected in Step 1 that is invoked as a function (`loadConfigs`, `saveConfig`, `deleteConfig`, `validateConfig`, `importYaml`, `loadTokens`, `createToken`, `revokeToken`, `loadHooks`, `saveHook`, `deleteHook`, `testHook`, and any config-preview/named-connection/API-endpoint CRUD methods you found in Step 1):

```bash
grep -n "  loadConfigs(" frontend/app.js
```

Cut each definition (open brace to matching close brace) out of `core`.

- [ ] **Step 4: Assemble `frontend/features/config.js`**

Follow Extraction Methodology step D. Name the global `ETL_FEATURE_CONFIG`.

- [ ] **Step 5: Wire it in**

Follow Extraction Methodology step E. Add `<script src="features/config.js"></script>` before the `compare.js` tag (keep tag order matching dependency order — `config.js` has no dependency on `compare.js` or vice versa, so either order is fine, but keep it alphabetically grouped with the other `features/*.js` tags for readability).

- [ ] **Step 6: Verify**

Run: `npx playwright test tests/e2e/00-auth-setup.spec.ts tests/e2e/01-config.spec.ts`
Expected: all tests PASS (auth-setup must run first — it creates the token every other spec authenticates with).

- [ ] **Step 7: Commit**

```bash
git add frontend/features/config.js frontend/app.js frontend/index.html
git commit -m "refactor(frontend): extract Config/Security/Notifications into features/config.js"
```

---

### Task 2: Launch tab (incl. Diagnostics & Schedules) → `frontend/features/launch.js`

**Files:**
- Create: `frontend/features/launch.js`
- Modify: `frontend/app.js`, `frontend/index.html`
- Test: `tests/e2e/02-launch-jobs.spec.ts`

- [ ] **Step 1: Enumerate identifiers**

```bash
grep -n "currentView === 'jobs'" frontend/index.html
```

Note `launchSubTab: 'jobs'` (confirmed at `frontend/app.js:465`) drives a Jobs/Schedules sub-tab split within this one container — both sub-tabs belong in this single feature file.

- [ ] **Step 2: Cut matching state**

Cut banner sections: `// Jobs / Launch` (`app.js:174`), `// Diagnostics` (`app.js:216`), `// Schedules` (`app.js:460`). This includes `jobs`, `selectedJobs`, `stepSettings`, `stepSettingsOpen`, `showJobModal`, `jobModal`, `jobModalEditing`, `jobGateVerdicts`, `launchSettings`, `isLaunching`, `validateJobLoading`, `diagnosticsOpen`/`diagnosticsLoading`/`diagnosticsData`/`diagnosticsError`/`diagnosticsIncludeLogs`, `schedules`, `launchSubTab`, `showScheduleModal`, `scheduleModal`, `jobSelections`, `showSelectionModal`, `selectionModal`, `selectionModalEditing`, `showCiIntegrationModal`, `ciIntegrationModal`, `selectedSelectionJobNames`, `showLaunchSelectionModal`, `launchSelectionModal`, `showSelectionRunsModal`, `selectionRunsPanel`, `selectionRuns`, `compareRunIds`, `scheduleModalEditing`.

- [ ] **Step 3: Cut matching methods**

Per methodology step C, find and cut definitions for job CRUD (`loadJobs`, `saveJob`, `deleteJob`, `openNewJob`/`editJob` or their actual names — confirm with `grep -n "showJobModal = true" frontend/app.js` to find the real opener method name), `validateJobQuery`, `previewQuery`, launch (`launchRun`/`startRun` — confirm actual name with `grep -n "isLaunching = true" frontend/app.js`), diagnostics (`runDiagnostics` or similar — confirm with `grep -n "diagnosticsLoading = true" frontend/app.js`), and schedule CRUD (`loadSchedules`, `saveSchedule`, `deleteSchedule`, `runScheduleNow`, `toggleSchedule`).

- [ ] **Step 4: Assemble `frontend/features/launch.js`**

Global name `ETL_FEATURE_LAUNCH`.

- [ ] **Step 5: Wire it in**

Add `<script src="features/launch.js"></script>`; extend the `Object.assign(...)` merge chain with `ETL_FEATURE_LAUNCH()`.

- [ ] **Step 6: Verify**

Run: `npx playwright test tests/e2e/00-auth-setup.spec.ts tests/e2e/02-launch-jobs.spec.ts`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/features/launch.js frontend/app.js frontend/index.html
git commit -m "refactor(frontend): extract Launch/Diagnostics/Schedules into features/launch.js"
```

---

### Task 3: Monitor tab → `frontend/features/monitor.js`

**Files:**
- Create: `frontend/features/monitor.js`
- Modify: `frontend/app.js`, `frontend/index.html`
- Test: `tests/e2e/03-monitor.spec.ts`

- [ ] **Step 1: Enumerate identifiers**

```bash
grep -n "currentView === 'monitor'" frontend/index.html
```

- [ ] **Step 2: Cut matching state**

Cut banner section `// Monitor` (`app.js:225`): `activeRuns` and any SSE-connection-state fields nested near it (`grep -n "EventSource\|activeRuns" frontend/app.js` to find the SSE wiring and any connection-tracking fields).

- [ ] **Step 3: Cut matching methods**

The SSE stream handler and cancel-run method are already partially visible: `grep -n "activeRuns\." frontend/app.js` shows update sites around lines 1561-1670. Cut the enclosing method definitions (likely named something like `subscribeToRun`, `handleProgress`, `cancelRun` — confirm exact names with `grep -n "cancel_requested\|EventSource(" frontend/app.js`).

- [ ] **Step 4: Assemble `frontend/features/monitor.js`**

Global name `ETL_FEATURE_MONITOR`.

- [ ] **Step 5: Wire it in**

Add `<script src="features/monitor.js"></script>`; extend the merge chain.

- [ ] **Step 6: Verify**

Run: `npx playwright test tests/e2e/00-auth-setup.spec.ts tests/e2e/02-launch-jobs.spec.ts tests/e2e/03-monitor.spec.ts`
Expected: all PASS (launch-jobs spec included because triggering a run is the only way to populate Monitor state — a regression here often shows up as a launch-jobs failure first).

- [ ] **Step 7: Commit**

```bash
git add frontend/features/monitor.js frontend/app.js frontend/index.html
git commit -m "refactor(frontend): extract Monitor tab into features/monitor.js"
```

---

### Task 4: History tab (incl. Profile, Trends, Lineage, Mismatch distribution, Audit, Coverage) → `frontend/features/history.js`

**Files:**
- Create: `frontend/features/history.js`
- Modify: `frontend/app.js`, `frontend/index.html`
- Test: `tests/e2e/04-history.spec.ts`

This is the largest extraction — History has the most sub-tabs (`historySubTab`: runs/trends/lineage/audit/profile/schema/coverage, plus scorecard if `docs/superpowers/plans/2026-07-16-etl-modernization-phase6.md` Task 6 already landed).

- [ ] **Step 1: Enumerate identifiers**

```bash
grep -n "currentView === 'history'" frontend/index.html
grep -n "historySubTab" frontend/index.html
```

- [ ] **Step 2: Cut matching state**

Cut banner sections: `// History` (`app.js:235`), `// Profile & Schema` (`app.js:255`), `// Trends` (`app.js:267`), `// Lineage` (`app.js:277`), `// Mismatch distribution` (`app.js:283`), `// Inline mismatch expand (History detail)` (`app.js:395`). This includes `runs`, `historySubTab`, `profileJobName` and its sibling profile-state, `trendsJobName` and its sibling trend-state, `lineageGraph`, `mismatchDist`, `expandedMismatches`. Also cut any `auditEvents`/`coverageData`/`flakyScores`/`scorecard` state you find with:

```bash
grep -n "loadAudit\|loadCoverage\|loadScorecard\|flaky" frontend/app.js
```

- [ ] **Step 3: Cut matching methods**

Cut `loadRuns`, `loadRunDetail`, `loadProfile`, `loadSchemaHistory`, `loadTrends`, `loadLineage`, `loadMismatchDist`, `toggleExpandedMismatches` (or its real name — confirm with `grep -n "expandedMismatches\[" frontend/app.js`), `loadAudit`, `loadCoverage`, `loadFlaky`, `pinBaseline`/`compareToBaseline` (confirm names with `grep -n "baseline" frontend/app.js`), and `loadScorecard` if it exists yet (phase6 Task 6).

- [ ] **Step 4: Assemble `frontend/features/history.js`**

Global name `ETL_FEATURE_HISTORY`.

- [ ] **Step 5: Wire it in**

Add `<script src="features/history.js"></script>`; extend the merge chain.

- [ ] **Step 6: Verify**

Run: `npx playwright test tests/e2e/00-auth-setup.spec.ts tests/e2e/04-history.spec.ts`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/features/history.js frontend/app.js frontend/index.html
git commit -m "refactor(frontend): extract History/Profile/Trends/Lineage/Audit/Coverage into features/history.js"
```

---

### Task 5: Adapters tab (SAP BO + Automic) → `frontend/features/adapters.js`

**Files:**
- Create: `frontend/features/adapters.js`
- Modify: `frontend/app.js`, `frontend/index.html`
- Test: `tests/e2e/05-adapters.spec.ts`

- [ ] **Step 1: Enumerate identifiers**

```bash
grep -n "currentView === 'adapters'" frontend/index.html
```

- [ ] **Step 2: Cut matching state**

Cut banner sections `// Adapters – SAP BO` (`app.js:290`) and `// Adapters – Automic` (`app.js:303`): `boConfigId` and sibling BO-browse state, `automicConfigId` and sibling Automic-browse state, plus `browseAutomicConfigId` (seen at `app.js:320`).

- [ ] **Step 3: Cut matching methods**

Cut BO document/report browse methods and "Add to Catalog" handler (confirm names with `grep -n "boDocsA\|browseBoDocuments\|addToCatalog" frontend/app.js`), and Automic browse/import methods (confirm with `grep -n "browseAutomicConfigId\|importAutomic" frontend/app.js`).

- [ ] **Step 4: Assemble `frontend/features/adapters.js`**

Global name `ETL_FEATURE_ADAPTERS`.

- [ ] **Step 5: Wire it in**

Add `<script src="features/adapters.js"></script>`; extend the merge chain.

- [ ] **Step 6: Verify**

Run: `npx playwright test tests/e2e/00-auth-setup.spec.ts tests/e2e/05-adapters.spec.ts`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/features/adapters.js frontend/app.js frontend/index.html
git commit -m "refactor(frontend): extract Adapters (SAP BO/Automic) into features/adapters.js"
```

---

### Task 6: Reports tab → `frontend/features/reports.js`

**Files:**
- Create: `frontend/features/reports.js`
- Modify: `frontend/app.js`, `frontend/index.html`
- Test: `tests/e2e/06-reports.spec.ts`

- [ ] **Step 1: Enumerate identifiers**

```bash
grep -n "currentView === 'reports'" frontend/index.html
```

- [ ] **Step 2: Cut matching state**

Cut banner section `// Reports tab` (`app.js:329`): `reportRunId` and sibling state.

- [ ] **Step 3: Cut matching methods**

Cut report-loading/download methods (confirm names with `grep -n "reportRunId" frontend/app.js`).

- [ ] **Step 4: Assemble `frontend/features/reports.js`**

Global name `ETL_FEATURE_REPORTS`.

- [ ] **Step 5: Wire it in**

Add `<script src="features/reports.js"></script>`; extend the merge chain.

- [ ] **Step 6: Verify**

Run: `npx playwright test tests/e2e/00-auth-setup.spec.ts tests/e2e/06-reports.spec.ts`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/features/reports.js frontend/app.js frontend/index.html
git commit -m "refactor(frontend): extract Reports tab into features/reports.js"
```

---

### Task 7: Differences Explorer tab → `frontend/features/differences.js`

**Files:**
- Create: `frontend/features/differences.js`
- Modify: `frontend/app.js`, `frontend/index.html`
- Test: `tests/e2e/07-differences.spec.ts`

- [ ] **Step 1: Enumerate identifiers**

```bash
grep -n "currentView === 'differences'" frontend/index.html
```

- [ ] **Step 2: Cut matching state**

Cut banner section `// Differences Explorer tab` (`app.js:344`): `diffRunId` and sibling state.

- [ ] **Step 3: Cut matching methods**

Cut difference-export/preview methods (confirm names with `grep -n "diffRunId" frontend/app.js` — this tab drives `api/services/difference_export.py`).

- [ ] **Step 4: Assemble `frontend/features/differences.js`**

Global name `ETL_FEATURE_DIFFERENCES`.

- [ ] **Step 5: Wire it in**

Add `<script src="features/differences.js"></script>`; extend the merge chain.

- [ ] **Step 6: Verify**

Run: `npx playwright test tests/e2e/00-auth-setup.spec.ts tests/e2e/07-differences.spec.ts`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/features/differences.js frontend/app.js frontend/index.html
git commit -m "refactor(frontend): extract Differences Explorer into features/differences.js"
```

---

### Task 8: Contracts tab → `frontend/features/contracts.js`

**Files:**
- Create: `frontend/features/contracts.js`
- Modify: `frontend/app.js`, `frontend/index.html`
- Test: `tests/e2e/09-contracts.spec.ts`

- [ ] **Step 1: Enumerate identifiers**

```bash
grep -n "currentView === 'contracts'" frontend/index.html
```

- [ ] **Step 2: Cut matching state**

Cut banner section `// Contracts` (`app.js:528`): `contracts` and sibling state (breach list, version-bump modal state — confirm nested fields with `grep -n "contracts:" -A 15 frontend/app.js`).

- [ ] **Step 3: Cut matching methods**

Cut contract CRUD/breach/version-bump methods (confirm names with `grep -n "loadContracts\|bumpVersion\|contract.breached" frontend/app.js`).

- [ ] **Step 4: Assemble `frontend/features/contracts.js`**

Global name `ETL_FEATURE_CONTRACTS`.

- [ ] **Step 5: Wire it in**

Add `<script src="features/contracts.js"></script>`; extend the merge chain.

- [ ] **Step 6: Verify**

Run: `npx playwright test tests/e2e/00-auth-setup.spec.ts tests/e2e/09-contracts.spec.ts`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/features/contracts.js frontend/app.js frontend/index.html
git commit -m "refactor(frontend): extract Contracts tab into features/contracts.js"
```

---

### Task 9: Global Logs tab → `frontend/features/logs.js`

**Files:**
- Create: `frontend/features/logs.js`
- Modify: `frontend/app.js`, `frontend/index.html`
- Test: `tests/e2e/10-logs.spec.ts`

- [ ] **Step 1: Enumerate identifiers**

```bash
grep -n "currentView === 'logs'" frontend/index.html
```

- [ ] **Step 2: Cut matching state**

Cut banner section `// Global Logs tab (server-wide, no run_id required)` (`app.js:373`): `globalLogEvents` and sibling filter/search state.

- [ ] **Step 3: Cut matching methods**

Cut log-fetch/filter methods (confirm names with `grep -n "globalLogEvents" frontend/app.js`).

- [ ] **Step 4: Assemble `frontend/features/logs.js`**

Global name `ETL_FEATURE_LOGS`.

- [ ] **Step 5: Wire it in**

Add `<script src="features/logs.js"></script>`; extend the merge chain — this should now be the last argument, e.g.:
```javascript
return Object.defineProperties(
  Object.assign({}, ETL_FEATURE_COMPARE(), ETL_FEATURE_CONFIG(), ETL_FEATURE_LAUNCH(),
    ETL_FEATURE_MONITOR(), ETL_FEATURE_HISTORY(), ETL_FEATURE_ADAPTERS(),
    ETL_FEATURE_REPORTS(), ETL_FEATURE_DIFFERENCES(), ETL_FEATURE_CONTRACTS(),
    ETL_FEATURE_LOGS()),
  Object.getOwnPropertyDescriptors(core)
);
```

- [ ] **Step 6: Verify**

Run: `npx playwright test tests/e2e/00-auth-setup.spec.ts tests/e2e/10-logs.spec.ts`
Expected: all PASS.

- [ ] **Step 7: Full regression + size check**

Run: `npx playwright test`
Expected: all specs PASS (full suite: 00 through 13).

Run: `wc -l frontend/app.js frontend/features/*.js`
Expected: `app.js` reduced from 4,276 lines to roughly 1,000-1,400 (core shell: navigation, auth wizard, help center, toasts, regional settings, drawer, plus the merge scaffolding); the nine feature files sum to roughly the remainder.

- [ ] **Step 8: Commit**

```bash
git add frontend/features/logs.js frontend/app.js frontend/index.html
git commit -m "refactor(frontend): extract Global Logs into features/logs.js; complete JS modularization"
```

---

### Task 10: HTML partial-ization (build-time concatenation)

**Files:**
- Create: `frontend/index.template.html`
- Create: `frontend/partials/shell.html`, `frontend/partials/tab-config.html`, `frontend/partials/tab-launch.html`, `frontend/partials/tab-monitor.html`, `frontend/partials/tab-history.html`, `frontend/partials/tab-adapters.html`, `frontend/partials/tab-reports.html`, `frontend/partials/tab-differences.html`, `frontend/partials/tab-compare.html`, `frontend/partials/tab-contracts.html`, `frontend/partials/tab-logs.html`, `frontend/partials/tab-help.html`
- Create: `scripts/build-html.js`
- Modify: `package.json`, `.gitignore` (do NOT gitignore `frontend/index.html` — it stays committed, same as `frontend/vendor/tailwind.css`)
- Test: `tests/e2e/*` (full suite), plus a byte-identical diff check

This task carries real risk (5,438 lines, one wrong cut boundary breaks the whole UI) — run it only after Tasks 1-9 are merged and green, and treat Step 3 (the identity check) as a hard gate before deleting anything.

- [ ] **Step 1: Identify the exact partial boundaries**

```bash
grep -n "^<div x-show=\"currentView === '" frontend/index.html
```

This lists the opening `<div>` line for every top-level tab body. For each, find its matching closing `</div>` using an editor's bracket-match or by counting nested `<div>`/`</div>` pairs from the opening line — do not guess; an off-by-one here corrupts every tab after it. If unsure, load the file in an editor with bracket matching rather than hand-counting in a terminal.

- [ ] **Step 2: Create `frontend/index.template.html` and the partials**

Copy the entirety of `frontend/index.html` to `frontend/index.template.html`. Then, for each tab boundary found in Step 1: cut that `<div>...</div>` block out into its own `frontend/partials/tab-<name>.html` file, and replace it in the template with a single-line marker:

```html
<!-- INCLUDE: partials/tab-config.html -->
```

Repeat for all eleven tabs (config, launch, monitor, history, adapters, reports, differences, compare, contracts, logs, help). Everything that remains in `index.template.html` after all eleven cuts — the `<head>`, top nav bar, the job/config/schedule/etc. modals, the mismatch drawer, the toast container, the command palette (if `docs/superpowers/plans/2026-07-16-etl-modernization-phase6.md` Task 9 landed), and the closing `<script>` tags — becomes `frontend/partials/shell.html`... actually keep those directly in `index.template.html` rather than a further partial; only the eleven tab bodies move out, since they are what makes the file large and they map cleanly to the JS feature split done in Tasks 1-9.

- [ ] **Step 3: Write `scripts/build-html.js`**

```javascript
#!/usr/bin/env node
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const TEMPLATE = path.join(ROOT, 'frontend', 'index.template.html');
const OUTPUT = path.join(ROOT, 'frontend', 'index.html');
const INCLUDE_RE = /^<!-- INCLUDE: (.+?) -->$/;

function build() {
  const template = fs.readFileSync(TEMPLATE, 'utf8');
  const lines = template.split('\n');
  const out = lines.map((line) => {
    const match = line.match(INCLUDE_RE);
    if (!match) return line;
    const partialPath = path.join(ROOT, 'frontend', match[1]);
    return fs.readFileSync(partialPath, 'utf8').replace(/\n$/, '');
  });
  fs.writeFileSync(OUTPUT, out.join('\n'));
  console.log(`Built ${OUTPUT} from ${TEMPLATE} + ${lines.filter(l => INCLUDE_RE.test(l)).length} partials`);
}

build();
```

- [ ] **Step 4: Add the npm script**

In `package.json`, add to `"scripts"`:

```json
"build:html": "node scripts/build-html.js"
```

- [ ] **Step 5: Verify byte-identical output before relying on it**

```bash
cp frontend/index.html /tmp/index.html.before 2>/dev/null || copy frontend\index.html %TEMP%\index.html.before
npm run build:html
```

Diff the freshly-built `frontend/index.html` against the pre-build copy. They must be identical (whitespace included) except for the loss of the eleven `<!-- INCLUDE -->` marker lines' worth of blank-line differences, if any. If they differ in any tab's actual markup, the partial boundaries from Step 1 were cut wrong — fix the partial, do not adjust the build script to paper over it.

- [ ] **Step 6: Run the full Playwright suite against the rebuilt file**

Run: `npx playwright test`
Expected: all specs PASS — this confirms the rebuilt `index.html` behaves identically to the original.

- [ ] **Step 7: Wire the build into CI**

Open `.github/workflows/ci.yml`. Add a step before the test/lint steps that runs `npm run build:html` and then fails the job if `git diff --exit-code frontend/index.html` reports changes (i.e., the committed `index.html` must always match what the partials produce). Match the workflow file's existing step syntax.

- [ ] **Step 8: Update README**

In the "Updating Vendor Files After UI Changes" section of `README.md`, add a parallel subsection: editing a tab now means editing `frontend/partials/tab-<name>.html` (or `frontend/features/<name>.js`) and running `npm run build:html`, then committing the regenerated `frontend/index.html` alongside the partial — exactly parallel to how `frontend/vendor/tailwind.css` is committed after `npm run build:css`.

- [ ] **Step 9: Commit**

```bash
git add frontend/index.template.html frontend/partials/ scripts/build-html.js package.json .github/workflows/ci.yml README.md frontend/index.html
git commit -m "refactor(frontend): split index.html into per-tab partials with build-time concatenation"
```

---

## Self-Review Notes

- Spec coverage: all nine data tabs get JS feature files (Tasks 1-9); HTML partial-ization covers all eleven bodies including Compare and Help (Task 10). Cross-cutting shell state (auth wizard, toasts, drawer, help search, regional settings, nav) is explicitly kept in `core` with reasoning given, not silently dropped.
- No placeholders: every task names its exact banner line numbers (verified via `grep -n "^    // -----------" frontend/app.js` during planning) and gives the literal grep commands needed to resolve method names that weren't statically enumerable ahead of time — this is discovery methodology, not a TBD.
- Type/name consistency: the `Object.assign(...)` merge chain in Task 9 Step 5 lists all nine `ETL_FEATURE_*` globals by the exact names introduced in Tasks 1-9 (`ETL_FEATURE_CONFIG`, `ETL_FEATURE_LAUNCH`, `ETL_FEATURE_MONITOR`, `ETL_FEATURE_HISTORY`, `ETL_FEATURE_ADAPTERS`, `ETL_FEATURE_REPORTS`, `ETL_FEATURE_DIFFERENCES`, `ETL_FEATURE_CONTRACTS`, `ETL_FEATURE_LOGS`), matching `ETL_FEATURE_COMPARE`'s existing convention.
- Risk sequencing: JS (precedented, low-risk, one Playwright-verified task at a time) is ordered before HTML (novel, high-risk, single all-or-nothing identity check) so any problem surfaces early and cheaply.
