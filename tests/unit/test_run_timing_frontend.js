'use strict';
const assert = require('node:assert');
const path = require('node:path');

global.window = global;
const storage = new Map();
global.localStorage = {
  getItem(key) { return storage.has(key) ? storage.get(key) : null; },
  setItem(key, value) { storage.set(key, String(value)); },
};
const TERMINAL_STATUSES = ['PASSED', 'FAILED', 'SLOW', 'ERROR', 'COMPLETED', 'CANCELLED', 'BLOCKED'];
const session = new Map();
global.sessionStorage = {
  getItem(key) { return session.has(key) ? session.get(key) : null; },
  setItem(key, value) { session.set(key, String(value)); },
};
global.API = '';
global.normalizeToken = (raw) => String(raw || '').trim();
global.isTerminalStatusValue = (status) => TERMINAL_STATUSES.includes(status);
require(path.join(__dirname, '..', '..', 'frontend', 'features', 'run-timing.js'));
require(path.join(__dirname, '..', '..', 'frontend', 'features', 'monitor.js'));
require(path.join(__dirname, '..', '..', 'frontend', 'features', 'history.js'));
require(path.join(__dirname, '..', '..', 'frontend', 'features', 'sequences.js'));
const { fmtSeconds, ganttLayout } = window.ETL_RUN_TIMING;
const { parseSseChunk } = window.ETL_MONITOR;

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
    isTerminalStatus: (status) => TERMINAL_STATUSES.includes(status),
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

  testTimingFreshness();
  await testLoadRunTimingGuard();
  testClockLabelsUseTheAppTimezone();
  testInterruptedAndNotRecorded();
  testSseParser();
  await testRunStream();
  await testPolling();
  await testStepsRefetch();
  await testRelease();
  await testHistoryRunDetail();
  await testSelectSequence();
  await testStreamLifecycle();
  testHeldAlertFallback();
}

// --- stale documents ------------------------------------------------------
function testTimingFreshness() {
  const c = component();
  const doc = (seconds, status) => ({ run_id: 'r', status, server_time: T(seconds), steps: [] });

  c.setRunTiming('r', doc(10, 'RUNNING'));
  c.setRunTiming('r', doc(5, 'RUNNING'));
  assert.strictEqual(c.runTimings.r.server_time, T(10), 'an older document must not replace a newer one');
  c.setRunTiming('r', doc(10, 'RUNNING'));
  c.setRunTiming('r', doc(11, 'RUNNING'));
  assert.strictEqual(c.runTimings.r.server_time, T(11));

  // The reported bug: done arrives, then a late /timing says RUNNING, for good.
  c.setRunTiming('r', doc(20, 'PASSED'));
  c.setRunTiming('r', doc(19, 'RUNNING'));
  assert.strictEqual(c.runTimings.r.status, 'PASSED');
  // /timing reads the run before it reads the clock, so a response can carry a
  // later server_time and still describe the run as it was before it finished.
  c.setRunTiming('r', doc(21, 'RUNNING'));
  assert.strictEqual(c.runTimings.r.status, 'PASSED', 'a finished run must not go back to running');
  c.setRunTiming('r', doc(22, 'PASSED'));
  assert.strictEqual(c.runTimings.r.server_time, T(22));

  // A document without a clock can't be judged stale, so it isn't dropped.
  const other = component();
  other.setRunTiming('o', { run_id: 'o', status: 'RUNNING', steps: [] });
  other.setRunTiming('o', { run_id: 'o', status: 'RUNNING', server_time: T(1), steps: [] });
  assert.strictEqual(other.runTimings.o.server_time, T(1));
}

// A promise the test settles by hand, for holding a request in flight.
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

async function testLoadRunTimingGuard() {
  const c = component();
  const calls = [];
  const pending = [];
  global.api = (method, url) => {
    calls.push(url);
    const d = deferred();
    pending.push(d);
    return d.promise;
  };

  const first = c.loadRunTiming('r');
  c.loadRunTiming('r');
  assert.deepStrictEqual(calls, ['/api/runs/r/timing'], 'a second load for the same run must wait its turn');

  // The guard is per run.
  const otherRun = c.loadRunTiming('s');
  assert.deepStrictEqual(calls, ['/api/runs/r/timing', '/api/runs/s/timing']);

  pending[0].resolve({ run_id: 'r', status: 'RUNNING', server_time: T(1), steps: [] });
  await first;
  pending[1].reject(new Error('nope'));
  await otherRun;
  assert.strictEqual(c.runTimings.r.server_time, T(1));

  // Both finished, so both can load again, including the one that failed.
  const again = c.loadRunTiming('r');
  const againOther = c.loadRunTiming('s');
  assert.strictEqual(calls.length, 4);

  // A load that must see the latest state doesn't wait for one already out,
  // and the older response can't overwrite it.
  const forced = c.loadRunTiming('r', { force: true });
  assert.strictEqual(calls.length, 5, 'force must not be swallowed by the guard');
  pending[4].resolve({ run_id: 'r', status: 'RUNNING', server_time: T(9), steps: [] });
  await forced;
  pending[2].resolve({ run_id: 'r', status: 'RUNNING', server_time: T(5), steps: [] });
  await again;
  assert.strictEqual(c.runTimings.r.server_time, T(9));
  pending[3].reject(new Error('nope'));
  await againOther;
}

