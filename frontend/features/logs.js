(function (global) {
  'use strict';
  // Logs feature slice (Global Logs tab: server-wide searchable log
  // viewer). Merged into the Alpine component via the FEATURE_SLICES
  // reduce in app.js.
  //
  // NOTE: highlightMatch() and logLevelClass() remain in core (app.js)
  // because the Reports tab's logs subtab (features/reports.js) also
  // uses them — see reports.js's own cross-reference comment.
  global.ETL_FEATURE_LOGS = function () {
    return {
      // ===== STATE (extracted from app.js) =====
    // -----------------------------------------------------------
    // Global Logs tab (server-wide, no run_id required)
    // -----------------------------------------------------------
    globalLogEvents: [],
    globalLogsLoading: false,
    globalLogFilterQuery: '',
    globalLogFilterLevel: '',
    globalLogRunId: '',
    globalLogsPollTimer: null,

      // ===== METHODS (extracted from app.js) =====
    async loadGlobalLogs() {
      const isFirstLoad = this.globalLogEvents.length === 0;
      if (isFirstLoad) this.globalLogsLoading = true;
      const params = new URLSearchParams({ limit: '1000' });
      if (this.globalLogRunId.trim()) params.set('run_id', this.globalLogRunId.trim());
      const logList = document.querySelector('.global-log-list');
      const wasAtBottom = logList
        ? logList.scrollHeight - logList.scrollTop - logList.clientHeight < 16
        : true;
      try {
        const data = await api('GET', `/api/logs?${params.toString()}`);
        this.globalLogEvents = data.lines || [];
        if (wasAtBottom) {
          this.$nextTick(() => {
            if (logList) logList.scrollTop = logList.scrollHeight;
          });
        }
      } catch (e) {
        if (isFirstLoad) this.toast('error', 'Failed to load logs', e.message);
        // Swallow errors on background poll ticks — the next poll recovers.
      } finally {
        this.globalLogsLoading = false;
      }
    },

    filteredGlobalLogEvents() {
      let events = this.globalLogEvents;
      if (this.globalLogFilterLevel) {
        const fl = (this.globalLogFilterLevel || '').toUpperCase();
        events = events.filter(e => {
          const el = (e.level || '').toUpperCase();
          if (fl === 'WARNING') return el === 'WARNING' || el === 'WARN';
          return el === fl;
        });
      }
      if (this.globalLogFilterQuery.trim()) {
        const q = this.globalLogFilterQuery.toLowerCase();
        events = events.filter(e => (e.text || '').toLowerCase().includes(q));
      }
      return events;
    },

    startGlobalLogsPolling() {
      this.loadGlobalLogs();
      if (this.globalLogsPollTimer) return;
      this.globalLogsPollTimer = setInterval(() => {
        if (document.visibilityState === 'visible') this.loadGlobalLogs();
      }, 5000);
    },

    stopGlobalLogsPolling() {
      if (this.globalLogsPollTimer) {
        clearInterval(this.globalLogsPollTimer);
        this.globalLogsPollTimer = null;
      }
    },
    };
  };
})(window);
