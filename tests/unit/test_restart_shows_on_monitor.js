'use strict';
// A restart can finish before the Monitor's next poll, and that poll only picks up
// live runs, so restartRun has to put the new run on the Monitor itself.
const assert = require('node:assert');
const path = require('node:path');

global.window = global;
require(path.join(__dirname, '..', '..', 'frontend', 'features', 'history.js'));

function component(runs, activeRuns = []) {
  const calls = { streams: [] };
  const slice = window.ETL_FEATURE_HISTORY();
  return Object.assign(slice, {
    runs,
    activeRuns,
    restartRerunDownstream: false,
    loadRuns: async () => {},
    viewRunDetail: async () => {},
    startRunStream: (run) => calls.streams.push(run.run_id),
    toast: () => {},
    calls,
  });
}

async function main() {
  global.api = async () => ({ run_id: 'new' });

  const run = { run_id: 'new', status: 'RUNNING' };
  const older = { run_id: 'old', status: 'FAILED' };
  const shown = component([run, older], [older]);
  await shown.restartRun('old');
  assert.deepStrictEqual(shown.activeRuns.map((r) => r.run_id), ['new', 'old']);
  assert.deepStrictEqual(shown.calls.streams, ['new']);

  // A run already on the Monitor isn't listed twice.
  const again = component([run], [run]);
  await again.restartRun('old');
  assert.strictEqual(again.activeRuns.length, 1);

  // A run missing from the loaded page still opens its detail, and doesn't break.
  const missing = component([], []);
  await missing.restartRun('old');
  assert.deepStrictEqual(missing.activeRuns, []);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
