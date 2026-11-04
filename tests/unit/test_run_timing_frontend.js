'use strict';
const assert = require('node:assert');
const path = require('node:path');

global.window = global;
const storage = new Map();
global.localStorage = {
  getItem(key) { return storage.has(key) ? storage.get(key) : null; },
  setItem(key, value) { storage.set(key, String(value)); },
};
require(path.join(__dirname, '..', '..', 'frontend', 'features', 'run-timing.js'));
const { fmtSeconds, ganttLayout } = window.ETL_RUN_TIMING;

const T = (seconds) => new Date(Date.UTC(2026, 10, 4, 9, 0, seconds)).toISOString();

function step(overrides) {
  return Object.assign({
    step_index: 0, step_id: 'a', job_name: 'a', job_type: 'bo_report', job_family: 'sap',
    status: 'PASSED', attempt: 1, started_at: null, finished_at: null, held_at: null,
    hold_ended_at: null, runtime_seconds: null, estimate_seconds: null,
    progress: { state: 'done', percent: 100, label: 'PASSED' },
  }, overrides);
}

// --- formatting -----------------------------------------------------------
assert.strictEqual(fmtSeconds(null), '—');
assert.strictEqual(fmtSeconds(0), '0s');
assert.strictEqual(fmtSeconds(42.4), '42s');
assert.strictEqual(fmtSeconds(217), '3m 37s');
assert.strictEqual(fmtSeconds(3725), '1h 02m 05s');

// --- Gantt layout ---------------------------------------------------------
const finished = ganttLayout({
  started_at: T(0), completed_at: T(40), server_time: T(50),
  steps: [
    step({ step_id: 'a', started_at: T(0), finished_at: T(10) }),
    step({ step_id: 'b', started_at: T(10), finished_at: T(20), held_at: T(20), hold_ended_at: T(30) }),
    step({ step_id: 'c', status: 'BLOCKED' }),
  ],
});
assert.deepStrictEqual(finished.rows[0].bar, { left: 0, width: 25 });
assert.deepStrictEqual(finished.rows[1].bar, { left: 25, width: 25 });
assert.deepStrictEqual(finished.rows[1].hold, { left: 50, width: 25 });
assert.strictEqual(finished.rows[2].bar, null);
assert.strictEqual(finished.ticks.length, 4);
assert.strictEqual(finished.ticks[0].percent, 0);
assert.strictEqual(finished.ticks[3].percent, 100);

// A live run's axis ends at server time, and a running step's bar reaches it.
const live = ganttLayout({
  started_at: T(0), completed_at: null, server_time: T(20),
  steps: [step({ status: 'RUNNING', started_at: T(10) })],
});
assert.deepStrictEqual(live.rows[0].bar, { left: 50, width: 50 });

// Parallel steps overlap on the axis.
const parallel = ganttLayout({
  started_at: T(0), completed_at: T(10), server_time: T(10),
  steps: [
    step({ step_id: 'l', started_at: T(0), finished_at: T(10) }),
    step({ step_id: 'r', started_at: T(0), finished_at: T(5) }),
  ],
});
assert.strictEqual(parallel.rows[0].bar.left, parallel.rows[1].bar.left);

// A run that hasn't started has no axis, and a zero-length run doesn't divide by zero.
assert.deepStrictEqual(ganttLayout({ started_at: null, steps: [step({})] }).ticks, []);
const instant = ganttLayout({
  started_at: T(0), completed_at: T(0), server_time: T(0),
  steps: [step({ started_at: T(0), finished_at: T(0) })],
});
assert.ok(Number.isFinite(instant.rows[0].bar.width));

// --- component ------------------------------------------------------------
function component() {
  return Object.assign(window.ETL_FEATURE_RUN_TIMING(), {
    isTerminalStatus: (status) => ['PASSED', 'FAILED', 'BLOCKED'].includes(status),
  });
}

