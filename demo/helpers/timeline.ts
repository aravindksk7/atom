// Builds the presenter's timeline after each take: every part and step with its real
// timecode in that take's video, and the narration from the script's STEP comments.
// The .srt holds the same narration as cues, for a video player or editor to show in sync.
import fs from 'node:fs';
import type { TimelineEntry } from './presenter';

const SPOKEN_WORDS_PER_SECOND = 2.5;
const TRIM_HINT_SECONDS = 30;

export function formatTimecode(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(total / 60);
  return `${String(minutes).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

function formatSrtTime(ms: number): string {
  const whole = Math.max(0, Math.round(ms));
  const pad = (value: number, width = 2) => String(value).padStart(width, '0');
  const hours = Math.floor(whole / 3_600_000);
  const minutes = Math.floor(whole / 60_000) % 60;
  const seconds = Math.floor(whole / 1000) % 60;
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)},${pad(whole % 1000, 3)}`;
}

/** The "// STEP n: ..." comment blocks in a script, as narration by step number. */
export function readNarration(source: string): Map<number, string> {
  const narration = new Map<number, string>();
  const lines = source.split(/\r?\n/);
  for (let index = 0; index < lines.length; index++) {
    const match = lines[index].match(/^\s*\/\/ STEP (\d+): (.*)$/);
    if (!match) continue;
    const parts = [match[2].trim()];
    for (let next = index + 1; next < lines.length; next++) {
      const more = lines[next].match(/^\s*\/\/ (.*)$/);
      if (!more) break;
      parts.push(more[1].trim());
    }
    narration.set(Number(match[1]), parts.join(' '));
  }
  return narration;
}

/** Seconds a sentence takes to say at a steady presenting pace. */
export function speakingSeconds(text: string): number {
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  return Math.ceil(words / SPOKEN_WORDS_PER_SECOND);
}

/** When each entry ends: at the next entry, or at the end of the video. */
function endOf(entries: TimelineEntry[], index: number, durationMs: number): number {
  return index + 1 < entries.length ? entries[index + 1].at : durationMs;
}

export function renderTimeline(
  entries: TimelineEntry[],
  narration: Map<number, string>,
  durationMs: number,
): string {
  const out: string[] = [
    '# ATOM regression demo: presenter timeline',
    '',
    `Generated from the recorded take. Video length: ${formatTimecode(durationMs)}. `
      + 'Each time is when the step banner appears in the video, and how long it '
      + 'stays until the next one. "Say" is about the length shown beside it at a steady pace, and '
      + '`timeline.srt` holds the same words as cues for a video player or editor.',
    '',
    '## At a glance',
    '',
    '| Time | Part |',
    '|---|---|',
  ];
  for (const entry of entries) {
    if (entry.kind === 'part') {
      out.push(`| ${formatTimecode(entry.at)} | **${entry.part}. ${entry.title}**: ${entry.summary} |`);
    }
  }
  out.push('', '## Script', '');
  entries.forEach((entry, index) => {
    if (entry.kind === 'part') {
      out.push(`### ${formatTimecode(entry.at)} Part ${entry.part}: ${entry.title}`, '', entry.summary, '');
      return;
    }
    const end = endOf(entries, index, durationMs);
    const window = Math.round((end - entry.at) / 1000);
    out.push(
      `**${formatTimecode(entry.at)}-${formatTimecode(end)} (${window}s) Step ${entry.step}: ${entry.title}**`,
      '',
      `On screen: ${entry.about}`,
      '',
    );
    const said = narration.get(entry.step);
    const needed = said ? speakingSeconds(said) : 0;
    if (said) out.push(`Say (${needed}s): ${said}`, '');
    if (needed > window) {
      out.push(`Too long for this window by ${needed - window}s: trim it, or freeze the frame in your editor.`, '');
    } else if (window - needed >= TRIM_HINT_SECONDS) {
      out.push(`Spare ${window - needed}s: a candidate to trim in your editor.`, '');
    }
  });
  return out.join('\n');
}

/** One cue per part and step, each shown until the next one starts. */
export function renderSrt(
  entries: TimelineEntry[],
  narration: Map<number, string>,
  durationMs: number,
): string {
  const cues = entries.map((entry, index) => {
    const text = entry.kind === 'part'
      ? `Part ${entry.part}: ${entry.title}. ${entry.summary}`
      : `Step ${entry.step}: ${narration.get(entry.step) ?? entry.about}`;
    const start = formatSrtTime(entry.at);
    const end = formatSrtTime(endOf(entries, index, durationMs));
    return `${index + 1}\n${start} --> ${end}\n${text}\n`;
  });
  return cues.join('\n');
}

export function writeTimeline(
  filePath: string,
  scriptPath: string,
  entries: TimelineEntry[],
  durationMs: number,
): void {
  const narration = readNarration(fs.readFileSync(scriptPath, 'utf-8'));
  fs.writeFileSync(filePath, renderTimeline(entries, narration, durationMs), 'utf-8');
  fs.writeFileSync(filePath.replace(/\.md$/, '.srt'), renderSrt(entries, narration, durationMs), 'utf-8');
}
