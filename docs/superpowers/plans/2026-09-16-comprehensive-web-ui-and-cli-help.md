# Comprehensive Web UI & CLI Help Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the Web UI Help Center into a comprehensive, multi-modal guide featuring interactive UI visual mockups and side-by-side command line (CLI) execution examples across all 14 Atom ETL framework modules.

**Architecture:** Extend `frontend/help-content.js` with structured UI mockup definitions and CLI command objects. Enhance `frontend/app-help.js` with search and clipboard copy helpers. Update `frontend/partials/tab-help.html` and `frontend/styles.css` to render interactive UI mockup cards, terminal-styled CLI boxes with copy actions, parameter tables, and sample outputs. Recompile `frontend/index.html` via `node scripts/build-html.js` and verify with Playwright E2E tests in `tests/e2e/11-help.spec.ts`.

**Tech Stack:** JavaScript (ES6), Alpine.js, Tailwind CSS / Custom CSS, Playwright (E2E testing), Node.js build script.

## Global Constraints

- Absolute paths only.
- No external image dependencies: UI visual mockups must be rendered natively with HTML/CSS.
- CLI commands must match actual framework CLI syntax (`atom run`, `atom report`, `atom selections`, `atom runs`, `scripts/ci/run-atom-target.sh`, `python -m etl_framework.runner.cli`).
- Light/Dark theme compatibility for all help components.
- When `frontend/partials/tab-help.html` changes, `node scripts/build-html.js` must be run to update `frontend/index.html`.

---

### Task 1: Extend Alpine.js Help Methods & Search Filtering

**Files:**
- Modify: `C:\atom\frontend\app-help.js`
- Test: `tests/e2e/11-help.spec.ts`

**Interfaces:**
- Consumes: `window.ETL_HELP.sections`
- Produces: `window.ETL_HELP_METHODS.copyCliCommand(command, event)`, enhanced `window.ETL_HELP_METHODS.helpStepMatches(step, search)`

- [ ] **Step 1: Update `frontend/app-help.js` with `copyCliCommand` and enhanced search**

Add `copyCliCommand` and update `helpStepMatches`:

