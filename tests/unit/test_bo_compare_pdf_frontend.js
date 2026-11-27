'use strict';
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
global.window = global;
global.APP_CONFIG = {};
require(path.join(ROOT, 'frontend', 'features', 'history.js'));

const history = window.ETL_FEATURE_HISTORY();

assert.strictEqual(typeof history.runStatusLabel, 'function');
assert.strictEqual(
  history.runStatusLabel({ status: 'PASSED', compared_as: 'printed' }),
  'PASSED (as printed)',
);
assert.strictEqual(
  history.runStatusLabel({ status: 'FAILED', compared_as: null }),
  'FAILED',
);
assert.strictEqual(
  history.runStatusLabel({ status: 'PASSED' }),
  'PASSED',
);

assert.strictEqual(typeof history.resultStatusLabel, 'function');
assert.strictEqual(
  history.resultStatusLabel({ status: 'PASSED', mismatch_summary: { pdf_sources: { a: {}, b: {} } } }),
  'PASSED (as printed)',
);
assert.strictEqual(
  history.resultStatusLabel({ status: 'FAILED', effective_status: 'PASSED', mismatch_summary: null }, { compared_as: 'printed' }),
  'PASSED (as printed)',
);
assert.strictEqual(
  history.resultStatusLabel({ status: 'FAILED', effective_status: 'FAILED' }, { compared_as: null }),
  'FAILED',
);
assert.strictEqual(
  history.resultStatusLabel(null, { compared_as: 'printed' }),
  '',
);

const historyHtml = fs.readFileSync(path.join(ROOT, 'frontend', 'partials', 'tab-history.html'), 'utf8');
assert.ok(
  historyHtml.includes('runStatusLabel(run)') &&
  historyHtml.includes('runStatusLabel(selectedRun)') &&
  historyHtml.includes('resultStatusLabel(r, selectedRun)') &&
  historyHtml.includes('runStatusLabel(compareResult.run_a)') &&
  historyHtml.includes('runStatusLabel(compareResult.run_b)'),
  'History list, detail, result rows, and compare headers must use runStatusLabel / resultStatusLabel',
);

const indexHtml = fs.readFileSync(path.join(ROOT, 'frontend', 'index.html'), 'utf8');
assert.ok(
  indexHtml.includes('runStatusLabel(run)') &&
  indexHtml.includes('runStatusLabel(selectedRun)') &&
  indexHtml.includes('resultStatusLabel(r, selectedRun)') &&
  indexHtml.includes('runStatusLabel(compareResult.run_a)') &&
  indexHtml.includes('runStatusLabel(compareResult.run_b)'),
  'Built index.html must include runStatusLabel and resultStatusLabel status badges',
);

console.log('ok - history status badge renders "(as printed)" when compared_as === "printed"');
