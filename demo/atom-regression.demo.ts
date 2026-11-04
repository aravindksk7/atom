// ATOM regression demo: narrated, highlighted, recorded.
//
//   Business Date -> 4 ETL Sets -> ~100 ETLs -> Output Files -> Reconciliation
//   -> Impact Validation -> BO Reports -> Consolidated Result
//
// Every "STEP" comment below is written to be read aloud. The script pauses on each
// step long enough to say it. Change the pace with DEMO_PACE (see demo/README.md).
//
// ATOM is the real application running a real run. Oracle, CrushFTP, AWS S3 and SAP are
// simulated, which the opening title card says on screen.
import { APIRequestContext, expect, Locator, Page, request as pwRequest, test } from '@playwright/test';
import https from 'node:https';
import path from 'node:path';
import { Presenter } from './helpers/presenter';
import {
  BUSINESS_DATE, EMAIL_RECIPIENTS, etlNames, FAILING_ETL, IMPACTED_FIELD, JOBS, SEQUENCE_NAME,
} from './seed/seed_atom';

const OUTPUT_DIR = path.join(__dirname, 'output');
const MAILPIT_URL = 'http://mailpit:8025';
const SAPDS_URL = 'https://sapds:8444';
const ETL_COUNT = 100;
const STEP_COUNT = 108; // 100 ETLs, the CrushFTP fetch, 4 reconciliations, 3 BO reports
// Missing and additional CSV records, wrong XML and Oracle notionals, the row missing from
// Postgres and the BO total. The failed ETL and its unmatched file are reported separately.
const OPEN_EXCEPTIONS = 6;
const TERMINAL =/^(PASSED|FAILED|ERROR|CANCELLED|BLOCKED)$/;
const ACCEPT_NOTE = 'Expected: fee_amount is changed by the new fee model (change request CR-2041).';

type Step = { step_id: string; job_name: string; status: string };

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
// assertions after the run has finished read these same values and do catch a real problem.
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

