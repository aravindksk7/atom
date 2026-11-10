'use strict';
// A multi-file result stores "N file(s) across M pair(s)" on each side, which read
// as one phrase repeated when the two sides were joined.
const assert = require('node:assert');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
global.window = global;
global.APP_CONFIG = {};
require(path.join(ROOT, 'frontend', 'features', 'history.js'));

const { resultFilesLabel } = window.ETL_FEATURE_HISTORY();

assert.strictEqual(
  resultFilesLabel('63 file(s) across 63 pair(s)', '63 file(s) across 63 pair(s)'),
  '63 → 63 files across 63 pairs',
);
assert.strictEqual(
  resultFilesLabel('4 file(s) across 2 pair(s)', '2 file(s) across 2 pair(s)'),
  '4 → 2 files across 2 pairs',
);
assert.strictEqual(
  resultFilesLabel('1 file(s) across 1 pair(s)', '1 file(s) across 1 pair(s)'),
  '1 → 1 file across 1 pair',
);
assert.strictEqual(resultFilesLabel('a.csv', 'b.csv'), 'a.csv → b.csv');
assert.strictEqual(resultFilesLabel('a.csv', null), 'a.csv → N/A');
assert.strictEqual(resultFilesLabel(null, 'b.csv'), 'N/A → b.csv');
assert.strictEqual(resultFilesLabel(null, undefined), '');
console.log('ok - result files label');
