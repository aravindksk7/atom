(function (global) {
  const TERMINAL_STATUSES = ['PASSED', 'FAILED', 'SLOW', 'ERROR', 'COMPLETED', 'CANCELLED'];

  function isTerminalStatusValue(status) {
    return TERMINAL_STATUSES.includes(String(status || '').toUpperCase());
  }

  // Shared by the Reports tab's logs subtab (features/reports.js) and the
  // Global Logs tab (features/logs.js) — both render log lines via these
  // two functions in index.html (e.g. `x-html="highlightMatch(line.text,
  // logFilterQuery)"`, `:class="logLevelClass(line.level)"`).
  function highlightMatch(text, query) {
    const safe = (text || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    if (!query.trim()) return safe;
    const escapedQ = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return safe.replace(new RegExp(`(${escapedQ})`, 'gi'), '<mark class="log-highlight">$1</mark>');
  }

  function logLevelClass(level) {
    const value = (level || '').toUpperCase();
    if (value === 'ERROR') return 'log-level-error';
    if (value === 'WARNING' || value === 'WARN') return 'log-level-warn';
    if (value === 'INFO') return 'log-level-info';
    if (value === 'DEBUG') return 'log-level-debug';
    return 'log-level-trace';
  }

  global.ETL_APP_CONFIG = {
    terminalStatuses: TERMINAL_STATUSES,
    isTerminalStatusValue,
    highlightMatch,
    logLevelClass,
    jobModalTabs: [
      { id: 'basic', label: 'Basic Info' },
      { id: 'settings', label: 'Settings' },
      { id: 'deps', label: 'Dependencies' },
      { id: 'rules', label: 'DQ Rules' },
      { id: 'tags', label: 'Tags' },
      { id: 'conditions', label: 'Conditions' },
    ],
    dqRuleTemplates: [
      { name: 'Price must be positive', type: 'column_mean_between', defaults: { min: 0, max: null } },
      { name: 'ID must be not null', type: 'not_null', defaults: {} },
      { name: 'Status code range', type: 'column_mean_between', defaults: { min: 100, max: 599 } },
      { name: 'Email format validation', type: 'match_regex', defaults: { pattern: '^[\\w.+-]+@[\\w-]+\\.[\\w.]+$' } },
    ],
    predefinedCompareTemplates: [
      { name: 'Daily BO Report Compare', type: 'bo', config: { sourceTypeA: 'api', sourceTypeB: 'api' } },
      { name: 'Weekly Report Trend Analysis', type: 'bo', config: { sourceTypeA: 'api', sourceTypeB: 'baseline' } },
      { name: 'Ad-hoc File Upload Comparison', type: 'bo', config: { sourceTypeA: 'upload', sourceTypeB: 'upload' } },
      { name: 'Daily Reconciliation vs Baseline', type: 'reconciliation', config: {} },
      { name: 'Production File Validation', type: 'reconciliation', config: { fileMode: 'upload' } },
      { name: 'Environment-to-Environment Diff', type: 'reconciliation', config: {} },
    ],
  };
})(window);
