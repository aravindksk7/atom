# Enhanced Diff Display — Better Than BeyondCompare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add character-level diff highlighting, filter toolbars, column heat maps, and navigation to both the HTML report template and the Compare tab UI, making the comparison display richer than BeyondCompare.

**Architecture:** Pure client-side JavaScript changes only — no new API endpoints, no Python model changes. The `charDiff`/`renderSrc`/`renderTgt` utility is written twice (once embedded in the Jinja2 HTML report template, once in `app.js`). All four changed files are frontend/template files.

**Tech Stack:** Jinja2 (autoescape=True), Alpine.js 3.x, vanilla JS (ES2020), CSS custom properties already defined in `frontend/styles.css` and `report.html.j2`.

---

## File Map

| File | Role |
|------|------|
| `etl_framework/reporting/templates/report.html.j2` | Standalone HTML report — all report enhancements |
| `frontend/styles.css` | Shared UI styles — new diff/filter/chart classes |
| `frontend/app.js` | Alpine component — diff utility + filter state + methods |
| `frontend/index.html` | Compare tab markup — filter bars, x-html diff, column summary |

---

## Task 1: Add diff CSS classes to `frontend/styles.css`

All new CSS classes used by both the Compare tab UI and the HTML report live here. Do this first so later tasks can reference them.

**Files:**
- Modify: `frontend/styles.css`

- [ ] **Step 1.1: Append new CSS rules**

Open `frontend/styles.css` and append the following at the end of the file:

```css
/* ── Enhanced Diff Display ───────────────────────────────────── */

/* Character-level diff spans */
.diff-del {
  background: rgba(251,113,133,0.28);
  color: #be123c;
  border-radius: 2px;
  padding: 0 1px;
}
.diff-ins {
  background: rgba(52,211,153,0.25);
  color: #065f46;
  border-radius: 2px;
  padding: 0 1px;
}
.null-val { color: #94a3b8; font-style: italic; }

/* Filter bar (Compare tab) */
.diff-filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  flex-wrap: wrap;
}
.diff-filter-bar select {
  padding: 2px 6px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  font-size: 0.8em;
  background: white;
}
.diff-filter-search {
  padding: 2px 6px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  font-size: 0.8em;
}
.diff-filter-count { font-size: 0.75em; color: #94a3b8; margin-left: auto; }

/* Expandable value cells */
.diff-val-truncated {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.diff-expand-btn {
  font-size: 0.75em;
  color: #3b82f6;
  cursor: pointer;
  background: none;
  border: none;
  padding: 0 4px;
  vertical-align: middle;
}

/* Column summary mini-chart (Compare tab) */
.col-summary {
  padding: 8px 12px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
}
.col-summary-label {
  font-size: 0.72em;
  text-transform: uppercase;
  color: #94a3b8;
  letter-spacing: 0.05em;
  margin-bottom: 4px;
}
.col-bar-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 3px;
  cursor: pointer;
}
.col-bar-row:hover .col-bar-name { color: #3b82f6; }
.col-bar-name {
  font-size: 0.78em;
  color: #475569;
  width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.col-bar-track {
  flex: 1;
  height: 8px;
  background: #e2e8f0;
  border-radius: 4px;
  overflow: hidden;
}
.col-bar-fill {
  height: 100%;
  background: #fb7185;
  border-radius: 4px;
  transition: width 0.3s ease;
}
.col-bar-count { font-size: 0.75em; color: #94a3b8; width: 24px; text-align: right; }
```

- [ ] **Step 1.2: Commit**

```bash
git add frontend/styles.css
git commit -m "feat(diff): add diff highlight, filter bar, and column summary CSS"
```

---

## Task 2: Add `charDiff` / `renderSrc` / `renderTgt` to `frontend/app.js`

The diff utility lives at the top of `app.js` (before the `function app()` declaration) so it's available globally for Alpine bindings.

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 2.1: Add diff utility before `function app()`**

Find the line `function app() {` in `frontend/app.js`. Insert the following block **immediately before** it:

```javascript
// ── Char-level diff utility ───────────────────────────────────────────────
function _escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function _charDiff(a, b) {
  // LCS O(n*m) character diff. Falls back to full-string highlight for
  // strings > 500 chars to keep it fast.
  const n = a.length, m = b.length;
  if (n === 0) return b.split('').map(c => ({ text: c, op: '+' }));
  if (m === 0) return a.split('').map(c => ({ text: c, op: '-' }));
  const dp = [];
  for (let i = 0; i <= n; i++) { dp[i] = new Uint16Array(m + 1); }
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      dp[i][j] = a[i-1] === b[j-1]
        ? dp[i-1][j-1] + 1
        : Math.max(dp[i-1][j], dp[i][j-1]);
    }
  }
  const ops = [];
  let i = n, j = m;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i-1] === b[j-1]) {
      ops.push({ text: a[i-1], op: '=' }); i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j-1] >= dp[i-1][j])) {
      ops.push({ text: b[j-1], op: '+' }); j--;
    } else {
      ops.push({ text: a[i-1], op: '-' }); i--;
    }
  }
  ops.reverse();
  // Merge consecutive same-op runs
  const merged = [];
  for (const { text, op } of ops) {
    if (merged.length && merged[merged.length-1].op === op) merged[merged.length-1].text += text;
    else merged.push({ text, op });
  }
  return merged;
}

/**
 * Render the SOURCE side of a diff: source text with deleted chars highlighted.
 * Returns an HTML string safe to use with x-html.
 */
function renderSrc(rawA, rawB) {
  if (rawA == null && rawB == null) return '<span class="null-val">NULL</span>';
  if (rawA == null) return '<span class="null-val">NULL</span>';
  if (rawB == null) return _escHtml(String(rawA));
  // Skip char-diff for numbers (both parseable as finite numbers)
  if (!isNaN(rawA) && !isNaN(rawB) && isFinite(rawA) && isFinite(rawB)) {
    return _escHtml(String(rawA));
  }
  const sa = String(rawA), sb = String(rawB);
  if (sa.length > 500 || sb.length > 500) return _escHtml(sa.slice(0, 500)) + '…';
  const ops = _charDiff(sa, sb);
  return ops.map(({ text, op }) =>
    op === '-' ? `<span class="diff-del">${_escHtml(text)}</span>` :
    op === '=' ? _escHtml(text) : ''          // source shows only what A has
  ).join('');
}

/**
 * Render the TARGET side of a diff: target text with inserted chars highlighted.
 * Returns an HTML string safe to use with x-html.
 */
function renderTgt(rawA, rawB) {
  if (rawA == null && rawB == null) return '<span class="null-val">NULL</span>';
  if (rawB == null) return '<span class="null-val">NULL</span>';
  if (rawA == null) return _escHtml(String(rawB));
  if (!isNaN(rawA) && !isNaN(rawB) && isFinite(rawA) && isFinite(rawB)) {
    return _escHtml(String(rawB));
  }
  const sa = String(rawA), sb = String(rawB);
  if (sa.length > 500 || sb.length > 500) return _escHtml(sb.slice(0, 500)) + '…';
  const ops = _charDiff(sa, sb);
  return ops.map(({ text, op }) =>
    op === '+' ? `<span class="diff-ins">${_escHtml(text)}</span>` :
    op === '=' ? _escHtml(text) : ''          // target shows only what B has
  ).join('');
}
// ─────────────────────────────────────────────────────────────────────────────
```

