/* ETL Framework – full 6-tab SPA */

const API = window.ETL_API_BASE || '';
const APP_CONFIG = window.ETL_APP_CONFIG || {};
const isTerminalStatusValue = APP_CONFIG.isTerminalStatusValue || ((status) =>
  ['PASSED', 'FAILED', 'SLOW', 'ERROR', 'COMPLETED', 'CANCELLED'].includes(String(status || '').toUpperCase()));
const highlightMatch = APP_CONFIG.highlightMatch || ((text, query) => {
  const safe = (text || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  if (!query.trim()) return safe;
  const escapedQ = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return safe.replace(new RegExp(`(${escapedQ})`, 'gi'), '<mark class="log-highlight">$1</mark>');
});
const logLevelClass = APP_CONFIG.logLevelClass || ((level) => {
  const value = (level || '').toUpperCase();
  if (value === 'ERROR') return 'log-level-error';
  if (value === 'WARNING' || value === 'WARN') return 'log-level-warn';
  if (value === 'INFO') return 'log-level-info';
  if (value === 'DEBUG') return 'log-level-debug';
  return 'log-level-trace';
});
const HELP_METHODS = window.ETL_HELP_METHODS || {
  showHelp() {},
  initKeyboardShortcuts() {},
};

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

async function apiPaged(path) {
  const token = normalizeToken(sessionStorage.getItem('etl_token'));
  const headers = token ? { 'Authorization': 'Bearer ' + token } : {};
  const resp = await fetch(API + path, { headers });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    const error = new Error(apiErrorMessage(err.detail ?? err, resp.statusText));
    error.status = resp.status;
    throw error;
  }
  const items = await resp.json();
  return {
    items,
    total: parseInt(resp.headers.get('x-total-count') || String(items.length), 10),
    storedComplete: resp.headers.get('x-stored-complete') === 'true',
  };

}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function app() {
  return _appRaw();
}

