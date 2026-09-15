# Multi-Day Sequence Recovery Playwright Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic Playwright test proving that a three-business-day sequence batch recovers from a Day 2 processing failure without rerunning Day 1, then completes Day 2 and Day 3.

**Architecture:** Add one focused E2E spec containing an inline page object and a stateful API simulator. Browser interactions create the three-job DAG, launch the date batch, inspect the failed run, edit the sequence configuration, restart from failure, and verify completion; request counters and stable run IDs prove which work did and did not execute.

**Tech Stack:** TypeScript, Playwright Test 1.48, existing `tests/e2e/fixtures.ts` authenticated fixture, Alpine.js UI, Playwright route interception.

## Global Constraints

- Run the same ordered jobs—Data Ingestion, Data Processing, Report Generation—for three consecutive business dates.
- Use the existing repeat-execution controls with `iterations: 3`, `step_days: 1`, and `weekend_policy: "ignore"`.
- Fail Data Processing during Day 2 and expose the controlled error in the run detail.
- Use the visible sequence edit/version action as the fix and `Restart from failure` as the resume action.
- Assert Day 1 is not launched again, Day 2 resumes from its successful ingestion step, Day 3 runs, and the batch finishes `COMPLETED`.
- Prefer `getByRole` and `getByText`; use existing `data-testid` selectors where no unique accessible query exists.
- Include the explicitly requested Creation, Failure, Resume, and Verification comments and meaningful `console.log` stage messages.
- Do not use fixed sleeps; use web-first assertions and `expect.poll`.
- Do not modify production behavior or add dependencies.

---

## File Structure

- Create `tests/e2e/51-multi-day-sequence-recovery.spec.ts`: types, stateful route simulator, inline `SchedulerPage` POM, and the complete browser scenario.
- Reuse `tests/e2e/fixtures.ts`: authenticated page fixture and Playwright `expect`; no changes required.
- Reuse UI contracts in `frontend/features/sequences.js:302-351` and `frontend/features/history.js:143-169`; no production changes required.

### Task 1: Add deterministic multi-day failure and recovery coverage

**Files:**
- Create: `tests/e2e/51-multi-day-sequence-recovery.spec.ts`
- Reference: `tests/e2e/17-sequences.spec.ts:17-183`
- Reference: `frontend/features/sequences.js:302-351`
- Reference: `frontend/features/history.js:143-169`

**Interfaces:**
- Consumes: `test` and `expect` from `./fixtures`; existing sequence editor test IDs; `POST /api/sequences/{id}/launch-batch`; `GET /api/run-batches/{batchId}`; `GET /api/runs`; `GET /api/runs/{runId}`; `GET /api/runs/{runId}/steps`; `GET|POST /api/runs/{runId}/restart`.
- Produces: `SchedulerScenarioMock.install(page: Page): Promise<void>`, `SchedulerPage` workflow methods, and one serial-safe Playwright scenario.

- [ ] **Step 1: Create the simulator types, deterministic IDs, and initial state**

Create `tests/e2e/51-multi-day-sequence-recovery.spec.ts` with the imports, constants, response types, and simulator shell below. The simulator owns every transition and exposes request counters for direct assertions.

```ts
import { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';

const SEQUENCE_NAME = `e2e-three-day-recovery-${Date.now()}`;
const JOBS = ['Data Ingestion', 'Data Processing', 'Report Generation'] as const;
const BATCH_ID = 'batch-three-day-recovery';
const DAY_1_RUN = '11111111-1111-4111-8111-111111111111';
const DAY_2_FAILED_RUN = '22222222-2222-4222-8222-222222222222';
const DAY_2_RESTARTED_RUN = '22222222-2222-4222-8222-333333333333';
const DAY_3_RUN = '33333333-3333-4333-8333-333333333333';

const dates = ['2026-09-14', '2026-09-15', '2026-09-16'] as const;

type Phase = 'failed' | 'fixed' | 'completed';
type StepStatus = 'PASSED' | 'FAILED' | 'BLOCKED';

type StepFixture = {
  step_index: number;
  step_id: string;
  job_name: string;
  status: StepStatus;
  depends_on: string[];
  carried_over?: boolean;
  error_message?: string;
};

type RunFixture = {
  run_id: string;
  label: string;
  report_name: string;
  status: 'PASSED' | 'FAILED' | 'COMPLETED';
  started_at: string;
  completed_at: string;
  source_env: string;
  target_env: string;
  total_tests: number;
  passed: number;
  failed: number;
  slow: number;
  error: number;
  results: Array<Record<string, unknown>>;
  steps: StepFixture[];
  restarted_from_run_id?: string;
};

class SchedulerScenarioMock {
  phase: Phase = 'failed';
  launchBatchRequests = 0;
  restartRequests: string[] = [];
  day1ExecutionCount = 1;
  day2IngestionExecutionCount = 1;
  day3ExecutionCount = 0;

  async install(page: Page): Promise<void> {
    await page.route('**/api/run-batches/**', (route) => this.handleBatch(route));
    await page.route('**/api/runs/**', (route) => this.handleRun(route));
    await page.route('**/api/runs**', (route) => this.handleRuns(route));
    await page.route('**/api/sequences/*/launch-batch', (route) => this.handleLaunch(route));
  }
}
```

