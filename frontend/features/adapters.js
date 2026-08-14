(function (global) {
  'use strict';
  // Adapters feature slice (Adapters tab: SAP BO document/report
  // browsing, Automic job browsing/import). Merged into the Alpine
  // component via the FEATURE_SLICES reduce in app.js.
  global.ETL_FEATURE_ADAPTERS = function () {
    return {
      // ===== STATE (extracted from app.js) =====
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
    boReportParams: {},    // doc.id → [{id,name,type,mandatory}] (report prompts)
    boParamValues: {},     // doc.id → { param_id → value }
    boFilterQuery: '',
    boRanOnDate: '',
    boRanOnDocIds: null,   // Set<string> | null — null means no date filter active
    boRanOnSupported: true,
    // NOTE: app-help.js's global Escape-key handler reads this flag directly to
    // close the modal — don't rename without updating app-help.js too.
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

      // ===== METHODS (extracted from app.js) =====
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
      this.boReportParams = {};
      this.boParamValues = {};
      this.boFilterQuery = '';
      this.boRanOnDate = '';
      this.boRanOnDocIds = null;
      this.boRanOnSupported = true;
      try {
        this.boDocs = await api('GET', `/api/adapters/sap-bo/documents?config_id=${this.boConfigId}`);
        this.toast('success', `${this.boDocs.length} documents loaded`);
      } catch (e) {
        this.toast('error', 'Load failed', e.message);
      } finally {
        this.boLoading = false;
      }
    },

    async loadBORanOnDocIds() {
      if (!this.boRanOnDate) {
        this.boRanOnDocIds = null;
        this.boRanOnSupported = true;
        return;
      }
      if (!this.boConfigId) return;
      try {
        const result = await api('GET',
          `/api/adapters/sap-bo/documents/ran-on?config_id=${this.boConfigId}&run_date=${this.boRanOnDate}`);
        this.boRanOnSupported = result.supported;
        this.boRanOnDocIds = result.supported ? new Set(result.document_ids) : null;
      } catch (e) {
        this.toast('error', 'Run-date filter failed', e.message);
        this.boRanOnDocIds = null;
        this.boRanOnSupported = true;
      }
    },

    // Search-within for the (potentially large) document/report tree: pure
    // client-side filter over what's already loaded. Reports are fetched
    // lazily per-document (toggleBODoc), so a query only matches reports for
    // documents that have already been expanded at least once.
    _boTextMatches(text, query) {
      return String(text || '').toLowerCase().includes(query);
    },

    boDocMatchesQuery(doc) {
      const q = this.boFilterQuery.trim().toLowerCase();
      if (!q) return true;
      return this._boTextMatches(doc.name, q) || this._boTextMatches(doc.folder, q) || this._boTextMatches(doc.id, q);
    },

    boDocHasMatchingReport(doc) {
      const q = this.boFilterQuery.trim().toLowerCase();
      if (!q) return false;
      const reports = this.boReports[doc.id];
      if (!reports) return false;
      return reports.some(r => this._boTextMatches(r.name, q) || this._boTextMatches(r.id, q));
    },

    get filteredBODocs() {
      const dateFiltered = this.boRanOnDocIds
        ? this.boDocs.filter(doc => this.boRanOnDocIds.has(doc.id))
        : this.boDocs;
      if (!this.boFilterQuery.trim()) return dateFiltered;
      return dateFiltered.filter(doc => this.boDocMatchesQuery(doc) || this.boDocHasMatchingReport(doc));
    },

    boFilteredReports(doc) {
      const all = this.boReports[doc.id] || [];
      const q = this.boFilterQuery.trim().toLowerCase();
      // Once the user matched this document by its own name/folder/id, show
      // all its reports so they can keep browsing. Otherwise they must have
      // matched via a report name, so narrow down to just those reports.
      if (!q || this.boDocMatchesQuery(doc)) return all;
      return all.filter(r => this._boTextMatches(r.name, q) || this._boTextMatches(r.id, q));
    },

    async toggleBODoc(doc) {
      const idx = this.expandedBODocs.indexOf(doc.id);
      if (idx >= 0) {
        this.expandedBODocs.splice(idx, 1);
        return;
      }
      this.expandedBODocs.push(doc.id);
      // Discover the report's prompts in parallel with the report list so the
      // download control knows whether to POST parameters or use the plain GET.
      this.loadBOReportParams(doc);
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

    // Fetch (and cache) the prompt/parameter definitions for a document's
    // report. Returns the cached list on repeat calls. Uses the spread-assign
    // idiom (like boReports) so Alpine picks up the change reactively.
    async loadBOReportParams(doc) {
      if (this.boReportParams[doc.id]) return this.boReportParams[doc.id];
      try {
        const params = await api('GET',
          `/api/adapters/sap-bo/documents/${doc.id}/parameters?config_id=${this.boConfigId}`);
        this.boReportParams = { ...this.boReportParams, [doc.id]: params };
        if (!this.boParamValues[doc.id]) {
          this.boParamValues = {
            ...this.boParamValues,
            [doc.id]: this.defaultBOParamValues(params),
          };
        }
        return params;
      } catch (e) {
        this.toast('error', 'Could not load report parameters', e.message);
        this.boReportParams = { ...this.boReportParams, [doc.id]: [] };
        return [];
      }
    },

    // Seed each prompt with an editable default: the BO-supplied current
    // value when present, otherwise today's date for DateTime prompts (so the
    // most common "run for a date" case is pre-filled, ISO YYYY-MM-DD as the
    // date picker requires) and blank for everything else.
    defaultBOParamValues(params) {
      const d = new Date();
      const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      const seeded = {};
      for (const p of params) {
        seeded[p.id] = p.default || (p.type === 'DateTime' ? today : '');
      }
      return seeded;
    },

    // Both download branches end the same way: hand the blob to the browser,
    // then report the server-side copy. A failed copy still gets its own error
    // toast on top of the success one — the download really did succeed and
    // the archive really did fail, and saying only one of those would be a lie.
    finishBODownload(blob, filename, savedPath, saveError) {
      triggerDownload(blob, filename);
      this.toast('success', 'Download started',
        savedPath ? `Also saved to ${savedPath}` : '');
      if (saveError) {
        this.toast('error', 'Server copy failed', saveError);
      }
    },

    async downloadBOReport(docId, reportId, format) {
      // An omitted reportId is SAP's whole-document export: every tab in one
      // file, served by the routes without the /reports/{id} segment. An empty
      // segment would name the tab called '' instead.
      const scope = reportId ? `/reports/${reportId}` : '';
      const fallbackName = reportId
        ? `report_${docId}_${reportId}.${format}`
        : `report_${docId}.${format}`;
      // Ensure prompt definitions are known before deciding which path to use;
      // returns the cached list when already loaded (e.g. on doc expand).
      const params = await this.loadBOReportParams({ id: docId });
      // No prompts → keep the existing authenticated GET blob download.
      if (!params.length) {
        try {
          const { blob, disposition, savedPath, saveError } = await apiBlob(
            `/api/adapters/sap-bo/documents/${docId}${scope}/download?config_id=${this.boConfigId}&format=${format}`
          );
          const match = disposition.match(/filename="?([^"]+)"?/);
          this.finishBODownload(blob, match ? match[1] : fallbackName, savedPath, saveError);
        } catch (e) {
          this.toast('error', 'Download failed', e.message);
        }
        return;
      }
      // Has prompts → POST the collected values to the parameterized endpoint.
      // Auth is replicated from the api()/apiBlob() helpers (Bearer token).
      const values = this.boParamValues[docId] || {};
      // Block on unanswered mandatory prompts here rather than let BO reject
      // the export (which surfaces as a generic 502).
      const missing = params.filter(p => p.mandatory && !String(values[p.id] || '').trim());
      if (missing.length) {
        this.toast('error', 'Missing required prompts',
          missing.map(p => p.name || ('Prompt ' + p.id)).join(', '));
        return;
      }
      const parameters = params.map(p => ({ id: p.id, type: p.type, value: values[p.id] || '' }));
      try {
        const token = normalizeToken(sessionStorage.getItem('etl_token'));
        const headers = { 'Content-Type': 'application/json' };
        if (token) headers['Authorization'] = 'Bearer ' + token;
        const resp = await fetch(
          API + `/api/adapters/sap-bo/documents/${docId}${scope}/download?config_id=${this.boConfigId}`,
          { method: 'POST', headers, body: JSON.stringify({ format, parameters }) }
        );
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: resp.statusText }));
          this.toast('error', 'Download failed', apiErrorMessage(err.detail ?? err, resp.statusText));
          return;
        }
        const disposition = resp.headers.get('content-disposition') || '';
        const match = disposition.match(/filename="?([^"]+)"?/);
        this.finishBODownload(
          await resp.blob(),
          match ? match[1] : fallbackName,
          decodeURIComponent(resp.headers.get('x-saved-path') || ''),
          decodeURIComponent(resp.headers.get('x-save-error') || ''),
        );
      } catch (e) {
        this.toast('error', 'Download failed', e.message);
      }
    },

    // `rep` omitted means the whole document — every tab as one table, the
    // same scope the "All tabs" download uses.
    openAddBOJobModal(doc, rep) {
      this.boJobForm = {
        name: (rep ? `bo_${doc.id}_${rep.id}` : `bo_${doc.id}_all`)
          .replace(/[^a-z0-9_]/gi, '_').toLowerCase(),
        title: rep ? `${doc.name} – ${rep.name}` : `${doc.name} – all tabs`,
        doc_id: doc.id,
        report_id: rep ? rep.id : '',
        key_columns_raw: 'id',
        format: 'xlsx',
      };
      this.showBOJobModal = true;
    },

    async saveBOJob() {
      try {
        // Carry over prompt answers already collected for this doc (loaded on
        // expand via loadBOReportParams) so the new job's Report Parameters
        // — including any DateTime prompt — aren't blank in the Job Launcher
        // editor until the user manually clicks "Load from report".
        const docId = this.boJobForm.doc_id;
        const docParams = this.boReportParams[docId] || [];
        const docValues = this.boParamValues[docId] || {};
        const parameters = docParams.map(p => ({
          id: p.id, type: p.type, value: String(docValues[p.id] ?? ''),
        }));
        await api('POST', '/api/adapters/jobs/from-bo-report', {
          name: this.boJobForm.name,
          title: this.boJobForm.title,
          doc_id: docId,
          report_id: this.boJobForm.report_id,
          key_columns: this.boJobForm.key_columns_raw.split(',').map(s => s.trim()).filter(Boolean),
          format: this.boJobForm.format,
          parameters,
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
      // Bailing out silently made a missing config or empty filter look
      // identical to "the scheduler has no jobs" — the button just did
      // nothing. Say which field is missing instead.
      this.browseAutomicError = '';
      if (!this.browseAutomicConfigId) {
        this.browseAutomicError = 'Select a config first.';
        return;
      }
      if (!this.browseAutomicFilter.trim()) {
        this.browseAutomicError = 'Enter a filter to search for (e.g. ETL_*).';
        return;
      }
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

    };
  };
})(window);