// --- clock labels ---------------------------------------------------------
function testClockLabelsUseTheAppTimezone() {
  const clock = (iso, timeZone) => new Date(iso).toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone,
  });
  const doc = {
    started_at: T(0), completed_at: T(30), server_time: T(30),
    steps: [step({ started_at: T(0), finished_at: T(30), runtime_seconds: 30 })],
  };
  const c = component();
  c.appTimezone = 'Asia/Tokyo';
  const tokyo = clock(T(0), 'Asia/Tokyo');
  assert.strictEqual(c.ganttLayout(doc).ticks[0].label, tokyo);
  assert.ok(c.ganttTooltip(doc.steps[0]).includes(tokyo), 'the tooltip must use the app timezone');

  c.appTimezone = 'America/New_York';
  const newYork = clock(T(0), 'America/New_York');
  assert.notStrictEqual(tokyo, newYork);
  assert.strictEqual(c.ganttLayout(doc).ticks[0].label, newYork);
  assert.ok(c.ganttTooltip(doc.steps[0]).includes(newYork));

  // fmtDate survives an unknown zone, so the clock labels have to as well.
  c.appTimezone = 'Not/AZone';
  assert.strictEqual(c.ganttLayout(doc).ticks.length, 4);
  assert.ok(c.ganttTooltip(doc.steps[0]).includes('30s'));
}

// --- interrupted and not-recorded steps -----------------------------------
function testInterruptedAndNotRecorded() {
  storage.clear();
  const c = component();
  const interrupted = step({
    job_name: 'crash', job_type: 'profile', job_family: 'quality', status: 'RUNNING',
    started_at: T(0), finished_at: null, runtime_seconds: 30,
    progress: { state: 'interrupted', percent: 100, label: 'interrupted' },
  });
  assert.strictEqual(c.ganttBarClass(interrupted), 'rt-s-idle', 'interrupted is grey, not running indigo');
  assert.strictEqual(c.progressBarClass(interrupted), 'rt-s-idle');
  assert.strictEqual(c.stepStatusLabel(interrupted), 'interrupted');
  const tip = c.ganttTooltip(interrupted);
  assert.ok(tip.includes('interrupted'), tip);
  assert.ok(!tip.includes('RUNNING'), tip);
  assert.ok(!tip.includes('to —'), tip);

  // The family toggle still colors it by family.
  c.setGanttColorBy('family');
  assert.strictEqual(c.ganttBarClass(interrupted), 'rt-f-quality');
  c.setGanttColorBy('status');

  // A live running step keeps its indigo and its open end.
  const running = step({
    status: 'RUNNING', started_at: T(0), runtime_seconds: 5,
    progress: { state: 'running', percent: 50, label: '50%' },
  });
  assert.strictEqual(c.ganttBarClass(running), 'rt-s-running');
  assert.strictEqual(c.stepStatusLabel(running), 'RUNNING');
  assert.ok(c.ganttTooltip(running).includes('RUNNING'));

  // An older run's finished step keeps its status color.
  const notRecorded = step({
    status: 'FAILED', progress: { state: 'not_recorded', percent: 100, label: 'FAILED' },
  });
  assert.strictEqual(c.ganttBarClass(notRecorded), 'rt-s-failed');
  assert.strictEqual(c.progressBarClass(notRecorded), 'rt-s-failed');
  assert.strictEqual(c.stepStatusLabel(notRecorded), 'FAILED');
  assert.ok(!c.ganttTooltip(notRecorded).includes('interrupted'));
}

// --- SSE parser -----------------------------------------------------------
function parseAll(chunks) {
  let rest = '';
  const events = [];
  for (const chunk of chunks) {
    const parsed = parseSseChunk(rest, chunk);
    events.push(...parsed.events);
    rest = parsed.rest;
  }
  return { events, rest };
}

function testSseParser() {
  const one = { event: 'progress', data: '{"a":1}' };
  assert.deepStrictEqual(parseAll(['event: progress\ndata: {"a":1}\n\n']), { events: [one], rest: '' });

  // Several events in one chunk, and an incomplete one left over.
  assert.deepStrictEqual(
    parseAll(['event: a\ndata: 1\n\nevent: b\ndata: 2\n\nevent: c\nda']),
    { events: [{ event: 'a', data: '1' }, { event: 'b', data: '2' }], rest: 'event: c\nda' },
  );

  // Network chunks can split anywhere, in LF and CRLF streams alike.
  for (const eol of ['\n', '\r\n']) {
    const text = ['event: progress', 'data: {"a":1}', '', 'event: done', 'data: {"b":2}', '', ''].join(eol);
    const expected = [one, { event: 'done', data: '{"b":2}' }];
    for (let i = 1; i < text.length; i++) {
      const got = parseAll([text.slice(0, i), text.slice(i)]);
      assert.deepStrictEqual(got.events, expected, `split at ${i} with ${JSON.stringify(eol)}`);
      assert.strictEqual(got.rest, '', `split at ${i} with ${JSON.stringify(eol)}`);
    }
    assert.deepStrictEqual(parseAll(text.split('')).events, expected);
  }

  // Multi-line data joins with a newline.
  assert.deepStrictEqual(parseAll(['data: one\ndata: two\n\n']).events, [{ event: 'message', data: 'one\ntwo' }]);
  assert.deepStrictEqual(parseAll(['data: one\r\ndata: two\r\n\r\n']).events, [{ event: 'message', data: 'one\ntwo' }]);

  // Comments and blocks without data dispatch nothing; a comment inside an event is skipped.
  assert.deepStrictEqual(parseAll([': keep-alive\n\n']).events, []);
  assert.deepStrictEqual(parseAll(['event: lonely\n\n']).events, []);
  assert.deepStrictEqual(parseAll(['event: a\n: note\ndata: 1\n\n']).events, [{ event: 'a', data: '1' }]);

  // Field details from the SSE spec: one optional space, unknown fields ignored,
  // and the event name doesn't leak into the next event.
  assert.deepStrictEqual(parseAll(['data:1\n\n']).events, [{ event: 'message', data: '1' }]);
  assert.deepStrictEqual(parseAll(['data:  1\n\n']).events, [{ event: 'message', data: ' 1' }]);
  assert.deepStrictEqual(parseAll(['id: 7\nretry: 5\ndata: 1\n\n']).events, [{ event: 'message', data: '1' }]);
  assert.deepStrictEqual(
    parseAll(['event: a\ndata: 1\n\ndata: 2\n\n']).events,
    [{ event: 'a', data: '1' }, { event: 'message', data: '2' }],
  );
}

