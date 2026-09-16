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
      content: 'A JSON object mapping SAP Data Services global variable names to the values to substitute when the job runs, e.g. {"$G_RUN_DATE": "2026-07-24"}. The name can be typed with or without the leading $ (Designer always shows it with one) — the app strips it before sending, since the SOAP call needs the bare name (confirmed live: keeping the $ still runs the job, but the substitution is silently ignored and the variable falls back to its compiled default). For a text/varchar global variable, use a plain value — do not add quotes yourself, the app quotes it for you automatically, e.g. entering 10-Jun-2026 sends \'10-Jun-2026\'.\n\nFor a date-typed global variable, a plain value will NOT work: SAP DS evaluates the substitution as a BODS expression, and a quoted string is not a valid date expression, so it silently falls back to the variable\'s compiled default (commonly today\'s date) with no error. Give the exact BODS expression yourself instead, e.g. {"$G_BUSINESS_DATE": "to_date(\'10-Jun-2026\',\'dd-mon-yyyy\')"} — the app detects a function-call value like this (or an already-quoted \'...\' literal) and sends it through unchanged rather than re-quoting it.\n\nTo drive a value from an atom Custom Variable (Config > Custom Variables) instead of a fixed literal, use a {{VARIABLE_NAME}} placeholder as the value, e.g. {"$G_BUSINESS_DATE": "to_date(\'{{BUSINESS_DATE}}\',\'dd-mon-yyyy\')"}. It is resolved once per run before the job is triggered, using (in priority order) any launch-time override, then the selected Config\'s override, then the variable\'s own default. A placeholder that resolves to blank falls through to the next-lower-priority value; a name with no matching Custom Variable is left as literal text, so check for a typo if the SAP DS side still sees the raw {{...}} string.',
    },
  };

  const COPY_STATES = new WeakMap();

  global.ETL_HELP_METHODS = {
    showHelp(topic) {
      const entry = HELP_TOPICS[topic];
      if (!entry) return;
      this.helpTitle = entry.title;
      this.helpContent = entry.content;
      this.showingHelp = true;
    },

    helpNormalize(value) {
      return (value || '').toString().toLowerCase();
    },

    helpSectionMatches(section, query) {
      const q = this.helpNormalize(query);
      if (!q) return true;
      if ([section.title, section.intro, section.category]
        .some((value) => this.helpNormalize(value).includes(q))) return true;
      return (section.steps || []).some((step) => this.helpStepMatches(step, q));
    },

    helpFilteredSections() {
      const q = this.helpNormalize((this.helpSearch || '').trim());
      if (!q) return this.helpSections;
      return this.helpSections.filter((section) => this.helpSectionMatches(section, q));
    },

    helpStepMatches(step, query, section) {
      const q = this.helpNormalize(query);
      if (!q) return true;
      if (section && [section.title, section.intro, section.category]
        .some((value) => this.helpNormalize(value).includes(q))) return true;
      const fields = [step.title, step.text, step.where, step.when, step.tip, step.warn];
      if (step.cli) {
        fields.push(step.cli.command, step.cli.description, step.cli.sampleOutput);
        if (Array.isArray(step.cli.params)) {
          step.cli.params.forEach((param) => fields.push(param.flag, param.desc));
        }
      }
      if (step.uiMockup) {
        fields.push(step.uiMockup.title, step.uiMockup.badge);
        if (Array.isArray(step.uiMockup.elements)) {
          step.uiMockup.elements.forEach((element) => fields.push(element.label, element.value));
        }
      }
      return fields.some((value) => this.helpNormalize(value).includes(q));
    },

    scrollToHelp(id) {
      this.helpActiveId = id;
      const element = document.getElementById('help-' + id);
      if (element) element.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },

    async copyCliCommand(command, event) {
      if (!command || !navigator.clipboard || !navigator.clipboard.writeText) return;
      try {
        await navigator.clipboard.writeText(command);
        const button = event && event.currentTarget;
        if (!button) return;
        const label = button.querySelector('span');
        if (!label) return;
        const previous = COPY_STATES.get(button);
        if (previous) clearTimeout(previous.timeoutId);
        const originalText = previous ? previous.originalText : label.textContent;
        const originalAriaLabel = previous ? previous.originalAriaLabel : button.getAttribute('aria-label');
        label.textContent = 'Copied!';
        button.setAttribute('aria-label', (button.dataset.copyLabel ? button.dataset.copyLabel + ' command copied' : 'Command copied'));
        button.dataset.copyState = 'copied';
        const timeoutId = setTimeout(() => {
          label.textContent = originalText;
          if (originalAriaLabel === null) button.removeAttribute('aria-label');
          else button.setAttribute('aria-label', originalAriaLabel);
          delete button.dataset.copyState;
          COPY_STATES.delete(button);
        }, 1800);
        COPY_STATES.set(button, { originalText, originalAriaLabel, timeoutId });
      } catch (error) {
        console.warn('Failed to copy CLI command:', error);
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