test('ATOM end-to-end regression for one business date', async ({ page }) => {
  const presenter = new Presenter(page);
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

    // STEP 1: Introducing the story. We take one business date through a full, production-like
    // regression: four ETL sets, around a hundred ETLs, reconciliation of their outputs against
    // Production, impact validation and BusinessObjects reports, ending in one consolidated result.
    // Oracle, CrushFTP, AWS S3 and SAP are simulated here so the demo runs anywhere; ATOM is real.
    await presenter.slate(
      'ATOM End-to-End Regression',
      'Business Date → 4 ETL Sets → ~100 ETLs → Output Files → Reconciliation → Impact Validation → BO Reports → Consolidated Result',
      'Demo environment: Oracle, CrushFTP, AWS S3 and SAP are simulated.',
      8000,
    );

    // ---------------------------------------------------------------------------------------------
    // 1. CONFIGURE
    // ---------------------------------------------------------------------------------------------
    await presenter.chapter(1, 'Configure', 'Select the business date and the ETL sets');

    // STEP 2: Opening Sequences. A sequence is ATOM's saved, versioned definition of what runs and
    // in what order. It mirrors the production schedule, so we test the order we actually run.
    await presenter.click(nav(page, 'sequences'));

    // STEP 3: Selecting the Nightly Regression sequence, which holds all four ETL sets.
    await presenter.click(page.getByTestId(`sequence-row-${SEQUENCE_NAME}`));
    const detail = page.getByTestId('sequence-detail');
    await presenter.note('108 steps: 100 ETLs in 4 sets, then a file fetch, reconciliation and BO reports');

    // STEP 4: This badge shows a custom flow. Sets run one after another, and each ETL only starts
    // once the one before it has finished.
    await presenter.show(page.getByTestId('sequence-flow-badge'));

    // STEP 5: The first ETL of Set 1. The "Runs after" column is the dependency ATOM will enforce.
    await presenter.show(detail.locator('tbody tr', { hasText: 'SET1_ETL_001' }).first());

    // STEP 6: The last ETL of Set 1, followed by the first of Set 2. This is the hand-off between
    // sets: Set 2 cannot begin until Set 1 has completed.
    await presenter.show(detail.locator('tbody tr', { hasText: 'SET2_ETL_026' }).first());

    // STEP 7: After the ETLs come the checks. We fetch a Production file from CrushFTP, reconcile
    // outputs and databases against Production, then compare the BusinessObjects reports.
    await presenter.show(detail.locator('tbody tr', { hasText: JOBS.reconCsv }).first());
    await presenter.show(detail.locator('tbody tr', { hasText: JOBS.boSummary }).first());
    await presenter.note();

    // STEP 8: Starting a run of this sequence.
    await presenter.centre(page.getByTestId('sequence-launch-btn'));
    await presenter.click(page.getByTestId('sequence-launch-btn'));

    // STEP 9: Entering the business date. This single value is passed to every ETL, every query,
    // every file path and every BO report prompt in the run.
    await presenter.type(
      page.getByTestId('launch-sequence-variable-overrides-textarea'),
      `run_date=${BUSINESS_DATE}`,
    );

    // STEP 10: Launching. From here on ATOM drives everything, with no manual steps.
    const launched = page.waitForResponse(
      (response) => /\/api\/sequences\/\d+\/launch$/.test(response.url())
        && response.request().method() === 'POST',
    );
    await presenter.click(page.getByTestId('launch-sequence-submit-btn'));
    const launchResponse = await launched;
    expect(launchResponse.ok(), 'the sequence launch was accepted').toBeTruthy();
    const runId = String((await launchResponse.json()).run_id);

    // ---------------------------------------------------------------------------------------------
    // 2. EXECUTE
    // ---------------------------------------------------------------------------------------------
    await presenter.chapter(2, 'Execute', 'Orchestrate ~100 ETLs in production sequence');

    // STEP 11: Opening the Execution Monitor to watch the run live.
    await presenter.click(nav(page, 'monitor'));
    const monitorRun = page.getByTestId(`monitor-run-${runId}`);
    await expect(monitorRun).toBeVisible({ timeout: 60_000 });

    // STEP 12: The counters at the top update as each ETL finishes, and this list shows every step.
    await presenter.show(monitorRun.locator('.grid-stat'));

    // STEP 13: ATOM now works through Set 1 → Set 2 → Set 3 → Set 4. For each ETL it triggers the
    // SAP Data Services job, passes the business date, monitors the status, records the result,
    // and only then starts the next one. We follow the running step down the page.
    const announced = new Set<string>();
    const milestones: Array<[string, string]> = [
      ['SET1_ETL_025', 'Set 1 complete: 25 of 25 ETLs ran in order'],
      ['SET2_ETL_050', 'Set 2 complete: running in the defined order, with the same business date'],
      [FAILING_ETL, `${FAILING_ETL} has failed: ATOM identifies it clearly, and the failure is recorded`],
      ['SET4_ETL_100', 'Set 4 complete: all 100 ETLs have been attempted'],
      [JOBS.fetchFlatFile, 'Production flat file fetched from CrushFTP'],
    ];
    let status = '';
    await presenter.note('Set 1 running');
    await expect.poll(async () => {
      const steps = await getSteps(api, runId);
      const finished = (name: string) => {
        const step = steps.find((candidate) => candidate.step_id === name);
        return Boolean(step) && !['PENDING', 'RUNNING'].includes(step!.status);
      };
      for (const [name, message] of milestones) {
        if (!announced.has(name) && finished(name)) {
          announced.add(name);
          await presenter.note(message);
          if (name === FAILING_ETL) await presenter.show(page.getByTestId(`monitor-step-${name}`));
        }
      }
      const running = steps.find((step) => step.status === 'RUNNING');
      if (running) {
        const chip = page.getByTestId(`monitor-step-${running.step_id}`);
        if (await chip.count()) await presenter.centre(chip).catch(() => undefined);
      }
      status = await runStatus(api, runId);
      return TERMINAL.test(status);
    }, { timeout: 30 * 60_000, intervals: [2000] }).toBe(true);
    await presenter.note();

    // Validation: correct ETLs, correct sequence and correct business date. These come from the
    // SAP DS mock's own record of what it was asked to run, not from ATOM's view of itself.
    const calls = await sapDsCalls();
    expect(calls.map((call) => call.job_name), 'the right ETLs ran in the right order').toEqual(etlNames());
    expect(calls.length).toBe(ETL_COUNT);
    for (const call of calls) {
      expect(call.variables.G_BUSINESS_DATE, `${call.job_name} got the business date`).toBe(BUSINESS_DATE);
    }
    const steps = await getSteps(api, runId);
    expect(steps.length, 'every step ran').toBe(STEP_COUNT);
    const etlSteps = steps.filter((step) => /^SET\d_ETL_/.test(step.step_id));
    expect(etlSteps.filter((step) => step.status === 'PASSED').length, '99 ETLs completed').toBe(99);
    expect(steps.find((step) => step.step_id === FAILING_ETL)?.status).toBe('FAILED');
    expect(status, 'the failure and the exceptions fail the run').toBe('FAILED');
    const finished = await (await api.get(`/api/runs/${runId}`)).json();
    // STEP 15 says these numbers aloud, so a take where they differ must fail.
    expect([finished.total_tests, finished.passed, finished.failed]).toEqual([STEP_COUNT, 102, 6]);

    // STEP 14: The run is finished. Now let's see what it found, starting with the run itself.
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    await presenter.click(nav(page, 'history'));
    await presenter.click(page.getByTestId(`history-run-row-${runId}`));

    // STEP 15: One run, one result. 108 steps executed: 102 passed and 6 failed. Let's see why.
    await presenter.show(page.locator('.grid-stat').first());

    // STEP 16: This is the ETL failure we saw earlier. Every other ETL completed, so the expected
    // ETLs are accounted for and the failure is impossible to miss.
    await presenter.show(page.locator(`tr[data-step-id="${FAILING_ETL}"]`));

    // ---------------------------------------------------------------------------------------------
    // 3. RECONCILE
    // ---------------------------------------------------------------------------------------------
    await presenter.chapter(3, 'Reconcile', 'Compare 100+ XML/CSV outputs with Production files');

    // STEP 17: ATOM fetched the Production flat file from CrushFTP before comparing against it.
    await presenter.show(page.getByTestId('run-result-file-transfer-summary'));

    // STEP 18: The CSV reconciliation. ATOM paired each generated CSV with its Production baseline
    // in AWS S3, matched the records by key and compared every field. These counts are the total
    // across all the file pairs.
    await presenter.show(resultRow(page, JOBS.reconCsv));

    // STEP 19: The same again for the XML outputs, using the same comparison engine.
    await presenter.show(resultRow(page, JOBS.reconXml));

    // STEP 20: Database comparisons too: QA Postgres against Production Oracle.
    await presenter.show(resultRow(page, JOBS.oracleVsPostgres));

    // STEP 21: And QA Postgres against the flat file we fetched from CrushFTP.
    await presenter.show(resultRow(page, JOBS.postgresVsFlatFile));

    // STEP 22: Expanding the CSV result shows individual differences: the record, the field, the
    // test value and the Production value.
    const csvRow = resultRow(page, JOBS.reconCsv);
    await presenter.click(csvRow.getByRole('button', { name: /Expand/ }));
    const firstDifference = page.locator('table.mismatch-diff-table tbody tr').first();
    await presenter.show(firstDifference);
    await presenter.click(csvRow.getByRole('button', { name: /Collapse/ }));

    // ---------------------------------------------------------------------------------------------
    // 4. VALIDATE
    // ---------------------------------------------------------------------------------------------
    await presenter.chapter(
      4,
      'Validate',
      'Expected changes + no unintended regression + BO reports',
    );

    // STEP 23: Opening the Differences Explorer. This is the heart of the regression story: fields
    // we meant to change should change, and everything else should not.
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

    // Accepts the intended change, leaving only what the change does not explain. It searches
    // rather than using the column dropdown, which is empty for multi-file results.
    const acceptImpactedField = async (jobName: string, exceptionsLeft: number) => {
      await chooseTest(jobName);
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

    // STEP 24: Progression validation. Filtering the CSV differences to fee_amount, the field this
    // change was designed to alter. These are expected, and a reviewer accepts them with a reason.
    // This is a human decision, recorded with a note in the audit trail.
    await acceptImpactedField(JOBS.reconCsv, 2);

    // STEP 25: Regression validation. Everything still open is unexpected. Here are two exceptions
    // the change does not explain: a record missing from Test, and an additional record.
    await presenter.note('Regression check: non-impacted data should match Production. These do not.');
    await presenter.show(differenceRows.nth(0));
    await presenter.show(differenceRows.nth(1));
    await presenter.note();

    // STEP 26: The XML outputs get the same review. After accepting fee_amount, one exception
    // remains: an incorrect notional value in a field that should not have changed.
    await acceptImpactedField(JOBS.reconXml, 1);
    await presenter.show(firstRow);

    // STEP 27: QA Postgres against Production Oracle. Same pattern: the fee change is accepted, and
    // one wrong notional stays open.
    await acceptImpactedField(JOBS.oracleVsPostgres, 1);
    await presenter.show(firstRow);

    // STEP 28: QA Postgres against the CrushFTP flat file. Nothing here is expected, so nothing is
    // accepted: one position is in the Production file but missing from Postgres.
    await chooseTest(JOBS.postgresVsFlatFile);
    await expect(differenceRows).toHaveCount(1);
    await presenter.show(firstRow);

    // STEP 29: BusinessObjects reports. ATOM pulled each impacted report live from QA for this
    // business date and compared it with the Production report. Two of the three reports match.
    // The Sales Summary does not: its amount total differs from Production.
    await chooseTest(JOBS.boSummary);
    await expect(differenceRows).toHaveCount(1);
    await presenter.note('BO Sales Summary: amount total differs from the Production report');
    await presenter.show(firstRow);
    await presenter.note();

    // STEP 30: The tally for the whole run. Hundreds of differences were reviewed. The expected ones
    // are accepted, and exactly six data exceptions stay open for someone to resolve.
    await expect(openCount).toHaveText(String(OPEN_EXCEPTIONS), { timeout: 30_000 });
    await presenter.show(openCount.locator('..'));

    // Validation: accepting the expected changes must not hide the real exceptions.
    expect(await runStatus(api, runId), 'open exceptions keep the run failed').toBe('FAILED');

    // ---------------------------------------------------------------------------------------------
    // 5. REPORT
    // ---------------------------------------------------------------------------------------------
    await presenter.chapter(5, 'Report', 'Consolidated evidence and an email summary to recipients');

    // STEP 31: Opening Reports for the consolidated evidence: execution, reconciliation and BO
    // results, all from the same run.
    await presenter.click(nav(page, 'reports'));
    await presenter.select(page.getByTestId('reports-run-select'), runId);
    await presenter.click(page.getByTestId('reports-load-btn'));
    const report = page.frameLocator('[data-testid="reports-iframe"]');
    await expect(report.locator('h1')).toHaveText('ETL Framework Execution Report', { timeout: 60_000 });

    // STEP 32: This report is what we would hand to a release manager or an auditor.
    await presenter.pace('long');
    await report.locator('body').evaluate((body) => {
      body.ownerDocument.defaultView?.scrollTo({ top: 700, behavior: 'smooth' });
    });
    await presenter.pace('long');

    // STEP 33: The recipients have already been told. ATOM emailed a summary to the distribution
    // list as soon as the run finished. Here is the inbox.
    await expect.poll(async () => {
      const response = await api.get(`${MAILPIT_URL}/api/v1/messages`);
      return (await response.json()).total;
    }, { timeout: 60_000 }).toBeGreaterThan(0);
    const mail = (await (await api.get(`${MAILPIT_URL}/api/v1/messages`)).json()).messages[0];
    const delivered = (mail.To as Array<{ Address: string }>).map((to) => to.Address).sort();
    expect(delivered, 'the summary reached the whole list').toEqual([...EMAIL_RECIPIENTS].sort());
    expect(mail.Subject).toContain('FAILED');

    await page.goto(MAILPIT_URL);
    const message = page.getByText(mail.Subject).first();

    // STEP 34: Opening the email: subject, recipients, and the pass/fail summary of the run.
    await presenter.click(message);
    const emailResult = page.getByText('Result: FAILED');
    await expect(emailResult).toBeVisible({ timeout: 30_000 });
    await presenter.show(page.getByText(EMAIL_RECIPIENTS[0]).last());
    await presenter.show(emailResult);
    await presenter.pace('long');
    await presenter.pace('long');

    // STEP 35: Closing. One business date, four ETL sets, a hundred ETLs and over a hundred file
    // comparisons, with BusinessObjects reports alongside. We confirmed what should have changed,
    // caught what should not have, and have the evidence in one place.
    await presenter.slate(
      'Progression + Regression, One Result',
      '100 ETLs orchestrated • 103 file pairs and 2 database comparisons reconciled • 3 BO reports compared',
      'Expected changes accepted with a reason. Exceptions remain open, with evidence.',
      7000,
    );
  } finally {
    await api.dispose();
    await page.close();
    await page.video()?.saveAs(path.join(OUTPUT_DIR, 'atom-regression-demo.webm'));
  }
});