- [ ] **Step 2: Run Playwright listing to verify the incomplete spec fails compilation**

Run:

```powershell
npx playwright test tests/e2e/51-multi-day-sequence-recovery.spec.ts --list
```

Expected: FAIL with TypeScript errors that `handleBatch`, `handleRun`, `handleRuns`, and `handleLaunch` do not exist. This confirms the new spec is discovered before behavior is implemented.

- [ ] **Step 3: Implement complete run and step fixtures**

Add these methods inside `SchedulerScenarioMock`, before `install`. The run detail includes result rows because the History detail template reads `selectedRun.results`, while the `/steps` response drives the explicit job timeline.

```ts
  private passedSteps(carriedIngestion = false): StepFixture[] {
    return JOBS.map((job_name, step_index) => ({
      step_index,
      step_id: ['ingestion', 'processing', 'report'][step_index],
      job_name,
      status: 'PASSED' as const,
      depends_on: step_index === 0 ? [] : [['ingestion'], ['processing']][step_index - 1],
      carried_over: carriedIngestion && step_index === 0,
    }));
  }

  private failedDay2Steps(): StepFixture[] {
    return [
      { step_index: 0, step_id: 'ingestion', job_name: JOBS[0], status: 'PASSED', depends_on: [] },
      {
        step_index: 1,
        step_id: 'processing',
        job_name: JOBS[1],
        status: 'FAILED',
        depends_on: ['ingestion'],
        error_message: 'Invalid processing configuration: transform_mode is missing',
      },
      { step_index: 2, step_id: 'report', job_name: JOBS[2], status: 'BLOCKED', depends_on: ['processing'] },
    ];
  }

  private result(job: string, index: number, carried_over = false): Record<string, unknown> {
    return {
      id: index,
      query_name: job,
      status: 'PASSED',
      effective_status: 'PASSED',
      duration_seconds: carried_over ? null : 0.2,
      source_row_count: 10,
      target_row_count: 10,
      mismatch_count: 0,
      carried_over,
      sample_rows: [],
    };
  }

  private run(
    run_id: string,
    report_name: string,
    status: RunFixture['status'],
    steps: StepFixture[],
    restarted_from_run_id?: string,
  ): RunFixture {
    const failed = status === 'FAILED' ? 1 : 0;
    return {
      run_id,
      label: report_name,
      report_name,
      status,
      started_at: `${report_name.slice(-10)}T08:00:00Z`,
      completed_at: `${report_name.slice(-10)}T08:01:00Z`,
      source_env: 'dev',
      target_env: 'dev',
      total_tests: 3,
      passed: failed ? 1 : 3,
      failed,
      slow: 0,
      error: 0,
      results: steps.map((step, index) => ({
        ...this.result(step.job_name, index + 1, Boolean(step.carried_over)),
        status: step.status,
        effective_status: step.status,
        error_message: step.error_message,
      })),
      steps,
      restarted_from_run_id,
    };
  }

  private visibleRuns(): RunFixture[] {
    const day1 = this.run(DAY_1_RUN, `Business date ${dates[0]}`, 'PASSED', this.passedSteps());
    const failedDay2 = this.run(
      DAY_2_FAILED_RUN,
      `Business date ${dates[1]}`,
      'FAILED',
      this.failedDay2Steps(),
    );
    if (this.phase !== 'completed') return [failedDay2, day1];
    return [
      this.run(DAY_3_RUN, `Business date ${dates[2]}`, 'PASSED', this.passedSteps()),
      this.run(
        DAY_2_RESTARTED_RUN,
        `Business date ${dates[1]}`,
        'PASSED',
        this.passedSteps(true),
        DAY_2_FAILED_RUN,
      ),
      failedDay2,
      day1,
    ];
  }
```

- [ ] **Step 4: Implement route handlers and state transitions**

Add these methods inside `SchedulerScenarioMock`. Parse URLs rather than relying on route-registration order, validate the launch payload, and move to `completed` only after the visible restart action is posted.

