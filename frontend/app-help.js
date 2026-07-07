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
  };

  global.ETL_HELP_METHODS = {
    showHelp(topic) {
      const entry = HELP_TOPICS[topic];
      if (!entry) return;
      this.helpTitle = entry.title;
      this.helpContent = entry.content;
      this.showingHelp = true;
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
          if (this.showingHelp) { this.showingHelp = false; return; }
          if (this.showJobModal) { this.showJobModal = false; return; }
          if (this.showCompareTemplatePanel) { this.showCompareTemplatePanel = false; return; }
          if (this.showConfigModal) { this.showConfigModal = false; return; }
          if (this.showBOJobModal) { this.showBOJobModal = false; return; }
          if (this.showScheduleModal) { this.showScheduleModal = false; return; }
          if (this.showHookModal) { this.showHookModal = false; return; }
          if (this.showContractModal) { this.showContractModal = false; return; }
          if (this.drawer && this.drawer.show) { this.drawer.show = false; return; }
        }
      });
    },
  };
})(window);
