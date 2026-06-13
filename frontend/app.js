const API = 'http://localhost:8000';  // backend base URL

async function api(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(API + path, opts);
  if (resp.status === 204) return null;
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || resp.statusText);
  }
  return resp.json();
}

function app() {
  return {
    // Navigation
    currentView: 'config',
    tabs: [
      { id: 'config', label: '⚙ Config' },
      { id: 'jobs',   label: '▶ Launch' },
      { id: 'monitor',label: '📡 Monitor' },
      { id: 'history',label: '📋 History' },
    ],
    apiStatus: '',

    // Config view
    configs: [],
    showConfigModal: false,
    configModal: {},

    // Jobs view
    jobs: [],
    selectedJobs: [],
    selectAll: false,
    launchSettings: { source_env: 'dev', target_env: 'prod', config_id: '' },
    isLaunching: false,
    launchToast: false,

    // Monitor view
    activeRuns: [],
    pollTimer: null,

    // History view
    runs: [],
    selectedRun: null,
    chartInstance: null,

    async init() {
      await this.loadConfigs();
      await this.loadJobs();
      await this.loadRuns();
      this.startPolling();
      try {
        await api('GET', '/api/health');
        this.apiStatus = '● Connected';
      } catch {
        this.apiStatus = '● Offline';
      }
    },

    // ---- Config ----
    async loadConfigs() {
      try { this.configs = await api('GET', '/api/configs'); } catch {}
    },
    openNewConfigModal() {
      this.configModal = {
        id: null, name: '', env_name: 'dev',
        db_host: '', db_port: 1433, db_name: '', db_user: '',
        timeout: 30, retries: 3, max_workers: 4, float_tolerance: '1e-9',
        fields: [],
      };
      this.showConfigModal = true;
    },
    editConfig(cfg) {
      const d = cfg.config_data || {};
      this.configModal = {
        id: cfg.id, name: cfg.name, env_name: cfg.env_name,
        db_host: d.db_host || '', db_port: d.db_port || 1433,
        db_name: d.db_name || '', db_user: d.db_user || '',
        timeout: d.timeout || 30, retries: d.retries || 3,
        max_workers: d.max_workers || 4, float_tolerance: d.float_tolerance || '1e-9',
        fields: Object.entries(d)
          .filter(([k]) => !['db_host','db_port','db_name','db_user','timeout','retries','max_workers','float_tolerance'].includes(k))
          .map(([key, value]) => ({ key, value })),
      };
      this.showConfigModal = true;
    },
    addConfigField() { this.configModal.fields.push({ key: '', value: '' }); },
    async saveConfig() {
      const m = this.configModal;
      const config_data = {
        db_host: m.db_host, db_port: Number(m.db_port),
        db_name: m.db_name, db_user: m.db_user,
        timeout: Number(m.timeout), retries: Number(m.retries),
        max_workers: Number(m.max_workers), float_tolerance: m.float_tolerance,
        ...Object.fromEntries(m.fields.filter(f => f.key).map(f => [f.key, f.value])),
      };
      try {
        if (m.id) {
          await api('PUT', `/api/configs/${m.id}`, { config_data, name: m.name, env_name: m.env_name });
        } else {
          await api('POST', '/api/configs', { name: m.name, env_name: m.env_name, config_data });
        }
        await this.loadConfigs();
        this.showConfigModal = false;
      } catch (e) { alert('Error: ' + e.message); }
    },
    async deleteConfig(id) {
      if (!confirm('Delete this config?')) return;
      await api('DELETE', `/api/configs/${id}`);
      await this.loadConfigs();
    },

    // ---- Jobs ----
    async loadJobs() {
      try { this.jobs = await api('GET', '/api/jobs'); } catch {}
    },
    toggleSelectAll() {
      this.selectedJobs = this.selectAll ? this.jobs.map(j => j.name) : [];
    },
    async runTests() {
      if (!this.selectedJobs.length) return;
      this.isLaunching = true;
      try {
        const cfg = this.launchSettings.config_id
          ? this.configs.find(c => c.id == this.launchSettings.config_id)
          : null;
        const run = await api('POST', '/api/runs', {
          source_env: this.launchSettings.source_env,
          target_env: this.launchSettings.target_env,
          job_names: [...this.selectedJobs],
          config_data: cfg ? cfg.config_data : {},
        });
        this.activeRuns.unshift(run);
        this.launchToast = true;
        setTimeout(() => { this.launchToast = false; }, 4000);
        this.selectedJobs = [];
        this.selectAll = false;
      } catch (e) { alert('Launch failed: ' + e.message); }
      finally { this.isLaunching = false; }
    },

    // ---- Monitor ----
    startPolling() {
      this.pollTimer = setInterval(() => this.pollActiveRuns(), 5000);
    },
    async pollActiveRuns() {
      if (this.activeRuns.length === 0 && this.currentView !== 'monitor') return;
      await this.loadRuns();
      // refresh active run statuses
      const active = this.runs.filter(r => ['PENDING','RUNNING'].includes(r.status));
      for (const run of active) {
        try {
          const status = await api('GET', `/api/runs/${run.run_id}/status`);
          const idx = this.activeRuns.findIndex(r => r.run_id === run.run_id);
          if (idx >= 0) Object.assign(this.activeRuns[idx], status);
          else this.activeRuns.unshift(status);
        } catch {}
      }
    },
    runProgress(run) {
      if (['PASSED','FAILED','SLOW','ERROR','COMPLETED'].includes(run.status)) return 100;
      if (run.status === 'PENDING') return 5;
      const done = (run.passed || 0) + (run.failed || 0) + (run.slow || 0) + (run.error || 0);
      return run.total_tests > 0 ? Math.round((done / run.total_tests) * 100) : 10;
    },

    // ---- History ----
    async loadRuns() {
      try { this.runs = await api('GET', '/api/runs'); } catch {}
    },
    async viewRunDetail(runId) {
      try {
        this.selectedRun = await api('GET', `/api/runs/${runId}`);
        this.$nextTick(() => this.renderChart());
      } catch (e) { alert('Error loading run: ' + e.message); }
    },
    renderChart() {
      const canvas = document.getElementById('runChart');
      if (!canvas) return;
      if (this.chartInstance) this.chartInstance.destroy();
      const r = this.selectedRun;
      this.chartInstance = new Chart(canvas, {
        type: 'doughnut',
        data: {
          labels: ['Passed', 'Failed', 'Slow', 'Error'],
          datasets: [{
            data: [r.passed || 0, r.failed || 0, r.slow || 0, r.error || 0],
            backgroundColor: ['#22c55e', '#ef4444', '#f59e0b', '#6b7280'],
            borderWidth: 0,
          }],
        },
        options: {
          cutout: '70%',
          plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } },
        },
      });
    },

    // ---- Utilities ----
    fmtDate(iso) {
      if (!iso) return '—';
      const d = new Date(iso);
      return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    },
    statusBadgeClass(status) {
      const map = {
        PASSED:    'badge badge-green',
        FAILED:    'badge badge-red',
        SLOW:      'badge badge-amber',
        RUNNING:   'badge badge-blue',
        PENDING:   'badge badge-gray',
        ERROR:     'badge badge-red',
        COMPLETED: 'badge badge-green',
      };
      return map[status] || 'badge badge-gray';
    },
  };
}
