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

// Matrix file upload must not reference undefined `namespace` or mutate BO PDF state
const origFileReader = global.FileReader;
try {
  global.FileReader = class MockFileReader {
    readAsArrayBuffer() {
      const bytes = new Uint8Array([104, 101, 108, 108, 111]);
      this.onload({ target: { result: bytes.buffer } });
    }
  };
  const sentinelPreview = { a: { columns: ['id'] }, b: null };
  const sentinelError = { message: 'keep me', suggestedKey: null, tables: [] };
  c.boPdfPreview = sentinelPreview;
  c.boPdfError = sentinelError;
  c.handleMatrixFileUpload({ target: { files: [{ name: 'matrix.csv' }] } }, 'a');
  assert.strictEqual(c.matrixSourceA.fileName, 'matrix.csv');
  assert.strictEqual(c.matrixSourceA.fileB64, 'aGVsbG8=');
  assert.strictEqual(c.boPdfPreview, sentinelPreview);
  assert.strictEqual(c.boPdfError, sentinelError);
} finally {
  global.FileReader = origFileReader;
}

// Final Review Fix Wave tests: openCompareForJob, swapCompareSides, clearBOPdfPreview, tab-compare.html
const vm = require('node:vm');
require(path.join(ROOT, 'frontend', 'features', 'launch.js'));

function createAppInstance() {
  const noopSlice = () => ({});
  const featureNames = [
    'ETL_FEATURE_COMPARE', 'ETL_FEATURE_CONFIG', 'ETL_FEATURE_LAUNCH',
    'ETL_FEATURE_MONITOR', 'ETL_FEATURE_RUN_TIMING', 'ETL_FEATURE_HISTORY',
    'ETL_FEATURE_CI_RUNS', 'ETL_FEATURE_ADAPTERS', 'ETL_FEATURE_AWS',
    'ETL_FEATURE_REPORTS', 'ETL_FEATURE_DIFFERENCES', 'ETL_FEATURE_CONTRACTS',
    'ETL_FEATURE_SCHEDULER_REPORTS', 'ETL_FEATURE_LOGS', 'ETL_FEATURE_SEQUENCES',
    'ETL_FEATURE_FILE_SERVERS', 'ETL_FEATURE_IMPORT_EXPORT', 'ETL_FEATURE_BULK_COMPARE',
  ];
  const sandbox = {
    window: {
      ETL_API_BASE: '',
      ETL_APP_CONFIG: {},
      ETL_HELP_METHODS: { showHelp() {}, initKeyboardShortcuts() {} },
    },
    document: { addEventListener() {} },
    localStorage: { getItem: () => null, setItem() {} },
    sessionStorage: { getItem: () => null, setItem() {} },
    console,
    Date,
    Object,
    Array,
    String,
    Number,
    Boolean,
    Math,
    JSON,
    Set,
    Map,
    RegExp,
    Error,
    parseInt,
    parseFloat,
    isNaN,
    encodeURIComponent,
  };
  for (const name of featureNames) {
    sandbox[name] = name === 'ETL_FEATURE_COMPARE'
      ? window.ETL_FEATURE_COMPARE
      : name === 'ETL_FEATURE_LAUNCH'
        ? window.ETL_FEATURE_LAUNCH
        : noopSlice;
  }
  const appCode = fs.readFileSync(path.join(ROOT, 'frontend', 'app.js'), 'utf8');
  vm.runInNewContext(appCode, sandbox, { filename: 'frontend/app.js' });
  const instance = sandbox.app();
  instance.toast = () => {};
  return instance;
}

// 1. openCompareForJob hydrates boPdfRewrap and resets stale PDF preview/error state
{
  const app = createAppInstance();
  app.boPdfPreview = { a: { pages: 1 }, b: { pages: 2 } };
  app.boPdfTableOptions = { a: [{ index: 1 }], b: [{ index: 2 }] };
  app.boPdfPreviewLoading = { a: true, b: true };
  app.boPdfError = { message: 'stale error', suggestedKey: 'id', tables: [] };
  app.boPdfRewrap = true;

  app.openCompareForJob({
    name: 'saved-bo-pdf-compare',
    job_type: 'compare',
    params: {
      compare_type: 'bo',
      request: {
        source_a: { source_type: 'path', file_path: '/a.pdf', format: 'pdf', pdf: { table: 1, number_locale: 'eu', date_order: 'dmy', fill_down: false } },
        source_b: { source_type: 'path', file_path: '/b.pdf', format: 'pdf', pdf: { table: 2, number_locale: 'en', date_order: null, fill_down: true } },
        key_columns: ['Region'],
        exclude_columns: [],
        pdf_rewrap: false,
      },
    },
  });

  assert.strictEqual(app.boPdfRewrap, false);
  assert.deepStrictEqual(app.boPdfPreview, { a: null, b: null });
  assert.deepStrictEqual(app.boPdfTableOptions, { a: [], b: [] });
  assert.deepStrictEqual(app.boPdfPreviewLoading, { a: false, b: false });
  assert.strictEqual(app.boPdfError, null);
}