```javascript
(function (global) {
  const HELP_TOPICS = {
    'job-search': {
      title: 'Job Search',
      content: 'Search jobs by name, description, or tags. The search is case-insensitive and matches partial text.',
    },
    chunkSize: {
      title: 'Chunk Size',
      content: 'Number of rows to process at once. Set to 0 to disable chunking and process all rows in memory. Larger values use more memory but may be faster for simple comparisons.',
    },
    runProfile: {
      title: 'Run Profile',
      content: 'Full compares every row. Shadow samples a small fraction of rows (Shadow Sample Fraction) via the sampling backend — useful for cheap, fast per-PR checks; rows missing on either side are always kept.',
    },
    hashPrecheck: {
      title: 'Hash Precheck',
      content: 'When enabled, computes hash values for rows first and only performs full row comparison when hashes differ. Significantly speeds up comparisons for large datasets with few actual differences.',
    },
    nullEqualsNull: {
      title: 'NULL Semantics',
      content: 'When enabled, treats two NULL values as equal during comparison. When disabled, NULL != NULL (SQL standard behavior).',
    },
    maxWorkers: {
      title: 'Max Workers',
      content: 'Maximum number of parallel test execution threads. Higher values speed up large test suites but increase database load.',
    },
    compareTemplate: {
      title: 'Compare Templates',
      content: 'Save and reuse comparison configurations. Templates store your source settings, key columns, and other options so you can quickly repeat common comparisons.',
    },
    sqlQuery: {
      title: 'SQL Query',
      content: 'The SELECT statement used to extract data for comparison. Must include all key columns and comparison columns. Parameterized queries use {env} as a placeholder for the environment name.',
    },
    dsJobParams: {
      title: 'SAP DS Job Params',
      content: 'A JSON object mapping SAP Data Services global variable names to the values to substitute when the job runs, e.g. {"$G_RUN_DATE": "2026-07-24"}.',
    },
  };

  global.ETL_HELP_METHODS = {
    showHelp(topic) {
      const entry = HELP_TOPICS[topic];
      if (!entry) return;
      this.helpTitle = entry.title;
      this.helpContent = entry.content;
      this.showingHelp = true;
    },

    helpFilteredSections() {
      const q = (this.helpSearch || '').trim().toLowerCase();
      const sections = (window.ETL_HELP && window.ETL_HELP.sections) || [];
      if (!q) return sections;
      return sections.filter((s) => {
        if (s.title && s.title.toLowerCase().includes(q)) return true;
        if (s.intro && s.intro.toLowerCase().includes(q)) return true;
        if (s.category && s.category.toLowerCase().includes(q)) return true;
        if (Array.isArray(s.steps)) {
          return s.steps.some((step) => this.helpStepMatches(step, q));
        }
        return false;
      });
    },

    helpStepMatches(step, query) {
      if (!query) return true;
      const q = query.toLowerCase();
      if (step.title && step.title.toLowerCase().includes(q)) return true;
      if (step.text && step.text.toLowerCase().includes(q)) return true;
      if (step.where && step.where.toLowerCase().includes(q)) return true;
      if (step.when && step.when.toLowerCase().includes(q)) return true;
      if (step.tip && step.tip.toLowerCase().includes(q)) return true;
      if (step.warn && step.warn.toLowerCase().includes(q)) return true;
      if (step.cli) {
        if (step.cli.command && step.cli.command.toLowerCase().includes(q)) return true;
        if (step.cli.description && step.cli.description.toLowerCase().includes(q)) return true;
        if (Array.isArray(step.cli.params)) {
          if (step.cli.params.some((p) => (p.flag && p.flag.toLowerCase().includes(q)) || (p.desc && p.desc.toLowerCase().includes(q)))) return true;
        }
        if (step.cli.sampleOutput && step.cli.sampleOutput.toLowerCase().includes(q)) return true;
      }
      if (step.uiMockup) {
        if (step.uiMockup.title && step.uiMockup.title.toLowerCase().includes(q)) return true;
        if (step.uiMockup.badge && step.uiMockup.badge.toLowerCase().includes(q)) return true;
        if (Array.isArray(step.uiMockup.elements)) {
          if (step.uiMockup.elements.some((el) => (el.label && el.label.toLowerCase().includes(q)) || (el.value && el.value.toLowerCase().includes(q)))) return true;
        }
      }
      return false;
    },

    scrollToHelp(sectionId) {
      this.helpActiveId = sectionId;
      const el = document.getElementById('help-' + sectionId);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    },

    async copyCliCommand(cmd, event) {
      if (!cmd) return;
      try {
        await navigator.clipboard.writeText(cmd);
        const btn = event && event.currentTarget;
        if (btn) {
          const originalText = btn.innerHTML;
          btn.innerHTML = '<span style="color:#10b981;font-weight:600;">✓ Copied</span>';
          setTimeout(() => {
            btn.innerHTML = originalText;
          }, 1800);
        }
      } catch (err) {
        console.warn('Failed to copy CLI command:', err);
      }
    },

    initKeyboardShortcuts() {
      document.addEventListener('keydown', (e) => {
        const tag = (document.activeElement && document.activeElement.tagName) || '';
        if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;

        const isMac = navigator.platform && navigator.platform.toUpperCase().includes('MAC');
        const ctrl = isMac ? e.metaKey : e.ctrlKey;

        if (ctrl && e.key === 's') {
          e.preventDefault();
          if (this.showJobModal) {
            this.saveJob();
          } else if (this.currentView === 'compare') {
            this.saveCompareTemplate();
          }
          return;
        }

        if (e.key === 'Enter') {
          if (this.currentView === 'jobs') {
            this.launchJobs();
          } else if (this.currentView === 'compare') {
            const sub = this.compareSubTab;
            if (sub === 'bo') this.runBOComparison && this.runBOComparison();
            else if (sub === 'reconciliation') this.runReconciliation && this.runReconciliation();
          }
          return;
        }

        if (e.key === 'Escape') {
          if (document.activeElement && document.activeElement.closest('[role="dialog"]')) return;
          if (this.showingHelp) { this.showingHelp = false; return; }
          if (this.showJobModal) { this.showJobModal = false; return; }
          if (this.showCompareTemplatePanel) { this.showCompareTemplatePanel = false; return; }
          if (this.showConfigModal) { this.showConfigModal = false; return; }
          if (this.showBOJobModal) { this.showBOJobModal = false; return; }
          if (this.showDSJobModal) { this.showDSJobModal = false; return; }
          if (this.showScheduleModal) { this.showScheduleModal = false; return; }
          if (this.showHookModal) { this.showHookModal = false; return; }
          if (this.showContractModal) { this.showContractModal = false; return; }
          if (this.drawer && this.drawer.show) { this.drawer.show = false; return; }
        }
      });
    },
  };
})(window);
```

