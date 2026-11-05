(function (global) {
  'use strict';
  // Run timing feature slice: formatting and Gantt layout for the timing
  // document from /api/runs/{id}/timing and /stream. The server decides state
  // and percent; this file only turns them into positions, text and classes.
  // Merged into the Alpine component via the FEATURE_SLICES reduce in app.js.
  const COLOR_BY_KEY = 'atom_gantt_color_by';
  const COLOR_BY_VALUES = ['status', 'family'];
  const TICK_COUNT = 4;
  const STATUS_CLASSES = {
    PASSED: 'passed', APPROVED: 'passed', FAILED: 'failed', ERROR: 'failed',
    SLOW: 'slow', RUNNING: 'running', HELD: 'held',
  };
  const STATUS_LEGEND = [
    ['passed', 'Passed'], ['failed', 'Failed'], ['slow', 'Slow or over estimate'],
    ['running', 'Running'], ['held', 'Held'], ['idle', 'Other'],
  ];
  const FAMILY_LEGEND = [
    ['reconciliation', 'Reconciliation'], ['file', 'File'], ['sap', 'SAP'],
    ['orchestration', 'Orchestration'], ['quality', 'Quality'], ['other', 'Other'],
  ];

  function fmtSeconds(value) {
    if (value === null || value === undefined) return '—';
    const total = Math.round(value);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    const pad = (n) => String(n).padStart(2, '0');
    if (hours) return `${hours}h ${pad(minutes)}m ${pad(seconds)}s`;
    if (minutes) return `${minutes}m ${pad(seconds)}s`;
    return `${seconds}s`;
  }

  function clockTime(ms, timeZone) {
    const options = { hour: '2-digit', minute: '2-digit', second: '2-digit' };
    try {
      return new Date(ms).toLocaleTimeString(undefined, { ...options, timeZone: timeZone || 'UTC' });
    } catch {
      // Unknown zone: fall back to the browser's, as fmtDate does.
      return new Date(ms).toLocaleTimeString(undefined, options);
    }
  }

  function ganttLayout(timing, timeZone) {
    const start = Date.parse(timing.started_at);
    const rowsWithoutBars = () => (timing.steps || []).map((step) => ({ step, bar: null, hold: null }));
    if (!timing.started_at || Number.isNaN(start)) return { ticks: [], rows: rowsWithoutBars() };

    const end = Date.parse(timing.completed_at || timing.server_time);
    const span = Math.max(end - start, 1);
    const percentOf = (ms) => Math.min(Math.max(((ms - start) / span) * 100, 0), 100);
    const segment = (from, to) => {
      const left = percentOf(Date.parse(from));
      return { left, width: Math.max(percentOf(Date.parse(to)) - left, 0) };
    };

    const ticks = [];
    for (let i = 0; i < TICK_COUNT; i++) {
      const fraction = i / (TICK_COUNT - 1);
      ticks.push({ percent: fraction * 100, label: clockTime(start + span * fraction, timeZone) });
    }
    const rows = (timing.steps || []).map((step) => ({
      step,
      bar: step.started_at
        ? segment(step.started_at, step.finished_at || timing.completed_at || timing.server_time)
        : null,
      hold: step.held_at && step.hold_ended_at ? segment(step.held_at, step.hold_ended_at) : null,
    }));
    return { ticks, rows };
  }

  function isInterrupted(step) {
    return Boolean(step.progress) && step.progress.state === 'interrupted';
  }

  function statusClass(step) {
    // An interrupted step's status still says RUNNING, but nothing is running.
    if (isInterrupted(step)) return 'rt-s-idle';
    // Still RUNNING, but past its estimate: amber, as the Progress bar shows it.
    if (step.progress && step.progress.state === 'over_estimate') return 'rt-s-slow';
    return `rt-s-${STATUS_CLASSES[step.status] || 'idle'}`;
  }

  // A response read just before a run finished can reach the browser after the
  // final document. /timing reads the run before the clock, so its server_time can
  // be the later one too, which is why a finished run also can't go back to live.
  // Restarting a run makes a new run_id, so this document can't legitimately do that.
  function isStaleTiming(stored, next, isTerminal) {
    if (!stored) return false;
    if (isTerminal(stored.status) && !isTerminal(next.status)) return true;
    return Date.parse(next.server_time) < Date.parse(stored.server_time);
  }

  function readColorBy() {
    try {
      const stored = global.localStorage.getItem(COLOR_BY_KEY);
      return COLOR_BY_VALUES.includes(stored) ? stored : 'status';
    } catch {
      return 'status';
    }
  }

  global.ETL_RUN_TIMING = { fmtSeconds, ganttLayout };

  global.ETL_FEATURE_RUN_TIMING = function () {
    // A plain closure, not component state: Alpine would otherwise track the read
    // from the card's x-init and rerun the run list whenever this changes.
    const timingLoads = new Set(); // run_ids with a /timing request out

    return {
      runTimings: {},       // { run_id: timing document }
      runTimingTabs: {},    // { run_id: 'progress' | 'timeline' }, only after a click
      ganttColorBy: readColorBy(),

      fmtSeconds,

      ganttLayout(timing) {
        return ganttLayout(timing, this.appTimezone);
      },

      setRunTiming(runId, timing) {
        if (!timing) return;
        if (isStaleTiming(this.runTimings[runId], timing, (status) => this.isTerminalStatus(status))) return;
        this.runTimings = { ...this.runTimings, [runId]: timing };
      },

      // force is for a caller that needs state newer than any request already out.
      // The older response can't overwrite the result: see isStaleTiming.
      async loadRunTiming(runId, { force = false } = {}) {
        if (!force && timingLoads.has(runId)) return;
        timingLoads.add(runId);
        try {
          this.setRunTiming(runId, await api('GET', `/api/runs/${runId}/timing`));
        } catch {
          // Older data or a passing failure: the card falls back to what it has.
        } finally {
          timingLoads.delete(runId);
        }
      },

      runTimingTab(run) {
        return this.runTimingTabs[run.run_id]
          || (this.isTerminalStatus(run.status) ? 'timeline' : 'progress');
      },

      setRunTimingTab(runId, tab) {
        this.runTimingTabs = { ...this.runTimingTabs, [runId]: tab };
      },

      progressBarClass(step) {
        const state = step.progress.state;
        if (state === 'running' || state === 'running_unknown') return 'rt-s-running rt-stripe';
        if (state === 'over_estimate') return 'rt-s-slow rt-stripe';
        if (state === 'held') return 'rt-s-held';
        if (state === 'carried_over') return 'rt-s-idle';
        return statusClass(step);
      },

      progressBarWidth(step) {
        const percent = step.progress.percent;
        return `${percent === null || percent === undefined ? 100 : percent}%`;
      },

      runtimeText(step) {
        const state = step.progress.state;
        if (state === 'queued') return 'queued';
        if (state === 'not_run') return 'not run';
        if (state === 'carried_over') return 'carried over';
        if (step.runtime_seconds === null || step.runtime_seconds === undefined) return '—';
        let text = fmtSeconds(step.runtime_seconds);
        if ((state === 'running' || state === 'over_estimate') && step.estimate_seconds) {
          text += ` / ~${fmtSeconds(step.estimate_seconds)}`;
        }
        if ((step.attempt || 0) > 1) text += ` · try ${step.attempt}`;
        return text;
      },

      setGanttColorBy(value) {
        if (!COLOR_BY_VALUES.includes(value)) return;
        this.ganttColorBy = value;
        try {
          global.localStorage.setItem(COLOR_BY_KEY, value);
        } catch {
          // Blocked storage only means the choice isn't remembered.
        }
      },

      stepStatusLabel(step) {
        return isInterrupted(step) ? 'interrupted' : step.status;
      },

      ganttBarClass(step) {
        return this.ganttColorBy === 'family' ? `rt-f-${step.job_family}` : statusClass(step);
      },

      ganttLegend() {
        return this.ganttColorBy === 'family'
          ? FAMILY_LEGEND.map(([key, label]) => ({ cls: `rt-f-${key}`, label }))
          : STATUS_LEGEND.map(([key, label]) => ({ cls: `rt-s-${key}`, label }));
      },

      ganttTooltip(step) {
        const time = (iso) => (iso ? clockTime(Date.parse(iso), this.appTimezone) : '—');
        const tries = (step.attempt || 0) > 1 ? ` · try ${step.attempt}` : '';
        const span = isInterrupted(step)
          ? `started ${time(step.started_at)}`
          : `${time(step.started_at)} to ${time(step.finished_at)}`;
        return `${step.job_name} (${step.job_type || 'unknown type'}) · ${this.stepStatusLabel(step)} · `
          + `${span} · ${fmtSeconds(step.runtime_seconds)}${tries}`;
      },
    };
  };
})(window);