// --- run stream -----------------------------------------------------------
// A response body the test feeds by hand. Aborting rejects the pending read, as fetch does.
function fakeBody(signal) {
  const encoder = new TextEncoder();
  const queue = [];
  let waiter = null;
  const settle = (item) => {
    if (waiter) {
      const w = waiter;
      waiter = null;
      if (item.error) w.reject(item.error);
      else w.resolve(item);
    } else {
      queue.push(item);
    }
  };
  signal.addEventListener('abort', () => settle({ error: new Error('aborted') }));
  return {
    push(text) { settle({ done: false, value: encoder.encode(text) }); },
    pushBytes(bytes) { settle({ done: false, value: bytes }); },
    end() { settle({ done: true }); },
    fail() { settle({ error: new Error('connection reset') }); },
    getReader() {
      return {
        read() {
          if (queue.length) {
            const item = queue.shift();
            return item.error ? Promise.reject(item.error) : Promise.resolve(item);
          }
          return new Promise((resolve, reject) => { waiter = { resolve, reject }; });
        },
      };
    },
  };
}

const flush = async () => { for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve)); };

function installFetch(status = 200) {
  const opened = [];
  global.fetch = async (url, options) => {
    const body = fakeBody(options.signal);
    opened.push({ url, options, body });
    return { ok: status >= 200 && status < 300, status, body };
  };
  return opened;
}

function monitor(extra) {
  return Object.assign(window.ETL_FEATURE_RUN_TIMING(), window.ETL_FEATURE_MONITOR(), {
    currentView: 'other',
    runs: [],
    loadRunsCalls: 0,
    async loadRuns() { this.loadRunsCalls += 1; },
    toast() {},
  }, extra);
}

const sse = (event, payload) => `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`;
const liveRun = (id = 'r') => ({ run_id: id, status: 'RUNNING' });