- [ ] **Step 2: Verify `frontend/app-help.js` syntax**

Run syntax check with node:
`node -c frontend/app-help.js`
Expected: Success with no syntax errors.

---

### Task 2: Build UI Mockup & CLI Terminal Templates and Styles

**Files:**
- Modify: `C:\atom\frontend\partials\tab-help.html`
- Modify: `C:\atom\frontend\styles.css`
- Modify: `C:\atom\frontend\index.html` (via `node scripts/build-html.js`)

**Interfaces:**
- Consumes: `step.uiMockup`, `step.cli`
- Produces: Visual UI Mockup cards and CLI command panels in Help Center view

- [ ] **Step 1: Update `frontend/partials/tab-help.html` to render UI Mockups and CLI Panels**

Update `frontend/partials/tab-help.html` to include template rendering for `step.uiMockup` and `step.cli`:

```html
<template x-if="currentView === 'help'"><div>
  <div class="section-header">
    <div>
      <div class="section-title">Help Center</div>
      <div class="section-sub">Comprehensive Web UI guide, interactive visual mockups & side-by-side CLI execution reference</div>
    </div>
  </div>

  <div class="help-layout">
    <aside class="help-sidebar">
      <div class="help-search-wrap">
        <svg class="help-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
        <input data-testid="help-search-input" class="help-search" type="text" placeholder="Search help, UI features, CLI flags..." x-model="helpSearch" aria-label="Search help" />
      </div>
      <nav class="help-nav">
        <template x-for="s in helpFilteredSections()" :key="s.id">
          <button class="help-nav-item" :class="helpActiveId === s.id ? 'active' : ''" @click="scrollToHelp(s.id)" x-text="s.title"></button>
        </template>
        <template x-if="helpFilteredSections().length === 0">
          <div class="help-nav-empty">No matches</div>
        </template>
      </nav>
    </aside>

    <div class="help-content">
      <template x-for="(s, i) in helpFilteredSections()" :key="s.id">
        <section class="help-section" :id="'help-' + s.id">
          <div class="help-section-head">
            <span class="help-badge" x-text="String(i + 1).padStart(2, '0')"></span>
            <div>
              <template x-if="s.category">
                <span class="help-badge-cat" x-text="s.category"></span>
              </template>
              <h2 class="help-section-title" x-text="s.title"></h2>
              <p class="help-section-intro" x-text="s.intro"></p>
            </div>
          </div>
          <div class="help-steps">
            <template x-for="(step, si) in s.steps" :key="step.title">
              <div class="help-step" x-show="helpStepMatches(step, helpSearch.trim())">
                <span class="help-step-num" x-text="String(si + 1).padStart(2, '0')"></span>
                <div class="help-step-body">
                  <h3 class="help-step-title" x-text="step.title"></h3>
                  <p class="help-step-text" x-text="step.text"></p>

                  <template x-if="step.where">
                    <div class="help-where"><span class="help-where-label">Web UI Location</span><span x-text="step.where"></span></div>
                  </template>
                  <template x-if="step.when">
                    <div class="help-where"><span class="help-where-label">When to use</span><span x-text="step.when"></span></div>
                  </template>

                  <!-- Visual UI Mockup Card -->
                  <template x-if="step.uiMockup">
                    <div class="help-mockup">
                      <div class="help-mockup-header">
                        <div class="help-mockup-dots"><span></span><span></span><span></span></div>
                        <div class="help-mockup-title" x-text="step.uiMockup.title || 'Web UI Preview'"></div>
                        <template x-if="step.uiMockup.badge">
                          <span class="help-mockup-badge" x-text="step.uiMockup.badge"></span>
                        </template>
                      </div>
                      <div class="help-mockup-body">
                        <div class="help-mockup-grid">
                          <template x-for="(el, eli) in step.uiMockup.elements" :key="eli">
                            <div class="help-mockup-element" :class="el.type">
                              <template x-if="el.type === 'input' || el.type === 'select'">
                                <div class="help-mockup-field">
                                  <label class="help-mockup-label" x-text="el.label"></label>
                                  <div class="help-mockup-val" :class="el.highlight ? 'highlight' : ''" x-text="el.value"></div>
                                </div>
                              </template>
                              <template x-if="el.type === 'button'">
                                <div class="help-mockup-btn" :class="el.highlight ? 'primary' : ''" x-text="el.label || el.value"></div>
                              </template>
                              <template x-if="el.type === 'badge' || el.type === 'status'">
                                <span class="help-mockup-status" :class="'status-' + (el.status || 'info')" x-text="el.value || el.label"></span>
                              </template>
                              <template x-if="el.type === 'dag'">
                                <div class="help-mockup-dag">
                                  <span class="help-mockup-dag-node" x-text="el.label"></span>
                                  <span class="help-mockup-dag-arrow">➔</span>
                                  <span class="help-mockup-dag-node active" x-text="el.value"></span>
                                </div>
                              </template>
                              <template x-if="el.type === 'table'">
                                <div class="help-mockup-table-wrap">
                                  <table class="help-mockup-table">
                                    <thead>
                                      <tr>
                                        <template x-for="col in el.columns" :key="col">
                                          <th x-text="col"></th>
                                        </template>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      <template x-for="(row, ri) in el.rows" :key="ri">
                                        <tr>
                                          <template x-for="(cell, ci) in row" :key="ci">
                                            <td x-text="cell"></td>
                                          </template>
                                        </tr>
                                      </template>
                                    </tbody>
                                  </table>
                                </div>
                              </template>
                            </div>
                          </template>
                        </div>
                      </div>
                    </div>
                  </template>

                  <!-- CLI Execution Panel -->
                  <template x-if="step.cli">
                    <div class="help-cli-box">
                      <div class="help-cli-header">
                        <div class="help-cli-title">
                          <svg class="help-cli-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"></polyline><line x1="12" y1="19" x2="20" y2="19"></line></svg>
                          <span>CLI Execution Equivalent</span>
                        </div>
                        <button type="button" class="help-cli-copy-btn" @click="copyCliCommand(step.cli.command, $event)">
                          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                          <span>Copy</span>
                        </button>
                      </div>
                      <template x-if="step.cli.description">
                        <div class="help-cli-desc" x-text="step.cli.description"></div>
                      </template>
                      <pre class="help-cli-code"><code x-text="step.cli.command"></code></pre>

                      <template x-if="step.cli.params && step.cli.params.length > 0">
                        <div class="help-cli-params">
                          <div class="help-cli-params-title">Flags & Arguments:</div>
                          <div class="help-cli-params-grid">
                            <template x-for="p in step.cli.params" :key="p.flag">
                              <div class="help-cli-param-row">
                                <code class="help-cli-param-flag" x-text="p.flag"></code>
                                <span class="help-cli-param-desc" x-text="p.desc"></span>
                              </div>
                            </template>
                          </div>
                        </div>
                      </template>

                      <template x-if="step.cli.sampleOutput">
                        <div class="help-cli-output-wrap">
                          <div class="help-cli-output-title">Sample Output / Response:</div>
                          <pre class="help-cli-output"><code x-text="step.cli.sampleOutput"></code></pre>
                        </div>
                      </template>
                    </div>
                  </template>

                  <template x-if="step.tip">
                    <div class="help-callout tip"><span class="help-callout-label">Tip</span><span x-text="step.tip"></span></div>
                  </template>
                  <template x-if="step.warn">
                    <div class="help-callout warn"><span class="help-callout-label">Caution</span><span x-text="step.warn"></span></div>
                  </template>
                </div>
              </div>
            </template>
          </div>
        </section>
      </template>

      <template x-if="helpFilteredSections().length === 0">
        <div class="help-empty">No help topics match "<span x-text="helpSearch"></span>".</div>
      </template>
    </div>
  </div>
</div></template>
```

