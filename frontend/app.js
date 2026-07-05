/* ETL Framework – full 6-tab SPA */

const API = window.ETL_API_BASE || '';

function normalizeToken(raw) {
  let token = String(raw || '').trim();
  token = token.replace(/^Authorization\s*:\s*/i, '').trim();
  token = token.replace(/^["']+|["']+$/g, '').trim();
  while (/^Bearer\s+/i.test(token)) {
    token = token.replace(/^Bearer\s+/i, '').trim();
  }
  return token;
}

async function api(method, path, body) {
  const token = normalizeToken(sessionStorage.getItem('etl_token'));
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = 'Bearer ' + token;
  const opts = { method, headers };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const resp = await fetch(API + path, opts);
  if (resp.status === 204) return null;
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    const error = new Error(apiErrorMessage(err.detail ?? err, resp.statusText));
    error.status = resp.status;
    throw error;
  }
  return resp.json();
}

function apiErrorMessage(detail, fallback = 'Request failed') {
  if (!detail) return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map(item => apiErrorMessage(item, '')).filter(Boolean).join('; ') || fallback;
  }
  if (typeof detail === 'object') {
    if (detail.message) return String(detail.message);
    if (detail.error) return String(detail.error);
    if (detail.error_type && detail.field_name) return `${detail.field_name}: ${detail.error_type}`;
    if (detail.errors) return apiErrorMessage(detail.errors, fallback);
    try { return JSON.stringify(detail); } catch (_) { return fallback; }
  }
  return String(detail);
}

async function apiBlob(path) {
  const token = normalizeToken(sessionStorage.getItem('etl_token'));
  const headers = token ? { 'Authorization': 'Bearer ' + token } : {};
  const resp = await fetch(API + path, { headers });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    const error = new Error(apiErrorMessage(err.detail ?? err, resp.statusText));
    error.status = resp.status;
    throw error;
  }
  return { blob: await resp.blob(), disposition: resp.headers.get('content-disposition') || '' };
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Char-level diff utility ───────────────────────────────────────────────
function _escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function _charDiff(a, b) {
  const n = a.length, m = b.length;
  if (n === 0) return b.split('').map(c => ({ text: c, op: '+' }));
  if (m === 0) return a.split('').map(c => ({ text: c, op: '-' }));
  const dp = [];
  for (let i = 0; i <= n; i++) { dp[i] = new Uint16Array(m + 1); }
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      dp[i][j] = a[i-1] === b[j-1]
        ? dp[i-1][j-1] + 1
        : Math.max(dp[i-1][j], dp[i][j-1]);
    }
  }
  const ops = [];
  let i = n, j = m;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i-1] === b[j-1]) {
      ops.push({ text: a[i-1], op: '=' }); i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j-1] >= dp[i-1][j])) {
      ops.push({ text: b[j-1], op: '+' }); j--;
    } else {
      ops.push({ text: a[i-1], op: '-' }); i--;
    }
  }
  ops.reverse();
  const merged = [];
  for (const { text, op } of ops) {
    if (merged.length && merged[merged.length-1].op === op) merged[merged.length-1].text += text;
    else merged.push({ text, op });
  }
  return merged;
}

function renderSrc(rawA, rawB) {
  if (rawA == null && rawB == null) return '<span class="null-val">NULL</span>';
  if (rawA == null) return '<span class="null-val">NULL</span>';
  if (rawB == null) return _escHtml(String(rawA));
  if (!isNaN(rawA) && !isNaN(rawB) && isFinite(rawA) && isFinite(rawB)) {
    return _escHtml(String(rawA));
  }
  const sa = String(rawA), sb = String(rawB);
  if (sa.length > 500 || sb.length > 500) return _escHtml(sa.slice(0, 500)) + '…';
  const ops = _charDiff(sa, sb);
  return ops.map(({ text, op }) =>
    op === '-' ? `<span class="diff-del">${_escHtml(text)}</span>` :
    op === '=' ? _escHtml(text) : ''
  ).join('');
}

function renderTgt(rawA, rawB) {
  if (rawA == null && rawB == null) return '<span class="null-val">NULL</span>';
  if (rawB == null) return '<span class="null-val">NULL</span>';
  if (rawA == null) return _escHtml(String(rawB));
  if (!isNaN(rawA) && !isNaN(rawB) && isFinite(rawA) && isFinite(rawB)) {
    return _escHtml(String(rawB));
  }
  const sa = String(rawA), sb = String(rawB);
  if (sa.length > 500 || sb.length > 500) return _escHtml(sb.slice(0, 500)) + '…';
  const ops = _charDiff(sa, sb);
  return ops.map(({ text, op }) =>
    op === '+' ? `<span class="diff-ins">${_escHtml(text)}</span>` :
    op === '=' ? _escHtml(text) : ''
  ).join('');
}
// ─────────────────────────────────────────────────────────────────────────────

