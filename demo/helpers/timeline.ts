// Builds the presenter's timeline after each take: every part and step with its real
// timecode in that take's video, and the narration from the script's STEP comments.
import fs from 'node:fs';
import type { TimelineEntry } from './presenter';

export function formatTimecode(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(total / 60);
  return `${String(minutes).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
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

export function renderTimeline(
  entries: TimelineEntry[],
  narration: Map<number, string>,
  durationMs: number,
): string {
  const out: string[] = [
    '# ATOM regression demo: presenter timeline',
    '',
    `Generated from the recorded take. Video length: ${formatTimecode(durationMs)}. `
      + 'Each time is when the step banner appears in `atom-regression-demo.webm`.',
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
  for (const entry of entries) {
    if (entry.kind === 'part') {
      out.push(`### ${formatTimecode(entry.at)} Part ${entry.part}: ${entry.title}`, '', entry.summary, '');
    } else {
      out.push(`**${formatTimecode(entry.at)} Step ${entry.step}: ${entry.title}**`, '');
      out.push(`On screen: ${entry.about}`, '');
      const said = narration.get(entry.step);
      if (said) out.push(`Say: ${said}`, '');
    }
  }
  return out.join('\n');
}

export function writeTimeline(
  filePath: string,
  scriptPath: string,
  entries: TimelineEntry[],
  durationMs: number,
): void {
  const narration = readNarration(fs.readFileSync(scriptPath, 'utf-8'));
  fs.writeFileSync(filePath, renderTimeline(entries, narration, durationMs), 'utf-8');
}
