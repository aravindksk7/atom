// ATOM regression demo: narrated, highlighted, recorded.
//
//   Business Date -> 4 ETL Sets -> Output Files -> Reconciliation -> Restart from failure
//   -> Impact Validation -> BO Reports -> Consolidated Result -> CI/CD
//
// Every "STEP" comment below is written to be read aloud, and the step banner on screen shows
// the same step number. After each take, demo/output/timeline.md lists every step with its
// timecode in the video. Change the pace with DEMO_PACE (see demo/README.md).
//
// ATOM is the real application running real runs. Oracle, CrushFTP, AWS S3, SAP and GitLab are
// simulated, which the opening title card says on screen.
import { APIRequestContext, expect, Locator, Page, request as pwRequest, test } from '@playwright/test';
import fs from 'node:fs';
import https from 'node:https';
import path from 'node:path';
import { Presenter } from './helpers/presenter';
import { writeTimeline } from './helpers/timeline';
import {
  allEtls, BUSINESS_DATE, ETLS, EXEC_JOBS, GITLAB_SIM_URL, IMPACTED_FIELD, RECON_JOBS, SEQUENCE_NAME,
} from './seed/seed_atom';

const OUTPUT_DIR = path.join(__dirname, 'output');
const SAPDS_URL = 'https://sapds:8444';
const TOTAL_PARTS = 7;
const EXECUTION_JOBS = 25;
const RECONCILIATION_JOBS = 10;
const STEP_COUNT = EXECUTION_JOBS + RECONCILIATION_JOBS;
// Steps that passed on the first run and are not run again: 25 execution jobs, 3 comparisons
// that match, and the first BO report.
const CARRIED_OVER = 29;
// Missing and additional CSV records, wrong XML and Oracle notionals, the row missing from
// Postgres and the BO total.
const OPEN_EXCEPTIONS = 6;
const TERMINAL = /^(PASSED|FAILED|ERROR|CANCELLED|BLOCKED)$/;
const ACCEPT_NOTE = 'Expected: new fee model, CR-2041';

type Step = { step_id: string; job_name: string; status: string; carried_over?: boolean };

const nav = (page: Page, tab: string) => page.getByTestId(`nav-tab-${tab}`);

function resultRow(page: Page, jobName: string): Locator {
  return page.locator('table[aria-label="history table 1"] > tbody > tr', { hasText: jobName }).first();
}

/** Every Run_Batch_Job the SAP DS mock received, in arrival order. */
function sapDsCalls(): Promise<Array<{ job_name: string; variables: Record<string, string> }>> {
  return new Promise((resolve, reject) => {
    https.get(`${SAPDS_URL}/debug/runs`, { rejectUnauthorized: false }, (response) => {
      let body = '';
      response.on('data', (chunk) => { body += chunk; });
      response.on('end', () => resolve(JSON.parse(body)));
    }).on('error', reject);
  });
}

// A single dropped poll must not end a long take: the next poll simply tries again. The
// assertions after a run has finished read these same values and do catch a real problem.
async function getSteps(api: APIRequestContext, runId: string): Promise<Step[]> {
  try {
    const response = await api.get(`/api/runs/${runId}/steps`);
    return response.ok() ? await response.json() : [];
  } catch {
    return [];
  }
}

async function runStatus(api: APIRequestContext, runId: string): Promise<string> {
  try {
    const response = await api.get(`/api/runs/${runId}/status`);
    return response.ok() ? String((await response.json()).status) : '';
  } catch {
    return '';
  }
}

/** Follows a run on the Monitor, keeping the running job on screen. */
class RunFollower {
  constructor(
    private readonly page: Page,
    private readonly api: APIRequestContext,
    private readonly presenter: Presenter,
    private readonly runId: string,
  ) {}

  private isFinished(steps: Step[], name: string): boolean {
    const step = steps.find((candidate) => candidate.step_id === name);
    return Boolean(step) && !['PENDING', 'RUNNING'].includes(step!.status);
  }

  /** The row showing one execution level on the Monitor's timeline. */
  level(jobName: string): Locator {
    return this.page.getByTestId(`monitor-step-${jobName}`)
      .locator('xpath=ancestor::div[contains(@class, "py-1")][1]');
  }

