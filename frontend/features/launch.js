(function (global) {
  'use strict';
  // Launch feature slice (Launch tab: job catalog/CRUD, run launch,
  // Schedules sub-tab). Merged into the Alpine component via
  // the FEATURE_SLICES reduce in app.js.
  global.ETL_FEATURE_LAUNCH = function () {
    return {
      // ===== STATE (extracted from app.js) =====
    // -----------------------------------------------------------
    // Jobs / Launch
    // -----------------------------------------------------------
    jobs: [],
    selectedJobs: [],
    // Saved execution sequences available to attach to a selection or schedule.
    availableSequences: [],
    selectionSourceMode: 'inline',     // 'inline' | 'sequence'
    selectionSequenceRef: { sequence_id: null, sequence_version: null },
    scheduleTargetMode: 'selection',   // 'selection' | 'sequence'
    scheduleSequenceRef: { sequence_id: null, sequence_version: null },
    stepSettings: {},      // { jobName: { hold_after, wait_seconds, require_status, max_mismatch_count } }
    stepSettingsOpen: {},  // { jobName: bool } — expanded settings panel
    // NOTE: app-help.js's global Escape-key handler reads this flag directly to
    // close the modal — don't rename without updating app-help.js too.
    showJobModal: false,
    showJobModalCompare: false,
    // The multi-file panel in tab-launch.html binds jobModal.mf_*_preview_creds.*
    // for 16 credential fields. Those bindings evaluate as soon as the Launch tab
    // mounts — long before newJob()/openJobModal() assigns the full shape — so the
    // two nested containers must exist from the start or Alpine logs an expression
    // error per field. Both code paths overwrite these wholesale, so seeding them
    // empty here changes nothing once a modal is actually opened.
    jobModal: { mf_source_preview_creds: {}, mf_target_preview_creds: {} },
    jobModalEditing: false,
    jobGateVerdicts: {},
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
      run_profile: 'full',
      shadow_sample_frac: 0.02,
      mismatch_row_limit: 1000,
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
    validateDefinitionLoading: false,
    validateDefinitionResult: null,

    // -----------------------------------------------------------
    // Schedules
    // -----------------------------------------------------------
    schedules: [],
    schedulesError: '',
    jobSelectionsError: '',
    schedulerStats: null,
    schedulerStatsLoading: false,
    schedulerStatsError: '',
    launchSubTab: 'jobs',
    // NOTE: app-help.js's global Escape-key handler reads this flag directly to
    // close the modal — don't rename without updating app-help.js too.
    showScheduleModal: false,
    scheduleModal: {},
    jobSelections: [],
    showSelectionModal: false,
    selectionModal: {},
    selectionModalEditing: false,
    showCiIntegrationModal: false,
    ciIntegrationModal: {},
    selectedSelectionJobNames: [],
    showLaunchSelectionModal: false,
    launchSelectionModal: {},
    showSelectionRunsModal: false,
    selectionRunsPanel: null,
    selectionRuns: [],
    compareRunIds: [],
    selectionCompareJobName: '',
    selectionCompareType: 'mismatch_diff', // 'mismatch_diff' | 'bo' | 'recon' | 'multi_file'
    scheduleModalEditing: false,

    jobSearchQuery: '',

    multiSelectMode: false,
    shiftLastIndex: -1,

    // (savedConfigDisplay is a method; session settings persisted via methods)

    jobModalTab: 'basic',
    jobModalTabs: APP_CONFIG.jobModalTabs || [],

    jobModalValidation: { sql: '', keyColumns: '', dependencies: '' },

    dqRuleTemplates: APP_CONFIG.dqRuleTemplates || [],
    jobTemplates: [],
    jobTemplateName: '',
    showSaveTemplatePrompt: false,

    dragSrcIndex: null,
      // ===== METHODS (extracted from app.js) =====
    // ===========================================================
    // JOBS / LAUNCH
    // ===========================================================
    async loadJobs() {
      try {
        const jobs = await api('GET', '/api/jobs');
        this.jobs = Array.isArray(jobs) ? jobs : [];
        return true;
      } catch (_) {
        return false;
      }
    },

    _upsertJobInList(job) {
      if (!job?.name) return;
      if (!Array.isArray(this.jobs)) this.jobs = [];
      const idx = this.jobs.findIndex(j => j.name === job.name);
      if (idx >= 0) this.jobs.splice(idx, 1, job);
      else this.jobs.push(job);
      this.jobs.sort((a, b) => String(a.name || '').localeCompare(String(b.name || '')));
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
        source_mode: 'sql',
        source_file_path: '', target_file_path: '',
        source_file_label: '', target_file_label: '',
        target_source_mode: 'path', target_file_b64: '', target_file_name: '',
        key_columns_raw: 'id', tags_raw: '', enabled: true,
        depends_on_raw: '', rules: [],
        bo_report_id: '', bo_page_id: '', bo_format: 'xlsx', bo_parameters: [],
        automic_job_name: '', automic_run_id: '',
        bo_job_object_id: '', bo_job_schedule_params_raw: '',
        bo_job_poll_interval_s: '', bo_job_timeout_s: '',
        ds_job_name: '', ds_job_repository: '', ds_job_params_raw: '',
        ds_job_poll_interval_s: '', ds_job_timeout_s: '',
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
        mf_strategy: 'explicit',
        mf_match_on_raw: '',
        mf_unmatched_policy: 'fail',
        mf_similarity_threshold: 0.7,
        mf_signal_filename: true,
        mf_signal_columns: true,
        mf_signal_rowcount: true,
        mf_source_kind: 'local', mf_source_root: '', mf_source_pattern: '', mf_source_credentials_ref: '',
        mf_target_kind: 'local', mf_target_root: '', mf_target_pattern: '', mf_target_credentials_ref: '',
        mf_source_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
        mf_target_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
        mfPreviewLoading: false,
        mfPreviewResult: null,
        mfPreviewError: '',
      };
      this.showJobModalCompare = false;
      // Prior Test Compare results belong to whichever job produced them --
      // don't let a stale badge/link linger under a different job's identity.
      this.boCompareResult = null;
      this.mfCompareResult = null;
      this.fileCompareResult = null;
      this.jobModalEditing = false;
      this.validateJobResult = null;
      this.jobModalValidation = { sql: '', keyColumns: '', dependencies: '' };
      this.jobModalTab = 'basic';
      this.showJobModal = true;
    },

    newDQRule(type = 'not_null') {
      return {
        type,
        column: '',
        severity: 'error',
        min_value: null,
        max_value: null,
        pattern: null,
        percentile: null,
        operator: null,
        column_b: null,
        lookup_query: null,
        expected_type: null,
        threshold: null,
        iqr_multiplier: null,
        fence_type: 'inner',
        distribution: 'normal',
        distribution_params: null,
        alpha: null,
        bins: null,
        expected_frequencies: [],
        expected_frequencies_raw: '',
        expected_proportion: null,
        condition: null,
        window: null,
      };
    },

    _hydrateDQRule(rule) {
      const next = { ...this.newDQRule(rule?.type || 'not_null'), ...(rule || {}) };
      next.expected_frequencies_raw = Array.isArray(next.expected_frequencies)
        ? next.expected_frequencies.join(', ')
        : '';
      return next;
    },

    _numberOrNull(value) {
      if (value === '' || value === null || value === undefined) return null;
      const num = Number(value);
      return Number.isFinite(num) ? num : null;
    },

    _serializeDQRule(rule) {
      const out = {
        ...rule,
        min_value: this._numberOrNull(rule.min_value),
        max_value: this._numberOrNull(rule.max_value),
        percentile: this._numberOrNull(rule.percentile),
        threshold: this._numberOrNull(rule.threshold),
        iqr_multiplier: this._numberOrNull(rule.iqr_multiplier),
        alpha: this._numberOrNull(rule.alpha),
        bins: this._numberOrNull(rule.bins),
        expected_proportion: this._numberOrNull(rule.expected_proportion),
        window: this._numberOrNull(rule.window),
      };
      if (typeof rule.expected_frequencies_raw === 'string') {
        out.expected_frequencies = rule.expected_frequencies_raw
          .split(',')
          .map(s => Number(s.trim()))
          .filter(n => Number.isFinite(n));
      }
      delete out.expected_frequencies_raw;
      Object.keys(out).forEach(key => {
        if (out[key] === '' || out[key] === undefined) out[key] = null;
      });
      if (!Array.isArray(out.expected_frequencies)) out.expected_frequencies = [];
      return out;
    },

    openEditJobModal(job) {
      this.jobModal = {
        name: job.name, description: job.description || '',
        job_type: job.job_type || 'reconciliation',
        query: job.query || '', key_columns_raw: (job.key_columns || ['id']).join(', '),
        source_mode: job.params?.source_mode || (job.params?.source_file_path || job.params?.file_a_path ? 'files' : 'sql'),
        source_file_path: job.params?.source_file_path || job.params?.file_a_path || '',
        target_file_path: job.params?.target_file_path || job.params?.file_b_path || '',
        source_file_label: job.params?.source_file_label || job.params?.label_a || '',
        target_file_label: job.params?.target_file_label || job.params?.label_b || '',
        target_file_b64: job.params?.target_file_content_b64 || '',
        target_file_name: job.params?.target_file_name || '',
        target_source_mode: job.params?.target_file_content_b64 ? 'upload' : 'path',
        tags_raw: (job.tags || []).join(', '), enabled: job.enabled !== false,
        depends_on_raw: (job.depends_on || []).join(', '),
        rules: (job.rules || []).map(r => this._hydrateDQRule(r)),
        bo_report_id: job.params?.report_id || '',
        bo_page_id: job.params?.bo_report_id || '',
        bo_format: job.params?.format || 'xlsx',
        bo_parameters: (job.params?.bo_parameters || []).map(p => ({ ...p })),
        // Fields with colliding param keys (e.g. 'job_name' shared by automic_job/ds_job,
        // 'poll_interval_s'/'timeout_s' shared by bo_job/ds_job) must guard on job.job_type
        // to prevent one type's saved value silently leaking into another type's hidden field
        // if the user switches Job Type dropdown mid-edit without saving.
        automic_job_name: job.job_type === 'automic_job' ? (job.params?.job_name || '') : '',
        automic_run_id: job.params?.run_id || '',
        bo_job_object_id: job.params?.object_id || '',
        bo_job_schedule_params_raw: job.params?.schedule_params ? JSON.stringify(job.params.schedule_params) : '',
        bo_job_poll_interval_s: job.job_type === 'bo_job' ? (job.params?.poll_interval_s ?? '') : '',
        bo_job_timeout_s: job.job_type === 'bo_job' ? (job.params?.timeout_s ?? '') : '',
        ds_job_name: job.job_type === 'ds_job' ? (job.params?.job_name || '') : '',
        ds_job_repository: job.params?.repository || '',
        ds_job_params_raw: job.params?.job_params ? JSON.stringify(job.params.job_params) : '',
        ds_job_poll_interval_s: job.job_type === 'ds_job' ? (job.params?.poll_interval_s ?? '') : '',
        ds_job_timeout_s: job.job_type === 'ds_job' ? (job.params?.timeout_s ?? '') : '',
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
        mf_strategy: job.params?.file_mapping?.strategy || 'explicit',
        mf_match_on_raw: (job.params?.file_mapping?.match_on || []).join(', '),
        mf_unmatched_policy: job.params?.file_mapping?.unmatched_policy || 'fail',
        mf_similarity_threshold: job.params?.file_mapping?.automated_mapping?.similarity_threshold ?? 0.7,
        mf_signal_filename: (job.params?.file_mapping?.automated_mapping?.signals || ['filename_tokens', 'column_signature', 'row_count_ratio']).includes('filename_tokens'),
        mf_signal_columns: (job.params?.file_mapping?.automated_mapping?.signals || ['filename_tokens', 'column_signature', 'row_count_ratio']).includes('column_signature'),
        mf_signal_rowcount: (job.params?.file_mapping?.automated_mapping?.signals || ['filename_tokens', 'column_signature', 'row_count_ratio']).includes('row_count_ratio'),
        mf_source_kind: job.params?.file_mapping?.source?.kind || 'local',
        mf_source_root: job.params?.file_mapping?.source?.root || '',
        mf_source_pattern: job.params?.file_mapping?.source?.pattern || '',
        mf_source_credentials_ref: job.params?.file_mapping?.source?.credentials_ref || '',
        mf_target_kind: job.params?.file_mapping?.target?.kind || 'local',
        mf_target_root: job.params?.file_mapping?.target?.root || '',
        mf_target_pattern: job.params?.file_mapping?.target?.pattern || '',
        mf_target_credentials_ref: job.params?.file_mapping?.target?.credentials_ref || '',
        // Preview-only credentials are never persisted with the job, so
        // there's nothing in `job.params` to hydrate them from -- always
        // start blank, same as newJobModal.
        mf_source_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
        mf_target_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
        mfPreviewLoading: false,
        mfPreviewResult: null,
        mfPreviewError: '',
      };
      this.showJobModalCompare = false;
      // Prior Test Compare results belong to whichever job produced them --
      // don't let a stale badge/link linger under a different job's identity.
      this.boCompareResult = null;
      this.mfCompareResult = null;
      this.fileCompareResult = null;
      this.jobModalEditing = true;
      this.validateJobResult = null;
      this.jobModalValidation = { sql: '', keyColumns: '', dependencies: '' };
      this.jobModalTab = 'basic';
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
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.storedToken}` },
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

    _buildFileMappingConfig(m) {
      const match_on = m.mf_match_on_raw.split(',').map(s => s.trim()).filter(Boolean);
      const config = {
        strategy: m.mf_strategy,
        unmatched_policy: m.mf_unmatched_policy,
        source: { kind: m.mf_source_kind, root: m.mf_source_root, pattern: m.mf_source_pattern },
        target: { kind: m.mf_target_kind, root: m.mf_target_root, pattern: m.mf_target_pattern },
      };
      if (m.mf_strategy === 'explicit') config.match_on = match_on;
      if (m.mf_strategy === 'automated') {
        const signals = [];
        if (m.mf_signal_filename) signals.push('filename_tokens');
        if (m.mf_signal_columns) signals.push('column_signature');
        if (m.mf_signal_rowcount) signals.push('row_count_ratio');
        const parsedThreshold = Number(m.mf_similarity_threshold);
        config.automated_mapping = {
          // Number(...) || 0.7 would silently turn an explicit 0 into 0.7
          // (JS falsy coercion) even though the backend allows 0.0 as a
          // valid (if degenerate, "match everything") threshold.
          similarity_threshold: Number.isFinite(parsedThreshold) && m.mf_similarity_threshold !== '' ? parsedThreshold : 0.7,
          signals,
        };
      }
      if (m.mf_source_kind !== 'local' && m.mf_source_credentials_ref) config.source.credentials_ref = m.mf_source_credentials_ref;
      if (m.mf_target_kind !== 'local' && m.mf_target_credentials_ref) config.target.credentials_ref = m.mf_target_credentials_ref;
      return config;
    },

    _previewCredsForKind(kind, creds) {
      const out = {};
      if (kind === 's3') {
        if (creds.aws_access_key_id) out.aws_access_key_id = creds.aws_access_key_id;
        if (creds.aws_secret_access_key) out.aws_secret_access_key = creds.aws_secret_access_key;
        if (creds.region_name) out.region_name = creds.region_name;
        if (creds.endpoint_url) out.endpoint_url = creds.endpoint_url;
      } else if (kind === 'sftp') {
        if (creds.host) out.host = creds.host;
        if (creds.port !== '' && creds.port !== null && creds.port !== undefined) {
          const port = Number(creds.port);
          if (Number.isFinite(port)) out.port = port;
        }
        if (creds.username) out.username = creds.username;
        if (creds.password) out.password = creds.password;
      }
      return out;
    },

    async previewFileMapping() {
      const m = this.jobModal;
      m.mfPreviewLoading = true;
      m.mfPreviewResult = null;
      m.mfPreviewError = '';
      try {
        // _buildFileMappingConfig(m) returns a fresh object every call (not
        // a reference into jobModal state), so mutating its credentials_ref
        // below for this one preview request is safe -- it never touches
        // the persisted job config the Save button will later write.
        const fileMapping = this._buildFileMappingConfig(m);
        const fileSourceCredentials = {};
        if (m.mf_source_kind !== 'local') {
          fileMapping.source.credentials_ref = '__preview_source__';
          fileSourceCredentials.__preview_source__ = this._previewCredsForKind(m.mf_source_kind, m.mf_source_preview_creds);
        }
        if (m.mf_target_kind !== 'local') {
          fileMapping.target.credentials_ref = '__preview_target__';
          fileSourceCredentials.__preview_target__ = this._previewCredsForKind(m.mf_target_kind, m.mf_target_preview_creds);
        }
        m.mfPreviewResult = await api('POST', '/api/jobs/preview-file-mapping', {
          file_mapping: fileMapping,
          file_source_credentials: fileSourceCredentials,
        });
      } catch (e) {
        m.mfPreviewError = e.message || 'Preview failed';
      } finally {
        m.mfPreviewLoading = false;
      }
    },

    handleJobTargetFileUpload(event) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        const bytes = new Uint8Array(e.target.result);
        let binary = '';
        for (let i = 0; i < bytes.length; i += 8192) {
          binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
        }
        this.jobModal.target_file_b64 = btoa(binary);
        this.jobModal.target_file_name = file.name;
      };
      reader.readAsArrayBuffer(file);
    },

    addDQRule() {
      this.jobModal.rules.push(this.newDQRule());
    },

    removeDQRule(idx) {
      this.jobModal.rules.splice(idx, 1);
    },

    _buildBoParameters(m) {
      return (m.bo_parameters || []).map(p => ({
        id: Number(p.id) || 0,
        type: p.type,
        value: String(p.value ?? ''),
      }));
    },

    // Prompts apply to any job that pulls a report live from BO — the bo_report
    // job type and a bo_live-sourced reconciliation.
    boPromptsApplicable(m) {
      const job = m || this.jobModal || {};
      return job.job_type === 'bo_report'
        || (job.job_type === 'reconciliation' && job.source_mode === 'bo_live');
    },

    // Pull the document's real prompt ids/types/defaults from BO, the same
    // endpoint the Adaptors tab uses, instead of hand-typing numeric ids.
    async loadBoParametersFromDocument() {
      const m = this.jobModal;
      const configId = m.previewConfigId || this.launchSettings.config_id;
      if (!configId || !m.bo_report_id) {
        this.toast('warn', 'Cannot load prompts', 'Select a config and enter the BO Document ID first');
        return;
      }
      try {
        const params = await api('GET',
          `/api/adapters/sap-bo/documents/${encodeURIComponent(m.bo_report_id)}/parameters?config_id=${configId}`);
        const existing = new Map((m.bo_parameters || []).map(p => [Number(p.id), p]));
        m.bo_parameters = (params || []).map(p => ({
          id: p.id,
          name: p.name || '',
          type: p.type || 'String',
          // Keep an answer the user already typed for this prompt id.
          value: existing.get(Number(p.id))?.value
            || p.default
            || (p.type === 'DateTime' ? this._todayIsoDate() : ''),
        }));
        this.toast('success', 'Prompts loaded', `${m.bo_parameters.length} parameter(s) from BO`);
      } catch (e) {
        this.toast('error', 'Load prompts failed', e.message);
      }
    },

    _todayIsoDate() {
      const d = new Date();
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    },

    addBoParameter() {
      // Match the interactive download default: a DateTime prompt pre-fills
      // today's local date (ISO YYYY-MM-DD for the date picker), editable.
      this.jobModal.bo_parameters.push({ id: 0, type: 'DateTime', value: this._todayIsoDate() });
    },

    removeBoParameter(idx) {
      this.jobModal.bo_parameters.splice(idx, 1);
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

    _buildJobRequestBody(m) {
      const params = {};
      const fileCapableJob = ['reconciliation', 'freshness', 'profile', 'schema_snapshot'].includes(m.job_type);
      const usesFileSource = fileCapableJob && m.source_mode === 'files';
      if (usesFileSource) {
        params.source_mode = 'files';
        if (m.source_file_path) params.source_file_path = m.source_file_path;
        if (m.source_file_label) params.source_file_label = m.source_file_label;
        if (m.job_type === 'reconciliation') {
          if (m.target_file_path) params.target_file_path = m.target_file_path;
          if (m.target_file_label) params.target_file_label = m.target_file_label;
        }
      }
      const usesMultiFile = m.job_type === 'reconciliation' && m.source_mode === 'multi_file';
      if (usesMultiFile) {
        params.source_mode = 'multi_file';
        params.file_mapping = this._buildFileMappingConfig(m);
      }
      const usesBoLive = m.job_type === 'reconciliation' && m.source_mode === 'bo_live';
      if (usesBoLive) {
        params.source_mode = 'bo_live';
        if (m.bo_report_id) params.report_id = m.bo_report_id;
        if (m.bo_page_id) params.bo_report_id = m.bo_page_id;
        params.format = m.bo_format || 'xlsx';
        // _build_case_bo_live_recon answers these before pulling the report, exactly
        // as the bo_report path does. Omitting them meant a bo_live recon always
        // exported whatever prompt answers the document last held.
        params.bo_parameters = this._buildBoParameters(m);
        if (m.target_source_mode === 'upload' && m.target_file_b64) {
          params.target_file_content_b64 = m.target_file_b64;
          if (m.target_file_name) params.target_file_name = m.target_file_name;
        } else if (m.target_file_path) {
          params.target_file_path = m.target_file_path;
        }
        if (m.target_file_label) params.target_file_label = m.target_file_label;
      }
      if (m.job_type === 'automic_job') {
        if (m.automic_job_name) params.job_name = m.automic_job_name;
        if (m.automic_run_id) params.run_id = m.automic_run_id;
      }
      if (m.job_type === 'bo_job') {
        params.object_id = m.bo_job_object_id;
        if (m.bo_job_schedule_params_raw) {
          try {
            params.schedule_params = JSON.parse(m.bo_job_schedule_params_raw);
          } catch (e) {
            throw new Error('Schedule Params must be valid JSON');
          }
        }
        if (m.bo_job_poll_interval_s !== '') params.poll_interval_s = Number(m.bo_job_poll_interval_s) || 5;
        if (m.bo_job_timeout_s !== '') params.timeout_s = Number(m.bo_job_timeout_s) || 600;
      }
      if (m.job_type === 'ds_job') {
        params.job_name = m.ds_job_name;
        if (m.ds_job_repository) params.repository = m.ds_job_repository;
        if (m.ds_job_params_raw) {
          try {
            params.job_params = JSON.parse(m.ds_job_params_raw);
          } catch (e) {
            throw new Error('Job Params must be valid JSON');
          }
        }
        if (m.ds_job_poll_interval_s !== '') params.poll_interval_s = Number(m.ds_job_poll_interval_s) || 5;
        if (m.ds_job_timeout_s !== '') params.timeout_s = Number(m.ds_job_timeout_s) || 600;
      }
      if (m.job_type === 'api_reconciliation') {
        params.source_api_endpoint = m.api_source_endpoint;
        if (m.api_target_endpoint) params.target_api_endpoint = m.api_target_endpoint;
      }
      if (m.job_type === 'bo_report') {
        if (m.bo_report_id) params.report_id = m.bo_report_id;
        if (m.bo_page_id) params.bo_report_id = m.bo_page_id;
        params.format = m.bo_format || 'xlsx';
        params.bo_parameters = this._buildBoParameters(m);
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
      return {
        name: m.name, description: m.description,
        job_type: m.job_type,
        query: ['reconciliation', 'freshness', 'profile', 'schema_snapshot'].includes(m.job_type) && !usesFileSource && !usesBoLive && !usesMultiFile ? m.query : '',
        key_columns: keyColumns,
        tags: m.tags_raw.split(',').map(s => s.trim()).filter(Boolean),
        enabled: m.enabled,
        depends_on: m.depends_on_raw.split(',').map(s => s.trim()).filter(Boolean),
        rules: (m.rules || []).filter(r => r.type).map(r => this._serializeDQRule(r)),
        params,
        pass_condition: Object.keys(pc).length ? pc : null,
      };
    },

    async validateJobDefinition() {
      this.validateDefinitionLoading = true;
      this.validateDefinitionResult = null;
      try {
        const body = this._buildJobRequestBody(this.jobModal);
        this.validateDefinitionResult = await api('POST', '/api/jobs/validate', body);
        if (this.validateDefinitionResult.ok) {
          this.toast('success', 'Job definition valid', this.jobModal.name || 'Untitled job');
        } else {
          const first = this.validateDefinitionResult.issues?.[0];
          this.toast('error', 'Job validation failed', first ? `${first.field}: ${first.message}` : 'Fix validation issues');
        }
        return this.validateDefinitionResult;
      } catch (e) {
        this.validateDefinitionResult = { ok: false, issues: [{ field: 'request', message: e.message, severity: 'error' }] };
        this.toast('error', 'Validation failed', e.message);
        return this.validateDefinitionResult;
      } finally {
        this.validateDefinitionLoading = false;
      }
    },

    async saveJob() {
      const m = this.jobModal;
      const validation = await this.validateJobDefinition();
      if (!validation?.ok) return;
      const body = this._buildJobRequestBody(m);
      try {
        let savedJob;
        if (this.jobModalEditing) {
          savedJob = await api('PUT', `/api/jobs/${encodeURIComponent(m.name)}`, body);
        } else {
          savedJob = await api('POST', '/api/jobs', body);
          this.jobSearchQuery = '';
        }
        this._upsertJobInList(savedJob);
        const refreshed = await this.loadJobs();
        if (!refreshed) this._upsertJobInList(savedJob);
        this.showJobModal = false;
        this.toast('success', this.jobModalEditing ? 'Job updated' : 'Job created', m.name);
      } catch (e) {
        this.toast('error', 'Save failed', e.message);
      }
    },

    canSaveJob() {
      const m = this.jobModal;
      if (!m?.name) return false;
      const hasKeys = Boolean(m.key_columns_raw?.split(',').map(s => s.trim()).filter(Boolean).length);
      if (m.job_type === 'reconciliation') {
        if (m.source_mode === 'files') {
          // key_columns is optional for file-backed jobs: the backend infers a
          // shared ID column, or falls back to positional row matching.
          return Boolean(m.source_file_path && m.target_file_path);
        }
        if (m.source_mode === 'bo_live') {
          const hasTarget = m.target_source_mode === 'upload'
            ? Boolean(m.target_file_b64)
            : Boolean(m.target_file_path);
          return Boolean(m.bo_report_id && m.bo_page_id && hasTarget);
        }
        if (m.source_mode === 'multi_file') {
          // Mirrors the backend's FileMappingSpec.from_params() (api/schemas.py's
          // validate_reconciliation_contract): source/target each need a root + pattern.
          // key_columns stays optional here too, same as 'files' mode above.
          return Boolean(m.mf_source_root && m.mf_source_pattern && m.mf_target_root && m.mf_target_pattern);
        }
        return Boolean(m.query?.trim() && hasKeys);
      }
      if (m.job_type === 'bo_report') return Boolean(m.bo_report_id && m.bo_page_id);
      if (m.job_type === 'bo_job') return Boolean(m.bo_job_object_id);
      if (m.job_type === 'ds_job') return Boolean(m.ds_job_name);
      if (m.job_type === 'automic_job') return Boolean(m.automic_job_name || m.automic_run_id);
      if (m.job_type === 'api_reconciliation') {
        return Boolean(
          m.api_source_endpoint &&
          m.key_columns_raw?.split(',').map(s => s.trim()).filter(Boolean).length
        );
      }
      if (m.job_type === 'dbt_artifact') return Boolean(m.dbt_run_results_path);
      if (m.job_type === 'freshness') {
        return Boolean((m.source_mode === 'files' ? m.source_file_path : m.query?.trim()) && m.freshness_ts_col);
      }
      if (m.job_type === 'profile') return Boolean(m.source_mode === 'files' ? m.source_file_path : m.query?.trim());
      if (m.job_type === 'schema_snapshot') return Boolean(m.source_mode === 'files' ? m.source_file_path : m.query?.trim());
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

    async checkJobGate(name) {
      try {
        const verdict = await api('POST', `/api/gates/${encodeURIComponent(name)}/evaluate`);
        this.jobGateVerdicts = { ...this.jobGateVerdicts, [name]: verdict };
        if (verdict.verdict === 'PROMOTE') {
          this.toast('success', 'PROMOTE', name);
        } else {
          this.toast('error', 'HOLD', verdict.reasons.join('; ') || name);
        }
      } catch (e) {
        this.toast('error', 'Gate check failed', e.message);
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
        run_profile: s.run_profile || 'full',
        shadow_sample_frac: Number(s.shadow_sample_frac) || 0.02,
        mismatch_row_limit: Number(s.mismatch_row_limit) || 1000,
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
    // SCHEDULES
    // ===========================================================
    async loadSchedules() {
      // Don't swallow the error: an empty `schedules` renders identically
      // whether none exist or the request 401'd/500'd, which sends the user
      // off creating things that already exist. Keep the message.
      this.schedulesError = '';
      try {
        this.schedules = await api('GET', '/api/schedules');
      } catch (e) {
        this.schedulesError = e.message || 'Unable to load schedules';
      }
      await this.loadSchedulerStats();
    },

    async loadSchedulerStats() {
      this.schedulerStatsLoading = true;
      this.schedulerStatsError = '';
      try {
        this.schedulerStats = await api('GET', '/api/schedules/stats?days=30');
      } catch (e) {
        this.schedulerStatsError = e.message || 'Unable to load scheduler statistics';
      } finally {
        this.schedulerStatsLoading = false;
      }
    },

    formatSchedulerPercent(value) {
      return value === null || value === undefined ? 'n/a' : `${Number(value).toFixed(2)}%`;
    },

    formatSchedulerDuration(value) {
      if (value === null || value === undefined) return 'n/a';
      if (value < 60) return `${Number(value).toFixed(1)}s`;
      return `${(Number(value) / 60).toFixed(1)}m`;
    },

    formatDateTime(value) {
      return this.fmtDate(value);
    },

    schedulerStateLabel() {
      const scheduler = this.schedulerStats?.scheduler;
      if (!scheduler) return 'Loading';
      if (!scheduler.available) return 'Unavailable';
      return scheduler.running ? 'Running' : 'Stopped';
    },

    openNewScheduleModal() {
      this.scheduleModal = {
        name: '', cron_expr: '0 6 * * *',
        source_env: 'dev', target_env: 'prod',
        selection_id: this.jobSelections[0]?.id || '',
        enabled: true,
      };
      this.scheduleTargetMode = 'selection';
      this.scheduleSequenceRef = { sequence_id: null, sequence_version: null };
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
        selection_id: sched.selection_id,
        selection_version: sched.selection_version,
        enabled: sched.enabled,
      };
      if (sched.sequence_id) {
        this.scheduleTargetMode = 'sequence';
        this.scheduleSequenceRef = {
          sequence_id: sched.sequence_id,
          sequence_version: sched.sequence_version,
        };
      } else {
        this.scheduleTargetMode = 'selection';
        this.scheduleSequenceRef = { sequence_id: null, sequence_version: null };
      }
      this.scheduleModalEditing = true;
      this.showScheduleModal = true;
    },

    async saveSchedule() {
      const m = this.scheduleModal;
      const body = {
        name: m.name,
        cron_expr: m.cron_expr,
        source_env: m.source_env,
        target_env: m.target_env || '',
        enabled: m.enabled,
      };
      if (this.scheduleTargetMode === 'sequence') {
        body.sequence_id = Number(this.scheduleSequenceRef.sequence_id);
        body.sequence_version = this.scheduleSequenceRef.sequence_version
          ? Number(this.scheduleSequenceRef.sequence_version)
          : null;
      } else {
        body.selection_id = Number(m.selection_id);
        body.selection_version = m.selection_version || null;
      }
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
        setTimeout(() => { this.loadRuns(); this.loadSchedulerStats(); }, 1000);
      } catch (e) {
        this.toast('error', 'Trigger failed', e.message);
      }
    },

    // ===========================================================
    // JOB SELECTIONS
    // ===========================================================
    async loadAvailableSequences() {
      try {
        this.availableSequences = await api('GET', '/api/sequences');
      } catch { this.availableSequences = []; }
    },

    sequenceVersionOptions(sequenceId) {
      const seq = this.availableSequences.find((s) => s.id === Number(sequenceId));
      if (!seq) return [];
      return Array.from({ length: seq.latest_version }, (_, i) => i + 1).reverse();
    },

    async loadJobSelections() {
      // Same reasoning as loadSchedules(): a failed fetch must not be
      // indistinguishable from "you have no job selections yet" — that empty
      // state is what the New Schedule modal keys its guidance off.
      this.jobSelectionsError = '';
      try {
        this.jobSelections = await api('GET', '/api/selections');
      } catch (e) {
        this.jobSelectionsError = e.message || 'Unable to load job selections';
      }
      await this.loadAvailableSequences();
    },

    openNewSelectionModal() {
      this.selectionModal = { name: '', description: '', tags: '', use_live_connections: false, config_id: '' };
      this.selectedSelectionJobNames = [];
      this.selectionSourceMode = 'inline';
      this.selectionSequenceRef = { sequence_id: null, sequence_version: null };
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
        use_live_connections: Boolean(latest.run_settings?.use_live_connections),
        config_id: latest.config_id || '',
      };
      if (latest.sequence_ref) {
        this.selectionSourceMode = 'sequence';
        this.selectionSequenceRef = {
          sequence_id: latest.sequence_ref.sequence_id,
          sequence_version: latest.sequence_ref.sequence_version,
        };
        this.selectedSelectionJobNames = [];
      } else {
        this.selectionSourceMode = 'inline';
        this.selectionSequenceRef = { sequence_id: null, sequence_version: null };
        this.selectedSelectionJobNames = (latest.job_sequence || []).map(
          s => (typeof s === 'string' ? s : s.job_name)
        );
      }
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
      };
      if (this.selectionSourceMode === 'sequence') {
        body.sequence_ref = {
          sequence_id: Number(this.selectionSequenceRef.sequence_id),
          sequence_version: this.selectionSequenceRef.sequence_version
            ? Number(this.selectionSequenceRef.sequence_version)
            : null,
        };
      } else {
        body.job_sequence = this.selectedSelectionJobNames;
        body.run_settings = { use_live_connections: Boolean(m.use_live_connections) };
        body.config_id = m.config_id || null;
      }
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
      this.selectionCompareJobName = '';
      this.selectionCompareType = 'mismatch_diff';
      try {
        this.selectionRuns = await api('GET', `/api/selections/${sel.id}/runs`);
      } catch (e) {
        this.selectionRuns = [];
        this.toast('error', 'Could not load run history', e.message);
      }
      this.showSelectionRunsModal = true;
    },

    openCiIntegrationModal(sel) {
      const yaml = [
        `atom-job-selection:`,
        `  stage: test`,
        `  script:`,
        `    - ./scripts/ci/run-atom-selection.sh ${sel.id}`,
        `  rules:`,
        `    - if: '$CI_COMMIT_BRANCH == "main"'`,
      ].join('\n');
      this.ciIntegrationModal = {
        selectionId: sel.id,
        selectionName: sel.name,
        yamlSnippet: yaml,
      };
      this.showCiIntegrationModal = true;
    },

    async copyCiYamlSnippet() {
      try {
        await navigator.clipboard.writeText(this.ciIntegrationModal.yamlSnippet);
        this.toast('success', 'Copied', 'Pipeline snippet copied to clipboard');
      } catch {
        this.toast('warn', 'Copy failed', 'Select the text manually');
      }
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

    get selectionCompareJobOptions() {
      const sel = this.selectionRunsPanel;
      if (!sel || this.compareRunIds.length !== 2) return [];
      return (sel.job_sequence || [])
        .map(item => item.job_name || item)
        .filter(name => {
          const job = (this.jobs || []).find(j => j.name === name);
          return job && (job.job_type === 'bo_report' || job.job_type === 'reconciliation');
        });
    },

    compareSelectedRuns() {
      if (this.compareRunIds.length !== 2) {
        this.toast('warn', 'Select exactly two runs', 'Pick two runs to compare');
        return;
      }
      this.showSelectionRunsModal = false;
      if (this.selectionCompareType === 'mismatch_diff' || !this.selectionCompareJobName) {
        this.mismatchDiffRunIdA = this.compareRunIds[0];
        this.mismatchDiffRunIdB = this.compareRunIds[1];
        this.mismatchDiffRunLabelA = 'Run A';
        this.mismatchDiffRunLabelB = 'Run B';
        this.mismatchDiffQueryName = this.selectionCompareJobName || '';
        this.currentView = 'compare';
        this.compareSubTab = 'mmdiff';
        this.runMismatchDiff();
        return;
      }
      const job = (this.jobs || []).find(j => j.name === this.selectionCompareJobName);
      if (!job) return;
      this.openCompareForJob(job, { runIdA: this.compareRunIds[0], runIdB: this.compareRunIds[1] });
    },

    async loadJobRuns(jobName) {
      try {
        return await api('GET', `/api/jobs/${encodeURIComponent(jobName)}/runs`);
      } catch (e) {
        this.toast('error', 'Could not load run history', e.message);
        return [];
      }
    },

    // Determine which Compare sub-tab a job's own compare type maps to.
    // Only bo_report and reconciliation (incl. multi_file source_mode) jobs
    // have a compare type at all -- callers must gate visibility on this too.
    _compareSubTabForJob(job) {
      if (job.job_type === 'bo_report') return 'bo';
      if (job.job_type === 'reconciliation' && job.params?.source_mode === 'multi_file') return 'multi_file';
      if (job.job_type === 'reconciliation') return 'recon';
      return null;
    },

    // Shared prefill + navigate used by the Job Catalog Compare button, the
    // Job modal's inline Test Compare section, and the Job Selections
    // History bridge. `opts.runIdA`/`opts.runIdB` (run-vs-run) and
    // `opts.navigate` (false keeps the caller on the current view, for the
    // Job modal's inline run) are optional.
    openCompareForJob(job, opts = {}) {
      const subTab = this._compareSubTabForJob(job);
      if (!subTab) {
        this.toast('warn', 'No compare type for this job', `job_type '${job.job_type}' has no compare equivalent`);
        return null;
      }
      if (subTab === 'bo') {
        this.boSourceAType = opts.runIdA ? 'run' : 'live';
        this.boSourceA = {
          ...this.boSourceA,
          runId: opts.runIdA || '', jobName: job.name,
          docId: job.params?.report_id || '', reportId: job.params?.bo_report_id || '',
          configId: this.boSourceA.configId,
        };
        this.boSourceBType = opts.runIdB ? 'run' : 'upload';
        if (opts.runIdB) {
          this.boSourceB = { ...this.boSourceB, runId: opts.runIdB, jobName: job.name };
        } else {
          // No run reference for Source B -- don't leave a prior session's
          // Source B (or its exclude-columns filter) attached to this job.
          this.boSourceB = { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source B', endpointName: '', parameters: [], runId: '', jobName: '' };
          this.boExcludeColumns = '';
        }
        this.boKeyColumns = (job.key_columns || []).join(', ');
      } else if (subTab === 'multi_file') {
        this.mfCompareKeyColumns = (job.key_columns || []).join(', ');
        this.mfCompareSourceMode = opts.runIdA ? 'run' : 'files';
        this.mfCompareRunId = opts.runIdA || '';
        this.mfCompareJobName = opts.runIdA ? job.name : '';
      } else if (subTab === 'recon') {
        this.reconMode = 'file';
        this.fileSourceAType = opts.runIdA ? 'run' : 'path';
        if (opts.runIdA) {
          this.fileRunIdA = opts.runIdA;
        } else {
          // No run reference for Source A -- clear any prior session's file
          // paths/uploads so this job isn't compared against stale data.
          this.filePathA = ''; this.fileB64A = ''; this.fileNameA = '';
        }
        this.fileSourceBType = opts.runIdB ? 'run' : 'path';
        if (opts.runIdB) {
          this.fileRunIdB = opts.runIdB;
        } else {
          this.filePathB = ''; this.fileB64B = ''; this.fileNameB = '';
        }
        if (!opts.runIdA && !opts.runIdB) this.fileCompareExcludeColumns = '';
        this.fileCompareKeyColumns = (job.key_columns || []).join(', ');
      }
      if (opts.navigate !== false) {
        this.currentView = 'compare';
        this.compareSubTab = subTab;
      }
      return subTab;
    },

    // Test Compare inside the Job modal: builds a job-shaped object from the
    // in-progress (possibly unsaved) form state and drives the same Compare
    // state/methods openCompareForJob uses, without navigating away.
    async runInlineJobCompare() {
      const m = this.jobModal;
      const job = {
        name: m.name, job_type: m.job_type,
        key_columns: (m.key_columns_raw || '').split(',').map(s => s.trim()).filter(Boolean),
        // report_id/bo_report_id are swapped relative to what they hold here:
        // job.params.report_id is the BO *document* id and job.params.bo_report_id
        // is the *page* id -- matches the existing job param convention read
        // elsewhere in openCompareForJob, just easy to misread in isolation.
        params: m.job_type === 'bo_report'
          ? { report_id: m.bo_report_id, bo_report_id: m.bo_page_id }
          : { source_mode: m.source_mode },
      };
      const subTab = this.openCompareForJob(job, { navigate: false });
      if (subTab === 'bo') await this.runBOComparison();
      else if (subTab === 'multi_file') await this.runMultiFileCompare();
      else if (subTab === 'recon') await this.runFileCompare();
    },

    // ===========================================================
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

    get jobCatalogCountLabel() {
      const total = (this.jobs || []).length;
      const shown = this.filteredJobList.length;
      if (shown === total) return total + ' jobs';
      return shown + ' of ' + total + ' jobs';
    },

    // ===========================================================
    // ===========================================================
    getJobLastStatus(job) {
      const status = job?.last_run_status || job?.last_status || null;
      return status ? String(status).toLowerCase() : null;
    },

    // ===========================================================
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
    // ===========================================================
    savedConfigDisplay(config) {
      if (!config) return '';
      return `${config.name} (${config.env_name})`;
    },

    // ===========================================================
    // ===========================================================
    // Note: jobModalTab is reset in openNewJobModal/openEditJobModal by calling _resetJobModalTab()
    _resetJobModalTab() {
      this.jobModalTab = 'basic';
    },

    // ===========================================================
    // ===========================================================
    validateJobModal() {
      const m = this.jobModal || {};
      const v = { sql: '', keyColumns: '', dependencies: '' };

      // SQL validation: only for SQL-backed reconciliation jobs
      if (m.job_type === 'reconciliation' && m.source_mode !== 'files') {
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
        source_mode: m.source_mode,
        source_file_path: m.source_file_path,
        target_file_path: m.target_file_path,
        source_file_label: m.source_file_label,
        target_file_label: m.target_file_label,
        key_columns_raw: m.key_columns_raw,
        tags_raw: m.tags_raw,
        enabled: m.enabled,
        depends_on_raw: m.depends_on_raw,
        rules: (m.rules || []).map(r => ({ ...r })),
        bo_report_id: m.bo_report_id,
        bo_page_id: m.bo_page_id,
        bo_format: m.bo_format,
        bo_parameters: (m.bo_parameters || []).map(p => ({ ...p })),
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
      const byName = new Map((this.jobs || []).map(job => [job.name, job]));
      let total = 0;
      (this.selectedJobs || []).forEach(name => {
        const job = byName.get(name);
        total += Number(job?.estimated_duration_seconds || job?.avg_duration_seconds || 0) || 0;
      });
      if (total === 0) return '';
      const m = Math.floor(total / 60);
      const s = Math.round(total % 60);
      return m > 0 ? `~${m}m ${s}s` : `~${s}s`;
    },
    };
  };
})(window);
