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

// Task 13: BO compare PDF frontend state & helpers
require(path.join(ROOT, 'frontend', 'features', 'compare.js'));
const c = window.ETL_FEATURE_COMPARE();
const src = {
  ...c.boSourceA,
  fileName: 'Prod.PDF',
  fileB64: 'eA==',
  pdf: { table: '2', numberLocale: 'eu', dateOrder: null, fillDown: true },
};
assert.deepStrictEqual(c._buildBOSource('upload', src), {
  source_type: 'upload',
  file_content_b64: 'eA==',
  file_name: 'Prod.PDF',
  format: 'pdf',
  pdf: { table: 2, number_locale: 'eu', date_order: null, fill_down: true },
});
assert.strictEqual(c._buildBOSource('upload', { ...src, fileName: 'a.csv' }).format, undefined);
const { type, src: back } = c._hydrateBOSourceFromConfig({
  source_type: 'path',
  file_path: '/r/p.pdf',
  format: 'pdf',
  pdf: { table: 2, number_locale: 'eu', date_order: 'dmy', fill_down: false },
});
assert.deepStrictEqual(
  [type, back.pdf],
  ['path', { table: 2, numberLocale: 'eu', dateOrder: 'dmy', fillDown: false }],
);
Object.assign(c, {
  boSourceAType: 'upload',
  boSourceA: { ...src },
  boSourceBType: 'upload',
  boSourceB: { ...src, fileName: 'qa.xlsx' },
});
assert.strictEqual(c._boPdfMismatch(), true);
Object.assign(c, {
  boSourceBType: 'path',
  boSourceB: { ...c.boSourceB, filePath: '/r/qa.pdf' },
  boPdfRewrap: false,
});
assert.strictEqual(c._boPdfMismatch(), false);
assert.strictEqual(c._buildBOComparePayload().pdf_rewrap, false);
assert.strictEqual(
  c.boStatusLabel({ status: 'PASSED', results: [{ mismatch_summary: { pdf_sources: {} } }] }),
  'PASSED (as printed)',
);
assert.strictEqual(
  c.boStatusLabel({ status: 'PASSED', results: [{ mismatch_summary: {} }] }),
  'PASSED',
);
c._showPdfError({ message: 'set key columns for a PDF compare', suggested_key: 'Group' });
assert.deepStrictEqual(c.boPdfError, {
  message: 'set key columns for a PDF compare',
  suggestedKey: 'Group',
  tables: [],
});
c._showPdfError('PDF reader busy');
assert.strictEqual(c.boPdfError.message, 'PDF reader busy');
console.log('ok');
