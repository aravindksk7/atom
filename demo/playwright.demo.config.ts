import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// 16:9 widescreen, matched by the viewport and the recording so nothing is scaled.
const VIDEO_SIZE = { width: 1920, height: 1080 };

export default defineConfig({
  testDir: __dirname,
  testMatch: /\.demo\.ts$/,
  outputDir: path.join(__dirname, 'output', 'results'),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // The run is long and DEMO_PACE can stretch it further.
  timeout: 60 * 60_000,
  reporter: [['list']],
  globalSetup: require.resolve('./seed/seed_atom.ts'),
  use: {
    ...devices['Desktop Chrome'],
    baseURL: process.env.DEMO_BASE_URL || 'http://atom:8055',
    viewport: VIDEO_SIZE,
    video: { mode: 'on', size: VIDEO_SIZE },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    actionTimeout: 30_000,
  },
});
