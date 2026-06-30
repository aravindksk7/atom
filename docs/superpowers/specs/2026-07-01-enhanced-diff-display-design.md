# Design: Enhanced Diff Display — Better Than BeyondCompare

**Date:** 2026-07-01
**Status:** Approved (autopilot)

---

## Problem

The current comparison surfaces (HTML report + Compare tab UI) show mismatch rows as plain source/target value pairs with no indication of *which characters* changed, no filtering, and no column-level analytics. A user staring at `source="John Smith"` vs `target="John Smyth"` must diff them by eye. BeyondCompare's signature feature is inline character highlighting — users instantly see the single character that differs.

Current gaps vs BeyondCompare:

| Feature | BeyondCompare | Current system |
|---------|--------------|----------------|
| Inline char-level diff highlight | ✅ | ❌ |
| Filter to show only differences | ✅ | ❌ |
| Jump to next / prev diff | ✅ | ❌ |
| Column-level analytics | ❌ | ❌ (we can beat BC here) |
| Mismatch type breakdown chart | ❌ | ❌ (we can beat BC here) |
| Row count visual comparison | ❌ | ❌ (we can beat BC here) |
| Mismatch acceptance workflow | ❌ | ✅ (already ahead of BC) |

---

## Goals

1. **HTML report** (`report.html.j2`): char-level diff, filter toolbar, column heat map, mismatch type chart, navigation, copy-to-clipboard.
2. **Compare tab UI** (`index.html` / `app.js`): char-level diff rendering, filter bar per result, column summary, expandable values.
3. Beat BeyondCompare by adding features it does not have: column heat map, mismatch type analytics, integrated acceptance workflow visible in the report.

---

## Non-Goals

- Server-side diff computation (all diff is client-side JS, zero new API endpoints).
- Char-level diff for purely numeric values (numbers are right or wrong; char diff on `1234567.89` vs `1234568.89` is noise).
- Parquet or other binary file format support.
- Merge / resolve functionality from within the HTML report.
- Changes to the Python reconciliation engine or database models.

---

## Section 1 — Shared Diff Utility (`charDiff` / `renderDiff`)

A pure-JS Myers diff implementation embedded in both the HTML report and `app.js`. No external dependency.

### Algorithm

```javascript
// Myers O(ND) diff — character-level
function charDiff(a, b) {
  // Returns [{text, op}] where op is '=' | '-' | '+'
  // '=' same, '-' in A only (deleted), '+' in B only (inserted)
}

function renderDiff(srcVal, tgtVal) {
  // Returns an HTML string:
  //   <span class="diff-del">removed</span>
  //   <span class="diff-ins">added</span>
  //   same text as plain text nodes
  //
  // Guards:
  //   • Both null/undefined → return '∅'
  //   • Either is null      → return full value highlighted as ins or del
  //   • Both are numbers    → no char diff, just return the value as-is
  //   • Strings longer than 500 chars → truncate diff (show first 500 + ellipsis)
}
```

### CSS for diff spans

```css
/* Shared across report template and styles.css */
.diff-del {
  background: rgba(251, 113, 133, 0.30);   /* rose-400/30 */
  color: #be123c;
  border-radius: 2px;
  padding: 0 1px;
  text-decoration: line-through;
  text-decoration-color: rgba(190, 18, 60, 0.5);
}
.diff-ins {
  background: rgba(52, 211, 153, 0.28);    /* emerald-400/28 */
  color: #065f46;
  border-radius: 2px;
  padding: 0 1px;
}
.null-val {
  color: #94a3b8;
  font-style: italic;
}
```

---

## Section 2 — HTML Report Template (`report.html.j2`)

All changes are to the single Jinja2 template file. No Python backend changes are needed — the data is already in the template context.

### 2a. Summary Dashboard Redesign

**Current:** Plain text `Total: N | Passed: N | Failed: N | Skipped: N`

**New:** Three stat cards + a pass-rate progress bar.

```
┌──────────────────────────────────────────────────────────────────┐
│  ✓ 12 PASSED    ✗ 3 FAILED    ⚑ 47 MISMATCHES    ⏱ 8.3s total  │
│  ████████████████████░░░░   80% pass rate                         │
└──────────────────────────────────────────────────────────────────┘
```

Cards use the existing CSS variables (`--emerald`, `--rose`, `--amber`). The progress bar is a `<div>` with inline `width` computed by Jinja: `{{ (suite.total_passed / suite.test_cases|length * 100)|round|int }}%`.