async function testRunStream() {
  session.set('etl_token', 'secret-token');
  global.api = async () => [];

  // Same bearer header as api(), and the token stays out of the URL.
  let opened = installFetch();
  let m = monitor({ activeRuns: [liveRun()] });
  m.startRunStream(m.activeRuns[0]);
  m.startRunStream(m.activeRuns[0]);
  assert.strictEqual(opened.length, 1, 'one stream per run');
  assert.strictEqual(opened[0].url, '/api/runs/r/stream');
  assert.ok(!opened[0].url.includes('secret-token'));
  assert.strictEqual(opened[0].options.headers.Authorization, 'Bearer secret-token');
  assert.strictEqual(typeof m.runStreams.r.close, 'function');

  // Not live until data arrives: a 200 with nothing behind it isn't a working stream.
  await flush();
  assert.strictEqual(m.isRunStreamLive('r'), false);

  // Progress reaches the card, split across chunks and with held_step for the alert.
  const payload = {
    run_id: 'r', status: 'RUNNING', total_tests: 2, held_step: 1, steps: [],
    timing: { run_id: 'r', status: 'RUNNING', server_time: T(1), steps: [] },
  };
  const text = sse('progress', payload);
  opened[0].body.push(text.slice(0, 12));
  opened[0].body.push(text.slice(12));
  await flush();
  assert.strictEqual(m.activeRuns[0]._progress.held_step, 1);
  assert.strictEqual(m.runTimings.r.server_time, T(1));
  assert.strictEqual(m.isRunStreamLive('r'), true);

  // A multi-byte character split across chunks survives.
  const bytes = new TextEncoder().encode(sse('progress', { ...payload, note: 'a·b', timing: null }));
  const middle = bytes.indexOf(0xc2) + 1;
  opened[0].body.pushBytes(bytes.slice(0, middle));
  opened[0].body.pushBytes(bytes.slice(middle));
  await flush();
  assert.strictEqual(m.activeRuns[0]._progress.note, 'a·b');

  // done: final timing stored, stream closed, entry gone, no reopen for a finished run.
  opened[0].body.push(sse('done', {
    ...payload, status: 'PASSED', held_step: null,
    timing: { run_id: 'r', status: 'PASSED', server_time: T(2), steps: [] },
  }));
  await flush();
  assert.strictEqual(m.activeRuns[0].status, 'PASSED');
  assert.strictEqual(m.runTimings.r.status, 'PASSED');
  assert.strictEqual(m.runStreams.r, undefined);
  assert.ok(opened[0].options.signal.aborted, 'done must close the connection');
  assert.strictEqual(m.loadRunsCalls, 1);
  m.startRunStream(m.activeRuns[0]);
  assert.strictEqual(opened.length, 1);

  // done without timing still replaces a RUNNING document.
  opened = installFetch();
  m = monitor({ activeRuns: [liveRun()] });
  const timingCalls = [];
  global.api = async (method, url) => {
    if (url.endsWith('/timing')) {
      timingCalls.push(url);
      return { run_id: 'r', status: 'FAILED', server_time: T(5), steps: [] };
    }
    return [];
  };
  m.setRunTiming('r', { run_id: 'r', status: 'RUNNING', server_time: T(1), steps: [] });
  m.startRunStream(m.activeRuns[0]);
  await flush();
  opened[0].body.push(sse('done', { run_id: 'r', status: 'FAILED', steps: [], timing: null }));
  await flush();
  assert.deepStrictEqual(timingCalls, ['/api/runs/r/timing']);
  assert.strictEqual(m.runTimings.r.status, 'FAILED');
  global.api = async () => [];

  // Ended without done: entry removed, nothing retries until something calls start again.
  opened = installFetch();
  m = monitor({ activeRuns: [liveRun()] });
  m.startRunStream(m.activeRuns[0]);
  await flush();
  opened[0].body.end();
  await flush();
  assert.strictEqual(m.runStreams.r, undefined);
  assert.strictEqual(m.isRunStreamLive('r'), false);
  await flush();
  assert.strictEqual(opened.length, 1, 'no tight retry loop');
  m.startRunStream(m.activeRuns[0]);
  assert.strictEqual(opened.length, 2, 'the next poll can reopen it');
  await flush();

  // The connection breaking behaves the same.
  opened[1].body.fail();
  await flush();
  assert.strictEqual(m.runStreams.r, undefined);
  assert.strictEqual(opened.length, 2);

  // A server error is not a denial, so the next poll may try again.
  opened = installFetch(500);
  m = monitor({ activeRuns: [liveRun()] });
  m.startRunStream(m.activeRuns[0]);
  await flush();
  assert.strictEqual(m.runStreams.r, undefined);
  m.startRunStream(m.activeRuns[0]);
  assert.strictEqual(opened.length, 2);
  await flush();

  // A bad payload ends the stream rather than leaving it half-alive.
  opened = installFetch();
  m = monitor({ activeRuns: [liveRun()] });
  m.startRunStream(m.activeRuns[0]);
  await flush();
  opened[0].body.push('event: progress\ndata: {not json\n\n');
  await flush();
  assert.strictEqual(m.runStreams.r, undefined);

  // 401 and 403: no retry for that run while the token stays the same.
  for (const status of [401, 403]) {
    opened = installFetch(status);
    m = monitor({ activeRuns: [liveRun()] });
    m.startRunStream(m.activeRuns[0]);
    await flush();
    assert.strictEqual(m.runStreams.r, undefined);
    m.startRunStream(m.activeRuns[0]);
    await flush();
    assert.strictEqual(opened.length, 1, `${status} must not be retried with the same token`);
    session.set('etl_token', `new-token-${status}`);
    m.startRunStream(m.activeRuns[0]);
    assert.strictEqual(opened.length, 2, 'a new token gets a new try');
    await flush();
    m.closeRunStream('r');
    session.set('etl_token', 'secret-token');
    await flush();
  }

  // closeRunStream aborts, and a closed reader can't remove the entry that replaced it.
  opened = installFetch();
  m = monitor({ activeRuns: [liveRun()] });
  m.startRunStream(m.activeRuns[0]);
  await flush();
  m.closeRunStream('r');
  assert.ok(opened[0].options.signal.aborted);
  assert.strictEqual(m.runStreams.r, undefined);
  m.startRunStream(m.activeRuns[0]);
  const replacement = m.runStreams.r;
  assert.ok(replacement, 'a stream reopens after a close');
  await flush();
  assert.strictEqual(m.runStreams.r, replacement);
  m.closeRunStream('r');
  await flush();

  // Browsers allow 6 connections per host over HTTP/1.1, so streams stop short of
  // that and the remaining runs use the poll.
  opened = installFetch();
  const many = ['a', 'b', 'c', 'd', 'e', 'f', 'g'].map(liveRun);
  m = monitor({ activeRuns: many });
  many.forEach((run) => m.startRunStream(run));
  assert.strictEqual(opened.length, 4);
  await flush();
  Object.keys(m.runStreams).forEach((id) => m.closeRunStream(id));
  await flush();
}

// --- polling --------------------------------------------------------------
function pollApi(timingDoc) {
  const calls = [];
  global.api = async (method, url) => {
    calls.push(url);
    if (url.endsWith('/status')) return { status: 'RUNNING' };
    if (url.endsWith('/progress')) return { run_id: 'r', status: 'RUNNING', percent_complete: 10, held_step: null };
    if (url.endsWith('/timing')) return timingDoc();
    return [];
  };
  return calls;
}

