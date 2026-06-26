# Metrics & Logs UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken new-tab Metrics/Logs buttons with in-app navigation to an enhanced Reports tab featuring a live client-side log filter and an improved per-test metrics table.

**Architecture:** All data fetching uses the existing `api()` helper (adds `Authorization: Bearer` header automatically). Log events are fetched once as JSON and filtered in the browser as the user types — no per-keystroke API calls. Clicking Metrics or Logs on a run in History calls `navigateToRunArtifact()` which switches the view and pre-loads the data.

**Tech Stack:** Alpine.js (x-data, x-for, x-html, x-model), plain HTML/CSS, existing FastAPI JSON endpoints (`/api/runs/{id}/logs?format=json`, `/api/runs/{id}/metrics?format=json`).

---

## File Map

| File | Changes |
|---|---|
| `frontend/app.js` | Add 4 state fields, 4 new methods, update 3 existing methods |
| `frontend/index.html` | Update 2 History buttons, replace Logs sub-tab HTML, enhance Metrics table rows |
| `frontend/styles.css` | Add `.log-highlight` and `.level-chip` + active-variant rules |

---

## Task 1: Add live-filter state fields to app.js

**Files:**
- Modify: `frontend/app.js:240` (after `reportLogLimit: 500,`)

No tests exist for this frontend code — verify manually after each task by opening the app.

- [ ] **Step 1: Add four state fields after `reportLogLimit: 500,`**

Open `frontend/app.js`. Find the line `reportLogLimit: 500,` (currently line 240). Add immediately after it:

```javascript
    reportLogLimit: 500,
    allLogEvents: [],
    allLogEventsLoading: false,
    logFilterQuery: '',
    logFilterLevel: '',
```

- [ ] **Step 2: Verify the state block looks correct**

The Reports tab state block should now read:

```javascript
    reportRunId: '',
    reportLoaded: false,
    reportBlobUrl: '',
    reportView: 'report',
    reportMetrics: null,
    reportMetricsLoading: false,
    reportLogs: null,
    reportLogsLoading: false,
    reportLogQuery: '',
    reportLogLevel: '',
    reportLogLimit: 500,
    allLogEvents: [],
    allLogEventsLoading: false,
    logFilterQuery: '',
    logFilterLevel: '',
```

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): add live-filter log state fields"
```

---

## Task 2: Add new methods to app.js

**Files:**
- Modify: `frontend/app.js` — insert after `openRunTab` method (currently ends around line 2058)

- [ ] **Step 1: Add `loadAllLogEvents`, `filteredLogEvents`, `highlightMatch`, and `navigateToRunArtifact` after `openRunTab`**

Find the closing `},` of `openRunTab` and insert the four methods immediately after:

```javascript
    async loadAllLogEvents() {
      if (!this.reportRunId) return;
      this.allLogEventsLoading = true;
      this.allLogEvents = [];
      try {
        const data = await api('GET', `/api/runs/${this.reportRunId}/logs?format=json&limit=5000&scope=run`);
        this.allLogEvents = data.lines || [];
      } catch (e) {
        this.toast('error', 'Failed to load logs', e.message);
      } finally {
        this.allLogEventsLoading = false;
      }
    },

    filteredLogEvents() {
      let events = this.allLogEvents;
      if (this.logFilterLevel) {
        const lvl = this.logFilterLevel.toUpperCase();
        events = events.filter(e => {
          const el = (e.level || '').toUpperCase();
          if (lvl === 'WARNING') return el === 'WARNING' || el === 'WARN';
          return el === lvl;
        });
      }
      if (this.logFilterQuery.trim()) {
        const q = this.logFilterQuery.toLowerCase();
        events = events.filter(e => (e.text || '').toLowerCase().includes(q));
      }
      return events;
    },

    highlightMatch(text, query) {
      const safe = (text || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
      if (!query.trim()) return safe;
      const escapedQ = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      return safe.replace(new RegExp(`(${escapedQ})`, 'gi'), '<mark class="log-highlight">$1</mark>');
    },

    navigateToRunArtifact(runId, view) {
      this.resetReportArtifacts();
      this.reportRunId = runId;
      this.reportView = view;
      this.currentView = 'reports';
      this.reportLoaded = true;
      if (view === 'metrics') this.loadRunMetrics();
      if (view === 'logs') this.loadAllLogEvents();
    },
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): add navigateToRunArtifact, loadAllLogEvents, filteredLogEvents, highlightMatch"
```

---

## Task 3: Update existing methods in app.js

**Files:**
- Modify: `frontend/app.js` — `resetReportArtifacts`, `switchReportView`, `loadReport`

- [ ] **Step 1: Update `resetReportArtifacts` to clear live-filter state**

Find `resetReportArtifacts()` and replace its body:

```javascript
    resetReportArtifacts() {
      this.reportLoaded = false;
      this.reportMetrics = null;
      this.reportLogs = null;
      if (this.reportBlobUrl) { URL.revokeObjectURL(this.reportBlobUrl); this.reportBlobUrl = ''; }
      this.allLogEvents = [];
      this.logFilterQuery = '';
      this.logFilterLevel = '';
    },
