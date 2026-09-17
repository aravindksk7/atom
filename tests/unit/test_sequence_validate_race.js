'use strict';
// Regression test: rapidly toggling step dependencies (or adding/removing
// steps) in the sequence editor fires one POST /api/sequences/validate per
// toggle without waiting for the previous call. If an earlier request's
// response arrives AFTER a later one (out-of-order network resolution),
// validateSequenceSteps() must not let the stale response clobber the
// result of the latest edit -- otherwise sequenceIssues can get stuck
// non-empty forever, sequenceIsValid stays false, and Save silently no-ops
// with no error shown to the user. This surfaces almost exclusively on
// sequences with many steps, where wiring dependencies means firing many
// validate calls in quick succession.
const assert = require('node:assert');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
global.window = global;
global.APP_CONFIG = {};
require(path.join(ROOT, 'frontend', 'features', 'sequences.js'));

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  const seq = window.ETL_FEATURE_SEQUENCES();
  seq.sequenceSteps = [{ step_id: 'a', job_name: 'a', depends_on: [] }];

  // Call #1 (stale): fired first, resolves LAST, carries issues matching an
  // earlier, since-fixed state.
  // Call #2 (fresh): fired second (as if the user toggled again right
  // after), resolves FIRST, carries the correct issues: none.
  let callCount = 0;
  global.api = async (_method, _url, _body) => {
    callCount += 1;
    if (callCount === 1) {
      await delay(50);
      return { errors: [{ step_id: 'a', field: 'depends_on', message: 'stale error' }], order: [] };
    }
    await delay(5);
    return { errors: [], order: ['a'] };
  };

  seq.validateSequenceSteps();       // call #1, in flight, not awaited (mirrors real callers)
  seq.validateSequenceSteps();       // call #2, in flight, not awaited

  await delay(100);                  // let both settle

  assert.strictEqual(callCount, 2);
  assert.deepStrictEqual(
    seq.sequenceIssues, [],
    'a stale, out-of-order validate response must not overwrite the latest result'
  );
  assert.strictEqual(seq.sequenceIsValid, true, 'Save must not be blocked by a stale validation race');

  console.log('ok - sequence validate race does not clobber latest result');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
