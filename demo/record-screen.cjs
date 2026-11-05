#!/usr/bin/env node
// Records the demo from the screen, for smoother video than Playwright's own recording. Windows only.
//
//   node demo/record-screen.cjs
//
// The browser runs full screen on this machine and the test drives it from Docker, as usual. ffmpeg
// captures the screen and encodes on the GPU. The test says when to start and stop: see
// demo/helpers/capture.ts. See demo/README.md for what to do with the machine during a take.
//
// This resets the demo stack first and last, deleting its data, as every take needs an empty one.
'use strict';
const { execFileSync, spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('@playwright/test');

const ROOT = path.join(__dirname, '..');
const OUTPUT_DIR = path.join(__dirname, 'output');
const CAPTURE_DIR = path.join(OUTPUT_DIR, 'capture');
const RAW_VIDEO = path.join(OUTPUT_DIR, 'capture.mkv');
const VIDEO = path.join(OUTPUT_DIR, 'atom-regression-demo.mp4');
const LOG = path.join(OUTPUT_DIR, 'record.log');
const COMPOSE = ['compose', '-f', 'docker-compose.demo.yml', '-f', 'docker-compose.demo.screen.yml'];
const BROWSER_PORT = 18999;
const FRAME_RATE = 60;
const POLL_MS = 50;
const WINDOW_SETTLE_MS = 500;
// The capture produces nothing until the screen changes, so the test's first navigation is what
// starts the video. This only has to be long enough for ffmpeg to be ready for it.
const CAPTURE_READY_MS = 2000;
// ffmpeg's start-up before its first frame, measured against the banners in a full take.
const CAPTURE_STARTUP_MS = 100;

// The browser's names for the services are the ones the test uses, as in Docker.
const HOST_RULES = 'MAP atom:8055 127.0.0.1:18055, MAP gitlab-sim:8090 127.0.0.1:18090';

/** Keeps the browser above every other window, so nothing else can get into the video. */
function pinAboveOtherWindows(browserPid) {
  const script = `
    Add-Type -TypeDefinition @'
    using System; using System.Runtime.InteropServices; using System.Text;
    public static class Win {
      public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
      [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);
      [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
      [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
      [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
      [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
      [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
      [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
      public static int Pin(uint target) {
        int pinned = 0;
        EnumWindows((hWnd, _) => {
          uint pid; GetWindowThreadProcessId(hWnd, out pid);
          if (pid == target && IsWindowVisible(hWnd) && GetWindowTextLength(hWnd) > 0) {
            SetWindowPos(hWnd, new IntPtr(-1), 0, 0, 0, 0, 0x0003);
            SetForegroundWindow(hWnd);
            pinned++;
          }
          return true;
        }, IntPtr.Zero);
        return pinned;
      }
    }
'@
    # Windows draws an I-beam into the screen image itself, whatever ffmpeg is told, so the real
    # pointer waits in the corner, where it can't turn into one over a text box.
    [void][Win]::SetCursorPos(99999, 99999)
    [Win]::Pin(${browserPid})
  `;
  const pinned = Number(execFileSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], { encoding: 'utf8' }).trim());
  if (pinned < 1) throw new Error('Could not find the browser window to pin');
}

function run(command, args, env = {}) {
  return new Promise((resolve, reject) => {
    const log = fs.openSync(LOG, 'a');
    const child = spawn(command, args, {
      cwd: ROOT, env: { ...process.env, ...env }, stdio: ['ignore', log, log],
    });
    child.on('error', reject);
    child.on('exit', (code) => {
      fs.closeSync(log);
      resolve(code);
    });
  });
}

