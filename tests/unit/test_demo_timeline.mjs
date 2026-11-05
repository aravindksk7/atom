// Run with --experimental-strip-types: the demo's helpers are TypeScript.
import assert from 'node:assert';
import fs from 'node:fs';
import { readNarration, renderSrt, renderTimeline, speakingSeconds } from '../../demo/helpers/timeline.ts';

const entries = [
  { kind: 'part', at: 0, part: 1, title: 'Configure', summary: 'Pick a date' },
  { kind: 'step', at: 4000, step: 1, title: 'Open', about: 'On-screen text' },
  { kind: 'step', at: 9000, step: 2, title: 'Launch', about: 'Launch text' },
];
const words = (count) => Array(count).fill('word').join(' ');

assert.strictEqual(speakingSeconds(words(5)), 2);
assert.strictEqual(speakingSeconds(words(6)), 3);

// Each step shows its window, ending at the next entry or the end of the video.
const narration = new Map([[1, words(5)], [2, words(50)]]);
const timeline = renderTimeline(entries, narration, 15000);
assert.ok(timeline.includes('**00:04-00:09 (5s) Step 1: Open**'), timeline);
assert.ok(timeline.includes('**00:09-00:15 (6s) Step 2: Launch**'), timeline);
assert.ok(timeline.includes(`Say (2s): ${words(5)}`), timeline);

// Only narration that can't fit is flagged, and by how much.
assert.strictEqual((timeline.match(/Too long for this window/g) ?? []).length, 1, timeline);
assert.ok(timeline.includes('Too long for this window by 14s'), timeline);

// A window far longer than its narration is pointed out as something to cut.
const spare = renderTimeline(entries, new Map([[1, words(5)]]), 60000);
assert.ok(spare.includes('Spare 51s: a candidate to trim in your editor.'), spare);
assert.ok(!timeline.includes('a candidate to trim'), timeline);

// Cues are numbered, contiguous, and fall back to the banner text without narration.
assert.strictEqual(renderSrt(entries, new Map([[1, 'Hello there.']]), 3_725_500), [
  '1\n00:00:00,000 --> 00:00:04,000\nPart 1: Configure. Pick a date\n',
  '2\n00:00:04,000 --> 00:00:09,000\nStep 1: Hello there.\n',
  '3\n00:00:09,000 --> 01:02:05,500\nStep 2: Launch text\n',
].join('\n'));

// Every step in the demo has narration, numbered in the order the banners count them.
const script = fs.readFileSync(new URL('../../demo/atom-regression.demo.ts', import.meta.url), 'utf-8');
const banners = (script.match(/presenter\.step\(/g) ?? []).length;
const said = readNarration(script);
assert.ok(banners > 0);
assert.deepStrictEqual([...said.keys()], Array.from({ length: banners }, (_, index) => index + 1));
