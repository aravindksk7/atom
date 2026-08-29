(function (global) {
  'use strict';
  // Extensions the recon-file backend reads as tabular data; anything else on that
  // side is parsed as an HTML report. Mirrors TABULAR_EXTS in
  // api/services/run_data_artifact.py — keep the two lists in step.
  const RECON_TABULAR_EXTS = ['.csv', '.xlsx', '.xls', '.json', '.xml', '.tsv', '.txt'];
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
    compareSourceType: 'aws_glue',
    compareTargetType: 'aws_glue',
    athenaSqlInput: '',
    saveJobModalOpen: false,
    saveJobCompareType: '',
    saveJobName: '',
    saveJobDescription: '',
    saveJobTags: '',
    saveJobError: '',
    saveJobSaving: false,
    // The compare job (job_type 'compare') currently loaded for editing via
    // openCompareForJob, if any -- set by launch.js's openCompareForJob, read
    // by openSaveCompareAsJob/saveCompareAsJob to update it in place (PUT)
    // instead of creating a duplicate (POST).
    editingCompareJob: null,
    reconMode: 'stored',

    boSourceAType: 'live',
    boSourceBType: 'upload',
    boSourceA: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source A', endpointName: '', parameters: [], runId: '', jobName: '' },
    boSourceB: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source B', endpointName: '', parameters: [], runId: '', jobName: '' },
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

    mfCompareLabelA: 'Source A',
    mfCompareLabelB: 'Source B',
    mfCompareSourceMode: 'files', // 'files' | 'run'
    mfCompareRunId: '',
    mfCompareJobName: '',
    mfCompareStrategy: 'explicit',
    mfCompareMatchOnRaw: '',
    mfCompareUnmatchedPolicy: 'fail',
    mfCompareSimilarityThreshold: 0.7,
    mfCompareSignalFilename: true,
    mfCompareSignalColumns: true,
    mfCompareSignalRowcount: true,
    mfCompareSourceRoot: '',
    mfCompareSourcePattern: '',
    mfCompareTargetRoot: '',
    mfCompareTargetPattern: '',
    mfCompareKeyColumns: '',
    mfCompareExcludeColumns: '',
    mfComparePreviewLoading: false,
    mfComparePreviewResult: null,
    mfComparePreviewError: '',
    mfCompareLoading: false,
    mfCompareResult: null,
    mfCompareError: '',

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

    matrixSourceAType: 'file',
    matrixSourceBType: 'sql',
    matrixSourceA: { configId: '', connectionName: '', queryOrTable: '', filePath: '', fileB64: '', fileName: '', athenaQuery: '', docId: '', reportId: '', endpointUrl: '', httpMethod: 'GET', label: 'Source A' },
    matrixSourceB: { configId: '', connectionName: '', queryOrTable: '', filePath: '', fileB64: '', fileName: '', athenaQuery: '', docId: '', reportId: '', endpointUrl: '', httpMethod: 'GET', label: 'Source B' },
    matrixKeyColumns: '',
    matrixExcludeColumns: '',
    matrixNumericTolerance: '0.0',
    matrixIgnoreCase: false,
    matrixTrimWhitespace: true,
    matrixCompareLoading: false,
    matrixCompareRunId: null,
    matrixCompareResult: null,
    matrixComparePollInterval: null,

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
    colStatsSourceA: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source A', endpointName: '', parameters: [] },
    colStatsSourceB: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source B', endpointName: '', parameters: [] },
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
      await this.loadCompareBOParameters(side);
    },

    async loadCompareBOParameters(side) {
      await this.loadBOSourceParameters(side === 'a' ? this.boSourceA : this.boSourceB);
    },

    // Discover a document's prompts the same way the Adaptors tab does, so ids come
    // from BO instead of being typed by hand. Shared by the BO Report Compare card
    // (auto, on document select) and Column Stats (on demand — it types doc ids).
    async loadBOSourceParameters(src) {
      if (!src || !src.configId || !src.docId) return;
      try {
        const params = await api('GET',
          `/api/adapters/sap-bo/documents/${encodeURIComponent(src.docId)}/parameters?config_id=${src.configId}`);
        const existing = new Map((src.parameters || []).map(p => [Number(p.id), p]));
        src.parameters = (params || []).map(p => ({
          id: p.id,
          name: p.name || '',
          type: p.type || 'String',
          mandatory: Boolean(p.mandatory),
          // Keep an answer already typed for this prompt id across a reload.
          value: existing.get(Number(p.id))?.value
            || p.default
            || (p.type === 'DateTime' ? this._todayIso() : ''),
        }));
      } catch (e) {
        // A document with no prompts, or an unreachable parameters resource, must
        // not block the compare — leave the rows empty and let the pull proceed.
        src.parameters = [];
      }
    },

    _todayIso() {
      const d = new Date();
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
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

    // The report select's '*' is "all tabs" — SAP's whole-document export,
    // which the API expresses as an empty report_id. Kept distinct from the
    // unselected '' so forgetting to pick a report stays an error the user is
    // told about, rather than silently becoming a whole-document pull.
    _boReportId(src) {
      if (src.reportId === '*') return '';
      return src.reportId || null;
    },

    _buildBOSource(type, src) {
      if (type === 'live') {
        return {
          source_type: 'live',
          config_id: Number(src.configId),
          doc_id: src.docId || null,
          report_id: this._boReportId(src),
          format: 'xlsx',
          // Prompt answers, or the export ignores the picked date and reflects
          // whatever answers the document last held.
          bo_parameters: (src.parameters || []).map(p => ({
            id: Number(p.id) || 0,
            type: p.type,
            value: String(p.value ?? ''),
          })),
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
      if (type === 'run') {
        return { source_type: 'run', run_id: src.runId, job_name: src.jobName };
      }
      return { source_type: 'upload', file_content_b64: src.fileB64, file_name: src.fileName };
    },

    // Reverse of _buildBOSource -- turns a saved compare job's SourceConfig
    // (params.request.source_a/source_b) back into boSourceAType/boSourceA
    // shape. Saved compare jobs only ever hold 'live', 'path', or 'api'
    // sources (_assertCompareJobSourcesAreRepeatable rejects 'upload'/'run'
    // at save time), so those are the only three handled here.
    _hydrateBOSourceFromConfig(cfg) {
      const base = { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: '', endpointName: '', parameters: [], runId: '', jobName: '' };
      if (!cfg) return { type: 'live', src: base };
      if (cfg.source_type === 'path') {
        return { type: 'path', src: { ...base, filePath: cfg.file_path || '' } };
      }
      if (cfg.source_type === 'api') {
        return { type: 'api', src: { ...base, configId: cfg.config_id ?? '', endpointName: cfg.api_endpoint_name || '' } };
      }
      return {
        type: 'live',
        src: {
          ...base,
          configId: cfg.config_id ?? '',
          docId: cfg.doc_id || '',
          reportId: cfg.report_id || '',
          parameters: (cfg.bo_parameters || []).map(p => ({ ...p })),
        },
      };
    },

    // Reverse of _buildAdvanced -- turns an AdvancedCompareOptions object back
    // into the `${prefix}FloatTolerance` etc. raw form fields.
    _applyAdvancedToPrefix(prefix, adv) {
      adv = adv || {};
      this[`${prefix}FloatTolerance`] = adv.float_tolerance != null ? String(adv.float_tolerance) : '1e-9';
      this[`${prefix}ColumnTolerances`] = Object.entries(adv.column_tolerances || {}).map(([k, v]) => `${k}:${v}`).join(', ');
      this[`${prefix}DatetimeTolerance`] = adv.datetime_tolerance_seconds ?? 0;
      this[`${prefix}CaseInsensitiveColumns`] = (adv.case_insensitive_columns || []).join(', ');
      this[`${prefix}WhitespaceNormalizeColumns`] = (adv.whitespace_normalize_columns || []).join(', ');
      this[`${prefix}Backend`] = adv.comparison_backend || 'pandas';
      this[`${prefix}MismatchRowLimit`] = adv.mismatch_row_limit ?? 5000;
      this[`${prefix}SampleFrac`] = adv.sample_frac != null ? String(adv.sample_frac) : '';
      this[`${prefix}ParallelColumns`] = Boolean(adv.parallel_columns);
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

    // A live source needs a report chosen — one tab, or the explicit "All
    // tabs". Blank means the user never touched the select, and sending that
    // would pull the whole document without them asking for it.
    _boSourceMissingReport(type, src) {
      return type === 'live' && Boolean(src.docId) && !src.reportId;
    },

    // Payload builders are shared by the Run buttons and Save as Job, so a
    // saved job always sends exactly what an ad-hoc run would.
    _buildBOComparePayload() {
      return {
        source_a: this._buildBOSource(this.boSourceAType, this.boSourceA),
        source_b: this._buildBOSource(this.boSourceBType, this.boSourceB),
        key_columns: this.boKeyColumns.split(',').map(s => s.trim()).filter(Boolean),
        exclude_columns: this.boExcludeColumns.split(',').map(s => s.trim()).filter(Boolean),
        label_a: this.boSourceA.label || 'Source A',
        label_b: this.boSourceB.label || 'Source B',
        advanced: this._buildAdvanced('bo'),
      };
    },

    _buildReconFilePayload() {
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
      if (this.reconSourceKindMismatch()) throw new Error(this.reconSourceKindWarning());
      payload.advanced = this._buildAdvanced('file');
      return payload;
    },

    _buildMultiFilePayload() {
      const payload = {
        label_a: this.mfCompareLabelA || 'Source A',
        label_b: this.mfCompareLabelB || 'Source B',
      };
      if (this.mfCompareSourceMode === 'run') {
        payload.run_id = this.mfCompareRunId;
        payload.job_name = this.mfCompareJobName;
      } else {
        payload.file_mapping = this._buildMfCompareFileMapping();
      }
      if (this.mfCompareKeyColumns.trim()) {
        payload.key_columns = this.mfCompareKeyColumns.split(',').map(s => s.trim()).filter(Boolean);
      }
      if (this.mfCompareExcludeColumns.trim()) {
        payload.exclude_columns = this.mfCompareExcludeColumns.split(',').map(s => s.trim()).filter(Boolean);
      }
      return payload;
    },

    openSaveCompareAsJob(compareType) {
      this.saveJobCompareType = compareType;
      // Only reuse editingCompareJob as an update target when it actually
      // matches the sub-tab being saved from -- e.g. editing a 'bo' job then
      // switching to the recon_file sub-tab without saving must not silently
      // overwrite the 'bo' job with an unrelated recon_file config.
      if (this.editingCompareJob && this.editingCompareJob.params?.compare_type !== compareType) {
        this.editingCompareJob = null;
      }
      const editing = this.editingCompareJob;
      this.saveJobName = editing ? editing.name : '';
      this.saveJobDescription = editing ? (editing.description || '') : '';
      this.saveJobTags = editing ? (editing.tags || []).join(', ') : '';
      this.saveJobError = '';
      this.saveJobModalOpen = true;
    },

    // Escape hatch out of the update-in-place flow: keeps the form's current
    // values but saves them as a brand new job instead of overwriting the one
    // that was loaded for editing.
    saveCompareAsNewJob() {
      this.editingCompareJob = null;
      this.saveJobName = '';
      this.saveJobDescription = '';
      this.saveJobTags = '';
    },

    // Mirror of the server-side validator (api/schemas.py's compare branch), so
    // a non-repeatable source is caught before the round trip. The server stays
    // authoritative.
    _assertCompareJobSourcesAreRepeatable(compareType, payload) {
      if (compareType === 'bo') {
        [['A', payload.source_a], ['B', payload.source_b]].forEach(([side, src]) => {
          if (!src) return;
          if (src.source_type === 'upload' || src.source_type === 'run') {
            const what = src.source_type === 'upload' ? 'an upload' : 'a past run';
            throw new Error(`Source ${side} is ${what} - a job that re-runs needs a live, path, or API source.`);
          }
        });
        return;
      }
      if (compareType === 'matrix') {
        [['A', payload.source_a], ['B', payload.source_b]].forEach(([side, src]) => {
          if (!src) return;
          if (src.source_type === 'file' && src.file_b64 && !src.file_path) {
            throw new Error(`Source ${side} is an upload - a job that re-runs needs a file path or another repeatable source.`);
          }
        });
        return;
      }
      [
        ['A', payload.stored_run_id, payload.file_a_content_b64],
        ['B', payload.stored_run_id_b, payload.file_b_content_b64],
      ].forEach(([side, storedRun, content]) => {
        if (storedRun || content) {
          const what = storedRun ? 'a stored run' : 'an upload';
          throw new Error(`Source ${side} is ${what} - a job that re-runs needs a file path.`);
        }
      });
    },

    _compareJobBody() {
      // Multi-file saves as the reconciliation/multi_file job that already runs
      // and already schedules - not as a `compare` job.
      if (this.saveJobCompareType === 'multi_file') {
        const payload = this._buildMultiFilePayload();
        if (payload.run_id) {
          throw new Error('A run-reference multi-file compare cannot be saved as a job - pick source and target roots instead.');
        }
        return {
          job_type: 'reconciliation',
          key_columns: payload.key_columns || [],
          exclude_columns: payload.exclude_columns || [],
          params: { source_mode: 'multi_file', file_mapping: payload.file_mapping },
        };
      }
      if (this.saveJobCompareType === 'matrix') {
        const payload = this._buildMatrixComparePayload();
        this._assertCompareJobSourcesAreRepeatable('matrix', payload);
        return {
          job_type: 'compare',
          key_columns: payload.key_columns || [],
          exclude_columns: payload.exclude_columns || [],
          params: { compare_type: 'matrix', request: payload },
        };
      }
      const payload = this.saveJobCompareType === 'bo'
        ? this._buildBOComparePayload()
        : this._buildReconFilePayload();
      this._assertCompareJobSourcesAreRepeatable(this.saveJobCompareType, payload);
      return {
        job_type: 'compare',
        key_columns: payload.key_columns || [],
        exclude_columns: payload.exclude_columns || [],
        params: { compare_type: this.saveJobCompareType, request: payload },
      };
    },

    async saveCompareAsJob() {
      const name = (this.saveJobName || '').trim();
      if (!name) { this.saveJobError = 'Enter a job name'; return; }
      this.saveJobSaving = true;
      this.saveJobError = '';
      const isUpdate = Boolean(this.editingCompareJob);
      try {
        const body = {
          ...this._compareJobBody(),
          name,
          description: this.saveJobDescription || '',
          tags: (this.saveJobTags || '').split(',').map(s => s.trim()).filter(Boolean),
        };
        if (isUpdate) {
          await api('PUT', `/api/jobs/${encodeURIComponent(this.editingCompareJob.name)}`, body);
        } else {
          await api('POST', '/api/jobs', body);
        }
        this.saveJobModalOpen = false;
        this.editingCompareJob = null;
        this.toast('success', isUpdate ? 'Job updated' : 'Saved as job',
          isUpdate
            ? `"${name}" was updated.`
            : `"${name}" is in the Job Catalog - add it to a selection to schedule it.`);
        if (this.loadJobs) await this.loadJobs();
      } catch (e) {
        this.saveJobError = e.message || 'Could not save this compare as a job';
      } finally {
        this.saveJobSaving = false;
      }
    },

    async runBOComparison() {
      if (this._boSourceMissingReport(this.boSourceAType, this.boSourceA) ||
          this._boSourceMissingReport(this.boSourceBType, this.boSourceB)) {
        this.toast('error', 'Select a report',
          'Choose a tab, or "All tabs" to compare the whole document.');
        return;
      }
      this.boCompareLoading = true;
      this.boCompareResult = null;
      if (this.boComparePollInterval) clearInterval(this.boComparePollInterval);
      try {
        const payload = this._buildBOComparePayload();
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

    // Recon-file mode can only diff like against like: two tabular data sources, or
    // two report-shaped ones. Mixing them used to be accepted by the UI and only
    // rejected server-side, after a run row had already been created and failed.
    reconSourceKind(side) {
      const type = side === 'a' ? this.fileSourceAType : this.fileSourceBType;
      if (type === 'run') {
        const runId = side === 'a' ? this.fileRunIdA : this.fileRunIdB;
        if (!runId) return 'none';
        // A run that kept its downloaded data (e.g. a BO report job) is row-diffable;
        // every other run only exposes per-test stats.
        const run = (this.runs || []).find(r => r.run_id === runId);
        return run && run.has_data_artifact ? 'tabular' : 'report';
      }
      const ref = (type === 'upload'
        ? (side === 'a' ? this.fileNameA : this.fileNameB)
        : (side === 'a' ? this.filePathA : this.filePathB)) || '';
      const trimmed = ref.trim();
      if (!trimmed) return 'none';
      const dot = trimmed.lastIndexOf('.');
      const ext = dot >= 0 ? trimmed.slice(dot).toLowerCase() : '';
      return RECON_TABULAR_EXTS.indexOf(ext) >= 0 ? 'tabular' : 'report';
    },

    reconSourceKindMismatch() {
      const a = this.reconSourceKind('a');
      const b = this.reconSourceKind('b');
      return a !== 'none' && b !== 'none' && a !== b;
    },

    reconSourceKindWarning() {
      if (!this.reconSourceKindMismatch()) return '';
      const describe = (side) => {
        const kind = this.reconSourceKind(side);
        if (kind === 'tabular') return 'tabular data';
        const type = side === 'a' ? this.fileSourceAType : this.fileSourceBType;
        return type === 'run' ? 'a stored run (per-test stats only)' : 'an HTML report';
      };
      return 'Source A is ' + describe('a') + ' and Source B is ' + describe('b')
        + '. Both sides must be the same kind: two tabular files '
        + '(' + RECON_TABULAR_EXTS.join(', ') + '), or two report-shaped sources.';
    },

    async runFileCompare() {
      this.fileCompareLoading = true;
      this.fileCompareResult = null;
      this.fileExpandedDiffs = {};
      try {
        const payload = this._buildReconFilePayload();
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

    _buildMfCompareFileMapping() {
      const match_on = this.mfCompareMatchOnRaw.split(',').map(s => s.trim()).filter(Boolean);
      const config = {
        strategy: this.mfCompareStrategy,
        unmatched_policy: this.mfCompareUnmatchedPolicy,
        source: { kind: 'local', root: this.mfCompareSourceRoot, pattern: this.mfCompareSourcePattern },
        target: { kind: 'local', root: this.mfCompareTargetRoot, pattern: this.mfCompareTargetPattern },
      };
      if (this.mfCompareStrategy === 'explicit') config.match_on = match_on;
      if (this.mfCompareStrategy === 'automated') {
        const signals = [];
        if (this.mfCompareSignalFilename) signals.push('filename_tokens');
        if (this.mfCompareSignalColumns) signals.push('column_signature');
        if (this.mfCompareSignalRowcount) signals.push('row_count_ratio');
        const parsedThreshold = Number(this.mfCompareSimilarityThreshold);
        config.automated_mapping = {
          similarity_threshold: Number.isFinite(parsedThreshold) && this.mfCompareSimilarityThreshold !== '' ? parsedThreshold : 0.7,
          signals,
        };
      }
      return config;
    },

    async previewMfCompareMapping() {
      this.mfComparePreviewLoading = true;
      this.mfComparePreviewResult = null;
      this.mfComparePreviewError = '';
      try {
        this.mfComparePreviewResult = await api('POST', '/api/jobs/preview-file-mapping', {
          file_mapping: this._buildMfCompareFileMapping(),
        });
      } catch (e) {
        this.mfComparePreviewError = e.message || 'Preview failed';
      } finally {
        this.mfComparePreviewLoading = false;
      }
    },

    async runMultiFileCompare() {
      this.mfCompareLoading = true;
      this.mfCompareResult = null;
      this.mfCompareError = '';
      try {
        const payload = this._buildMultiFilePayload();
        const run = await api('POST', '/api/compare/multi-file', payload);
        const poll = setInterval(async () => {
          try {
            const st = await api('GET', `/api/runs/${run.run_id}/status`);
            if (this.isTerminalStatus(st.status)) {
              clearInterval(poll);
              this.mfCompareResult = await api('GET', `/api/runs/${run.run_id}`);
              this.mfCompareLoading = false;
              await this.loadRuns();
            }
          } catch (e) {
            clearInterval(poll);
            this.mfCompareLoading = false;
          }
        }, 3000);
      } catch (e) {
        this.mfCompareError = e.message || 'Multi-file compare failed';
        this.toast('error', 'Multi-file compare failed', e.message);
        this.mfCompareLoading = false;
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

    handleMatrixFileUpload(event, side) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        const bytes = new Uint8Array(e.target.result);
        let binary = '';
        for (let i = 0; i < bytes.length; i += 8192) {
          binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
        }
        const src = side === 'a' ? this.matrixSourceA : this.matrixSourceB;
        src.fileB64 = btoa(binary);
        src.fileName = file.name;
      };
      reader.readAsArrayBuffer(file);
    },

    _buildMatrixSourceSpec(type, src) {
      const spec = { source_type: type };
      if (type === 'sql') {
        if (src.configId) spec.config_id = parseInt(src.configId, 10);
        if (src.connectionName) spec.connection_name = src.connectionName;
        if (src.queryOrTable) spec.query_or_table = src.queryOrTable;
      } else if (type === 'file') {
        if (src.filePath) spec.file_path = src.filePath;
        if (src.fileB64) spec.file_b64 = src.fileB64;
        if (src.fileName) spec.file_name = src.fileName;
      } else if (type === 'aws_athena') {
        if (src.configId) spec.config_id = parseInt(src.configId, 10);
        if (src.athenaQuery || src.queryOrTable) spec.query_or_table = src.athenaQuery || src.queryOrTable;
      } else if (type === 'sap_bo') {
        if (src.configId) spec.config_id = parseInt(src.configId, 10);
        if (src.docId) spec.bo_doc_id = src.docId;
        if (src.reportId) spec.bo_report_id = src.reportId;
      } else if (type === 'api') {
        if (src.configId) spec.config_id = parseInt(src.configId, 10);
        if (src.endpointUrl) spec.endpoint_url = src.endpointUrl;
        spec.http_method = src.httpMethod || 'GET';
      }
      return spec;
    },

    _buildMatrixComparePayload() {
      return {
        source_a: this._buildMatrixSourceSpec(this.matrixSourceAType, this.matrixSourceA),
        source_b: this._buildMatrixSourceSpec(this.matrixSourceBType, this.matrixSourceB),
        label_a: this.matrixSourceA.label || 'Source A',
        label_b: this.matrixSourceB.label || 'Source B',
        key_columns: this.matrixKeyColumns ? this.matrixKeyColumns.split(',').map(s => s.trim()).filter(Boolean) : [],
        exclude_columns: this.matrixExcludeColumns ? this.matrixExcludeColumns.split(',').map(s => s.trim()).filter(Boolean) : [],
        numeric_tolerance: parseFloat(this.matrixNumericTolerance) || 0.0,
        ignore_case: !!this.matrixIgnoreCase,
        trim_whitespace: !!this.matrixTrimWhitespace,
      };
    },

    async runMatrixCompare() {
      this.matrixCompareLoading = true;
      this.matrixCompareResult = null;
      if (this.matrixComparePollInterval) clearInterval(this.matrixComparePollInterval);
      try {
        const payload = this._buildMatrixComparePayload();
        const run = await api('POST', '/api/compare/matrix', payload);
        this.matrixCompareRunId = run.run_id;
        this.matrixComparePollInterval = setInterval(() => this._pollMatrixCompare(), 3000);
        await this._pollMatrixCompare();
        await this.loadRuns();
      } catch (e) {
        this.toast('error', 'Matrix comparison failed', e.message);
        this.matrixCompareLoading = false;
      }
    },

    async _pollMatrixCompare() {
      if (!this.matrixCompareRunId) return;
      try {
        const status = await api('GET', `/api/runs/${this.matrixCompareRunId}/status`);
        if (this.isTerminalStatus(status.status)) {
          clearInterval(this.matrixComparePollInterval);
          this.matrixComparePollInterval = null;
          this.matrixCompareResult = await api('GET', `/api/runs/${this.matrixCompareRunId}`);
          this.matrixCompareLoading = false;
          await this.loadRuns();
        }
      } catch (e) {
        clearInterval(this.matrixComparePollInterval);
        this.matrixComparePollInterval = null;
        this.matrixCompareLoading = false;
      }
    },

    filteredDiff(diffs, filterKey, filterState) {
      const f = filterState[filterKey] || {};
      if (!f.type && !f.col && !f.search) return diffs;
      // The free-text box used to search key values alone, so a column name or
      // either side's value was unreachable. It now runs the same query engine
      // the downloadable HTML report uses -- see features/diff-search.js.
      const terms = parseDiffQuery(f.search || '');
      return (diffs || []).filter(m => {
        if (f.type && m.mismatch_type !== f.type) return false;
        if (f.col  && m.column_name !== f.col)   return false;
        if (terms.length && !matchesDiffQuery(diffSearchFields(m), terms)) return false;
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
      if (this._boSourceMissingReport(this.colStatsSourceAType, this.colStatsSourceA) ||
          this._boSourceMissingReport(this.colStatsSourceBType, this.colStatsSourceB)) {
        this.toast('error', 'Select a report',
          'Choose a tab, or "All tabs" to compare the whole document.');
        return;
      }
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
        // These are the request-level fallbacks each source inherits when it
        // names no report of its own; '*' must not leak through as a tab name.
        const fallbackReport = this._boReportId(this.colStatsSourceA);
        if (fallbackReport) payload.report_id = fallbackReport;
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
        // Rarely hit -- the server always sets Content-Disposition now (see
        // export_filename in api/services/difference_export.py). This fallback
        // can't compute the full report-name convention client-side (it doesn't
        // have the run's config_snapshot/started_at loaded here), so it just
        // avoids the old, now-inconsistent "all_differences_" prefix.
        const fallback = `report_${runId}.${format === 'parquet' ? 'parquet' : 'csv'}`;
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
          // Rarely hit -- the server always sets Content-Disposition now (see
          // export_filename in api/services/difference_export.py). This fallback
          // can't compute the full report-name convention client-side (it doesn't
          // have the run's config_snapshot/started_at loaded here), so it just
          // avoids the old, now-inconsistent "all_differences_" prefix.
          const fallback = `report_${runId}_${exportId}.${ext}`;
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