async function testPolling() {
  const timing = (status = 'RUNNING', seconds = 5) => ({ run_id: 'r', status, server_time: T(seconds), steps: [] });

  // No stream: the poll is the fallback.
  let opened = installFetch();
  let calls = pollApi(timing);
  let m = monitor({ activeRuns: [liveRun()] });
  await m.pollActiveRuns();
  assert.ok(calls.includes('/api/runs/r/timing'), 'polling is the fallback when there is no stream');

  // Stream sending data: no /timing, and /progress can't wipe the stream's held_step.
  opened = installFetch();
  m = monitor({ activeRuns: [liveRun()] });
  m.startRunStream(m.activeRuns[0]);
  await flush();
  opened[0].body.push(sse('progress', { run_id: 'r', status: 'RUNNING', held_step: 1, steps: [], timing: timing('RUNNING', 1) }));
  await flush();
  calls = pollApi(timing);
  await m.pollActiveRuns();
  assert.ok(!calls.includes('/api/runs/r/timing'), 'a connected stream replaces the /timing poll');
  assert.ok(calls.includes('/api/runs/r/status'));
  assert.strictEqual(m.activeRuns[0]._progress.held_step, 1);

  // A stream gone quiet is treated as not connected.
  const realNow = Date.now;
  Date.now = () => realNow() + 60_000;
  calls = pollApi(timing);
  await m.pollActiveRuns();
  Date.now = realNow;
  assert.ok(calls.includes('/api/runs/r/timing'), 'a silent stream must not stop the fallback');
  m.closeRunStream('r');
  await flush();

  // The poll reopens a failed stream, on the Monitor view.
  opened = installFetch();
  calls = pollApi(timing);
  const listed = liveRun();
  m = monitor({ currentView: 'monitor', activeRuns: [], runs: [listed] });
  await m.pollActiveRuns();
  assert.strictEqual(opened.length, 1);
  await flush();
  opened[0].body.end();
  await flush();
  assert.strictEqual(m.runStreams.r, undefined);
  await m.pollActiveRuns();
  assert.strictEqual(opened.length, 2);
  m.closeRunStream('r');
  await flush();

  // The poll sees the run finish while its own /timing was read before that: the
  // card must not be left saying RUNNING, because this run won't be polled again.
  installFetch();
  let served = 0;
  global.api = async (method, url) => {
    if (url.endsWith('/status')) return { status: 'PASSED' };
    if (url.endsWith('/progress')) return null;
    if (url.endsWith('/timing')) {
      served += 1;
      return served === 1 ? timing('RUNNING', 3) : timing('PASSED', 4);
    }
    return [];
  };
  m = monitor({ activeRuns: [liveRun()] });
  await m.pollActiveRuns();
  assert.strictEqual(m.activeRuns[0].status, 'PASSED');
  assert.strictEqual(m.runTimings.r.status, 'PASSED');
  assert.strictEqual(served, 2);
}

// --- /steps refetch -------------------------------------------------------
async function testStepsRefetch() {
  const tick = (seconds) => sse('progress', {
    run_id: 'r', status: 'RUNNING', steps: [{ step_id: 'a', status: 'RUNNING' }],
    timing: { run_id: 'r', status: 'RUNNING', server_time: T(seconds), steps: [] },
  });
  let calls = [];
  const stepsCalls = () => calls.filter((url) => url.endsWith('/steps')).length;

  // /steps failing: ticks don't hammer it, and the failure holds until the next poll.
  let opened = installFetch();
  global.api = async (method, url) => {
    calls.push(url);
    if (url.endsWith('/steps')) throw new Error('nope');
    if (url.endsWith('/status')) return { status: 'RUNNING' };
    return null;
  };
  let m = monitor({ activeRuns: [liveRun()] });
  m.startRunStream(m.activeRuns[0]);
  await flush();
  for (let i = 0; i < 10; i++) {
    opened[0].body.push(tick(i));
    await flush();
  }
  assert.strictEqual(stepsCalls(), 1, `${stepsCalls()} /steps calls in 10 ticks`);
  await m.pollActiveRuns();
  assert.strictEqual(stepsCalls(), 2, 'the poll retries once');
  for (let i = 10; i < 20; i++) {
    opened[0].body.push(tick(i));
    await flush();
  }
  assert.ok(stepsCalls() <= 3, `${stepsCalls()} /steps calls after the poll`);
  m.closeRunStream('r');
  await flush();

  // Slow but healthy /steps: one request at a time, then the cache takes over.
  calls = [];
  opened = installFetch();
  const held = deferred();
  global.api = (method, url) => {
    calls.push(url);
    return url.endsWith('/steps') ? held.promise : Promise.resolve(null);
  };
  m = monitor({ activeRuns: [liveRun()] });
  m.startRunStream(m.activeRuns[0]);
  await flush();
  for (let i = 0; i < 5; i++) {
    opened[0].body.push(tick(i));
    await flush();
  }
  assert.strictEqual(stepsCalls(), 1, 'one /steps request in flight at a time');
  held.resolve([{ step_id: 'a', status: 'RUNNING' }]);
  await flush();
  opened[0].body.push(tick(6));
  await flush();
  assert.strictEqual(stepsCalls(), 1, 'a filled cache needs no refetch');
  m.closeRunStream('r');
  await flush();
}

