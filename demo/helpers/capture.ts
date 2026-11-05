// Hand-off with demo/record-screen.cjs, which captures the screen on the host while this test runs
// in Docker. The test says when, so the capture starts once the browser fills the screen and stops
// before it closes, and the desktop never appears in the video. Without DEMO_CAPTURE_DIR these do
// nothing, and Playwright records the video itself.
import type { Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const POLL_MS = 50;
const TIMEOUT_MS = 60_000;
// Long enough for the browser's full-screen notice to clear before anything is captured.
const FULL_SCREEN_SETTLE_MS = 5000;

async function handOff(dir: string, request: string, reply: string): Promise<void> {
  fs.writeFileSync(path.join(dir, request), '');
  const deadline = Date.now() + TIMEOUT_MS;
  while (!fs.existsSync(path.join(dir, reply))) {
    if (Date.now() > deadline) throw new Error(`The screen recorder did not answer "${request}"`);
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
}

/** Fills the screen with the browser, hiding its toolbar and the Windows taskbar. */
export async function enterFullScreen(page: Page, dir = process.env.DEMO_CAPTURE_DIR): Promise<void> {
  if (!dir) return;
  const session = await page.context().newCDPSession(page);
  const { windowId } = await session.send('Browser.getWindowForTarget');
  await session.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'fullscreen' } });
  await session.detach();
  await page.waitForFunction(() => window.innerWidth === 1920 && window.innerHeight === 1080);
  await page.waitForTimeout(FULL_SCREEN_SETTLE_MS);
}

/**
 * Returns once frames are being captured: how many milliseconds of video already exist, for
 * timecodes to count from. Null if no capture is in use.
 */
export async function startCapture(dir = process.env.DEMO_CAPTURE_DIR): Promise<number | null> {
  if (!dir) return null;
  await handOff(dir, 'start.request', 'started');
  return Number(fs.readFileSync(path.join(dir, 'started'), 'utf-8'));
}

export async function stopCapture(dir = process.env.DEMO_CAPTURE_DIR): Promise<void> {
  if (dir) await handOff(dir, 'stop.request', 'stopped');
}