- [ ] **Step 2.2: Add filter state variables to `app()` state object**

Inside `function app()`, find the block that contains `sqlExpandedDiffs: {},` (around line 341). Add these three new state properties immediately after it:

```javascript
sqlDiffFilter: {},    // { [query_name]: { type: '', col: '', search: '' } }
fileDiffFilter: {},   // same shape, for file compare
expandedCell: {},     // { [mismatch_id + '_src' | '_tgt']: true } — expanded long values
```

- [ ] **Step 2.3: Add `filteredDiff` and `colSummary` methods**

Inside `function app()`, find the `methods:` section (or any method block — search for `async loadRuns()`). Add the following two methods anywhere in the methods block:

```javascript
filteredDiff(diffs, filterKey, filterState) {
  const f = filterState[filterKey] || {};
  if (!f.type && !f.col && !f.search) return diffs;
  return (diffs || []).filter(m => {
    if (f.type && m.mismatch_type !== f.type) return false;
    if (f.col  && m.column_name !== f.col)   return false;
    if (f.search) {
      const key = JSON.stringify(m.key_values || {}).toLowerCase();
      if (!key.includes(f.search.toLowerCase())) return false;
    }
    return true;
  });
},

colSummary(diffs) {
  const counts = {};
  (diffs || []).forEach(m => {
    const c = m.column_name || '(none)';
    counts[c] = (counts[c] || 0) + 1;
  });
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const max = sorted[0]?.[1] || 1;
  return sorted.map(([col, count]) => ({ col, count, pct: Math.round(count / max * 100) }));
},
```

- [ ] **Step 2.4: Verify app.js loads without error**

Open the app in a browser (`http://localhost:8004` or wherever it runs). Open DevTools → Console. There should be no JS errors. If `renderSrc is not defined` appears, check that the utility block was inserted **before** `function app()`.

- [ ] **Step 2.5: Commit**

```bash
git add frontend/app.js
git commit -m "feat(diff): add charDiff utility and filter/colSummary methods to app.js"
```

---

## Task 3: Add column summary + filter bar to SQL compare result panel in `index.html`

The SQL compare result section is inside a `<template x-for>` that iterates over `sqlCompareResult.results`. We enhance the expanded diff section.

**Files:**
- Modify: `frontend/index.html`

- [ ] **Step 3.1: Locate the SQL compare expanded diff section**

Search for this string in `frontend/index.html`:

```
<div x-show="r.status !== 'PASSED' && sqlExpandedDiffs[r.query_name]?.open" class="border-t border-slate-200">
```

You will find a block that ends with `</div>` after the "No row-level diff details available." paragraph.

- [ ] **Step 3.2: Replace the entire expanded diff block**

Replace the full block starting with `<div x-show="r.status !== 'PASSED' && sqlExpandedDiffs[r.query_name]?.open"` and ending at its closing `</div>` with the following:

```html
<div x-show="r.status !== 'PASSED' && sqlExpandedDiffs[r.query_name]?.open" class="border-t border-slate-200">
  <div x-show="sqlExpandedDiffs[r.query_name]?.loading" class="px-3 py-2 text-xs text-slate-400">Loading diff details…</div>
  <div x-show="sqlExpandedDiffs[r.query_name]?.error" x-text="sqlExpandedDiffs[r.query_name]?.error" class="px-3 py-2 text-xs text-red-500"></div>
  <template x-if="sqlExpandedDiffs[r.query_name]?.data?.length">
    <div>
      <!-- Column summary mini-chart (hidden when only 1 column) -->
      <div class="col-summary" x-show="colSummary(sqlExpandedDiffs[r.query_name]?.data).length > 1">
        <div class="col-summary-label">Mismatches by column</div>
        <template x-for="item in colSummary(sqlExpandedDiffs[r.query_name]?.data)" :key="item.col">
          <div class="col-bar-row"
               @click="(sqlDiffFilter[r.query_name] ??= {type:'',col:'',search:''}).col = item.col">
            <span class="col-bar-name" x-text="item.col"></span>
            <div class="col-bar-track">
              <div class="col-bar-fill" :style="`width:${item.pct}%`"></div>
            </div>
            <span class="col-bar-count" x-text="item.count"></span>
          </div>
        </template>
      </div>
      <!-- Filter bar (hidden when ≤1 rows) -->
      <div class="diff-filter-bar" x-show="(sqlExpandedDiffs[r.query_name]?.data?.length || 0) > 1">
        <select x-model="(sqlDiffFilter[r.query_name] ??= {type:'',col:'',search:''}).type">
          <option value="">All types</option>
          <option value="value_mismatch">Value diff</option>
          <option value="missing_in_target">Missing →</option>
          <option value="missing_in_source">Missing ←</option>
        </select>
        <select x-model="(sqlDiffFilter[r.query_name] ??= {type:'',col:'',search:''}).col">
          <option value="">All columns</option>
          <template x-for="item in colSummary(sqlExpandedDiffs[r.query_name]?.data)" :key="item.col">
            <option :value="item.col" x-text="item.col"></option>
          </template>
        </select>
        <input type="text"
               x-model="(sqlDiffFilter[r.query_name] ??= {type:'',col:'',search:''}).search"
               placeholder="Search key values…"
               class="diff-filter-search">
        <span class="diff-filter-count"
              x-text="filteredDiff(sqlExpandedDiffs[r.query_name]?.data||[], r.query_name, sqlDiffFilter).length + ' shown'">
        </span>
      </div>
      <!-- Diff table -->
      <div class="overflow-x-auto max-h-64">
        <table class="text-xs w-full">
          <thead class="bg-slate-100 sticky top-0">
            <tr>
              <th class="px-2 py-1 text-left text-slate-600">Key</th>
              <th class="px-2 py-1 text-left text-slate-600">Column</th>
              <th class="px-2 py-1 text-left text-slate-600">Source</th>
              <th class="px-2 py-1 text-left text-slate-600">Target</th>
              <th class="px-2 py-1 text-left text-slate-600">Type</th>
            </tr>
          </thead>
          <tbody>
            <template x-for="(m, mi) in filteredDiff(sqlExpandedDiffs[r.query_name]?.data||[], r.query_name, sqlDiffFilter)" :key="m.id">
              <tr :class="mi % 2 === 0 ? 'bg-white' : 'bg-red-50'">
                <td class="px-2 py-1 font-mono text-slate-500 border-r border-slate-100 max-w-xs truncate"
                    x-text="JSON.stringify(m.key_values)"></td>
                <td class="px-2 py-1 font-semibold border-r border-slate-100" x-text="m.column_name"></td>
                <!-- Source value with char-level diff highlight -->
                <td class="px-2 py-1 text-emerald-700 border-r border-slate-100">
                  <span x-html="renderSrc(m.source_value, m.target_value)"
                        :class="expandedCell[m.id+'_src'] ? '' : 'diff-val-truncated'"></span>
                  <button x-show="(String(m.source_value||'')).length > 60 && !expandedCell[m.id+'_src']"
                          @click="expandedCell = {...expandedCell, [m.id+'_src']: true}"
                          class="diff-expand-btn">…more</button>
                </td>
                <!-- Target value with char-level diff highlight -->
                <td class="px-2 py-1 text-red-700 border-r border-slate-100">
                  <span x-html="renderTgt(m.source_value, m.target_value)"
                        :class="expandedCell[m.id+'_tgt'] ? '' : 'diff-val-truncated'"></span>
                  <button x-show="(String(m.target_value||'')).length > 60 && !expandedCell[m.id+'_tgt']"
                          @click="expandedCell = {...expandedCell, [m.id+'_tgt']: true}"
                          class="diff-expand-btn">…more</button>
                </td>
                <td class="px-2 py-1 text-slate-500" x-text="m.mismatch_type"></td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>
  </template>
  <template x-if="!sqlExpandedDiffs[r.query_name]?.loading && !sqlExpandedDiffs[r.query_name]?.data?.length && !sqlExpandedDiffs[r.query_name]?.error">
    <p class="text-xs text-slate-500 px-3 py-2">No row-level diff details available.</p>
  </template>
</div>
```

