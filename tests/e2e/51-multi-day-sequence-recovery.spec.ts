import { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { authedContext, createPassingFileJob, deleteJob } from './api-helpers';

type StepStatus = 'PASSED' | 'FAILED' | 'BLOCKED';
type MockStep = {
  id: number;
  run_id: string;
  job_name: string;
  step_index: number;
  step_id: string;
  status: StepStatus;
  carried_over?: boolean;
};
type ExecutionEvent = {
  phase: 'initial' | 'restart';
  businessDate: string;
  jobName: string;
  outcome: StepStatus;
  kind: 'executed' | 'carried-over';
};
type RecoveryNames = {
  ingestion: string;
  processing: string;
  report: string;
  sequence: string;
  variable: string;
};

class SequenceRecoveryPage {
  constructor(private readonly page: Page) {}

  async openSequences() {
    await this.page.getByRole('button', { name: 'Sequences' }).click();
    await expect(this.page.getByTestId('sequences-panel')).toBeVisible();
  }

  async createOrderedSequence(name: string, jobs: string[]) {
    await this.page.getByTestId('sequence-new-btn').click();
    await this.page.getByTestId('sequence-name-input').fill(name);
    await this.page.getByTestId('sequence-step-job-0').selectOption(jobs[0]);
    await this.page.getByTestId('sequence-step-id-0').fill('data_ingestion');

    await this.page.getByTestId('sequence-add-step').click();
    await this.page.getByTestId('sequence-step-job-1').selectOption(jobs[1]);
    await this.page.getByTestId('sequence-step-id-1').fill('data_processing');
    await this.page.getByTestId('sequence-step-deps-1').getByRole('checkbox', { name: 'depends on data_ingestion' }).check();

    await this.page.getByTestId('sequence-add-step').click();
    await this.page.getByTestId('sequence-step-job-2').selectOption(jobs[2]);
    await this.page.getByTestId('sequence-step-id-2').fill('report_generation');
    await this.page.getByTestId('sequence-step-deps-2').getByRole('checkbox', { name: 'depends on data_processing' }).check();

    const created = this.page.waitForResponse(
      (response) => response.url().endsWith('/api/sequences') && response.request().method() === 'POST',
    );
    await this.page.getByTestId('sequence-save-btn').click();
    const response = await created;
    expect(response.ok()).toBeTruthy();
    await expect(this.page.getByTestId(`sequence-row-${name}`)).toBeVisible();
    return (await response.json()).id as number;
  }

  async launchRepeat(variableName: string) {
    await this.page.getByTestId('sequence-launch-btn').click();
    await this.page.getByTestId('launch-sequence-target-env').selectOption('dev');
    await this.page.getByTestId('sequence-repeat-toggle').check();
    await this.page.getByTestId('sequence-batch-variable-select').selectOption(variableName);
    await this.page.getByTestId('sequence-batch-start-date').fill('2026-09-14');
    await this.page.getByTestId('sequence-batch-iterations').fill('3');
    await this.page.getByTestId('sequence-batch-step-days').fill('1');
    await this.page.getByTestId('sequence-batch-weekend-policy').selectOption('ignore');
    await this.page.getByTestId('sequence-batch-stop-on-failure').check();
    await this.page.getByTestId('launch-sequence-submit-btn').click();
  }

  async openRun(runId: string) {
    await this.page.getByRole('button', { name: 'History' }).click();
    const back = this.page.getByTestId('run-detail-back-btn');
    if (await back.isVisible()) await back.click();
    await this.page.getByTestId(`history-run-row-${runId}`).click();
  }

  async addAndVerifyProcessingRetry() {
    await this.openSequences();
    await this.page.getByTestId('sequence-edit-btn').click();
    await this.openProcessingAdvanced();
    await this.page.getByTestId('sequence-step-retries-1').fill('1');
    const versionResponse = this.page.waitForResponse(
      (response) => /\/api\/sequences\/\d+\/versions$/.test(response.url()) && response.request().method() === 'POST',
    );
    await this.page.getByTestId('sequence-save-btn').click();
    expect((await versionResponse).ok()).toBeTruthy();
    await expect(this.page.getByTestId('sequence-editor')).toBeHidden();

    await this.page.getByTestId('sequence-edit-btn').click();
    await this.openProcessingAdvanced();
    await expect(this.page.getByTestId('sequence-step-retries-1')).toHaveValue('1');
    await this.page.getByRole('button', { name: 'Cancel', exact: true }).click();
  }

  private async openProcessingAdvanced() {
    await this.page.getByTestId('sequence-step-editor').getByText('Advanced', { exact: true }).nth(1).click();
  }
}

class SequenceRecoveryMockController {
  readonly ledger: ExecutionEvent[] = [];
  readonly batchId: string;
  readonly day1Run = '51000000-0000-0000-0000-000000000001';
  readonly day2FailedRun = '51000000-0000-0000-0000-000000000002';
  readonly day2RestartRun = '51000000-0000-0000-0000-000000000022';
  readonly day3Run = '51000000-0000-0000-0000-000000000003';
  readonly controlledError = 'Controlled configuration error: processing retries must be enabled';
  launchPayload: Record<string, unknown> | undefined;
  sequenceId = 0;
  private resumed = false;
  private fixPersisted = false;

  constructor(private readonly page: Page, private readonly names: RecoveryNames, suffix: string) {
    this.batchId = `batch-recovery-${suffix}`;
  }

  async install() {
    await this.page.route('**/api/sequences/*/versions', async (route) => {
      const payload = route.request().postDataJSON();
      const processing = payload.steps?.find((step: { step_id?: string }) => step.step_id === 'data_processing');
      expect(processing?.max_retries).toBe(1);
      const response = await route.fetch();
      this.fixPersisted = response.ok();
      await route.fulfill({ response });
    });
    await this.page.route('**/api/sequences/*/launch-batch', async (route) => {
      this.launchPayload = route.request().postDataJSON();
      this.executeInitialBatch();
      await route.fulfill({ json: this.batchPayload() });
    });
    await this.page.route(`**/api/run-batches/${this.batchId}`, async (route) => {
      await route.fulfill({ json: this.batchPayload() });
    });
    await this.page.route('**/api/runs**', async (route) => this.handleRuns(route));
  }

  private executeInitialBatch() {
    if (this.ledger.length) return;
    this.executeDay('initial', '2026-09-14', [
      [this.names.ingestion, 'PASSED'],
      [this.names.processing, 'PASSED'],
      [this.names.report, 'PASSED'],
    ]);
    this.executeDay('initial', '2026-09-15', [
      [this.names.ingestion, 'PASSED'],
      [this.names.processing, 'FAILED'],
    ]);
  }

  private executeRestart() {
    this.ledger.push({ phase: 'restart', businessDate: '2026-09-15', jobName: this.names.ingestion, outcome: 'PASSED', kind: 'carried-over' });
    this.executeDay('restart', '2026-09-15', [[this.names.processing, 'PASSED'], [this.names.report, 'PASSED']]);
    this.executeDay('restart', '2026-09-16', [
      [this.names.ingestion, 'PASSED'],
      [this.names.processing, 'PASSED'],
      [this.names.report, 'PASSED'],
    ]);
  }

  private executeDay(phase: ExecutionEvent['phase'], businessDate: string, jobs: Array<[string, StepStatus]>) {
    for (const [jobName, outcome] of jobs) {
      this.ledger.push({ phase, businessDate, jobName, outcome, kind: 'executed' });
    }
  }

  private batchPayload() {
    return {
      batch_id: this.batchId,
      target_type: 'sequence',
      target_id: this.sequenceId,
      status: this.resumed ? 'COMPLETED' : 'STOPPED',
      variable_name: this.names.variable,
      start_value: '2026-09-14',
      iterations: 3,
      step_days: 1,
      weekend_policy: 'ignore',
      stop_on_failure: true,
      completed: this.resumed ? 3 : 2,
      current_iteration: this.resumed ? 3 : 2,
      current_value: this.resumed ? '2026-09-16' : '2026-09-15',
      created_at: '2026-09-14T09:00:00Z',
      completed_at: this.resumed ? '2026-09-16T09:03:00Z' : null,
      runs: this.resumed
        ? [
            this.batchMember(this.day1Run, 'PASSED', 1, '2026-09-14'),
            this.batchMember(this.day2RestartRun, 'PASSED', 2, '2026-09-15'),
            this.batchMember(this.day3Run, 'PASSED', 3, '2026-09-16'),
          ]
        : [
            this.batchMember(this.day1Run, 'PASSED', 1, '2026-09-14'),
            this.batchMember(this.day2FailedRun, 'FAILED', 2, '2026-09-15'),
            { run_id: null, status: 'PENDING', iteration_index: 3, business_date: '2026-09-16', started_at: null, completed_at: null },
          ],
    };
  }

  private batchMember(runId: string, status: StepStatus, iterationIndex: number, businessDate: string) {
    return {
      run_id: runId,
      status,
      iteration_index: iterationIndex,
      business_date: businessDate,
      started_at: `${businessDate}T09:00:00Z`,
      completed_at: `${businessDate}T09:01:00Z`,
    };
  }

  private async handleRuns(route: Parameters<Page['route']>[1] extends (route: infer R) => unknown ? R : never) {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'GET' && path === '/api/runs') {
      const rows = this.resumed
        ? [this.summary(this.day3Run, 'PASSED', 3, 0), this.summary(this.day2RestartRun, 'PASSED', 3, 0), this.summary(this.day1Run, 'PASSED', 3, 0)]
        : [this.summary(this.day2FailedRun, 'FAILED', 1, 1), this.summary(this.day1Run, 'PASSED', 3, 0)];
      await route.fulfill({ json: rows });
      return;
    }
    const stepMatch = path.match(/^\/api\/runs\/([^/]+)\/steps$/);
    if (stepMatch) {
      await route.fulfill({ json: this.stepsFor(stepMatch[1]) });
      return;
    }
    const restartMatch = path.match(/^\/api\/runs\/([^/]+)\/restart$/);
    if (restartMatch && request.method() === 'POST') {
      expect(restartMatch[1]).toBe(this.day2FailedRun);
      if (!this.fixPersisted) {
        await route.fulfill({ status: 409, json: { detail: 'sequence fix was not persisted' } });
        return;
      }
      this.executeRestart();
      this.resumed = true;
      await route.fulfill({ status: 202, json: this.summary(this.day2RestartRun, 'PASSED', 3, 0) });
      return;
    }
    if (restartMatch) {
      await route.fulfill({ json: null });
      return;
    }
    const detailMatch = path.match(/^\/api\/runs\/([^/]+)$/);
    if (detailMatch) {
      await route.fulfill({ json: this.detailFor(detailMatch[1]) });
      return;
    }
    await route.continue();
  }

  private summary(runId: string, status: string, passed: number, failed: number) {
    return {
      run_id: runId,
      label: `${this.names.sequence} · ${runId.slice(0, 8)}`,
      report_name: this.names.sequence,
      status,
      started_at: '2026-09-15T09:00:00Z',
      completed_at: '2026-09-15T09:01:00Z',
      total_tests: passed + failed,
      passed,
      failed,
      slow: 0,
      error: 0,
      run_type: 'reconciliation',
      source_env: 'dev',
      target_env: 'dev',
      target_type: 'sequence',
      target_name: this.names.sequence,
    };
  }

  private stepsFor(runId: string): MockStep[] {
    if (runId === this.day2FailedRun) return this.failedSteps();
    if (runId === this.day2RestartRun) {
      return [
        { ...this.failedSteps()[0], id: 221, run_id: runId, carried_over: true },
        { ...this.failedSteps()[1], id: 222, run_id: runId, status: 'PASSED' },
        { ...this.failedSteps()[2], id: 223, run_id: runId, status: 'PASSED' },
      ];
    }
    return this.passedSteps(runId, runId === this.day1Run ? 101 : 301);
  }

  private failedSteps(): MockStep[] {
    return [
      { id: 201, run_id: this.day2FailedRun, job_name: this.names.ingestion, step_index: 0, step_id: 'data_ingestion', status: 'PASSED' },
      { id: 202, run_id: this.day2FailedRun, job_name: this.names.processing, step_index: 1, step_id: 'data_processing', status: 'FAILED' },
      { id: 203, run_id: this.day2FailedRun, job_name: this.names.report, step_index: 2, step_id: 'report_generation', status: 'BLOCKED' },
    ];
  }

  private passedSteps(runId: string, baseId: number): MockStep[] {
    return [
      { id: baseId, run_id: runId, job_name: this.names.ingestion, step_index: 0, step_id: 'data_ingestion', status: 'PASSED' },
      { id: baseId + 1, run_id: runId, job_name: this.names.processing, step_index: 1, step_id: 'data_processing', status: 'PASSED' },
      { id: baseId + 2, run_id: runId, job_name: this.names.report, step_index: 2, step_id: 'report_generation', status: 'PASSED' },
    ];
  }

  private detailFor(runId: string) {
    const failed = runId === this.day2FailedRun;
    const restarted = runId === this.day2RestartRun;
    return {
      ...this.summary(runId, failed ? 'FAILED' : 'PASSED', failed ? 1 : 3, failed ? 1 : 0),
      restarted_from_run_id: restarted ? this.day2FailedRun : null,
      config_snapshot: { business_date: failed || restarted ? '2026-09-15' : runId === this.day1Run ? '2026-09-14' : '2026-09-16' },
      results: failed
        ? [this.result(201, this.names.ingestion, 'PASSED'), this.result(202, this.names.processing, 'FAILED', { error: this.controlledError }), this.result(203, this.names.report, 'BLOCKED')]
        : restarted
          ? [this.result(221, this.names.ingestion, 'PASSED', { carried: true }), this.result(222, this.names.processing, 'PASSED'), this.result(223, this.names.report, 'PASSED')]
          : this.stepsFor(runId).map((step) => this.result(step.id, step.job_name, 'PASSED')),
    };
  }

  private result(id: number, jobName: string, status: StepStatus, options: { carried?: boolean; error?: string } = {}) {
    return {
      id,
      query_name: jobName,
      status,
      effective_status: status,
      duration_seconds: options.carried ? 0 : 0.2,
      source_row_count: status === 'BLOCKED' ? 0 : 3,
      target_row_count: status === 'BLOCKED' ? 0 : 3,
      value_mismatch_count: 0,
      missing_in_target_count: 0,
      missing_in_source_count: 0,
      error_message: options.error || null,
      carried_over: Boolean(options.carried),
    };
  }
}

