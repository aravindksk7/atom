(function (global) {
  'use strict';
  // Compare feature slice (Compare tab + Schema-Explorer helpers used by it).
  // Merged into the Alpine component via the FEATURE_SLICES reduce in app.js.
  global.ETL_FEATURE_COMPARE = function () {
    return {
      // ===== STATE (extracted from app.js) =====
    // -----------------------------------------------------------
    // Compare runs
    // -----------------------------------------------------------
    compareMode: false,
    compareRunA: '',
    compareRunB: '',
    compareLoading: false,
    compareResult: null,

    // -----------------------------------------------------------
    // Compare tab
    // -----------------------------------------------------------
    compareSubTab: 'bo',
    reconMode: 'stored',

    boSourceAType: 'live',
    boSourceBType: 'upload',
    boSourceA: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source A', endpointName: '' },
    boSourceB: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source B', endpointName: '' },
    boDocsA: [],
    boDocsB: [],
    boReportsA: [],
    boReportsB: [],
    boKeyColumns: '',
    boExcludeColumns: '',
    boCompareLoading: false,
    boCompareRunId: null,
    boCompareResult: null,
    boComparePollInterval: null,

    dualEnvConfigA: '',
    dualEnvConfigB: '',
    dualEnvSourceEnvA: '',
    dualEnvTargetEnvA: '',
    dualEnvSourceEnvB: '',
    dualEnvTargetEnvB: '',
    dualEnvJobs: [],
    dualEnvLoading: false,
    dualEnvPairId: null,
    dualEnvPollInterval: null,
    dualEnvResult: null,

    fileSourceAType: 'run',
    fileSourceBType: 'upload',
    fileLabelA: 'Source A',
    fileLabelB: 'Production Report',
    fileRunIdA: '',
    fileRunIdB: '',
    filePathA: '',
    fileB64A: '',
    fileNameA: '',
    filePathB: '',
    fileB64B: '',
    fileNameB: '',
    fileCompareLoading: false,
    fileCompareResult: null,
    fileExpandedDiffs: {},
    fileCompareKeyColumns: '',
    fileCompareExcludeColumns: '',

    sqlConfigA: '',
    sqlConfigB: '',
    sqlConnectionA: null,
    sqlConnectionB: null,
    sqlQueryA: 'SELECT * FROM ',
    sqlQueryB: 'SELECT * FROM ',
    sqlLabelA: 'Source A',
    sqlLabelB: 'Source B',
    sqlKeyColumns: '',
    sqlExcludeColumns: '',
    sqlCompareLoading: false,
    sqlCompareResult: null,
    sqlExpandedDiffs: {},
    sqlDiffFilter: {},
    fileDiffFilter: {},
    expandedCell: {},

    // Advanced compare options (shared shape for BO, File, SQL)
    boAdvancedOpen: false,
    boFloatTolerance: '1e-9',
    boColumnTolerances: '',
    boDatetimeTolerance: 0,
    boCaseInsensitiveColumns: '',
    boWhitespaceNormalizeColumns: '',
    boBackend: 'pandas',
    boMismatchRowLimit: 5000,
    boSampleFrac: '',
    boParallelColumns: false,

    fileAdvancedOpen: false,
    fileFloatTolerance: '1e-9',
    fileColumnTolerances: '',
    fileDatetimeTolerance: 0,
    fileCaseInsensitiveColumns: '',
    fileWhitespaceNormalizeColumns: '',
    fileBackend: 'pandas',
    fileMismatchRowLimit: 5000,
    fileSampleFrac: '',
    fileParallelColumns: false,

    sqlAdvancedOpen: false,
    sqlFloatTolerance: '1e-9',
    sqlColumnTolerances: '',
    sqlDatetimeTolerance: 0,
    sqlCaseInsensitiveColumns: '',
    sqlWhitespaceNormalizeColumns: '',
    sqlBackend: 'pandas',
    sqlMismatchRowLimit: 5000,
    sqlSampleFrac: '',
    sqlParallelColumns: false,

    // Column Stats
    colStatsSourceAType: 'upload',
    colStatsSourceBType: 'upload',
    colStatsSourceA: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source A', endpointName: '' },
    colStatsSourceB: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source B', endpointName: '' },
    colStatsQueryName: 'stats_compare',
    colStatsFloatTol: '1e-9',
    colStatsRowCountTol: 0,
    colStatsLoading: false,
    colStatsResult: null,

    // Mismatch Diff
    mismatchDiffRunIdA: '',
    mismatchDiffRunIdB: '',
    mismatchDiffQueryName: '',
    mismatchDiffRunLabelA: 'Run A',
    mismatchDiffRunLabelB: 'Run B',
    mismatchDiffLoading: false,
    mismatchDiffResult: null,
    mismatchDiffVisible: { new: 50, resolved: 50, persistent: 50 },

    // Full differences export jobs
    differenceExports: {},
    columnStatFilters: {},
    columnStatSort: {},
      // ===== METHODS (extracted from app.js) =====
    // ===========================================================
    // COMPARE RUNS
    // ===========================================================
    async loadCompare() {
      if (!this.compareRunA || !this.compareRunB) return;
      if (this.compareRunA === this.compareRunB) {
        this.compareResult = null;
        this.toast('warn', 'Same run', 'Select two different runs to compare');
        return;
      }
      this.compareLoading = true;
      this.compareResult = null;
      try {
        this.compareResult = await api('GET',
          `/api/runs/compare?run_a=${encodeURIComponent(this.compareRunA)}&run_b=${encodeURIComponent(this.compareRunB)}`);
      } catch (e) {
        this.toast('error', 'Compare failed', e.message);
      } finally {
        this.compareLoading = false;
      }
    },

    compareDelta(test) {
      const a = test.status_a ? String(test.status_a).toUpperCase() : null;
      const b = test.status_b ? String(test.status_b).toUpperCase() : null;
      if (!a) return { label: 'New in B', cls: 'badge-sky' };
      if (!b) return { label: 'Removed', cls: 'badge-gray' };
      if (a === 'PASSED' && b !== 'PASSED') return { label: '▼ Regressed', cls: 'badge-rose' };
      if (a !== 'PASSED' && b === 'PASSED') return { label: '▲ Improved', cls: 'badge-green' };
      if (a === b) return { label: '— Same', cls: 'badge-gray' };
      return { label: '~ Changed', cls: 'badge-amber' };
    },

    // ===========================================================
    // COMPARE TAB
    // ===========================================================
    async loadCompareBODocuments(side) {
      const src = side === 'a' ? this.boSourceA : this.boSourceB;
      if (!src.configId) return;
      try {
        const docs = await api('GET', `/api/adapters/sap-bo/documents?config_id=${src.configId}`);
        if (side === 'a') {
          this.boDocsA = docs;
          this.boReportsA = [];
        } else {
          this.boDocsB = docs;
          this.boReportsB = [];
        }
      } catch (e) {
        this.toast('error', 'Load documents failed', e.message);
      }
    },

    async loadCompareBOReports(side) {
      const src = side === 'a' ? this.boSourceA : this.boSourceB;
      if (!src.configId || !src.docId) return;
      try {
        const reports = await api('GET',
          `/api/adapters/sap-bo/documents/${encodeURIComponent(src.docId)}/reports?config_id=${src.configId}`);
        if (side === 'a') this.boReportsA = reports;
        else this.boReportsB = reports;
      } catch (e) {
        this.toast('error', 'Load reports failed', e.message);
      }
    },

    handleBOFileUpload(event, side, namespace) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        const bytes = new Uint8Array(e.target.result);
        let binary = '';
        for (let i = 0; i < bytes.length; i += 8192) {
          binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
        }
        let src;
        if (namespace === 'colStats') {
          src = side === 'a' ? this.colStatsSourceA : this.colStatsSourceB;
        } else {
          src = side === 'a' ? this.boSourceA : this.boSourceB;
        }
        src.fileB64 = btoa(binary);
        src.fileName = file.name;
      };
      reader.readAsArrayBuffer(file);
    },

    async openSchemaExplorer(cfg) {
      if (this.schemaExplorerId === cfg.id) {
        this.closeSchemaExplorer();
        return;
      }
      this.schemaExplorerId = cfg.id;
      this.schemaExplorerData = [];
      this.schemaExpandedSchemas = {};
      this.schemaExpandedTables = {};
      this.schemaTablePreviews = {};
      this.schemaExplorerLoading = true;
      try {
        this.schemaExplorerData = await api('GET', `/api/configs/${cfg.id}/schema`);
        const schemas = [...new Set(this.schemaExplorerData.map(t => t.schema))];
        this.schemaExpandedSchemas = Object.fromEntries(schemas.map(s => [s, true]));
      } catch (e) {
        this.toast('error', 'Schema load failed', e.message);
        this.schemaExplorerId = null;
      } finally {
        this.schemaExplorerLoading = false;
      }
    },

    closeSchemaExplorer() {
      this.schemaExplorerId = null;
      this.schemaExplorerData = [];
      this.schemaTablePreviews = {};
    },

    toggleSchemaGroup(schema) {
      this.schemaExpandedSchemas[schema] = !this.schemaExpandedSchemas[schema];
    },

    toggleSchemaTable(key) {
      this.schemaExpandedTables[key] = !this.schemaExpandedTables[key];
    },

    async previewSchemaTable(configId, schema, table) {
      const key = `${schema}.${table}`;
      this.schemaTablePreviews = { ...this.schemaTablePreviews, [key]: 'loading' };
      try {
        const result = await api('POST', `/api/configs/${configId}/preview-query`, {
          query: `SELECT * FROM [${schema}].[${table}]`,
          limit: 50,
        });
        this.schemaTablePreviews = { ...this.schemaTablePreviews, [key]: result };
      } catch (e) {
        this.schemaTablePreviews = { ...this.schemaTablePreviews, [key]: `error:${e.message}` };
      }
    },

    useTableInJob(schema, table) {
      sessionStorage.setItem('etl_pending_query', `SELECT * FROM [${schema}].[${table}]`);
      this.activeTab = 'launch';
      this.$nextTick(() => this.openNewJobModal());
      this.toast('info', 'Query pre-filled', 'Finish the job setup');
    },

    handleReconFileUpload(event, side) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        const arr = new Uint8Array(e.target.result);
        let b64 = '';
        const CHUNK = 8192;
        for (let i = 0; i < arr.length; i += CHUNK) {
          b64 += String.fromCharCode(...arr.subarray(i, i + CHUNK));
        }
        b64 = btoa(b64);
        if (side === 'a') {
          this.fileB64A = b64;
          this.fileNameA = file.name;
          this.fileSourceAType = 'upload';
        } else {
          this.fileB64B = b64;
          this.fileNameB = file.name;
          this.fileSourceBType = 'upload';
        }
      };
      reader.readAsArrayBuffer(file);
    },

    _buildBOSource(type, src) {
      if (type === 'live') {
        return {
          source_type: 'live',
          config_id: Number(src.configId),
          doc_id: src.docId || null,
          report_id: src.reportId || null,
          format: 'xlsx',
        };
      }
      if (type === 'path') return { source_type: 'path', file_path: src.filePath };
      if (type === 'api') {
        return {
          source_type: 'api',
          config_id: Number(src.configId),
          api_endpoint_name: src.endpointName,
        };
      }
      return { source_type: 'upload', file_content_b64: src.fileB64, file_name: src.fileName };
    },

    _parseColumnTolerances(raw) {
      const out = {};
      (raw || '').split(',').forEach(part => {
        const [col, val] = part.trim().split(':');
        if (col && val && !isNaN(parseFloat(val))) out[col.trim()] = parseFloat(val.trim());
      });
      return out;
    },

    _buildAdvanced(prefix) {
      const p = prefix;
      const rowLimit = parseInt(this[`${p}MismatchRowLimit`], 10);
      const adv = {
        float_tolerance: parseFloat(this[`${p}FloatTolerance`]) || 1e-9,
        column_tolerances: this._parseColumnTolerances(this[`${p}ColumnTolerances`]),
        datetime_tolerance_seconds: parseFloat(this[`${p}DatetimeTolerance`]) || 0,
        case_insensitive_columns: (this[`${p}CaseInsensitiveColumns`] || '').split(',').map(s => s.trim()).filter(Boolean),
        whitespace_normalize_columns: (this[`${p}WhitespaceNormalizeColumns`] || '').split(',').map(s => s.trim()).filter(Boolean),
        comparison_backend: this[`${p}Backend`] || 'pandas',
        mismatch_row_limit: rowLimit > 0 ? rowLimit : 5000,
        parallel_columns: Boolean(this[`${p}ParallelColumns`]),
        parallel_workers: 4,
      };
      const sf = parseFloat(this[`${p}SampleFrac`]);
      if (sf > 0 && sf <= 1) adv.sample_frac = sf;
      return adv;
    },

    async runBOComparison() {
      this.boCompareLoading = true;
      this.boCompareResult = null;
      if (this.boComparePollInterval) clearInterval(this.boComparePollInterval);
      try {
        const payload = {
          source_a: this._buildBOSource(this.boSourceAType, this.boSourceA),
          source_b: this._buildBOSource(this.boSourceBType, this.boSourceB),
          key_columns: this.boKeyColumns.split(',').map(s => s.trim()).filter(Boolean),
          exclude_columns: this.boExcludeColumns.split(',').map(s => s.trim()).filter(Boolean),
          label_a: this.boSourceA.label || 'Source A',
          label_b: this.boSourceB.label || 'Source B',
          advanced: this._buildAdvanced('bo'),
        };
        const run = await api('POST', '/api/compare/bo-report', payload);
        this.boCompareRunId = run.run_id;
        this.boComparePollInterval = setInterval(() => this._pollBOCompare(), 3000);
        await this._pollBOCompare();
        await this.loadRuns();
      } catch (e) {
        this.toast('error', 'BO comparison failed', e.message);
        this.boCompareLoading = false;
      }
    },

    async _pollBOCompare() {
      if (!this.boCompareRunId) return;
      try {
        const status = await api('GET', `/api/runs/${this.boCompareRunId}/status`);
        if (this.isTerminalStatus(status.status)) {
          clearInterval(this.boComparePollInterval);
          this.boComparePollInterval = null;
          this.boCompareResult = await api('GET', `/api/runs/${this.boCompareRunId}`);
          this.boCompareLoading = false;
          if (this.boSaveAsBaseline && status.status === 'PASSED') {
            try { await api('POST', `/api/runs/${this.boCompareRunId}/set-baseline`); } catch (_) {}
          }
          await this.loadRuns();
        }
      } catch (e) {
        clearInterval(this.boComparePollInterval);
        this.boComparePollInterval = null;
        this.boCompareLoading = false;
      }
    },

    async launchDualEnv() {
      if (!this.dualEnvConfigA || !this.dualEnvConfigB) {
        this.toast('warn', 'Missing config', 'Select configs for both environments');
        return;
      }
      this.dualEnvLoading = true;
      this.dualEnvResult = null;
      this.dualEnvPairId = null;
      if (this.dualEnvPollInterval) clearInterval(this.dualEnvPollInterval);
      try {
        const payload = {
          config_id_a: Number(this.dualEnvConfigA),
          config_id_b: Number(this.dualEnvConfigB),
          source_env_a: this.dualEnvSourceEnvA,
          target_env_a: this.dualEnvTargetEnvA,
          source_env_b: this.dualEnvSourceEnvB,
          target_env_b: this.dualEnvTargetEnvB,
          job_names: this.dualEnvJobs,
          run_settings: this._runSettingsPayload(),
        };
        const launch = await api('POST', '/api/compare/dual-env', payload);
        this.dualEnvPairId = launch.pair_id;
        this.dualEnvPollInterval = setInterval(
          () => this._pollDualEnv(launch.run_id_a, launch.run_id_b),
          3000
        );
        await this._pollDualEnv(launch.run_id_a, launch.run_id_b);
        await this.loadRuns();
      } catch (e) {
        this.toast('error', 'Launch failed', e.message);
        this.dualEnvLoading = false;
      }
    },

    async _pollDualEnv(runIdA, runIdB) {
      if (!this.dualEnvPairId) return;
      try {
        const pair = await api('GET', `/api/compare/pairs/${this.dualEnvPairId}`);
        if (this.isTerminalStatus(pair.run_a.status) && this.isTerminalStatus(pair.run_b.status)) {
          clearInterval(this.dualEnvPollInterval);
          this.dualEnvPollInterval = null;
          this.dualEnvResult = await api('GET', `/api/runs/compare?run_a=${encodeURIComponent(runIdA)}&run_b=${encodeURIComponent(runIdB)}`);
          this.dualEnvLoading = false;
          await this.loadRuns();
        }
      } catch (e) {
        clearInterval(this.dualEnvPollInterval);
        this.dualEnvPollInterval = null;
        this.dualEnvLoading = false;
      }
    },

    async runFileCompare() {
      this.fileCompareLoading = true;
      this.fileCompareResult = null;
      this.fileExpandedDiffs = {};
      try {
        const payload = {
          label_a: this.fileLabelA || 'Source A',
          label_b: this.fileLabelB || 'Production Report',
        };
        if (this.fileCompareKeyColumns.trim()) {
          payload.key_columns = this.fileCompareKeyColumns.split(',').map(s => s.trim()).filter(Boolean);
        }
        if (this.fileCompareExcludeColumns.trim()) {
          payload.exclude_columns = this.fileCompareExcludeColumns.split(',').map(s => s.trim()).filter(Boolean);
        }
        const applySource = (side, type, runId, path, content, fname) => {
          const label = side === 'a' ? 'Source A' : 'Source B';
          const suffix = side === 'a' ? '' : '_b';
          if (type === 'run') {
            if (!runId) throw new Error(`${label}: select a stored run`);
            payload[`stored_run_id${suffix}`] = runId;
          } else if (type === 'path') {
            if (!(path || '').trim()) throw new Error(`${label}: enter a file path`);
            payload[`file_${side}_path`] = path.trim();
          } else {
            if (!content) throw new Error(`${label}: upload a file`);
            payload[`file_${side}_content_b64`] = content;
            if (fname) payload[`file_${side}_name`] = fname;
          }
        };
        applySource('a', this.fileSourceAType, this.fileRunIdA, this.filePathA, this.fileB64A, this.fileNameA);
        applySource('b', this.fileSourceBType, this.fileRunIdB, this.filePathB, this.fileB64B, this.fileNameB);
        payload.advanced = this._buildAdvanced('file');
        const run = await api('POST', '/api/compare/recon-file', payload);
        const poll = setInterval(async () => {
          try {
            const st = await api('GET', `/api/runs/${run.run_id}/status`);
            if (this.isTerminalStatus(st.status)) {
              clearInterval(poll);
              this.fileCompareResult = await api('GET', `/api/runs/${run.run_id}`);
              this.fileCompareLoading = false;
              await this.loadRuns();
            }
          } catch (e) {
            clearInterval(poll);
            this.fileCompareLoading = false;
          }
        }, 3000);
      } catch (e) {
        this.toast('error', 'File compare failed', e.message);
        this.fileCompareLoading = false;
      }
    },

    sqlConfigAConnections() {
      const cfg = this.configs.find(c => String(c.id) === String(this.sqlConfigA));
      if (!cfg || !cfg.config_data || !cfg.config_data.connections) return [];
      return Object.keys(cfg.config_data.connections);
    },

    sqlConfigBConnections() {
      const cfg = this.configs.find(c => String(c.id) === String(this.sqlConfigB));
      if (!cfg || !cfg.config_data || !cfg.config_data.connections) return [];
      return Object.keys(cfg.config_data.connections);
    },

    async runSQLComparison() {
      if (!this.sqlConfigA) { this.toast('warn', 'Config A required', 'Select a config for Source A'); return; }
      if (!this.sqlConfigB) { this.toast('warn', 'Config B required', 'Select a config for Source B'); return; }
      if (!this.sqlQueryA.trim()) { this.toast('warn', 'Query A required', 'Enter a SQL query for Source A'); return; }
      if (!this.sqlQueryB.trim()) { this.toast('warn', 'Query B required', 'Enter a SQL query for Source B'); return; }
      this.sqlCompareLoading = true;
      this.sqlCompareResult = null;
      this.sqlExpandedDiffs = {};
      try {
        const payload = {
          config_id_a: parseInt(this.sqlConfigA),
          config_id_b: parseInt(this.sqlConfigB),
          query_a: this.sqlQueryA.trim(),
          query_b: this.sqlQueryB.trim(),
          label_a: this.sqlLabelA || 'Source A',
          label_b: this.sqlLabelB || 'Source B',
          connection_a: this.sqlConnectionA || null,
          connection_b: this.sqlConnectionB || null,
          key_columns: this.sqlKeyColumns.split(',').map(s => s.trim()).filter(Boolean),
          exclude_columns: this.sqlExcludeColumns.split(',').map(s => s.trim()).filter(Boolean),
          advanced: this._buildAdvanced('sql'),
        };
        const run = await api('POST', '/api/compare/sql', payload);
        const poll = setInterval(async () => {
          try {
            const st = await api('GET', `/api/runs/${run.run_id}/status`);
            if (this.isTerminalStatus(st.status)) {
              clearInterval(poll);
              this.sqlCompareResult = await api('GET', `/api/runs/${run.run_id}`);
              this.sqlCompareLoading = false;
              await this.loadRuns();
            }
          } catch (e) {
            clearInterval(poll);
            this.sqlCompareLoading = false;
          }
        }, 3000);
      } catch (e) {
        this.toast('error', 'SQL compare failed', e.message);
        this.sqlCompareLoading = false;
      }
    },

    filteredDiff(diffs, filterKey, filterState) {
      const f = filterState[filterKey] || {};
      if (!f.type && !f.col && !f.search) return diffs;
      return (diffs || []).filter(m => {
        if (f.type && m.mismatch_type !== f.type) return false;
        if (f.col  && m.column_name !== f.col)   return false;
        if (f.search) {
          const key = JSON.stringify(m.key_values || {}).toLowerCase();
          if (!key.includes(f.search.toLowerCase())) return false;
        }
        return true;
      });
    },

    colSummary(diffs) {
      const counts = {};
      (diffs || []).forEach(m => {
        const c = m.column_name || '(none)';
        counts[c] = (counts[c] || 0) + 1;
      });
      const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 8);
      const max = sorted[0]?.[1] || 1;
      return sorted.map(([col, count]) => ({ col, count, pct: Math.round(count / max * 100) }));
    },

    async toggleSQLDiff(r) {
      const name = r.query_name;
      const runId = this.sqlCompareResult?.run_id;
      if (!runId) return;
      const cur = this.sqlExpandedDiffs[name];
      if (cur) {
        this.sqlExpandedDiffs = { ...this.sqlExpandedDiffs, [name]: { ...cur, open: !cur.open } };
        return;
      }
      this.sqlExpandedDiffs = { ...this.sqlExpandedDiffs, [name]: { open: true, loading: true, loadingMore: false, data: [], error: null, hasMore: false, offset: 0, resultId: r.id } };
      try {
        const rows = await api('GET', `/api/runs/${runId}/results/${r.id}/mismatches?limit=100&offset=0`);
        this.sqlExpandedDiffs = { ...this.sqlExpandedDiffs, [name]: { open: true, loading: false, loadingMore: false, data: rows || [], error: null, hasMore: (rows || []).length === 100, offset: 0, resultId: r.id } };
      } catch (e) {
        this.sqlExpandedDiffs = { ...this.sqlExpandedDiffs, [name]: { open: true, loading: false, loadingMore: false, data: [], error: e.message, hasMore: false, offset: 0, resultId: r.id } };
      }
    },

    async loadMoreSQLDiffs(name) {
      const runId = this.sqlCompareResult?.run_id;
      const cur = this.sqlExpandedDiffs[name];
      if (!runId || !cur || cur.loadingMore) return;
      const nextOffset = (cur.offset || 0) + 100;
      this.sqlExpandedDiffs = { ...this.sqlExpandedDiffs, [name]: { ...cur, loadingMore: true } };
      try {
        const rows = await api('GET', `/api/runs/${runId}/results/${cur.resultId}/mismatches?limit=100&offset=${nextOffset}`);
        this.sqlExpandedDiffs = {
          ...this.sqlExpandedDiffs,
          [name]: { ...cur, loadingMore: false, data: [...cur.data, ...(rows || [])], offset: nextOffset, hasMore: (rows || []).length === 100 },
        };
      } catch (e) {
        this.sqlExpandedDiffs = { ...this.sqlExpandedDiffs, [name]: { ...cur, loadingMore: false, error: e.message || 'Failed to load more' } };
      }
    },

    async toggleFileDiff(r) {
      const name = r.query_name;
      const runId = this.fileCompareResult?.run_id;
      if (!runId) return;
      const cur = this.fileExpandedDiffs[name];
      if (cur) {
        this.fileExpandedDiffs = { ...this.fileExpandedDiffs, [name]: { ...cur, open: !cur.open } };
        return;
      }
      this.fileExpandedDiffs = { ...this.fileExpandedDiffs, [name]: { open: true, loading: true, loadingMore: false, data: [], page: 0, hasMore: false, resultId: r.id, error: '' } };
      try {
        const data = await api('GET', `/api/runs/${runId}/results/${r.id}/mismatches?limit=100&offset=0`);
        const rows = data || [];
        this.fileExpandedDiffs = { ...this.fileExpandedDiffs, [name]: { open: true, loading: false, loadingMore: false, data: rows, page: 0, hasMore: rows.length === 100, resultId: r.id, error: '' } };
      } catch (e) {
        this.fileExpandedDiffs = { ...this.fileExpandedDiffs, [name]: { open: true, loading: false, loadingMore: false, data: [], page: 0, hasMore: false, resultId: r.id, error: e.message || 'Failed to load diff details' } };
      }
    },

    async loadMoreFileDiffs(name) {
      const runId = this.fileCompareResult?.run_id;
      const cur = this.fileExpandedDiffs[name];
      if (!runId || !cur || cur.loadingMore) return;
      const nextPage = cur.page + 1;
      const offset = nextPage * 100;
      this.fileExpandedDiffs = { ...this.fileExpandedDiffs, [name]: { ...cur, loadingMore: true } };
      try {
        const data = await api('GET', `/api/runs/${runId}/results/${cur.resultId}/mismatches?limit=100&offset=${offset}`);
        const rows = data || [];
        this.fileExpandedDiffs = {
          ...this.fileExpandedDiffs,
          [name]: { ...cur, loadingMore: false, data: [...cur.data, ...rows], page: nextPage, hasMore: rows.length === 100 },
        };
      } catch (e) {
        this.fileExpandedDiffs = { ...this.fileExpandedDiffs, [name]: { ...cur, loadingMore: false, error: e.message || 'Failed to load more' } };
      }
    },

    async runColumnStats() {
      this.colStatsLoading = true;
      this.colStatsResult = null;
      try {
        const payload = {
          source_a: this._buildBOSource(this.colStatsSourceAType, this.colStatsSourceA),
          source_b: this._buildBOSource(this.colStatsSourceBType, this.colStatsSourceB),
          label_a: this.colStatsSourceA.label || 'Source A',
          label_b: this.colStatsSourceB.label || 'Source B',
          query_name: this.colStatsQueryName || 'stats_compare',
          float_tolerance: parseFloat(this.colStatsFloatTol) || 1e-9,
          row_count_tolerance: parseInt(this.colStatsRowCountTol) || 0,
        };
        if (this.colStatsSourceA.docId) payload.doc_id = this.colStatsSourceA.docId;
        if (this.colStatsSourceA.reportId) payload.report_id = this.colStatsSourceA.reportId;
        this.colStatsResult = await api('POST', '/api/compare/column-stats', payload);
      } catch (e) {
        this.toast('error', 'Column stats failed', e.message);
      } finally {
        this.colStatsLoading = false;
      }
    },

    async runMismatchDiff() {
      if (!this.mismatchDiffRunIdA || !this.mismatchDiffRunIdB) {
        this.toast('warn', 'Run IDs required', 'Enter both Run A and Run B IDs');
        return;
      }
      this.mismatchDiffLoading = true;
      this.mismatchDiffResult = null;
      this.mismatchDiffVisible = { new: 50, resolved: 50, persistent: 50 };
      try {
        const payload = {
          run_id_a: this.mismatchDiffRunIdA.trim(),
          run_id_b: this.mismatchDiffRunIdB.trim(),
          run_a_label: this.mismatchDiffRunLabelA || 'Run A',
          run_b_label: this.mismatchDiffRunLabelB || 'Run B',
        };
        if (this.mismatchDiffQueryName.trim()) payload.query_name = this.mismatchDiffQueryName.trim();
        this.mismatchDiffResult = await api('POST', '/api/compare/mismatch-diff', payload);
      } catch (e) {
        this.toast('error', 'Mismatch diff failed', e.message);
      } finally {
        this.mismatchDiffLoading = false;
      }
    },

    async downloadCompareResults(format) {
      const runId = this.fileCompareResult?.run_id;
      if (!runId) return;
      try {
        const { blob, disposition } = await apiBlob(`/api/runs/${runId}/mismatches/download?format=${format}`);
        const fallback = `compare_results_${runId}.${format === 'xlsx' ? 'xlsx' : format}`;
        const filename = disposition.match(/filename="?([^"]+)"?/)?.[1] || fallback;
        triggerDownload(blob, filename);
      } catch (e) {
        this.toast('error', 'Download failed', e.message);
      }
    },

    showMoreMismatchDiff(kind) {
      this.mismatchDiffVisible = {
        ...this.mismatchDiffVisible,
        [kind]: (this.mismatchDiffVisible[kind] || 50) + 50,
      };
    },

    differenceExportKey(runId, format) {
      return `${runId}:${format}`;
    },

    differenceExportState(runId, format) {
      return this.differenceExports[this.differenceExportKey(runId, format)] || {};
    },

    differenceExportLabel(runId, format) {
      const st = this.differenceExportState(runId, format);
      if (st.status === 'PENDING' || st.status === 'RUNNING') return 'Preparing...';
      if (st.status === 'FAILED') return 'Retry';
      return format.toUpperCase();
    },

    isDifferenceExportBusy(runId, format) {
      const st = this.differenceExportState(runId, format);
      return st.status === 'PENDING' || st.status === 'RUNNING';
    },

    async downloadAllDifferences(runId, format) {
      if (!runId || this.isDifferenceExportBusy(runId, format)) return;
      const key = this.differenceExportKey(runId, format);
      this.differenceExports = { ...this.differenceExports, [key]: { status: 'CHECKING' } };
      try {
        const token = normalizeToken(sessionStorage.getItem('etl_token'));
        const headers = token ? { Authorization: 'Bearer ' + token } : {};
        const resp = await fetch(API + `/api/runs/${runId}/differences/download?format=${format}`, { headers });
        if (resp.status === 202) {
          const info = await resp.json();
          this.differenceExports = { ...this.differenceExports, [key]: { status: 'PENDING', info } };
          const job = await api('POST', `/api/runs/${runId}/exports`, { format });
          this.differenceExports = { ...this.differenceExports, [key]: job };
          await this.pollDifferenceExport(runId, job.export_id, format);
          return;
        }
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: resp.statusText }));
          throw new Error(apiErrorMessage(err.detail ?? err, resp.statusText));
        }
        const blob = await resp.blob();
        const disposition = resp.headers.get('content-disposition') || '';
        const fallback = `all_differences_${runId}.${format === 'parquet' ? 'parquet' : 'csv'}`;
        const filename = disposition.match(/filename="?([^"]+)"?/)?.[1] || fallback;
        triggerDownload(blob, filename);
        this.differenceExports = { ...this.differenceExports, [key]: { status: 'DOWNLOADED' } };
      } catch (e) {
        this.differenceExports = { ...this.differenceExports, [key]: { status: 'FAILED', error_message: e.message } };
        this.toast('error', 'Full export failed', e.message);
      }
    },

    async downloadFullHtmlReport(runId) {
      if (!runId || this.isDifferenceExportBusy(runId, 'html')) return;
      let summary;
      try {
        summary = await api('GET', `/api/runs/${runId}/differences/summary`);
      } catch (e) {
        this.toast('error', 'Failed to load mismatch summary', e.message);
        return;
      }
      const estMb = ((summary.total_issues || 0) * 1.8 / 1024).toFixed(1);
      if (!confirm(`This run has ${summary.total_issues} total mismatches (~${estMb} MB estimated). Continue?`)) return;
      const key = this.differenceExportKey(runId, 'html');
      this.differenceExports = { ...this.differenceExports, [key]: { status: 'PENDING' } };
      try {
        const job = await api('POST', `/api/runs/${runId}/exports`, { format: 'html' });
        this.differenceExports = { ...this.differenceExports, [key]: job };
        await this.pollDifferenceExport(runId, job.export_id, 'html');
      } catch (e) {
        this.differenceExports = { ...this.differenceExports, [key]: { status: 'FAILED', error_message: e.message } };
        this.toast('error', 'Full report download failed', e.message);
      }
    },

    async pollDifferenceExport(runId, exportId, format) {
      const key = this.differenceExportKey(runId, format);
      for (let attempt = 0; attempt < 240; attempt++) {
        const status = await api('GET', `/api/runs/${runId}/exports/${exportId}`);
        this.differenceExports = { ...this.differenceExports, [key]: status };
        if (status.status === 'COMPLETED') {
          const { blob, disposition } = await apiBlob(`/api/runs/${runId}/exports/${exportId}/download`);
          const ext = format === 'parquet' ? 'parquet' : format === 'html' ? 'html' : 'csv';
          const fallback = `all_differences_${runId}_${exportId}.${ext}`;
          const filename = disposition.match(/filename="?([^"]+)"?/)?.[1] || fallback;
          triggerDownload(blob, filename);
          this.differenceExports = { ...this.differenceExports, [key]: { ...status, status: 'DOWNLOADED' } };
          return;
        }
        if (status.status === 'FAILED') {
          throw new Error(status.error_message || 'Export job failed');
        }
        await new Promise(resolve => setTimeout(resolve, 2000));
      }
      throw new Error('Export job timed out');
    },

    };
  };
})(window);