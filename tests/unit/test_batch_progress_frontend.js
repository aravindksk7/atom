'use strict';
const assert = require('node:assert');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
global.window = global;
global.APP_CONFIG = {};

const storage = new Map();
global.localStorage = {
  getItem(key) { return storage.has(key) ? storage.get(key) : null; },
  setItem(key, value) { storage.set(key, String(value)); },
};
global.location = { search: '', pathname: '/', hash: '#launch' };
global.history = { replaceState() {} };
require(path.join(ROOT, 'frontend', 'features', 'launch.js'));
require(path.join(ROOT, 'frontend', 'features', 'sequences.js'));

function component(overrides = {}) {
  return Object.assign(window.ETL_FEATURE_LAUNCH(), {
    toast() {},
    runStepsCache: {},
  }, overrides);
}

async function main() {
  storage.clear();
  const recent = component();
  for (let i = 1; i <= 12; i++) recent.rememberRecentBatch(`batch-${i}`);
  assert.deepStrictEqual(recent.recentBatchIds, [
    'batch-12', 'batch-11', 'batch-10', 'batch-9', 'batch-8',
    'batch-7', 'batch-6', 'batch-5', 'batch-4', 'batch-3',
  ]);
  recent.rememberRecentBatch('batch-8');
  assert.deepStrictEqual(recent.recentBatchIds.slice(0, 3), ['batch-8', 'batch-12', 'batch-11']);

  storage.set('etl_recent_batches', JSON.stringify(['stored', 'missing']));
  global.location.search = '?batch_id=url-batch';
  const requested = [];
  global.api = async (_method, url) => {
    requested.push(url);
    return { batch_id: 'url-batch', status: 'COMPLETED', runs: [] };
  };
  const fromUrl = component();
  await fromUrl.rehydrateBatchProgress();
  assert.strictEqual(requested[0], '/api/run-batches/url-batch');
  assert.strictEqual(fromUrl.batchProgress.batch_id, 'url-batch');
  assert.strictEqual(fromUrl.batchPollingTimer, null);
  assert.deepStrictEqual(fromUrl.recentBatchIds, ['stored', 'missing']);

  global.location.search = '';
  storage.set('etl_recent_batches', JSON.stringify(['missing', 'running']));
  global.api = async (_method, url) => {
    if (url.endsWith('/missing')) {
      const error = new Error('Not found');
      error.status = 404;
      throw error;
    }
    return { batch_id: 'running', status: 'RUNNING', runs: [] };
  };
  const rehydrated = component();
  const originalSetTimeout = global.setTimeout;
  global.setTimeout = () => 17;
  await rehydrated.rehydrateBatchProgress();
  global.setTimeout = originalSetTimeout;
  assert.strictEqual(rehydrated.batchProgress.batch_id, 'running');
  assert.deepStrictEqual(rehydrated.recentBatchIds, ['running']);
  assert.strictEqual(rehydrated.batchPollingTimer, 17);

  storage.set('etl_recent_batches', JSON.stringify(['transient']));
  let rehydrateCalls = 0;
  global.api = async () => {
    rehydrateCalls++;
    if (rehydrateCalls === 1) throw new Error('temporary outage');
    return { batch_id: 'transient', status: 'RUNNING', runs: [] };
  };
  const transient = component();
  let retryCallback = null;
  global.setTimeout = (callback) => {
    retryCallback = callback;
    return 19;
  };
  await transient.rehydrateBatchProgress();
  assert.strictEqual(transient.selectedBatchId, 'transient');
  assert.strictEqual(transient.batchPollingTimer, 19);
  assert.ok(retryCallback);
  await retryCallback();
  global.setTimeout = originalSetTimeout;
  assert.strictEqual(transient.batchProgress.batch_id, 'transient');
  assert.strictEqual(rehydrateCalls, 2);

  let pollingCalls = 0;
  global.api = async () => {
    pollingCalls++;
    if (pollingCalls === 1) throw new Error('temporary outage');
    return { batch_id: 'retrying', status: 'RUNNING', runs: [] };
  };
  const retrying = component();
  retrying.selectedBatchId = 'retrying';
  global.setTimeout = () => 23;
  await retrying.pollBatch('retrying');
  global.setTimeout = originalSetTimeout;
  assert.strictEqual(retrying.batchPollingTimer, 23);

  const refreshedRunIds = [];
  const refreshTimers = [];
  global.api = async (_method, url) => {
    assert.strictEqual(url, '/api/run-batches/refreshing');
    return {
      batch_id: 'refreshing',
      status: 'RUNNING',
      runs: [
        { run_id: 'run-expanded', status: 'RUNNING' },
        { run_id: 'run-collapsed', status: 'RUNNING' },
        { run_id: null, status: 'PENDING' },
      ],
    };
  };
  const refreshing = component({
    selectedBatchId: 'refreshing',
    expandedBatchRuns: { 'run-expanded': true, 'run-collapsed': false },
    async loadRunSteps(runId) { refreshedRunIds.push(runId); },
  });
  global.setTimeout = (callback) => {
    refreshTimers.push(callback);
    return refreshTimers.length;
  };
  await refreshing.pollBatch('refreshing');
  global.setTimeout = originalSetTimeout;
  assert.deepStrictEqual(refreshedRunIds, ['run-expanded']);
  assert.strictEqual(refreshTimers.length, 1);

  const refreshOutcomes = [false, true];
  const recovering = component({
    selectedBatchId: 'recovering',
    expandedBatchRuns: { 'run-expanded': true },
    async loadRunSteps() { return refreshOutcomes.shift(); },
  });
  global.api = async () => ({
    batch_id: 'recovering',
    status: 'RUNNING',
    runs: [{ run_id: 'run-expanded', status: 'RUNNING' }],
  });
  global.setTimeout = () => 31;
  await recovering.pollBatch('recovering');
  assert.strictEqual(recovering.batchStepsUnavailable['run-expanded'], true);
  await recovering.pollBatch('recovering');
  global.setTimeout = originalSetTimeout;
  assert.strictEqual(recovering.batchStepsUnavailable['run-expanded'], undefined);

  const terminalRefreshedRunIds = [];
  const terminalRefreshTimers = [];
  global.api = async (_method, url) => {
    assert.strictEqual(url, '/api/run-batches/finished');
    return {
      batch_id: 'finished',
      status: 'COMPLETED',
      runs: [
        { run_id: 'run-expanded', status: 'COMPLETED' },
        { run_id: 'run-collapsed', status: 'COMPLETED' },
        { run_id: null, status: 'COMPLETED' },
      ],
    };
  };
  const finished = component({
    selectedBatchId: 'finished',
    expandedBatchRuns: { 'run-expanded': true, 'run-collapsed': false },
    async loadRunSteps(runId) { terminalRefreshedRunIds.push(runId); },
  });
  global.setTimeout = (callback) => {
    terminalRefreshTimers.push(callback);
    return terminalRefreshTimers.length;
  };
  await finished.pollBatch('finished');
  global.setTimeout = originalSetTimeout;
  assert.deepStrictEqual(terminalRefreshedRunIds, ['run-expanded']);
  assert.strictEqual(terminalRefreshTimers.length, 0);
  assert.strictEqual(finished.batchPollingTimer, null);

  global.api = async () => {
    const error = new Error('Not found');
    error.status = 404;
    throw error;
  };
  storage.set('etl_recent_batches', JSON.stringify(['deleted']));
  const deleted = component();
  deleted.selectedBatchId = 'deleted';
  global.setTimeout = () => 29;
  await deleted.pollBatch('deleted');
  global.setTimeout = originalSetTimeout;
  assert.strictEqual(deleted.batchPollingTimer, null);
  assert.deepStrictEqual(deleted.recentBatchIds, []);
  assert.strictEqual(deleted.batchProgressError, 'Batch not found — it may have been deleted');

  let stepCalls = 0;
  const expanded = component({
    async loadRunSteps(runId) {
      stepCalls++;
      this.runStepsCache[runId] = [];
    },
  });
  await expanded.toggleBatchRunSteps(null);
  assert.strictEqual(stepCalls, 0);
  await expanded.toggleBatchRunSteps('run-1');
  assert.strictEqual(stepCalls, 1);
  assert.strictEqual(expanded.expandedBatchRuns['run-1'], true);
  assert.strictEqual(expanded.batchStepsUnavailable['run-1'], true);
  await expanded.toggleBatchRunSteps('run-1');
  assert.strictEqual(expanded.expandedBatchRuns['run-1'], false);

  let earlyCalls = 0;
  const earlyEmpty = component({
    batchProgress: { status: 'RUNNING' },
    async loadRunSteps(runId) {
      earlyCalls++;
      this.runStepsCache[runId] = [];
      return true;
    },
  });
  await earlyEmpty.toggleBatchRunSteps('run-early');
  await earlyEmpty.toggleBatchRunSteps('run-early');
  await earlyEmpty.toggleBatchRunSteps('run-early');
  assert.strictEqual(earlyCalls, 2);
  assert.strictEqual(earlyEmpty.batchStepsUnavailable['run-early'], undefined);

  const failedSteps = component({
    async loadRunSteps(runId) {
      this.runStepsCache[runId] = [];
      return false;
    },
  });
  await failedSteps.toggleBatchRunSteps('run-failed');
  assert.strictEqual(failedSteps.batchStepsUnavailable['run-failed'], true);

  let replacedUrl = '';
  global.history.replaceState = (_state, _title, url) => { replacedUrl = url; };
  global.location.pathname = '/app';
  global.location.search = '?other=1';
  global.location.hash = '#jobs';
  const selected = component({ async pollBatch() {} });
  await selected.selectRecentBatch('chosen');
  assert.strictEqual(replacedUrl, '/app?other=1&batch_id=chosen#jobs');

  storage.clear();
  global.location.search = '';
  let sequencePolled = '';
  global.api = async (method, url) => {
    assert.strictEqual(method, 'POST');
    assert.strictEqual(url, '/api/sequences/7/launch-batch');
    return { batch_id: 'sequence-batch', completed: 0, iterations: 2 };
  };
  const sequence = Object.assign(component(), window.ETL_FEATURE_SEQUENCES(), {
    launchSequenceModal: {
      sequence_id: 7,
      source_env: 'dev',
      target_env: '',
      variableOverridesRaw: '',
      repeat_enabled: true,
    },
    _batchOptionsFromModal() { return {}; },
    pollBatch(id) { sequencePolled = id; },
  });
  await sequence.launchSequence();
  assert.deepStrictEqual(JSON.parse(storage.get('etl_recent_batches')), ['sequence-batch']);
  assert.strictEqual(sequence.selectedBatchId, 'sequence-batch');
  assert.strictEqual(sequencePolled, 'sequence-batch');

  console.log('ok - batch progress frontend state and polling');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