// 2. openCompareForJob resets boSourceB.pdf on non-compare BO jobs so switching to Path .pdf never throws
{
  const app = createAppInstance();
  app.boPdfPreview = { a: { pages: 1 }, b: { pages: 2 } };
  app.boPdfTableOptions = { a: [{ index: 1 }], b: [{ index: 2 }] };
  app.boPdfPreviewLoading = { a: true, b: false };
  app.boPdfError = { message: 'stale error', suggestedKey: null, tables: [] };

  app.openCompareForJob({
    name: 'bo-report-job',
    job_type: 'bo_report',
    key_columns: ['id'],
    params: { report_id: '100', bo_report_id: '2' },
  });

  assert.deepStrictEqual(app.boSourceB.pdf, app._defaultBOPdfOptions());
  assert.strictEqual(app.boSourceB.pdf.numberLocale, 'en');
  assert.deepStrictEqual(app.boPdfPreview, { a: null, b: null });
  assert.deepStrictEqual(app.boPdfTableOptions, { a: [], b: [] });
  assert.deepStrictEqual(app.boPdfPreviewLoading, { a: false, b: false });
  assert.strictEqual(app.boPdfError, null);
}

// 3. swapCompareSides swaps boPdfPreview, boPdfTableOptions, boPdfPreviewLoading and clears boPdfError
{
  const app = createAppInstance();
  const prevA = { pages: 1, chosen: { index: 1 } };
  const prevB = { pages: 3, chosen: { index: 2 } };
  const tblsA = [{ index: 1, rows: 5 }];
  const tblsB = [{ index: 2, rows: 10 }];
  app.boSourceAType = 'path';
  app.boSourceBType = 'upload';
  app.boSourceA.filePath = '/a.pdf';
  app.boSourceB.fileName = 'b.pdf';
  app.boPdfPreview = { a: prevA, b: prevB };
  app.boPdfTableOptions = { a: tblsA, b: tblsB };
  app.boPdfPreviewLoading = { a: true, b: false };
  app.boPdfError = { message: 'old err', suggestedKey: null, tables: [] };

  app.swapCompareSides();

  assert.strictEqual(app.boPdfPreview.a, prevB);
  assert.strictEqual(app.boPdfPreview.b, prevA);
  assert.strictEqual(app.boPdfTableOptions.a, tblsB);
  assert.strictEqual(app.boPdfTableOptions.b, tblsA);
  assert.strictEqual(app.boPdfPreviewLoading.a, false);
  assert.strictEqual(app.boPdfPreviewLoading.b, true);
  assert.strictEqual(app.boPdfError, null);
}

// 4. clearBOPdfPreview clears side preview, table options, pdf.table, and boPdfError
{
  const app = createAppInstance();
  assert.strictEqual(typeof app.clearBOPdfPreview, 'function');
  app.boSourceA.pdf = { table: 2, numberLocale: 'eu', dateOrder: 'dmy', fillDown: false };
  app.boPdfPreview = { a: { pages: 1 }, b: { pages: 2 } };
  app.boPdfTableOptions = { a: [{ index: 2 }], b: [{ index: 1 }] };
  app.boPdfError = { message: 'err', suggestedKey: null, tables: [] };

  app.clearBOPdfPreview('a');

  assert.strictEqual(app.boPdfPreview.a, null);
  assert.deepStrictEqual(app.boPdfPreview.b, { pages: 2 });
  assert.deepStrictEqual(app.boPdfTableOptions.a, []);
  assert.deepStrictEqual(app.boPdfTableOptions.b, [{ index: 1 }]);
  assert.strictEqual(app.boSourceA.pdf.table, null);
  assert.strictEqual(app.boSourceA.pdf.numberLocale, 'eu');
  assert.strictEqual(app.boPdfError, null);
}

// 5. tab-compare.html and built index.html include result-row "(as printed)" badge and path @input handler
{
  const compareHtml = fs.readFileSync(path.join(ROOT, 'frontend', 'partials', 'tab-compare.html'), 'utf8');
  assert.ok(
    compareHtml.includes('boStatusLabel({ status: r.status, results: [r] })'),
    'tab-compare.html result row badge must use boStatusLabel({ status: r.status, results: [r] })',
  );
  assert.ok(
    compareHtml.includes("clearBOPdfPreview('a')") && compareHtml.includes("clearBOPdfPreview('b')"),
    'tab-compare.html path inputs must clear PDF preview on @input',
  );

  const builtHtml = fs.readFileSync(path.join(ROOT, 'frontend', 'index.html'), 'utf8');
  assert.ok(
    builtHtml.includes('boStatusLabel({ status: r.status, results: [r] })'),
    'frontend/index.html result row badge must include boStatusLabel({ status: r.status, results: [r] })',
  );
  assert.ok(
    builtHtml.includes("clearBOPdfPreview('a')") && builtHtml.includes("clearBOPdfPreview('b')"),
    'frontend/index.html path inputs must include clearBOPdfPreview on @input',
  );
}

console.log('ok');