// --- release --------------------------------------------------------------
async function testRelease() {
  // A poll's /timing, read before the release, is still out when the release lands.
  const calls = [];
  const pendingTiming = [];
  global.api = (method, url) => {
    calls.push(`${method} ${url}`);
    if (url.endsWith('/timing')) {
      const d = deferred();
      pendingTiming.push(d);
      return d.promise;
    }
    return Promise.resolve([]);
  };
  const m = monitor({
    activeRuns: [liveRun()],
    stepReleaseModal: { show: true, runId: 'r', stepIndex: 1, releasedBy: 'me', note: 'ok', action: 'approve' },
  });
  const held = (seconds, state) => ({
    run_id: 'r', status: 'RUNNING', server_time: T(seconds),
    steps: [step({ status: state === 'held' ? 'HELD' : 'APPROVED', progress: { state, percent: 100, label: state } })],
  });
  m.setRunTiming('r', held(1, 'held'));
  const pollLoad = m.loadRunTiming('r');
  const release = m.submitStepRelease();
  await flush();
  assert.strictEqual(pendingTiming.length, 2, 'the release must not be dropped by the in-flight guard');
  pendingTiming[1].resolve(held(10, 'done'));
  await release;
  assert.strictEqual(m.runTimings.r.steps[0].progress.state, 'done', 'Release must go away after success');
  pendingTiming[0].resolve(held(5, 'held'));
  await pollLoad;
  assert.strictEqual(m.runTimings.r.steps[0].progress.state, 'done', 'the older poll must not bring Release back');
  assert.ok(calls.includes('POST /api/runs/r/steps/1/release'));
}

// --- History run detail ---------------------------------------------------
function historyDetail() {
  const { viewRunDetail } = window.ETL_FEATURE_HISTORY();
  return Object.assign(component(), {
    viewRunDetail,
    selectedRun: null,
    restartRerunDownstream: false,
    toasts: [],
    charted: 0,
    toast(...args) { this.toasts.push(args); },
    renderChart() { this.charted += 1; },
    $nextTick(callback) { callback(); },
  });
}

// Serves the run itself at once and hands /timing to the caller.
function detailApi(onTiming) {
  const calls = [];
  global.api = (method, url) => {
    calls.push(url);
    if (url.endsWith('/timing')) return onTiming(url);
    if (url.endsWith('/steps')) return Promise.resolve([{ step_id: 'a' }]);
    if (url.endsWith('/restart')) return Promise.resolve({ run_id: 'next' });
    return Promise.resolve({ run_id: url.split('/').pop(), status: 'PASSED' });
  };
  return calls;
}

async function testHistoryRunDetail() {
  const doc = (runId, status, seconds) => ({ run_id: runId, status, server_time: T(seconds), steps: [] });

  // An older server or a 500 on /timing costs the timeline and nothing else.
  let h = historyDetail();
  detailApi(() => Promise.reject(new Error('500')));
  await h.viewRunDetail('r');
  await flush();
  assert.strictEqual(h.selectedRun.run_id, 'r');
  assert.deepStrictEqual(h.selectedRun.steps, [{ step_id: 'a' }]);
  assert.strictEqual(h.selectedRun.restarted_as_run_id, 'next');
  assert.strictEqual(h.charted, 1);
  assert.deepStrictEqual(h.toasts, [], 'a failed /timing must not look like a failed load');
  assert.strictEqual(h.runTimings.r, undefined);

  // A slow /timing doesn't hold up the rest of the detail.
  h = historyDetail();
  const slow = deferred();
  detailApi(() => slow.promise);
  await h.viewRunDetail('r');
  assert.strictEqual(h.charted, 1, 'the detail must not wait for /timing');
  slow.resolve(doc('r', 'PASSED', 1));
  await flush();
  assert.strictEqual(h.runTimings.r.status, 'PASSED');

  // Seen on Monitor as running, with a poll's /timing still out, then finished.
  h = historyDetail();
  const pending = [];
  const calls = detailApi(() => {
    const d = deferred();
    pending.push(d);
    return d.promise;
  });
  h.setRunTiming('r', doc('r', 'RUNNING', 1));
  const poll = h.loadRunTiming('r');
  await h.viewRunDetail('r');
  assert.strictEqual(calls.filter((url) => url.endsWith('/timing')).length, 2, 'the in-flight guard must not swallow this load');
  pending[1].resolve(doc('r', 'PASSED', 10));
  await flush();
  assert.strictEqual(h.runTimings.r.status, 'PASSED');
  pending[0].resolve(doc('r', 'RUNNING', 5));
  await poll;
  assert.strictEqual(h.runTimings.r.status, 'PASSED', 'the older poll must not bring back the live timing');

  // Each run keeps its own document, and opening a run again asks the server again.
  h = historyDetail();
  let clock = 0;
  detailApi((url) => Promise.resolve(doc(url.split('/')[3], 'PASSED', ++clock)));
  await h.viewRunDetail('a');
  await h.viewRunDetail('b');
  await flush();
  assert.strictEqual(h.selectedRun.run_id, 'b');
  assert.strictEqual(h.runTimings.a.run_id, 'a');
  assert.strictEqual(h.runTimings.b.run_id, 'b');
  const before = h.runTimings.b.server_time;
  await h.viewRunDetail('b');
  await flush();
  assert.notStrictEqual(h.runTimings.b.server_time, before);
}

// --- Sequences detail -----------------------------------------------------
function sequencesDetail(extra) {
  return Object.assign(Object.create(null), {
    selectSequence: window.ETL_FEATURE_SEQUENCES().selectSequence,
    loadSequences: window.ETL_FEATURE_SEQUENCES().loadSequences,
    loadSequenceRuns: window.ETL_FEATURE_SEQUENCES().loadSequenceRuns,
    openSequenceRun: window.ETL_FEATURE_SEQUENCES().openSequenceRun,
    sequences: [],
    sequencesLoading: false,
    selectedSequence: null,
    sequenceUsage: { selections: [], schedules: [] },
    sequenceRuns: [],
    _sequenceSelectToken: 0,
  }, extra);
}

