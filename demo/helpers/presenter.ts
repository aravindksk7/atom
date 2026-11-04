// Presentation layer for the demo video: visible cursor, element highlights, captions
// and pacing. Nothing here knows about ATOM, so the script reads as the narration.
import { Locator, Page } from '@playwright/test';

/** Multiplies every pause. 1 is presentation pace, 2 is slower, 0 is a fast smoke run. */
export const DEMO_PACE = Number(process.env.DEMO_PACE ?? 1);

const PAUSE_MS = { short: 1500, medium: 2000, long: 2500 } as const;
const TYPING_DELAY_MS = 85;
const CURSOR_GLIDE_MS = 700;
const HIGHLIGHT_FADE_MS = 400;
const Z_TOP = 2147483647;

// Runs in every document, including Mailpit's inbox, so the cursor and captions
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
      #demo-cursor.pressed svg { transform: scale(.82); }
      #demo-chapter {
        position: fixed; top: 10px; left: 50%; transform: translateX(-50%); z-index: ${zTop - 1}; pointer-events: none;
        background: #111827; color: #fff; padding: 9px 16px; border-radius: 8px;
        font: 600 17px/1.3 system-ui, sans-serif; box-shadow: 0 4px 14px rgba(0,0,0,.35);
      }
      #demo-chapter:empty { display: none; }
      #demo-note {
        position: fixed; left: 50%; bottom: 26px; transform: translateX(-50%); z-index: ${zTop - 1};
        pointer-events: none; max-width: 1400px; background: rgba(17,24,39,.94); color: #fff;
        padding: 13px 24px; border-radius: 10px; text-align: center;
        font: 500 23px/1.35 system-ui, sans-serif; box-shadow: 0 6px 18px rgba(0,0,0,.4);
      }
      #demo-note:empty { display: none; }
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
    cursor.innerHTML = `<svg viewBox="0 0 24 24" width="28" height="28" style="transition:transform .12s">
      <path d="M4 2 L4 20 L9 15.5 L12.5 22.5 L15.5 21 L12 14 L19 14 Z"
            fill="#fff" stroke="#111827" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
    document.documentElement.appendChild(cursor);

    for (const id of ['demo-chapter', 'demo-note']) {
      const element = document.createElement('div');
      element.id = id;
      document.documentElement.appendChild(element);
    }
  };
  if (document.documentElement) build();
  document.addEventListener('DOMContentLoaded', build);
}

export class Presenter {
  private cursorX = 960;
  private cursorY = 540;
  private chapterText = '';

  constructor(private readonly page: Page) {}

  /** Call once, before the first navigation. */
  async install(): Promise<void> {
    await this.page.addInitScript(installOverlay, Z_TOP, CURSOR_GLIDE_MS, HIGHLIGHT_FADE_MS);
    this.page.on('load', () => {
      void this.restoreOverlay().catch(() => undefined);
    });
  }

  private async restoreOverlay(): Promise<void> {
    await this.page.evaluate(
      ({ x, y, chapter }) => {
        const cursor = document.getElementById('demo-cursor');
        if (cursor) cursor.style.transform = `translate(${x}px, ${y}px)`;
        const label = document.getElementById('demo-chapter');
        if (label) label.textContent = chapter;
      },
      { x: this.cursorX, y: this.cursorY, chapter: this.chapterText },
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

  /** Announces a chapter on a title card, then keeps its name in the corner. */
  async chapter(number: number, title: string, summary: string): Promise<void> {
    await this.slate(`${number}. ${title}`, summary, '', 3500);
    this.chapterText = `Step ${number} of 5: ${title}`;
    await this.restoreOverlay();
  }

  /** A caption along the bottom of the screen. Pass nothing to clear it. */
  async note(text = ''): Promise<void> {
    await this.page.evaluate((value) => {
      const note = document.getElementById('demo-note');
      if (note) note.textContent = value;
    }, text);
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

  /** Types at human speed. */
  async type(target: Locator, text: string): Promise<void> {
    await this.spotlight(target, async () => {
      await target.click();
      await target.pressSequentially(text, { delay: TYPING_DELAY_MS * Math.min(DEMO_PACE, 1) });
    });
    await this.pace('short');
  }

  /** Scrolls an element to the middle of the screen without highlighting it. */
  async centre(target: Locator): Promise<void> {
    await target.evaluate((element) =>
      element.scrollIntoView({ behavior: 'smooth', block: 'center' }));
  }
}