test('recovers a stopped multi-day sequence without rerunning successful ingestion', async ({ authedPage: page, adminToken }) => {
  test.setTimeout(60_000);
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const names: RecoveryNames = {
    ingestion: `e2e-data-ingestion-${suffix}`,
    processing: `e2e-data-processing-${suffix}`,
    report: `e2e-report-generation-${suffix}`,
    sequence: `e2e-multi-day-recovery-${suffix}`,
    variable: `e2e_business_date_${suffix.replaceAll('-', '_')}`,
  };
  const ctx = await authedContext(adminToken);
  const mock = new SequenceRecoveryMockController(page, names, suffix);
  let sequenceId: number | undefined;
  let variableId: number | undefined;

  try {
    // Creation
    console.log('Creation: create real jobs, variable, and ordered sequence');
    await Promise.all([
      createPassingFileJob(ctx, names.ingestion),
      createPassingFileJob(ctx, names.processing),
      createPassingFileJob(ctx, names.report),
    ]);
    const variableResponse = await ctx.post('/api/variables', {
      data: { name: names.variable, var_type: 'date', default_value: '2026-09-14', description: 'multi-day recovery business date' },
    });
    expect(variableResponse.ok()).toBeTruthy();
    variableId = (await variableResponse.json()).id;
    await mock.install();

    await page.reload();
    const recovery = new SequenceRecoveryPage(page);
    await recovery.openSequences();
    sequenceId = await recovery.createOrderedSequence(names.sequence, [names.ingestion, names.processing, names.report]);
    mock.sequenceId = sequenceId;

    // Failure
    console.log('Failure: launch three business dates and stop on the controlled Day 2 error');
    await recovery.launchRepeat(names.variable);
    expect(mock.launchPayload).toEqual({
      source_env: 'dev',
      target_env: 'dev',
      batch: {
        variable_name: names.variable,
        start_value: '2026-09-14',
        iterations: 3,
        step_days: 1,
        weekend_policy: 'ignore',
        stop_on_failure: true,
      },
    });
    await expect(page.getByTestId('batch-progress-status')).toContainText('status STOPPED');
    await expect(page.getByTestId('batch-loop-1')).toContainText('PASSED');
    await expect(page.getByTestId('batch-loop-2')).toContainText('FAILED');
    await expect(page.getByTestId('batch-loop-3')).toContainText('PENDING');

    await recovery.openRun(mock.day2FailedRun);
    const detailHeader = page.getByTestId('run-detail-back-btn').locator('xpath=..');
    await expect(detailHeader).toContainText('FAILED');
    const resultsTable = page.getByRole('table', { name: 'history table 1' });
    const processingFailure = resultsTable.locator('tbody').filter({
      has: page.getByText(names.processing, { exact: true }),
    });
    await expect(processingFailure.locator('tr').first()).toContainText('FAILED');
    await expect(processingFailure.getByText(mock.controlledError, { exact: true })).toBeVisible();
    const blockedReport = resultsTable.locator('tbody').filter({
      has: page.getByText(names.report, { exact: true }),
    });
    await expect(blockedReport.locator('tr').first()).toContainText('BLOCKED');
    await expect(page.getByRole('button', { name: 'Restart from failure' })).toBeVisible();

    // Resume
    console.log('Resume: save a visible sequence fix and restart from the failed run');
    await recovery.addAndVerifyProcessingRetry();
    await recovery.openRun(mock.day2FailedRun);
    await page.getByRole('button', { name: 'Restart from failure' }).click();
    await expect(page.getByText('Restarted from')).toBeVisible();
    for (const jobName of [names.ingestion, names.processing, names.report]) {
      const resultGroup = page.getByText(jobName, { exact: true }).locator('xpath=ancestor::tbody[1]');
      await expect(resultGroup).toContainText('PASSED');
      if (jobName === names.ingestion) await expect(resultGroup).toContainText('carried over');
    }

    // Verification
    console.log('Verification: confirm recovery completion and execution-event integrity');
    await page.reload();
    await recovery.openSequences();
    await expect(page.getByTestId('batch-progress-status')).toContainText('status COMPLETED');
    await expect(page.getByTestId('batch-progress-panel')).toContainText('3 / 3 complete');
    for (const [loop, date] of [[1, '2026-09-14'], [2, '2026-09-15'], [3, '2026-09-16']] as const) {
      const loopCard = page.getByTestId(`batch-loop-${loop}`);
      await expect(loopCard).toContainText(date);
      await expect(loopCard).toContainText('PASSED');
      await loopCard.getByRole('button', { name: `Toggle loop ${loop} steps` }).click();
      for (const jobName of [names.ingestion, names.processing, names.report]) {
        const stepRow = loopCard.getByText(jobName, { exact: true }).locator('xpath=..');
        await expect(stepRow).toContainText('PASSED');
      }
    }

    const restartExecutions = mock.ledger.filter((event) => event.phase === 'restart' && event.kind === 'executed');
    expect(restartExecutions.filter((event) => event.businessDate === '2026-09-14')).toEqual([]);
    expect(restartExecutions.filter((event) => event.businessDate === '2026-09-15' && event.jobName === names.ingestion)).toEqual([]);
    expect(mock.ledger).toContainEqual({ phase: 'restart', businessDate: '2026-09-15', jobName: names.ingestion, outcome: 'PASSED', kind: 'carried-over' });
    expect(restartExecutions.filter((event) => event.businessDate === '2026-09-15').map((event) => event.jobName)).toEqual([names.processing, names.report]);
    expect(restartExecutions.filter((event) => event.businessDate === '2026-09-16').map((event) => event.jobName)).toEqual([names.ingestion, names.processing, names.report]);
    expect(mock.ledger.filter((event) => event.phase === 'initial' && event.businessDate === '2026-09-14' && event.kind === 'executed').map((event) => event.jobName)).toEqual([names.ingestion, names.processing, names.report]);
  } finally {
    if (sequenceId !== undefined) await ctx.delete(`/api/sequences/${sequenceId}`);
    if (variableId !== undefined) await ctx.delete(`/api/variables/${variableId}`);
    await Promise.all([deleteJob(ctx, names.ingestion), deleteJob(ctx, names.processing), deleteJob(ctx, names.report)]);
    await ctx.dispose();
  }
});