// Every request waits until the test settles it by URL.
function heldApi() {
  const held = new Map();
  global.api = (method, url) => {
    const d = deferred();
    held.set(url, d);
    return d.promise;
  };
  return {
    has: (url) => held.has(url),
    resolve: async (url, value) => { held.get(url).resolve(value); await flush(); },
    reject: async (url) => { held.get(url).reject(new Error('nope')); await flush(); },
  };
}

async function testSelectSequence() {
  const usage = (name) => ({ selections: [name], schedules: [] });
  const runs = (name) => [{ run_id: `${name}-run`, status: 'PASSED' }];

  // Rows from the last selection are gone as soon as another is picked.
  let s = sequencesDetail({ selectedSequence: { id: 1 }, sequenceRuns: runs('old') });
  let held = heldApi();
  s.selectSequence({ id: 2 });
  assert.deepStrictEqual(s.sequenceRuns, []);
  await held.resolve('/api/sequences/2', { id: 2 });
  await held.resolve('/api/sequences/2/usage', usage('b'));
  await held.resolve('/api/sequences/2/runs?limit=20', runs('b'));
  assert.deepStrictEqual(s.sequenceRuns, runs('b'));

  // A then B: A's responses landing after B's must not show under B.
  s = sequencesDetail();
  held = heldApi();
  s.selectSequence({ id: 1 });
  await held.resolve('/api/sequences/1', { id: 1 });
  await held.resolve('/api/sequences/1/usage', usage('a'));
  s.selectSequence({ id: 2 });
  await held.resolve('/api/sequences/2', { id: 2 });
  await held.resolve('/api/sequences/2/usage', usage('b'));
  await held.resolve('/api/sequences/2/runs?limit=20', runs('b'));
  await held.resolve('/api/sequences/1/runs?limit=20', runs('a'));
  assert.deepStrictEqual(s.sequenceRuns, runs('b'));

  // The same holds at each earlier request, and for a late failure.
  s = sequencesDetail();
  held = heldApi();
  s.selectSequence({ id: 1 });
  s.selectSequence({ id: 2 });
  await held.resolve('/api/sequences/2', { id: 2 });
  await held.resolve('/api/sequences/2/usage', usage('b'));
  await held.resolve('/api/sequences/2/runs?limit=20', runs('b'));
  await held.resolve('/api/sequences/1', { id: 1 });
  assert.strictEqual(s.selectedSequence.id, 2);
  assert.ok(!held.has('/api/sequences/1/usage'), 'a replaced selection stops loading');
  assert.deepStrictEqual(s.sequenceUsage, usage('b'));

  s = sequencesDetail();
  held = heldApi();
  s.selectSequence({ id: 1 });
  s.selectSequence({ id: 2 });
  await held.resolve('/api/sequences/2', { id: 2 });
  await held.reject('/api/sequences/1');
  assert.strictEqual(s.selectedSequence.id, 2, 'a late failure must not close the current sequence');

  // The detail is still useful without its history.
  s = sequencesDetail();
  held = heldApi();
  s.selectSequence({ id: 1 });
  await held.resolve('/api/sequences/1', { id: 1 });
  await held.resolve('/api/sequences/1/usage', usage('a'));
  await held.reject('/api/sequences/1/runs?limit=20');
  assert.strictEqual(s.selectedSequence.id, 1);
  assert.deepStrictEqual(s.sequenceRuns, []);

  // A failed detail closes the panel and shows no runs.
  s = sequencesDetail({ selectedSequence: { id: 9 } });
  held = heldApi();
  s.selectSequence({ id: 1 });
  await held.reject('/api/sequences/1');
  assert.strictEqual(s.selectedSequence, null);
  assert.ok(!held.has('/api/sequences/1/runs?limit=20'));

  // Coming back to the tab refreshes the list, so a run that just finished shows.
  s = sequencesDetail({ selectedSequence: { id: 1 }, sequenceRuns: runs('old') });
  held = heldApi();
  const loading = s.loadSequences();
  await held.resolve('/api/sequences', []);
  await loading;
  await held.resolve('/api/sequences/1/runs?limit=20', runs('new'));
  assert.deepStrictEqual(s.sequenceRuns, runs('new'));

  // ...but not over a selection made while that refresh was out.
  s = sequencesDetail({ selectedSequence: { id: 1 } });
  held = heldApi();
  s.loadSequenceRuns(1);
  s.selectSequence({ id: 2 });
  await held.resolve('/api/sequences/1/runs?limit=20', runs('a'));
  assert.deepStrictEqual(s.sequenceRuns, []);

  // Opening a run goes through the tab switch, which also starts and stops pollers,
  // and an earlier run's detail doesn't stand in while this one loads.
  const entered = [];
  const opened = [];
  s = sequencesDetail({
    selectedRun: { run_id: 'earlier' },
    onTabEnter(id) { entered.push(id); },
    viewRunDetail(id) { opened.push(id); assert.strictEqual(this.selectedRun, null); },
  });
  s.openSequenceRun('r');
  assert.deepStrictEqual(entered, ['history']);
  assert.deepStrictEqual(opened, ['r']);
}

