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

const historyHtml = fs.readFileSync(path.join(ROOT, 'frontend', 'partials', 'tab-history.html'), 'utf8');
assert.ok(
  historyHtml.includes('runStatusLabel(run)') && historyHtml.includes('runStatusLabel(selectedRun)'),
  'History list and detail status badges must use runStatusLabel',
);

const indexHtml = fs.readFileSync(path.join(ROOT, 'frontend', 'index.html'), 'utf8');
assert.ok(
  indexHtml.includes('runStatusLabel(run)') && indexHtml.includes('runStatusLabel(selectedRun)'),
  'Built index.html must include runStatusLabel status badges',
);

console.log('ok - history status badge renders "(as printed)" when compared_as === "printed"');
