(function (global) {
  'use strict';
  // Monitor feature slice (Monitor tab: active runs, SSE progress
  // streaming, run cancellation). Merged into the Alpine component via
  // the FEATURE_SLICES reduce in app.js.
  global.ETL_FEATURE_MONITOR = function () {
    return {
      // ===== STATE (extracted from app.js) =====
    // -----------------------------------------------------------
    // Monitor
    // -----------------------------------------------------------
    activeRuns: [],
    pollTimer: null,
    runStreams: {},
    cancellingRuns: {},
    runStepsCache: {},   // { run_id: RunStep[] }
    stepReleaseModal: { show: false, runId: '', stepIndex: 0, releasedBy: '', note: '', action: 'approve' },
      // ===== METHODS (extracted from app.js) =====
    // ===========================================================
    // MONITOR
    // ===========================================================
    startPolling() {
      this.pollTimer = setInterval(() => this.pollActiveRuns(), 5000);
    },

    isTerminalStatus(status) {
      return isTerminalStatusValue(status);
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

    async cancelRun(runId) {
      if (!runId || this.cancellingRuns[runId]) return;
      this.cancellingRuns = { ...this.cancellingRuns, [runId]: true };
      try {
        await api('POST', `/api/runs/${runId}/cancel`);
        const idx = this.activeRuns.findIndex(r => r.run_id === runId);
        if (idx >= 0) this.activeRuns[idx].cancel_requested = true;
        this.toast('success', 'Cancel requested', `Run ${runId.substring(0,8)} will stop after current step`);
        await this.pollActiveRuns();
      } catch(e) {
        this.toast('error', 'Cancel failed', e.message);
      } finally {
        const next = { ...this.cancellingRuns };
        delete next[runId];
        this.cancellingRuns = next;
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

    };
  };
})(window);
