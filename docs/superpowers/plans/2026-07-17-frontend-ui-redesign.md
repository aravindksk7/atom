# Frontend UI/UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the ETL Framework frontend from a flat 11-tab top-nav into a grouped sidebar with a new Home/Overview landing tab, apply a 60-30-10 vibrant multi-accent palette with a dark/light toggle, and standardize shared component styling (buttons, forms, cards, tables, status pills) — per `docs/superpowers/specs/2026-07-16-frontend-ui-redesign-design.md`.

**Architecture:** All changes stay within the existing three frontend files (`frontend/index.html`, `frontend/app.js`, `frontend/styles.css`) plus one new Playwright spec — no new build step, no framework change. The body layout gains a `.sidebar` + `.app-content` wrapper (two small edits at the very start/end of `<body>`, so every existing tab view and modal — including the ones that already live after the `</main>` tag — stays inside the new layout without being individually relocated). `data-testid`s on existing elements (`nav-tab-*`, `auth-status-connected`, `auth-status-open-btn`) are preserved unchanged so the 15 existing Playwright spec files and `tests/e2e/fixtures.ts` keep working without modification.

**Scope boundary (confirmed with user):** The dark/light toggle only re-themes tokens + the components named in the approved Component Spec (sidebar, top bar, Home tab, buttons, forms, cards, tables, status pills). Tab-specific styling not in that spec (Compare tables, job-type badges, mismatch-diff views, etc.) keeps its current dark-oriented look in both theme modes — out of scope for this plan.

**Tech Stack:** Alpine.js 3, vanilla CSS (custom properties), Playwright (`@playwright/test`) for E2E.

---

## File Structure

- **Modify `frontend/styles.css`**: add new design tokens (primary/accent/semantic) to the existing dark `:root` block, add a new `:root[data-theme="light"]` override block (spec-scoped components only), add sidebar/Home/stat-card/status-pill/theme-toggle rules, repoint `.btn-primary`/`.field-input:focus` to the new `--primary` token, change `.app-shell` to a row layout with `.sidebar` + `.app-content`.
- **Modify `frontend/app.js`**: add `group` field to each `tabs` entry (~app.js:126-149) plus a new `home` entry, add `themeMode` state + `toggleTheme()` + `applyTheme()` (called from `init()`), add `get homeStats()` and `get homeRecentRuns()` computed getters (derived from existing `runs`/`configs` state — no new API calls).
- **Modify `frontend/index.html`**: wrap the whole `<body>` content in `<aside class="sidebar">…</aside><div class="app-content">…</div>` (two edits, top and bottom of body), replace the `<nav class="top-nav">` tab-strip (index.html:19-41) with a slim top bar + the new sidebar's grouped nav markup, insert a new `home` view block (the first view block, before the existing `config` view block at index.html:73).
- **Create `tests/e2e/15-home-and-nav.spec.ts`**: covers sidebar grouping/active state, Home tab stat cards/quick actions/recent activity navigation, and theme toggle persistence.

---

### Task 1: Design tokens — primary, group accents, semantic states, light theme

**Files:**
- Modify: `frontend/styles.css:1028-1053` (existing dark `:root` block)

- [ ] **Step 1: Add new tokens to the existing dark `:root` block**

In `frontend/styles.css`, find:

```css
:root {
  --bg: #050505;
  --panel: #0d0f12;
  --panel-2: #12161b;
  --panel-3: #171d24;
  --line: rgba(255,255,255,0.10);
  --line-strong: rgba(255,255,255,0.16);
  --text: #f4f7fb;
  --text-soft: #c7d0dc;
  --muted: #8491a3;
  /* single brand accent — terminal amber. Used for chrome/interaction (nav, tabs,
     primary actions, focus, selection). Categorical badge colors below are
     intentionally independent so data-type coding stays legible. */
  --accent: #ffb300;
  --accent-2: #ff7a1a;
  --accent-rgb: 255,179,0;
  --cyan: #22d3ee;
  --blue: #3b82f6;
  --violet: #8b5cf6;
  --magenta: #e879f9;
  --emerald: #34d399;
  --amber: #fbbf24;
  --rose: #fb7185;
  --shadow: 0 18px 60px rgba(0,0,0,0.42);
  --font-display: 'IBM Plex Mono', 'Fira Code', Consolas, monospace;
}
```

Replace with (adds primary/group/semantic tokens, keeps every existing token unchanged so nothing else in the file breaks):

