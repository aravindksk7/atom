(function (global) {
  'use strict';

  global.ETL_FEATURE_SCHEDULER_REPORTS = function () {
    return {
      schedulerReportFilters: { days: 30, job: '', status: '', exitCode: '' },
      schedulerReportSummary: null,
      schedulerReportGrid: [],
      schedulerReportTimeline: [],
      schedulerReportMetrics: null,
      schedulerReportWarnings: [],
      schedulerReportLoading: false,
      schedulerReportError: '',
      schedulerReportPollTimer: null,
      schedulerReportEditing: null,
      schedulerReportOutcomeChart: null,

      schedulerReportParams() {
        const params = new URLSearchParams();
        params.set('days', String(this.schedulerReportFilters.days || 30));
        if (this.schedulerReportFilters.job) params.set('job', this.schedulerReportFilters.job);
        if (this.schedulerReportFilters.status) params.set('status', this.schedulerReportFilters.status);
        if (this.schedulerReportFilters.exitCode !== '') params.set('exit_code', String(this.schedulerReportFilters.exitCode));
        return params.toString();
      },

      async loadSchedulerReports() {
        this.schedulerReportLoading = true;
        this.schedulerReportError = '';
        const qs = this.schedulerReportParams();
        try {
          const [summary, grid, timeline, metrics] = await Promise.all([
            api('GET', `/api/scheduler-reports/summary?${qs}`),
            api('GET', `/api/scheduler-reports/grid?${qs}`),
            api('GET', `/api/scheduler-reports/timeline?${qs}`),
            api('GET', `/api/scheduler-reports/metrics?${qs}`),
          ]);
          this.schedulerReportSummary = summary;
          this.schedulerReportGrid = grid.rows || [];
          this.schedulerReportTimeline = timeline.segments || [];
          this.schedulerReportMetrics = metrics;
          this.schedulerReportWarnings = [...(summary.warnings || []), ...(grid.warnings || []), ...(timeline.warnings || []), ...(metrics.warnings || [])]
            .filter((value, index, all) => all.indexOf(value) === index);
          this.$nextTick(() => this.renderSchedulerReportCharts());
        } catch (e) {
          this.schedulerReportError = e.message || 'Failed to load scheduler reports';
          this.toast('error', 'Scheduler reports unavailable', this.schedulerReportError);
        } finally {
          this.schedulerReportLoading = false;
        }
      },

      startSchedulerReportPolling() {
        this.stopSchedulerReportPolling();
        this.loadSchedulerReports();
        this.schedulerReportPollTimer = setInterval(() => {
          if (this.currentView === 'scheduler-reports') this.loadSchedulerReports();
        }, 15000);
      },

      stopSchedulerReportPolling() {
        if (this.schedulerReportPollTimer) clearInterval(this.schedulerReportPollTimer);
        this.schedulerReportPollTimer = null;
      },

      schedulerStatusClass(status) {
        const value = String(status || '').toUpperCase();
        if (['PASSED', 'COMPLETED', 'RUNNING'].includes(value)) return 'status-pill is-success';
        if (value === 'FAILED' || value === 'ERROR') return 'status-pill is-danger';
        if (value === 'CANCELLED' || value === 'BLOCKED' || value === 'SLOW') return 'status-pill is-warning';
        return 'status-pill';
      },

      async schedulerReportRunNow(row) {
        await api('POST', `/api/schedules/${row.schedule_id}/run-now`);
        this.toast('success', 'Schedule triggered', row.schedule_name);
        await this.loadSchedulerReports();
      },

      schedulerReportSchedulePayload(row, overrides = {}) {
        return {
          name: row.schedule_name,
          cron_expr: row.cron_expr,
          selection_id: row.selection_id,
          selection_version: row.selection_version,
          source_env: row.source_env,
          target_env: row.target_env || '',
          enabled: row.enabled,
          ...overrides,
        };
      },

      async schedulerReportToggle(row) {
        await api('PUT', `/api/schedules/${row.schedule_id}`, this.schedulerReportSchedulePayload(row, { enabled: !row.enabled }));
        this.toast('success', row.enabled ? 'Schedule disabled' : 'Schedule enabled', row.schedule_name);
        await this.loadSchedulerReports();
      },

      schedulerReportEdit(row) {
        this.schedulerReportEditing = { ...row, cron_expr: row.cron_expr || '' };
      },

      async schedulerReportSaveEdit() {
        const edit = this.schedulerReportEditing;
        if (!edit) return;
        await api('PUT', `/api/schedules/${edit.schedule_id}`, this.schedulerReportSchedulePayload(edit, {
          cron_expr: edit.cron_expr,
          enabled: edit.enabled,
        }));
        this.schedulerReportEditing = null;
        this.toast('success', 'Schedule updated', edit.schedule_name);
        await this.loadSchedulerReports();
      },

      async schedulerReportDelete(row) {
        if (!confirm(`Delete schedule ${row.schedule_name}?`)) return;
        await api('DELETE', `/api/schedules/${row.schedule_id}`);
        this.toast('success', 'Schedule deleted', row.schedule_name);
        await this.loadSchedulerReports();
      },

      schedulerReportExportUrl(format) {
        return `/api/scheduler-reports/export?format=${format}&${this.schedulerReportParams()}`;
      },

      renderSchedulerReportCharts() {
        if (!global.Chart || !this.schedulerReportMetrics) return;
        const canvas = document.getElementById('scheduler-report-outcomes-chart');
        if (!canvas) return;
        if (this.schedulerReportOutcomeChart) this.schedulerReportOutcomeChart.destroy();
        const outcomes = this.schedulerReportMetrics.outcomes || [];
        this.schedulerReportOutcomeChart = new Chart(canvas, {
          type: 'doughnut',
          data: {
            labels: outcomes.map(o => o.status),
            datasets: [{
              data: outcomes.map(o => o.count),
              backgroundColor: ['#22c55e', '#f43f5e', '#f59e0b', '#38bdf8', '#64748b'],
            }],
          },
          options: { responsive: true, plugins: { legend: { position: 'bottom' } } },
        });
      },
    };
  };
})(window);