```ts
  private json(route: Route, body: unknown, status = 200): Promise<void> {
    return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  }

  private async handleLaunch(route: Route): Promise<void> {
    if (route.request().method() !== 'POST') return route.fallback();
    const payload = route.request().postDataJSON();
    expect(payload.batch).toEqual({
      variable_name: 'business_date',
      start_value: dates[0],
      iterations: 3,
      step_days: 1,
      weekend_policy: 'ignore',
      stop_on_failure: true,
    });
    this.launchBatchRequests += 1;
    return this.json(route, this.batchPayload());
  }

  private batchPayload(): Record<string, unknown> {
    const completed = this.phase === 'completed' ? 3 : 1;
    return {
      batch_id: BATCH_ID,
      status: this.phase === 'completed' ? 'COMPLETED' : 'FAILED',
      variable_name: 'business_date',
      start_value: dates[0],
      current_value: this.phase === 'completed' ? dates[2] : dates[1],
      iterations: 3,
      completed,
      failed: this.phase === 'completed' ? 0 : 1,
      run_ids: this.phase === 'completed'
        ? [DAY_1_RUN, DAY_2_RESTARTED_RUN, DAY_3_RUN]
        : [DAY_1_RUN, DAY_2_FAILED_RUN],
    };
  }

  private async handleBatch(route: Route): Promise<void> {
    if (route.request().method() !== 'GET') return route.fallback();
    return this.json(route, this.batchPayload());
  }

  private async handleRuns(route: Route): Promise<void> {
    if (route.request().method() !== 'GET') return route.fallback();
    return this.json(route, this.visibleRuns().map(({ steps, results, ...summary }) => summary));
  }

  private async handleRun(route: Route): Promise<void> {
    const url = new URL(route.request().url());
    const match = url.pathname.match(/^\/api\/runs\/([^/]+)(?:\/(steps|restart))?$/);
    if (!match) return route.fallback();
    const [, runId, suffix] = match;

    if (suffix === 'restart' && route.request().method() === 'POST') {
      expect(runId).toBe(DAY_2_FAILED_RUN);
      expect(this.phase).toBe('fixed');
      this.restartRequests.push(runId);
      this.phase = 'completed';
      this.day3ExecutionCount += 1;
      const restarted = this.visibleRuns().find((run) => run.run_id === DAY_2_RESTARTED_RUN)!;
      return this.json(route, restarted, 202);
    }

    if (suffix === 'restart') {
      const restarted = this.phase === 'completed'
        ? this.visibleRuns().find((run) => run.run_id === DAY_2_RESTARTED_RUN)
        : null;
      return this.json(route, restarted ?? null);
    }

    const run = this.visibleRuns().find((candidate) => candidate.run_id === runId);
    if (!run) return this.json(route, { detail: 'Run not found' }, 404);
    if (suffix === 'steps') return this.json(route, run.steps);
    return this.json(route, run);
  }

  markFixed(): void {
    expect(this.phase).toBe('failed');
    this.phase = 'fixed';
  }
```

Update `install` so the specific launch route is registered before the broad run routes and broad handlers explicitly fall back for unmatched URLs:

```ts
  async install(page: Page): Promise<void> {
    await page.route('**/api/sequences/*/launch-batch', (route) => this.handleLaunch(route));
    await page.route('**/api/run-batches/**', (route) => this.handleBatch(route));
    await page.route('**/api/runs/**', (route) => this.handleRun(route));
    await page.route('**/api/runs**', (route) => this.handleRuns(route));
  }
```

- [ ] **Step 5: Add the inline page object**

Add `SchedulerPage` after the simulator. It owns selectors and browser actions while leaving assertions about execution counts in the test.