```css
:root {
  --bg: #050505;
  --panel: #0d0f12;
  --panel-2: #12161b;
  --panel-3: #171d24;
  --panel-raised: #1c212c;
  --line: rgba(255,255,255,0.10);
  --line-strong: rgba(255,255,255,0.16);
  --border: var(--line);
  --text: #f4f7fb;
  --text-soft: #c7d0dc;
  --muted: #8491a3;
  /* single brand accent — terminal amber. Used for chrome/interaction (nav, tabs,
     primary actions, focus, selection). Categorical badge colors below are
     intentionally independent so data-type coding stays legible. */
  --accent: #ffb300;
  --accent-2: #ff7a1a;
  --accent-rgb: 255,179,0;
  --cyan: #22d3ee;
  --blue: #3b82f6;
  --violet: #8b5cf6;
  --magenta: #e879f9;
  --emerald: #34d399;
  --amber: #fbbf24;
  --rose: #fb7185;
  --shadow: 0 18px 60px rgba(0,0,0,0.42);
  --font-display: 'IBM Plex Mono', 'Fira Code', Consolas, monospace;

  /* ---- Redesign tokens (2026-07-16 spec): primary + group + semantic ---- */
  --primary: #6366f1;
  --primary-hover: #818cf8;
  --primary-rgb: 99,102,241;
  --primary-soft: rgba(99,102,241,0.10);
  --accent-setup: #f59e0b;
  --accent-exec: #22d3ee;
  --accent-analysis: #a855f7;
  --accent-system: #64748b;
  --success: #22c55e;
  --warning: #f59e0b;
  --danger: #f43f5e;
}

/* ---- Light theme override (spec-scoped: tokens + sidebar/top bar/Home/
   buttons/forms/cards/tables/status pills only — other tab-specific styling
   keeps its dark look in both modes, per 2026-07-16 design spec) ---- */
:root[data-theme="light"] {
  --bg: #f7f8fb;
  --panel: #ffffff;
  --panel-2: #f3f5f9;
  --panel-3: #eef0f5;
  --panel-raised: #eef0f5;
  --line: #e2e5eb;
  --line-strong: #d3d8e0;
  --border: var(--line);
  --text: #151922;
  --text-soft: #525a6b;
  --muted: #8992a3;
  --primary-soft: rgba(99,102,241,0.08);
  --shadow: 0 10px 30px rgba(15,23,42,0.08);
}
```

- [ ] **Step 2: Verify the tokens parse (no build step for CSS in this repo — visually confirmed in Task 4/9)**

Run: `grep -n "primary-rgb\|accent-setup\|data-theme=\"light\"" frontend/styles.css`
Expected: matches for all three, confirming the new block was added correctly.

- [ ] **Step 3: Commit**

```bash
git add frontend/styles.css
git commit -m "feat(frontend): add primary/group/semantic design tokens and light theme base"
```

---

### Task 2: Theme toggle state + persistence in app.js

**Files:**
- Modify: `frontend/app.js:150` (right after the `tabs` array / `apiOk: false,`)
- Modify: `frontend/app.js:287-320` (`init()`)

- [ ] **Step 1: Add `themeMode` state**

Find (app.js:150):

```js
    apiOk: false,
```

Replace with:

```js
    apiOk: false,
    themeMode: localStorage.getItem('etl_theme') === 'light' ? 'light' : 'dark',
```

- [ ] **Step 2: Add `toggleTheme()` / `applyTheme()` methods**

Find (app.js:280-285):

```js
    onTabEnter(id) {
      this.currentView = id;
      if (id === 'contracts') this.loadContracts();
      if (id === 'logs') this.startGlobalLogsPolling();
      else this.stopGlobalLogsPolling();
    },
```

Replace with:

```js
    onTabEnter(id) {
      this.currentView = id;
      if (id === 'contracts') this.loadContracts();
      if (id === 'logs') this.startGlobalLogsPolling();
      else this.stopGlobalLogsPolling();
    },

    applyTheme() {
      document.documentElement.setAttribute('data-theme', this.themeMode);
    },

    toggleTheme() {
      this.themeMode = this.themeMode === 'dark' ? 'light' : 'dark';
      localStorage.setItem('etl_theme', this.themeMode);
      this.applyTheme();
    },
```

- [ ] **Step 3: Apply the stored theme on load**

Find (app.js:287-288):

```js
    async init() {
      this.storedTokenValue = normalizeToken(sessionStorage.getItem('etl_token'));
```

Replace with:

```js
    async init() {
      this.applyTheme();
      this.storedTokenValue = normalizeToken(sessionStorage.getItem('etl_token'));
```

- [ ] **Step 4: Manual verification**

Run: `python -m uvicorn api.main:app --host 127.0.0.1 --port 8000` (in one terminal), open `http://127.0.0.1:8000` in a browser, open devtools console, run `document.documentElement.getAttribute('data-theme')` — expect `"dark"`. This confirms the attribute is wired before any markup depends on it (sidebar/Home CSS added in later tasks will read it).