async function main() {
  storage.clear();
  const c = component();

  // Tabs: live runs open on Progress, finished runs on Timeline, a click wins.
  assert.strictEqual(c.runTimingTab({ run_id: 'x', status: 'RUNNING' }), 'progress');
  assert.strictEqual(c.runTimingTab({ run_id: 'x', status: 'PASSED' }), 'timeline');
  c.setRunTimingTab('x', 'progress');
  assert.strictEqual(c.runTimingTab({ run_id: 'x', status: 'PASSED' }), 'progress');

  // Bars.
  const bar = (state, status, percent) => step({ status, progress: { state, percent, label: '' } });
  assert.strictEqual(c.progressBarClass(bar('running', 'RUNNING', 50)), 'rt-s-running rt-stripe');
  assert.strictEqual(c.progressBarClass(bar('over_estimate', 'RUNNING', 95)), 'rt-s-slow rt-stripe');
  assert.strictEqual(c.progressBarClass(bar('running_unknown', 'RUNNING', null)), 'rt-s-running rt-stripe');
  assert.strictEqual(c.progressBarClass(bar('held', 'HELD', 100)), 'rt-s-held');
  assert.strictEqual(c.progressBarClass(bar('done', 'FAILED', 100)), 'rt-s-failed');
  assert.strictEqual(c.progressBarClass(bar('done', 'ERROR', 100)), 'rt-s-failed');
  assert.strictEqual(c.progressBarClass(bar('done', 'SLOW', 100)), 'rt-s-slow');
  assert.strictEqual(c.progressBarClass(bar('done', 'APPROVED', 100)), 'rt-s-passed');
  assert.strictEqual(c.progressBarClass(bar('done', 'CANCELLED', 100)), 'rt-s-idle');
  assert.strictEqual(c.progressBarWidth(bar('running', 'RUNNING', 62)), '62%');
  assert.strictEqual(c.progressBarWidth(bar('running_unknown', 'RUNNING', null)), '100%');

  // Runtime text.
  assert.strictEqual(c.runtimeText(step({
    runtime_seconds: 217, estimate_seconds: 350, status: 'RUNNING',
    progress: { state: 'running', percent: 62, label: '62%' },
  })), '3m 37s / ~5m 50s');
  assert.strictEqual(c.runtimeText(step({ runtime_seconds: 60, attempt: 2 })), '1m 00s · try 2');
  assert.strictEqual(c.runtimeText(step({
    status: 'PENDING', progress: { state: 'queued', percent: 0, label: 'queued' },
  })), 'queued');
  assert.strictEqual(c.runtimeText(step({
    status: 'BLOCKED', progress: { state: 'not_run', percent: 0, label: 'BLOCKED' },
  })), 'not run');
  assert.strictEqual(c.runtimeText(step({
    progress: { state: 'carried_over', percent: 100, label: 'carried over' },
  })), 'carried over');

  // Color toggle: status by default, remembered, and bad values ignored.
  assert.strictEqual(c.ganttColorBy, 'status');
  const failed = step({ status: 'FAILED', job_family: 'sap' });
  assert.strictEqual(c.ganttBarClass(failed), 'rt-s-failed');
  c.setGanttColorBy('family');
  assert.strictEqual(c.ganttBarClass(failed), 'rt-f-sap');
  assert.strictEqual(component().ganttColorBy, 'family');
  storage.set('atom_gantt_color_by', 'nonsense');
  assert.strictEqual(component().ganttColorBy, 'status');

  // Storage that throws must not break the toggle.
  const realStorage = global.localStorage;
  global.localStorage = {
    getItem() { throw new Error('blocked'); },
    setItem() { throw new Error('blocked'); },
  };
  const blocked = component();
  assert.strictEqual(blocked.ganttColorBy, 'status');
  blocked.setGanttColorBy('family');
  assert.strictEqual(blocked.ganttColorBy, 'family');
  global.localStorage = realStorage;

  // Legends match the toggle.
  storage.clear();
  const legend = component();
  assert.ok(legend.ganttLegend().some((item) => item.label === 'Failed'));
  legend.setGanttColorBy('family');
  assert.ok(legend.ganttLegend().some((item) => item.label === 'SAP'));

  // Tooltip carries type, status, runtime and tries.
  const tip = c.ganttTooltip(step({
    job_name: 'recon', job_type: 'reconciliation', status: 'FAILED', attempt: 2,
    started_at: T(0), finished_at: T(30), runtime_seconds: 30,
  }));
  for (const part of ['recon', 'reconciliation', 'FAILED', '30s', 'try 2']) {
    assert.ok(tip.includes(part), `${part} missing from ${tip}`);
  }

  // Load: a failed request leaves nothing cached, and a new timing replaces the old.
  global.api = async () => ({ run_id: 'r', steps: [] });
  await c.loadRunTiming('r');
  assert.strictEqual(c.runTimings.r.run_id, 'r');
  global.api = async () => { throw new Error('nope'); };
  await c.loadRunTiming('other');
  assert.strictEqual(c.runTimings.other, undefined);
  c.setRunTiming('r', { run_id: 'r', status: 'PASSED' });
  assert.strictEqual(c.runTimings.r.status, 'PASSED');
  c.setRunTiming('r', null);
  assert.strictEqual(c.runTimings.r.status, 'PASSED');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});