  /**
   * Waits for jobs to finish, scrolling to whichever is running. Returns the largest number of
   * them seen running at the same moment. Runs in real time whatever DEMO_PACE is.
   */
  async until(jobNames: string[]): Promise<number> {
    const deadline = Date.now() + 15 * 60_000;
    let peakRunning = 0;
    let highlightedParallel = false;
    while (Date.now() < deadline) {
      const steps = await getSteps(this.api, this.runId);
      const mine = steps.filter((step) => jobNames.includes(step.step_id));
      const running = mine.filter((step) => step.status === 'RUNNING');
      peakRunning = Math.max(peakRunning, running.length);
      const current = steps.find((step) => step.status === 'RUNNING');
      if (current) {
        const chip = this.page.getByTestId(`monitor-step-${current.step_id}`);
        if (await chip.count()) await this.presenter.centre(chip).catch(() => undefined);
      }
      // The Monitor refreshes a few seconds behind the API, so highlight the level only once the
      // screen itself shows several jobs running.
      if (running.length > 1 && !highlightedParallel) {
        const level = this.level(running[0].step_id);
        const onScreen = (await level.textContent().catch(() => '')) ?? '';
        if ((onScreen.match(/RUNNING/g) ?? []).length > 1) {
          highlightedParallel = true;
          await this.presenter.show(level).catch(() => undefined);
        }
      }
      if (jobNames.every((name) => this.isFinished(steps, name))) return peakRunning;
      if (TERMINAL.test(await runStatus(this.api, this.runId))) return peakRunning;
      await this.page.waitForTimeout(1000);
    }
    throw new Error(`Timed out waiting for: ${jobNames.join(', ')}`);
  }

  async untilRunEnds(): Promise<string> {
    await expect.poll(() => runStatus(this.api, this.runId), { timeout: 15 * 60_000, intervals: [1000] })
      .toMatch(TERMINAL);
    return runStatus(this.api, this.runId);
  }
}

