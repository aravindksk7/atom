// Presentation layer for the demo video: visible cursor, element highlights, step banners
// and pacing. Nothing here knows about ATOM, so the script reads as the narration.
import { Locator, Page } from '@playwright/test';

/** Multiplies every pause. 1 is presentation pace, 2 is slower, 0 is a fast smoke run. */
export const DEMO_PACE = Number(process.env.DEMO_PACE ?? 1);

const PAUSE_MS = { short: 1500, medium: 2000, long: 2500 } as const;
const TYPING_DELAY_MS = 85;
const CURSOR_GLIDE_MS = 700;
const HIGHLIGHT_FADE_MS = 400;
const Z_TOP = 2147483647;

export type TimelineEntry =
  | { kind: 'part'; at: number; part: number; title: string; summary: string }
  | { kind: 'step'; at: number; step: number; title: string; about: string };

// Runs in every document, including the simulated GitLab page, so the cursor and banners
// survive navigation.
function installOverlay(zTop: number, glideMs: number, fadeMs: number) {
  const build = () => {
    if (document.getElementById('demo-cursor')) return;

    const style = document.createElement('style');
    style.textContent = `
      [data-demo-spotlight] { transition: outline-color ${fadeMs}ms ease, background-color ${fadeMs}ms ease; }
      #demo-cursor {
        position: fixed; left: 0; top: 0; width: 28px; height: 28px; z-index: ${zTop};
        pointer-events: none; transition: transform ${glideMs}ms cubic-bezier(.22,.8,.3,1);
        filter: drop-shadow(0 2px 3px rgba(0,0,0,.45));
      }
      #demo-part {
        position: fixed; top: 10px; left: 50%; transform: translateX(-50%); z-index: ${zTop - 1}; pointer-events: none;
        background: #111827; color: #fff; padding: 9px 16px; border-radius: 8px;
        font: 600 17px/1.3 system-ui, sans-serif; box-shadow: 0 4px 14px rgba(0,0,0,.35);
      }
      #demo-part:empty { display: none; }
      #demo-banner {
        position: fixed; left: 50%; bottom: 22px; transform: translateX(-50%); z-index: ${zTop - 1};
        pointer-events: none; width: min(1500px, 86vw); box-sizing: border-box;
        background: rgba(17,24,39,.96); color: #fff; padding: 13px 24px; border-radius: 10px;
        border-left: 6px solid #FF3366; box-shadow: 0 6px 18px rgba(0,0,0,.45);
        font-family: system-ui, sans-serif;
      }
      #demo-banner:empty { display: none; }
      #demo-banner b { display: block; font-size: 23px; font-weight: 700; line-height: 1.3; }
      #demo-banner span { display: block; font-size: 19px; line-height: 1.35; color: #d1d5db; margin-top: 2px; }
      #demo-slate {
        position: fixed; inset: 0; z-index: ${zTop - 2}; display: flex; flex-direction: column;
        align-items: center; justify-content: center; gap: 28px; padding: 60px; text-align: center;
        background: #0f172a; color: #f8fafc; font-family: system-ui, sans-serif;
      }
      #demo-slate h1 { font-size: 62px; margin: 0; font-weight: 700; }
      #demo-slate p { font-size: 30px; line-height: 1.45; margin: 0; max-width: 1500px; }
      #demo-slate small { font-size: 23px; color: #cbd5e1; max-width: 1400px; line-height: 1.4; }
    `;
    document.documentElement.appendChild(style);

    const cursor = document.createElement('div');
    cursor.id = 'demo-cursor';
    cursor.innerHTML = `<svg viewBox="0 0 24 24" width="28" height="28">
      <path d="M4 2 L4 20 L9 15.5 L12.5 22.5 L15.5 21 L12 14 L19 14 Z"
            fill="#fff" stroke="#111827" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
    document.documentElement.appendChild(cursor);

    for (const id of ['demo-part', 'demo-banner']) {
      const element = document.createElement('div');
      element.id = id;
      document.documentElement.appendChild(element);
    }
  };
  if (document.documentElement) build();
  document.addEventListener('DOMContentLoaded', build);
}

export class Presenter {
  /** Every part and step with its time from the start of the video, for the timeline. */
  readonly timeline: TimelineEntry[] = [];

  private cursorX = 960;
  private cursorY = 540;
  private partText = '';
  private bannerTitle = '';
  private bannerAbout = '';
  private stepNumber = 0;
  private readonly startedAt = Date.now();

  constructor(private readonly page: Page, private readonly totalParts: number) {}

  private now(): number {
    return Date.now() - this.startedAt;
  }

  /** Call once, before the first navigation. */
  async install(): Promise<void> {
    await this.page.addInitScript(installOverlay, Z_TOP, CURSOR_GLIDE_MS, HIGHLIGHT_FADE_MS);
    this.page.on('load', () => {
      void this.restoreOverlay().catch(() => undefined);
    });
  }

  private async restoreOverlay(): Promise<void> {
    await this.page.evaluate(
      ({ x, y, part, title, about }) => {
        const cursor = document.getElementById('demo-cursor');
        if (cursor) cursor.style.transform = `translate(${x}px, ${y}px)`;
        const label = document.getElementById('demo-part');
        if (label) label.textContent = part;
        const banner = document.getElementById('demo-banner');
        if (banner) {
          banner.textContent = '';
          if (title) {
            const heading = document.createElement('b');
            heading.textContent = title;
            const detail = document.createElement('span');
            detail.textContent = about;
            banner.append(heading, detail);
          }
        }
      },
      {
        x: this.cursorX, y: this.cursorY, part: this.partText,
        title: this.bannerTitle, about: this.bannerAbout,
      },
    );
  }

  /** Waits so the speaker can finish the sentence. */
  async pace(length: keyof typeof PAUSE_MS = 'medium'): Promise<void> {
    await this.page.waitForTimeout(PAUSE_MS[length] * DEMO_PACE);
  }

  /** Full-screen title card. */
  async slate(title: string, body: string, footnote = '', holdMs = 5000): Promise<void> {
    await this.page.evaluate(
      ({ title: t, body: b, footnote: f }) => {
        document.getElementById('demo-slate')?.remove();
        const slate = document.createElement('div');
        slate.id = 'demo-slate';
        for (const [tag, text] of [['h1', t], ['p', b], ['small', f]] as const) {
          if (!text) continue;
          const element = document.createElement(tag);
          element.textContent = text;
          slate.appendChild(element);
        }
        document.documentElement.appendChild(slate);
      },
      { title, body, footnote },
    );
    await this.page.waitForTimeout(holdMs * DEMO_PACE);
    await this.page.evaluate(() => document.getElementById('demo-slate')?.remove());
  }

  /** Announces a part on a title card, then keeps its name at the top of the screen. */
  async part(number: number, title: string, summary: string): Promise<void> {
    this.timeline.push({ kind: 'part', at: this.now(), part: number, title, summary });
    this.bannerTitle = '';
    this.bannerAbout = '';
    await this.restoreOverlay();
    await this.slate(`${number}. ${title}`, summary, '', 3500);
    this.partText = `Part ${number} of ${this.totalParts}: ${title}`;
    await this.restoreOverlay();
  }

  /** Shows the banner naming the current step and what it is about. Numbers steps in order. */
  async step(title: string, about: string): Promise<void> {
    this.stepNumber += 1;
    this.timeline.push({ kind: 'step', at: this.now(), step: this.stepNumber, title, about });
    this.bannerTitle = `Step ${this.stepNumber}: ${title}`;
    this.bannerAbout = about;
    await this.restoreOverlay();
    await this.pace('short');
  }

  private async moveCursorTo(target: Locator): Promise<void> {
    await target.scrollIntoViewIfNeeded();
    const box = await target.boundingBox();
    if (!box) return;
    this.cursorX = box.x + Math.min(box.width / 2, 260);
    this.cursorY = box.y + box.height / 2;
    await this.page.mouse.move(this.cursorX, this.cursorY);
    await this.restoreOverlay();
    await this.page.waitForTimeout(CURSOR_GLIDE_MS * Math.min(DEMO_PACE, 1));
  }

  private async setHighlight(target: Locator, on: boolean): Promise<void> {
    await target.evaluate((element, enable) => {
      const el = element as HTMLElement;
      if (enable) {
        el.setAttribute('data-demo-spotlight', '');
        el.dataset.demoOutline = el.style.outline;
        el.dataset.demoOffset = el.style.outlineOffset;
        el.dataset.demoBackground = el.style.background;
        el.style.setProperty('outline', '3px solid #FF3366', 'important');
        el.style.setProperty('outline-offset', '2px', 'important');
        el.style.setProperty('background', 'rgba(255, 51, 102, 0.1)', 'important');
      } else {
        el.style.outline = el.dataset.demoOutline ?? '';
        el.style.outlineOffset = el.dataset.demoOffset ?? '';
        el.style.background = el.dataset.demoBackground ?? '';
        el.removeAttribute('data-demo-spotlight');
      }
    }, on);
  }

  /**
   * Moves the cursor to an element, holds a highlight on it, runs the action, then
   * removes the highlight. An action can remove the element, which is fine.
   */
  async spotlight(
    target: Locator,
    action?: () => Promise<unknown>,
    hold: keyof typeof PAUSE_MS = 'short',
  ): Promise<void> {
    await target.waitFor({ state: 'visible' });
    await this.moveCursorTo(target);
    await this.setHighlight(target, true);
    await this.pace(hold);
    try {
      if (action) await action();
      await this.page.waitForTimeout(HIGHLIGHT_FADE_MS);
    } finally {
      await this.setHighlight(target, false).catch(() => undefined);
    }
  }

  /** Highlights something the speaker is talking about, without touching it. */
  async show(target: Locator, hold: keyof typeof PAUSE_MS = 'long'): Promise<void> {
    await this.spotlight(target, undefined, hold);
  }

  async click(target: Locator): Promise<void> {
    await this.spotlight(target, () => target.click());
    await this.pace('short');
  }

  async select(target: Locator, value: string | { label: string }): Promise<void> {
    await this.spotlight(target, () => target.selectOption(value));
    await this.pace('short');
  }

  /**
   * Types at a steady human speed. pressSequentially's delay was unsteady here, because the page
   * responds slowly while recording. So this shows the text growing at the set speed and sends the
   * app one input event when typing finishes.
   */
  async type(target: Locator, text: string): Promise<void> {
    await this.spotlight(target, async () => {
      await target.click();
      for (let length = 1; length <= text.length; length++) {
        await target.evaluate((element, value) => {
          (element as HTMLInputElement).value = value;
        }, text.slice(0, length));
        await this.page.waitForTimeout(TYPING_DELAY_MS * Math.min(DEMO_PACE, 1));
      }
      await target.dispatchEvent('input');
    });
    await this.pace('short');
  }

  /** Scrolls an element to the middle of the screen without highlighting it. */
  async centre(target: Locator): Promise<void> {
    await target.evaluate((element) =>
      element.scrollIntoView({ behavior: 'smooth', block: 'center' }));
  }
}
