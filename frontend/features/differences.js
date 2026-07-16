(function (global) {
  'use strict';
  // Differences feature slice (Differences Explorer tab). Merged into
  // the Alpine component via the FEATURE_SLICES reduce in app.js.
  global.ETL_FEATURE_DIFFERENCES = function () {
    return {
      // ===== STATE (extracted from app.js) =====
    // -----------------------------------------------------------
    // Differences Explorer tab
    // -----------------------------------------------------------
    // The tab's accept/reject-all buttons drive openMismatchDecisionForm /
    // closeMismatchDecisionForm / submitMismatchDecision, which live in
    // app.js (core) — shared with the mismatch drawer. Those methods read
    // this file's diff* state (diffSearch, diffColumn, diffType, diffStatus,
    // diffRunId, diffResultId, diffPage) and call fetchDifferenceRows() /
    // loadDifferenceInsights() directly via `this`, relying on the
    // FEATURE_SLICES merge in app.js to flatten both files onto one object.
    diffRunId: '',
    diffResultId: null,
    diffRunDetail: null,
    diffTestOptions: [],
    diffColumnOptions: [],
    diffSearch: '',
    diffColumn: '',
    diffType: '',
    diffStatus: '',
    diffSort: 'id',
    diffPage: 0,
    diffPageSize: 100,
    diffRows: [],
    diffTotal: 0,
    diffStoredComplete: true,
    diffLoading: false,
    diffInsights: null,
    diffInsightsLoading: false,

      // ===== METHODS (extracted from app.js) =====
    _applyDeepLink() {
      const params = new URLSearchParams(window.location.search);
      const tab = params.get('tab');
      const run = params.get('run');
      const result = params.get('result');
      if (tab === 'differences' && run) {
        this.currentView = 'differences';
        this.selectDifferenceRun(run).then(() => {
          if (result) this.selectDifferenceResult(result);
        });
        window.history.replaceState(null, '', window.location.pathname);
      }
    },

    async selectDifferenceRun(runId) {
      this.diffRunId = runId;
      this.diffResultId = null;
      this.diffRunDetail = null;
      this.diffTestOptions = [];
      this.diffColumnOptions = [];
      this.diffRows = [];
      this.diffTotal = 0;
      this.diffPage = 0;
      this.diffInsights = null;
      if (!runId) return;
      try {
        const run = await api('GET', `/api/runs/${runId}`);
        this.diffRunDetail = run;
        this.diffTestOptions = (run.results || []).map(r => ({
          id: r.id,
          query_name: r.query_name,
          total_issues: (r.value_mismatch_count || 0) + (r.missing_in_target_count || 0) + (r.missing_in_source_count || 0),
        }));
      } catch (e) {
        this.toast('error', 'Failed to load run', e.message);
      }
      await this.loadDifferenceInsights();
    },

    async loadDifferenceInsights() {
      if (!this.diffRunId) return;
      this.diffInsightsLoading = true;
      try {
        this.diffInsights = await api('GET', `/api/runs/${this.diffRunId}/mismatches/insights`);
      } catch (e) {
        this.diffInsights = null;
        this.toast('error', 'Failed to load insights', e.message);
      } finally {
        this.diffInsightsLoading = false;
        this.$nextTick(() => this._renderDifferenceCharts());
      }
    },

    async selectDifferenceResult(resultId) {
      this.diffResultId = resultId ? Number(resultId) : null;
      this.diffPage = 0;
      this.diffColumnOptions = [];
      if (!this.diffResultId) { this.diffRows = []; this.diffTotal = 0; return; }
      const result = (this.diffRunDetail?.results || []).find(r => r.id === this.diffResultId);
      this.diffColumnOptions = (result?.column_stats || []).map(s => s.column);
      await this.fetchDifferenceRows();
    },

    differenceQueryString() {
      const params = new URLSearchParams();
      params.set('limit', String(this.diffPageSize));
      params.set('offset', String(this.diffPage * this.diffPageSize));
      if (this.diffSearch) params.set('search', this.diffSearch);
      if (this.diffColumn) params.set('column', this.diffColumn);
      if (this.diffType) params.set('mismatch_type', this.diffType);
      if (this.diffStatus) params.set('status', this.diffStatus);
      if (this.diffSort) params.set('sort', this.diffSort);
      return params.toString();
    },

    async fetchDifferenceRows() {
      if (!this.diffRunId || !this.diffResultId) return;
      this.diffLoading = true;
      try {
        const { items, total, storedComplete } = await apiPaged(
          `/api/runs/${this.diffRunId}/results/${this.diffResultId}/mismatches?${this.differenceQueryString()}`);
        this.diffRows = items;
        this.diffTotal = total;
        this.diffStoredComplete = storedComplete;
      } catch (e) {
        this.toast('error', 'Failed to load differences', e.message);
      } finally {
        this.diffLoading = false;
      }
    },

    applyDifferenceFilters() {
      this.diffPage = 0;
      this.fetchDifferenceRows();
    },

    clearDifferenceFilters() {
      this.diffSearch = ''; this.diffColumn = ''; this.diffType = ''; this.diffStatus = ''; this.diffSort = 'id';
      this.applyDifferenceFilters();
    },

    differenceTotalPages() {
      return this.diffTotal > 0 ? Math.ceil(this.diffTotal / this.diffPageSize) : 1;
    },

    nextDifferencePage() {
      if ((this.diffPage + 1) * this.diffPageSize >= this.diffTotal) return;
      this.diffPage++;
      this.fetchDifferenceRows();
    },

    prevDifferencePage() {
      if (this.diffPage === 0) return;
      this.diffPage--;
      this.fetchDifferenceRows();
    },

    _renderDifferenceCharts() {
      if (typeof Chart === 'undefined' || !this.diffInsights) return;
      const colCanvas = document.getElementById('diffColumnsChart');
      if (colCanvas) {
        if (this._diffColumnsChartInstance) { this._diffColumnsChartInstance.destroy(); this._diffColumnsChartInstance = null; }
        const cols = this.diffInsights.top_columns || [];
        this._diffColumnsChartInstance = new Chart(colCanvas, {
          type: 'bar',
          data: {
            labels: cols.map(c => c.column),
            datasets: [{ label: 'Mismatches', data: cols.map(c => c.count), backgroundColor: '#fb7185', borderColor: '#0d0f12', borderWidth: 1 }],
          },
          options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
              x: { ticks: { color: '#94a3b8' }, grid: { color: '#1e2533' } },
              y: { ticks: { color: '#94a3b8' }, grid: { color: '#1e2533' } },
            },
          },
        });
      }
      const typeCanvas = document.getElementById('diffTypeChart');
      if (typeCanvas) {
        if (this._diffTypeChartInstance) { this._diffTypeChartInstance.destroy(); this._diffTypeChartInstance = null; }
        const totals = this.diffInsights.type_totals || {};
        this._diffTypeChartInstance = new Chart(typeCanvas, {
          type: 'doughnut',
          data: {
            labels: ['Value diff', 'Missing →', 'Missing ←'],
            datasets: [{
              data: [totals.value_diff || 0, totals.missing_in_target || 0, totals.missing_in_source || 0],
              backgroundColor: ['#fbbf24', '#38bdf8', '#a78bfa'],
            }],
          },
          options: { responsive: true, plugins: { legend: { position: 'bottom', labels: { color: '#94a3b8' } } } },
        });
      }
    },
    };
  };
})(window);
