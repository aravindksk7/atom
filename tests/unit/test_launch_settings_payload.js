'use strict';
// The Launch tab no longer has an Execution Mode control, so a value left in saved
// session settings must not reach the API, where it could pin a sequence-backed
// selection to one worker with nothing on screen to explain it.
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const launch = fs.readFileSync(path.join(ROOT, 'frontend', 'features', 'launch.js'), 'utf8');
const app = fs.readFileSync(path.join(ROOT, 'frontend', 'app.js'), 'utf8');
const partial = fs.readFileSync(path.join(ROOT, 'frontend', 'partials', 'tab-launch.html'), 'utf8');

assert.ok(!launch.includes('execution_mode'), 'launch.js must not send or default execution_mode');
assert.ok(
  /delete settings\.launchSettings\.execution_mode/.test(app),
  'loadSessionSettings must drop a stale execution_mode'
);
assert.ok(!partial.includes('a11y-launch-execution-mode'), 'the dropdown must be gone');
assert.ok(partial.includes('data-testid="launch-order-note"'), 'the note must replace it');
console.log('ok - launch settings no longer carry execution_mode');
