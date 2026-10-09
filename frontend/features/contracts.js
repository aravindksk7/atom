(function (global) {
  'use strict';
  // Contracts feature slice (Contracts tab: data contract CRUD, breach
  // tracking, version bumping). Merged into the Alpine component via
  // the FEATURE_SLICES reduce in app.js.
  global.ETL_FEATURE_CONTRACTS = function () {
    return {
      // ===== STATE (extracted from app.js) =====
    // -----------------------------------------------------------
    // Contracts
    // -----------------------------------------------------------
    contracts: [],
    contractsLoading: false,
    selectedContract: null,
    contractStatusMap: {},          // name → { status, open_breach }
    contractBreachHistory: [],
    contractVersionHistory: [],
    contractBreachLoading: false,
    // NOTE: app-help.js's global Escape-key handler reads this flag directly to
    // close the modal — don't rename without updating app-help.js too.
    showContractModal: false,
    contractModal: { name: '', source_job: '', owner: '', sla_hours: 4, consumers: [], breach_severity: 'error', version: '1.0' },
    contractModalEditing: false,
    contractConsumerDraft: '',
    showResolveModal: false,
    resolveTarget: null,            // breach id, or 'all'
    resolveNote: '',
    resolveBy: '',
    resolveLoading: false,
    showContractImport: false,
    contractImportText: '',
    contractImportPreview: null,    // { dry_run, applied, rows: [{name, action, reason?, warning?}] }
    contractImportError: '',
    contractImportLoading: false,
    contractBumpType: 'minor',
    showContractExamples: false,
    contractExamplesAutoApplied: false,
    contractExamples: window.CONTRACT_EXAMPLES || [],
    expandedExampleId: null,
    contractBumpNote: '',
    contractBumpLoading: false,
    contractTestingLoading: false,
    contractTestResult: null,
    contractTestCategoryFilter: 'all',

      // ===== METHODS (extracted from app.js) =====
    async loadContracts() {
      this.contractsLoading = true;
      try {
        this.contracts = await api('GET', '/api/contracts');
        this._ensureJobsLoaded();
        // Expand the example library only on the first load of the tab, and only
        // when there is nothing else to show. After that the toggle is the user's:
        // creating the first contract or deleting the last must not move it.
        if (!this.contractExamplesAutoApplied) {
          this.contractExamplesAutoApplied = true;
          this.showContractExamples = this.contracts.length === 0;
        }
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
      this.contractTestResult = null;
      this.contractTestCategoryFilter = 'all';
      try { this.contractBreachHistory = await api('GET', `/api/contracts/${encodeURIComponent(contract.name)}/breaches`); } catch {}
      this.contractBreachLoading = false;
      try { this.contractVersionHistory = await api('GET', `/api/contracts/${encodeURIComponent(contract.name)}/versions`); } catch {}
    },

    async _ensureJobsLoaded() {
      if (!Array.isArray(this.jobs) || this.jobs.length === 0) await this.loadJobs();
    },

    contractSourceJobKnown(name) {
      return Array.isArray(this.jobs) && this.jobs.some(j => j.name === name);
    },

    contractJobMissing(name) {
      // Only flag once a job list has actually loaded; an empty/failed load
      // must not paint every contract as broken.
      return Array.isArray(this.jobs) && this.jobs.length > 0 && !this.contractSourceJobKnown(name);
    },

    addContractConsumer() {
      for (const part of this.contractConsumerDraft.split(',')) {
        const v = part.trim();
        if (v && !this.contractModal.consumers.includes(v)) this.contractModal.consumers.push(v);
      }
      this.contractConsumerDraft = '';
    },

    removeContractConsumer(idx) {
      this.contractModal.consumers.splice(idx, 1);
    },

    onContractConsumerKeydown(e) {
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        this.addContractConsumer();
      } else if (e.key === 'Backspace' && this.contractConsumerDraft === '' && this.contractModal.consumers.length) {
        this.contractModal.consumers.pop();
      }
    },

    contractConsumerSuggestions() {
      const all = new Set();
      for (const c of this.contracts) for (const name of (c.consumers || [])) all.add(name);
      return [...all].filter(n => !this.contractModal.consumers.includes(n)).sort();
    },

    contractHasOpenBreach() {
      return this.contractBreachHistory.some(b => !b.resolved_at);
    },

    openResolveBreach(target) {
      this.resolveTarget = target;
      this.resolveNote = '';
      try { this.resolveBy = localStorage.getItem('contract_resolved_by') || ''; } catch { this.resolveBy = ''; }
      this.showResolveModal = true;
    },

    async confirmResolveBreach() {
      if (!this.selectedContract || !this.resolveNote.trim()) return;
      const name = this.selectedContract.name;
      const base = `/api/contracts/${encodeURIComponent(name)}/breaches`;
      const path = this.resolveTarget === 'all' ? `${base}/resolve-all` : `${base}/${this.resolveTarget}/resolve`;
      this.resolveLoading = true;
      try {
        await api('POST', path, { note: this.resolveNote.trim(), resolved_by: this.resolveBy.trim() || null });
        try { localStorage.setItem('contract_resolved_by', this.resolveBy.trim()); } catch {}
        this.showResolveModal = false;
        try {
          this.contractStatusMap[name] = await api('GET', `/api/contracts/${encodeURIComponent(name)}/status`);
        } catch {}
        await this.selectContract(this.selectedContract);
      } catch (e) { alert('Resolve failed: ' + (e.message || e)); }
      this.resolveLoading = false;
    },

    async openNewContractModal() {
      this.contractModal = { name: '', source_job: '', owner: '', sla_hours: 4, consumers: [], breach_severity: 'error', version: '1.0' };
      this.contractModalEditing = false;
      this.contractConsumerDraft = '';
      this.showContractModal = true;
      await this._ensureJobsLoaded();
    },

    async useContractExample(ex) {
      const c = ex.contract || {};
      this.contractModal = {
        name: c.name || '',
        source_job: c.source_job || '',
        owner: c.owner || '',
        sla_hours: c.sla_hours != null ? c.sla_hours : 4,
        consumers: [...new Set(Array.isArray(c.consumers) ? c.consumers
          : String(c.consumers_raw || '').split(',').map(s => s.trim()).filter(Boolean))],
        breach_severity: c.breach_severity || 'error',
        version: c.version || '1.0',
      };
      this.contractModalEditing = false;
      this.contractConsumerDraft = '';
      this.showContractExamples = false;
      this.expandedExampleId = null;
      this.showContractModal = true;
      await this._ensureJobsLoaded();
    },

    async openEditContractModal(contract) {
      this.contractModal = {
        name: contract.name,
        source_job: contract.source_job,
        owner: contract.owner,
        sla_hours: contract.sla_hours,
        consumers: [...new Set(contract.consumers || [])],
        breach_severity: contract.breach_severity,
        version: contract.version,
      };
      this.contractModalEditing = true;
      this.contractConsumerDraft = '';
      this.showContractModal = true;
      await this._ensureJobsLoaded();
    },

    async saveContract() {
      if (this.contractConsumerDraft.trim()) this.addContractConsumer();
      const consumers = [...this.contractModal.consumers];
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

    async runContractTest(contractName) {
      this.contractTestingLoading = true;
      try {
        this.contractTestResult = await api('POST', `/api/contracts/${encodeURIComponent(contractName)}/test`);
      } catch (e) {
        alert('Contract test failed: ' + (e.message || e));
        this.contractTestResult = null;
      }
      this.contractTestingLoading = false;
    },

    filteredContractChecks() {
      if (!this.contractTestResult || !this.contractTestResult.checks) return [];
      if (this.contractTestCategoryFilter === 'all') return this.contractTestResult.checks;
      return this.contractTestResult.checks.filter(c => c.category === this.contractTestCategoryFilter);
    },

    _downloadBlob(blob, filename) {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    },

    exportContractTestReport() {
      if (!this.contractTestResult) return;
      const blob = new Blob([JSON.stringify(this.contractTestResult, null, 2)], { type: 'application/json' });
      this._downloadBlob(blob, `contract-test-${this.contractTestResult.contract}-${Date.now()}.json`);
    },

    async exportContractsYaml(name) {
      const query = name ? `?name=${encodeURIComponent(name)}` : '';
      try {
        const { blob } = await apiBlob(`/api/contracts/export${query}`);
        this._downloadBlob(blob, name ? `${name}.contract.yaml` : 'contracts.yaml');
      } catch (e) { alert('Export failed: ' + (e.message || e)); }
    },

    openContractImport() {
      this.contractImportText = '';
      this.contractImportPreview = null;
      this.contractImportError = '';
      this.showContractImport = true;
    },

    async onContractImportFile(event) {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      try {
        this.contractImportText = await file.text();
        this.contractImportPreview = null;
        this.contractImportError = '';
      } catch (e) {
        this.contractImportError = 'Could not read file: ' + (e.message || e);
      }
      event.target.value = '';
    },

    async _postContractImport(dryRun) {
      this.contractImportLoading = true;
      this.contractImportError = '';
      try {
        return await api('POST', '/api/contracts/import', { yaml: this.contractImportText, dry_run: dryRun });
      } catch (e) {
        this.contractImportError = e.message || String(e);
        return null;
      } finally {
        this.contractImportLoading = false;
      }
    },

    async previewContractImport() {
      this.contractImportPreview = await this._postContractImport(true);
    },

    contractImportCanApply() {
      const rows = (this.contractImportPreview && this.contractImportPreview.rows) || [];
      return !rows.some(r => r.action === 'error') && rows.some(r => r.action === 'create' || r.action === 'update');
    },

    async applyContractImport() {
      const result = await this._postContractImport(false);
      if (!result) return;
      this.contractImportPreview = result;
      if (result.applied) {
        this.showContractImport = false;
        await this.loadContracts();
        if (this.selectedContract) {
          const updated = this.contracts.find(c => c.name === this.selectedContract.name);
          if (updated) this.selectedContract = updated;
        }
      } else if (!(result.rows || []).some(r => r.action === 'error')) {
        this.contractImportError = 'Nothing to apply.';
      }
    },

    // NOTE: dead code — not called from index.html markup (contract status
    // badges use an inline :class object instead) or anywhere else. Moved
    // as-is per pure code-motion instructions.
    contractStatusBadgeClass(name) {
      const s = (this.contractStatusMap[name] || {}).status;
      if (s === 'OK') return 'badge-ok';
      if (s === 'BREACHED') return 'badge-breached';
      if (s === 'OVERDUE') return 'badge-overdue';
      return 'badge-unknown';
    },
    };
  };
})(window);