- [ ] **Step 2: Add CSS rules for UI Mockups and CLI Panels in `frontend/styles.css`**

Add styling for `.help-mockup`, `.help-cli-*` in `frontend/styles.css`:

```css
/* Help Mockup & CLI Panel Styling */
.help-mockup {
  margin: 0.85rem 0;
  border-radius: 8px;
  border: 1px solid var(--border, #334155);
  background: var(--card-bg, #1e293b);
  overflow: hidden;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
}
.help-mockup-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.45rem 0.75rem;
  background: rgba(15, 23, 42, 0.75);
  border-bottom: 1px solid var(--border, #334155);
  font-size: 0.78rem;
}
.help-mockup-dots {
  display: flex;
  gap: 4px;
}
.help-mockup-dots span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #64748b;
  display: inline-block;
}
.help-mockup-dots span:nth-child(1) { background: #ef4444; }
.help-mockup-dots span:nth-child(2) { background: #f59e0b; }
.help-mockup-dots span:nth-child(3) { background: #10b981; }
.help-mockup-title {
  font-weight: 600;
  color: #cbd5e1;
  flex: 1;
}
.help-mockup-badge {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 2px 6px;
  border-radius: 4px;
  background: #3b82f6;
  color: #ffffff;
  font-weight: 700;
}
.help-mockup-body {
  padding: 0.75rem;
}
.help-mockup-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: center;
}
.help-mockup-field {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 140px;
}
.help-mockup-label {
  font-size: 0.7rem;
  color: #94a3b8;
  font-weight: 500;
}
.help-mockup-val {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 4px;
  padding: 4px 8px;
  font-size: 0.78rem;
  color: #e2e8f0;
}
.help-mockup-val.highlight {
  border-color: #38bdf8;
  background: rgba(56, 189, 248, 0.1);
  color: #38bdf8;
}
.help-mockup-btn {
  padding: 5px 10px;
  border-radius: 4px;
  font-size: 0.75rem;
  font-weight: 600;
  background: #334155;
  color: #f1f5f9;
  border: 1px solid #475569;
}
.help-mockup-btn.primary {
  background: #2563eb;
  border-color: #3b82f6;
  color: #ffffff;
}
.help-mockup-status {
  padding: 2px 8px;
  border-radius: 9999px;
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
}
.help-mockup-status.status-passed { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid #059669; }
.help-mockup-status.status-failed { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid #dc2626; }
.help-mockup-status.status-running { background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid #2563eb; }
.help-mockup-status.status-warn { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid #d97706; }
.help-mockup-status.status-info { background: rgba(100, 116, 139, 0.2); color: #cbd5e1; border: 1px solid #475569; }

.help-mockup-dag {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  background: #0f172a;
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px dashed #475569;
}
.help-mockup-dag-node {
  background: #1e293b;
  border: 1px solid #3b82f6;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 0.75rem;
  color: #e2e8f0;
}
.help-mockup-dag-node.active {
  background: #1d4ed8;
  color: #ffffff;
  font-weight: 600;
}
.help-mockup-dag-arrow {
  color: #38bdf8;
  font-weight: bold;
}
.help-mockup-table-wrap {
  width: 100%;
  overflow-x: auto;
}
.help-mockup-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.75rem;
}
.help-mockup-table th {
  background: #0f172a;
  color: #94a3b8;
  padding: 4px 8px;
  text-align: left;
  border-bottom: 1px solid #334155;
}
.help-mockup-table td {
  padding: 4px 8px;
  border-bottom: 1px solid #1e293b;
  color: #e2e8f0;
}

/* CLI Execution Box */
.help-cli-box {
  margin: 0.85rem 0;
  border-radius: 8px;
  border: 1px solid #334155;
  background: #090d16;
  overflow: hidden;
}
.help-cli-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.4rem 0.75rem;
  background: #131b2e;
  border-bottom: 1px solid #1e293b;
}
.help-cli-title {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.76rem;
  font-weight: 600;
  color: #38bdf8;
}
.help-cli-icon {
  width: 14px;
  height: 14px;
}
.help-cli-copy-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  background: #1e293b;
  border: 1px solid #475569;
  color: #cbd5e1;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.72rem;
  cursor: pointer;
  transition: all 0.15s ease;
}
.help-cli-copy-btn:hover {
  background: #334155;
  color: #ffffff;
}
.help-cli-desc {
  padding: 0.4rem 0.75rem 0.2rem 0.75rem;
  font-size: 0.75rem;
  color: #94a3b8;
}
.help-cli-code {
  margin: 0;
  padding: 0.6rem 0.75rem;
  background: #06090e;
  color: #34d399;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.8rem;
  line-height: 1.4;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.help-cli-params {
  padding: 0.5rem 0.75rem;
  border-top: 1px solid #1e293b;
  background: #0c1220;
}
.help-cli-params-title {
  font-size: 0.7rem;
  font-weight: 600;
  color: #94a3b8;
  margin-bottom: 0.3rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.help-cli-params-grid {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.help-cli-param-row {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  font-size: 0.75rem;
}
.help-cli-param-flag {
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.1);
  padding: 1px 4px;
  border-radius: 3px;
  font-family: monospace;
  font-size: 0.73rem;
  white-space: nowrap;
}
.help-cli-param-desc {
  color: #cbd5e1;
}
.help-cli-output-wrap {
  padding: 0.5rem 0.75rem;
  border-top: 1px solid #1e293b;
  background: #080d17;
}
.help-cli-output-title {
  font-size: 0.7rem;
  font-weight: 600;
  color: #64748b;
  margin-bottom: 0.3rem;
  text-transform: uppercase;
}
.help-cli-output {
  margin: 0;
  padding: 0.5rem;
  background: #030712;
  border: 1px solid #1f2937;
  border-radius: 4px;
  color: #9ca3af;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.73rem;
  line-height: 1.35;
  overflow-x: auto;
  white-space: pre;
}
```

