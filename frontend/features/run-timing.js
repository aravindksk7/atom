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

  function clockTime(ms) {
    return new Date(ms).toLocaleTimeString(undefined, {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  }

  function ganttLayout(timing) {
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
      ticks.push({ percent: fraction * 100, label: clockTime(start + span * fraction) });
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

  function statusClass(step) {
    return `rt-s-${STATUS_CLASSES[step.status] || 'idle'}`;
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
    return {
      runTimings: {},       // { run_id: timing document }
      runTimingTabs: {},    // { run_id: 'progress' | 'timeline' }, only after a click
      ganttColorBy: readColorBy(),

      fmtSeconds,
      ganttLayout,

      setRunTiming(runId, timing) {
        if (timing) this.runTimings = { ...this.runTimings, [runId]: timing };
      },

      async loadRunTiming(runId) {
        try {
          this.setRunTiming(runId, await api('GET', `/api/runs/${runId}/timing`));
        } catch {
          // Older data or a passing failure: the card falls back to what it has.
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
        if (state === 'carried_over' || state === 'interrupted') return 'rt-s-idle';
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

      ganttBarClass(step) {
        return this.ganttColorBy === 'family' ? `rt-f-${step.job_family}` : statusClass(step);
      },

      ganttLegend() {
        return this.ganttColorBy === 'family'
          ? FAMILY_LEGEND.map(([key, label]) => ({ cls: `rt-f-${key}`, label }))
          : STATUS_LEGEND.map(([key, label]) => ({ cls: `rt-s-${key}`, label }));
      },

      ganttTooltip(step) {
        const time = (iso) => (iso ? clockTime(Date.parse(iso)) : '—');
        const tries = (step.attempt || 0) > 1 ? ` · try ${step.attempt}` : '';
        return `${step.job_name} (${step.job_type || 'unknown type'}) · ${step.status} · `
          + `${time(step.started_at)} to ${time(step.finished_at)} · `
          + `${fmtSeconds(step.runtime_seconds)}${tries}`;
      },
    };
  };
})(window);