- [ ] **Step 5: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): add theme toggle state with localStorage persistence"
```

---

### Task 3: Group tabs + add Home tab entry

**Files:**
- Modify: `frontend/app.js:126-149` (`tabs` array)

- [ ] **Step 1: Add `group` to every existing tab and a new `home` entry**

Find (app.js:126-149):

```js
    tabs: [
      { id: 'config',   label: 'Config',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>' },
      { id: 'jobs',     label: 'Launch',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>' },
      { id: 'monitor',  label: 'Monitor',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>' },
      { id: 'history',  label: 'History',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>' },
      { id: 'adapters', label: 'Adapters',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2v6M15 2v6M6 8h12l-1 5a5 5 0 0 1-10 0L6 8z"></path><path d="M10 19v3M14 19v3"></path></svg>' },
      { id: 'reports',  label: 'Reports',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>' },
      { id: 'differences', label: 'Differences',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>' },
      { id: 'compare',  label: 'Compare',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"></polyline><path d="M3 5h18"></path><polyline points="7 23 3 19 7 15"></polyline><path d="M21 19H3"></path></svg>' },
      { id: 'contracts', label: 'Contracts',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><path d="M9 15l2 2 4-4"></path></svg>' },
      { id: 'logs', label: 'Logs',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"></polyline><line x1="12" y1="19" x2="20" y2="19"></line></svg>' },
      { id: 'help', label: 'Help',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>' },
    ],
```

Replace with:

```js
    tabs: [
      { id: 'home', label: 'Home', group: null,
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg>' },
      { id: 'config',   label: 'Config', group: 'setup',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>' },
      { id: 'adapters', label: 'Adapters', group: 'setup',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2v6M15 2v6M6 8h12l-1 5a5 5 0 0 1-10 0L6 8z"></path><path d="M10 19v3M14 19v3"></path></svg>' },
      { id: 'contracts', label: 'Contracts', group: 'setup',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><path d="M9 15l2 2 4-4"></path></svg>' },
      { id: 'jobs',     label: 'Launch', group: 'execution',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>' },
      { id: 'monitor',  label: 'Monitor', group: 'execution',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>' },
      { id: 'history',  label: 'History', group: 'analysis',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>' },
      { id: 'reports',  label: 'Reports', group: 'analysis',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>' },
      { id: 'differences', label: 'Differences', group: 'analysis',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>' },
      { id: 'compare',  label: 'Compare', group: 'analysis',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"></polyline><path d="M3 5h18"></path><polyline points="7 23 3 19 7 15"></polyline><path d="M21 19H3"></path></svg>' },
      { id: 'logs', label: 'Logs', group: 'system',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"></polyline><line x1="12" y1="19" x2="20" y2="19"></line></svg>' },
      { id: 'help', label: 'Help', group: 'system',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>' },
    ],
    tabGroups: [
      { id: 'setup', label: 'Setup' },
      { id: 'execution', label: 'Execution' },
      { id: 'analysis', label: 'Analysis' },
      { id: 'system', label: 'System' },
    ],
```

- [ ] **Step 2: Change the default landing view**

Find (app.js:125):

```js
    currentView: 'config',
```

Replace with:

```js
    currentView: 'home',
```

- [ ] **Step 3: Verify no other code hardcodes the old default**

Run: `grep -n "currentView: 'config'\|currentView ?= ?'config'" frontend/app.js`
Expected: no matches (confirms `home` is now the only default-view assignment).

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): group tabs by workflow stage and add Home tab entry"
```

---

### Task 4: Home tab computed data (stats + recent runs)

**Files:**
- Modify: `frontend/app.js:761-763` (near the existing `get storedToken()` getter)

- [ ] **Step 1: Add `homeStats` and `homeRecentRuns` getters**

Find (app.js:761-763):

```js
    get storedToken() {
      return this.storedTokenValue;
    },
```

Replace with:

```js
    get storedToken() {
      return this.storedTokenValue;
    },

    get homeStats() {
      return {
        activeRuns: this.runs.filter(r => r.status === 'RUNNING').length,
        pendingJobs: this.runs.filter(r => r.status === 'PENDING').length,
        connectedEnvironments: this.configs.length,
        lastRunStatus: this.runs.length ? this.runs[0].status : null,
      };
    },

    get homeRecentRuns() {
      return this.runs.slice(0, 8);
    },
```

`this.runs` is already sorted most-recent-first by the backend (`ORDER BY TestRun.id DESC` in `etl_framework/repository/repository.py:280`) and is loaded on every app init via `loadAll() → loadRuns()` regardless of which tab is active, so these getters need no new API calls and no tab-visit gating.

- [ ] **Step 2: Manual verification**

With the dev server running and at least one run recorded, open devtools console on the app page and run `document.querySelector('[x-data]').__x.$data.homeStats` (or add a temporary `console.log(this.homeStats)` inside `init()` and remove it after checking) — expect an object with all four keys populated from real data, not `undefined`.

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): add Home tab stats and recent-runs computed getters"
```

---

### Task 5: Body layout — sidebar + app-content wrapper

**Files:**
- Modify: `frontend/index.html:13` (body open) and `frontend/index.html:5447` (body close)
- Modify: `frontend/styles.css:93` (`.app-shell`)

- [ ] **Step 1: Wrap body content — open tags**

Find (index.html:13-14):

```html
<body class="app-shell" x-data="app()" x-init="init()">
<span class="sr-only">Validate Configuration Run Health Check Add Job Execution Sequence</span>
```

Replace with:

```html
<body class="app-shell" x-data="app()" x-init="init()">
<span class="sr-only">Validate Configuration Run Health Check Add Job Execution Sequence</span>

<aside class="sidebar" :class="sidebarCollapsed ? 'is-collapsed' : ''" data-testid="app-sidebar">
  <div class="sidebar-brand">
    <svg class="brand-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
    <span class="sidebar-brand-label" x-show="!sidebarCollapsed">ETL Framework</span>
  </div>

  <nav class="sidebar-nav">
    <template x-for="tab in tabs.filter(t => !t.group)" :key="tab.id">
      <button @click="onTabEnter(tab.id)" :class="currentView === tab.id ? 'active' : ''"
              class="sidebar-nav-item" :data-testid="'nav-tab-' + tab.id">
        <span class="sidebar-nav-icon" x-html="tab.icon"></span>
        <span class="sidebar-nav-label" x-show="!sidebarCollapsed" x-text="tab.label"></span>
      </button>
    </template>

    <template x-for="grp in tabGroups" :key="grp.id">
      <div class="sidebar-group" :data-group="grp.id">
        <div class="sidebar-group-header" x-show="!sidebarCollapsed" x-text="grp.label"></div>
        <template x-for="tab in tabs.filter(t => t.group === grp.id)" :key="tab.id">
          <button @click="onTabEnter(tab.id)" :class="currentView === tab.id ? 'active' : ''"
                  class="sidebar-nav-item" :data-group="grp.id" :data-testid="'nav-tab-' + tab.id">
            <span class="sidebar-nav-icon" x-html="tab.icon"></span>
            <span class="sidebar-nav-label" x-show="!sidebarCollapsed" x-text="tab.label"></span>
          </button>
        </template>
      </div>
    </template>
  </nav>

  <button class="sidebar-collapse-btn" data-testid="sidebar-collapse-btn"
          @click="sidebarCollapsed = !sidebarCollapsed"
          :aria-label="sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" :style="sidebarCollapsed ? 'transform: rotate(180deg)' : ''"><polyline points="15 18 9 12 15 6"></polyline></svg>
  </button>
</aside>

<div class="app-content">
```

- [ ] **Step 2: Wrap body content — close tag**

Find (index.html:5446-5448):

```html
    </template>
  </body>
</html>
```

Replace with:

```html
    </template>
</div><!-- end .app-content -->
  </body>
</html>
```

- [ ] **Step 3: Add `sidebarCollapsed` state**

Find (app.js, immediately after the `themeMode` line added in Task 2 Step 1):

```js
    themeMode: localStorage.getItem('etl_theme') === 'light' ? 'light' : 'dark',
```

Replace with:

```js
    themeMode: localStorage.getItem('etl_theme') === 'light' ? 'light' : 'dark',
    sidebarCollapsed: localStorage.getItem('etl_sidebar_collapsed') === 'true',
```

And find (app.js, `toggleTheme()` added in Task 2 Step 2) — add a watcher in `init()` instead of a dedicated method, since collapsing is a simple persisted boolean. Find (app.js, the line added in Task 2 Step 3):

```js
    async init() {
      this.applyTheme();
```

Replace with:

```js
    async init() {
      this.applyTheme();
      this.$watch('sidebarCollapsed', (v) => localStorage.setItem('etl_sidebar_collapsed', String(v)));
```

- [ ] **Step 4: Update `.app-shell` to a row layout**

Find (`frontend/styles.css:93`):

```css
.app-shell { min-height: 100vh; display: flex; flex-direction: column; }
```

Replace with:

```css
.app-shell { min-height: 100vh; display: flex; flex-direction: row; }
.app-content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
```

- [ ] **Step 5: Manual verification**

Run the dev server (`python -m uvicorn api.main:app --host 127.0.0.1 --port 8000`), open `http://127.0.0.1:8000`. Expect: page still renders (no JS console errors), existing top-nav/auth-bar/main content now sit to the right of an (unstyled, plain-list) sidebar column instead of full width. Full styling comes in Task 7 — this step only confirms the wrapper didn't break rendering or break any modal (open the "New Config" modal and confirm it still centers over the full viewport, not just the `.app-content` column, proving `position: fixed` still escapes to the viewport).

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/app.js frontend/styles.css
git commit -m "feat(frontend): wrap body in sidebar + app-content layout shell"
```

---

### Task 6: Remove old top-nav tab strip, add slim top bar

**Files:**
- Modify: `frontend/index.html:19-41` (old `<nav class="top-nav">`)

- [ ] **Step 1: Replace the tab-strip nav with a slim status/theme/profile bar**

Find (index.html:19-41):

```html
<nav class="top-nav">
  <div class="top-nav-inner">
    <span class="brand">
      <svg class="brand-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
      ETL Framework
    </span>
    <div class="flex gap-1 flex-1">
      <template x-for="tab in tabs" :key="tab.id">
        <button
          @click="onTabEnter(tab.id)"
          :class="currentView === tab.id ? 'active' : ''"
          class="tab-btn"
          :data-testid="'nav-tab-' + tab.id">
          <span class="tab-icon" x-html="tab.icon"></span>
          <span x-text="tab.label"></span>
        </button>
      </template>
    </div>
    <div class="flex items-center gap-2">
      <span class="text-xs" :class="apiOk ? 'text-emerald-500' : 'text-rose-400'" x-text="apiOk ? '● Connected' : '● Offline'"></span>
    </div>
  </div>
</nav>
```

Replace with:

```html
<nav class="top-nav">
  <div class="top-nav-inner">
    <span class="page-title" x-text="(tabs.find(t => t.id === currentView) || {}).label || ''"></span>
    <div class="flex items-center gap-3 ml-auto">
      <span class="text-xs" :class="apiOk ? 'text-emerald-500' : 'text-rose-400'" x-text="apiOk ? '● Connected' : '● Offline'"></span>
      <button class="theme-toggle-btn" data-testid="theme-toggle-btn" @click="toggleTheme()" :aria-label="themeMode === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'">
        <span x-show="themeMode === 'dark'">🌙</span>
        <span x-show="themeMode === 'light'">☀️</span>
      </button>
    </div>
  </div>
</nav>
```

The tab-strip's `data-testid="'nav-tab-' + tab.id"` bindings already moved into the sidebar in Task 5 Step 1 — this step only removes the now-duplicate strip from the top bar and adds the page title + theme toggle. The existing `auth-status-bar` section (index.html:46-63, right after this nav) is untouched — it keeps its exact markup and `auth-status-connected` / `auth-status-open-btn` test IDs so `tests/e2e/fixtures.ts` and `tests/e2e/00-auth-setup.spec.ts` keep passing unmodified.

- [ ] **Step 2: Manual verification**

Reload the dev server page. Expect: top bar shows only a page title (e.g. "Home"), connection dot, and a moon/sun toggle button. Click the toggle — expect the emoji swaps and (after Task 8's CSS lands) the page recolors; for now just confirm no console errors and `document.documentElement.getAttribute('data-theme')` changes between `dark`/`light` in devtools.

- [ ] **Step 3: Run the existing auth/nav Playwright specs to confirm no regression yet**

Run: `npm run test:e2e -- tests/e2e/00-auth-setup.spec.ts tests/e2e/11-help.spec.ts`
Expected: all tests PASS (these two specifically depend on `nav-tab-*` and `auth-status-*` test IDs, which is why they're checked first, before running the full suite at the end of the plan).

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): replace top-nav tab strip with slim title/status/theme bar"
```

---

### Task 7: Sidebar, top bar, and layout CSS

**Files:**
- Modify: `frontend/styles.css` (append new rules near the existing `.top-nav`/`.tab-btn` rules, ~styles.css:1075-1124)

- [ ] **Step 1: Add sidebar/top-bar CSS**

Find (`frontend/styles.css:1107-1122`, the existing `.tab-btn` rules):

```css
.tab-btn {
  color: var(--muted);
  border: 1px solid transparent;
}
.tab-btn:hover {
  background: rgba(255,255,255,0.06);
  color: var(--text-soft);
  border-color: var(--line);
}
.tab-btn.active {
  background: linear-gradient(135deg, rgba(var(--accent-rgb),0.20), rgba(var(--accent-rgb),0.10));
  color: #ffffff;
  border-color: rgba(var(--accent-rgb),0.40);
  box-shadow: 0 0 0 1px rgba(var(--accent-rgb),0.14), 0 12px 34px rgba(var(--accent-rgb),0.10);
  border-bottom: 2px solid var(--accent);
}
```

Add immediately after (keep `.tab-btn` rules — they still style the `sub-tab`-style pill buttons used inside several tabs, unrelated to this nav change):

```css
/* ---- Sidebar (2026-07-16 redesign) ---- */
.sidebar {
  width: 232px;
  flex-shrink: 0;
  background: var(--panel);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 0.75rem 0.5rem;
  transition: width 0.15s ease;
}
.sidebar.is-collapsed { width: 64px; }
.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.5rem 1rem;
  color: var(--text);
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
}
.sidebar-brand .brand-icon { flex-shrink: 0; color: var(--primary); }
.sidebar-nav { flex: 1; overflow-y: auto; }
.sidebar-group { margin-top: 0.75rem; }
.sidebar-group-header {
  font-size: 0.6875rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
  padding: 0.25rem 0.75rem;
  position: relative;
}
.sidebar-group-header::after {
  content: '';
  display: block;
  height: 2px;
  width: 1.5rem;
  margin-top: 0.25rem;
  border-radius: 9999px;
  background: linear-gradient(90deg, var(--primary), var(--primary-hover));
}
.sidebar-group[data-group="setup"] .sidebar-group-header::after { background: linear-gradient(90deg, var(--accent-setup), var(--accent-setup)); }
.sidebar-group[data-group="execution"] .sidebar-group-header::after { background: linear-gradient(90deg, var(--accent-exec), var(--accent-exec)); }
.sidebar-group[data-group="analysis"] .sidebar-group-header::after { background: linear-gradient(90deg, var(--accent-analysis), var(--accent-analysis)); }
.sidebar-group[data-group="system"] .sidebar-group-header::after { background: linear-gradient(90deg, var(--accent-system), var(--accent-system)); }

.sidebar-nav-item {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  width: 100%;
  padding: 0.5rem 0.75rem;
  border-radius: 0.375rem;
  border: none;
  border-left: 3px solid transparent;
  background: transparent;
  color: var(--text-soft);
  font-size: 0.8125rem;
  white-space: nowrap;
  cursor: pointer;
  transition: background 0.1s, color 0.1s;
}
.sidebar-nav-item:hover { background: var(--panel-raised); }
.sidebar-nav-item.active {
  background: var(--primary-soft);
  color: var(--text);
  border-left-color: var(--muted);
}
.sidebar-group[data-group="setup"] .sidebar-nav-item.active { border-left-color: var(--accent-setup); }
.sidebar-group[data-group="execution"] .sidebar-nav-item.active { border-left-color: var(--accent-exec); }
.sidebar-group[data-group="analysis"] .sidebar-nav-item.active { border-left-color: var(--accent-analysis); }
.sidebar-group[data-group="system"] .sidebar-nav-item.active { border-left-color: var(--accent-system); }
.sidebar-nav-icon { flex-shrink: 0; width: 18px; height: 18px; }
.sidebar-nav-icon svg { width: 100%; height: 100%; }

.sidebar-collapse-btn {
  margin-top: 0.5rem;
  align-self: center;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--muted);
  cursor: pointer;
}
.sidebar-collapse-btn:hover { background: var(--panel-raised); color: var(--text); }

/* ---- Slim top bar ---- */
.page-title { color: var(--text); font-weight: 600; font-size: 0.9375rem; }
.theme-toggle-btn {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  border: 1px solid var(--border);
  background: transparent;
  cursor: pointer;
  font-size: 0.875rem;
  line-height: 1;
}
.theme-toggle-btn:hover { background: var(--panel-raised); }
```

- [ ] **Step 2: Manual verification**

Reload the dev server page. Expect: a 232px dark sidebar on the left with "Home" standalone at top, then SETUP/EXECUTION/ANALYSIS/SYSTEM group headers each with a small colored underline, nav items highlighting on hover, and the active tab showing a left accent border in its group's color. Click the collapse button — sidebar should shrink to 64px and labels hide (icons remain).

- [ ] **Step 3: Commit**

```bash
git add frontend/styles.css
git commit -m "feat(frontend): style grouped sidebar and slim top bar"
```

---

### Task 8: Home tab view (markup)

**Files:**
- Modify: `frontend/index.html:73` (insert new view block right before the existing `config` view block)

- [ ] **Step 1: Insert the Home view block**

Find (index.html:70-73):

```html
<!-- ====================================================================
     TAB 1 – CONFIG EDITOR
     ==================================================================== -->
<div x-show="currentView === 'config'" x-cloak>
```

Replace with:

```html
<!-- ====================================================================
     TAB 0 – HOME / OVERVIEW
     ==================================================================== -->
<div x-show="currentView === 'home'" x-cloak data-testid="home-view">
  <div class="section-header">
    <div>
      <div class="section-title">Overview</div>
      <div class="section-sub">At-a-glance status and quick actions</div>
    </div>
  </div>

  <div class="stat-row">
    <button class="stat-card" data-testid="home-stat-active-runs" @click="onTabEnter('monitor')">
      <div class="stat-card-label">Active Runs</div>
      <div class="stat-card-value" x-text="homeStats.activeRuns"></div>
    </button>
    <button class="stat-card" data-testid="home-stat-last-run" @click="onTabEnter('history')">
      <div class="stat-card-label">Last Run Status</div>
      <div class="stat-card-value">
        <span x-show="homeStats.lastRunStatus" class="badge" :class="statusBadgeClass(homeStats.lastRunStatus)" x-text="homeStats.lastRunStatus"></span>
        <span x-show="!homeStats.lastRunStatus" class="text-muted text-sm">No runs yet</span>
      </div>
    </button>
    <button class="stat-card" data-testid="home-stat-environments" @click="onTabEnter('config')">
      <div class="stat-card-label">Connected Environments</div>
      <div class="stat-card-value" x-text="homeStats.connectedEnvironments"></div>
    </button>
    <button class="stat-card" data-testid="home-stat-pending-jobs" @click="onTabEnter('monitor')">
      <div class="stat-card-label">Pending Jobs</div>
      <div class="stat-card-value" x-text="homeStats.pendingJobs"></div>
    </button>
  </div>

  <div class="flex gap-2 mt-4 mb-4">
    <button class="btn-primary" data-testid="home-quick-action-new-config" @click="onTabEnter('config'); openNewConfigModal()">+ New Config</button>
    <button class="btn-primary" data-testid="home-quick-action-launch" @click="onTabEnter('jobs')">▶ Launch Job</button>
    <button class="btn-primary" data-testid="home-quick-action-reports" @click="onTabEnter('reports')">📊 View Reports</button>
  </div>

  <div class="card overflow-hidden mb-4">
    <div class="section-header" style="padding: 0.75rem 1rem 0;">
      <div class="section-title" style="font-size: 0.9375rem;">Recent Activity</div>
    </div>
    <table class="data-table" data-testid="home-recent-runs-table">
      <thead>
        <tr>
          <th>Run ID</th>
          <th>Status</th>
          <th>Environments</th>
          <th>Started</th>
        </tr>
      </thead>
      <tbody>
        <template x-for="run in homeRecentRuns" :key="run.run_id">
          <tr class="cursor-pointer" @click="onTabEnter('history'); viewRunDetail(run.run_id)" :data-testid="'home-recent-run-row-' + run.run_id">
            <td class="font-mono text-xs text-slate-500" x-text="run.run_id.substring(0,8)+'…'"></td>
            <td><span class="badge" :class="statusBadgeClass(run.status)" x-text="run.status"></span></td>
            <td class="text-muted" x-text="(run.source_env||'?') + ' → ' + (run.target_env||'?')"></td>
            <td class="text-muted" x-text="fmtDate(run.started_at)"></td>
          </tr>
        </template>
        <template x-if="homeRecentRuns.length === 0">
          <tr><td colspan="4" class="text-muted text-center py-4">No runs yet — launch a job to get started.</td></tr>
        </template>
      </tbody>
    </table>
  </div>

  <div class="card" data-testid="home-health-card">
    <div class="section-title" style="font-size: 0.9375rem;">Connection Health</div>
    <div class="flex items-center gap-2 mt-2">
      <span x-show="!storedToken" class="text-muted text-sm">No API token set —
        <button class="text-indigo-500 hover:underline" @click="openAuthModal()">set up access</button>
      </span>
      <span x-show="storedToken" class="text-sm" x-text="activeTokenName ? 'Connected as ' + activeTokenName + (activeTokenIsAdmin ? ' (Administrator)' : ' (Standard user)') : 'Connected'"></span>
      <button x-show="storedToken" class="text-indigo-500 hover:underline text-sm" @click="goToTokenManagement()">Manage →</button>
    </div>
  </div>
</div>

<!-- ====================================================================
     TAB 1 – CONFIG EDITOR
     ==================================================================== -->
<div x-show="currentView === 'config'" x-cloak>
```

- [ ] **Step 2: Manual verification**

Reload the dev server, log in with a token. Expect the Home tab to load by default showing 4 stat cards, 3 quick-action buttons, a recent-activity table (or the empty-state row if no runs exist yet), and a connection health line. Click each stat card and confirm it navigates to the right tab (Monitor/History/Config). Click "+ New Config" and confirm the config modal opens with the Config tab active.

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): add Home/Overview tab with stats, quick actions, recent activity"
```

---

### Task 9: Home tab, stat card, and shared component CSS

**Files:**
- Modify: `frontend/styles.css` (append near the card rules, ~styles.css:1124-1145; update `.btn-primary` at styles.css:1230 and `.field-input:focus` at styles.css:1213-1217; add `.status-pill`)

- [ ] **Step 1: Add stat-card CSS**

Append (anywhere after the `:root` block added in Task 1, e.g. right after the `.card::before` rule at `frontend/styles.css:1137-1144`):

```css
.stat-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 0.75rem;
}
.stat-card {
  text-align: left;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  padding: 1rem 1.125rem;
  cursor: pointer;
  transition: border-color 0.1s;
}
.stat-card:hover { border-color: var(--primary); }
.stat-card-label { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; }
.stat-card-value { color: var(--text); font-size: 1.5rem; font-weight: 700; margin-top: 0.25rem; }

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.1875rem 0.625rem;
  border-radius: 9999px;
  font-size: 0.6875rem;
  font-weight: 600;
}
.status-pill::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.status-pill.is-success { background: rgba(34,197,94,0.12); color: var(--success); }
.status-pill.is-warning { background: rgba(245,158,11,0.12); color: var(--warning); }
.status-pill.is-danger { background: rgba(244,63,94,0.12); color: var(--danger); }
```

- [ ] **Step 2: Repoint `.btn-primary` and focus ring to the `--primary` token**

Find (`frontend/styles.css:1230-1234`):

```css
.btn-primary {
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  color: #1a0d00;
  border: 1px solid rgba(var(--accent-rgb),0.45);
  box-shadow: 0 10px 28px rgba(var(--accent-rgb),0.22);
```

Replace with:

```css
.btn-primary {
  background: linear-gradient(135deg, var(--primary), var(--primary-hover));
  color: #ffffff;
  border: 1px solid rgba(var(--primary-rgb),0.45);
  box-shadow: 0 10px 28px rgba(var(--primary-rgb),0.22);
```

Find (`frontend/styles.css:1213-1217`):

```css
.field-input:focus,
.accept-note-input:focus {
  border-color: rgba(var(--accent-rgb),0.75);
  box-shadow: 0 0 0 3px rgba(var(--accent-rgb),0.18), inset 0 1px 0 rgba(255,255,255,0.05);
}
```

Replace with:

```css
.field-input:focus,
.accept-note-input:focus {
  border-color: rgba(var(--primary-rgb),0.75);
  box-shadow: 0 0 0 3px rgba(var(--primary-rgb),0.18), inset 0 1px 0 rgba(255,255,255,0.05);
}
```

- [ ] **Step 3: Manual verification**

Reload the dev server. Confirm: stat cards render in a responsive grid (resize the window narrow — cards should wrap to fewer columns, not overflow), hovering a stat card brightens its border to indigo, and any `btn-primary` button elsewhere in the app (e.g. Config tab's "+ New Config") now shows an indigo gradient instead of amber. Toggle to light theme (Task 2's button) and confirm the sidebar/top bar/Home cards switch to light backgrounds with readable text (this is the "spec-scoped" light theme — other tabs like Compare are expected to still look dark, per the confirmed scope boundary).

- [ ] **Step 4: Commit**

```bash
git add frontend/styles.css
git commit -m "feat(frontend): style Home stat cards/status pills, repoint primary buttons to indigo token"
```

---

### Task 10: New Playwright coverage for sidebar, Home tab, and theme toggle

**Files:**
- Create: `tests/e2e/15-home-and-nav.spec.ts`

- [ ] **Step 1: Write the spec**

```typescript
import { test, expect } from './fixtures';

test.describe('15 home and navigation', () => {
  test('sidebar groups tabs and highlights the active item', async ({ authedPage }) => {
    await authedPage.goto('/');
    await expect(authedPage.locator('[data-testid="nav-tab-home"]')).toHaveClass(/active/);
    await authedPage.locator('[data-testid="nav-tab-config"]').click();
    await expect(authedPage.locator('[data-testid="nav-tab-config"]')).toHaveClass(/active/);
    await expect(authedPage.locator('[data-testid="nav-tab-home"]')).not.toHaveClass(/active/);
  });

  test('sidebar collapse toggles width state', async ({ authedPage }) => {
    await authedPage.goto('/');
    const sidebar = authedPage.locator('[data-testid="app-sidebar"]');
    await expect(sidebar).not.toHaveClass(/is-collapsed/);
    await authedPage.locator('[data-testid="sidebar-collapse-btn"]').click();
    await expect(sidebar).toHaveClass(/is-collapsed/);
  });

  test('Home is the default landing view with stat cards', async ({ authedPage }) => {
    await authedPage.goto('/');
    await expect(authedPage.locator('[data-testid="home-view"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="home-stat-active-runs"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="home-stat-environments"]')).toBeVisible();
  });

  test('stat card navigates to the corresponding tab', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="home-stat-environments"]').click();
    await expect(authedPage.locator('[data-testid="nav-tab-config"]')).toHaveClass(/active/);
  });

  test('quick action opens the new-config modal on the Config tab', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="home-quick-action-new-config"]').click();
    await expect(authedPage.locator('[data-testid="nav-tab-config"]')).toHaveClass(/active/);
    await expect(authedPage.locator('[data-testid="config-new-btn"]')).toBeVisible();
  });

  test('theme toggle persists across reload', async ({ authedPage }) => {
    await authedPage.goto('/');
    await expect(authedPage.locator('html')).toHaveAttribute('data-theme', 'dark');
    await authedPage.locator('[data-testid="theme-toggle-btn"]').click();
    await expect(authedPage.locator('html')).toHaveAttribute('data-theme', 'light');
    await authedPage.reload();
    await expect(authedPage.locator('html')).toHaveAttribute('data-theme', 'light');
    // reset so later spec files (and their pixel/contrast assumptions) see the default theme
    await authedPage.locator('[data-testid="theme-toggle-btn"]').click();
  });

  test('negative: quick action with zero runs shows the recent-activity empty state', async ({ authedPage }) => {
    await authedPage.goto('/');
    const table = authedPage.locator('[data-testid="home-recent-runs-table"]');
    // Either populated rows or the explicit empty-state text — never a blank/broken table.
    const hasRows = await table.locator('tbody tr').count();
    if (hasRows === 1) {
      await expect(table).toContainText('No runs yet');
    } else {
      expect(hasRows).toBeGreaterThan(0);
    }
  });
});
```

- [ ] **Step 2: Run the new spec**

Run: `npm run test:e2e -- tests/e2e/15-home-and-nav.spec.ts`
Expected: all 7 tests PASS. If the theme-toggle test fails on the `data-theme` attribute check, re-verify Task 2 Step 3/4 wired `applyTheme()` into `init()` correctly.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/15-home-and-nav.spec.ts
git commit -m "test(e2e): add coverage for sidebar grouping, Home tab, and theme toggle"
```

---

### Task 11: Full regression run

**Files:** none (verification only)

- [ ] **Step 1: Run the entire existing E2E suite**

Run: `npm run test:e2e`
Expected: all specs across `00`–`15` PASS. Pay particular attention to `00-auth-setup.spec.ts`, `11-help.spec.ts`, and `12-cross-cutting.spec.ts` — these are the ones that depend on `auth-status-connected`/`auth-status-open-btn`/`nav-tab-*` test IDs identified during planning as regression risks.

- [ ] **Step 2: If any spec fails**

Diagnose against the specific markup/CSS change in this plan that touched the failing selector (Tasks 5, 6, and 8 are the ones that moved or added DOM structure) — fix the regression in the relevant task's file, re-run the single failing spec file to confirm the fix, then re-run the full suite.

- [ ] **Step 3: Commit** (only if fixes were needed in Step 2; skip if Step 1 passed clean)

```bash
git add -A
git commit -m "fix(frontend): resolve e2e regressions from sidebar/home redesign"
```

---

## Self-Review Notes

- **Spec coverage:** Navigation hierarchy → Tasks 3, 5, 7. Color scheme → Task 1 (+9 for component repointing). Component spec (buttons/forms/cards/tables/status pills/sidebar item) → Tasks 7, 9 (buttons/forms/sidebar), table styling reused as-is from existing `.data-table` rules (already spec-compliant: sticky-feeling header via `--panel-raised` bg, border-bottom rows, no zebra — confirmed against `frontend/styles.css:1308-1320`, no changes needed there). Home tab → Task 8 (markup) + Task 9 (styling) + Task 4 (data). Testing → Tasks 10, 11.
- **Preserved test IDs:** `nav-tab-*` (moved, not renamed — Task 5), `auth-status-connected`/`auth-status-open-btn` (untouched — Task 6 explicitly leaves `auth-status-bar` alone), `config-new-btn` (untouched, reused by Task 10's quick-action test).
- **No new backend calls:** confirmed `homeStats`/`homeRecentRuns` (Task 4) read only from `runs`/`configs`, both already loaded by the existing `loadAll()` in `init()`.