```ts
class SchedulerPage {
  constructor(private readonly page: Page) {}

  async openSequences(): Promise<void> {
    await this.page.goto('/');
    await this.page.getByRole('button', { name: 'Sequences' }).click();
    await expect(this.page.getByTestId('sequences-panel')).toBeVisible();
  }

  async createThreeJobSequence(jobNames: readonly string[]): Promise<void> {
    await this.page.getByTestId('sequence-new-btn').click();
    await this.page.getByTestId('sequence-name-input').fill(SEQUENCE_NAME);

    for (let index = 0; index < jobNames.length; index += 1) {
      if (index > 0) await this.page.getByTestId('sequence-add-step').click();
      await this.page.getByTestId(`sequence-step-job-${index}`).selectOption(jobNames[index]);
      await this.page.getByTestId(`sequence-step-id-${index}`).fill(
        ['ingestion', 'processing', 'report'][index],
      );
      if (index > 0) {
        await this.page
          .getByTestId(`sequence-step-deps-${index}`)
          .getByRole('checkbox', { name: ['ingestion', 'processing'][index - 1] })
          .check();
      }
    }

    await expect(this.page.getByTestId('sequence-graph-preview')).toBeVisible();
    await this.page.getByTestId('sequence-save-btn').click();
    await expect(this.page.getByTestId(`sequence-row-${SEQUENCE_NAME}`)).toBeVisible();
  }

  async launchThreeDayBatch(): Promise<void> {
    await this.page.getByTestId('sequence-launch-btn').click();
    await this.page.getByTestId('launch-sequence-target-env').selectOption('dev');
    await this.page.getByTestId('sequence-repeat-toggle').check();
    await this.page.getByTestId('sequence-batch-variable-select').selectOption('business_date');
    await this.page.getByTestId('sequence-batch-start-date').fill(dates[0]);
    await this.page.getByTestId('sequence-batch-iterations').fill('3');
    await this.page.getByTestId('sequence-batch-step-days').fill('1');
    await this.page.getByTestId('sequence-batch-weekend-policy').selectOption('ignore');
    await this.page.getByTestId('sequence-batch-stop-on-failure').check();
    await this.page.getByTestId('launch-sequence-submit-btn').click();
  }

  async openFailedDay2(): Promise<void> {
    await this.page.getByRole('button', { name: 'History' }).click();
    await this.page.getByTestId(`history-run-row-${DAY_2_FAILED_RUN}`).click();
    await expect(this.page.getByText('Run Detail')).toBeVisible();
  }

  async expectDay2Failure(): Promise<void> {
    await expect(this.page.getByText('FAILED', { exact: true }).first()).toBeVisible();
    await expect(this.page.getByText(JOBS[1], { exact: true })).toBeVisible();
    await expect(this.page.getByText('Invalid processing configuration: transform_mode is missing')).toBeVisible();
    await expect(this.page.getByText(JOBS[2], { exact: true })).toBeVisible();
    await expect(this.page.getByText('BLOCKED', { exact: true })).toBeVisible();
  }

  async applyFix(mock: SchedulerScenarioMock): Promise<void> {
    await this.page.getByRole('button', { name: 'Sequences' }).click();
    await this.page.getByTestId(`sequence-row-${SEQUENCE_NAME}`).click();
    await this.page.getByTestId('sequence-edit-btn').click();
    await this.page.getByTestId('sequence-step-retries-1').fill('1');
    await this.page.getByTestId('sequence-save-btn').click();
    mock.markFixed();
  }

  async restartFailedDay2(): Promise<void> {
    await this.page.getByRole('button', { name: 'History' }).click();
    await this.page.getByTestId(`history-run-row-${DAY_2_FAILED_RUN}`).click();
    await this.page.getByRole('button', { name: 'Restart from failure' }).click();
  }

  async expectRecoveredDay2(): Promise<void> {
    await expect(this.page.getByText(DAY_2_RESTARTED_RUN, { exact: true })).toBeVisible();
    await expect(this.page.getByText('PASSED', { exact: true }).first()).toBeVisible();
    const ingestionRow = this.page.getByRole('row').filter({ hasText: JOBS[0] });
    await expect(ingestionRow.getByText('carried over', { exact: true })).toBeVisible();
    await expect(this.page.getByRole('row').filter({ hasText: JOBS[1] })).toContainText('PASSED');
    await expect(this.page.getByRole('row').filter({ hasText: JOBS[2] })).toContainText('PASSED');
  }

  async expectDay3AndBatchCompleted(): Promise<void> {
    await this.page.getByTestId('run-detail-back-btn').click();
    await expect(this.page.getByTestId(`history-run-row-${DAY_3_RUN}`)).toContainText('PASSED');
    await this.page.getByRole('button', { name: 'Sequences' }).click();
    await expect(this.page.getByTestId('batch-progress-panel')).toContainText('3 / 3 complete');
    await expect(this.page.getByTestId('batch-progress-panel')).toContainText('COMPLETED');
  }
}
```

Before coding, verify exact existing test IDs for step days and weekend policy in `frontend/index.html:3347-3365`; use `sequence-batch-step-days` and `sequence-batch-weekend-policy` only if those exact IDs are present. If an exact ID differs, use the value present in that file rather than adding production selectors.

- [ ] **Step 6: Add the complete staged test**

Append this test. Set up the three jobs through the existing API helpers or real UI before calling `createThreeJobSequence`; prefer API setup because job CRUD is not the behavior under test. If the three display names collide with another run, suffix their stored names and keep the descriptive labels in constants used for logging.