async function mustRun(command, args, env) {
  const code = await run(command, args, env);
  if (code !== 0) throw new Error(`${command} ${args.join(' ')} exited with ${code}. See ${LOG}`);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** The capture, started and stopped when the test asks. */
class ScreenCapture {
  constructor(browserPid) {
    this.browserPid = browserPid;
    this.ffmpeg = null;
    this.exited = null;
    this.watching = true;
  }

  async start() {
    pinAboveOtherWindows(this.browserPid);
    await sleep(WINDOW_SETTLE_MS);
    const log = fs.openSync(path.join(OUTPUT_DIR, 'capture.log'), 'w');
    this.ffmpeg = spawn('ffmpeg', [
      '-y', '-hide_banner', '-loglevel', 'warning',
      // The browser draws its own presenter cursor, so the real one stays out of the video.
      '-f', 'lavfi', '-i', `ddagrab=output_idx=0:framerate=${FRAME_RATE}:draw_mouse=0`,
      '-vf', 'hwdownload,format=bgra,format=nv12',
      '-c:v', 'h264_qsv', '-global_quality', '20', '-preset', 'medium', '-bf', '0',
      // Matroska, so a crash mid-take still leaves a playable file.
      RAW_VIDEO,
    ], { cwd: ROOT, stdio: ['pipe', 'ignore', log] });
    this.exited = new Promise((resolve) => this.ffmpeg.on('exit', resolve));

    await sleep(CAPTURE_READY_MS);
    if (this.ffmpeg.exitCode !== null) throw new Error(`ffmpeg exited early. See ${OUTPUT_DIR}/capture.log`);
  }

  async stop() {
    if (!this.ffmpeg) return;
    this.ffmpeg.stdin.write('q');
    await this.exited;
    this.ffmpeg = null;
  }

  /** Answers the test's requests until told to finish. */
  async watch() {
    const has = (name) => fs.existsSync(path.join(CAPTURE_DIR, name));
    const answer = (name, content = '') => fs.writeFileSync(path.join(CAPTURE_DIR, name), String(content));
    let started = false;
    while (this.watching) {
      if (!started && has('start.request')) {
        await this.start();
        started = true;
        answer('started', CAPTURE_READY_MS - CAPTURE_STARTUP_MS);
      }
      if (has('stop.request') && !has('stopped')) {
        await this.stop();
        answer('stopped');
      }
      await sleep(POLL_MS);
    }
  }
}

async function main() {
  const testFiles = process.argv.slice(2);
  const pace = process.env.DEMO_PACE ?? '1';
  fs.rmSync(CAPTURE_DIR, { recursive: true, force: true });
  fs.mkdirSync(CAPTURE_DIR, { recursive: true });
  fs.rmSync(RAW_VIDEO, { force: true });
  fs.writeFileSync(LOG, '');

  await mustRun('docker', [...COMPOSE, 'down', '-v']);
  await mustRun('docker', [...COMPOSE, 'up', '-d', '--wait', 'atom', 'gitlab-sim']);

  // On loopback, with a random path in the address, because this controls a browser on this machine.
  const server = await chromium.launchServer({
    headless: false,
    host: '127.0.0.1',
    port: BROWSER_PORT,
    ignoreDefaultArgs: ['--enable-automation'],
    args: [
      // One CSS pixel to one screen pixel, whatever the Windows display scale.
      '--force-device-scale-factor=1',
      `--host-resolver-rules=${HOST_RULES}`,
    ],
  });
  const wsEndpoint = server.wsEndpoint().replace('127.0.0.1', 'host.docker.internal');

  const capture = new ScreenCapture(server.process().pid);
  // Caught here so a capture failure still closes the browser, which is covering the screen. The
  // test then fails too, because nothing answers it.
  let captureError = null;
  const watching = capture.watch().catch((error) => { captureError = error; });
  let code = 1;
  try {
    code = await run('docker', [
      ...COMPOSE, 'run', '--rm', '-e', `DEMO_PACE=${pace}`, 'demo-runner',
      'node', 'node_modules/@playwright/test/cli.js', 'test',
      '--config', 'demo/playwright.demo.config.ts', ...testFiles,
    ], { DEMO_BROWSER_WS: wsEndpoint });
  } finally {
    capture.watching = false;
    await watching;
    await capture.stop();
    await server.close();
  }

  if (fs.existsSync(RAW_VIDEO)) {
    await mustRun('ffmpeg', ['-y', '-hide_banner', '-loglevel', 'error', '-i', RAW_VIDEO, '-c', 'copy', '-movflags', '+faststart', VIDEO]);
    fs.rmSync(RAW_VIDEO);
  }
  await mustRun('docker', [...COMPOSE, 'down', '-v']);
  if (captureError) console.error(`The capture failed: ${captureError.message}`);
  console.log(code === 0 ? `Recorded ${VIDEO}` : `The take failed. See ${LOG}`);
  process.exit(code);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