// --- stream lifecycle -----------------------------------------------------
async function testStreamLifecycle() {
  session.set('etl_token', 'secret-token');
  global.api = async () => [];

  // A token change closes every open stream, so none keeps the old bearer.
  let opened = installFetch();
  let m = monitor({ activeRuns: [liveRun('a'), liveRun('b')] });
  m.activeRuns.forEach((run) => m.startRunStream(run));
  await flush();
  assert.strictEqual(typeof m.closeAllRunStreams, 'function');
  m.closeAllRunStreams();
  assert.ok(opened.every((o) => o.options.signal.aborted), 'every stream must be aborted');
  assert.deepStrictEqual(Object.keys(m.runStreams), []);
  await flush();

  // The poll is a backstop: a stream opened with another token is closed.
  opened = installFetch();
  m = monitor({ activeRuns: [liveRun()] });
  m.startRunStream(m.activeRuns[0]);
  await flush();
  session.set('etl_token', 'changed-token');
  pollApi(() => ({ run_id: 'r', status: 'RUNNING', server_time: T(5), steps: [] }));
  await m.pollActiveRuns();
  assert.ok(opened[0].options.signal.aborted, 'a stream on the old token must be closed');
  session.set('etl_token', 'secret-token');
  await flush();

  // The token handlers in app.js close streams.
  const fs = require('node:fs');
  const appSource = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'app.js'), 'utf8');
  for (const name of ['handleAuthError', 'setStoredToken', 'activateToken']) {
    const found = new RegExp(`^\\s+(async )?${name}\\(`, 'm').exec(appSource);
    assert.ok(found, `${name} not found`);
    const body = appSource.slice(found.index, found.index + 1400);
    assert.ok(body.includes('closeAllRunStreams()'), `${name} must close run streams`);
  }

  // A handler error aborts the connection, not just drops the entry.
  opened = installFetch();
  m = monitor({ activeRuns: [liveRun()] });
  m.startRunStream(m.activeRuns[0]);
  await flush();
  opened[0].body.push('event: progress\ndata: {not json\n\n');
  await flush();
  assert.strictEqual(m.runStreams.r, undefined);
  assert.ok(opened[0].options.signal.aborted, 'an error must abort the connection');

  // The poll sees the run finished: the stream is closed, freeing the slot.
  opened = installFetch();
  m = monitor({ activeRuns: [liveRun()] });
  m.startRunStream(m.activeRuns[0]);
  await flush();
  opened[0].body.push(sse('progress', { run_id: 'r', status: 'RUNNING', steps: [], timing: null }));
  await flush();
  global.api = async (method, url) => {
    if (url.endsWith('/status')) return { status: 'PASSED' };
    if (url.endsWith('/progress')) return null;
    if (url.endsWith('/timing')) return { run_id: 'r', status: 'PASSED', server_time: T(9), steps: [] };
    return [];
  };
  await m.pollActiveRuns();
  assert.ok(opened[0].options.signal.aborted, 'a finished run must not keep its stream');
  assert.strictEqual(m.runStreams.r, undefined);
  await flush();

  // A stream silent for 30 s is closed so the poll takes over; 11 s only stops counting as live.
  global.api = async () => [];
  opened = installFetch();
  m = monitor({ activeRuns: [liveRun()] });
  m.startRunStream(m.activeRuns[0]);
  await flush();
  opened[0].body.push(sse('progress', { run_id: 'r', status: 'RUNNING', steps: [], timing: null }));
  await flush();
  const realNow = Date.now;
  pollApi(() => ({ run_id: 'r', status: 'RUNNING', server_time: T(5), steps: [] }));
  Date.now = () => realNow() + 11_000;
  await m.pollActiveRuns();
  assert.ok(!opened[0].options.signal.aborted, 'a stream quiet for 11 s is kept');
  Date.now = () => realNow() + 31_000;
  await m.pollActiveRuns();
  Date.now = realNow;
  assert.ok(opened[0].options.signal.aborted, 'a stream quiet for 31 s must be closed');
  assert.strictEqual(m.runStreams.r, undefined);
  await flush();

  // One that never sent anything is held to the same limit.
  opened = installFetch();
  m = monitor({ activeRuns: [liveRun()] });
  m.startRunStream(m.activeRuns[0]);
  await flush();
  pollApi(() => ({ run_id: 'r', status: 'RUNNING', server_time: T(5), steps: [] }));
  Date.now = () => realNow() + 31_000;
  await m.pollActiveRuns();
  Date.now = realNow;
  assert.ok(opened[0].options.signal.aborted, 'a stream that never delivered must be closed');
  await flush();
}

// --- HELD alert on the poll path ------------------------------------------
function testHeldAlertFallback() {
  const m = monitor({});
  const run = { run_id: 'r', status: 'RUNNING', _progress: { held_step: null } };
  assert.strictEqual(m.heldStepIndex(run), null);
  m.setRunTiming('r', {
    run_id: 'r', status: 'RUNNING', server_time: T(1),
    steps: [step({ step_index: 0 }), step({ step_index: 2, status: 'HELD' })],
  });
  assert.strictEqual(m.heldStepIndex(run), 2, 'derived from the timing when /progress has none');
  assert.strictEqual(m.heldStepIndex({ ...run, _progress: { held_step: 1 } }), 1, 'the stream value wins');
  assert.strictEqual(m.heldStepIndex({ run_id: 'x', _progress: null }), null);
  const html = require('node:fs').readFileSync(
    path.join(__dirname, '..', '..', 'frontend', 'partials', 'tab-monitor.html'), 'utf8');
  assert.ok(html.includes('heldStepIndex(run)'), 'the alert must read heldStepIndex');
  assert.ok(html.includes('Sequence held at step'), 'alert text is unchanged');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});