```

- [ ] **Step 2: Update `switchReportView` to use `loadAllLogEvents` for logs**

Find `switchReportView` and replace it:

```javascript
    async switchReportView(view) {
      this.reportView = view;
      if (!this.reportRunId || !this.reportLoaded) return;
      if (view === 'metrics') await this.loadRunMetrics();
      if (view === 'logs' && this.allLogEvents.length === 0) await this.loadAllLogEvents();
    },
```

- [ ] **Step 3: Update `loadReport` to call `loadAllLogEvents` for the logs view**

Find `loadReport` and replace it:

```javascript
    async loadReport() {
      if (!this.reportRunId) return;
      if (this.reportBlobUrl) { URL.revokeObjectURL(this.reportBlobUrl); this.reportBlobUrl = ''; }
      try {
        const { blob } = await apiBlob(`/api/runs/${this.reportRunId}/report`);
        this.reportBlobUrl = URL.createObjectURL(blob);
      } catch (e) {
        this.toast('error', 'Failed to load report', e.message);
      }
      this.reportLoaded = true;
      if (this.reportView === 'metrics') this.loadRunMetrics();
      if (this.reportView === 'logs') this.loadAllLogEvents();
    },
```

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): wire loadAllLogEvents into loadReport and switchReportView"
```

---

## Task 4: Update History tab buttons in index.html

**Files:**
- Modify: `frontend/index.html:1350-1351`

- [ ] **Step 1: Replace Metrics and Logs buttons in the History run-detail panel**

Find these two lines (currently 1350–1351):

```html
            <button @click="openRunTab(selectedRun.run_id, 'metrics')" class="text-indigo-500 hover:underline text-xs">Metrics</button>
            <button @click="openRunTab(selectedRun.run_id, 'logs')" class="text-indigo-500 hover:underline text-xs">Logs</button>
```

Replace with:

```html
            <button @click="navigateToRunArtifact(selectedRun.run_id, 'metrics')" class="text-indigo-500 hover:underline text-xs">Metrics</button>
            <button @click="navigateToRunArtifact(selectedRun.run_id, 'logs')" class="text-indigo-500 hover:underline text-xs">Logs</button>
```

- [ ] **Step 2: Verify manually**

Open the app, go to History, click a run, click **Metrics** — should jump to the Reports tab with the Metrics sub-tab pre-loaded for that run. Click **Logs** — should jump to Reports → Logs sub-tab.

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): History Metrics/Logs buttons navigate in-app via navigateToRunArtifact"
```

---

## Task 5: Add CSS for log highlight and level chips

**Files:**
- Modify: `frontend/styles.css` — append at end of file

- [ ] **Step 1: Append CSS rules to styles.css**

Add to the end of `frontend/styles.css`:

```css
/* Live log filter — keyword highlight */
.log-highlight {
  background: rgba(251, 191, 36, 0.28);
  color: #fcd34d;
  border-radius: 2px;
  padding: 0 2px;
}

