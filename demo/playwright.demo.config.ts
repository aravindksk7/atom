import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// 16:9 widescreen, matched by the viewport and the recording so nothing is scaled.
const VIDEO_SIZE = { width: 1920, height: 1080 };
// Set by demo/record-screen.cjs, which runs the browser on the host and captures the screen.
const REMOTE_BROWSER = process.env.DEMO_BROWSER_WS;

export default defineConfig({
  testDir: __dirname,
  testMatch: /\.demo\.ts$/,
  outputDir: path.join(__dirname, 'output', 'results'),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // The run is long and DEMO_PACE can stretch it further.
  timeout: 60 * 60_000,
  // The page can stall for a while under recording, and one stall shouldn't end a long take.
  expect: { timeout: 120_000 },
  reporter: [['list']],
  globalSetup: require.resolve('./seed/seed_atom.ts'),
  use: {
    // For a screen capture the full-screen window sets the size, so nothing may emulate one.
    ...(REMOTE_BROWSER ? { viewport: null } : { ...devices['Desktop Chrome'], viewport: VIDEO_SIZE }),
    baseURL: process.env.DEMO_BASE_URL || 'http://atom:8055',
    video: REMOTE_BROWSER ? 'off' : { mode: 'on', size: VIDEO_SIZE },
    connectOptions: REMOTE_BROWSER ? { wsEndpoint: REMOTE_BROWSER } : undefined,
    // A trace copies the page on every action, which slows a take enough to show in the video.
    // DEMO_TRACE=1 turns it on for debugging a failing take.
    trace: process.env.DEMO_TRACE ? 'retain-on-failure' : 'off',
    screenshot: 'only-on-failure',
    actionTimeout: 120_000,
  },
});
