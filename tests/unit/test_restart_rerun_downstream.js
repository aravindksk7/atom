'use strict';
// Restart sends the option only when ticked, so the default request stays bodyless.
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const history = fs.readFileSync(path.join(ROOT, 'frontend', 'features', 'history.js'), 'utf8');
const partial = fs.readFileSync(path.join(ROOT, 'frontend', 'partials', 'tab-history.html'), 'utf8');

assert.ok(history.includes('this.restartRerunDownstream ? { rerun_downstream: true } : undefined'));
// The option is cleared only when a different run is viewed, so refreshing the
// same run (after Set baseline, say) keeps what the user ticked.
assert.ok(history.includes('if (runId !== this.selectedRun?.run_id) this.restartRerunDownstream = false;'));
assert.ok(
  history.indexOf('this.restartRerunDownstream = false;') < history.indexOf('this.selectedRun = await api(\'GET\', `/api/runs/${runId}`)'),
  'the comparison must run before selectedRun is reassigned'
);
assert.ok(partial.includes('data-testid="history-restart-rerun-downstream"'));
// The side-effect warning is a visible note, shown only while the box is ticked.
assert.ok(partial.includes('data-testid="history-restart-rerun-downstream-note"'));
assert.ok(/<p[^>]*x-show="[^"]*restartRerunDownstream"[^>]*data-testid="history-restart-rerun-downstream-note"/.test(partial));
assert.ok(partial.includes('Passed steps after a re-run step run again, repeating side effects such as file transfers.'));
assert.ok(!partial.includes('title="Passed steps that come after'), 'the warning must not be tooltip-only');
console.log('ok - restart option is wired');