/* Level filter chips */
.level-chip {
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid rgba(148, 163, 184, 0.4);
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  background: rgba(255, 255, 255, 0.05);
  color: var(--muted);
  transition: background 0.15s, border-color 0.15s, color 0.15s;
  line-height: 1;
}
.level-chip:hover { background: rgba(255, 255, 255, 0.09); }
.level-chip.chip-active-ALL    { background: rgba(255,255,255,.12); border-color: rgba(255,255,255,.4); color: var(--text); }
.level-chip.chip-active-ERROR  { border-color: rgba(251,113,133,.6); color: #fda4af; background: rgba(251,113,133,.12); }
.level-chip.chip-active-WARNING { border-color: rgba(251,191,36,.6); color: #fcd34d; background: rgba(251,191,36,.12); }
.level-chip.chip-active-INFO   { border-color: rgba(59,130,246,.6);  color: #93c5fd; background: rgba(59,130,246,.12); }
.level-chip.chip-active-DEBUG  { border-color: rgba(139,92,246,.6);  color: #c4b5fd; background: rgba(139,92,246,.12); }

/* Metrics table row tinting */
.metric-row-failed { background: rgba(251, 113, 133, 0.06); }
.metric-row-slow   { background: rgba(251, 191, 36,  0.04); }
.metric-mini-bar   { width: 72px; height: 4px; background: rgba(255,255,255,.1); border-radius: 999px; overflow: hidden; }
.metric-mini-bar span { display: block; height: 100%; border-radius: 999px; }
```

- [ ] **Step 2: Commit**

```bash
git add frontend/styles.css
git commit -m "feat(frontend): add log-highlight, level-chip, and metric row tint CSS"
```

---

## Task 6: Replace Logs sub-tab HTML in index.html

**Files:**
- Modify: `frontend/index.html:2397-2451`

- [ ] **Step 1: Replace the entire logs template**

Find and replace the block starting at `<template x-if="reportRunId && reportLoaded && reportView === 'logs'">` and ending at its closing `</template>` (lines 2397–2451):

**Old block (exact match):**

```html
  <template x-if="reportRunId && reportLoaded && reportView === 'logs'">
    <div class="space-y-4">
      <div class="card">
        <div class="log-toolbar">
          <div class="flex-1" style="min-width:240px">
            <label class="field-label">Search logs</label>
            <input x-model="reportLogQuery" @keydown.enter.prevent="loadRunLogs()" class="field-input" placeholder="exception, module, run id, message" />
          </div>
          <div style="min-width:150px">
            <label class="field-label">Level</label>
            <select x-model="reportLogLevel" class="field-input field-select">
              <option value="">All levels</option>
              <option value="ERROR">ERROR</option>
              <option value="WARNING">WARNING</option>
              <option value="INFO">INFO</option>
              <option value="DEBUG">DEBUG</option>
            </select>
          </div>
          <div style="width:120px">
            <label class="field-label">Limit</label>
            <input x-model.number="reportLogLimit" type="number" min="1" max="5000" class="field-input" />
          </div>
          <button @click="loadRunLogs()" :disabled="reportLogsLoading" class="btn-primary">
            <span x-text="reportLogsLoading ? 'Searching...' : 'Search'"></span>
          </button>
          <button @click="openRunTab(reportRunId, 'logs')" class="btn-secondary">Open Log GUI</button>
        </div>
      </div>
      <template x-if="reportLogs">
        <div class="card p-0 overflow-hidden">
          <div class="log-summary">
            <span x-text="reportLogs.matched_lines + ' matches'"></span>
            <span x-text="reportLogs.total_lines + ' total lines'"></span>
          </div>
          <div class="log-list">
            <template x-if="!reportLogs.lines || reportLogs.lines.length === 0">
              <div class="empty-state"><div class="empty-state-title">No log lines match the current search</div></div>
            </template>
            <template x-for="line in (reportLogs.lines || [])" :key="line.number">
              <div class="log-entry" :class="logLevelClass(line.level)">
                <div class="log-entry-meta">
                  <span class="font-mono" x-text="'#' + line.number"></span>
                  <span class="badge" x-text="line.level"></span>
                </div>
                <pre x-text="line.text"></pre>
              </div>
            </template>
          </div>
        </div>
      </template>
      <template x-if="reportLogsLoading">
        <div class="card empty-state"><div class="empty-state-title">Searching logs...</div></div>
      </template>
    </div>
  </template>
```

**New block:**

```html
  <template x-if="reportRunId && reportLoaded && reportView === 'logs'">
    <div class="space-y-4">
      <!-- Search bar + level chips -->
      <div class="card">
        <div class="flex flex-col gap-3">
          <input
            x-model="logFilterQuery"
            class="field-input"
            placeholder="Search logs… (live)"
          />
          <div class="flex gap-2 flex-wrap items-center">
            <button
              class="level-chip"
              :class="logFilterLevel === '' ? 'chip-active-ALL' : ''"
              @click="logFilterLevel = ''">ALL</button>
            <button
              class="level-chip"
              :class="logFilterLevel === 'ERROR' ? 'chip-active-ERROR' : ''"
              @click="logFilterLevel = logFilterLevel === 'ERROR' ? '' : 'ERROR'">ERROR</button>
            <button
              class="level-chip"
              :class="logFilterLevel === 'WARNING' ? 'chip-active-WARNING' : ''"
              @click="logFilterLevel = logFilterLevel === 'WARNING' ? '' : 'WARNING'">WARN</button>
            <button
              class="level-chip"
              :class="logFilterLevel === 'INFO' ? 'chip-active-INFO' : ''"
              @click="logFilterLevel = logFilterLevel === 'INFO' ? '' : 'INFO'">INFO</button>
            <button
              class="level-chip"
              :class="logFilterLevel === 'DEBUG' ? 'chip-active-DEBUG' : ''"
              @click="logFilterLevel = logFilterLevel === 'DEBUG' ? '' : 'DEBUG'">DEBUG</button>
            <span class="text-muted text-xs ml-2" x-text="filteredLogEvents().length + ' / ' + allLogEvents.length + ' events'"></span>
          </div>
        </div>
      </div>

      <!-- Loading state -->
      <template x-if="allLogEventsLoading">
        <div class="card empty-state"><div class="empty-state-title">Loading logs…</div></div>
      </template>

      <!-- Empty state -->
      <template x-if="!allLogEventsLoading && allLogEvents.length === 0">
        <div class="card empty-state"><div class="empty-state-title">No log events for this run.</div></div>
      </template>

      <!-- Log list -->
      <template x-if="!allLogEventsLoading && allLogEvents.length > 0">
        <div class="card p-0 overflow-hidden">
          <div class="log-list">
            <template x-if="filteredLogEvents().length === 0">
              <div class="empty-state"><div class="empty-state-title">No events match the current filter.</div></div>
            </template>
            <template x-for="line in filteredLogEvents()" :key="line.number">
              <div class="log-entry" :class="logLevelClass(line.level)">
                <div class="log-entry-meta">
                  <span class="font-mono" x-text="'#' + line.number"></span>
                  <span class="badge" :class="'badge-' + (line.level || '').toLowerCase()" x-text="line.level"></span>
                </div>
                <pre x-html="highlightMatch(line.text, logFilterQuery)"></pre>
              </div>
            </template>
          </div>
        </div>
      </template>
    </div>
  </template>
```

- [ ] **Step 2: Verify manually**

Open the app, go to Reports tab, select any completed run, click **Load**, switch to the **Logs** sub-tab. You should see all events load. Type in the search box — results should filter live. Click level chips — results should filter by level. Keyword matches should be highlighted in yellow.

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): replace Logs sub-tab with live client-side filter + level chips"
```

---

## Task 7: Enhance Metrics sub-tab table in index.html

**Files:**
- Modify: `frontend/index.html:2344-2395`

- [ ] **Step 1: Replace the metrics template**

Find and replace the block starting at `<template x-if="reportRunId && reportLoaded && reportView === 'metrics'">` (lines 2344–2395):

**Old block (exact match):**

```html
  <template x-if="reportRunId && reportLoaded && reportView === 'metrics'">
    <div class="space-y-4">
      <div class="flex gap-2 mb-3 flex-wrap">
        <button @click="loadRunMetrics()" :disabled="reportMetricsLoading" class="btn-secondary btn-sm">
          <span x-text="reportMetricsLoading ? 'Refreshing...' : 'Refresh Metrics'"></span>
        </button>
        <button @click="openRunTab(reportRunId, 'metrics')" class="btn-secondary btn-sm">Open Themed Metrics</button>
        <button @click="openRunTab(reportRunId, 'metrics?format=json')" class="btn-secondary btn-sm">Raw JSON</button>
      </div>
      <template x-if="reportMetricsLoading">
        <div class="card empty-state"><div class="empty-state-title">Loading metrics...</div></div>
      </template>
      <template x-if="!reportMetricsLoading && reportMetrics">
        <div>
          <div class="metric-grid mb-4">
            <div class="metric-card metric-cyan">
              <div class="metric-label">Pass Rate</div>
              <div class="metric-value" x-text="metricsPassRate(reportMetrics) + '%'"></div>
              <div class="metric-bar"><span :style="'width:' + metricsPassRate(reportMetrics) + '%'"></span></div>
            </div>
            <div class="metric-card metric-green"><div class="metric-label">Passed</div><div class="metric-value" x-text="reportMetrics.passed || 0"></div></div>
            <div class="metric-card metric-rose"><div class="metric-label">Failed</div><div class="metric-value" x-text="reportMetrics.failed || 0"></div></div>
            <div class="metric-card metric-amber"><div class="metric-label">Slow</div><div class="metric-value" x-text="reportMetrics.slow || 0"></div></div>
            <div class="metric-card metric-violet"><div class="metric-label">Duration</div><div class="metric-value" x-text="(reportMetrics.total_duration_seconds || 0).toFixed(3) + 's'"></div></div>
          </div>
          <div class="card overflow-hidden p-0">
            <table class="data-table">
              <thead><tr><th>Test</th><th>Status</th><th>Duration</th><th>Source Rows</th><th>Target Rows</th><th>Issues</th></tr></thead>
              <tbody>
                <template x-if="!reportMetrics.tests || reportMetrics.tests.length === 0">
                  <tr><td colspan="6" class="text-center text-muted py-4">No per-test metrics were recorded.</td></tr>
                </template>
                <template x-for="t in (reportMetrics.tests || [])" :key="t.name">
                  <tr>
                    <td class="font-mono text-xs" x-text="t.name"></td>
                    <td><span class="badge" :class="statusBadgeClass(t.status)" x-text="t.status"></span></td>
                    <td x-text="t.duration_seconds != null ? t.duration_seconds.toFixed(3) + 's' : '-'"></td>
                    <td x-text="t.source_row_count ?? 0"></td>
                    <td x-text="t.target_row_count ?? 0"></td>
                    <td x-text="t.total_issues ?? 0"></td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
        </div>
      </template>
      <template x-if="!reportMetricsLoading && !reportMetrics">
        <div class="card empty-state"><div class="empty-state-title">Metrics are not available for this run</div></div>
      </template>
    </div>
  </template>
```

**New block:**

```html
  <template x-if="reportRunId && reportLoaded && reportView === 'metrics'">
    <div class="space-y-4">
      <div class="flex gap-2 mb-3 flex-wrap">
        <button @click="loadRunMetrics()" :disabled="reportMetricsLoading" class="btn-secondary btn-sm">
          <span x-text="reportMetricsLoading ? 'Refreshing…' : 'Refresh Metrics'"></span>
        </button>
      </div>
      <template x-if="reportMetricsLoading">
        <div class="card empty-state"><div class="empty-state-title">Loading metrics…</div></div>
      </template>
      <template x-if="!reportMetricsLoading && reportMetrics">
        <div>
          <div class="metric-grid mb-4">
            <div class="metric-card metric-cyan">
              <div class="metric-label">Pass Rate</div>
              <div class="metric-value" x-text="metricsPassRate(reportMetrics) + '%'"></div>
              <div class="metric-bar"><span :style="'width:' + metricsPassRate(reportMetrics) + '%'"></span></div>
            </div>
            <div class="metric-card metric-green"><div class="metric-label">Passed</div><div class="metric-value" x-text="reportMetrics.passed || 0"></div></div>
            <div class="metric-card metric-rose"><div class="metric-label">Failed</div><div class="metric-value" x-text="reportMetrics.failed || 0"></div></div>
            <div class="metric-card metric-amber"><div class="metric-label">Slow</div><div class="metric-value" x-text="reportMetrics.slow || 0"></div></div>
            <div class="metric-card metric-violet"><div class="metric-label">Duration</div><div class="metric-value" x-text="(reportMetrics.total_duration_seconds || 0).toFixed(3) + 's'"></div></div>
          </div>
          <div class="card overflow-hidden p-0">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Test</th><th>Status</th><th>Duration</th>
                  <th>Source Rows</th><th>Target Rows</th><th>Issues</th><th></th>
                </tr>
              </thead>
              <tbody>
                <template x-if="!reportMetrics.tests || reportMetrics.tests.length === 0">
                  <tr><td colspan="7" class="text-center text-muted py-4">No per-test metrics were recorded.</td></tr>
                </template>
                <template x-for="t in (reportMetrics.tests || [])" :key="t.name">
                  <tr :class="(t.status||'').toUpperCase()==='FAILED' ? 'metric-row-failed' : (t.status||'').toUpperCase()==='SLOW' ? 'metric-row-slow' : ''">
                    <td class="font-mono text-xs" x-text="t.name"></td>
                    <td><span class="badge" :class="statusBadgeClass(t.status)" x-text="t.status"></span></td>
                    <td
                      :class="(t.status||'').toUpperCase()==='SLOW' ? 'text-amber-400 font-semibold' : ''"
                      x-text="t.duration_seconds != null ? t.duration_seconds.toFixed(3) + 's' : '-'"></td>
                    <td x-text="t.source_row_count ?? 0"></td>
                    <td x-text="t.target_row_count ?? 0"></td>
                    <td
                      :class="(t.total_issues ?? 0) > 0 ? 'text-rose-400 font-bold' : 'text-emerald-400'"
                      x-text="t.total_issues ?? 0"></td>
                    <td>
                      <div class="metric-mini-bar">
                        <span :style="'width:100%;background:' + ((t.status||'').toUpperCase()==='FAILED' ? 'var(--rose)' : (t.status||'').toUpperCase()==='SLOW' ? 'var(--amber)' : 'var(--emerald)')"></span>
                      </div>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
        </div>
      </template>
      <template x-if="!reportMetricsLoading && !reportMetrics">
        <div class="card empty-state"><div class="empty-state-title">Metrics are not available for this run</div></div>
      </template>
    </div>
  </template>
```

- [ ] **Step 2: Verify manually**

Open the app, go to Reports tab, select a run with mixed results, click **Load**, switch to **Metrics**. Failed test rows should have a faint red background, slow rows amber. The Issues column should show red text for failures. A coloured mini-bar should appear in the last column.

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): enhance metrics table with row tinting, coloured values, mini bar"
```

---

## Task 8: Final smoke test

- [ ] **Step 1: Full end-to-end check**

1. Open the app → History tab
2. Click any completed run → run detail panel opens
3. Click **Metrics** → app switches to Reports tab, Metrics sub-tab loads for that run (no 401 error)
4. Click back to History, click **Logs** → app switches to Reports tab, Logs sub-tab, events load
5. Type in the search box → results filter live with no button click
6. Click **ERROR** chip → only ERROR events shown; click again to deselect
7. Match count in chip bar updates correctly
8. Type a keyword that appears in log text → matching term highlighted in yellow
9. Open Reports tab manually, select a run from the dropdown, click **Load** → same behaviours work

- [ ] **Step 2: Check for regressions**

1. Report sub-tab (iframe) still loads correctly
2. Export CSV still works
3. History page still shows runs

- [ ] **Step 3: Final commit if any fixups were needed**

```bash
git add -p
git commit -m "fix(frontend): smoke test fixups for metrics/logs UI"
```