### 2b. Column Heat Map (new section)

Positioned below the summary, above the reconciliation table.

A horizontal bar chart rendered entirely in client-side JS. On `DOMContentLoaded`, the script walks all mismatch `<tr>` elements, reads `data-column` attributes, counts occurrences, and renders an inline `<svg>` bar chart showing the **top 10 columns by mismatch count**. Bars are colored by mismatch type (rose = value diff, blue = missing in target, purple = missing in source — stacked if mixed).

Clicking a bar filters the mismatch table to that column (integrates with the filter toolbar in 2d).

The SVG is inserted into a `<div id="col-heatmap">` placeholder that exists in the Jinja template.

### 2c. Mismatch Type Breakdown (new section)

An inline donut chart next to the column heat map (two-column flex layout). Counts:
- `value_mismatch` — amber
- `missing_in_target` — sky blue
- `missing_in_source` — violet

Rendered as a pure SVG donut (no Chart.js dependency). Total mismatch count is shown in the center. Legend below with percentages.

### 2d. Filter Toolbar (new)

A sticky bar pinned below the page header (CSS `position: sticky; top: 0; z-index: 10`). Appears above the reconciliation results table.

Controls:
| Control | Type | Populates from |
|---------|------|----------------|
| Test filter | `<select>` | `{{ result.query_name }}` values |
| Column filter | `<select>` | Unique `data-column` values in DOM |
| Type filter | `<select>` | All \| Value Diff \| Missing → \| Missing ← |
| Key search | `<input type="text">` | Live text search on `data-key` |
| Clear button | `<button>` | Resets all filters |

Filter logic: each mismatch `<tr>` gets `data-test`, `data-column`, `data-type`, `data-key` attributes (injected in Jinja). The filter JS function sets `tr.style.display` based on whether all active filters match.

A counter shows "Showing **47** of **127** mismatches" updated on every filter change.

### 2e. Character-level Diff in Mismatch Rows

**Current layout:** Five columns: Mismatch Type | Column | Row Key Values | Source Value | Target Value

**New layout:** Source and target values are rendered in a two-panel side-by-side div inside the "values" cell, using `renderDiff()`:

```html
<td class="diff-values-cell">
  <div class="diff-panels">
    <div class="diff-panel diff-panel-src">
      <span class="diff-panel-label">{{ suite.source_env }}</span>
      <!-- rendered innerHTML by JS via renderSrc() -->
    </div>
    <div class="diff-panel diff-panel-tgt">
      <span class="diff-panel-label">{{ suite.target_env }}</span>
      <!-- rendered innerHTML by JS -->
    </div>
  </div>
</td>
```

The Jinja template writes raw values into `data-src` and `data-tgt` attributes on the `<tr>`. On `DOMContentLoaded`, the JS fills the innerHTML of each panel using `renderDiff()`.

This means the diff rendering is progressive: the page renders instantly with plain values visible in attributes, then the JS applies diff highlighting in one pass.

### 2f. Navigation Buttons

A fixed floating pill in the bottom-right corner (CSS `position: fixed; bottom: 24px; right: 24px`):

```
[ ↑ Prev ]  5 / 47  [ Next ↓ ]
```

The navigation index tracks only **visible** mismatch rows (respects filters). Clicking scrolls to the target row (`element.scrollIntoView({ behavior: 'smooth', block: 'center' })`) and adds a brief yellow pulse animation (`@keyframes pulse-highlight`).

Keyboard shortcuts (registered once at `document` level):
- `n` → next mismatch
- `p` → previous mismatch
- `/` → focus key search input
- `Escape` → clear all filters

### 2g. Expand All / Collapse All

Two buttons added above the mismatches section:
```html
<button onclick="setAllDetails(true)">Expand All</button>
<button onclick="setAllDetails(false)">Collapse All</button>
```
`setAllDetails` toggles `.open` on all `<details>` elements.

### 2h. Copy to Clipboard

Each diff panel gets a `⎘` icon button (top-right corner, appears on hover). Copies the raw value (the `data-src` / `data-tgt` attribute value, not the diff HTML) via `navigator.clipboard.writeText()`. On success, the icon briefly changes to `✓`.

---

## Section 3 — Compare Tab UI

Changes to `frontend/index.html`, `frontend/app.js`, and `frontend/styles.css`.

### 3a. `renderDiff` in `app.js`

Add the same `charDiff` / `renderDiff` utility from Section 1 at the top of `app.js` (before the Alpine component). This is the single copy; the HTML report embeds its own copy.