- [ ] **Step 3.3: Test the SQL compare diff panel**

1. Run the app.
2. Go to Compare tab → SQL Compare.
3. Configure two DB configs with a SQL query and click Compare.
4. Wait for results. Click the expand toggle on a "Differs" row.
5. Verify: column summary bars appear (if multiple columns differ), filter selects populate, diff table shows.
6. Change a filter — table should update immediately.
7. If source or target values contain changed characters, they should be highlighted (red strikethrough on source, green on target).

- [ ] **Step 3.4: Commit**

```bash
git add frontend/index.html
git commit -m "feat(diff): add column summary, filter bar, and char diff to SQL compare panel"
```

---

## Task 4: Add file compare enhancements to `index.html`

The file compare result section uses `fileExpandedDiffs` (keyed by `r.query_name`). The pattern is identical to Task 3.

**Files:**
- Modify: `frontend/index.html`

- [ ] **Step 4.1: Locate the file compare diff table**

Search for this string in `frontend/index.html`:

```
<template x-for="(m, mi) in fileExpandedDiffs[r.query_name].data" :key="m.id">
```

- [ ] **Step 4.2: Replace the file compare expanded diff section**

Find the parent `<div>` that wraps this `<template x-for>` (it will contain the overflow-x-auto table). Replace it with the following (same structure as Task 3.2 but using `fileDiffFilter`):

```html
<div>
  <!-- Column summary mini-chart -->
  <div class="col-summary" x-show="colSummary(fileExpandedDiffs[r.query_name]?.data).length > 1">
    <div class="col-summary-label">Mismatches by column</div>
    <template x-for="item in colSummary(fileExpandedDiffs[r.query_name]?.data)" :key="item.col">
      <div class="col-bar-row"
           @click="(fileDiffFilter[r.query_name] ??= {type:'',col:'',search:''}).col = item.col">
        <span class="col-bar-name" x-text="item.col"></span>
        <div class="col-bar-track">
          <div class="col-bar-fill" :style="`width:${item.pct}%`"></div>
        </div>
        <span class="col-bar-count" x-text="item.count"></span>
      </div>
    </template>
  </div>
  <!-- Filter bar -->
  <div class="diff-filter-bar" x-show="(fileExpandedDiffs[r.query_name]?.data?.length || 0) > 1">
    <select x-model="(fileDiffFilter[r.query_name] ??= {type:'',col:'',search:''}).type">
      <option value="">All types</option>
      <option value="value_mismatch">Value diff</option>
      <option value="missing_in_target">Missing →</option>
      <option value="missing_in_source">Missing ←</option>
    </select>
    <select x-model="(fileDiffFilter[r.query_name] ??= {type:'',col:'',search:''}).col">
      <option value="">All columns</option>
      <template x-for="item in colSummary(fileExpandedDiffs[r.query_name]?.data)" :key="item.col">
        <option :value="item.col" x-text="item.col"></option>
      </template>
    </select>
    <input type="text"
           x-model="(fileDiffFilter[r.query_name] ??= {type:'',col:'',search:''}).search"
           placeholder="Search key values…"
           class="diff-filter-search">
    <span class="diff-filter-count"
          x-text="filteredDiff(fileExpandedDiffs[r.query_name]?.data||[], r.query_name, fileDiffFilter).length + ' shown'">
    </span>
  </div>
  <!-- Diff table -->
  <div class="overflow-x-auto max-h-64">
    <table class="text-xs w-full">
      <thead class="bg-slate-50 sticky top-0">
        <tr>
          <th class="px-2 py-1 text-left font-medium text-slate-600 border-r border-slate-100">Key</th>
          <th class="px-2 py-1 text-left font-medium text-slate-600 border-r border-slate-100">Column</th>
          <th class="px-2 py-1 text-left font-medium text-slate-600 border-r border-slate-100">Source</th>
          <th class="px-2 py-1 text-left font-medium text-slate-600 border-r border-slate-100">Target</th>
          <th class="px-2 py-1 text-left font-medium text-slate-600">Type</th>
        </tr>
      </thead>
      <tbody>
        <template x-for="(m, mi) in filteredDiff(fileExpandedDiffs[r.query_name]?.data||[], r.query_name, fileDiffFilter)" :key="m.id">
          <tr :class="mi % 2 === 0 ? 'bg-white' : 'bg-red-50'">
            <td class="px-2 py-1 font-mono text-slate-500 border-r border-slate-100 max-w-xs truncate"
                x-text="JSON.stringify(m.key_values)"></td>
            <td class="px-2 py-1 font-semibold border-r border-slate-100" x-text="m.column_name"></td>
            <td class="px-2 py-1 text-emerald-700 border-r border-slate-100">
              <span x-html="renderSrc(m.source_value, m.target_value)"
                    :class="expandedCell[m.id+'_src'] ? '' : 'diff-val-truncated'"></span>
              <button x-show="(String(m.source_value||'')).length > 60 && !expandedCell[m.id+'_src']"
                      @click="expandedCell = {...expandedCell, [m.id+'_src']: true}"
                      class="diff-expand-btn">…more</button>
            </td>
            <td class="px-2 py-1 text-red-700 border-r border-slate-100">
              <span x-html="renderTgt(m.source_value, m.target_value)"
                    :class="expandedCell[m.id+'_tgt'] ? '' : 'diff-val-truncated'"></span>
              <button x-show="(String(m.target_value||'')).length > 60 && !expandedCell[m.id+'_tgt']"
                      @click="expandedCell = {...expandedCell, [m.id+'_tgt']: true}"
                      class="diff-expand-btn">…more</button>
            </td>
            <td class="px-2 py-1 text-slate-500" x-text="m.mismatch_type"></td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</div>
```

- [ ] **Step 4.3: Commit**

```bash
git add frontend/index.html
git commit -m "feat(diff): add column summary, filter bar, and char diff to file compare panel"
```

---

## Task 5: HTML report — summary dashboard + CSS redesign (section 2a)

**Files:**
- Modify: `etl_framework/reporting/templates/report.html.j2`