function _appRaw() {
  // Compare feature slice is merged in from features/compare.js (window.ETL_FEATURE_COMPARE).
  // Merged with Object.defineProperties (not Object.assign) because Object.assign
  // reads each `get x()` accessor below immediately and copies the *value* it
  // returned at that instant, freezing computed properties (filteredJobList,
  // jobCatalogCountLabel, etc.) as one-time snapshots that never update again.
  // defineProperties copies the accessor itself, so it keeps recomputing on access.
  const core = {
    // -----------------------------------------------------------
    // Navigation
    // -----------------------------------------------------------
    currentView: 'config',
    tabs: [
      { id: 'config',   label: 'Config',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>' },
      { id: 'jobs',     label: 'Launch',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>' },
      { id: 'monitor',  label: 'Monitor',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>' },
      { id: 'history',  label: 'History',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>' },
      { id: 'adapters', label: 'Adapters',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2v6M15 2v6M6 8h12l-1 5a5 5 0 0 1-10 0L6 8z"></path><path d="M10 19v3M14 19v3"></path></svg>' },
      { id: 'reports',  label: 'Reports',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>' },
      { id: 'differences', label: 'Differences',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>' },
      { id: 'compare',  label: 'Compare',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"></polyline><path d="M3 5h18"></path><polyline points="7 23 3 19 7 15"></polyline><path d="M21 19H3"></path></svg>' },
      { id: 'contracts', label: 'Contracts',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><path d="M9 15l2 2 4-4"></path></svg>' },
      { id: 'logs', label: 'Logs',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"></polyline><line x1="12" y1="19" x2="20" y2="19"></line></svg>' },
      { id: 'help', label: 'Help',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>' },
    ],
    apiOk: false,
    themeMode: localStorage.getItem('etl_theme') === 'light' ? 'light' : 'dark',

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
    // Help center
    // -----------------------------------------------------------
    helpSearch: '',
    helpSections: (window.ETL_HELP && window.ETL_HELP.sections) || [],
    helpActiveId: (window.ETL_HELP && window.ETL_HELP.sections && window.ETL_HELP.sections[0] && window.ETL_HELP.sections[0].id) || '',

    // -----------------------------------------------------------
    // Diagnostics
    // -----------------------------------------------------------
    diagnosticsOpen: false,
    diagnosticsLoading: false,
    diagnosticsData: null,
    diagnosticsError: '',
    diagnosticsIncludeLogs: false,

    // Monitor state/methods moved to features/monitor.js (merged in app())

    // History / Profile & Schema / Trends / Lineage / Mismatch distribution
    // state moved to features/history.js (merged in app())

    // Adapters – SAP BO / Automic state moved to features/adapters.js
    // (merged in app())

    // Reports tab state moved to features/reports.js (merged in app())

    // Differences Explorer tab state moved to features/differences.js
    // (merged in app())

    // Reports tab logs-subtab state (allLogEvents*, logFilter*) moved to
    // features/reports.js (merged in app())

    // Global Logs tab state (globalLogEvents*, globalLogFilter*,
    // globalLogRunId, globalLogsPollTimer) moved to features/logs.js
    // (merged in app())

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

    // Inline mismatch expand (History detail) state moved to
    // features/history.js (merged in app())

      // Compare state/methods moved to features/compare.js (merged in app())

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
    mismatchDecisionForm: { open: false, scope: null, decision: null, note: '', saving: false },

    // -----------------------------------------------------------
    // Regional — app-wide timezone
    // -----------------------------------------------------------
    appTimezone: 'UTC',
    timezoneOpen: false,
    timezoneDraft: 'UTC',
    timezoneSaving: false,
    timezoneOptions: [
      'UTC',
      'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
      'America/Anchorage', 'America/Sao_Paulo', 'America/Mexico_City', 'America/Toronto',
      'Europe/London', 'Europe/Dublin', 'Europe/Paris', 'Europe/Berlin', 'Europe/Madrid',
      'Europe/Rome', 'Europe/Amsterdam', 'Europe/Moscow', 'Europe/Istanbul',
      'Asia/Kolkata', 'Asia/Dubai', 'Asia/Karachi', 'Asia/Dhaka', 'Asia/Bangkok',
      'Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Singapore', 'Asia/Tokyo', 'Asia/Seoul',
      'Australia/Sydney', 'Australia/Perth', 'Pacific/Auckland',
    ],

    // -----------------------------------------------------------
    // Toast
    // -----------------------------------------------------------
    toasts: [],
    _toastSeq: 0,

    compareTemplates: [],
    activeCompareTemplate: '',
    showCompareTemplatePanel: false,
    newCompareTemplateName: '',
    predefinedCompareTemplates: APP_CONFIG.predefinedCompareTemplates || [],

    boSaveAsBaseline: false,
    boLastUsedSourceTypes: { a: '', b: '' },

    quickCompareMode: false,

    showMismatchChart: false,
    mismatchChartType: 'column',
    mismatchChartData: null,

    mismatchStatusFilter: 'ALL',

    showingHelp: false,
    helpTitle: '',
    helpContent: '',

    // ===========================================================
    // INIT
    // ===========================================================
    onTabEnter(id) {
      this.currentView = id;
      if (id === 'contracts') this.loadContracts();
      if (id === 'logs') this.startGlobalLogsPolling();
      else this.stopGlobalLogsPolling();
    },

    applyTheme() {
      document.documentElement.setAttribute('data-theme', this.themeMode);
    },

    toggleTheme() {
      this.themeMode = this.themeMode === 'dark' ? 'light' : 'dark';
      localStorage.setItem('etl_theme', this.themeMode);
      this.applyTheme();
    },

    async init() {
      this.applyTheme();
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
          this.loadTimezoneSetting();
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
      this.loadSessionSettings();
      this._loadJobTemplatesFromStorage();
      this.loadCompareTemplates();
      this.initKeyboardShortcuts();
      this.$watch('launchSettings.config_id', () => {
        this.launchSettings.source_connection = null;
        this.launchSettings.target_connection = null;
      });
      this.$watch('sqlConfigA', () => { this.sqlConnectionA = null; });
      this.$watch('sqlConfigB', () => { this.sqlConnectionB = null; });
      this._applyDeepLink();
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

    async loadDiagnostics() {
      this.diagnosticsLoading = true;
      this.diagnosticsError = '';
      try {
        const qs = new URLSearchParams({ include_logs: String(Boolean(this.diagnosticsIncludeLogs)) });
        this.diagnosticsData = await api('GET', `/api/health/diagnostics?${qs}`);
      } catch (e) {
        this.diagnosticsError = e.message || 'Diagnostics failed';
      } finally {
        this.diagnosticsLoading = false;
      }
    },

    // Monitor methods moved to features/monitor.js (merged in app())

    // loadRuns, loadAudit, loadProfile, suggestDQRules, loadSchemaHistory,
    // viewRunDetail, downloadRunCsv, deleteRun moved to features/history.js
    // (merged in app())

    totalMismatches(r) {
      return (r.value_mismatch_count || 0) + (r.missing_in_target_count || 0) + (r.missing_in_source_count || 0);
    },

    mismatchStats(r) {
      return {
        value: r?.value_mismatch_count || 0,
        missingTarget: r?.missing_in_target_count || 0,
        missingSource: r?.missing_in_source_count || 0,
        total: this.totalMismatches(r || {}),
      };
    },

    mismatchBreakdownText(r) {
      const s = this.mismatchStats(r);
      const parts = [];
      if (s.value) parts.push(`${s.value} value`);
      if (s.missingTarget) parts.push(`${s.missingTarget} missing in target`);
      if (s.missingSource) parts.push(`${s.missingSource} missing in source`);
      return parts.length ? parts.join(' / ') : '0';
    },

    columnStatKey(scope, r) {
      return `${scope}:${r?.id ?? r?.query_name ?? 'result'}`;
    },

    columnStatsFor(r, scope = 'default') {
      const key = this.columnStatKey(scope, r);
      const filter = (this.columnStatFilters[key] || '').toLowerCase();
      const sort = this.columnStatSort[key] || { field: 'mismatch_count', dir: -1 };
      const rows = [...(r?.column_stats || [])].filter(row => {
        if (!filter) return true;
        return String(row.column || '').toLowerCase().includes(filter);
      });
      rows.sort((a, b) => {
        const av = a[sort.field];
        const bv = b[sort.field];
        if (typeof av === 'number' || typeof bv === 'number') {
          return ((Number(av) || 0) - (Number(bv) || 0)) * sort.dir;
        }
        return String(av || '').localeCompare(String(bv || '')) * sort.dir;
      });
      return rows;
    },

    setColumnStatSort(scope, r, field) {
      const key = this.columnStatKey(scope, r);
      const current = this.columnStatSort[key] || { field: 'mismatch_count', dir: -1 };
      const dir = current.field === field ? -current.dir : (field === 'column' ? 1 : -1);
      this.columnStatSort = { ...this.columnStatSort, [key]: { field, dir } };
    },

    matchPctText(value) {
      return value == null ? 'N/A' : `${Number(value).toFixed(2)}%`;
    },

    detailRowsLabel(rows, r) {
      const loaded = Array.isArray(rows) ? rows.length : 0;
      const total = this.totalMismatches(r || {});
      if (total > loaded) return `${loaded} detail rows shown of ${total} total mismatches`;
      return `${loaded} detail rows shown`;
    },

    filteredDetailLabel(rows, filterKey, filterState, r) {
      const shown = this.filteredDiff(rows || [], filterKey, filterState).length;
      const loaded = Array.isArray(rows) ? rows.length : 0;
      const total = this.totalMismatches(r || {});
      if (total > loaded) return `${shown} shown of ${loaded} loaded (${total} total)`;
      if (shown !== loaded) return `${shown} shown of ${loaded} loaded`;
      return `${shown} shown`;
    },

    // toggleOutcomeOverrideForm, passWithAgreedActions, removeOutcomeOverride,
    // selectedResultCount, toggleResultSelection, clearResultSelection,
    // selectAllResults, deselectAllResults, isResultSelected,
    // openBulkDecisionForm, closeBulkDecisionForm, submitBulkDecision,
    // renderChart moved to features/history.js (merged in app())

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

    // toggleSampleRowsExpand, sampleRowColumns, toggleMismatchExpand,
    // loadMoreInlineMismatches moved to features/history.js (merged in app())

    // Compare methods moved to features/compare.js
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

    // Adapters (SAP BO / Automic) methods moved to features/adapters.js
    // (merged in app())

    // Reports tab methods (resetReportArtifacts, switchReportView,
    // loadReport, loadAllLogEvents, filteredLogEvents, loadRunMetrics,
    // metricsPassRate) moved to features/reports.js (merged in app()).
    // openReportTab/openRunTab/navigateToRunArtifact stay here — they're
    // called only from the History tab's run-detail markup, not from
    // Reports itself. highlightMatch/logLevelClass moved to app-config.js
    // as shared top-level utilities — see the const declarations above.

    // Differences Explorer tab methods (_applyDeepLink, selectDifferenceRun,
    // loadDifferenceInsights, selectDifferenceResult, differenceQueryString,
    // fetchDifferenceRows, applyDifferenceFilters, clearDifferenceFilters,
    // differenceTotalPages, nextDifferencePage, prevDifferencePage,
    // _renderDifferenceCharts) moved to features/differences.js (merged in
    // app()). openMismatchDecisionForm/closeMismatchDecisionForm/
    // submitMismatchDecision stay here — they're shared with the mismatch
    // drawer (scope: 'drawer', see decideAllPendingDrawerMismatches below),
    // not exclusive to the Differences tab.
    openMismatchDecisionForm(scope, decision) {
      this.mismatchDecisionForm = { open: true, scope, decision, note: '', saving: false };
    },

    closeMismatchDecisionForm() {
      this.mismatchDecisionForm = { open: false, scope: null, decision: null, note: '', saving: false };
    },

    async submitMismatchDecision() {
      const { scope, decision, note } = this.mismatchDecisionForm;
      const trimmed = (note || '').trim();
      if (!trimmed) {
        this.toast('warn', 'Reason required', 'Enter a reason before deciding these mismatches');
        return;
      }
      this.mismatchDecisionForm.saving = true;
      try {
        if (scope === 'diff') {
          const body = { decision, note: trimmed };
          if (this.diffSearch) body.search = this.diffSearch;
          if (this.diffColumn) body.column = this.diffColumn;
          if (this.diffType) body.mismatch_type = this.diffType;
          if (this.diffStatus) body.status = this.diffStatus;
          const result = await api('POST',
            `/api/runs/${this.diffRunId}/results/${this.diffResultId}/mismatches/bulk-decide`, body);
          this.closeMismatchDecisionForm();
          this.toast('success', `${result.decided_count} mismatch(es) ${decision}ed`,
            result.result_status_updated ? 'Test flipped to PASSED' : '');
          this.diffPage = 0;
          await this.fetchDifferenceRows();
          await this.loadDifferenceInsights();
        } else if (scope === 'drawer') {
          const body = { decision, note: trimmed, status: 'pending' };
          const result = await api('POST',
            `/api/runs/${this.drawer.runId}/results/${this.drawer.result.id}/mismatches/bulk-decide`, body);
          this.closeMismatchDecisionForm();
          this.toast('success', `${result.decided_count} mismatch(es) ${decision}ed`,
            result.result_status_updated ? 'Test flipped to PASSED' : '');
          this.drawer.offset = 0;
          this.drawer.loading = true;
          await this._fetchMismatches();
          if (result.decided_count > 0) await this.loadRuns();
        }
      } catch (e) {
        this.mismatchDecisionForm.saving = false;
        this.toast('error', 'Bulk decision failed', e.message);
      }
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

    // highlightMatch()/logLevelClass() moved to app-config.js as shared
    // top-level utilities (APP_CONFIG.highlightMatch / .logLevelClass),
    // same pattern as isTerminalStatusValue above. Still called bare
    // (no `this.`) from index.html and from features/reports.js and
    // features/logs.js, resolving to the top-level const declared above.

    // loadGlobalLogs / filteredGlobalLogEvents / startGlobalLogsPolling /
    // stopGlobalLogsPolling moved to features/logs.js (merged in app())

    navigateToRunArtifact(runId, view) {
      this.resetReportArtifacts();
      this.reportRunId = runId;
      this.reportView = view;
      this.currentView = 'reports';
      this.reportLoaded = true;
      if (view === 'metrics') this.loadRunMetrics();
      if (view === 'logs') this.loadAllLogEvents();
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
    // REGIONAL – APP-WIDE TIMEZONE
    // ===========================================================
    async loadTimezoneSetting() {
      try {
        const resp = await api('GET', '/api/settings');
        this.appTimezone = resp.timezone || 'UTC';
        this.timezoneDraft = this.appTimezone;
      } catch {}
    },

    async saveTimezoneSetting() {
      this.timezoneSaving = true;
      try {
        const resp = await api('PUT', '/api/settings', { timezone: this.timezoneDraft });
        this.appTimezone = resp.timezone;
        this.toast('success', 'Timezone updated', `All timestamps now shown in ${resp.timezone}`);
      } catch (e) {
        this.toast('error', 'Failed to update timezone', e.message || '');
      } finally {
        this.timezoneSaving = false;
      }
    },

    // setBaseline, badgeUrl, copyBadgeUrl, loadTrends, renderTrendsChart,
    // loadMismatchDist, loadSegmentDrill, segmentMax, isSegDrillBusy,
    // loadLineage, lineageSvg, loadCoverage, coverageColumns,
    // coverageLevelClass moved to features/history.js (merged in app())

    // ===========================================================
    // UTILITIES
    // ===========================================================
    fmtDate(iso) {
      if (!iso) return '—';
      // Treat bare ISO strings (no timezone suffix) as UTC so conversion below is correct
      const ts = /[Zz]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + 'Z';
      const d = new Date(ts);
      if (isNaN(d.getTime())) return '—';
      try {
        return new Intl.DateTimeFormat([], {
          timeZone: this.appTimezone || 'UTC',
          year: 'numeric', month: 'numeric', day: 'numeric',
          hour: '2-digit', minute: '2-digit',
        }).format(d);
      } catch {
        // Unknown/unsupported timeZone value — fall back to browser-local rather than throwing
        return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }
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
            label: 'Stored detail rows',
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
    // ===========================================================
    get filteredMismatches() {
      const rows = this.drawer.rows || [];
      if (this.mismatchStatusFilter === 'ALL') return rows;
      if (this.mismatchStatusFilter === 'ACCEPTED') return rows.filter(m => m.accepted);
      if (this.mismatchStatusFilter === 'REJECTED') return rows.filter(m => m.rejected);
      if (this.mismatchStatusFilter === 'PENDING') return rows.filter(m => !m.accepted && !m.rejected);
      return rows;
    },

    async decideAllPendingDrawerMismatches(decision) {
      this.openMismatchDecisionForm('drawer', decision);
    },

    ...HELP_METHODS,

    // -----------------------------------------------------------
    // Help center
    // -----------------------------------------------------------
    helpNormalize(s) {
      return (s || '').toString().toLowerCase();
    },
    helpSectionMatches(section, q) {
      if (!q) return true;
      const hay = [section.title, section.intro,
        ...(section.steps || []).flatMap((s) => [s.title, s.text, s.where, s.tip, s.warn])]
        .map((v) => this.helpNormalize(v)).join(' ');
      return hay.includes(q);
    },
    helpFilteredSections() {
      const q = this.helpNormalize(this.helpSearch.trim());
      if (!q) return this.helpSections;
      return this.helpSections.filter((s) => this.helpSectionMatches(s, q));
    },
    helpStepMatches(step, q) {
      if (!q) return true;
      const hay = [step.title, step.text, step.where, step.tip, step.warn]
        .map((v) => this.helpNormalize(v)).join(' ');
      return hay.includes(q);
    },
    scrollToHelp(id) {
      this.helpActiveId = id;
      const el = document.getElementById('help-' + id);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },

  };
  // Several feature slices (e.g. ETL_FEATURE_LAUNCH(), and `core` itself)
  // define real `get` accessors (filteredJobList, jobCatalogCountLabel,
  // estimatedSequenceDuration, ...). A plain Object.assign would read each
  // one immediately and freeze it as a one-time snapshot value instead of a
  // live computed property, silently breaking reactivity. Object.defineProperties
  // + Object.getOwnPropertyDescriptors correctly copies getters AND plain data
  // properties either way, so every slice — not just the ones that currently
  // happen to declare getters — is merged uniformly through it. This removes
  // the need to judge, slice by slice, whether "this one needs" special
  // handling: future slices with getters are handled automatically, and
  // forgetting to special-case a getter-bearing slice can no longer happen.
  const FEATURE_SLICES = [ETL_FEATURE_COMPARE(), ETL_FEATURE_CONFIG(), ETL_FEATURE_LAUNCH(), ETL_FEATURE_MONITOR(), ETL_FEATURE_HISTORY(), ETL_FEATURE_ADAPTERS(), ETL_FEATURE_REPORTS(), ETL_FEATURE_DIFFERENCES(), ETL_FEATURE_CONTRACTS(), ETL_FEATURE_LOGS()];
  const merged = FEATURE_SLICES.reduce(
    (acc, slice) => Object.defineProperties(acc, Object.getOwnPropertyDescriptors(slice)),
    {}
  );
  return Object.defineProperties(merged, Object.getOwnPropertyDescriptors(core));

}