function app() {
  return {
    // -----------------------------------------------------------
    // Navigation
    // -----------------------------------------------------------
    currentView: 'config',
    tabs: [
      { id: 'config',   label: '⚙ Config' },
      { id: 'jobs',     label: '▶ Launch' },
      { id: 'monitor',  label: '📡 Monitor' },
      { id: 'history',  label: '📋 History' },
      { id: 'adapters', label: '🔌 Adapters' },
      { id: 'reports',  label: '📊 Reports' },
      { id: 'compare',  label: '\u21c4 Compare' },
      { id: 'contracts', label: '\u{1F4CB} Contracts' },
      { id: 'logs', label: '🪵 Logs' },
    ],
    apiOk: false,

    // -----------------------------------------------------------
    // Auth setup wizard
    // -----------------------------------------------------------
    showAuthModal: false,
    authTokenName: '',
    authPasteValue: '',
    authError: '',
    authCreatedToken: null,
    authInitialized: true,
    activeTokenName: '',
    activeTokenIsAdmin: false,
    storedTokenValue: normalizeToken(sessionStorage.getItem('etl_token')),

    // -----------------------------------------------------------
    // Config
    // -----------------------------------------------------------
    configs: [],
    showConfigModal: false,
    configModal: {},
    configValidation: null,

    // -----------------------------------------------------------
    // Config – YAML import
    // -----------------------------------------------------------
    yamlImportOpen: false,
    yamlImportText: '',
    yamlImporting: false,

    // -----------------------------------------------------------
    // Jobs / Launch
    // -----------------------------------------------------------
    jobs: [],
    selectedJobs: [],
    stepSettings: {},      // { jobName: { hold_after, wait_seconds, require_status, max_mismatch_count } }
    stepSettingsOpen: {},  // { jobName: bool } — expanded settings panel
    showJobModal: false,
    jobModal: {},
    jobModalEditing: false,
    launchSettings: {
      source_env: 'dev',
      target_env: 'prod',
      config_id: '',
      execution_mode: 'parallel',
      max_workers: 4,
      max_duration_seconds: 0,
      float_tolerance: '1e-9',
      schema_mismatch_policy: 'warn',
      null_equals_null: true,
      chunk_size: 0,
      use_hash_precheck: true,
      comparison_backend: 'pandas',
      health_check: false,
      metrics_enabled: true,
      use_live_connections: false,
      notes: '',
      max_retries: 0,
      retry_delay_seconds: 30,
      source_connection: null,
      target_connection: null,
    },
    isLaunching: false,
    validateJobLoading: false,
    validateJobResult: null,

    // -----------------------------------------------------------
    // Monitor
    // -----------------------------------------------------------
    activeRuns: [],
    pollTimer: null,
    runStreams: {},
    runStepsCache: {},   // { run_id: RunStep[] }
    stepReleaseModal: { show: false, runId: '', stepIndex: 0, releasedBy: '', note: '', action: 'approve' },

    // -----------------------------------------------------------
    // History
    // -----------------------------------------------------------
    runs: [],
    selectedRun: null,
    chartInstance: null,
    historyFilterStatus: '',
    historyFilterRunType: '',
    historySubTab: 'runs',
    auditEvents: [],
    auditLoading: false,
    auditFilterResourceType: '',
    auditFilterResourceId: '',

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

    // -----------------------------------------------------------
    // Adapters – SAP BO
    // -----------------------------------------------------------
    boConfigId: '',
    boTesting: false,
    boLoading: false,
    boTestResult: null,
    boDocs: [],
    expandedBODocs: [],
    boReports: {},         // doc.id → list of reports
    showBOJobModal: false,
    boJobForm: { name: '', title: '', doc_id: '', report_id: '', key_columns_raw: 'id', format: 'xlsx' },

    // -----------------------------------------------------------
    // Adapters – Automic
    // -----------------------------------------------------------
    automicConfigId: '',
    automicIdentifier: '',
    automicIdType: 'job_name',
    automicLoading: false,
    automicResult: null,
    automicHistory: JSON.parse(sessionStorage.getItem('automicHistory') || '[]'),

    // Adapters – Import from File
    fileImportOpen: false,
    fileImportJobs: [],
    fileImportErrors: [],
    fileImportLoading: false,

    // Adapters – Browse & Import from Automic
    browseAutomicOpen: false,
    browseAutomicConfigId: '',
    browseAutomicFilter: '',
    browseAutomicResults: [],
    browseAutomicSelected: [],
    browseAutomicLoading: false,
    browseAutomicImporting: false,
    browseAutomicError: '',

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
    logFilterQuery: '',
    logFilterLevel: '',

    // -----------------------------------------------------------
    // Global Logs tab (server-wide, no run_id required)
    // -----------------------------------------------------------
    globalLogEvents: [],
    globalLogsLoading: false,
    globalLogFilterQuery: '',
    globalLogFilterLevel: '',
    globalLogRunId: '',
    globalLogsPollTimer: null,

    // -----------------------------------------------------------
    // Mismatch drawer
    // -----------------------------------------------------------
    drawer: {
      show: false,
      loading: false,
      runId: '',
      result: null,
      rows: [],
      offset: 0,
    },

    // -----------------------------------------------------------
    // Inline mismatch expand (History detail)
    // -----------------------------------------------------------
    expandedMismatches: {},      // result_id → rows[]
    expandingMismatch: {},       // result_id → bool
    expandedMismatchOffset: {},  // result_id → current offset
    outcomeOverrideForms: {},    // result_id → { open, reason, saving }
    expandedSampleRows: {},      // result_id → bool

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
    boSampleFrac: '',
    boParallelColumns: false,

    fileAdvancedOpen: false,
    fileFloatTolerance: '1e-9',
    fileColumnTolerances: '',
    fileDatetimeTolerance: 0,
    fileCaseInsensitiveColumns: '',
    fileWhitespaceNormalizeColumns: '',
    fileBackend: 'pandas',
    fileSampleFrac: '',
    fileParallelColumns: false,

    sqlAdvancedOpen: false,
    sqlFloatTolerance: '1e-9',
    sqlColumnTolerances: '',
    sqlDatetimeTolerance: 0,
    sqlCaseInsensitiveColumns: '',
    sqlWhitespaceNormalizeColumns: '',
    sqlBackend: 'pandas',
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

    // Schema Explorer (Config tab)
    schemaExplorerId: null,
    schemaExplorerData: [],
    schemaExplorerLoading: false,
    schemaExpandedSchemas: {},
    schemaExpandedTables: {},
    schemaTablePreviews: {},

    pastPairs: [],
    pastPairsLoading: false,

    acceptForms: {},

    // -----------------------------------------------------------
    // Security – API tokens
    // -----------------------------------------------------------
    tokens: [],
    securityOpen: false,
    showCreateToken: false,
    newTokenName: '',
    newTokenRole: 'user',
    newTokenExpiresAt: '',
    createdToken: null,
    createdTokenHint: null,
    createdTokenRole: 'user',

    // -----------------------------------------------------------
    // Notifications – webhook hooks
    // -----------------------------------------------------------
    hooks: [],
    notifOpen: false,
    showHookModal: false,
    hookModal: { name: '', url: '', events: [], secret: '' },
    hookEventOptions: ['run.passed', 'run.failed', 'run.slow', 'run.error', 'run.completed', 'run.held', 'run.cancelled'],

    // -----------------------------------------------------------
    // Schedules
    // -----------------------------------------------------------
    schedules: [],
    launchSubTab: 'jobs',
    showScheduleModal: false,
    scheduleModal: {},
    jobSelections: [],
    showSelectionModal: false,
    selectionModal: {},
    selectionModalEditing: false,
    selectedSelectionJobNames: [],
    showLaunchSelectionModal: false,
    launchSelectionModal: {},
    showSelectionRunsModal: false,
    selectionRunsPanel: null,
    selectionRuns: [],
    compareRunIds: [],
    scheduleModalEditing: false,

    // -----------------------------------------------------------
    // Toast
    // -----------------------------------------------------------
    toasts: [],
    _toastSeq: 0,

    // --- Task 1: Job Catalog Search/Filter ---
    jobSearchQuery: '',

    // --- Task 2: Job Last Run Status ---
    // (stored in localStorage, accessed via methods)

    // --- Task 3: Multi-select with Shift-click ---
    multiSelectMode: false,
    shiftLastIndex: -1,

    // --- Task 7: Config Dropdown + Session Memory ---
    // (savedConfigDisplay is a method; session settings persisted via methods)

    // --- Task 8: Job Modal Tab Reorganization ---
    jobModalTab: 'basic',
    jobModalTabs: [
      { id: 'basic', label: 'Basic Info' },
      { id: 'settings', label: 'Settings' },
      { id: 'deps', label: 'Dependencies' },
      { id: 'rules', label: 'DQ Rules' },
      { id: 'tags', label: 'Tags' },
      { id: 'conditions', label: 'Conditions' },
    ],

    // --- Task 9: Job Modal Inline Validation ---
    jobModalValidation: { sql: '', keyColumns: '', dependencies: '' },

    // --- Task 10: DQ Rule Templates + Job Templates ---
    dqRuleTemplates: [
      { name: 'Price must be positive', type: 'column_mean_between', defaults: { min: 0, max: null } },
      { name: 'ID must be not null', type: 'not_null', defaults: {} },
      { name: 'Status code range', type: 'column_mean_between', defaults: { min: 100, max: 599 } },
      { name: 'Email format validation', type: 'match_regex', defaults: { pattern: '^[\\w.+-]+@[\\w-]+\\.[\\w.]+$' } },
    ],
    jobTemplates: [],
    jobTemplateName: '',
    showSaveTemplatePrompt: false,

    // --- Task 11: Execution Sequence Drag-to-Reorder ---
    dragSrcIndex: null,

    // --- Task 13: Compare Tab Template System ---
    compareTemplates: [],
    activeCompareTemplate: '',
    showCompareTemplatePanel: false,
    newCompareTemplateName: '',
    predefinedCompareTemplates: [
      { name: 'Daily BO Report Compare', type: 'bo', config: { sourceTypeA: 'api', sourceTypeB: 'api' } },
      { name: 'Weekly Report Trend Analysis', type: 'bo', config: { sourceTypeA: 'api', sourceTypeB: 'baseline' } },
      { name: 'Ad-hoc File Upload Comparison', type: 'bo', config: { sourceTypeA: 'upload', sourceTypeB: 'upload' } },
      { name: 'Daily Reconciliation vs Baseline', type: 'reconciliation', config: {} },
      { name: 'Production File Validation', type: 'reconciliation', config: { fileMode: 'upload' } },
      { name: 'Environment-to-Environment Diff', type: 'reconciliation', config: {} },
    ],

    // --- Task 14: BO Report Tab Improvements ---
    boSaveAsBaseline: false,
    boLastUsedSourceTypes: { a: '', b: '' },

    // --- Task 15: Quick Compare Mode ---
    quickCompareMode: false,

    // --- Task 17: Results Panel Export + Visualization Toggle ---
    showMismatchChart: false,
    mismatchChartType: 'column',
    mismatchChartData: null,

    // --- Task 18: Mismatch Acceptance Workflow ---
    mismatchStatusFilter: 'ALL',

    // --- Task 19: Help System + Keyboard Shortcuts ---
    showingHelp: false,
    helpTitle: '',
    helpContent: '',

    // -----------------------------------------------------------
    // Contracts
    // -----------------------------------------------------------
    contracts: [],
    contractsLoading: false,
    selectedContract: null,
    contractDetailLoading: false,
    contractStatusMap: {},          // name → { status, open_breach }
    contractBreachHistory: [],
    contractVersionHistory: [],
    contractStatusLoading: false,
    contractBreachLoading: false,
    contractVersionLoading: false,
    showContractModal: false,
    contractModal: { name: '', source_job: '', owner: '', sla_hours: 4, consumers_raw: '', breach_severity: 'error', version: '1.0' },
    contractModalEditing: false,
    contractBumpType: 'minor',
    contractBumpNote: '',
    contractBumpLoading: false,

    // ===========================================================
    // INIT
    // ===========================================================
    async init() {
      this.storedTokenValue = normalizeToken(sessionStorage.getItem('etl_token'));
      await this.loadAuthSetupStatus();
      if (this.storedTokenValue) sessionStorage.setItem('etl_token', this.storedTokenValue);
      if (this.storedToken) {
        const tokenValid = await this.resolveActiveTokenName({ verify: true, clearInvalid: true });
        if (tokenValid) {
          await this.loadAll();
          this.loadTokens();
          this.loadHooks();
          this.loadSchedules();
        }
      }
      if (!this.storedToken && !this.authInitialized) this.showAuthModal = true;
      this.startPolling();
      try {
        await api('GET', '/api/health');
        this.apiOk = true;
      } catch {
        this.apiOk = false;
      }
      // --- Task 7: Load session settings from localStorage ---
      this.loadSessionSettings();
      // --- Task 10: Load job templates from localStorage ---
      this._loadJobTemplatesFromStorage();
      // --- Task 13: Load compare templates from localStorage ---
      this.loadCompareTemplates();
      // --- Task 19: Init keyboard shortcuts ---
      this.initKeyboardShortcuts();
      this.$watch('launchSettings.config_id', () => {
        this.launchSettings.source_connection = null;
        this.launchSettings.target_connection = null;
      });
      this.$watch('sqlConfigA', () => { this.sqlConnectionA = null; });
      this.$watch('sqlConfigB', () => { this.sqlConnectionB = null; });
    },

    // ===========================================================
    // TOAST helpers
    // ===========================================================
    toast(type, title, msg = '') {
      const id = ++this._toastSeq;
      this.toasts.push({ id, type, title, msg, fading: false });
      setTimeout(() => {
        const t = this.toasts.find(x => x.id === id);
        if (t) t.fading = true;
        setTimeout(() => { this.toasts = this.toasts.filter(x => x.id !== id); }, 350);
      }, 3500);
    },

    isAuthError(e) {
      return e?.status === 401 || /invalid or expired token|authorization header/i.test(e?.message || '');
    },

    handleAuthError(e) {
      if (!this.isAuthError(e)) return false;
      sessionStorage.removeItem('etl_token');
      this.storedTokenValue = '';
      this.activeTokenName = '';
      this.activeTokenIsAdmin = false;
      this.authError = 'Your API token was rejected. Create or paste a valid token.';
      this.showAuthModal = true;
      this.toast('error', 'API token rejected', 'Set up access again');
      return true;
    },

    async loadAll() {
      await Promise.allSettled([
        this.loadConfigs(),
        this.loadJobs(),
        this.loadRuns(),
      ]);
    },

    async loadAuthSetupStatus() {
      try {
        const status = await api('GET', '/api/auth/setup-status');
        this.authInitialized = Boolean(status.initialized);
      } catch {
        this.authInitialized = true;
      }
    },

    openAuthModal() {
      this.authError = '';
      this.showAuthModal = true;
    },

    closeAuthModal() {
      this.showAuthModal = false;
      this.authCreatedToken = null;
    },

    async activateToken() {
      const raw = normalizeToken(this.authPasteValue);
      if (!raw) {
        this.authError = 'Paste your token';
        return;
      }
      sessionStorage.setItem('etl_token', raw);
      this.storedTokenValue = raw;
      this.authPasteValue = '';
      this.authError = '';
      const valid = await this.resolveActiveTokenName({ verify: true });
      if (!valid) {
        sessionStorage.removeItem('etl_token');
        this.storedTokenValue = '';
        this.authError = 'Your API token was rejected. Paste a valid raw token.';
        this.showAuthModal = true;
        return;
      }
      this.closeAuthModal();
      await this.loadAll();
    },

    async verifyStoredToken({ clearInvalid = false } = {}) {
      if (!this.storedToken) return false;
      try {
        const verified = await api('GET', '/api/auth/verify');
        this.activeTokenName = verified.actor || '';
        this.activeTokenIsAdmin = Boolean(verified.is_admin);
        return true;
      } catch (e) {
        if (e?.status === 404) {
          try {
            this.configs = await api('GET', '/api/configs');
            this.activeTokenName = '';
            return true;
          } catch (fallbackError) {
            e = fallbackError;
          }
        }
        if (clearInvalid) {
          sessionStorage.removeItem('etl_token');
          this.storedTokenValue = '';
          this.activeTokenName = '';
          this.activeTokenIsAdmin = false;
        }
        return false;
      }
    },

    async resolveActiveTokenName({ verify = false, clearInvalid = false } = {}) {
      this.activeTokenName = '';
      this.activeTokenIsAdmin = false;
      if (!this.storedToken) return;
      if (verify) {
        const valid = await this.verifyStoredToken({ clearInvalid });
        if (!valid) return false;
        return true;
      }
      try {
        const tokens = await this.loadTokens();
        const active = (tokens || []).find(t => t.enabled);
        this.activeTokenName = active?.name || '';
        this.activeTokenIsAdmin = Boolean(active?.is_admin);
        return true;
      } catch {
        this.activeTokenName = '';
        this.activeTokenIsAdmin = false;
        return true;
      }
    },

    goToTokenManagement() {
      this.currentView = 'config';
      this.securityOpen = true;
      this.loadTokens();
    },

    // ===========================================================
    // CONFIG
    // ===========================================================
    async loadConfigs() {
      try { this.configs = await api('GET', '/api/configs'); } catch {}
    },

    openNewConfigModal() {
      this.configModal = {
        id: null, name: '', env_name: 'dev',
        db_host: 'localhost', db_port: 1433, db_name: '', db_user: '', db_password: '',
        db_connect_timeout: 15,
        bo_url: '', bo_user: '', bo_password: '', bo_auth_type: 'secEnterprise', bo_timeout: 60,
        bo_proxy_url: '', bo_verify_ssl: true,
        automic_url: '', automic_user: '', automic_password: '',
        connections: [],
        apiEndpoints: [],
      };
      this.configValidation = null;
      this.showConfigModal = true;
    },

    editConfig(cfg) {
      const d = cfg.config_data || {};
      this.configModal = {
        id: cfg.id, name: cfg.name, env_name: cfg.env_name,
        db_host: d.db_host || '', db_port: d.db_port || 1433,
        db_name: d.db_name || '', db_user: d.db_user || '', db_password: d.db_password || '',
        db_connect_timeout: d.db_connect_timeout || 15,
        bo_url: d.bo_url || '', bo_user: d.bo_user || '', bo_password: d.bo_password || '',
        bo_auth_type: d.bo_auth_type || 'secEnterprise',
        bo_timeout: d.bo_timeout || 60,
        bo_proxy_url: d.bo_proxy_url || '',
        bo_verify_ssl: d.bo_verify_ssl !== false,
        automic_url: d.automic_url || '', automic_user: d.automic_user || '',
        automic_password: d.automic_password || '',
        connections: Object.entries(d.connections || {}).map(([name, entry]) => ({
          name,
          db_host: entry.db_host || '',
          db_name: entry.db_name || '',
          db_user: entry.db_user || '',
          db_password: entry.db_password || '',
          expanded: false,
        })),
        apiEndpoints: Object.entries(d.api_endpoints || {}).map(([name, entry]) => ({
          name,
          base_url: entry.base_url || '',
          method: entry.method || 'GET',
          auth_type: entry.auth_type || 'none',
          api_key_header: entry.api_key_header || 'X-API-Key',
          api_key: entry.api_key || '',
          bearer_token: entry.bearer_token || '',
          basic_username: entry.basic_username || '',
          basic_password: entry.basic_password || '',
          headers_raw: Object.entries(entry.headers || {}).map(([k, v]) => `${k}: ${v}`).join('\n'),
          query_params_raw: Object.entries(entry.query_params || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
          body_raw: entry.body ? JSON.stringify(entry.body, null, 2) : '',
          timeout: entry.timeout ?? 30,
          verify_ssl: entry.verify_ssl !== false,
          response_format: entry.response_format || 'json',
          json_root_path: entry.json_root_path || '',
          pagination_type: entry.pagination_type || 'none',
          pagination_cursor_path: entry.pagination_cursor_path || '',
          pagination_cursor_param: entry.pagination_cursor_param || 'cursor',
          pagination_page_param: entry.pagination_page_param || 'page',
          pagination_size_param: entry.pagination_size_param || 'limit',
          pagination_page_size: entry.pagination_page_size ?? 100,
          pagination_max_pages: entry.pagination_max_pages ?? 50,
          expanded: false,
          previewResult: null,
          previewError: '',
          testResult: null,
        })),
      };
      this.configValidation = null;
      this.showConfigModal = true;
    },

    _configDataFromModal() {
      const m = this.configModal;
      const data = {
        db_host: m.db_host || 'localhost',
        db_port: Number(m.db_port) || 1433,
        db_name: m.db_name || '',
        db_user: m.db_user || '',
        db_password: m.db_password || '',
        db_driver: 'ODBC Driver 17 for SQL Server',
        db_pool_size: 5, db_pool_overflow: 10, db_pool_timeout: 30,
        db_pool_recycle: 3600,
        db_connect_timeout: Number(m.db_connect_timeout) || 15,
        bo_url: m.bo_url || '', bo_user: m.bo_user || '',
        bo_password: m.bo_password || '',
        bo_auth_type: m.bo_auth_type || 'secEnterprise',
        bo_timeout: Number(m.bo_timeout) || 60,
        bo_proxy_url: m.bo_proxy_url || '',
        bo_verify_ssl: m.bo_verify_ssl !== false,
        automic_url: m.automic_url || '', automic_user: m.automic_user || '',
        automic_password: m.automic_password || '',
        automic_timeout: 30, automic_max_retries: 3,
      };
      if (m.connections && m.connections.length > 0) {
        data.connections = Object.fromEntries(
          m.connections
            .filter(c => c.name.trim())
            .map(c => [c.name.trim(), {
              ...(c.db_host ? { db_host: c.db_host } : {}),
              ...(c.db_name ? { db_name: c.db_name } : {}),
              ...(c.db_user ? { db_user: c.db_user } : {}),
              ...(c.db_password ? { db_password: c.db_password } : {}),
            }])
        );
      }
      if (m.apiEndpoints && m.apiEndpoints.length > 0) {
        data.api_endpoints = Object.fromEntries(
          m.apiEndpoints
            .filter(e => e.name.trim() && e.base_url.trim())
            .map(e => {
              const headers = {};
              (e.headers_raw || '').split('\n').forEach(line => {
                const idx = line.indexOf(':');
                if (idx > 0) headers[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
              });
              const query_params = {};
              (e.query_params_raw || '').split('\n').forEach(line => {
                const idx = line.indexOf('=');
                if (idx > 0) query_params[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
              });
              let body = null;
              if (e.body_raw && e.body_raw.trim()) {
                try { body = JSON.parse(e.body_raw); } catch { body = null; }
              }
              return [e.name.trim(), {
                base_url: e.base_url.trim(),
                method: e.method || 'GET',
                auth_type: e.auth_type || 'none',
                api_key_header: e.api_key_header || 'X-API-Key',
                api_key: e.api_key || '',
                bearer_token: e.bearer_token || '',
                basic_username: e.basic_username || '',
                basic_password: e.basic_password || '',
                headers, query_params, body,
                timeout: Number(e.timeout) || 30,
                verify_ssl: e.verify_ssl !== false,
                response_format: e.response_format || 'json',
                json_root_path: e.json_root_path || '',
                pagination_type: e.pagination_type || 'none',
                pagination_cursor_path: e.pagination_cursor_path || '',
                pagination_cursor_param: e.pagination_cursor_param || 'cursor',
                pagination_page_param: e.pagination_page_param || 'page',
                pagination_size_param: e.pagination_size_param || 'limit',
                pagination_page_size: Number(e.pagination_page_size) || 100,
                pagination_max_pages: Number(e.pagination_max_pages) || 50,
              }];
            })
        );
      }
      return data;
    },

    addNamedConnection() {
      const idx = this.configModal.connections.length + 1;
      this.configModal.connections.push({
        name: `connection_${idx}`,
        db_host: '', db_name: '', db_user: '', db_password: '',
        expanded: true,
      });
    },

    removeNamedConnection(idx) {
      this.configModal.connections.splice(idx, 1);
    },

    toggleNamedConnection(idx) {
      this.configModal.connections[idx].expanded = !this.configModal.connections[idx].expanded;
    },

    namedConnectionSummary(conn) {
      const parts = [conn.db_host, conn.db_name].filter(Boolean);
      return parts.length ? parts.join(' / ') : 'not configured';
    },

    addApiEndpoint() {
      const idx = this.configModal.apiEndpoints.length + 1;
      this.configModal.apiEndpoints.push({
        name: `endpoint_${idx}`, base_url: '', method: 'GET',
        auth_type: 'none', api_key_header: 'X-API-Key', api_key: '',
        bearer_token: '', basic_username: '', basic_password: '',
        headers_raw: '', query_params_raw: '', body_raw: '',
        timeout: 30, verify_ssl: true,
        response_format: 'json', json_root_path: '',
        pagination_type: 'none', pagination_cursor_path: '',
        pagination_cursor_param: 'cursor', pagination_page_param: 'page',
        pagination_size_param: 'limit', pagination_page_size: 100, pagination_max_pages: 50,
        expanded: true, previewResult: null, previewError: '', testResult: null,
      });
    },

    removeApiEndpoint(idx) {
      this.configModal.apiEndpoints.splice(idx, 1);
    },

    toggleApiEndpoint(idx) {
      this.configModal.apiEndpoints[idx].expanded = !this.configModal.apiEndpoints[idx].expanded;
    },

    async testApiEndpoint(idx) {
      const m = this.configModal;
      const ep = m.apiEndpoints[idx];
      if (!m.id) { ep.testResult = { ok: false, message: 'Save the config first, then test.' }; return; }
      try {
        ep.testResult = await api('POST', '/api/adapters/rest-api/test', {
          config_id: m.id, endpoint_name: ep.name,
        });
      } catch (e) {
        ep.testResult = { ok: false, message: e.message };
      }
    },

    async previewApiEndpoint(idx) {
      const m = this.configModal;
      const ep = m.apiEndpoints[idx];
      if (!m.id) { ep.previewError = 'Save the config first, then preview.'; return; }
      ep.previewError = '';
      try {
        ep.previewResult = await api('POST', '/api/adapters/rest-api/preview', {
          config_id: m.id, endpoint_name: ep.name, limit: 20,
        });
      } catch (e) {
        ep.previewError = e.message;
      }
    },

    async validateConfig() {
      try {
        this.configValidation = await api('POST', '/api/configs/validate', {
          env_name: this.configModal.env_name,
          config_data: this._configDataFromModal(),
        });
      } catch (e) {
        this.configValidation = { ok: false, errors: [{ field_name: 'request', message: e.message }] };
      }
    },

    async saveConfig() {
      const m = this.configModal;
      const config_data = this._configDataFromModal();
      try {
        if (m.id) {
          await api('PUT', `/api/configs/${m.id}`, { config_data, name: m.name, env_name: m.env_name });
        } else {
          await api('POST', '/api/configs', { name: m.name, env_name: m.env_name, config_data });
        }
        await this.loadConfigs();
        this.showConfigModal = false;
        this.toast('success', 'Config saved', m.name);
      } catch (e) {
        this.toast('error', 'Save failed', e.message);
      }
    },

    async deleteConfig(id) {
      if (!confirm('Delete this configuration?')) return;
      try {
        await api('DELETE', `/api/configs/${id}`);
        await this.loadConfigs();
        this.toast('success', 'Config deleted');
      } catch (e) {
        this.toast('error', 'Delete failed', e.message);
      }
    },

    async importYaml() {
      this.yamlImporting = true;
      try {
        const r = await api('POST', '/api/configs/import-yaml', { yaml_content: this.yamlImportText });
        this.yamlImportText = '';
        this.yamlImportOpen = false;
        await this.loadConfigs();
        this.toast('success', 'YAML imported', `${r.environments?.length || 0} environment(s)`);
      } catch (e) {
        this.toast('error', 'Import failed', e.message);
      } finally {
        this.yamlImporting = false;
      }
    },

    // ===========================================================
    // JOBS / LAUNCH
    // ===========================================================
    async loadJobs() {
      try { this.jobs = await api('GET', '/api/jobs'); } catch {}
    },

    isJobSelected(name) {
      return this.selectedJobs.includes(name);
    },

    toggleJobSelection(job) {
      const idx = this.selectedJobs.indexOf(job.name);
      if (idx >= 0) this.selectedJobs.splice(idx, 1);
      else this.selectedJobs.push(job.name);
    },

    openNewJobModal() {
      const _pendingQuery = sessionStorage.getItem('etl_pending_query') || '';
      sessionStorage.removeItem('etl_pending_query');
      this.jobModal = {
        name: '', description: '', job_type: 'reconciliation', query: _pendingQuery,
        key_columns_raw: 'id', tags_raw: '', enabled: true,
        depends_on_raw: '', rules: [],
        bo_report_id: '', bo_page_id: '', bo_format: 'xlsx',
        automic_job_name: '', automic_run_id: '',
        api_source_endpoint: '', api_target_endpoint: '',
        dbt_manifest_path: '', dbt_run_results_path: '',
        pass_min_row_count: '',
        pass_max_row_count: '',
        pass_max_value_mismatches: '',
        pass_max_missing_in_target: '',
        pass_max_missing_in_source: '',
        pass_require_status: '',
        pass_sql: '',
        pass_sql_mode: 'rows_mean_pass',
        freshness_ts_col: '', freshness_max_hours: 24,
        profile_columns: '', profile_drift_pct: 20,
        snapshot_environment: 'source',
        cja_source_job: '', cja_source_metric: 'count', cja_source_col: '',
        cja_target_job: '', cja_target_metric: 'count', cja_target_col: '',
        cja_tolerance: 0, cja_tolerance_type: 'absolute',
        previewConfigId: String(this.launchSettings.config_id || ''),
        previewLoading: false,
        previewResult: null,
        previewError: '',
      };
      this.jobModalEditing = false;
      this.validateJobResult = null;
      this.jobModalValidation = { sql: '', keyColumns: '', dependencies: '' };
      this.jobModalTab = 'basic';  // Task 8: reset tab
      this.showJobModal = true;
    },

    openEditJobModal(job) {
      this.jobModal = {
        name: job.name, description: job.description || '',
        job_type: job.job_type || 'reconciliation',
        query: job.query || '', key_columns_raw: (job.key_columns || ['id']).join(', '),
        tags_raw: (job.tags || []).join(', '), enabled: job.enabled !== false,
        depends_on_raw: (job.depends_on || []).join(', '),
        rules: (job.rules || []).map(r => ({ ...r })),
        bo_report_id: job.params?.report_id || '',
        bo_page_id: job.params?.bo_report_id || '',
        bo_format: job.params?.format || 'xlsx',
        automic_job_name: job.params?.job_name || '',
        automic_run_id: job.params?.run_id || '',
        api_source_endpoint: job.params?.source_api_endpoint || '',
        api_target_endpoint: job.params?.target_api_endpoint || '',
        dbt_manifest_path: job.params?.manifest_path || '',
        dbt_run_results_path: job.params?.run_results_path || '',
        pass_min_row_count: job.pass_condition?.min_row_count ?? '',
        pass_max_row_count: job.pass_condition?.max_row_count ?? '',
        pass_max_value_mismatches: job.pass_condition?.max_value_mismatches ?? '',
        pass_max_missing_in_target: job.pass_condition?.max_missing_in_target ?? '',
        pass_max_missing_in_source: job.pass_condition?.max_missing_in_source ?? '',
        pass_require_status: (job.pass_condition?.require_status || []).join(', '),
        pass_sql: job.pass_condition?.pass_sql || '',
        pass_sql_mode: job.pass_condition?.pass_sql_mode || 'rows_mean_pass',
        freshness_ts_col: job.params?.timestamp_column || '',
        freshness_max_hours: job.params?.max_age_hours ?? 24,
        profile_columns: (job.params?.columns || []).join(', '),
        profile_drift_pct: job.params?.drift_threshold_pct ?? 20,
        snapshot_environment: job.params?.environment || 'source',
        cja_source_job: job.params?.source_job || '',
        cja_source_metric: job.params?.source_metric || 'count',
        cja_source_col: job.params?.source_column || '',
        cja_target_job: job.params?.target_job || '',
        cja_target_metric: job.params?.target_metric || 'count',
        cja_target_col: job.params?.target_column || '',
        cja_tolerance: job.params?.tolerance ?? 0,
        cja_tolerance_type: job.params?.tolerance_type || 'absolute',
        previewConfigId: String(job.config_id || this.launchSettings.config_id || ''),
        previewLoading: false,
        previewResult: null,
        previewError: '',
      };
      this.jobModalEditing = true;
      this.validateJobResult = null;
      this.jobModalValidation = { sql: '', keyColumns: '', dependencies: '' };
      this.jobModalTab = 'basic';  // Task 8: reset tab
      this.showJobModal = true;
    },

    async previewJobQuery() {
      const query = this.jobModal.query?.trim();
      const configId = this.jobModal.previewConfigId;
      if (!query || !configId) {
        this.jobModal.previewError = !configId ? 'Select a config to preview against.' : 'Enter a query first.';
        return;
      }
      this.jobModal.previewLoading = true;
      this.jobModal.previewResult = null;
      this.jobModal.previewError = '';
      try {
        const resp = await fetch(`/api/configs/${configId}/preview-query`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.token}` },
          body: JSON.stringify({ query, limit: 50 }),
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          this.jobModal.previewError = err.detail || `Error ${resp.status}`;
        } else {
          this.jobModal.previewResult = await resp.json();
        }
      } catch (e) {
        this.jobModal.previewError = e.message || 'Network error';
      } finally {
        this.jobModal.previewLoading = false;
      }
    },

    addDQRule() {
      this.jobModal.rules.push({ type: 'not_null', column: '', severity: 'error', min_value: null, max_value: null, pattern: null, percentile: null, operator: null, column_b: null, lookup_query: null, expected_type: null });
    },

    removeDQRule(idx) {
      this.jobModal.rules.splice(idx, 1);
    },

    async validateJob() {
      const m = this.jobModal;
      if (!m.name || !this.jobModalEditing) return;
      this.validateJobLoading = true;
      this.validateJobResult = null;
      try {
        const s = this.launchSettings;
        this.validateJobResult = await api('POST', `/api/jobs/${encodeURIComponent(m.name)}/validate`, {
          source_env: s.source_env,
          target_env: s.target_env,
          config_data: {},
        });
      } catch (e) {
        this.validateJobResult = { source_ok: false, target_ok: false, errors: [e.message] };
      } finally {
        this.validateJobLoading = false;
      }
    },

    async saveJob() {
      const m = this.jobModal;
      const params = {};
      if (m.job_type === 'automic_job') {
        if (m.automic_job_name) params.job_name = m.automic_job_name;
        if (m.automic_run_id) params.run_id = m.automic_run_id;
      }
      if (m.job_type === 'api_reconciliation') {
        params.source_api_endpoint = m.api_source_endpoint;
        params.target_api_endpoint = m.api_target_endpoint;
      }
      if (m.job_type === 'bo_report') {
        if (m.bo_report_id) params.report_id = m.bo_report_id;
        if (m.bo_page_id) params.bo_report_id = m.bo_page_id;
        params.format = m.bo_format || 'xlsx';
      }
      if (m.job_type === 'dbt_artifact') {
        if (m.dbt_manifest_path) params.manifest_path = m.dbt_manifest_path;
        if (m.dbt_run_results_path) params.run_results_path = m.dbt_run_results_path;
      }
      if (m.job_type === 'freshness') {
        if (m.freshness_ts_col) params.timestamp_column = m.freshness_ts_col;
        params.max_age_hours = Number(m.freshness_max_hours) || 24;
      }
      if (m.job_type === 'profile') {
        const cols = m.profile_columns.split(',').map(s => s.trim()).filter(Boolean);
        if (cols.length) params.columns = cols;
        params.drift_threshold_pct = Number(m.profile_drift_pct) || 20;
      }
      if (m.job_type === 'schema_snapshot') {
        params.environment = m.snapshot_environment || 'source';
      }
      if (m.job_type === 'cross_job_assertion') {
        params.source_job = m.cja_source_job;
        params.source_metric = m.cja_source_metric || 'count';
        if (m.cja_source_col) params.source_column = m.cja_source_col;
        params.target_job = m.cja_target_job;
        params.target_metric = m.cja_target_metric || 'count';
        if (m.cja_target_col) params.target_column = m.cja_target_col;
        params.tolerance = Number(m.cja_tolerance) || 0;
        params.tolerance_type = m.cja_tolerance_type || 'absolute';
      }
      const keyColumns = ['reconciliation', 'bo_report', 'api_reconciliation'].includes(m.job_type)
        ? m.key_columns_raw.split(',').map(s => s.trim()).filter(Boolean)
        : [];
      const pc = {};
      if (m.pass_min_row_count !== '') pc.min_row_count = Number(m.pass_min_row_count);
      if (m.pass_max_row_count !== '') pc.max_row_count = Number(m.pass_max_row_count);
      if (m.pass_max_value_mismatches !== '') pc.max_value_mismatches = Number(m.pass_max_value_mismatches);
      if (m.pass_max_missing_in_target !== '') pc.max_missing_in_target = Number(m.pass_max_missing_in_target);
      if (m.pass_max_missing_in_source !== '') pc.max_missing_in_source = Number(m.pass_max_missing_in_source);
      if (m.pass_require_status) pc.require_status = m.pass_require_status.split(',').map(s => s.trim()).filter(Boolean);
      if (m.pass_sql?.trim()) { pc.pass_sql = m.pass_sql.trim(); pc.pass_sql_mode = m.pass_sql_mode; }
      const body = {
        name: m.name, description: m.description,
        job_type: m.job_type,
        query: ['reconciliation', 'freshness', 'profile', 'schema_snapshot'].includes(m.job_type) ? m.query : '',
        key_columns: keyColumns,
        tags: m.tags_raw.split(',').map(s => s.trim()).filter(Boolean),
        enabled: m.enabled,
        depends_on: m.depends_on_raw.split(',').map(s => s.trim()).filter(Boolean),
        rules: (m.rules || []).filter(r => r.type),
        params,
        pass_condition: Object.keys(pc).length ? pc : null,
      };
      try {
        if (this.jobModalEditing) {
          await api('PUT', `/api/jobs/${encodeURIComponent(m.name)}`, body);
        } else {
          await api('POST', '/api/jobs', body);
        }
        await this.loadJobs();
        this.showJobModal = false;
        this.toast('success', this.jobModalEditing ? 'Job updated' : 'Job created', m.name);
      } catch (e) {
        this.toast('error', 'Save failed', e.message);
      }
    },

    canSaveJob() {
      const m = this.jobModal;
      if (!m?.name) return false;
      if (m.job_type === 'reconciliation') {
        return Boolean(m.query?.trim() && m.key_columns_raw?.split(',').map(s => s.trim()).filter(Boolean).length);
      }
      if (m.job_type === 'bo_report') return Boolean(m.bo_report_id && m.bo_page_id);
      if (m.job_type === 'automic_job') return Boolean(m.automic_job_name || m.automic_run_id);
      if (m.job_type === 'api_reconciliation') {
        return Boolean(
          m.api_source_endpoint && m.api_target_endpoint &&
          m.key_columns_raw?.split(',').map(s => s.trim()).filter(Boolean).length
        );
      }
      if (m.job_type === 'dbt_artifact') return Boolean(m.dbt_run_results_path);
      if (m.job_type === 'freshness') return Boolean(m.query?.trim() && m.freshness_ts_col);
      if (m.job_type === 'profile') return Boolean(m.query?.trim());
      if (m.job_type === 'schema_snapshot') return Boolean(m.query?.trim());
      if (m.job_type === 'cross_job_assertion') return Boolean(m.cja_source_job && m.cja_target_job);
      return true;
    },

    async deleteJob(name) {
      if (!confirm(`Delete job "${name}"?`)) return;
      try {
        await api('DELETE', `/api/jobs/${encodeURIComponent(name)}`);
        this.jobs = this.jobs.filter(j => j.name !== name);
        this.selectedJobs = this.selectedJobs.filter(n => n !== name);
        this.toast('success', 'Job deleted', name);
      } catch (e) {
        this.toast('error', 'Delete failed', e.message);
      }
    },

    _runSettingsPayload() {
      const s = this.launchSettings;
      return {
        execution_mode: s.execution_mode,
        max_workers: Number(s.max_workers),
        max_duration_seconds: Number(s.max_duration_seconds),
        float_tolerance: s.float_tolerance,
        schema_mismatch_policy: s.schema_mismatch_policy,
        null_equals_null: Boolean(s.null_equals_null),
        chunk_size: Number(s.chunk_size),
        use_hash_precheck: Boolean(s.use_hash_precheck),
        comparison_backend: s.comparison_backend,
        health_check: Boolean(s.health_check),
        metrics_enabled: Boolean(s.metrics_enabled),
        use_live_connections: Boolean(s.use_live_connections),
        notes: s.notes,
        max_retries: Number(s.max_retries) || 0,
        retry_delay_seconds: Number(s.retry_delay_seconds) || 30,
      };
    },

    getStepCfg(name) {
      if (!this.stepSettings[name]) {
        this.stepSettings[name] = {
          hold_after: false, wait_seconds: 0,
          require_status: '', max_mismatch_count: '',
          min_row_count: '', max_row_count: '',
          max_value_mismatches: '', max_missing_in_target: '', max_missing_in_source: '',
        };
      }
      return this.stepSettings[name];
    },

    _buildJobSequence() {
      return this.selectedJobs.map(name => {
        const s = this.stepSettings[name] || {};
        const step = { job_name: name };
        if (s.hold_after) step.hold_after = true;
        if (Number(s.wait_seconds) > 0) step.wait_seconds = Number(s.wait_seconds);
        const hasCondition = s.require_status
          || (s.max_mismatch_count !== '' && s.max_mismatch_count != null)
          || (s.min_row_count !== '' && s.min_row_count != null)
          || (s.max_row_count !== '' && s.max_row_count != null)
          || (s.max_value_mismatches !== '' && s.max_value_mismatches != null)
          || (s.max_missing_in_target !== '' && s.max_missing_in_target != null)
          || (s.max_missing_in_source !== '' && s.max_missing_in_source != null);
        if (hasCondition) {
          step.condition = {};
          if (s.require_status) step.condition.require_status = s.require_status.split(',').map(x => x.trim()).filter(Boolean);
          if (s.max_mismatch_count !== '' && s.max_mismatch_count != null) step.condition.max_mismatch_count = Number(s.max_mismatch_count);
          if (s.min_row_count !== '' && s.min_row_count != null) step.condition.min_row_count = Number(s.min_row_count);
          if (s.max_row_count !== '' && s.max_row_count != null) step.condition.max_row_count = Number(s.max_row_count);
          if (s.max_value_mismatches !== '' && s.max_value_mismatches != null) step.condition.max_value_mismatches = Number(s.max_value_mismatches);
          if (s.max_missing_in_target !== '' && s.max_missing_in_target != null) step.condition.max_missing_in_target = Number(s.max_missing_in_target);
          if (s.max_missing_in_source !== '' && s.max_missing_in_source != null) step.condition.max_missing_in_source = Number(s.max_missing_in_source);
        }
        return step;
      });
    },

    launchConfigConnections() {
      const cfg = this.configs.find(c => String(c.id) === String(this.launchSettings.config_id));
      if (!cfg || !cfg.config_data || !cfg.config_data.connections) return [];
      return Object.keys(cfg.config_data.connections);
    },

    configApiEndpointNames(configId) {
      const cfg = this.configs.find(c => String(c.id) === String(configId));
      if (!cfg || !cfg.config_data || !cfg.config_data.api_endpoints) return [];
      return Object.keys(cfg.config_data.api_endpoints);
    },

    async runTests() {
      if (!this.selectedJobs.length) return;
      this.isLaunching = true;
      try {
        const cfg = this.launchSettings.config_id
          ? this.configs.find(c => String(c.id) === String(this.launchSettings.config_id))
          : null;
        const run = await api('POST', '/api/runs', {
          source_env: this.launchSettings.source_env,
          target_env: this.launchSettings.target_env,
          job_sequence: this._buildJobSequence(),
          config_id: cfg ? cfg.id : null,
          run_settings: this._runSettingsPayload(),
          config_data: cfg ? cfg.config_data : {},
          source_connection: this.launchSettings.source_connection || null,
          target_connection: this.launchSettings.target_connection || null,
        });
        this.activeRuns.unshift(run);
        this.startRunStream(run);
        this.selectedJobs = [];
        this.stepSettings = {};
        this.stepSettingsOpen = {};
        this.currentView = 'monitor';
        this.toast('success', 'Run started', `ID: ${run.run_id.substring(0,8)}…`);
      } catch (e) {
        this.toast('error', 'Launch failed', e.message);
      } finally {
        this.isLaunching = false;
      }
    },

    // ===========================================================
    // MONITOR
    // ===========================================================
    startPolling() {
      this.pollTimer = setInterval(() => this.pollActiveRuns(), 5000);
    },

    isTerminalStatus(status) {
      return ['PASSED','FAILED','SLOW','ERROR','COMPLETED','CANCELLED'].includes(status);
    },

    startRunStream(run) {
      if (!window.EventSource || !run?.run_id || this.runStreams[run.run_id] || this.isTerminalStatus(run.status)) return;
      const stream = new EventSource(API + `/api/runs/${run.run_id}/stream`);
      this.runStreams[run.run_id] = stream;
      stream.addEventListener('progress', (event) => {
        const progress = JSON.parse(event.data);
        const idx = this.activeRuns.findIndex(r => r.run_id === progress.run_id);
        if (idx >= 0) {
          Object.assign(this.activeRuns[idx], {
            status: progress.status,
            total_tests: progress.total_tests,
            _progress: progress,
          });
        }
        if (progress.held_step != null) {
          this.loadRunSteps(progress.run_id);
        }
      });
      stream.addEventListener('done', async (event) => {
        const progress = JSON.parse(event.data);
        const idx = this.activeRuns.findIndex(r => r.run_id === progress.run_id);
        if (idx >= 0) Object.assign(this.activeRuns[idx], { status: progress.status, _progress: progress });
        this.closeRunStream(progress.run_id);
        // --- Task 2: Save per-job last run statuses ---
        if (progress.job_results) {
          progress.job_results.forEach(jr => {
            if (jr.job_name && jr.status) this.saveJobRunStatus(jr.job_name, jr.status);
          });
        }
        // --- Task 12: Save per-job durations ---
        if (progress.job_results) {
          progress.job_results.forEach(jr => {
            if (jr.job_name && jr.duration_seconds != null) {
              this.saveJobDuration(jr.job_name, jr.duration_seconds);
            }
          });
        }
        await this.loadRuns();
      });
      stream.onerror = () => this.closeRunStream(run.run_id);
    },

    closeRunStream(runId) {
      if (this.runStreams[runId]) {
        this.runStreams[runId].close();
        delete this.runStreams[runId];
      }
    },

    async pollActiveRuns() {
      const liveRuns = this.activeRuns.filter(r => ['PENDING', 'RUNNING'].includes(r.status));
      for (const run of liveRuns) {
        try {
          const [status, progress] = await Promise.all([
            api('GET', `/api/runs/${run.run_id}/status`),
            api('GET', `/api/runs/${run.run_id}/progress`).catch(() => null),
          ]);
          const idx = this.activeRuns.findIndex(r => r.run_id === run.run_id);
          if (idx >= 0) {
            Object.assign(this.activeRuns[idx], status);
            if (progress) this.activeRuns[idx]._progress = progress;
          }
        } catch {}
      }
      // also refresh monitor list from server periodically
      if (this.currentView === 'monitor') {
        await this.loadRuns();
        for (const run of this.runs.filter(r => ['PENDING','RUNNING'].includes(r.status))) {
          if (!this.activeRuns.find(a => a.run_id === run.run_id)) {
            this.activeRuns.unshift(run);
          }
          this.startRunStream(run);
        }
      }
    },

    runProgress(run) {
      if (this.isTerminalStatus(run.status)) return 100;
      if (run.status === 'PENDING') return 5;
      if (run._progress) return run._progress.percent_complete || 5;
      const done = (run.passed||0) + (run.failed||0) + (run.slow||0) + (run.error||0);
      return run.total_tests > 0 ? Math.round(done / run.total_tests * 100) : 10;
    },

    async loadRunSteps(runId) {
      try {
        this.runStepsCache[runId] = await api('GET', `/api/runs/${runId}/steps`);
      } catch {
        this.runStepsCache[runId] = [];
      }
    },

    openStepRelease(runId, stepIndex) {
      this.stepReleaseModal = { show: true, runId, stepIndex, releasedBy: '', note: '', action: 'approve' };
    },

    async submitStepRelease() {
      const m = this.stepReleaseModal;
      if (!m.note.trim() || !m.releasedBy.trim()) {
        this.toast('error', 'Required', 'Name and note are required to release a hold');
        return;
      }
      try {
        await api('POST', `/api/runs/${m.runId}/steps/${m.stepIndex}/release`, {
          action: m.action,
          note: m.note.trim(),
          released_by: m.releasedBy.trim(),
        });
        this.stepReleaseModal.show = false;
        await this.loadRunSteps(m.runId);
        this.toast('success', 'Step released', `Action: ${m.action}`);
      } catch(e) {
        this.toast('error', 'Release failed', e.message);
      }
    },

    stepStatusBadgeClass(status) {
      const map = {
        PENDING: 'badge-gray', RUNNING: 'badge-blue', HELD: 'badge-amber',
        APPROVED: 'badge-green', SKIPPED: 'badge-blue', CANCELLED: 'badge-gray',
        PASSED: 'badge-green', FAILED: 'badge-rose', ERROR: 'badge-rose',
      };
      return map[status] || 'badge-gray';
    },

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

    totalMismatches(r) {
      return (r.value_mismatch_count || 0) + (r.missing_in_target_count || 0) + (r.missing_in_source_count || 0);
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
    // MISMATCH DRAWER
    // ===========================================================
    async openMismatchDrawer(runId, result) {
      this.drawer = { show: true, loading: true, runId, result, rows: [], offset: 0 };
      await this._fetchMismatches();
    },

    async _fetchMismatches() {
      try {
        const rows = await api('GET',
          `/api/runs/${this.drawer.runId}/results/${this.drawer.result.id}/mismatches?limit=100&offset=${this.drawer.offset}`);
        this.drawer.rows = this.drawer.offset === 0 ? rows : [...this.drawer.rows, ...rows];
      } catch (e) {
        this.toast('error', 'Mismatches load failed', e.message);
      } finally {
        this.drawer.loading = false;
      }
    },

    async loadMoreMismatches() {
      this.drawer.offset += 100;
      this.drawer.loading = true;
      await this._fetchMismatches();
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
    // COMPARE RUNS
    // ===========================================================
    async loadCompare() {
      if (!this.compareRunA || !this.compareRunB) return;
      if (this.compareRunA === this.compareRunB) {
        this.toast('warn', 'Same run', 'Select two different runs to compare');
        return;
      }
      this.compareLoading = true;
      this.compareResult = null;
      try {
        this.compareResult = await api('GET',
          `/api/runs/compare?run_a=${this.compareRunA}&run_b=${this.compareRunB}`);
      } catch (e) {
        this.toast('error', 'Compare failed', e.message);
      } finally {
        this.compareLoading = false;
      }
    },

    compareDelta(test) {
      const a = test.status_a, b = test.status_b;
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
    _isTerminalStatus(status) {
      return ['PASSED', 'FAILED', 'SLOW', 'ERROR', 'COMPLETED'].includes(status);
    },

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
      const adv = {
        float_tolerance: parseFloat(this[`${p}FloatTolerance`]) || 1e-9,
        column_tolerances: this._parseColumnTolerances(this[`${p}ColumnTolerances`]),
        datetime_tolerance_seconds: parseFloat(this[`${p}DatetimeTolerance`]) || 0,
        case_insensitive_columns: (this[`${p}CaseInsensitiveColumns`] || '').split(',').map(s => s.trim()).filter(Boolean),
        whitespace_normalize_columns: (this[`${p}WhitespaceNormalizeColumns`] || '').split(',').map(s => s.trim()).filter(Boolean),
        comparison_backend: this[`${p}Backend`] || 'pandas',
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
        if (this._isTerminalStatus(status.status)) {
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
        if (this._isTerminalStatus(pair.run_a.status) && this._isTerminalStatus(pair.run_b.status)) {
          clearInterval(this.dualEnvPollInterval);
          this.dualEnvPollInterval = null;
          this.dualEnvResult = await api('GET', `/api/runs/compare?run_a=${runIdA}&run_b=${runIdB}`);
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
            if (this._isTerminalStatus(st.status)) {
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
            if (this._isTerminalStatus(st.status)) {
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
      this.sqlExpandedDiffs = { ...this.sqlExpandedDiffs, [name]: { open: true, loading: true, data: [], error: null, hasMore: false, offset: 0 } };
      try {
        const details = await api('GET', `/api/runs/${runId}/mismatches?result_name=${encodeURIComponent(name)}&limit=100&offset=0`);
        const rows = Array.isArray(details) ? details : (details.items || []);
        const hasMore = Array.isArray(details) ? false : (details.has_more ?? false);
        this.sqlExpandedDiffs = { ...this.sqlExpandedDiffs, [name]: { open: true, loading: false, data: rows, error: null, hasMore, offset: rows.length } };
      } catch (e) {
        this.sqlExpandedDiffs = { ...this.sqlExpandedDiffs, [name]: { open: true, loading: false, data: [], error: e.message, hasMore: false, offset: 0 } };
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

    toggleAcceptForm(mismatchId) {
      if (this.acceptForms[mismatchId]?.open) {
        const copy = { ...this.acceptForms };
        delete copy[mismatchId];
        this.acceptForms = copy;
        return;
      }
      this.acceptForms = { ...this.acceptForms, [mismatchId]: { open: true, note: '' } };
    },

    async submitAccept(runId, resultId, mismatchId) {
      const form = this.acceptForms[mismatchId];
      if (!form || !form.note) return;
      try {
        const result = await api('PATCH',
          `/api/runs/${runId}/results/${resultId}/mismatches/${mismatchId}/accept`,
          { note: form.note });

        const patchRow = (m) => m.id === mismatchId
          ? { ...m, accepted: result.accepted, accepted_note: result.accepted_note, accepted_at: result.accepted_at, accepted_by: result.accepted_by }
          : m;
        if (this.expandedMismatches[resultId]) {
          this.expandedMismatches = {
            ...this.expandedMismatches,
            [resultId]: this.expandedMismatches[resultId].map(patchRow),
          };
        }
        if (this.drawer.result && this.drawer.result.id === resultId) {
          this.drawer.rows = this.drawer.rows.map(patchRow);
        }

        const copy = { ...this.acceptForms };
        delete copy[mismatchId];
        this.acceptForms = copy;
        if (result.result_status_updated) {
          this.toast('success', 'Test passed', 'All mismatches accepted');
          if (this.selectedRun && this.selectedRun.run_id === runId) {
            await this.viewRunDetail(runId);
          }
          await this.loadRuns();
        } else {
          this.toast('success', 'Accepted', 'Mismatch accepted');
        }
      } catch (e) {
        this.toast('error', 'Accept failed', e.message);
      }
    },

    async loadPastPairs() {
      this.pastPairsLoading = true;
      try {
        this.pastPairs = await api('GET', '/api/compare/pairs');
      } catch (e) {
        this.toast('error', 'Load pairs failed', e.message);
      } finally {
        this.pastPairsLoading = false;
      }
    },

    // ===========================================================
    // ADAPTERS – SAP BO
    // ===========================================================
    async testBOConnection() {
      if (!this.boConfigId) return;
      this.boTesting = true;
      this.boTestResult = null;
      try {
        this.boTestResult = await api('POST', '/api/adapters/sap-bo/test', { config_id: Number(this.boConfigId) });
        if (this.boTestResult.ok) this.toast('success', 'SAP BO connected', `${this.boTestResult.latency_ms}ms`);
        else this.toast('error', 'Connection failed', this.boTestResult.message);
      } catch (e) {
        this.boTestResult = { ok: false, message: e.message };
        this.toast('error', 'Connection error', e.message);
      } finally {
        this.boTesting = false;
      }
    },

    async loadBODocuments() {
      if (!this.boConfigId) return;
      this.boLoading = true;
      this.boDocs = [];
      this.expandedBODocs = [];
      this.boReports = {};
      try {
        this.boDocs = await api('GET', `/api/adapters/sap-bo/documents?config_id=${this.boConfigId}`);
        this.toast('success', `${this.boDocs.length} documents loaded`);
      } catch (e) {
        this.toast('error', 'Load failed', e.message);
      } finally {
        this.boLoading = false;
      }
    },

    async toggleBODoc(doc) {
      const idx = this.expandedBODocs.indexOf(doc.id);
      if (idx >= 0) {
        this.expandedBODocs.splice(idx, 1);
        return;
      }
      this.expandedBODocs.push(doc.id);
      if (!this.boReports[doc.id]) {
        try {
          const reports = await api('GET',
            `/api/adapters/sap-bo/documents/${doc.id}/reports?config_id=${this.boConfigId}`);
          this.boReports = { ...this.boReports, [doc.id]: reports };
        } catch (e) {
          this.boReports = { ...this.boReports, [doc.id]: [] };
          this.toast('error', 'Reports load failed', e.message);
        }
      }
    },

    async downloadBOReport(docId, reportId, format) {
      try {
        const { blob, disposition } = await apiBlob(
          `/api/adapters/sap-bo/documents/${docId}/reports/${reportId}/download?config_id=${this.boConfigId}&format=${format}`
        );
        const match = disposition.match(/filename="?([^"]+)"?/);
        triggerDownload(blob, match ? match[1] : `report_${docId}_${reportId}.${format}`);
        this.toast('success', 'Download started');
      } catch (e) {
        this.toast('error', 'Download failed', e.message);
      }
    },

    openAddBOJobModal(doc, rep) {
      this.boJobForm = {
        name: `bo_${doc.id}_${rep.id}`.replace(/[^a-z0-9_]/gi, '_').toLowerCase(),
        title: `${doc.name} – ${rep.name}`,
        doc_id: doc.id,
        report_id: rep.id,
        key_columns_raw: 'id',
        format: 'xlsx',
      };
      this.showBOJobModal = true;
    },

    async saveBOJob() {
      try {
        await api('POST', '/api/adapters/jobs/from-bo-report', {
          name: this.boJobForm.name,
          title: this.boJobForm.title,
          doc_id: this.boJobForm.doc_id,
          report_id: this.boJobForm.report_id,
          key_columns: this.boJobForm.key_columns_raw.split(',').map(s => s.trim()).filter(Boolean),
          format: this.boJobForm.format,
        });
        await this.loadJobs();
        this.showBOJobModal = false;
        this.toast('success', 'Job added', this.boJobForm.name);
      } catch (e) {
        this.toast('error', 'Save failed', e.message);
      }
    },

    // ===========================================================
    // ADAPTERS – Import from File
    // ===========================================================

    _parseCSV(text) {
      const lines = text.trim().split('\n').filter(l => l.trim());
      if (lines.length < 2) return [];
      const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, ''));
      return lines.slice(1).map(line => {
        const vals = line.split(',').map(v => v.trim().replace(/^"|"$/g, ''));
        const obj = {};
        headers.forEach((h, i) => { obj[h] = vals[i] || ''; });
        return obj;
      });
    },

    _csvRowToJobDef(row) {
      const params = {};
      if (row.job_name) params.job_name = row.job_name;
      if (row.run_id)   params.run_id   = row.run_id;
      return {
        name:        row.name || '',
        description: row.description || '',
        job_type:    row.job_type || 'automic_job',
        query:       '',
        key_columns: [],
        tags:        row.tags ? row.tags.split(/[,\s]+/).filter(Boolean) : [],
        params,
        enabled:     true,
      };
    },

    onFileSelected(event) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        const text = e.target.result;
        this.fileImportErrors = [];
        try {
          let rows;
          if (file.name.endsWith('.csv')) {
            rows = this._parseCSV(text).map(r => this._csvRowToJobDef(r));
          } else {
            rows = JSON.parse(text);
          }
          this.fileImportJobs = rows;
          const missing = rows.filter(r => !r.name);
          if (missing.length > 0) {
            this.fileImportErrors = [`${missing.length} row(s) missing "name" — fix the file and re-upload`];
          }
        } catch (err) {
          this.fileImportErrors = [`Parse error: ${err.message}`];
          this.fileImportJobs = [];
        }
      };
      reader.readAsText(file);
    },

    async importFromFile() {
      if (!this.fileImportJobs.length || this.fileImportErrors.length) return;
      this.fileImportLoading = true;
      try {
        const result = await api('POST', '/api/jobs/import', this.fileImportJobs);
        this.toast('success', 'Import complete', `${result.length} job(s) imported`);
        this.fileImportJobs = [];
        this.fileImportOpen = false;
        await this.loadJobs();
      } catch (e) {
        this.toast('error', 'Import failed', e.message);
      } finally {
        this.fileImportLoading = false;
      }
    },

    // ===========================================================
    // ADAPTERS – Browse & Import from Automic
    // ===========================================================

    async searchAutomic() {
      if (!this.browseAutomicConfigId || !this.browseAutomicFilter.trim()) return;
      this.browseAutomicLoading = true;
      this.browseAutomicResults = [];
      this.browseAutomicSelected = [];
      this.browseAutomicError = '';
      try {
        const qs = `config_id=${this.browseAutomicConfigId}&filter=${encodeURIComponent(this.browseAutomicFilter)}`;
        this.browseAutomicResults = await api('GET', `/api/adapters/automic/search?${qs}`);
        if (!this.browseAutomicResults.length) {
          this.browseAutomicError = 'No jobs found for that filter.';
        }
      } catch (e) {
        this.browseAutomicError = e.message;
      } finally {
        this.browseAutomicLoading = false;
      }
    },

    toggleBrowseSelection(name) {
      const idx = this.browseAutomicSelected.indexOf(name);
      if (idx >= 0) this.browseAutomicSelected.splice(idx, 1);
      else this.browseAutomicSelected.push(name);
    },

    isBrowseAllSelected() {
      return this.browseAutomicResults.length > 0 &&
             this.browseAutomicResults.every(r => this.browseAutomicSelected.includes(r.name));
    },

    toggleSelectAll() {
      if (this.isBrowseAllSelected()) {
        this.browseAutomicSelected = [];
      } else {
        this.browseAutomicSelected = this.browseAutomicResults.map(r => r.name);
      }
    },

    async importSelectedAutomic() {
      if (!this.browseAutomicSelected.length) return;
      this.browseAutomicImporting = true;
      try {
        const result = await api('POST', '/api/adapters/jobs/from-automic/bulk', {
          config_id: Number(this.browseAutomicConfigId),
          job_names: this.browseAutomicSelected,
        });
        const nImported = result.imported.length;
        const nErrors = Object.keys(result.errors).length;
        if (nErrors > 0) {
          this.toast('error', `${nImported} imported, ${nErrors} failed`,
            Object.keys(result.errors).join(', '));
        } else {
          this.toast('success', 'Import complete', `${nImported} job(s) added to catalog`);
        }
        this.browseAutomicSelected = [];
        await this.loadJobs();
      } catch (e) {
        this.toast('error', 'Import failed', e.message);
      } finally {
        this.browseAutomicImporting = false;
      }
    },

    // ===========================================================
    // ADAPTERS – Automic (single lookup — unchanged)
    // ===========================================================
    async lookupAutomic() {
      if (!this.automicConfigId || !this.automicIdentifier) return;
      this.automicLoading = true;
      this.automicResult = null;
      try {
        this.automicResult = await api('POST', '/api/adapters/automic/lookup', {
          config_id: Number(this.automicConfigId),
          identifier: this.automicIdentifier,
          id_type: this.automicIdType,
        });
        // persist to sessionStorage history
        const h = [this.automicResult, ...this.automicHistory.filter(
          x => x.identifier !== this.automicResult.identifier
        )].slice(0, 20);
        this.automicHistory = h;
        sessionStorage.setItem('automicHistory', JSON.stringify(h));
        this.toast('success', 'Lookup complete', `Status: ${this.automicResult.status}`);
      } catch (e) {
        this.toast('error', 'Lookup failed', e.message);
      } finally {
        this.automicLoading = false;
      }
    },

    async addAutomicJob() {
      if (!this.automicResult) return;
      try {
        const name = ('automic_' + this.automicResult.identifier).toLowerCase().replace(/[^a-z0-9_]/g, '_');
        const body = { name };
        if (this.automicResult.identifier_type === 'run_id') body.run_id = this.automicResult.identifier;
        else body.job_name = this.automicResult.identifier;
        await api('POST', '/api/adapters/jobs/from-automic', {
          ...body,
        });
        await this.loadJobs();
        this.toast('success', 'Job added', name);
      } catch (e) {
        this.toast('error', 'Save failed', e.message);
      }
    },

    // ===========================================================
    // REPORTS TAB
    // ===========================================================
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
      try {
        const { blob } = await apiBlob(`/api/runs/${this.reportRunId}/report`);
        this.reportBlobUrl = URL.createObjectURL(blob);
      } catch (e) {
        this.toast('error', 'Failed to load report', e.message);
      }
      this.reportLoaded = true;
      if (this.reportView === 'metrics') this.loadRunMetrics();
      if (this.reportView === 'logs') this.loadAllLogEvents();
    },

    async openReportTab(runId) {
      await this.openRunTab(runId, 'report');
    },

    async openRunTab(runId, suffix) {
      try {
        const { blob } = await apiBlob(`/api/runs/${runId}/${suffix}`);
        const url = URL.createObjectURL(blob);
        window.open(url, '_blank');
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      } catch (e) {
        this.toast('error', 'Failed to open', e.message);
      }
    },

    async loadAllLogEvents() {
      if (!this.reportRunId) return;
      this.allLogEventsLoading = true;
      this.allLogEvents = [];
      try {
        const data = await api('GET', `/api/runs/${this.reportRunId}/logs?format=json&limit=5000&scope=run`);
        this.allLogEvents = data.lines || [];
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

    highlightMatch(text, query) {
      const safe = (text || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
      if (!query.trim()) return safe;
      const escapedQ = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      return safe.replace(new RegExp(`(${escapedQ})`, 'gi'), '<mark class="log-highlight">$1</mark>');
    },

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
        events = events.filter(e => e.level === this.globalLogFilterLevel);
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

    navigateToRunArtifact(runId, view) {
      this.resetReportArtifacts();
      this.reportRunId = runId;
      this.reportView = view;
      this.currentView = 'reports';
      this.reportLoaded = true;
      if (view === 'metrics') this.loadRunMetrics();
      if (view === 'logs') this.loadAllLogEvents();
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

    logLevelClass(level) {
      const value = (level || '').toUpperCase();
      if (value === 'ERROR') return 'log-level-error';
      if (value === 'WARNING' || value === 'WARN') return 'log-level-warn';
      if (value === 'INFO') return 'log-level-info';
      if (value === 'DEBUG') return 'log-level-debug';
      return 'log-level-trace';
    },

    // ===========================================================
    // SECURITY – API TOKENS
    // ===========================================================
    async loadTokens() {
      try {
        this.tokens = await api('GET', '/api/tokens');
        return this.tokens;
      } catch {
        return [];
      }
    },

    async createToken(source = 'security') {
      const fromAuthWizard = source === 'auth';
      const name = (fromAuthWizard ? this.authTokenName : this.newTokenName).trim();
      if (!name) {
        if (fromAuthWizard) this.authError = 'Enter a token name';
        return;
      }
      try {
        const body = {
          name,
          is_admin: fromAuthWizard || this.newTokenRole === 'admin',
          expires_at: !fromAuthWizard && this.newTokenExpiresAt
            ? new Date(this.newTokenExpiresAt).toISOString()
            : null,
        };
        const resp = await api('POST', '/api/tokens', body);
        if (fromAuthWizard) {
          sessionStorage.setItem('etl_token', resp.raw_token);
          this.storedTokenValue = resp.raw_token;
          this.activeTokenName = resp.name || name;
          this.activeTokenIsAdmin = true;
          this.authInitialized = true;
          this.authTokenName = '';
          this.authError = '';
          this.authCreatedToken = resp.raw_token;
          await this.loadAll();
        } else {
          this.createdToken = resp.raw_token;
          this.createdTokenHint = resp.token_hint || null;
          this.createdTokenRole = resp.is_admin ? 'admin' : 'user';
          this.newTokenName = '';
          this.newTokenRole = 'user';
          this.newTokenExpiresAt = '';
          this.showCreateToken = false;
          this.toast('success', 'Access created', 'Copy and give this token to the intended user');
        }
        await this.loadTokens();
      } catch (e) {
        let msg = e.message;
        if (/already exists|duplicate|unique/i.test(msg)) {
          msg = 'A token with that name already exists';
        } else if (fromAuthWizard && e.status === 403) {
          msg = 'Token creation is restricted — paste an existing token or ask an admin to create one for you.';
        }
        if (fromAuthWizard) this.authError = msg;
        else this.toast('error', 'Create failed', msg);
      }
    },

    async revokeToken(id) {
      if (!confirm('Revoke this token? Any sessions using it will stop working.')) return;
      try {
        await api('DELETE', `/api/tokens/${id}`);
        await this.loadTokens();
        this.toast('success', 'Token revoked');
      } catch (e) {
        this.toast('error', 'Revoke failed', e.message);
      }
    },

    setStoredToken(raw) {
      const token = normalizeToken(raw);
      if (token) {
        sessionStorage.setItem('etl_token', token);
        this.storedTokenValue = token;
        this.resolveActiveTokenName({ verify: true });
        this.loadAll();
        this.toast('success', 'Token saved', 'Will be used for all API calls');
      } else {
        sessionStorage.removeItem('etl_token');
        this.storedTokenValue = '';
        this.activeTokenName = '';
        this.activeTokenIsAdmin = false;
        this.toast('warn', 'Token cleared');
      }
    },

    get storedToken() {
      return this.storedTokenValue;
    },

    // ===========================================================
    // NOTIFICATIONS – WEBHOOK HOOKS
    // ===========================================================
    async loadHooks() {
      try { this.hooks = await api('GET', '/api/notifications'); } catch {}
    },

    openNewHookModal() {
      this.hookModal = { name: '', url: '', events: ['run.failed', 'run.error'], secret: '' };
      this.showHookModal = true;
    },

    toggleHookEvent(event) {
      const idx = this.hookModal.events.indexOf(event);
      if (idx >= 0) this.hookModal.events.splice(idx, 1);
      else this.hookModal.events.push(event);
    },

    async saveHook() {
      const m = this.hookModal;
      if (!m.name || !m.url || !m.events.length) return;
      try {
        await api('POST', '/api/notifications', {
          name: m.name, url: m.url,
          events: m.events,
          secret: m.secret || null,
        });
        await this.loadHooks();
        this.showHookModal = false;
        this.toast('success', 'Webhook saved', m.name);
      } catch (e) {
        this.toast('error', 'Save failed', e.message);
      }
    },

    async deleteHook(id) {
      if (!confirm('Delete this webhook?')) return;
      try {
        await api('DELETE', `/api/notifications/${id}`);
        await this.loadHooks();
        this.toast('success', 'Webhook deleted');
      } catch (e) {
        this.toast('error', 'Delete failed', e.message);
      }
    },

    async testHook(id) {
      try {
        await api('POST', `/api/notifications/${id}/test`);
        this.toast('success', 'Test ping sent');
      } catch (e) {
        this.toast('error', 'Ping failed', e.message);
      }
    },

    // ===========================================================
    // SCHEDULES
    // ===========================================================
    async loadSchedules() {
      try { this.schedules = await api('GET', '/api/schedules'); } catch {}
    },

    openNewScheduleModal() {
      this.scheduleModal = {
        name: '', cron_expr: '0 6 * * *',
        source_env: 'dev', target_env: 'prod',
        job_sequence_raw: '',
        enabled: true,
      };
      this.scheduleModalEditing = false;
      this.showScheduleModal = true;
    },

    openEditScheduleModal(sched) {
      this.scheduleModal = {
        id: sched.id,
        name: sched.name,
        cron_expr: sched.cron_expr,
        source_env: sched.source_env,
        target_env: sched.target_env,
        job_sequence_raw: (sched.job_sequence || []).join(', '),
        enabled: sched.enabled,
      };
      this.scheduleModalEditing = true;
      this.showScheduleModal = true;
    },

    async saveSchedule() {
      const m = this.scheduleModal;
      const body = {
        name: m.name,
        cron_expr: m.cron_expr,
        source_env: m.source_env,
        target_env: m.target_env,
        job_sequence: m.job_sequence_raw.split(',').map(s => s.trim()).filter(Boolean),
        enabled: m.enabled,
        run_settings_json: {},
      };
      try {
        if (this.scheduleModalEditing) {
          await api('PUT', `/api/schedules/${m.id}`, body);
        } else {
          await api('POST', '/api/schedules', body);
        }
        await this.loadSchedules();
        this.showScheduleModal = false;
        this.toast('success', this.scheduleModalEditing ? 'Schedule updated' : 'Schedule created', m.name);
      } catch (e) {
        this.toast('error', 'Save failed', e.message);
      }
    },

    async deleteSchedule(id) {
      if (!confirm('Delete this schedule?')) return;
      try {
        await api('DELETE', `/api/schedules/${id}`);
        await this.loadSchedules();
        this.toast('success', 'Schedule deleted');
      } catch (e) {
        this.toast('error', 'Delete failed', e.message);
      }
    },

    async runScheduleNow(id) {
      try {
        await api('POST', `/api/schedules/${id}/run-now`);
        this.toast('success', 'Run triggered');
        setTimeout(() => this.loadRuns(), 1000);
      } catch (e) {
        this.toast('error', 'Trigger failed', e.message);
      }
    },

    // ===========================================================
    // JOB SELECTIONS
    // ===========================================================
    async loadJobSelections() {
      try { this.jobSelections = await api('GET', '/api/selections'); } catch {}
    },

    openNewSelectionModal() {
      this.selectionModal = { name: '', description: '', tags: '' };
      this.selectedSelectionJobNames = [];
      this.selectionModalEditing = false;
      this.showSelectionModal = true;
    },

    async openEditSelectionModal(sel) {
      const detail = await api('GET', `/api/selections/${sel.id}`);
      const latest = detail.versions[detail.versions.length - 1];
      this.selectionModal = {
        id: detail.id,
        name: detail.name,
        description: detail.description,
        tags: (detail.tags || []).join(', '),
      };
      this.selectedSelectionJobNames = (latest.job_sequence || []).map(
        s => (typeof s === 'string' ? s : s.job_name)
      );
      this.selectionModalEditing = true;
      this.showSelectionModal = true;
    },

    isSelectionJobChecked(name) {
      return this.selectedSelectionJobNames.includes(name);
    },

    toggleSelectionJob(name) {
      const idx = this.selectedSelectionJobNames.indexOf(name);
      if (idx >= 0) this.selectedSelectionJobNames.splice(idx, 1);
      else this.selectedSelectionJobNames.push(name);
    },

    async saveSelection() {
      const m = this.selectionModal;
      const body = {
        name: m.name,
        description: m.description || '',
        tags: (m.tags || '').split(',').map(s => s.trim()).filter(Boolean),
        job_sequence: this.selectedSelectionJobNames,
      };
      try {
        if (this.selectionModalEditing) {
          await api('PUT', `/api/selections/${m.id}`, body);
        } else {
          await api('POST', '/api/selections', body);
        }
        await this.loadJobSelections();
        this.showSelectionModal = false;
        this.toast('success', this.selectionModalEditing ? 'Selection updated' : 'Selection created', m.name);
      } catch (e) {
        this.toast('error', 'Save failed', e.message);
      }
    },

    async deleteSelection(id) {
      if (!confirm('Archive this job selection?')) return;
      try {
        await api('DELETE', `/api/selections/${id}`);
        await this.loadJobSelections();
        this.toast('success', 'Selection archived');
      } catch (e) {
        this.toast('error', 'Archive failed', e.message);
      }
    },

    openLaunchSelectionModal(sel) {
      this.launchSelectionModal = { selection_id: sel.id, source_env: 'dev', target_env: 'prod' };
      this.showLaunchSelectionModal = true;
    },

    async launchSelection() {
      const m = this.launchSelectionModal;
      const body = { source_env: m.source_env, target_env: m.target_env || '' };
      try {
        const run = await api('POST', `/api/selections/${m.selection_id}/launch`, body);
        this.showLaunchSelectionModal = false;
        this.toast('success', 'Launched', `Run ${run.run_id} started`);
        setTimeout(() => this.loadRuns(), 1000);
      } catch (e) {
        this.toast('error', 'Launch failed', e.message);
      }
    },

    async openSelectionRuns(sel) {
      this.selectionRunsPanel = sel;
      this.compareRunIds = [];
      try {
        this.selectionRuns = await api('GET', `/api/selections/${sel.id}/runs`);
      } catch (e) {
        this.selectionRuns = [];
        this.toast('error', 'Could not load run history', e.message);
      }
      this.showSelectionRunsModal = true;
    },

    isCompareRunSelected(runId) {
      return this.compareRunIds.includes(runId);
    },

    toggleCompareRunSelection(runId) {
      const idx = this.compareRunIds.indexOf(runId);
      if (idx >= 0) {
        this.compareRunIds.splice(idx, 1);
      } else {
        if (this.compareRunIds.length >= 2) this.compareRunIds.shift();
        this.compareRunIds.push(runId);
      }
    },

    compareSelectedRuns() {
      if (this.compareRunIds.length !== 2) {
        this.toast('warn', 'Select exactly two runs', 'Pick two runs to compare');
        return;
      }
      this.mismatchDiffRunIdA = this.compareRunIds[0];
      this.mismatchDiffRunIdB = this.compareRunIds[1];
      this.showSelectionRunsModal = false;
      this.currentView = 'compare';
      this.compareSubTab = 'mmdiff';
      this.runMismatchDiff();
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
        svg += `<rect x="${x}" y="${y}" width="${W}" height="${H}" rx="4" fill="#1e2533" stroke="#334155" stroke-width="1"/>`;
        svg += `<text x="${x + W/2}" y="${y + H/2 + 4}" text-anchor="middle" fill="#c7d0dc">${n.name}</text>`;
      });
      svg += '</svg>';
      return svg;
    },

    // ===========================================================
    // UTILITIES
    // ===========================================================
    fmtDate(iso) {
      if (!iso) return '—';
      // Treat bare ISO strings (no timezone suffix) as UTC so toLocale* shows local time correctly
      const ts = /[Zz]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + 'Z';
      const d = new Date(ts);
      return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    },

    statusBadgeClass(status) {
      const map = {
        PASSED:    'badge-green',
        FAILED:    'badge-red',
        SLOW:      'badge-amber',
        RUNNING:   'badge-blue',
        PENDING:   'badge-gray',
        ERROR:     'badge-rose',
        COMPLETED: 'badge-green',
      };
      return 'badge ' + (map[status] || 'badge-gray');
    },

    // ===========================================================
    // --- Task 1: Job Catalog Search/Filter ---
    // ===========================================================
    get filteredJobList() {
      const q = (this.jobSearchQuery || '').toLowerCase().trim();
      if (!q) return this.jobs || [];
      return (this.jobs || []).filter(job => {
        const nameMatch = (job.name || '').toLowerCase().includes(q);
        const descMatch = (job.description || '').toLowerCase().includes(q);
        const tagsMatch = (job.tags || []).some(t => t.toLowerCase().includes(q));
        return nameMatch || descMatch || tagsMatch;
      });
    },

    // ===========================================================
    // --- Task 2: Job Last Run Status ---
    // ===========================================================
    getJobLastStatus(jobName) {
      try {
        return localStorage.getItem(`etl_job_status_${jobName}`) || null;
      } catch {
        return null;
      }
    },

    saveJobRunStatus(jobName, status) {
      try {
        localStorage.setItem(`etl_job_status_${jobName}`, status);
      } catch {}
    },

    // ===========================================================
    // --- Task 3: Multi-select with Shift-click + Select All/None ---
    // ===========================================================
    toggleJobWithShift(idx, event) {
      const jobs = this.filteredJobList;
      const job = jobs[idx];
      if (!job) return;
      if (event && event.shiftKey && this.shiftLastIndex >= 0) {
        const lo = Math.min(this.shiftLastIndex, idx);
        const hi = Math.max(this.shiftLastIndex, idx);
        for (let i = lo; i <= hi; i++) {
          const j = jobs[i];
          if (!j) continue;
          if (!this.selectedJobs.includes(j.name)) {
            this.selectedJobs.push(j.name);
          }
        }
      } else {
        const pos = this.selectedJobs.indexOf(job.name);
        if (pos >= 0) this.selectedJobs.splice(pos, 1);
        else this.selectedJobs.push(job.name);
      }
      this.shiftLastIndex = idx;
    },

    selectAllJobs() {
      const names = this.filteredJobList.map(j => j.name);
      names.forEach(name => {
        if (!this.selectedJobs.includes(name)) this.selectedJobs.push(name);
      });
    },

    selectNoneJobs() {
      this.selectedJobs = [];
      this.shiftLastIndex = -1;
    },

    // ===========================================================
    // --- Task 7: Config Dropdown Enhancement + Session Memory ---
    // ===========================================================
    savedConfigDisplay(config) {
      if (!config) return '';
      return `${config.name} (${config.env_name})`;
    },

    saveSessionSettings() {
      try {
        const settings = {
          launchSettings: { ...this.launchSettings },
          compareSubTab: this.compareSubTab,
          reconMode: this.reconMode,
          boSourceAType: this.boSourceAType,
          boSourceBType: this.boSourceBType,
          boKeyColumns: this.boKeyColumns,
          boExcludeColumns: this.boExcludeColumns,
          historyFilterStatus: this.historyFilterStatus,
          historyFilterRunType: this.historyFilterRunType,
        };
        localStorage.setItem('etl_session_settings', JSON.stringify(settings));
      } catch {}
    },

    loadSessionSettings() {
      try {
        const raw = localStorage.getItem('etl_session_settings');
        if (!raw) return;
        const settings = JSON.parse(raw);
        if (settings.launchSettings) Object.assign(this.launchSettings, settings.launchSettings);
        if (settings.compareSubTab !== undefined) this.compareSubTab = settings.compareSubTab;
        if (settings.reconMode !== undefined) this.reconMode = settings.reconMode;
        if (settings.boSourceAType !== undefined) this.boSourceAType = settings.boSourceAType;
        if (settings.boSourceBType !== undefined) this.boSourceBType = settings.boSourceBType;
        if (settings.boKeyColumns !== undefined) this.boKeyColumns = settings.boKeyColumns;
        if (settings.boExcludeColumns !== undefined) this.boExcludeColumns = settings.boExcludeColumns;
        if (settings.historyFilterStatus !== undefined) this.historyFilterStatus = settings.historyFilterStatus;
        if (settings.historyFilterRunType !== undefined) this.historyFilterRunType = settings.historyFilterRunType;
      } catch {}
    },

    // ===========================================================
    // --- Task 8: Job Modal Tab – reset on open (patch existing openers) ---
    // ===========================================================
    // Note: jobModalTab is reset in openNewJobModal/openEditJobModal by calling _resetJobModalTab()
    _resetJobModalTab() {
      this.jobModalTab = 'basic';
    },

    // ===========================================================
    // --- Task 9: Job Modal Inline Validation ---
    // ===========================================================
    validateJobModal() {
      const m = this.jobModal || {};
      const v = { sql: '', keyColumns: '', dependencies: '' };

      // SQL validation: only for reconciliation jobs
      if (m.job_type === 'reconciliation') {
        const query = (m.query || '').trim();
        if (query && !/select/i.test(query)) {
          v.sql = 'Query should contain a SELECT statement';
        } else if (query && /select/i.test(query)) {
          v.sql = '✓ Query looks valid';
        }
      }

      // Key columns: check alphanumeric/underscore
      if (m.key_columns_raw) {
        const cols = m.key_columns_raw.split(',').map(s => s.trim()).filter(Boolean);
        const invalid = cols.filter(c => !/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(c));
        if (invalid.length) {
          v.keyColumns = `Invalid column name(s): ${invalid.join(', ')}`;
        }
      }

      // Dependencies: check if referenced job names exist
      if (m.depends_on_raw) {
        const deps = m.depends_on_raw.split(',').map(s => s.trim()).filter(Boolean);
        const jobNames = (this.jobs || []).map(j => j.name);
        const missing = deps.filter(d => d && !jobNames.includes(d));
        if (missing.length) {
          v.dependencies = `Unknown job(s): ${missing.join(', ')}`;
        }
      }

      this.jobModalValidation = v;
    },

    // ===========================================================
    // --- Task 10: DQ Rule Templates + Save as Template ---
    // ===========================================================
    applyDqTemplate(templateName) {
      const tpl = (this.dqRuleTemplates || []).find(t => t.name === templateName);
      if (!tpl) return;
      if (!this.jobModal.rules) this.jobModal.rules = [];
      this.jobModal.rules.push({
        type: tpl.type,
        column: '',
        severity: 'error',
        min_value: tpl.defaults.min !== undefined ? tpl.defaults.min : null,
        max_value: tpl.defaults.max !== undefined ? tpl.defaults.max : null,
        pattern: null,
      });
    },

    _loadJobTemplatesFromStorage() {
      try {
        const raw = localStorage.getItem('etl_job_templates');
        this.jobTemplates = raw ? JSON.parse(raw) : [];
      } catch {
        this.jobTemplates = [];
      }
    },

    saveJobAsTemplate() {
      const name = (this.jobTemplateName || '').trim();
      if (!name) {
        this.toast('warn', 'Template name required', 'Enter a name for the template');
        return;
      }
      const m = this.jobModal || {};
      // Save all modal fields except id
      const tpl = {
        name,
        job_type: m.job_type,
        description: m.description,
        query: m.query,
        key_columns_raw: m.key_columns_raw,
        tags_raw: m.tags_raw,
        enabled: m.enabled,
        depends_on_raw: m.depends_on_raw,
        rules: (m.rules || []).map(r => ({ ...r })),
        bo_report_id: m.bo_report_id,
        bo_page_id: m.bo_page_id,
        bo_format: m.bo_format,
        automic_job_name: m.automic_job_name,
        automic_run_id: m.automic_run_id,
        dbt_manifest_path: m.dbt_manifest_path,
        dbt_run_results_path: m.dbt_run_results_path,
      };
      // Replace if same name exists
      const idx = this.jobTemplates.findIndex(t => t.name === name);
      if (idx >= 0) this.jobTemplates.splice(idx, 1, tpl);
      else this.jobTemplates.push(tpl);
      try {
        localStorage.setItem('etl_job_templates', JSON.stringify(this.jobTemplates));
      } catch {}
      this.jobTemplateName = '';
      this.showSaveTemplatePrompt = false;
      this.toast('success', 'Template saved', name);
    },

    loadJobTemplate(name) {
      const tpl = (this.jobTemplates || []).find(t => t.name === name);
      if (!tpl) return;
      const fields = { ...tpl };
      delete fields.name;
      Object.assign(this.jobModal, fields);
      this.toast('success', 'Template loaded', name);
    },

    deleteJobTemplate(name) {
      this.jobTemplates = (this.jobTemplates || []).filter(t => t.name !== name);
      try {
        localStorage.setItem('etl_job_templates', JSON.stringify(this.jobTemplates));
      } catch {}
      this.toast('success', 'Template deleted', name);
    },

    // ===========================================================
    // --- Task 11: Execution Sequence Drag-to-Reorder ---
    // ===========================================================
    onDragStart(idx) {
      this.dragSrcIndex = idx;
    },

    onDragOver(e, idx) {
      e.preventDefault();
    },

    onDrop(idx) {
      if (this.dragSrcIndex === null || this.dragSrcIndex === idx) {
        this.dragSrcIndex = null;
        return;
      }
      const seq = this.selectedJobs;
      const item = seq.splice(this.dragSrcIndex, 1)[0];
      seq.splice(idx, 0, item);
      this.dragSrcIndex = null;
    },

    onDragEnd() {
      this.dragSrcIndex = null;
    },

    // ===========================================================
    // --- Task 12: Execution Sequence Utilities ---
    // ===========================================================
    clearExecutionSequence() {
      this.selectedJobs = [];
    },

    invertJobSelection() {
      const visibleNames = this.filteredJobList.map(j => j.name);
      const newSelected = [];
      visibleNames.forEach(name => {
        if (!this.selectedJobs.includes(name)) newSelected.push(name);
      });
      // Keep selections outside the visible list unchanged
      const outsideVisible = this.selectedJobs.filter(n => !visibleNames.includes(n));
      this.selectedJobs = [...outsideVisible, ...newSelected];
    },

    hasCircularDependency() {
      const seq = this.selectedJobs || [];
      const jobMap = {};
      (this.jobs || []).forEach(j => { jobMap[j.name] = j; });
      for (const nameA of seq) {
        const jobA = jobMap[nameA];
        if (!jobA) continue;
        const depsA = (jobA.depends_on || []);
        for (const nameB of depsA) {
          const jobB = jobMap[nameB];
          if (!jobB) continue;
          const depsB = (jobB.depends_on || []);
          if (depsB.includes(nameA)) return true;
        }
      }
      return false;
    },

    get estimatedSequenceDuration() {
      const seq = this.selectedJobs || [];
      let total = 0;
      seq.forEach(name => {
        try {
          const val = localStorage.getItem(`etl_job_duration_${name}`);
          if (val) total += Number(val) || 0;
        } catch {}
      });
      if (total === 0) return '';
      const m = Math.floor(total / 60);
      const s = Math.round(total % 60);
      return m > 0 ? `~${m}m ${s}s` : `~${s}s`;
    },

    saveJobDuration(jobName, durationSeconds) {
      try {
        localStorage.setItem(`etl_job_duration_${jobName}`, String(durationSeconds));
      } catch {}
    },

    // ===========================================================
    // --- Task 13: Compare Tab Template System ---
    // ===========================================================
    loadCompareTemplates() {
      try {
        const raw = localStorage.getItem('etl_compare_templates');
        const saved = raw ? JSON.parse(raw) : [];
        // Merge predefined with user-saved (user saved names take priority)
        const savedNames = saved.map(t => t.name);
        const predefined = (this.predefinedCompareTemplates || []).filter(t => !savedNames.includes(t.name));
        this.compareTemplates = [...predefined, ...saved];
      } catch {
        this.compareTemplates = [...(this.predefinedCompareTemplates || [])];
      }
    },

    saveCompareTemplate() {
      const name = (this.newCompareTemplateName || '').trim();
      if (!name) {
        this.toast('warn', 'Template name required', 'Enter a name for the compare template');
        return;
      }
      const tpl = {
        name,
        type: this.compareSubTab,
        config: {
          compareSubTab: this.compareSubTab,
          reconMode: this.reconMode,
          boSourceAType: this.boSourceAType,
          boSourceBType: this.boSourceBType,
          boKeyColumns: this.boKeyColumns,
          boExcludeColumns: this.boExcludeColumns,
          boSourceA: { ...this.boSourceA },
          boSourceB: { ...this.boSourceB },
        },
      };
      const idx = this.compareTemplates.findIndex(t => t.name === name);
      if (idx >= 0) this.compareTemplates.splice(idx, 1, tpl);
      else this.compareTemplates.push(tpl);
      // Persist only user-saved (non-predefined) templates
      const predefinedNames = (this.predefinedCompareTemplates || []).map(t => t.name);
      const toSave = this.compareTemplates.filter(t => !predefinedNames.includes(t.name));
      try {
        localStorage.setItem('etl_compare_templates', JSON.stringify(toSave));
      } catch {}
      this.newCompareTemplateName = '';
      this.activeCompareTemplate = name;
      this.toast('success', 'Compare template saved', name);
    },

    loadCompareTemplate(name) {
      const tpl = (this.compareTemplates || []).find(t => t.name === name);
      if (!tpl || !tpl.config) return;
      const c = tpl.config;
      if (c.compareSubTab) this.compareSubTab = c.compareSubTab;
      if (c.reconMode) this.reconMode = c.reconMode;
      if (c.boSourceAType) this.boSourceAType = c.boSourceAType;
      if (c.boSourceBType) this.boSourceBType = c.boSourceBType;
      if (c.boKeyColumns !== undefined) this.boKeyColumns = c.boKeyColumns;
      if (c.boExcludeColumns !== undefined) this.boExcludeColumns = c.boExcludeColumns;
      if (c.boSourceA) Object.assign(this.boSourceA, c.boSourceA);
      if (c.boSourceB) Object.assign(this.boSourceB, c.boSourceB);
      this.activeCompareTemplate = name;
      this.toast('success', 'Compare template loaded', name);
    },

    deleteCompareTemplate(name) {
      this.compareTemplates = (this.compareTemplates || []).filter(t => t.name !== name);
      const predefinedNames = (this.predefinedCompareTemplates || []).map(t => t.name);
      const toSave = this.compareTemplates.filter(t => !predefinedNames.includes(t.name));
      try {
        localStorage.setItem('etl_compare_templates', JSON.stringify(toSave));
      } catch {}
      if (this.activeCompareTemplate === name) this.activeCompareTemplate = '';
      this.toast('success', 'Compare template deleted', name);
    },

    // ===========================================================
    // --- Task 14: BO Report Tab Improvements ---
    // ===========================================================
    swapCompareSides() {
      // Swap source types
      const tmpType = this.boSourceAType;
      this.boSourceAType = this.boSourceBType;
      this.boSourceBType = tmpType;
      // Swap source configs
      const tmpSrc = { ...this.boSourceA };
      this.boSourceA = { ...this.boSourceB, label: 'Source A' };
      this.boSourceB = { ...tmpSrc, label: 'Source B' };
      // Swap loaded docs/reports
      const tmpDocs = this.boDocsA;
      this.boDocsA = this.boDocsB;
      this.boDocsB = tmpDocs;
      const tmpReports = this.boReportsA;
      this.boReportsA = this.boReportsB;
      this.boReportsB = tmpReports;
      this.toast('success', 'Sides swapped', 'Source A and Source B have been swapped');
    },

    saveBoLastUsedSourceTypes() {
      try {
        this.boLastUsedSourceTypes = { a: this.boSourceAType, b: this.boSourceBType };
        localStorage.setItem('etl_bo_last_source_types', JSON.stringify(this.boLastUsedSourceTypes));
      } catch {}
    },

    loadBoLastUsedSourceTypes() {
      try {
        const raw = localStorage.getItem('etl_bo_last_source_types');
        if (raw) this.boLastUsedSourceTypes = JSON.parse(raw);
      } catch {}
    },

    // ===========================================================
    // --- Task 15: Quick Compare Mode ---
    // ===========================================================
    enableQuickCompare() {
      this.quickCompareMode = true;
      // Auto-select last successful run as source A
      const passed = (this.runs || []).filter(r => r.status === 'PASSED' || r.status === 'COMPLETED');
      if (passed.length > 0) {
        // Sort by created_at descending
        const sorted = this.sortRunsForDisplay(passed);
        this.fileRunIdA = sorted[0].run_id;
        this.fileSourceAType = 'run';
        this.toast('success', 'Quick compare enabled', `Using run ${sorted[0].run_id.substring(0, 8)}... as Source A`);
      } else {
        this.toast('warn', 'No successful runs found', 'Quick compare needs at least one passed run');
      }
    },

    disableQuickCompare() {
      this.quickCompareMode = false;
      this.fileRunIdA = '';
      this.fileSourceAType = 'run';
    },

    // ===========================================================
    // --- Task 16: Sort Runs for Display ---
    // ===========================================================
    sortRunsForDisplay(runs) {
      if (!runs || !runs.length) return [];
      return [...runs].sort((a, b) => {
        const da = new Date(a.created_at || 0).getTime();
        const db = new Date(b.created_at || 0).getTime();
        return db - da;
      });
    },

    // ===========================================================
    // --- Task 17: Results Panel Export Settings + Chart Toggle ---
    // ===========================================================
    exportCompareSettings() {
      try {
        const settings = {
          compareSubTab: this.compareSubTab,
          reconMode: this.reconMode,
          boSourceAType: this.boSourceAType,
          boSourceBType: this.boSourceBType,
          boKeyColumns: this.boKeyColumns,
          boExcludeColumns: this.boExcludeColumns,
          boSourceA: { ...this.boSourceA },
          boSourceB: { ...this.boSourceB },
          dualEnvConfigA: this.dualEnvConfigA,
          dualEnvConfigB: this.dualEnvConfigB,
          dualEnvSourceEnvA: this.dualEnvSourceEnvA,
          dualEnvTargetEnvA: this.dualEnvTargetEnvA,
          dualEnvSourceEnvB: this.dualEnvSourceEnvB,
          dualEnvTargetEnvB: this.dualEnvTargetEnvB,
          exportedAt: new Date().toISOString(),
        };
        const blob = new Blob([JSON.stringify(settings, null, 2)], { type: 'application/json' });
        triggerDownload(blob, `compare-settings-${Date.now()}.json`);
        this.toast('success', 'Settings exported');
      } catch (e) {
        this.toast('error', 'Export failed', e.message);
      }
    },

    async loadMismatchChart(runId, resultId) {
      try {
        const data = await api('GET', `/api/runs/${runId}/results/${resultId}/mismatch-distribution`);
        this.mismatchChartData = data;
        this.$nextTick(() => this._renderMismatchChart());
      } catch {
        this.mismatchChartData = null;
      }
    },

    _renderMismatchChart() {
      const canvas = document.getElementById('mismatchChart');
      if (!canvas || !this.mismatchChartData) return;
      const dist = this.mismatchChartData.distribution || [];
      if (typeof Chart === 'undefined') return;
      // Destroy previous instance if any
      if (this._mismatchChartInstance) {
        this._mismatchChartInstance.destroy();
        this._mismatchChartInstance = null;
      }
      this._mismatchChartInstance = new Chart(canvas, {
        type: this.mismatchChartType === 'column' ? 'bar' : this.mismatchChartType,
        data: {
          labels: dist.map(d => d.column || d.label || ''),
          datasets: [{
            label: 'Mismatches',
            data: dist.map(d => d.count || 0),
            backgroundColor: '#fb7185',
            borderColor: '#0d0f12',
            borderWidth: 1,
          }],
        },
        options: {
          responsive: true,
          plugins: {
            legend: { display: false },
          },
          scales: {
            x: { ticks: { color: '#94a3b8' }, grid: { color: '#1e2533' } },
            y: { ticks: { color: '#94a3b8' }, grid: { color: '#1e2533' } },
          },
        },
      });
    },

    // ===========================================================
    // --- Task 18: Mismatch Acceptance Workflow ---
    // ===========================================================
    get filteredMismatches() {
      const rows = this.drawer.rows || [];
      if (this.mismatchStatusFilter === 'ALL') return rows;
      if (this.mismatchStatusFilter === 'ACCEPTED') return rows.filter(m => m.accepted);
      if (this.mismatchStatusFilter === 'REJECTED') return rows.filter(m => m.rejected);
      if (this.mismatchStatusFilter === 'PENDING') return rows.filter(m => !m.accepted && !m.rejected);
      return rows;
    },

    async acceptAllVisibleMismatches() {
      const pending = this.filteredMismatches.filter(m => !m.accepted && !m.rejected);
      if (!pending.length) {
        this.toast('warn', 'No pending mismatches', 'No pending mismatches visible to accept');
        return;
      }
      const runId = this.drawer.runId;
      const resultId = this.drawer.result && this.drawer.result.id;
      if (!runId || !resultId) return;
      let accepted = 0;
      for (const m of pending) {
        try {
          const result = await api('PATCH',
            `/api/runs/${runId}/results/${resultId}/mismatches/${m.id}/accept`,
            { note: 'Bulk accepted' });
          const patchRow = (row) => row.id === m.id
            ? { ...row, accepted: result.accepted, accepted_note: result.accepted_note, accepted_at: result.accepted_at, accepted_by: result.accepted_by }
            : row;
          this.drawer.rows = this.drawer.rows.map(patchRow);
          accepted++;
        } catch {}
      }
      this.toast('success', `${accepted} mismatch(es) accepted`);
      if (accepted > 0) await this.loadRuns();
    },

    // ===========================================================
    // --- Task 19: Help System + Keyboard Shortcuts ---
    // ===========================================================
    showHelp(topic) {
      const helpTopics = {
        'job-search': { title: 'Job Search', content: 'Search jobs by name, description, or tags. The search is case-insensitive and matches partial text.' },
        'chunkSize': { title: 'Chunk Size', content: 'Number of rows to process at once. Set to 0 to disable chunking and process all rows in memory. Larger values use more memory but may be faster for simple comparisons.' },
        'hashPrecheck': { title: 'Hash Precheck', content: 'When enabled, computes hash values for rows first and only performs full row comparison when hashes differ. Significantly speeds up comparisons for large datasets with few actual differences.' },
        'nullEqualsNull': { title: 'NULL Semantics', content: 'When enabled, treats two NULL values as equal during comparison. When disabled, NULL != NULL (SQL standard behavior).' },
        'maxWorkers': { title: 'Max Workers', content: 'Maximum number of parallel test execution threads. Higher values speed up large test suites but increase database load.' },
        'compareTemplate': { title: 'Compare Templates', content: 'Save and reuse comparison configurations. Templates store your source settings, key columns, and other options so you can quickly repeat common comparisons.' },
        'sqlQuery': { title: 'SQL Query', content: 'The SELECT statement used to extract data for comparison. Must include all key columns and comparison columns. Parameterized queries use {env} as a placeholder for the environment name.' },
      };
      const entry = helpTopics[topic];
      if (!entry) return;
      this.helpTitle = entry.title;
      this.helpContent = entry.content;
      this.showingHelp = true;
    },

    initKeyboardShortcuts() {
      document.addEventListener('keydown', (e) => {
        // Skip if focus is in a form input
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

    // ===========================================================
    // CONTRACTS
    // ===========================================================

    async loadContracts() {
      this.contractsLoading = true;
      try {
        this.contracts = await api('GET', '/api/contracts');
        for (const c of this.contracts) {
          try {
            this.contractStatusMap[c.name] = await api('GET', `/api/contracts/${encodeURIComponent(c.name)}/status`);
          } catch { this.contractStatusMap[c.name] = { status: 'UNKNOWN', open_breach: null }; }
        }
      } catch {}
      this.contractsLoading = false;
    },

    async selectContract(contract) {
      this.selectedContract = contract;
      this.contractBreachHistory = [];
      this.contractVersionHistory = [];
      this.contractBreachLoading = true;
      this.contractVersionLoading = true;
      try { this.contractBreachHistory = await api('GET', `/api/contracts/${encodeURIComponent(contract.name)}/breaches`); } catch {}
      this.contractBreachLoading = false;
      try { this.contractVersionHistory = await api('GET', `/api/contracts/${encodeURIComponent(contract.name)}/versions`); } catch {}
      this.contractVersionLoading = false;
    },

    openNewContractModal() {
      this.contractModal = { name: '', source_job: '', owner: '', sla_hours: 4, consumers_raw: '', breach_severity: 'error', version: '1.0' };
      this.contractModalEditing = false;
      this.showContractModal = true;
    },

    openEditContractModal(contract) {
      this.contractModal = {
        name: contract.name,
        source_job: contract.source_job,
        owner: contract.owner,
        sla_hours: contract.sla_hours,
        consumers_raw: (contract.consumers || []).join(', '),
        breach_severity: contract.breach_severity,
        version: contract.version,
      };
      this.contractModalEditing = true;
      this.showContractModal = true;
    },

    async saveContract() {
      const consumers = this.contractModal.consumers_raw
        ? this.contractModal.consumers_raw.split(',').map(s => s.trim()).filter(Boolean)
        : [];
      const payload = {
        name: this.contractModal.name,
        source_job: this.contractModal.source_job,
        owner: this.contractModal.owner,
        sla_hours: parseFloat(this.contractModal.sla_hours),
        consumers,
        breach_severity: this.contractModal.breach_severity,
        version: this.contractModal.version,
      };
      try {
        if (this.contractModalEditing) {
          await api('PUT', `/api/contracts/${encodeURIComponent(this.contractModal.name)}`, {
            owner: payload.owner, sla_hours: payload.sla_hours, consumers, breach_severity: payload.breach_severity,
          });
        } else {
          await api('POST', '/api/contracts', payload);
        }
        this.showContractModal = false;
        await this.loadContracts();
        if (this.selectedContract && this.selectedContract.name === this.contractModal.name) {
          const updated = this.contracts.find(c => c.name === this.contractModal.name);
          if (updated) this.selectedContract = updated;
        }
      } catch (e) { alert('Save failed: ' + (e.message || e)); }
    },

    async deleteContract(name) {
      if (!confirm(`Delete contract "${name}"?`)) return;
      try {
        await api('DELETE', `/api/contracts/${encodeURIComponent(name)}`);
        if (this.selectedContract && this.selectedContract.name === name) this.selectedContract = null;
        await this.loadContracts();
      } catch (e) { alert('Delete failed: ' + (e.message || e)); }
    },

    async bumpContractVersion(name) {
      this.contractBumpLoading = true;
      try {
        await api('POST', `/api/contracts/${encodeURIComponent(name)}/bump`, {
          bump_type: this.contractBumpType, note: this.contractBumpNote || null,
        });
        this.contractBumpNote = '';
        await this.loadContracts();
        if (this.selectedContract && this.selectedContract.name === name) {
          const updated = this.contracts.find(c => c.name === name);
          if (updated) await this.selectContract(updated);
        }
      } catch (e) { alert('Bump failed: ' + (e.message || e)); }
      this.contractBumpLoading = false;
    },

    contractStatusBadgeClass(name) {
      const s = (this.contractStatusMap[name] || {}).status;
      if (s === 'OK') return 'badge-ok';
      if (s === 'BREACHED') return 'badge-breached';
      if (s === 'OVERDUE') return 'badge-overdue';
      return 'badge-unknown';
    },

  };
}