test('ATOM end-to-end regression for one business date', async ({ page }) => {
  const presenter = new Presenter(page, TOTAL_PARTS);
  const startedAt = Date.now();
  const api = await pwRequest.newContext({
    baseURL: process.env.DEMO_BASE_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.DEMO_TOKEN}` },
  });
  await page.addInitScript((token) => {
    window.sessionStorage.setItem('etl_token', token);
  }, process.env.DEMO_TOKEN as string);
  await presenter.install();

  try {
    // Recording starts with the browser. A dark page avoids opening on a white flash.
    await page.setContent('<body style="background:#0f172a"></body>');
    await page.goto('/');
    await expect(page.getByTestId('auth-status-connected')).toBeVisible();

    await presenter.slate(
      'ATOM End-to-End Regression',
      'Business Date → 4 ETL Sets → Output Files → Reconciliation → Restart → Impact Validation → BO Reports → Consolidated Result → CI/CD',
      'Demo environment: Oracle, CrushFTP, AWS S3, SAP and GitLab are simulated.',
      8000,
    );

    // ---------------------------------------------------------------------------------------------
    // 1. CONFIGURE
    // ---------------------------------------------------------------------------------------------
    await presenter.part(1, 'Configure', 'Select the business date and the ETL sets');

    // STEP 1: Opening Sequences. A sequence is ATOM's saved, versioned definition of what runs and
    // in what order. It mirrors the production schedule, so we test the order we actually run.
    await presenter.step('Open the saved sequences', 'A sequence is the versioned definition of what runs, and in what order');
    await presenter.click(nav(page, 'sequences'));

    // STEP 2: Selecting Nightly Regression. It holds 35 jobs: 25 that execute, and 10 that compare
    // against Production. They are ETLs, file watchers, CrushFTP fetches, AWS S3 publishing and
    // validation steps, all in one flow.
    await presenter.step('Select Nightly Regression', '35 jobs: 25 to execute and 10 reconciliations against Production');
    await presenter.click(page.getByTestId(`sequence-row-${SEQUENCE_NAME}`));
    await presenter.show(page.getByTestId('sequence-flow-badge'));

    // STEP 3: Opening the flow editor to see how the jobs are arranged. This is where we choose
    // series, parallel or a mix of the two, stage by stage.
    await presenter.step('Series, parallel and hybrid in one flow', 'Each stage runs its jobs one after another, or together');
    await presenter.click(page.getByTestId('sequence-edit-btn'));
    await expect(page.getByTestId('sequence-flow-custom')).toBeChecked();
    await presenter.show(page.getByTestId('sequence-flow-custom'));
    // Stage 2 is Set 2, which runs its ETLs in parallel.
    await presenter.show(page.getByTestId('sequence-stage-mode-1'));

    // STEP 4: The execution order preview. Each line is a level: one job on a line runs alone, and
    // several on a line run together. Set 1 is in series, Set 2 is in parallel, and Set 3 is a
    // hybrid: one job, then three together, then one again.
    await presenter.step('Preview the execution order', 'One job on a line runs alone, several on a line run together');
    await presenter.show(page.getByTestId('sequence-graph-preview'));
    await presenter.click(page.getByTestId('sequence-editor').getByRole('button', { name: 'Cancel' }));

    // STEP 5: Starting a run, and entering the business date. This single value goes to every ETL,
    // every query, every file path and every BO report prompt in the run.
    await presenter.step('Enter the business date', `run_date=${BUSINESS_DATE} goes to every job in the run`);
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    await presenter.click(page.getByTestId('sequence-launch-btn'));
    await presenter.type(
      page.getByTestId('launch-sequence-variable-overrides-textarea'),
      `run_date=${BUSINESS_DATE}`,
    );

    // STEP 6: Launching. From here on ATOM drives everything, with no manual steps.
    await presenter.step('Launch', 'From here on ATOM drives everything');
    const launched = page.waitForResponse(
      (response) => /\/api\/sequences\/\d+\/launch$/.test(response.url())
        && response.request().method() === 'POST',
    );
    await presenter.click(page.getByTestId('launch-sequence-submit-btn'));
    const launchResponse = await launched;
    expect(launchResponse.ok(), 'the sequence launch was accepted').toBeTruthy();
    const firstRunId = String((await launchResponse.json()).run_id);
    const follower = new RunFollower(page, api, presenter, firstRunId);

    // ---------------------------------------------------------------------------------------------
    // 2. EXECUTE
    // ---------------------------------------------------------------------------------------------
    await presenter.part(2, 'Execute', '25 jobs: ETLs, file watcher, CrushFTP, AWS S3 and step validation');

    // STEP 7: Opening the Execution Monitor to watch the run live. The counters update as each job
    // finishes, and the timeline below shows every job on its execution level.
    await presenter.step('Watch the run live', 'Counters update as each job finishes');
    await presenter.click(nav(page, 'monitor'));
    const monitorRun = page.getByTestId(`monitor-run-${firstRunId}`);
    await expect(monitorRun).toBeVisible({ timeout: 60_000 });
    await presenter.show(monitorRun.locator('.grid-stat'), 'short');
    await expect(monitorRun.getByTestId('monitor-step-timeline')).toBeVisible({ timeout: 60_000 });

    // STEP 8: Set 1 runs in series. Here the file watcher is waiting for the upstream arrival signal
    // on CrushFTP, and nothing starts until it lands. There it is. Now we fetch the Production file,
    // then three ETLs run one after another, each passed the business date. A validation step then
    // confirms the output is there before anything continues.
    await presenter.step('Set 1: SERIES', 'File watcher → CrushFTP fetch → 3 ETLs → validation, one after another');
    const watcher = page.getByTestId(`monitor-step-${EXEC_JOBS.watchTrigger}`);
    await presenter.show(watcher);
    expect(await watcher.textContent(), 'the watcher is waiting for its signal').toContain('RUNNING');
    fs.writeFileSync(path.posix.join('/demo-data/crushftp/inbound', BUSINESS_DATE, 'trigger.done'), BUSINESS_DATE);
    const set1Peak = await follower.until([
      EXEC_JOBS.watchTrigger, EXEC_JOBS.fetchFlatFile, ...ETLS[1], EXEC_JOBS.validateSet1,
    ]);
    expect(set1Peak, 'Set 1 ran one job at a time').toBe(1);

    // STEP 9: Set 2 runs in parallel. Six ETLs start together, and you can see several running at
    // the same moment. The validation step waits until all six have finished.
    await presenter.step('Set 2: PARALLEL', '6 ETLs running at the same time, then one validation step');
    const set2Peak = await follower.until(ETLS[2]);
    expect(set2Peak, 'Set 2 ran all its ETLs at the same time').toBe(ETLS[2].length);
    await follower.until([EXEC_JOBS.validateSet2]);

    // STEP 10: Set 3 is a hybrid. One ETL runs alone, then three run together, then one more runs
    // alone, followed by validation. Real batch schedules look like this, and ATOM reproduces it.
    await presenter.step('Set 3: HYBRID', 'One ETL → 3 together → one ETL → validation');
    await follower.until([ETLS[3][0]]);
    const set3Peak = await follower.until(ETLS[3].slice(1, 4));
    expect(set3Peak, 'the middle of Set 3 ran at the same time').toBe(3);
    await follower.until([ETLS[3][4], EXEC_JOBS.validateSet3]);

    // STEP 11: Set 4 finishes the execution in series, then publishes the generated files to AWS S3
    // and validates that they arrived.
    await presenter.step('Set 4: SERIES with AWS S3', '4 ETLs → publish to AWS S3 → validate the upload');
    await follower.until([...ETLS[4], EXEC_JOBS.publishS3, EXEC_JOBS.validateS3]);

    // ---------------------------------------------------------------------------------------------
    // 3. RECONCILE
    // ---------------------------------------------------------------------------------------------
    await presenter.part(3, 'Reconcile', '10 comparisons across files, databases and BO reports');

    // STEP 12: The reconciliation runs. Seven comparisons run in parallel: generated CSV and XML
    // files against baselines in AWS S3, Postgres against Oracle, and databases against the CrushFTP
    // flat file. Three BusinessObjects comparisons then run in series.
    await presenter.step('10 comparisons', '7 file and database comparisons in parallel, then 3 BO reports in series');
    const firstStatus = await follower.untilRunEnds();

    // Validation: correct ETLs, sequence and business date. These come from the SAP DS mock's own
    // record of what it was asked to run, not from ATOM's view of itself.
    const firstCalls = await sapDsCalls();
    const called = firstCalls.map((call) => call.job_name);
    expect([...called].sort(), 'the right ETLs ran').toEqual([...allEtls()].sort());
    for (const call of firstCalls) {
      expect(call.variables.G_BUSINESS_DATE, `${call.job_name} got the business date`).toBe(BUSINESS_DATE);
    }
    const lastOfSet = (set: 1 | 2 | 3) => Math.max(...ETLS[set].map((name) => called.indexOf(name)));
    const firstOfSet = (set: 2 | 3 | 4) => Math.min(...ETLS[set].map((name) => called.indexOf(name)));
    expect(lastOfSet(1), 'Set 1 finished before Set 2').toBeLessThan(firstOfSet(2));
    expect(lastOfSet(2), 'Set 2 finished before Set 3').toBeLessThan(firstOfSet(3));
    expect(lastOfSet(3), 'Set 3 finished before Set 4').toBeLessThan(firstOfSet(4));

    const firstSteps = await getSteps(api, firstRunId);
    expect(firstSteps.length, 'every job is in the run').toBe(STEP_COUNT);
    const statusOf = (steps: Step[], name: string) =>
      steps.find((step) => step.step_id === name)?.status;
    expect(statusOf(firstSteps, RECON_JOBS.boDaily), 'the late file stops this job').toMatch(/^(ERROR|FAILED)$/);
    expect(statusOf(firstSteps, RECON_JOBS.boSummary), 'the next report never ran').toBe('BLOCKED');
    expect(firstStatus).toMatch(TERMINAL);

    // STEP 13: The run has stopped, so let's see the result in History.
    await presenter.step('Open the result', 'The run has stopped: let us see why');
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    await presenter.click(nav(page, 'history'));
    await presenter.click(page.getByTestId(`history-run-row-${firstRunId}`));
    await presenter.show(page.locator('.grid-stat').first());

    // STEP 14: The CSV reconciliation. ATOM paired each generated file with its Production baseline
    // in AWS S3, matched the records by key and compared every field. These counts are the total
    // across all the file pairs.
    await presenter.step('Files vs Production baselines', 'Each file is paired with its baseline, matched by key, and compared');
    await presenter.show(resultRow(page, RECON_JOBS.csv));
    await presenter.show(resultRow(page, RECON_JOBS.xml));

    // STEP 15: Database comparisons: QA Postgres against Production Oracle. The balances match, and
    // the trades have differences we will review shortly.
    await presenter.step('Postgres vs Oracle', 'Balances match, trades have differences to review');
    await presenter.show(resultRow(page, RECON_JOBS.balances));
    await presenter.show(resultRow(page, RECON_JOBS.trades));

    // STEP 16: Databases against the flat file fetched from CrushFTP.
    await presenter.step('Databases vs the CrushFTP flat file', 'The file fetched in Set 1, compared with Postgres and Oracle');
    await presenter.show(resultRow(page, RECON_JOBS.positionsFile));
    await presenter.show(resultRow(page, RECON_JOBS.oracleFile));

    // ---------------------------------------------------------------------------------------------
    // 4. RESTART
    // ---------------------------------------------------------------------------------------------
    await presenter.part(4, 'Restart', 'Continue from the failure, without repeating what already passed');

    // STEP 17: Here is an unintended failure. The Production copy of the Daily Sales report was not
    // delivered, so this job failed with an error. It isn't a data problem, it's an operational one.
    // The next BO report never ran, because it depends on this one.
    await presenter.step('An unintended failure', 'The Production Daily Sales file was not delivered, so this job failed');
    await presenter.show(page.locator(`tr[data-step-id="${RECON_JOBS.boDaily}"]`));
    await presenter.show(page.locator(`tr[data-step-id="${RECON_JOBS.boSummary}"]`));
    await presenter.show(resultRow(page, RECON_JOBS.boDaily));

    // STEP 18: Operations have now delivered the missing file. In a traditional setup we would run
    // the whole batch again. ATOM can restart from the failure instead, so none of the ETLs repeat.
    await presenter.step('Restart from failure', 'The file has been delivered. Passed jobs are kept, and only the rest run');
    const lateBaseline = (folder: string) =>
      path.posix.join('/demo-data', folder, BUSINESS_DATE, 'daily_sales.csv');
    fs.copyFileSync(lateBaseline('bo-late'), lateBaseline('bo'));
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    const restarted = page.waitForResponse(
      (response) => response.url().endsWith(`/api/runs/${firstRunId}/restart`)
        && response.request().method() === 'POST',
    );
    await presenter.click(page.getByRole('button', { name: 'Restart from failure' }));
    const restartResponse = await restarted;
    expect(restartResponse.ok(), 'the restart was accepted').toBeTruthy();
    const runId = String((await restartResponse.json()).run_id);

    // STEP 19: Watching the restarted run. All 25 execution jobs are carried over as already passed,
    // and ATOM continues from the failed job. The report that was blocked now runs as well.
    await presenter.step('Continuing from the failure', '25 execution jobs carried over, not run again');
    await presenter.click(nav(page, 'monitor'));
    const restartFollower = new RunFollower(page, api, presenter, runId);
    const status = await restartFollower.untilRunEnds();

    // Validation: the restart reused the earlier work, and only ran what it should have.
    const callsAfterRestart = await sapDsCalls();
    expect(callsAfterRestart.length, 'no ETL ran a second time').toBe(allEtls().length);
    const steps = await getSteps(api, runId);
    expect(steps.length).toBe(STEP_COUNT);
    expect(steps.filter((step) => step.carried_over).length, 'passed jobs were carried over').toBe(CARRIED_OVER);
    expect(statusOf(steps, RECON_JOBS.boDaily), 'the delivered file fixed the job').toBe('PASSED');
    expect(statusOf(steps, RECON_JOBS.boSummary), 'the blocked report ran and found the difference').toBe('FAILED');
    expect(status, 'data exceptions keep the run failed').toBe('FAILED');
    const finished = await (await api.get(`/api/runs/${runId}`)).json();
    // STEP 20 says these numbers aloud, so a take where they differ must fail.
    expect([finished.total_tests, finished.passed, finished.failed]).toEqual([STEP_COUNT, 30, 5]);

    // STEP 20: The restarted run, linked to the original. Of 35 jobs, 30 passed and 5 failed, and
    // those 5 are now true data findings rather than operational problems. This ETL says "carried
    // over": it ran once, in the first run, and its result is kept.
    await presenter.step('The restarted run', '35 jobs: 30 passed and 5 failed, with the ETLs carried over');
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    await presenter.click(nav(page, 'history'));
    // History reopens on the run it last showed, so go back to the list first.
    const backToList = page.getByTestId('run-detail-back-btn');
    if (await backToList.isVisible()) await presenter.click(backToList);
    await presenter.click(page.getByTestId('history-refresh-btn'));
    await presenter.click(page.getByTestId(`history-run-row-${runId}`));
    await presenter.show(page.locator('.grid-stat').first());
    await presenter.show(page.locator(`tr[data-step-id="${ETLS[1][0]}"]`));

    // ---------------------------------------------------------------------------------------------
    // 5. VALIDATE
    // ---------------------------------------------------------------------------------------------
    await presenter.part(5, 'Validate', 'Expected changes + no unintended regression + BO reports');

    // STEP 21: Opening the Differences Explorer. This is the heart of the regression story: fields
    // we meant to change should change, and everything else should not.
    await presenter.step('Review the differences', 'Fields we meant to change should change, and nothing else should');
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    await presenter.click(nav(page, 'differences'));
    await presenter.select(page.getByTestId('diff-run-select'), runId);

    const testSelect = page.getByTestId('diff-test-select');
    const chooseTest = async (jobName: string) => {
      const value = await testSelect.locator('option', { hasText: jobName }).getAttribute('value');
      await presenter.select(testSelect, value as string);
      await expect(page.getByTestId('diff-results-table')).toBeVisible();
    };
    const openCount = page.locator('.metric-rose .metric-value');
    const differenceRows = page.locator('[data-testid="diff-results-table"] tbody tr');
    const firstRow = differenceRows.first();

    // Accepts the intended change, leaving only what the change does not explain.
    const acceptImpactedField = async (jobName: string, exceptionsLeft: number) => {
      await chooseTest(jobName);
      await expect(page.getByTestId('diff-column-select').locator('option', { hasText: IMPACTED_FIELD })).toHaveCount(1);
      await presenter.click(page.getByTestId('diff-clear-filters-btn'));
      await presenter.type(page.getByTestId('diff-search-input'), IMPACTED_FIELD);
      await expect(firstRow).toContainText(IMPACTED_FIELD);
      await presenter.click(page.getByTestId('diff-accept-all-btn'));
      await presenter.type(page.getByTestId('decision-modal-note-textarea'), ACCEPT_NOTE);
      await presenter.click(page.getByTestId('decision-modal-confirm-btn'));
      await expect(page.getByTestId('decision-modal-note-textarea')).toBeHidden({ timeout: 30_000 });
      await presenter.click(page.getByTestId('diff-clear-filters-btn'));
      await presenter.select(page.getByTestId('diff-status-select'), 'pending');
      await expect(differenceRows).toHaveCount(exceptionsLeft, { timeout: 30_000 });
    };

    // STEP 22: Progression validation. Searching the CSV differences for fee_amount, the field this
    // change was designed to alter. These are expected, and a reviewer accepts them with a reason.
    // This is a human decision, recorded with a note.
    await presenter.step('Progression: the expected change', 'fee_amount was meant to change, so a reviewer accepts it with a reason');
    await acceptImpactedField(RECON_JOBS.csv, 2);

    // STEP 23: Regression validation. Everything still open is unexpected. Here are two exceptions
    // the change does not explain: a record missing from Test, and an additional record.
    await presenter.step('Regression: what should not have changed', 'A missing record and an additional record: both are exceptions');
    await presenter.show(differenceRows.nth(0));
    await presenter.show(differenceRows.nth(1));

    // STEP 24: The XML outputs get the same review. After accepting fee_amount, one exception
    // remains: an incorrect notional value in a field that should not have changed.
    await presenter.step('XML outputs', 'After the expected change, one incorrect value remains');
    await acceptImpactedField(RECON_JOBS.xml, 1);
    await presenter.show(firstRow);

    // STEP 25: QA Postgres against Production Oracle. Same pattern: the fee change is accepted, and
    // one wrong notional stays open.
    await presenter.step('Postgres vs Oracle', 'After the expected change, one wrong notional remains');
    await acceptImpactedField(RECON_JOBS.trades, 1);
    await presenter.show(firstRow);

    // STEP 26: QA Postgres against the CrushFTP flat file. Nothing here is expected, so nothing is
    // accepted: one position is in the Production file but missing from Postgres.
    await presenter.step('Postgres vs the flat file', 'One position is missing from Postgres');
    await chooseTest(RECON_JOBS.positionsFile);
    await expect(differenceRows).toHaveCount(1);
    await presenter.show(firstRow);

    // STEP 27: BusinessObjects reports. ATOM pulled each impacted report live from QA for this
    // business date and compared it with the Production report. Two of the three reports match.
    // The Sales Summary does not: its amount total differs from Production.
    await presenter.step('BO reports', 'QA vs Production: the Sales Summary amount total differs');
    await chooseTest(RECON_JOBS.boSummary);
    await expect(differenceRows).toHaveCount(1);
    await presenter.show(firstRow);

    // STEP 28: The tally for the whole run. Hundreds of differences were reviewed. The expected ones
    // are accepted, and exactly six exceptions stay open for someone to resolve.
    await presenter.step('The tally', `${OPEN_EXCEPTIONS} exceptions stay open across the whole run`);
    // With no test selected, the metrics cover the whole run.
    await presenter.select(testSelect, '');
    await expect(page.getByTestId('diff-insights-scope')).toHaveText('Showing the whole run');
    await expect(openCount).toHaveText(String(OPEN_EXCEPTIONS), { timeout: 30_000 });
    await presenter.centre(openCount);
    await presenter.show(openCount.locator('..'));

    // Validation: accepting the expected changes must not hide the real exceptions.
    expect(await runStatus(api, runId), 'open exceptions keep the run failed').toBe('FAILED');

    // ---------------------------------------------------------------------------------------------
    // 6. REPORT
    // ---------------------------------------------------------------------------------------------
    await presenter.part(6, 'Report', 'Consolidated execution, reconciliation and pass/fail evidence');

    // STEP 29: Opening Reports for the consolidated evidence: execution, reconciliation and BO
    // results, all from the same run. This is what we would hand to a release manager or an auditor.
    await presenter.step('Consolidated evidence', 'Execution, reconciliation and BO results in one report');
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    await presenter.click(nav(page, 'reports'));
    await presenter.select(page.getByTestId('reports-run-select'), runId);
    await presenter.click(page.getByTestId('reports-load-btn'));
    const report = page.frameLocator('[data-testid="reports-iframe"]');
    await expect(report.locator('h1')).toHaveText('ETL Framework Execution Report', { timeout: 60_000 });
    await presenter.pace('long');
    await report.locator('body').evaluate((body) => {
      body.ownerDocument.defaultView?.scrollTo({ top: 700, behavior: 'smooth' });
    });
    await presenter.pace('long');

    // ---------------------------------------------------------------------------------------------
    // 7. CI/CD
    // ---------------------------------------------------------------------------------------------
    await presenter.part(7, 'CI/CD', 'Run the regression from a GitLab pipeline');

    // A CI run has nobody to type a date, so it uses the variable's configured value.
    const dateUpdated = await api.put(`/api/variables/${process.env.DEMO_RUN_DATE_VARIABLE_ID}`, {
      data: { default_value: BUSINESS_DATE },
    });
    expect(dateUpdated.ok(), 'the CI business date was configured').toBeTruthy();

    // STEP 30: Everything so far can run from CI/CD. ATOM generates the GitLab job for this
    // sequence, ready to paste into the project's pipeline file.
    await presenter.step('Generate the GitLab job', 'ATOM writes the pipeline job for this sequence');
    await presenter.click(nav(page, 'sequences'));
    await presenter.click(page.getByTestId(`sequence-row-${SEQUENCE_NAME}`));
    await presenter.click(page.getByTestId('sequence-ci-btn'));
    const ciModal = page.locator('.gitlab-ci-modal');
    await presenter.show(ciModal.locator('pre').last());
    await presenter.click(ciModal.getByRole('button', { name: 'Close' }));

    // STEP 31: This is a simulated GitLab runner, as the page says, running ATOM's real CI script
    // with a scoped CI token. In a real project this starts on a commit or a schedule. Here we press
    // Run pipeline, and it launches the same 35-job regression.
    await presenter.step('Run the pipeline (simulated GitLab runner)', 'It runs ATOM\'s real CI script with a scoped token');
    await page.goto(GITLAB_SIM_URL);
    await presenter.click(page.getByTestId('run-pipeline'));
    await expect(page.getByTestId('pipeline-status')).toHaveText('running', { timeout: 15_000 });

    // STEP 32: ATOM reports back to the pipeline, and the pipeline's result follows ATOM's result.
    // Because this run finds real exceptions, the pipeline fails, so a regression can't reach
    // release unnoticed. Accepted differences apply to the run they were reviewed in, so a fresh
    // run reports them again.
    await presenter.step('The pipeline follows the result', 'ATOM found exceptions, so the pipeline fails and blocks the release');
    await expect(page.getByTestId('pipeline-status')).toHaveText('failed', { timeout: 15 * 60_000 });
    // The narration says ATOM's result failed the pipeline, so prove that is why it failed.
    const pipeline = await (await api.get(`${GITLAB_SIM_URL}/api/state`)).json();
    const pipelineLog = (pipeline.log as string[]).join('\n');
    expect(pipelineLog, 'the pipeline ran an ATOM regression to the end').toMatch(/Run [0-9a-f-]{36} finished/);
    expect(pipelineLog, 'ATOM reported the data failures').toContain('"verdict": "FAILED"');
    expect(pipeline.exit_code, 'the job failed because ATOM\'s gate failed').toBe(1);
    await presenter.show(page.getByTestId('pipeline-status'));
    await presenter.show(page.getByTestId('pipeline-log'));

    // STEP 33: Back in ATOM, the CI Runs tab lists every pipeline-triggered run with its commit,
    // branch and a link to the pipeline, so there is one place to see them all.
    await presenter.step('CI Runs in ATOM', 'Every pipeline-triggered run, with its commit, branch and pipeline link');
    await page.goto('/');
    await presenter.click(nav(page, 'ci-runs'));
    const ciRow = page.locator('[data-testid^="ci-runs-row-"]').first();
    await presenter.show(ciRow);
    const ciRunId = (await ciRow.getAttribute('data-testid'))!.replace('ci-runs-row-', '');
    const pipelineLink = page.getByTestId(`ci-runs-pipeline-link-${ciRunId}`);
    await expect(pipelineLink).toHaveAttribute('href', /gitlab-sim.*pipelines\/4821/);
    await presenter.show(pipelineLink);

    // STEP 34: Closing. One business date. Twenty-five jobs in series, parallel and hybrid flows,
    // ten comparisons across files, databases and BusinessObjects reports, a restart that continued
    // from the failure, and a pipeline that follows the result. We confirmed what should have
    // changed, caught what should not have, and have the evidence in one place.
    await presenter.step('Summary', 'Progression + regression, with one consolidated result');
    await presenter.slate(
      'Progression + Regression, One Result',
      '25 jobs in series, parallel and hybrid • 10 comparisons across files, databases and BO reports • Restart from failure • CI/CD',
      'Expected changes accepted with a reason. Exceptions remain open, with evidence.',
      7000,
    );
  } finally {
    await api.dispose();
    await page.close();
    await page.video()?.saveAs(path.join(OUTPUT_DIR, 'atom-regression-demo.webm'));
    writeTimeline(
      path.join(OUTPUT_DIR, 'timeline.md'),
      __filename,
      presenter.timeline,
      Date.now() - startedAt,
    );
  }
});
