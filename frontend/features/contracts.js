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
    // NOTE: dead — set nowhere, read nowhere; moved as-is (pure code motion).
    contractDetailLoading: false,
    contractStatusMap: {},          // name → { status, open_breach }
    contractBreachHistory: [],
    contractVersionHistory: [],
    // NOTE: dead — set nowhere, read nowhere; moved as-is (pure code motion).
    contractStatusLoading: false,
    contractBreachLoading: false,
    // NOTE: dead — set/cleared in selectContract() but never read in
    // index.html; moved as-is (pure code motion).
    contractVersionLoading: false,
    showContractModal: false,
    contractModal: { name: '', source_job: '', owner: '', sla_hours: 4, consumers_raw: '', breach_severity: 'error', version: '1.0' },
    contractModalEditing: false,
    contractBumpType: 'minor',
    showContractExamples: false,
    contractExamples: window.CONTRACT_EXAMPLES || [],
    expandedExampleId: null,
    contractBumpNote: '',
    contractBumpLoading: false,

      // ===== METHODS (extracted from app.js) =====
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

    useContractExample(ex) {
      const c = ex.contract || {};
      this.contractModal = {
        name: c.name || '',
        source_job: c.source_job || '',
        owner: c.owner || '',
        sla_hours: c.sla_hours != null ? c.sla_hours : 4,
        consumers_raw: c.consumers_raw || (Array.isArray(c.consumers) ? c.consumers.join(', ') : ''),
        breach_severity: c.breach_severity || 'error',
        version: c.version || '1.0',
      };
      this.contractModalEditing = false;
      this.showContractExamples = false;
      this.expandedExampleId = null;
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