The report uses `autoescape=True` in Jinja2. All `{{ }}` expressions are auto-escaped. JavaScript code embedded in `<script>` tags is **not** auto-escaped (only `{{ }}` blocks are). Jinja variables used inside JS strings must use `| tojson` to produce safe JS literals.

- [ ] **Step 5.1: Replace the summary section**

In `report.html.j2`, find and replace the entire `<div id="summary">` block (lines 148–156):

```html
    <div id="summary">
      <h2>Summary</h2>
      <p>
        <strong>Total Tests:</strong> {{ suite.test_cases | length }} &nbsp;|&nbsp;
        <strong>Passed:</strong> <span style="color: #86efac; font-weight: bold;">{{ suite.total_passed }}</span> &nbsp;|&nbsp;
        <strong>Failed/Error:</strong> <span style="color: #fda4af; font-weight: bold;">{{ suite.total_failed }}</span> &nbsp;|&nbsp;
        <strong>Skipped:</strong> {{ suite.total_skipped }}
      </p>
    </div>
```

Replace with:

```html
    <div id="summary">
      <h2>Summary</h2>
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px">
        <div style="flex:1;min-width:120px;background:rgba(52,211,153,0.12);border:1px solid rgba(52,211,153,0.3);border-radius:8px;padding:12px 16px">
          <div style="font-size:1.8em;font-weight:700;color:#34d399">{{ suite.total_passed }}</div>
          <div style="font-size:0.8em;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em">Passed</div>
        </div>
        <div style="flex:1;min-width:120px;background:rgba(251,113,133,0.12);border:1px solid rgba(251,113,133,0.3);border-radius:8px;padding:12px 16px">
          <div style="font-size:1.8em;font-weight:700;color:#fb7185">{{ suite.total_failed }}</div>
          <div style="font-size:0.8em;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em">Failed</div>
        </div>
        <div style="flex:1;min-width:120px;background:rgba(251,191,36,0.12);border:1px solid rgba(251,191,36,0.3);border-radius:8px;padding:12px 16px">
          <div id="stat-total-mm" style="font-size:1.8em;font-weight:700;color:#fbbf24">…</div>
          <div style="font-size:0.8em;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em">Mismatches</div>
        </div>
        <div style="flex:1;min-width:120px;background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.3);border-radius:8px;padding:12px 16px">
          <div id="stat-duration" style="font-size:1.8em;font-weight:700;color:#60a5fa">…</div>
          <div style="font-size:0.8em;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em">Total Duration</div>
        </div>
      </div>
      {% if suite.test_cases | length > 0 %}
      <div style="background:rgba(255,255,255,0.06);border-radius:6px;overflow:hidden;height:10px;margin-bottom:6px">
        <div style="height:100%;background:linear-gradient(90deg,#34d399,#22d3ee);width:{{ (suite.total_passed / (suite.test_cases | length) * 100) | round | int }}%;transition:width 0.5s ease"></div>
      </div>
      <div style="font-size:0.8em;color:var(--muted)">{{ (suite.total_passed / (suite.test_cases | length) * 100) | round | int }}% pass rate</div>
      {% endif %}
      <hr>
    </div>
```

- [ ] **Step 5.2: Add `<style>` additions for report-specific classes**

In `report.html.j2`, find the closing `</style>` tag (around line 122). Insert the following block **before** `</style>`:

```css
    /* Summary stats filled by JS */
    #stat-total-mm, #stat-duration { transition: opacity 0.3s; }

    /* Analytics section */
    .analytics-row { display:flex; gap:24px; flex-wrap:wrap; margin-bottom:20px; }
    .analytics-panel { flex:1; min-width:260px; background:rgba(255,255,255,0.04); border:1px solid var(--line); border-radius:8px; padding:14px; }
    .analytics-title { font-size:0.75em; text-transform:uppercase; color:var(--muted); letter-spacing:0.05em; margin-bottom:10px; }

    /* Filter toolbar */
    #filter-toolbar { position:sticky; top:0; z-index:10; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin-bottom:16px; display:flex; gap:10px; flex-wrap:wrap; align-items:center; box-shadow:0 4px 12px rgba(0,0,0,0.25); }
    #filter-toolbar select, #filter-search { background:var(--panel-2); border:1px solid var(--line); color:var(--text); border-radius:6px; padding:5px 9px; font-size:0.85em; }
    #filter-toolbar select:focus, #filter-search:focus { outline:none; border-color:var(--cyan); }
    #filter-count { margin-left:auto; font-size:0.8em; color:var(--muted); }
    .filter-clear-btn { background:rgba(255,255,255,0.07); border:1px solid var(--line); color:var(--soft); padding:4px 10px; border-radius:6px; cursor:pointer; font-size:0.82em; }
    .filter-clear-btn:hover { background:rgba(255,255,255,0.12); }

    /* Mismatch diff panels */
    .diff-values-cell { padding:6px 8px !important; }
    .diff-panels { display:flex; gap:8px; }
    .diff-panel { flex:1; min-width:0; background:rgba(255,255,255,0.04); border-radius:6px; padding:8px 10px; position:relative; }
    .diff-panel-src { border-left:3px solid var(--rose); }
    .diff-panel-tgt { border-left:3px solid var(--emerald); }
    .diff-panel-label { display:block; font-size:0.7em; text-transform:uppercase; letter-spacing:0.05em; color:var(--muted); margin-bottom:4px; }
    .diff-panel-val { font-family:monospace; font-size:0.88em; word-break:break-all; color:var(--soft); }
    .diff-del { background:rgba(251,113,133,0.30); color:#fda4af; border-radius:2px; padding:0 1px; }
    .diff-ins { background:rgba(52,211,153,0.28); color:#86efac; border-radius:2px; padding:0 1px; }
    .null-val { color:var(--muted); font-style:italic; }
    .copy-btn { position:absolute; top:6px; right:6px; background:none; border:none; color:var(--muted); cursor:pointer; font-size:0.85em; opacity:0; transition:opacity 0.15s; }
    .diff-panel:hover .copy-btn { opacity:1; }

    /* Navigation pill */
    #nav-pill { position:fixed; bottom:24px; right:24px; display:flex; align-items:center; gap:8px; background:var(--panel-2); border:1px solid var(--line); border-radius:999px; padding:8px 16px; box-shadow:0 4px 20px rgba(0,0,0,0.4); z-index:100; }
    .nav-btn { background:rgba(255,255,255,0.08); border:1px solid var(--line); color:var(--soft); padding:4px 12px; border-radius:999px; cursor:pointer; font-size:0.82em; }
    .nav-btn:hover { background:rgba(34,211,238,0.15); color:var(--cyan); }
    #nav-pos { color:var(--muted); font-size:0.8em; min-width:50px; text-align:center; }

    /* Row highlight on nav */
    @keyframes nav-pulse { 0%,100%{background:transparent} 40%{background:rgba(34,211,238,0.15)} }
    .nav-active { animation: nav-pulse 1s ease; }

    /* Expand/collapse buttons */
    .expand-all-btn { background:rgba(255,255,255,0.07); border:1px solid var(--line); color:var(--soft); padding:4px 12px; border-radius:6px; cursor:pointer; font-size:0.82em; margin-right:6px; }
    .expand-all-btn:hover { background:rgba(255,255,255,0.12); }
```

