(function (global) {
  'use strict';
  // History feature slice (History tab: runs list, Profile & Schema,
  // Trends, Lineage, mismatch distribution, inline mismatch expand,
  // Audit, Coverage). Merged into the Alpine component via the
  // FEATURE_SLICES reduce in app.js.
  global.ETL_FEATURE_HISTORY = function () {
    return {
      // ===== STATE (extracted from app.js) =====
    // -----------------------------------------------------------
    // History
    // -----------------------------------------------------------
    runs: [],
    selectedRun: null,
    chartInstance: null,
    historyFilterStatus: '',
    historyFilterRunType: '',
    historySubTab: 'runs',
    coverageData: null,
    coverageLoading: false,
    coverageGapsOnly: false,
    flakyData: null,
    auditEvents: [],
    auditLoading: false,
    auditFilterResourceType: '',
    auditFilterResourceId: '',
    selectedResultIds: {},
    bulkDecisionForm: { open: false, mode: null, note: '', saving: false },

    // -----------------------------------------------------------
    // Profile & Schema
    // -----------------------------------------------------------
    profileJobName: '',
    profileData: null,
    profileLoading: false,
    suggestedRules: null,
    schemaJobName: '',
    schemaEnvironment: 'source',
    schemaHistory: null,
    schemaLoading: false,

    // -----------------------------------------------------------
    // Trends
    // -----------------------------------------------------------
    trendsJobName: '',
    trendsMetric: 'mismatch_rate',
    trendsWindow: 30,
    trendsData: null,
    trendsLoading: false,
    trendsChartInstance: null,

    // -----------------------------------------------------------
    // Lineage
    // -----------------------------------------------------------
    lineageGraph: null,
    lineageLoading: false,

    // -----------------------------------------------------------
    // Mismatch distribution
    // -----------------------------------------------------------
    mismatchDist: {},  // result_id → distribution array
    segmentDrill: {},
    segmentDrillLoading: {},

    // -----------------------------------------------------------
    // Inline mismatch expand (History detail)
    // -----------------------------------------------------------
    expandedMismatches: {},      // result_id → rows[]
    expandingMismatch: {},       // result_id → bool
    expandedMismatchOffset: {},  // result_id → current offset
    outcomeOverrideForms: {},    // result_id → { open, reason, saving }
    expandedSampleRows: {},      // result_id → bool

      // ===== METHODS (extracted from app.js) =====
    // ===========================================================
    // HISTORY
    // ===========================================================
    async loadRuns() {
      const params = new URLSearchParams();
      if (this.historyFilterStatus) params.set('status', this.historyFilterStatus);
      if (this.historyFilterRunType) params.set('run_type', this.historyFilterRunType);
      const qs = params.toString() ? '?' + params.toString() : '';
      try { this.runs = await api('GET', '/api/runs' + qs); } catch {}
    },

    async loadAudit() {
      this.auditLoading = true;
      const params = new URLSearchParams({ limit: '100' });
      if (this.auditFilterResourceType) params.set('resource_type', this.auditFilterResourceType);
      if (this.auditFilterResourceId) params.set('resource_id', this.auditFilterResourceId);
      try {
        this.auditEvents = await api('GET', '/api/audit?' + params.toString());
      } catch (e) {
        if (!this.handleAuthError(e)) this.toast('error', 'Audit load failed', e.message);
      } finally {
        this.auditLoading = false;
      }
    },

    async loadProfile() {
      if (!this.profileJobName) return;
      this.profileLoading = true;
      this.profileData = null;
      this.suggestedRules = null;
      try {
        this.profileData = await api('GET', `/api/jobs/${encodeURIComponent(this.profileJobName)}/profile`);
      } catch (e) {
        if (e.message && e.message.includes('404')) this.profileData = [];
        else if (!this.handleAuthError(e)) this.toast('error', 'Profile load failed', e.message);
      } finally {
        this.profileLoading = false;
      }
    },

    async suggestDQRules() {
      if (!this.profileJobName) return;
      this.profileLoading = true;
      try {
        const res = await api('POST', `/api/jobs/${encodeURIComponent(this.profileJobName)}/suggest-rules`);
        this.suggestedRules = res.suggested_rules || [];
      } catch (e) {
        if (!this.handleAuthError(e)) this.toast('error', 'Suggest failed', e.message);
      } finally {
        this.profileLoading = false;
      }
    },

    async loadSchemaHistory() {
      if (!this.schemaJobName) return;
      this.schemaLoading = true;
      this.schemaHistory = null;
      try {
        const qs = new URLSearchParams({ environment: this.schemaEnvironment || 'source' });
        this.schemaHistory = await api('GET', `/api/jobs/${encodeURIComponent(this.schemaJobName)}/schema-history?${qs}`);
      } catch (e) {
        if (e.message && e.message.includes('404')) this.schemaHistory = [];
        else if (!this.handleAuthError(e)) this.toast('error', 'Schema history load failed', e.message);
      } finally {
        this.schemaLoading = false;
      }
    },

    async viewRunDetail(runId) {
      try {
        this.selectedRun = await api('GET', `/api/runs/${runId}`);
        this.$nextTick(() => this.renderChart());
      } catch (e) {
        this.toast('error', 'Load failed', e.message);
      }
    },

    async downloadRunCsv(runId) {
      try {
        const { blob, disposition } = await apiBlob(`/api/runs/${runId}/export`);
        const filename = disposition.match(/filename="?([^"]+)"?/)?.[1] || `run_${runId.substring(0,8)}_results.csv`;
        triggerDownload(blob, filename);
      } catch (e) {
        this.toast('error', 'Export failed', e.message);
      }
    },

    async deleteRun(runId) {
      if (!confirm(`Delete run ${runId.substring(0, 8)}…? This cannot be undone.`)) return;
      try {
        await api('DELETE', `/api/runs/${runId}`);
        this.runs = this.runs.filter(r => r.run_id !== runId);
        if (this.selectedRun?.run_id === runId) this.selectedRun = null;
        this.closeRunStream(runId);
        if (this.historySubTab === 'audit') this.loadAudit();
        this.toast('success', 'Run deleted');
      } catch (e) {
        this.toast('error', 'Delete failed', e.message);
      }
    },

    toggleOutcomeOverrideForm(resultId) {
      if (this.outcomeOverrideForms[resultId]?.open) {
        const forms = { ...this.outcomeOverrideForms };
        delete forms[resultId];
        this.outcomeOverrideForms = forms;
        return;
      }
      this.outcomeOverrideForms = {
        ...this.outcomeOverrideForms,
        [resultId]: { open: true, reason: '', saving: false },
      };
    },

    async passWithAgreedActions(runId, result) {
      const form = this.outcomeOverrideForms[result.id];
      const reason = (form?.reason || '').trim();
      if (!reason) {
        this.toast('warn', 'Agreed actions required', 'Enter the actions before marking this test as passed');
        return;
      }
      form.saving = true;
      try {
        const updated = await api('PATCH', `/api/runs/${runId}/results/${result.id}/override`, {
          status: 'PASSED', reason,
        });
        Object.assign(result, updated);
        const forms = { ...this.outcomeOverrideForms };
        delete forms[result.id];
        this.outcomeOverrideForms = forms;
        this.toast('success', 'Test marked as passed', 'Agreed actions recorded');
      } catch (e) {
        form.saving = false;
        this.toast('error', 'Outcome update failed', e.message);
      }
    },

    async removeOutcomeOverride(runId, result) {
      try {
        const updated = await api('DELETE', `/api/runs/${runId}/results/${result.id}/override`);
        Object.assign(result, updated);
        this.toast('success', 'Pass override removed');
      } catch (e) {
        this.toast('error', 'Outcome update failed', e.message);
      }
    },

    selectedResultCount() {
      return Object.values(this.selectedResultIds || {}).filter(Boolean).length;
    },

    toggleResultSelection(resultId) {
      this.selectedResultIds = {
        ...this.selectedResultIds,
        [resultId]: !this.selectedResultIds[resultId],
      };
      if (!this.selectedResultIds[resultId]) {
        const copy = { ...this.selectedResultIds };
        delete copy[resultId];
        this.selectedResultIds = copy;
      }
    },

    clearResultSelection() {
      this.selectedResultIds = {};
    },

    selectAllResults() {
      if (!this.selectedRun || !this.selectedRun.results) return;
      const next = {};
      for (const r of this.selectedRun.results) {
        next[r.id] = true;
      }
      this.selectedResultIds = next;
    },

    deselectAllResults() {
      this.selectedResultIds = {};
    },

    isResultSelected(resultId) {
      return !!this.selectedResultIds[resultId];
    },

    openBulkDecisionForm(mode) {
      this.bulkDecisionForm = { open: true, mode, note: '', saving: false };
    },

    closeBulkDecisionForm() {
      this.bulkDecisionForm = { open: false, mode: null, note: '', saving: false };
    },

    async submitBulkDecision(runId) {
      const { mode, note } = this.bulkDecisionForm;
      const selectedIds = Object.entries(this.selectedResultIds || {})
        .filter(([, v]) => v)
        .map(([k]) => parseInt(k, 10));
      if (!selectedIds.length) return;

      if (mode === 'bulk-accept') {
        const trimmed = (note || '').trim();
        if (!trimmed) {
          this.toast('warn', 'Note required', 'Enter a reason before accepting mismatches');
          return;
        }
        this.bulkDecisionForm.saving = true;
        try {
          const result = await api('POST', `/api/runs/${runId}/results/bulk-accept`, {
            result_ids: selectedIds,
            note: trimmed,
          });
          this.closeBulkDecisionForm();
          this.clearResultSelection();
          await this.viewRunDetail(runId);
          if (result.result_status_updated > 0) {
            this.toast('success', 'Mismatches accepted', `${result.accepted_mismatch_count} accepted, ${result.result_status_updated} tests passed`);
          } else {
            this.toast('success', 'Mismatches accepted', `${result.accepted_mismatch_count} accepted`);
          }
        } catch (e) {
          this.bulkDecisionForm.saving = false;
          this.toast('error', 'Bulk accept failed', e.message);
        }
      } else if (mode === 'bulk-override') {
        const trimmed = (note || '').trim();
        if (!trimmed) {
          this.toast('warn', 'Reason required', 'Enter agreed actions before marking tests as passed');
          return;
        }
        this.bulkDecisionForm.saving = true;
        try {
          const updated = await api('POST', `/api/runs/${runId}/results/bulk-override`, {
            result_ids: selectedIds,
            reason: trimmed,
          });
          this.closeBulkDecisionForm();
          this.clearResultSelection();
          await this.viewRunDetail(runId);
          this.toast('success', 'Tests marked as passed', `${updated.length} tests overridden`);
        } catch (e) {
          this.bulkDecisionForm.saving = false;
          this.toast('error', 'Bulk override failed', e.message);
        }
      }
    },

    renderChart() {
      const canvas = document.getElementById('runChart');
      if (!canvas || !this.selectedRun) return;
      if (this.chartInstance) this.chartInstance.destroy();
      const r = this.selectedRun;
      this.chartInstance = new Chart(canvas, {
        type: 'doughnut',
        data: {
          labels: ['Passed', 'Failed', 'Slow', 'Error'],
          datasets: [{
            data: [r.passed || 0, r.failed || 0, r.slow || 0, r.error || 0],
            backgroundColor: ['#34d399', '#fb7185', '#fbbf24', '#38bdf8'],
            borderColor: '#0d0f12',
            borderWidth: 2,
          }],
        },
        options: {
          cutout: '72%',
          plugins: {
            legend: {
              position: 'bottom',
              labels: {
                boxWidth: 10,
                color: '#c7d0dc',
                font: { size: 11 },
              },
            },
          },
        },
      });
    },

    // ===========================================================
    // INLINE SAMPLE ROWS EXPAND (rows actually read from the source)
    // ===========================================================
    toggleSampleRowsExpand(resultId) {
      this.expandedSampleRows = {
        ...this.expandedSampleRows,
        [resultId]: !this.expandedSampleRows[resultId],
      };
    },

    sampleRowColumns(rows) {
      return rows && rows.length ? Object.keys(rows[0]) : [];
    },

    // ===========================================================
    // INLINE MISMATCH EXPAND
    // ===========================================================
    async toggleMismatchExpand(runId, result) {
      if (this.expandedMismatches[result.id] !== undefined) {
        const copy = { ...this.expandedMismatches };
        delete copy[result.id];
        this.expandedMismatches = copy;
        const offCopy = { ...this.expandedMismatchOffset };
        delete offCopy[result.id];
        this.expandedMismatchOffset = offCopy;
        return;
      }
      this.expandingMismatch = { ...this.expandingMismatch, [result.id]: true };
      try {
        const rows = await api('GET',
          `/api/runs/${runId}/results/${result.id}/mismatches?limit=50&offset=0`);
        this.expandedMismatches = { ...this.expandedMismatches, [result.id]: rows };
        this.expandedMismatchOffset = { ...this.expandedMismatchOffset, [result.id]: 0 };
      } catch (e) {
        this.toast('error', 'Load mismatches failed', e.message);
      } finally {
        const copy = { ...this.expandingMismatch };
        delete copy[result.id];
        this.expandingMismatch = copy;
      }
    },

    async loadMoreInlineMismatches(runId, result) {
      const nextOffset = (this.expandedMismatchOffset[result.id] || 0) + 50;
      this.expandingMismatch = { ...this.expandingMismatch, [result.id]: true };
      try {
        const rows = await api('GET',
          `/api/runs/${runId}/results/${result.id}/mismatches?limit=50&offset=${nextOffset}`);
        this.expandedMismatches = {
          ...this.expandedMismatches,
          [result.id]: [...(this.expandedMismatches[result.id] || []), ...rows],
        };
        this.expandedMismatchOffset = { ...this.expandedMismatchOffset, [result.id]: nextOffset };
      } catch (e) {
        this.toast('error', 'Load mismatches failed', e.message);
      } finally {
        const copy = { ...this.expandingMismatch };
        delete copy[result.id];
        this.expandingMismatch = copy;
      }
    },

    // ===========================================================
    // BASELINE
    // ===========================================================
    async setBaseline(runId) {
      try {
        await api('POST', `/api/runs/${runId}/set-baseline`);
        await this.loadRuns();
        if (this.selectedRun?.run_id === runId) await this.viewRunDetail(runId);
        this.toast('success', 'Baseline pinned', `Run ${runId.substring(0,8)}… is now the baseline`);
      } catch (e) {
        this.toast('error', 'Baseline failed', e.message);
      }
    },

    badgeUrl(runId) {
      return (window.location.origin + '/api/runs/' + runId + '/badge');
    },

    async copyBadgeUrl(runId) {
      try {
        await navigator.clipboard.writeText(this.badgeUrl(runId));
        this.toast('success', 'Copied', 'Badge URL copied to clipboard');
      } catch {
        this.toast('warn', 'Copy failed', 'Use the URL field manually');
      }
    },

    // ===========================================================
    // TRENDS
    // ===========================================================
    async loadTrends() {
      if (!this.trendsJobName) return;
      this.trendsLoading = true;
      this.trendsData = null;
      try {
        const qs = new URLSearchParams({
          job_name: this.trendsJobName,
          metric: this.trendsMetric,
          window: String(this.trendsWindow),
        });
        this.trendsData = await api('GET', '/api/runs/trends?' + qs.toString());
        this.$nextTick(() => this.renderTrendsChart());
      } catch (e) {
        this.toast('error', 'Trends load failed', e.message);
      } finally {
        this.trendsLoading = false;
      }
    },

    renderTrendsChart() {
      const canvas = document.getElementById('trendsChart');
      if (!canvas || !this.trendsData?.points?.length) return;
      if (this.trendsChartInstance) this.trendsChartInstance.destroy();
      const pts = this.trendsData.points;
      this.trendsChartInstance = new Chart(canvas, {
        type: 'line',
        data: {
          labels: pts.map(p => p.date),
          datasets: [{
            label: this.trendsMetric,
            data: pts.map(p => p.value),
            borderColor: this.trendsData.drift_detected ? '#fb7185' : '#6366f1',
            backgroundColor: 'transparent',
            pointRadius: 3,
            tension: 0.3,
          }],
        },
        options: {
          responsive: true,
          plugins: {
            legend: { display: false },
            tooltip: { mode: 'index', intersect: false },
          },
          scales: {
            x: { ticks: { color: '#94a3b8', maxTicksLimit: 7 }, grid: { color: '#1e2533' } },
            y: { ticks: { color: '#94a3b8' }, grid: { color: '#1e2533' } },
          },
        },
      });
    },

    // ===========================================================
    // MISMATCH DISTRIBUTION
    // ===========================================================
    async loadMismatchDist(runId, result) {
      if (this.mismatchDist[result.id]) return;
      try {
        const data = await api('GET', `/api/runs/${runId}/results/${result.id}/mismatch-distribution`);
        this.mismatchDist = { ...this.mismatchDist, [result.id]: data.distribution };
      } catch {
        this.mismatchDist = { ...this.mismatchDist, [result.id]: [] };
      }
    },

    async loadSegmentDrill(runId, result, segmentColumn) {
      const key = result.id + ':' + segmentColumn;
      this.segmentDrillLoading = { ...this.segmentDrillLoading, [key]: true };
      try {
        const data = await api('POST', `/api/runs/${runId}/results/${result.id}/drilldown`,
                               { segment_column: segmentColumn });
        this.segmentDrill = { ...this.segmentDrill, [key]: data.rows };
      } catch (e) {
        if (!this.handleAuthError(e)) this.toast('error', 'Drill-down failed', e.message);
      } finally {
        this.segmentDrillLoading = { ...this.segmentDrillLoading, [key]: false };
      }
    },

    segmentMax(rows) {
      return Math.max(1, ...(rows || []).map(r => r.mismatch_count));
    },

    // NB: keep this expression dot-free where it's bound via `:disabled` in
    // index.html — Alpine's x-bind coerces an `undefined` result to `""`
    // whenever the *expression text* contains a literal `.` (a heuristic for
    // dotted-path form bindings), and `""` is not null/undefined/false, so a
    // boolean attribute like `disabled` would incorrectly get set permanently.
    isSegDrillBusy(result, segCol) {
      return !!this.segmentDrillLoading[result.id + ':' + segCol];
    },

    // ===========================================================
    // LINEAGE
    // ===========================================================
    async loadLineage() {
      this.lineageLoading = true;
      try {
        this.lineageGraph = await api('GET', '/api/lineage/jobs');
      } catch (e) {
        if (!this.handleAuthError(e)) this.toast('error', 'Lineage load failed', e.message);
      } finally {
        this.lineageLoading = false;
      }
    },

    lineageSvg() {
      if (!this.lineageGraph?.nodes?.length) return '';
      const nodes = this.lineageGraph.nodes;
      const edges = this.lineageGraph.edges;
      const W = 140, H = 40, HGAP = 180, VGAP = 70, PAD = 20;

      // Assign layers via topological sort
      const layer = {};
      const inDeg = {};
      nodes.forEach(n => { inDeg[n.name] = 0; layer[n.name] = 0; });
      edges.forEach(e => { inDeg[e.to] = (inDeg[e.to] || 0) + 1; });
      const queue = nodes.filter(n => !inDeg[n.name]).map(n => n.name);
      while (queue.length) {
        const cur = queue.shift();
        edges.filter(e => e.from === cur).forEach(e => {
          layer[e.to] = Math.max(layer[e.to] || 0, (layer[cur] || 0) + 1);
          inDeg[e.to]--;
          if (inDeg[e.to] === 0) queue.push(e.to);
        });
      }

      // Position nodes
      const byLayer = {};
      nodes.forEach(n => {
        const l = layer[n.name] || 0;
        if (!byLayer[l]) byLayer[l] = [];
        byLayer[l].push(n.name);
      });
      const pos = {};
      Object.entries(byLayer).forEach(([l, names]) => {
        names.forEach((name, i) => {
          pos[name] = { x: PAD + Number(l) * HGAP, y: PAD + i * VGAP };
        });
      });
      const maxX = Math.max(...Object.values(pos).map(p => p.x)) + W + PAD;
      const maxY = Math.max(...Object.values(pos).map(p => p.y)) + H + PAD;

      let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${maxX}" height="${maxY}" style="font-family:monospace;font-size:11px">`;
      svg += `<defs><marker id="arr" markerWidth="8" markerHeight="8" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#6366f1"/></marker></defs>`;
      edges.forEach(e => {
        if (!pos[e.from] || !pos[e.to]) return;
        const x1 = pos[e.from].x + W, y1 = pos[e.from].y + H / 2;
        const x2 = pos[e.to].x, y2 = pos[e.to].y + H / 2;
        svg += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#6366f1" stroke-width="1.5" marker-end="url(#arr)"/>`;
      });
      nodes.forEach(n => {
        const { x, y } = pos[n.name];
        const safeName = (n.name || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        svg += `<rect x="${x}" y="${y}" width="${W}" height="${H}" rx="4" fill="#1e2533" stroke="#334155" stroke-width="1"/>`;
        svg += `<text x="${x + W/2}" y="${y + H/2 + 4}" text-anchor="middle" fill="#c7d0dc">${safeName}</text>`;
      });
      svg += '</svg>';
      return svg;
    },

    // ===========================================================
    // COVERAGE
    // ===========================================================
    async loadCoverage() {
      this.coverageLoading = true;
      try {
        this.coverageData = await api('GET', '/api/coverage');
        this.flakyData = await api('GET', '/api/coverage/flaky');
      } catch (e) {
        if (!this.handleAuthError(e)) this.toast('error', 'Coverage load failed', e.message);
      } finally {
        this.coverageLoading = false;
      }
    },

    coverageColumns(table) {
      const cols = table.columns || [];
      return this.coverageGapsOnly ? cols.filter(c => c.level === 'untested') : cols;
    },

    coverageLevelClass(level) {
      return {
        tested: 'bg-emerald-100 text-emerald-700',
        observed: 'bg-amber-100 text-amber-700',
        untested: 'bg-rose-100 text-rose-700',
      }[level] || 'bg-slate-100 text-slate-600';
    },

    };
  };
})(window);
