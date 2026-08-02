// Behaviour tests for the shared mismatch search engine. Run: node tests/unit/test_diff_search_engine.js
// Driven from test_diff_search_engine.py so it runs in the normal pytest suite.
'use strict';
const assert = require('node:assert');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
global.window = global;
require(path.join(ROOT, 'etl_framework', 'reporting', 'templates', '_diff_search.js'));

const ROWS = [
  { test_name: 'orders', column_name: 'amount', mismatch_type: 'value_diff',
    key_values: { id: 1042 }, source_value: '100.00', target_value: '100.01' },
  { test_name: 'orders', column_name: '<row>', mismatch_type: 'missing_in_target',
    key_values: { id: 7 }, source_value: 'present', target_value: 'missing' },
  { test_name: 'invoices', column_name: 'status', mismatch_type: 'value_diff',
    key_values: { id: 9 }, source_value: 'two words', target_value: null },
  { test_name: 'invoices', column_name: 'notes', mismatch_type: 'value_diff',
    key_values: { id: 11 }, source_value: 'words two', target_value: 'x' },
];

const find = (query) =>
  ROWS.filter((row) => matchesDiffQuery(diffSearchFields(row), parseDiffQuery(query)))
      .map((row) => row.column_name);

const cases = [
  ['', ['amount', '<row>', 'status', 'notes']],
  ['amount', ['amount']],                         // bare term hits the column
  ['100.01', ['amount']],                         // ...and a target value
  ['1042', ['amount']],                           // ...and a key value
  ['col:amount', ['amount']],
  ['column:status', ['status']],
  ['col:orders', []],                             // scoping really scopes
  ['test:orders', ['amount', '<row>']],
  ['type:missing', ['<row>']],
  ['key:9', ['status']],
  ['src:present', ['<row>']],
  ['tgt:missing', ['<row>']],
  ['src:100.01', []],                             // value is on the target side
  ['val:100.01', ['amount']],
  ['"two words"', ['status']],                    // phrase: adjacency required
  ['two words', ['status', 'notes']],             // unquoted: two terms ANDed, any order
  ['orders -type:missing', ['amount']],
  ['-col:amount', ['<row>', 'status', 'notes']],
  ['AMOUNT', ['amount']],                         // case-insensitive
  ['col:AMOUNT', ['amount']],
  ['nothingmatches', []],
];

// Multi-file rows carry their file pair under __pair__ inside key_values. It is
// pairing metadata, not row identity, so it splits into its own field.
const PAIRED = [
  { column_name: 'amount', mismatch_type: 'value_diff',
    key_values: { __pair__: { region: 'east' }, id: 1 }, source_value: '100', target_value: '101' },
  { column_name: '<row>', mismatch_type: 'missing_in_target',
    key_values: JSON.stringify({ __pair__: { region: 'west' }, id: 9 }),
    source_value: 'present', target_value: 'missing' },
];
const findPaired = (query) =>
  PAIRED.filter((row) => matchesDiffQuery(diffSearchFields(row), parseDiffQuery(query)))
        .map((row) => row.column_name);

// Object and JSON-string key_values behave identically.
assert.strictEqual(diffPairLabel(PAIRED[0].key_values), 'region=east');
assert.strictEqual(diffPairLabel(PAIRED[1].key_values), 'region=west');
assert.strictEqual(diffKeyWithoutPair(PAIRED[0].key_values), '{"id":1}');
assert.strictEqual(diffKeyWithoutPair(PAIRED[1].key_values), '{"id":9}');
// Rows with no pair are untouched.
assert.strictEqual(diffPairLabel({ id: 3 }), '');
assert.strictEqual(diffPairLabel(null), '');

assert.deepStrictEqual(findPaired('pair:west'), ['<row>']);
assert.deepStrictEqual(findPaired('west'), ['<row>']);       // bare term still reaches it
assert.deepStrictEqual(findPaired('key:west'), []);          // key: excludes pairing metadata
assert.deepStrictEqual(findPaired('key:9'), ['<row>']);
assert.deepStrictEqual(findPaired('pair:east -type:missing_in_target'), ['amount']);

let failures = 0;
for (const [query, expected] of cases) {
  const actual = find(query);
  try {
    assert.deepStrictEqual(actual, expected);
  } catch (e) {
    failures++;
    console.error(`FAIL  ${JSON.stringify(query)}\n  expected ${JSON.stringify(expected)}\n  actual   ${JSON.stringify(actual)}`);
  }
}

// A colon that is not a known field must stay literal text, or values holding a
// URL or a timestamp become unsearchable.
const urlRow = { column_name: 'endpoint', source_value: 'http://host/a', target_value: 'http://host/b' };
assert.deepStrictEqual(parseDiffQuery('http://host').map((t) => t.field), ['any']);
assert.ok(matchesDiffQuery(diffSearchFields(urlRow), parseDiffQuery('http://host')));

// Null values are searchable as empty, never as the string "null".
const nullRow = { column_name: 'note', source_value: null, target_value: 'x' };
assert.ok(!matchesDiffQuery(diffSearchFields(nullRow), parseDiffQuery('src:null')));

// Highlight needles drop excluded terms.
assert.deepStrictEqual(diffQueryNeedles(parseDiffQuery('amount -status')), ['amount']);

if (failures) {
  console.error(`${failures} case(s) failed`);
  process.exit(1);
}
console.log(`ok - ${cases.length} query cases + 15 assertions`);