- [ ] **Step 5.3: Commit partial progress**

```bash
git add etl_framework/reporting/templates/report.html.j2
git commit -m "feat(report): redesign summary dashboard with stat cards and progress bar"
```

---

## Task 6: HTML report — analytics panels (column heatmap + donut chart)

**Files:**
- Modify: `etl_framework/reporting/templates/report.html.j2`

- [ ] **Step 6.1: Add analytics placeholder section**

Find `<div id="recon">` in `report.html.j2` (around line 158 after the summary). Insert the following **before** `<div id="recon">`:

```html
    <div class="analytics-row" id="analytics-row">
      <div class="analytics-panel" id="col-heatmap-panel">
        <div class="analytics-title">Top Columns by Mismatch Count</div>
        <div id="col-heatmap"><span style="color:var(--muted);font-size:0.85em">No mismatches recorded.</span></div>
      </div>
      <div class="analytics-panel" id="type-donut-panel">
        <div class="analytics-title">Mismatch Type Breakdown</div>
        <div id="type-donut"><span style="color:var(--muted);font-size:0.85em">No mismatches recorded.</span></div>
      </div>
    </div>
```

- [ ] **Step 6.2: Commit**

```bash
git add etl_framework/reporting/templates/report.html.j2
git commit -m "feat(report): add analytics panel placeholders for heatmap and donut chart"
```

---

## Task 7: HTML report — mismatch row restructure with diff panels (section 2e)

**Files:**
- Modify: `etl_framework/reporting/templates/report.html.j2`

- [ ] **Step 7.1: Replace the mismatch table header**

Find the mismatch `<details>` block's table header (in the `<div id="mismatches">` section):

```html
              <thead>
                <tr>
                  <th>Mismatch Type</th>
                  <th>Column</th>
                  <th>Row Key Values</th>
                  <th>Source Value</th>
                  <th>Target Value</th>
                </tr>
              </thead>
```

Replace with:

```html
              <thead>
                <tr>
                  <th>Mismatch Type</th>
                  <th>Column</th>
                  <th>Row Key Values</th>
                  <th colspan="2">Values ({{ suite.source_env }} → {{ suite.target_env }})</th>
                </tr>
              </thead>
```

- [ ] **Step 7.2: Replace the mismatch row `<tr>` block**

Find the mismatch row block (inside `{% for mm in result.mismatches %}`):

```html
                {% for mm in result.mismatches %}
                <tr>
                  <td><span class="badge badge-gray">{{ mm.mismatch_type }}</span></td>
                  <td>{{ mm.column_name }}</td>
                  <td style="font-family: monospace; font-size: 0.85em;">{{ mm.key_values }}</td>
                  <td style="color: #fda4af;">{{ mm.source_value }}</td>
                  <td style="color: #86efac;">{{ mm.target_value }}</td>
                </tr>
                {% if mm.accepted %}
                <tr style="background: rgba(52,211,153,0.12);">
                  <td colspan="5" class="accepted-note">
                    ✓ Accepted{% if mm.accepted_by %} by {{ mm.accepted_by }}{% endif %}{% if mm.accepted_at %} on {{ mm.accepted_at.strftime('%Y-%m-%d %H:%M') }}{% endif %}{% if mm.accepted_note %} — {{ mm.accepted_note }}{% endif %}
                  </td>
                </tr>
                {% endif %}
                {% endfor %}
```

Replace with:

```html
                {% for mm in result.mismatches %}
                <tr data-mismatch
                    data-test="{{ result.query_name }}"
                    data-column="{{ mm.column_name }}"
                    data-type="{{ mm.mismatch_type }}"
                    data-key="{{ mm.key_values | tojson }}"
                    data-src="{{ mm.source_value if mm.source_value is not none else '' }}"
                    data-tgt="{{ mm.target_value if mm.target_value is not none else '' }}">
                  <td><span class="badge {% if mm.mismatch_type == 'value_mismatch' %}badge-amber{% elif mm.mismatch_type == 'missing_in_target' %}badge-gray{% else %}badge-gray{% endif %}">{{ mm.mismatch_type }}</span></td>
                  <td>{{ mm.column_name }}</td>
                  <td style="font-family: monospace; font-size: 0.85em;">{{ mm.key_values | tojson }}</td>
                  <td class="diff-values-cell" colspan="2">
                    <div class="diff-panels">
                      <div class="diff-panel diff-panel-src">
                        <span class="diff-panel-label">{{ suite.source_env }}</span>
                        <span class="diff-panel-val" data-role="src-diff"
                              data-raw="{{ mm.source_value if mm.source_value is not none else '' }}">{{ mm.source_value if mm.source_value is not none else 'NULL' }}</span>
                        <button class="copy-btn" onclick="copyVal(this, this.closest('tr').dataset.src)" title="Copy source value">⎘</button>
                      </div>
                      <div class="diff-panel diff-panel-tgt">
                        <span class="diff-panel-label">{{ suite.target_env }}</span>
                        <span class="diff-panel-val" data-role="tgt-diff"
                              data-raw="{{ mm.target_value if mm.target_value is not none else '' }}">{{ mm.target_value if mm.target_value is not none else 'NULL' }}</span>
                        <button class="copy-btn" onclick="copyVal(this, this.closest('tr').dataset.tgt)" title="Copy target value">⎘</button>
                      </div>
                    </div>
                  </td>
                </tr>
                {% if mm.accepted %}
                <tr style="background: rgba(52,211,153,0.12);">
                  <td colspan="4" class="accepted-note">
                    ✓ Accepted{% if mm.accepted_by %} by {{ mm.accepted_by }}{% endif %}{% if mm.accepted_at %} on {{ mm.accepted_at.strftime('%Y-%m-%d %H:%M') }}{% endif %}{% if mm.accepted_note %} — {{ mm.accepted_note }}{% endif %}
                  </td>
                </tr>
                {% endif %}
                {% endfor %}
```

- [ ] **Step 7.3: Add "Expand All / Collapse All" buttons above mismatches**

Find `<div id="mismatches">`. Replace it with:

```html
    <div id="mismatches-header" style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
      <h2 style="margin:0">Mismatch Details</h2>
      <button class="expand-all-btn" onclick="setAllDetails(true)">Expand All</button>
      <button class="expand-all-btn" onclick="setAllDetails(false)">Collapse All</button>
    </div>
    <div id="mismatches">
```

