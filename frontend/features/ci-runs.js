(function (global) {
  'use strict';

  global.ETL_FEATURE_CI_RUNS = function () {
    return {
      ciRunFilters: { days: 30, targetType: '', status: '' },
      ciRunSummary: null,
      ciRunRows: [],
      ciRunLoading: false,
      ciRunError: '',
      ciRunRequestGeneration: 0,

      ciRunSummaryParams() {
        const params = new URLSearchParams();
        params.set('days', String(this.ciRunFilters.days || 30));
        if (this.ciRunFilters.targetType) params.set('target_type', this.ciRunFilters.targetType);
        if (this.ciRunFilters.status) params.set('status', this.ciRunFilters.status);
        return params.toString();
      },

      ciRunListParams() {
        const params = new URLSearchParams();
        params.set('ci_only', 'true');
        params.set('limit', '100');
        params.set('days', String(this.ciRunFilters.days || 30));
        if (this.ciRunFilters.targetType) params.set('target_type', this.ciRunFilters.targetType);
        if (this.ciRunFilters.status) params.set('status', this.ciRunFilters.status);
        return params.toString();
      },

      async loadCiRuns() {
        const generation = ++this.ciRunRequestGeneration;
        this.ciRunLoading = true;
        this.ciRunError = '';
        try {
          const [summary, rows] = await Promise.all([
            api('GET', `/api/runs/ci-summary?${this.ciRunSummaryParams()}`),
            api('GET', `/api/runs?${this.ciRunListParams()}`),
          ]);
          if (generation !== this.ciRunRequestGeneration) return;
          this.ciRunSummary = summary;
          this.ciRunRows = rows || [];
        } catch (e) {
          if (generation !== this.ciRunRequestGeneration) return;
          this.ciRunSummary = null;
          this.ciRunRows = [];
          if (!this.handleAuthError(e)) {
            this.ciRunError = e.message || 'Failed to load CI runs';
            this.toast('error', 'CI runs load failed', this.ciRunError);
          }
        } finally {
          if (generation === this.ciRunRequestGeneration) this.ciRunLoading = false;
        }
      },

      ciRunPassRate() {
        const rate = Number(this.ciRunSummary?.pass_rate || 0);
        return `${Math.round(rate * 100)}%`;
      },

      ciRunCommit(run) {
        const commit = run?.ci_context?.commit_sha;
        return typeof commit === 'string' && commit ? commit.slice(0, 8) : '—';
      },

      ciRunPipelineUrl(run) {
        const value = run?.ci_context?.pipeline_url;
        if (typeof value !== 'string' || !value) return '';
        try {
          const parsed = new URL(value);
          return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : '';
        } catch {
          return '';
        }
      },

      ciRunRef(run) {
        const ref = run?.ci_context?.ref;
        return typeof ref === 'string' && ref ? ref : '—';
      },

      ciRunTimestamp(value) {
        if (typeof value !== 'string' && typeof value !== 'number') return null;
        const timestamp = new Date(value).getTime();
        return Number.isFinite(timestamp) ? timestamp : null;
      },

      ciRunDuration(run) {
        const started = this.ciRunTimestamp(run.started_at);
        const completed = this.ciRunTimestamp(run.completed_at);
        if (started === null || completed === null || completed < started) return '—';
        const seconds = Math.round((completed - started) / 1000);
        if (seconds < 60) return `${seconds}s`;
        const minutes = Math.floor(seconds / 60);
        const remainder = seconds % 60;
        return `${minutes}m ${remainder}s`;
      },

      ciRunTargetLabel(run) {
        if (!run.target_name || !run.target_type) return '—';
        const type = run.target_type.charAt(0).toUpperCase() + run.target_type.slice(1);
        return `${type}: ${run.target_name}`;
      },

      async navigateToCiRunTarget(run) {
        if (run.target_type === 'sequence') {
          window.history.pushState(null, '', '#sequences');
          this.onTabEnter('sequences');
          if (!this.sequences.length) await this.loadSequences();
          const sequence = this.sequences.find((item) => item.name === run.target_name);
          if (sequence) await this.selectSequence(sequence);
        }
        if (run.target_type === 'selection') {
          this.launchSubTab = 'selections';
          this.jobSelectionSearchQuery = run.target_name || '';
          window.history.pushState(null, '', '#jobs');
          this.onTabEnter('jobs');
          await this.loadJobSelections();
          this.$nextTick(() => document.querySelector('[data-testid="job-selection-search-input"]')?.focus());
        }
      },

      openCiRun(run) {
        this.onTabEnter('history');
        this.viewRunDetail(run.run_id);
      },
    };
  };
})(window);
