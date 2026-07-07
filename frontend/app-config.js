(function (global) {
  const TERMINAL_STATUSES = ['PASSED', 'FAILED', 'SLOW', 'ERROR', 'COMPLETED', 'CANCELLED'];

  function isTerminalStatusValue(status) {
    return TERMINAL_STATUSES.includes(String(status || '').toUpperCase());
  }

  global.ETL_APP_CONFIG = {
    terminalStatuses: TERMINAL_STATUSES,
    isTerminalStatusValue,
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