(Close the new `<div>` properly — you're wrapping the mismatches section, so make sure the closing `</div>` for `<div id="mismatches">` remains after the `{% endfor %}` loop.)

- [ ] **Step 7.4: Commit**

```bash
git add etl_framework/reporting/templates/report.html.j2
git commit -m "feat(report): restructure mismatch rows with diff panels and data attributes"
```

---

## Task 8: HTML report — filter toolbar (section 2d)

**Files:**
- Modify: `etl_framework/reporting/templates/report.html.j2`

- [ ] **Step 8.1: Add the filter toolbar before the reconciliation table**

Find `<div id="recon">`. Insert the following **immediately after** `<div id="recon">` (before the `<h2>Reconciliation Results</h2>`):

```html
      <div id="filter-toolbar">
        <select id="filter-test" onchange="filterState.test=this.value;applyFilters()">
          <option value="">All tests</option>
          {% for result in suite.reconciliation_results %}
          <option value="{{ result.query_name }}">{{ result.query_name }}</option>
          {% endfor %}
        </select>
        <select id="filter-col" onchange="filterState.col=this.value;applyFilters()">
          <option value="">All columns</option>
        </select>
        <select id="filter-type" onchange="filterState.type=this.value;applyFilters()">
          <option value="">All types</option>
          <option value="value_mismatch">Value diff</option>
          <option value="missing_in_target">Missing →</option>
          <option value="missing_in_source">Missing ←</option>
        </select>
        <input id="filter-search" type="text" placeholder="Search key values… (/ to focus)"
               oninput="filterState.search=this.value;applyFilters()">
        <button class="filter-clear-btn" onclick="clearFilters()">✕ Clear</button>
        <span id="filter-count"></span>
      </div>
```

- [ ] **Step 8.2: Commit**

```bash
git add etl_framework/reporting/templates/report.html.j2
git commit -m "feat(report): add interactive filter toolbar to mismatch section"
```

---

## Task 9: HTML report — JavaScript block (navigation + diff + charts + filter logic)

This is the largest single step. Add the complete `<script>` block at the end of `<body>` in `report.html.j2`.

**Files:**
- Modify: `etl_framework/reporting/templates/report.html.j2`

- [ ] **Step 9.1: Add the navigation pill HTML**

Find `</div>` that closes `<div class="container">` (near the bottom of `<body>`). Insert the navigation pill **after** this closing `</div>` but **before** `</body>`:

```html
  <!-- Navigation pill -->
  <div id="nav-pill">
    <button class="nav-btn" onclick="navTo(-1)">↑ Prev</button>
    <span id="nav-pos">0 / 0</span>
    <button class="nav-btn" onclick="navTo(1)">Next ↓</button>
  </div>
```

- [ ] **Step 9.2: Add the complete `<script>` block**

Immediately after the navigation pill, add:

```html
  <script>
  // ── Helpers ──────────────────────────────────────────────────────────────
  function escHtml(s) {
    return String(s==null?'':s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── Char diff (LCS O(n*m)) ────────────────────────────────────────────────
  function charDiff(a, b) {
    const n=a.length, m=b.length;
    if(n===0) return b.split('').map(c=>({text:c,op:'+'}));
    if(m===0) return a.split('').map(c=>({text:c,op:'-'}));
    const dp=[];
    for(let i=0;i<=n;i++) dp[i]=new Uint16Array(m+1);
    for(let i=1;i<=n;i++) for(let j=1;j<=m;j++)
      dp[i][j]=a[i-1]===b[j-1]?dp[i-1][j-1]+1:Math.max(dp[i-1][j],dp[i][j-1]);
    const ops=[];
    let i=n,j=m;
    while(i>0||j>0){
      if(i>0&&j>0&&a[i-1]===b[j-1]){ops.push({text:a[i-1],op:'='});i--;j--;}
      else if(j>0&&(i===0||dp[i][j-1]>=dp[i-1][j])){ops.push({text:b[j-1],op:'+'});j--;}
      else{ops.push({text:a[i-1],op:'-'});i--;}
    }
    ops.reverse();
    const m2=[];
    for(const {text,op} of ops){if(m2.length&&m2[m2.length-1].op===op)m2[m2.length-1].text+=text;else m2.push({text,op});}
    return m2;
  }

  function renderSrc(a,b){
    if(a==null&&b==null) return '<span class="null-val">NULL</span>';
    if(a==null) return '<span class="null-val">NULL</span>';
    if(b==null) return escHtml(String(a));
    if(!isNaN(a)&&!isNaN(b)&&isFinite(a)&&isFinite(b)) return escHtml(String(a));
    const sa=String(a),sb=String(b);
    if(sa.length>500||sb.length>500) return escHtml(sa.slice(0,500))+'…';
    return charDiff(sa,sb).map(({text,op})=>
      op==='-'?`<span class="diff-del">${escHtml(text)}</span>`:op==='='?escHtml(text):''
    ).join('');
  }

  function renderTgt(a,b){
    if(a==null&&b==null) return '<span class="null-val">NULL</span>';
    if(b==null) return '<span class="null-val">NULL</span>';
    if(a==null) return escHtml(String(b));
    if(!isNaN(a)&&!isNaN(b)&&isFinite(a)&&isFinite(b)) return escHtml(String(b));
    const sa=String(a),sb=String(b);
    if(sa.length>500||sb.length>500) return escHtml(sb.slice(0,500))+'…';
    return charDiff(sa,sb).map(({text,op})=>
      op==='+'?`<span class="diff-ins">${escHtml(text)}</span>`:op==='='?escHtml(text):''
    ).join('');
  }

  // ── Apply diff to rendered mismatch rows ─────────────────────────────────
  function applyDiff() {
    document.querySelectorAll('tr[data-mismatch]').forEach(tr => {
      const src = tr.dataset.src, tgt = tr.dataset.tgt;
      tr.querySelectorAll('[data-role="src-diff"]').forEach(el => { el.innerHTML = renderSrc(src,tgt); });
      tr.querySelectorAll('[data-role="tgt-diff"]').forEach(el => { el.innerHTML = renderTgt(src,tgt); });
    });
  }

  // ── Filter ────────────────────────────────────────────────────────────────
  const filterState = {test:'',col:'',type:'',search:''};

  function applyFilters() {
    const rows = document.querySelectorAll('tr[data-mismatch]');
    let shown=0;
    rows.forEach(tr => {
      const ok = (
        (!filterState.test   || tr.dataset.test===filterState.test) &&
        (!filterState.col    || tr.dataset.column===filterState.col) &&
        (!filterState.type   || tr.dataset.type===filterState.type) &&
        (!filterState.search || tr.dataset.key.toLowerCase().includes(filterState.search.toLowerCase()))
      );
      tr.style.display = ok ? '' : 'none';
      if(ok) shown++;
    });
    const count = document.getElementById('filter-count');
    if(count) count.textContent = shown + ' of ' + rows.length + ' mismatches';
    navIndex=-1; buildNavList();
  }

  function clearFilters() {
    filterState.test=filterState.col=filterState.type=filterState.search='';
    ['filter-test','filter-col','filter-type'].forEach(id=>{const el=document.getElementById(id);if(el)el.value='';});
    const fs=document.getElementById('filter-search'); if(fs) fs.value='';
    applyFilters();
  }

  function populateColFilter() {
    const cols = new Set();
    document.querySelectorAll('tr[data-mismatch]').forEach(tr=>cols.add(tr.dataset.column));
    const sel=document.getElementById('filter-col'); if(!sel) return;
    [...cols].filter(Boolean).sort().forEach(col=>{
      const opt=document.createElement('option'); opt.value=col; opt.textContent=col; sel.appendChild(opt);
    });
  }

  // ── Navigation ────────────────────────────────────────────────────────────
  let navList=[], navIndex=-1;

  function buildNavList(){
    navList=[...document.querySelectorAll('tr[data-mismatch]')].filter(tr=>tr.style.display!=='none');
    const pos=document.getElementById('nav-pos');
    if(pos) pos.textContent=(navIndex<0?0:navIndex+1)+' / '+navList.length;
  }

  function navTo(delta){
    if(!navList.length) buildNavList();
    const prev=navIndex;
    navIndex=Math.max(0,Math.min(navList.length-1, navIndex+delta));
    if(prev>=0&&navList[prev]) navList[prev].classList.remove('nav-active');
    if(navList[navIndex]){
      navList[navIndex].scrollIntoView({behavior:'smooth',block:'center'});
      navList[navIndex].classList.add('nav-active');
      // Open parent <details> if collapsed
      const det=navList[navIndex].closest('details');
      if(det&&!det.open) det.open=true;
    }
    const pos=document.getElementById('nav-pos');
    if(pos) pos.textContent=(navIndex+1)+' / '+navList.length;
  }

  // ── Expand / Collapse all ─────────────────────────────────────────────────
  function setAllDetails(open){
    document.querySelectorAll('#mismatches details').forEach(d=>d.open=open);
  }

  // ── Copy to clipboard ─────────────────────────────────────────────────────
  function copyVal(btn, val){
    navigator.clipboard.writeText(val||'').then(()=>{
      const orig=btn.textContent; btn.textContent='✓';
      setTimeout(()=>btn.textContent=orig, 1200);
    }).catch(()=>{});
  }

  // ── Column heat map ──────────────────────────────────────────────────────
  function buildHeatmap(){
    const counts={};
    document.querySelectorAll('tr[data-mismatch]').forEach(tr=>{
      const c=tr.dataset.column||'(none)';
      counts[c]=(counts[c]||0)+1;
    });
    const sorted=Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,10);
    if(!sorted.length) return;
    const maxC=sorted[0][1];
    const barH=20,gap=5,labelW=130,trackW=180,numW=28,svgW=labelW+trackW+numW+16;
    const svgH=sorted.length*(barH+gap);
    const bars=sorted.map(([col,count],i)=>{
      const y=i*(barH+gap);
      const fw=Math.round(count/maxC*trackW);
      const ce=escHtml(col);
      const cs=col.replace(/'/g,"\\'");
      return `<g style="cursor:pointer" onclick="filterByCol('${cs}')">
        <text x="${labelW-4}" y="${y+barH*0.72}" text-anchor="end" font-size="12" fill="#c7d0dc">${ce}</text>
        <rect x="${labelW}" y="${y}" width="${trackW}" height="${barH}" fill="rgba(255,255,255,0.06)" rx="3"/>
        <rect x="${labelW}" y="${y}" width="${fw}" height="${barH}" fill="#fb7185" rx="3"/>
        <text x="${labelW+trackW+4}" y="${y+barH*0.72}" font-size="12" fill="#8491a3">${count}</text>
      </g>`;
    }).join('');
    const container=document.getElementById('col-heatmap');
    if(container) container.innerHTML=`<svg width="${svgW}" height="${svgH}" xmlns="http://www.w3.org/2000/svg">${bars}</svg>`;
    document.getElementById('stat-total-mm').textContent=Object.values(counts).reduce((a,b)=>a+b,0);
  }

  function filterByCol(col){
    const sel=document.getElementById('filter-col'); if(sel) sel.value=col;
    filterState.col=col; applyFilters();
  }

  // ── Mismatch type donut ───────────────────────────────────────────────────
  function buildDonut(){
    const counts={value_mismatch:0,missing_in_target:0,missing_in_source:0};
    document.querySelectorAll('tr[data-mismatch]').forEach(tr=>{
      const t=tr.dataset.type; if(t in counts) counts[t]++; else counts.value_mismatch++;
    });
    const total=Object.values(counts).reduce((a,b)=>a+b,0);
    if(!total) return;
    const colors={value_mismatch:'#fbbf24',missing_in_target:'#38bdf8',missing_in_source:'#a78bfa'};
    const labels={value_mismatch:'Value diff',missing_in_target:'Missing →',missing_in_source:'Missing ←'};
    const cx=80,cy=80,r=60,ir=36;
    let angle=-Math.PI/2, paths='';
    for(const [key,count] of Object.entries(counts)){
      if(!count) continue;
      const slice=(count/total)*2*Math.PI, end=angle+slice;
      const x1=cx+r*Math.cos(angle),y1=cy+r*Math.sin(angle);
      const x2=cx+r*Math.cos(end),y2=cy+r*Math.sin(end);
      const ix1=cx+ir*Math.cos(angle),iy1=cy+ir*Math.sin(angle);
      const ix2=cx+ir*Math.cos(end),iy2=cy+ir*Math.sin(end);
      const lg=slice>Math.PI?1:0;
      paths+=`<path d="M${ix1},${iy1}L${x1},${y1}A${r},${r} 0 ${lg},1 ${x2},${y2}L${ix2},${iy2}A${ir},${ir} 0 ${lg},0 ${ix1},${iy1}" fill="${colors[key]}" opacity="0.85"/>`;
      angle=end;
    }
    const legend=Object.entries(counts).filter(([,c])=>c>0).map(([key,count])=>
      `<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
        <span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${colors[key]}"></span>
        <span style="font-size:12px;color:#c7d0dc">${labels[key]}: <strong>${count}</strong> (${Math.round(count/total*100)}%)</span>
      </div>`
    ).join('');
    const html=`<div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
      <svg width="160" height="160" xmlns="http://www.w3.org/2000/svg">
        ${paths}
        <text x="${cx}" y="${cy-6}" text-anchor="middle" font-size="22" font-weight="bold" fill="#f4f7fb">${total}</text>
        <text x="${cx}" y="${cy+14}" text-anchor="middle" font-size="11" fill="#8491a3">mismatches</text>
      </svg>
      <div>${legend}</div>
    </div>`;
    const container=document.getElementById('type-donut');
    if(container) container.innerHTML=html;
  }

  // ── Duration stat ─────────────────────────────────────────────────────────
  function fillDuration(){
    // Reads duration from the reconciliation table cells (column index 3 = "Duration")
    let total=0;
    document.querySelectorAll('#recon tbody tr td:nth-child(3)').forEach(td=>{
      const match=td.textContent.match(/[\d.]+/);
      if(match) total+=parseFloat(match[0]);
    });
    const el=document.getElementById('stat-duration');
    if(el) el.textContent = total>0 ? total.toFixed(1)+'s' : '—';
  }

  // ── Keyboard shortcuts ────────────────────────────────────────────────────
  document.addEventListener('keydown', e=>{
    if(e.target.tagName==='INPUT'||e.target.tagName==='SELECT') return;
    if(e.key==='n'||e.key==='ArrowDown'){e.preventDefault();navTo(1);}
    if(e.key==='p'||e.key==='ArrowUp'){e.preventDefault();navTo(-1);}
    if(e.key==='/'){e.preventDefault();const fs=document.getElementById('filter-search');if(fs)fs.focus();}
    if(e.key==='Escape') clearFilters();
  });

  // ── Initialise on DOM ready ───────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', ()=>{
    populateColFilter();
    applyDiff();
    buildHeatmap();
    buildDonut();
    fillDuration();
    buildNavList();
    // initialise filter count label
    const rows=document.querySelectorAll('tr[data-mismatch]').length;
    const fc=document.getElementById('filter-count');
    if(fc) fc.textContent=rows+' of '+rows+' mismatches';
  });
  </script>
```

- [ ] **Step 9.3: Generate a test report and open it in a browser**

Run the ETL test suite to generate a fresh report (or use an existing one in `reports/`):

```bash
# Open any existing report
start reports/report_*.html
```

Verify:
1. Stat cards show Passed, Failed, and a loading `…` for Mismatches (should fill to a number after JS runs).
2. Column heat map and donut chart appear if the report has mismatches.
3. Filter toolbar is visible and sticky.
4. Mismatch rows show two-panel diff layout with copy buttons.
5. Character changes in string values are highlighted (red = deleted chars, green = inserted chars).
6. Prev/Next pill is in bottom-right corner.
7. `n` / `p` keyboard keys navigate between mismatches.
8. `/` focuses the search input.

- [ ] **Step 9.4: Commit**

```bash
git add etl_framework/reporting/templates/report.html.j2
git commit -m "feat(report): add JS block — charDiff, filter, heatmap, donut, navigation, copy"
```

---

## Task 10: Add a Python smoke test for the HTML report template

**Files:**
- Create: `tests/unit/test_report_template.py`

- [ ] **Step 10.1: Write the test**

Create `tests/unit/test_report_template.py`:

```python
"""Smoke tests for report.html.j2 — verifies the template renders and includes
key HTML landmarks introduced by the enhanced diff display feature."""
import types
from datetime import datetime, timezone

from etl_framework.reporting.generator import ReportGenerator


def _make_suite(mismatches=None):
    """Build a minimal fake suite object for template rendering."""
    mm_list = mismatches or []

    result = types.SimpleNamespace(
        query_name="orders_recon",
        status="FAILED",
        duration_seconds=1.23,
        source_row_count=100,
        target_row_count=98,
        total_issues=len(mm_list),
        value_mismatch_count=sum(1 for m in mm_list if m.mismatch_type == "value_mismatch"),
        missing_in_target_count=sum(1 for m in mm_list if m.mismatch_type == "missing_in_target"),
        missing_in_source_count=sum(1 for m in mm_list if m.mismatch_type == "missing_in_source"),
        mismatches=mm_list,
        schema_diff=None,
        effective_status="FAILED",
        override_status=None,
    )

    suite = types.SimpleNamespace(
        run_id="test-run-001",
        started_at=datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc),
        source_env="dev",
        target_env="prod",
        test_cases=[result],
        reconciliation_results=[result],
        total_passed=0,
        total_failed=1,
        total_skipped=0,
    )
    return suite


def _make_mm(col, src, tgt, mm_type="value_mismatch"):
    return types.SimpleNamespace(
        column_name=col,
        source_value=src,
        target_value=tgt,
        mismatch_type=mm_type,
        key_values={"id": 1},
        accepted=False,
        accepted_by=None,
        accepted_at=None,
        accepted_note=None,
    )


def _render(suite, tmp_path):
    gen = ReportGenerator(output_dir=str(tmp_path))
    path = gen.generate(suite)
    return open(path, encoding="utf-8").read()


class TestReportTemplateSmoke:
    def test_renders_without_error(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        assert "ETL Framework Execution Report" in html

    def test_stat_cards_present(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        assert "stat-total-mm" in html
        assert "stat-duration" in html
        assert "nav-pill" in html

    def test_analytics_placeholders_present(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        assert 'id="col-heatmap"' in html
        assert 'id="type-donut"' in html

    def test_filter_toolbar_present(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        assert 'id="filter-toolbar"' in html
        assert 'id="filter-search"' in html

    def test_mismatch_row_data_attributes(self, tmp_path):
        mm = _make_mm("amount", "100.00", "100.01")
        html = _render(_make_suite([mm]), tmp_path)
        assert 'data-mismatch' in html
        assert 'data-column="amount"' in html
        assert 'data-type="value_mismatch"' in html
        assert 'data-role="src-diff"' in html
        assert 'data-role="tgt-diff"' in html

    def test_diff_panels_present_for_mismatches(self, tmp_path):
        mm = _make_mm("status", "active", "inactive")
        html = _render(_make_suite([mm]), tmp_path)
        assert "diff-panel-src" in html
        assert "diff-panel-tgt" in html
        assert "copy-btn" in html

    def test_js_block_present(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        assert "charDiff" in html
        assert "renderSrc" in html
        assert "applyDiff" in html
        assert "buildHeatmap" in html
        assert "buildDonut" in html
```

- [ ] **Step 10.2: Run the tests**

```bash
python -m pytest tests/unit/test_report_template.py -v
```

Expected output:
```
tests/unit/test_report_template.py::TestReportTemplateSmoke::test_renders_without_error PASSED
tests/unit/test_report_template.py::TestReportTemplateSmoke::test_stat_cards_present PASSED
tests/unit/test_report_template.py::TestReportTemplateSmoke::test_analytics_placeholders_present PASSED
tests/unit/test_report_template.py::TestReportTemplateSmoke::test_filter_toolbar_present PASSED
tests/unit/test_report_template.py::TestReportTemplateSmoke::test_mismatch_row_data_attributes PASSED
tests/unit/test_report_template.py::TestReportTemplateSmoke::test_diff_panels_present_for_mismatches PASSED
tests/unit/test_report_template.py::TestReportTemplateSmoke::test_js_block_present PASSED
```

If any test fails, compare the expected HTML landmark strings with what was actually written into the template in Tasks 5–9.

- [ ] **Step 10.3: Final commit**

```bash
git add tests/unit/test_report_template.py
git commit -m "test(report): add smoke tests for enhanced diff display template landmarks"
```

---

## Self-Review Checklist

| Spec requirement | Covered by |
|-----------------|------------|
| charDiff / renderSrc / renderTgt utility | Task 2 (app.js), Task 9 (report template) |
| diff-del / diff-ins CSS | Task 1 |
| Summary stat cards + progress bar | Task 5 |
| Column heat map (report) | Task 6 placeholder, Task 9 JS |
| Mismatch type donut (report) | Task 6 placeholder, Task 9 JS |
| Filter toolbar with test/col/type/search | Task 8 HTML, Task 9 JS |
| Char-level diff in mismatch panels | Task 7 HTML structure, Task 9 applyDiff() |
| Navigation prev/next + keyboard n/p/slash/Escape | Task 9 JS |
| Expand all / Collapse all | Task 7 buttons, Task 9 setAllDetails() |
| Copy to clipboard | Task 7 copy-btn, Task 9 copyVal() |
| SQL compare filter bar | Task 3 |
| SQL compare column summary | Task 3 |
| SQL compare char diff (x-html) | Task 3 |
| SQL compare expandable values | Task 3 |
| File compare filter bar | Task 4 |
| File compare column summary | Task 4 |
| File compare char diff (x-html) | Task 4 |
| File compare expandable values | Task 4 |
| styles.css additions | Task 1 |
| No backend changes | ✅ confirmed — zero Python model/API changes |
