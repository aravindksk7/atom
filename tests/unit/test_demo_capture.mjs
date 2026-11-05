// Run with --experimental-strip-types: the demo's helpers are TypeScript.
import assert from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { startCapture, stopCapture } from '../../demo/helpers/capture.ts';

// Without a recorder these do nothing, so a normal take is unaffected.
assert.strictEqual(await startCapture(''), null);
await stopCapture('');

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'demo-capture-'));

// Each call asks, then waits for the recorder's answer.
let answered = false;
const starting = startCapture(dir).then((videoSoFar) => {
  assert.ok(answered, 'returned before the recorder answered');
  return videoSoFar;
});
await new Promise((resolve) => setTimeout(resolve, 150));
assert.ok(fs.existsSync(path.join(dir, 'start.request')));
answered = true;
// The recorder reports how much video exists, because timecodes have to count from its start.
fs.writeFileSync(path.join(dir, 'started'), '1900');
assert.strictEqual(await starting, 1900);

answered = false;
const stopping = stopCapture(dir).then(() => assert.ok(answered, 'returned before the recorder answered'));
await new Promise((resolve) => setTimeout(resolve, 150));
assert.ok(fs.existsSync(path.join(dir, 'stop.request')));
answered = true;
fs.writeFileSync(path.join(dir, 'stopped'), '');
await stopping;

fs.rmSync(dir, { recursive: true });
