(function (global) {
  'use strict';
  // Reports feature slice (Reports tab). Merged into the Alpine
  // component via the FEATURE_SLICES reduce in app.js.
  //
  // NOTE: this tab's logs subtab renders log lines via highlightMatch()
  // and logLevelClass() (see the x-for template in index.html's Reports
  // section, e.g. `x-html="highlightMatch(line.text, logFilterQuery)"`
  // and `:class="logLevelClass(line.level)"`). Both functions remain in
  // core (app.js) — do not move them here — because the Global Logs tab
  // (features/logs.js) uses the exact same two functions on its own log
  // lines. Moving either copy would orphan the other tab's markup.
  global.ETL_FEATURE_REPORTS = function () {
    return {
      // ===== STATE (extracted from app.js) =====
    // -----------------------------------------------------------
    // Reports tab
    // -----------------------------------------------------------
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
    allLogEventsTruncated: false,
    allLogEventsTotalLines: 0,
    logFilterQuery: '',
    logFilterLevel: '',

      // ===== METHODS (extracted from app.js) =====
    resetReportArtifacts() {
      this.reportLoaded = false;
      this.reportMetrics = null;
      this.reportLogs = null;
      if (this.reportBlobUrl) { URL.revokeObjectURL(this.reportBlobUrl); this.reportBlobUrl = ''; }
      this.allLogEvents = [];
      this.logFilterQuery = '';
      this.logFilterLevel = '';
    },

    async switchReportView(view) {
      this.reportView = view;
      if (!this.reportRunId || !this.reportLoaded) return;
      if (view === 'metrics') await this.loadRunMetrics();
      if (view === 'logs' && this.allLogEvents.length === 0) await this.loadAllLogEvents();
    },

    async loadReport() {
      if (!this.reportRunId) return;
      if (this.reportBlobUrl) { URL.revokeObjectURL(this.reportBlobUrl); this.reportBlobUrl = ''; }
      this.reportLoaded = false;
      try {
        const { blob } = await apiBlob(`/api/runs/${this.reportRunId}/report`);
        this.reportBlobUrl = URL.createObjectURL(blob);
        this.reportLoaded = true;
        if (this.reportView === 'metrics') this.loadRunMetrics();
        if (this.reportView === 'logs') this.loadAllLogEvents();
      } catch (e) {
        this.reportLoaded = false;
        this.toast('error', 'Failed to load report', e.message);
      }
    },

    async loadAllLogEvents() {
      if (!this.reportRunId) return;
      this.allLogEventsLoading = true;
      this.allLogEvents = [];
      this.allLogEventsTruncated = false;
      this.allLogEventsTotalLines = 0;
      try {
        const data = await api('GET', `/api/runs/${this.reportRunId}/logs?format=json&limit=5000&scope=run`);
        this.allLogEvents = data.lines || [];
        this.allLogEventsTotalLines = data.total_lines || 0;
        this.allLogEventsTruncated = this.allLogEventsTotalLines > 5000;
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

    async loadRunMetrics() {
      if (!this.reportRunId) return;
      this.reportMetricsLoading = true;
      try {
        this.reportMetrics = await api('GET', `/api/runs/${this.reportRunId}/metrics?format=json`);
      } catch (e) {
        this.reportMetrics = null;
        this.toast('error', 'Metrics unavailable', e.message);
      } finally {
        this.reportMetricsLoading = false;
      }
    },

    async loadRunLogs() {
      if (!this.reportRunId) return;
      this.reportLogsLoading = true;
      const params = new URLSearchParams({
        format: 'json',
        limit: String(this.reportLogLimit || 500),
      });
      if (this.reportLogQuery) params.set('q', this.reportLogQuery);
      if (this.reportLogLevel) params.set('level', this.reportLogLevel);
      try {
        this.reportLogs = await api('GET', `/api/runs/${this.reportRunId}/logs?${params.toString()}`);
      } catch (e) {
        this.reportLogs = null;
        this.toast('error', 'Logs unavailable', e.message);
      } finally {
        this.reportLogsLoading = false;
      }
    },

    metricsPassRate(metrics) {
      const total = metrics?.total_tests || 0;
      return total ? Math.round(((metrics.passed || 0) / total) * 1000) / 10 : 0;
    },
    };
  };
})(window);