Two rendering helpers are derived from `charDiff`:

```javascript
// Source cell: show source text; chars that differ from target are red (del style, no strikethrough)
function renderSrc(a, b) { ... }

// Target cell: show target text; chars that differ from source are green (ins style)
function renderTgt(a, b) { ... }
```

Replace `x-text="m.source_value ?? 'NULL'"` on source cells with `x-html="renderSrc(m.source_value, m.target_value)"` and `x-text="m.target_value ?? 'NULL'"` on target cells with `x-html="renderTgt(m.source_value, m.target_value)"`. This preserves the existing two-column layout (source column / target column) while adding inline highlights within each cell — matching BeyondCompare's side-by-side approach.

### 3b. Filter State in `app.js`

Add to the `app()` state object:

```javascript
// SQL compare diff filters (keyed by result query_name)
sqlDiffFilter: {},    // { [name]: { type: '', col: '', search: '' } }
// File compare diff filters (keyed by result query_name)
fileDiffFilter: {},
```

Add a computed method:

```javascript
filteredDiff(diffs, filterKey, filterState) {
  const f = filterState[filterKey] || {};
  return diffs.filter(m => {
    if (f.type && m.mismatch_type !== f.type) return false;
    if (f.col  && m.column_name !== f.col) return false;
    if (f.search) {
      const key = JSON.stringify(m.key_values || {}).toLowerCase();
      if (!key.includes(f.search.toLowerCase())) return false;
    }
    return true;
  });
}
```

### 3c. Filter Bar in `index.html`

Added inside the `x-show="...open"` section for both SQL compare and file compare result rows, **above** the diff table:

```html
<div class="diff-filter-bar" x-show="sqlExpandedDiffs[r.query_name]?.data?.length > 1">
  <select x-model="(sqlDiffFilter[r.query_name] ??= {type:'',col:'',search:''}).type">
    <option value="">All types</option>
    <option value="value_mismatch">Value diff</option>
    <option value="missing_in_target">Missing →</option>
    <option value="missing_in_source">Missing ←</option>
  </select>
  <select x-model="sqlDiffFilter[r.query_name].col">
    <option value="">All columns</option>
    <template x-for="c in [...new Set((sqlExpandedDiffs[r.query_name]?.data||[]).map(m=>m.column_name))]" :key="c">
      <option :value="c" x-text="c"></option>
    </template>
  </select>
  <input type="text" x-model="sqlDiffFilter[r.query_name].search" placeholder="Search key values…" class="diff-filter-search">
  <span class="diff-filter-count"
        x-text="filteredDiff(sqlExpandedDiffs[r.query_name]?.data||[], r.query_name, sqlDiffFilter).length + ' shown'">
  </span>
</div>
```

The `x-for` loop in the diff table changes from iterating `sqlExpandedDiffs[r.query_name].data` to `filteredDiff(sqlExpandedDiffs[r.query_name]?.data||[], r.query_name, sqlDiffFilter)`.

Same pattern applied to the file compare diff table (using `fileDiffFilter`).

The filter bar is hidden when there are ≤1 rows (no point filtering a single mismatch).

### 3d. Expandable Values

Replace `class="... max-w-xs truncate"` on value cells with a short-value / expand pattern:

```html
<td class="px-2 py-1 border-r border-slate-100">
  <span x-html="renderDiff(m.source_value, m.target_value)"
        :class="expandedCell[m.id+'_src'] ? '' : 'diff-val-truncated'"></span>
  <button x-show="(m.source_value||'').length > 60 && !expandedCell[m.id+'_src']"
          @click="expandedCell = {...expandedCell, [m.id+'_src']: true}"
          class="diff-expand-btn">…more</button>
</td>
```

State: `expandedCell: {}` added to `app()`. Values expand in-place with no modal.

### 3e. Column Summary Mini-Chart

When a SQL or file compare result is expanded and diff data is loaded, a column summary bar chart appears above the filter bar. It is generated by a computed getter:

```javascript
colSummary(diffs) {
  const counts = {};
  (diffs || []).forEach(m => {
    counts[m.column_name] = (counts[m.column_name] || 0) + 1;
  });
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const max = sorted[0]?.[1] || 1;
  return sorted.map(([col, count]) => ({ col, count, pct: Math.round(count / max * 100) }));
}
```

Rendered as CSS flex bars (not Chart.js — no dependency). Each entry now carries a pre-computed `pct` (percentage of max bar), so the template never divides:

```html
<div class="col-summary" x-show="colSummary(sqlExpandedDiffs[r.query_name]?.data).length > 1">
  <div class="col-summary-label">Mismatches by column</div>
  <template x-for="item in colSummary(sqlExpandedDiffs[r.query_name]?.data)" :key="item.col">
    <div class="col-bar-row" @click="(sqlDiffFilter[r.query_name]??={}).col = item.col">
      <span class="col-bar-name" x-text="item.col"></span>
      <div class="col-bar-track">
        <div class="col-bar-fill" :style="`width:${item.pct}%`"></div>
      </div>
      <span class="col-bar-count" x-text="item.count"></span>
    </div>
  </template>
</div>
```

Clicking a bar sets `sqlDiffFilter[r.query_name].col = col`.

The column summary is hidden for single-column diffs (no chart needed).

---

## Section 4 — `frontend/styles.css` Additions

```css
/* Diff highlighting */
.diff-del { background: rgba(251,113,133,0.28); color: #be123c; border-radius: 2px; padding: 0 1px; }
.diff-ins { background: rgba(52,211,153,0.25);  color: #065f46; border-radius: 2px; padding: 0 1px; }
.null-val  { color: #94a3b8; font-style: italic; }

/* Filter bar */
.diff-filter-bar  { display: flex; align-items: center; gap: 8px; padding: 6px 12px; background: #f8fafc; border-bottom: 1px solid #e2e8f0; flex-wrap: wrap; }
.diff-filter-search { padding: 2px 6px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 0.8em; }
.diff-filter-count  { font-size: 0.75em; color: #94a3b8; margin-left: auto; }

/* Expandable values */
.diff-val-truncated { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.diff-expand-btn    { font-size: 0.75em; color: #3b82f6; cursor: pointer; background: none; border: none; padding: 0 4px; }

/* Column summary bars */
.col-summary        { padding: 8px 12px; background: #f8fafc; border-bottom: 1px solid #e2e8f0; }
.col-summary-label  { font-size: 0.72em; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.05em; margin-bottom: 4px; }
.col-bar-row        { display: flex; align-items: center; gap: 8px; margin-bottom: 3px; }
.col-bar-name       { font-size: 0.78em; color: #475569; width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.col-bar-track      { flex: 1; height: 8px; background: #e2e8f0; border-radius: 4px; overflow: hidden; }
.col-bar-fill       { height: 100%; background: #fb7185; border-radius: 4px; transition: width 0.3s ease; }
.col-bar-count      { font-size: 0.75em; color: #94a3b8; width: 24px; text-align: right; }
```

---

## Section 5 — Files Changed

| File | Change |
|------|--------|
| `etl_framework/reporting/templates/report.html.j2` | All HTML report enhancements (sections 2a–2h): summary redesign, column heat map, donut chart, filter toolbar, char-level diff, navigation, expand-all, copy-to-clipboard |
| `frontend/app.js` | Add `charDiff`/`renderDiff` utility; add `sqlDiffFilter`, `fileDiffFilter`, `expandedCell` state; add `filteredDiff`, `colSummary` methods |
| `frontend/index.html` | Filter bars in SQL compare and file compare diff sections; `x-html` diff rendering; expandable value cells; column summary bars |
| `frontend/styles.css` | Diff span styles, filter bar, expandable values, column summary bars |

**No Python/backend changes. No new API endpoints. No new npm/pip packages.**

---

## Section 6 — Out of Scope

- Server-side diff computation (pure client-side JS).
- Char-level diff for numeric values (numbers are right or wrong; char diff produces noise).
- Diff of entire rows (diffs individual column values only).
- Merge / resolve workflow from within the HTML report.
- Parquet file support.
- Saving filter state across sessions.
- The mismatch drawer (BO compare) — it already has a reasonable layout; filter bar can be added in a follow-up.

---

## Section 7 — How This Beats BeyondCompare

| Capability | BeyondCompare | This design |
|-----------|--------------|-------------|
| Char-level diff highlighting | ✅ | ✅ |
| Filter to show only diffs | ✅ | ✅ |
| Jump to next / prev diff | ✅ | ✅ (+ keyboard shortcuts) |
| Column heat map | ❌ | ✅ |
| Mismatch type breakdown chart | ❌ | ✅ |
| Row count visual comparison | ❌ | ✅ (in existing summary) |
| Mismatch acceptance workflow | ❌ | ✅ (existing feature) |
| Works in the browser (no install) | ❌ | ✅ |
| Downloadable report with all features | ❌ | ✅ |
