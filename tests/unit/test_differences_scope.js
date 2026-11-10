'use strict';
// The Differences metrics and charts follow the selected test, and fall back to
// the whole run when none is selected.
const assert = require('node:assert');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
global.window = global;
global.APP_CONFIG = {};
require(path.join(ROOT, 'frontend', 'features', 'differences.js'));

const calls = [];
let respond = (url) => ({ url });
global.api = async (method, url) => { calls.push(url); return respond(url); };
global.apiPaged = async () => ({ items: [], total: 0, storedComplete: true });

function explorer() {
  const diff = window.ETL_FEATURE_DIFFERENCES();
  diff.$nextTick = () => {};
  diff.toast = () => {};
  diff.diffRunId = 'r1';
  diff.diffRunDetail = { results: [{ id: 7, column_stats: [{ column: 'fee' }] }] };
  return diff;
}

(async () => {
  let diff = explorer();
  calls.length = 0;
  await diff.selectDifferenceResult('7');
  assert.deepStrictEqual(calls, ['/api/runs/r1/mismatches/insights?result_id=7']);
  assert.deepStrictEqual(diff.diffColumnOptions, ['fee']);
  console.log('ok - selecting a test scopes the insights to it');

  calls.length = 0;
  await diff.selectDifferenceResult('');
  assert.deepStrictEqual(calls, ['/api/runs/r1/mismatches/insights']);
  console.log('ok - clearing the test returns to the whole run');

  diff = explorer();
  const pending = [];
  respond = (url) => new Promise((resolve) => pending.push(() => resolve({ url })));
  const first = diff.selectDifferenceResult('7');
  const second = diff.selectDifferenceResult('');
  pending[1]();
  await second;
  pending[0]();
  await first;
  assert.strictEqual(diff.diffInsights.url, '/api/runs/r1/mismatches/insights');
  console.log('ok - a slow response for an earlier selection is ignored');
})().catch((err) => { console.error(err); process.exit(1); });
