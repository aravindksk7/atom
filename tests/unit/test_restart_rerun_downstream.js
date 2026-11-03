'use strict';
// Restart sends the option only when ticked, so the default request stays bodyless.
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const history = fs.readFileSync(path.join(ROOT, 'frontend', 'features', 'history.js'), 'utf8');
const partial = fs.readFileSync(path.join(ROOT, 'frontend', 'partials', 'tab-history.html'), 'utf8');

assert.ok(history.includes('this.restartRerunDownstream ? { rerun_downstream: true } : undefined'));
assert.ok(history.includes('this.restartRerunDownstream = false;'));
assert.ok(partial.includes('data-testid="history-restart-rerun-downstream"'));
console.log('ok - restart option is wired');