```ts
test.describe('multi-day sequence failure recovery', () => {
  test('resumes Day 2 from Data Processing and completes Day 3 without rerunning Day 1', async ({
    authedPage: page,
  }) => {
    const scenario = new SchedulerScenarioMock();
    await scenario.install(page);
    const scheduler = new SchedulerPage(page);

    // Creation: build one three-job DAG and launch it for three consecutive business dates.
    console.log('[Creation] Creating Data Ingestion -> Data Processing -> Report Generation sequence');
    await scheduler.openSequences();
    await scheduler.createThreeJobSequence(JOBS);
    await scheduler.launchThreeDayBatch();

    // Failure: Day 1 passed; Day 2 stops at Data Processing and leaves reporting blocked.
    console.log('[Failure] Verifying Day 2 Data Processing failure after Day 1 completed');
    await scheduler.openFailedDay2();
    await scheduler.expectDay2Failure();
    expect(scenario.launchBatchRequests).toBe(1);
    expect(scenario.day1ExecutionCount).toBe(1);
    expect(scenario.day2IngestionExecutionCount).toBe(1);

    // Resume: edit the processing configuration and restart from the failed run.
    console.log('[Resume] Applying configuration fix and restarting from the failed step');
    await scheduler.applyFix(scenario);
    await scheduler.restartFailedDay2();
    await scheduler.expectRecoveredDay2();

    // Verification: Day 1 was not relaunched, Day 2 carried ingestion over, and Day 3 completed.
    console.log('[Verification] Confirming resumed Day 2 and completed Day 3');
    await scheduler.expectDay3AndBatchCompleted();
    expect(scenario.restartRequests).toEqual([DAY_2_FAILED_RUN]);
    expect(scenario.launchBatchRequests).toBe(1);
    expect(scenario.day1ExecutionCount).toBe(1);
    expect(scenario.day2IngestionExecutionCount).toBe(1);
    expect(scenario.day3ExecutionCount).toBe(1);
  });
});
```

When implementing, create the three selectable jobs in `beforeAll` using repository helpers and unique stored names. Extend `tests/e2e/api-helpers.ts` only if no existing helper can create the required selectable job records; otherwise keep all changes in the spec. Route handlers must not intercept sequence CRUD, variable CRUD, or job CRUD so the editor remains integrated with the real backend.

- [ ] **Step 7: Run the focused test and fix contract mismatches**

Run:

```powershell
npx playwright test tests/e2e/51-multi-day-sequence-recovery.spec.ts --project=chromium
```

Expected: PASS with the four stage logs. If a fixture shape fails, inspect the current frontend consumer and adjust only the mock response to the actual API contract; do not weaken assertions or add sleeps.

- [ ] **Step 8: Run TypeScript/Playwright discovery validation**

Run:

```powershell
npx playwright test tests/e2e/51-multi-day-sequence-recovery.spec.ts --list
```

Expected: the scenario is listed once under the `chromium` project with no TypeScript compilation error.

- [ ] **Step 9: Run the existing adjacent sequence coverage**

Run:

```powershell
npx playwright test tests/e2e/17-sequences.spec.ts tests/e2e/51-multi-day-sequence-recovery.spec.ts --project=chromium
```

Expected: all tests in both files PASS, proving route interception is local to the new page and does not affect existing sequence coverage.

- [ ] **Step 10: Run repository lint/typecheck commands if available**

`package.json` defines no lint or typecheck script. Confirm this has not changed:

```powershell
npm run
```

Expected: scripts include `test:e2e` but no `lint` or `typecheck`. The Playwright discovery and focused execution commands above are therefore the available TypeScript validation for this change.

- [ ] **Step 11: Review the final diff**

Run:

```powershell
git diff -- tests/e2e/51-multi-day-sequence-recovery.spec.ts docs/superpowers/specs/2026-09-15-multi-day-sequence-recovery-playwright-design.md docs/superpowers/plans/2026-09-15-multi-day-sequence-recovery-playwright.md
git status --short
```

Expected: only the intended new test, approved design, and implementation plan are present for this work; no generated Playwright reports, token caches, databases, or secrets are included.

- [ ] **Step 12: Commit only if explicitly requested**

No commit is authorized by the current user request. If the user later explicitly requests a commit, inspect `git status`, `git diff`, and `git log --oneline -10`, stage only the three intended files, then use:

```powershell
git add tests/e2e/51-multi-day-sequence-recovery.spec.ts docs/superpowers/specs/2026-09-15-multi-day-sequence-recovery-playwright-design.md docs/superpowers/plans/2026-09-15-multi-day-sequence-recovery-playwright.md
git commit -m "test: cover multi-day sequence recovery"
```
