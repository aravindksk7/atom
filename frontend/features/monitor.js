(function (global) {
  'use strict';
  // Monitor feature slice (Monitor tab: active runs, SSE progress
  // streaming, run cancellation). Merged into the Alpine component via
  // the FEATURE_SLICES reduce in app.js.

  // Browsers allow 6 connections per host over HTTP/1.1, and a stream holds one
  // for as long as the run lasts. Stop short of that so ordinary requests still
  // get through; runs beyond the cap use the poll.
  const MAX_RUN_STREAMS = 4;
  // The server sends each second while it has timing, so a stream this quiet
  // isn't delivering, whatever the connection says. The poll covers for it.
  const STREAM_QUIET_MS = 10000;

  // Pure SSE parser. Takes what was left over from the last call and the newly
  // arrived text, and returns the complete events plus the new leftover. Chunks
  // can split anywhere, so nothing is consumed until its blank line has arrived.
  function parseSseChunk(buffer, chunk) {
    let text = buffer + chunk;
    // A trailing CR may be half of a CRLF split across chunks.
    const heldBack = text.endsWith('\r') ? '\r' : '';
    if (heldBack) text = text.slice(0, -1);

    const events = [];
    const lineEnd = /\r\n|\n|\r/g;
    let eventName = '';
    let data = [];
    let lineStart = 0;
    let consumed = 0;
    let match;
    while ((match = lineEnd.exec(text))) {
      const line = text.slice(lineStart, match.index);
      lineStart = lineEnd.lastIndex;
      if (line === '') {
        if (data.length) events.push({ event: eventName || 'message', data: data.join('\n') });
        eventName = '';
        data = [];
        consumed = lineStart;
      } else if (line[0] !== ':') {
        const colon = line.indexOf(':');
        const field = colon === -1 ? line : line.slice(0, colon);
        const raw = colon === -1 ? '' : line.slice(colon + 1);
        const value = raw[0] === ' ' ? raw.slice(1) : raw;
        if (field === 'data') data.push(value);
        else if (field === 'event') eventName = value;
      }
    }
    return { events, rest: text.slice(consumed) + heldBack };
  }

  global.ETL_MONITOR = { parseSseChunk };

  global.ETL_FEATURE_MONITOR = function () {
    // Plain closures, not component state: Alpine would otherwise track reads of
    // these from x-init and rerun the run list whenever one changes.
    const deniedStreams = new Map(); // { run_id: token the server refused }
    const stepsRefetchHeld = new Set(); // run_ids with a refetch out, or one that failed

    return {
      // ===== STATE (extracted from app.js) =====
    // -----------------------------------------------------------
    // Monitor
    // -----------------------------------------------------------
    activeRuns: [],
    pollTimer: null,
    runStreams: {},      // { run_id: { close(), lastEventAt } }
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

    // EventSource can't send a bearer header, so every stream would 401 with auth
    // on. Putting the token in the URL would send it to access logs, so this reads
    // the stream with fetch instead.
    //
    // A stream that fails or ends without `done` drops its entry and the next poll
    // reopens it. A 401 or 403 isn't retried for that run until the token changes
    // or the page reloads; the poll keeps that run up to date meanwhile.
    startRunStream(run) {
      const runId = run?.run_id;
      if (!runId || this.runStreams[runId] || this.isTerminalStatus(run.status)) return;
      if (Object.keys(this.runStreams).length >= MAX_RUN_STREAMS) return;
      const token = normalizeToken(sessionStorage.getItem('etl_token'));
      if (deniedStreams.get(runId) === token) return;

      const controller = new AbortController();
      const stream = { close: () => controller.abort(), lastEventAt: 0 };
      this.runStreams[runId] = stream;
      this.readRunStream(runId, stream, token, controller.signal);
    },

    async readRunStream(runId, stream, token, signal) {
      try {
        const headers = { Accept: 'text/event-stream' };
        if (token) headers['Authorization'] = 'Bearer ' + token;
        const resp = await fetch(API + `/api/runs/${runId}/stream`, { headers, signal });
        if (resp.status === 401 || resp.status === 403) {
          deniedStreams.set(runId, token);
          return;
        }
        if (!resp.ok || !resp.body) return;

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) return;
          const parsed = parseSseChunk(buffer, decoder.decode(value, { stream: true }));
          buffer = parsed.rest;
          for (const message of parsed.events) {
            stream.lastEventAt = Date.now();
            if (message.event === 'progress') {
              this.handleRunProgress(JSON.parse(message.data));
            } else if (message.event === 'done') {
              await this.handleRunDone(JSON.parse(message.data));
              return;
            } else if (message.event === 'error') {
              return;
            }
          }
        }
      } catch {
        // Closed by us, or the connection broke. Either way the poll takes over.
      } finally {
        // Compare the close function, not the object: Alpine hands back a proxy of
        // it. A closed stream must not remove the one that replaced it.
        if (this.runStreams[runId]?.close === stream.close) delete this.runStreams[runId];
      }
    },

    handleRunProgress(progress) {
      this.setRunTiming(progress.run_id, progress.timing);
      const idx = this.activeRuns.findIndex(r => r.run_id === progress.run_id);
      if (idx >= 0) {
        Object.assign(this.activeRuns[idx], {
          status: progress.status,
          total_tests: progress.total_tests,
          _progress: progress,
        });
      }
      if (Array.isArray(progress.steps) && progress.steps.length > 0) {
        const existing = this.runStepsCache[progress.run_id];
        if (existing && existing.length === progress.steps.length) {
          const byId = Object.fromEntries(progress.steps.map(step => [step.step_id, step]));
          this.runStepsCache[progress.run_id] = existing.map(step => ({ ...step, ...(byId[step.step_id] || {}) }));
        } else {
          this.refetchRunSteps(progress.run_id);
        }
      }
    },

    async handleRunDone(progress) {
      this.setRunTiming(progress.run_id, progress.timing);
      const idx = this.activeRuns.findIndex(r => r.run_id === progress.run_id);
      if (idx >= 0) Object.assign(this.activeRuns[idx], { status: progress.status, _progress: progress });
      this.closeRunStream(progress.run_id);
      // This run won't be polled again, so this is the last chance to replace a
      // document that still says RUNNING.
      if (!progress.timing) await this.loadRunTiming(progress.run_id, { force: true });
      await this.loadRunSteps(progress.run_id);
      await this.loadRuns();
    },

    // Stream ticks arrive each second and a failed load empties the cache, so this
    // keeps to one request at a time and, after a failure, waits for the next poll.
    refetchRunSteps(runId) {
      if (stepsRefetchHeld.has(runId)) return;
      stepsRefetchHeld.add(runId);
      this.loadRunSteps(runId).then((loaded) => {
        if (loaded) stepsRefetchHeld.delete(runId);
      });
    },

    isRunStreamLive(runId) {
      const stream = this.runStreams[runId];
      return Boolean(stream && stream.lastEventAt && Date.now() - stream.lastEventAt < STREAM_QUIET_MS);
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
          stepsRefetchHeld.delete(run.run_id);
          const [status, progress] = await Promise.all([
            api('GET', `/api/runs/${run.run_id}/status`),
            api('GET', `/api/runs/${run.run_id}/progress`).catch(() => null),
            this.loadRunSteps(run.run_id),
            // A connected stream already sends this each second.
            this.isRunStreamLive(run.run_id) ? null : this.loadRunTiming(run.run_id),
          ]);
          const idx = this.activeRuns.findIndex(r => r.run_id === run.run_id);
          if (idx >= 0) {
            Object.assign(this.activeRuns[idx], status);
            // /progress has no held_step, so it must not replace what the stream sent.
            if (progress && !this.isRunStreamLive(run.run_id)) this.activeRuns[idx]._progress = progress;
          }
          // The /timing above may predate the finish this /status reports, and a
          // finished run isn't polled again.
          if (this.isTerminalStatus(status.status) && !this.isTerminalStatus(this.runTimings[run.run_id]?.status)) {
            await this.loadRunTiming(run.run_id, { force: true });
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
        return true;
      } catch {
        delete this.runStepsCache[runId];
        return false;
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
        // Forced: a poll's /timing still out predates the release, and the row's
        // Release button reads this document.
        await Promise.all([
          this.loadRunSteps(m.runId),
          this.loadRunTiming(m.runId, { force: true }),
        ]);
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