- [ ] **Step 3: Run HTML compilation script to update `frontend/index.html`**

Run: `node scripts/build-html.js`
Expected: Successfully generated `frontend/index.html`.

---

### Task 3: Populate Comprehensive Help Content Across All 14 Modules

**Files:**
- Modify: `C:\atom\frontend\help-content.js`
- Test: `tests/e2e/11-help.spec.ts`

**Interfaces:**
- Consumes: `window.ETL_HELP.sections`
- Produces: 14 richly populated modules with `uiMockup` cards and `cli` command specifications.

- [ ] **Step 1: Update `frontend/help-content.js` with comprehensive data for all 14 modules**

Ensure all sections (Primer, Config, Launch/Jobs, Sequences, Compare, Differences, Monitor, History, Contracts, AWS, File Servers, Adapters, Reports & Scheduler Stats, CI/CD & Logs) have:
1. `where` and `when` tags
2. `uiMockup` structures with realistic inputs, buttons, status badges, DAG previews, or tables
3. `cli` objects with runnable commands (`atom run`, `atom report`, `atom selections`, `atom runs`, script wrappers, curl endpoints, python runner), parameter breakdowns, and sample outputs.

- [ ] **Step 2: Validate `frontend/help-content.js` with node**

Run: `node -c frontend/help-content.js`
Expected: No syntax errors.

---

### Task 4: Expand Playwright E2E Tests & System Verification

**Files:**
- Modify: `C:\atom\tests\e2e\11-help.spec.ts`

**Interfaces:**
- Consumes: Frontend web server, Playwright test harness
- Produces: 100% passing E2E verification of Help Center UI mockups, CLI panels, search, and deep links.

- [ ] **Step 1: Add E2E tests for UI Mockups and CLI Panels in `tests/e2e/11-help.spec.ts`**

Update `tests/e2e/11-help.spec.ts` with test cases:
1. `sidebar lists all 14 sections from window.ETL_HELP`
2. `search finds CLI commands and flags (e.g. atom run, --target-type sequence, --junit-out, --var)`
3. `search finds UI mockup controls (e.g. DAG, Mismatch Inspector, Data Contracts)`
4. `UI mockup cards and CLI execution panels render properly on help page`
5. `deep-link query parameter (?tab=help) loads help view seamlessly`

- [ ] **Step 2: Run Playwright E2E test suite**

Run: `npx playwright test tests/e2e/11-help.spec.ts`
Expected: All tests pass.

- [ ] **Step 3: Run full HTML build to ensure clean distribution state**

Run: `npm run build:html`
Expected: Clean build output.
