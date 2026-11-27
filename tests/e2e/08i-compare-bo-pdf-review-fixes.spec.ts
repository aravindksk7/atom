// tests/e2e/08i-compare-bo-pdf-review-fixes.spec.ts
//
// UI coverage for the BO PDF compare review fixes:
//   1. "(as printed)" applies per job, not to a whole mixed run (History)
//   2. multi-page tables without a repeated header join up despite page text
//   3. key-column errors on saved jobs still name the missing columns
//   4. request-validation 422s render as text, not "[object Object]"
//   5. table-choice errors fill only the failing side's table picker
//   6. the suggested key is valid in both PDFs
//
// Not UI-observable (covered by unit tests instead): the shared precheck
// deadline, the selection-runs eager load, the cache/page-limit key, and the
// xdist skip reminder.
import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { authedContext, createFileJob, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

const repoRoot = path.resolve(__dirname, '..', '..');
// Path-mode PDFs must sit under SERVER_FILE_ALLOWED_DIRS (playwright.config.ts).
const GEN_DIR = path.join(__dirname, 'fixtures', 'data', '_generated', 'bo_pdf_review');

const PDF_JOB = 'e2e_bo_pdf_review_pdf_job';
const BAD_KEY_JOB = 'e2e_bo_pdf_review_bad_key_job';
const FILE_JOB = 'e2e_bo_pdf_review_file_job';

function pythonExe(): string {
  if (process.env.PYTHON) return process.env.PYTHON;
  const venvPy = path.join(repoRoot, '.venv', 'Scripts', 'python.exe');
  if (existsSync(venvPy)) return venvPy;
  return 'python';
}

/** Build a synthetic BO PDF from build_bo_pdf keyword arguments. */
function buildPdf(name: string, spec: Record<string, unknown>): string {
  const specPath = path.join(GEN_DIR, `${name}.json`);
  const out = path.join(GEN_DIR, `${name}.pdf`);
  writeFileSync(specPath, JSON.stringify(spec), 'utf8');
  execFileSync(pythonExe(), ['-m', 'tests.helpers.pdf_fixtures', 'spec', specPath, out], {
    cwd: repoRoot,
    stdio: 'pipe',
  });
  return out;
}

async function openBO(page: Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-bo"]').click();
}

async function uploadSide(page: Page, side: 'a' | 'b', filePath: string) {
  await page.locator(`[data-testid="compare-bo-source-${side}-mode-upload"]`).click();
  await page.locator(`[data-testid="compare-bo-source-${side}-upload-input"]`).setInputFiles(filePath);
}

async function pathSide(page: Page, side: 'a' | 'b', filePath: string) {
  await page.locator(`[data-testid="compare-bo-source-${side}-mode-path"]`).click();
  await page.locator(`[data-testid="compare-bo-source-${side}-path-input"]`).fill(filePath);
}

async function saveBOJob(page: Page, name: string) {
  await page.locator('[data-testid="compare-bo-save-job-btn"]').click();
  await expect(page.locator('[data-testid="compare-save-job-modal"]')).toBeVisible();
  await page.locator('[data-testid="compare-save-job-name"]').fill(name);
  await page.locator('[data-testid="compare-save-job-confirm"]').click();
  await expect(page.locator('[data-testid="compare-save-job-modal"]')).toBeHidden();
}

async function openRunDetail(page: Page, runId: string) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-history"]').click();
  await page.locator('[data-testid="history-subtab-runs"]').click();
  const row = page.locator(`[data-testid="history-run-row-${runId}"]`);
  await expect(row).toBeVisible();
  return row;
}

const REGION_ROWS = [
  ['North', '10.5'],
  ['South', '20.2'],
  ['East', '30.8'],
];

test.describe('08i compare / BO PDF review fixes', () => {
  let demo: string;
  let paginated: string;
  let oneTable: string;
  let twoTables: string;
  let keyA: string;
  let keyB: string;

  test.beforeAll(() => {
    mkdirSync(GEN_DIR, { recursive: true });
    demo = buildPdf('demo', {
      title: 'Regional Sales',
      tables: [{ header: ['Region', 'Value'], rows: REGION_ROWS }],
    });
    // 60 rows, 25 per page, header printed only on page 1, "Page n of 3" on
    // every page: before the fix each later page read as a new table.
    paginated = buildPdf('paginated', {
      tables: [
        {
          header: ['Id', 'Amount'],
          rows: Array.from({ length: 60 }, (_, i) => [String(i + 1), `${i + 1}.00`]),
        },
      ],
      rows_per_page: 25,
      repeat_header: false,
      page_text: 'Page {n} of {total}',
    });
    oneTable = buildPdf('one_table', {
      tables: [{ header: ['Id', 'V'], rows: [['1', 'a'], ['2', 'b']] }],
    });
    twoTables = buildPdf('two_tables', {
      tables: [
        { header: ['Id', 'V'], rows: [['1', 'a'], ['2', 'b']] },
        { header: ['Code', 'Amt'], rows: [['x', '9']] },
      ],
    });
    // "Line" is unique in A but duplicated in B; "Code" is unique in both.
    keyA = buildPdf('key_a', {
      tables: [{ header: ['Line', 'Code', 'V'], rows: [['1', 'x', '5'], ['2', 'y', '6']] }],
    });
    keyB = buildPdf('key_b', {
      tables: [{ header: ['Line', 'Code', 'V'], rows: [['1', 'x', '5'], ['1', 'y', '6']] }],
    });
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      for (const name of [PDF_JOB, BAD_KEY_JOB, FILE_JOB]) await deleteJob(ctx, name);
    } finally {
      await ctx.dispose();
    }
    rmSync(GEN_DIR, { recursive: true, force: true });
  });

  test('multi-page table without a repeated header joins up despite page numbers', async ({ authedPage }) => {
    await openBO(authedPage);
    await uploadSide(authedPage, 'a', paginated);
    await uploadSide(authedPage, 'b', paginated);

    // Preview sees one 60-row table, not one table per page.
    await authedPage.locator('[data-testid="compare-bo-source-a-pdf-preview-btn"]').click();
    const preview = authedPage.locator('[data-testid="compare-bo-source-a-pdf-preview"]');
    await expect(preview).toBeVisible({ timeout: 30_000 });
    await expect(preview).toContainText('Id');
    await expect(preview).toContainText('Amount');
    await expect(authedPage.locator('[data-testid="compare-bo-source-a-pdf-table-select"]')).toBeHidden();

    await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('Id');
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-bo-pdf-error"]')).toBeHidden();
    await expect(authedPage.locator('[data-testid="compare-bo-result-status"]')).toHaveText(
      'PASSED (as printed)',
      { timeout: 60_000 },
    );
  });

  test('request-validation error renders as text, not [object Object]', async ({ authedPage }) => {
    await openBO(authedPage);
    await uploadSide(authedPage, 'a', demo);
    await uploadSide(authedPage, 'b', demo);
    await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('Region');

    // The table picker only offers valid indices, so send table 0 the way a
    // stale or hand-edited payload would; the real backend answers with a
    // Pydantic 422 whose detail is an array.
    let rewritten = false;
    await authedPage.route('**/api/compare/bo-report', async (route) => {
      const body = route.request().postDataJSON() as Record<string, any>;
      if (body?.source_a?.pdf) {
        body.source_a.pdf.table = 0;
        rewritten = true;
      }
      await route.continue({ postData: JSON.stringify(body) });
    });
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();

    const errBox = authedPage.locator('[data-testid="compare-bo-pdf-error"]');
    await expect(errBox).toBeVisible({ timeout: 30_000 });
    expect(rewritten).toBe(true);
    await expect(errBox).not.toContainText('[object Object]');
    await expect(errBox).toContainText('table');
    await authedPage.unroute('**/api/compare/bo-report');
  });

  test('table-choice error fills only the failing side\'s table picker', async ({ authedPage }) => {
    await openBO(authedPage);
    await uploadSide(authedPage, 'a', oneTable);
    await uploadSide(authedPage, 'b', twoTables);
    await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('Id');
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();

    const errBox = authedPage.locator('[data-testid="compare-bo-pdf-error"]');
    await expect(errBox).toBeVisible({ timeout: 30_000 });
    await expect(errBox).toContainText('several data tables');

    const selectB = authedPage.locator('[data-testid="compare-bo-source-b-pdf-table-select"]');
    await expect(selectB).toBeVisible();
    await expect(selectB.locator('option')).toHaveCount(3); // Auto + 2 tables
    await expect(selectB).toContainText('Code, Amt');
    // Source A has one table; it must not be offered B's tables.
    await expect(authedPage.locator('[data-testid="compare-bo-source-a-pdf-table-select"]')).toBeHidden();

    // Picking table 1 on B lets the compare run.
    await selectB.selectOption('1');
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-bo-result-status"]')).toHaveText(
      'PASSED (as printed)',
      { timeout: 60_000 },
    );
  });

  test('suggested key is valid in both PDFs and runs cleanly', async ({ authedPage }) => {
    await openBO(authedPage);
    await uploadSide(authedPage, 'a', keyA);
    await uploadSide(authedPage, 'b', keyB);
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();

    const errBox = authedPage.locator('[data-testid="compare-bo-pdf-error"]');
    await expect(errBox).toBeVisible({ timeout: 30_000 });
    await expect(errBox).toContainText('set key columns for a PDF compare');
    const useKey = authedPage.locator('[data-testid="compare-bo-pdf-error-use-key-btn"]');
    await expect(useKey).toHaveText('Use Code');
    await useKey.click();
    await expect(authedPage.locator('[data-testid="compare-bo-key-columns-input"]')).toHaveValue('Code');

    // The suggestion must not lead straight into another key error.
    await authedPage.locator('[data-testid="compare-bo-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-bo-result-status"]')).toHaveText(
      /\(as printed\)$/,
      { timeout: 60_000 },
    );
    await expect(errBox).toBeHidden();
  });

  test('mixed run: only the PDF job is labelled "(as printed)" in History', async ({ authedPage, adminToken }) => {
    await openBO(authedPage);
    await pathSide(authedPage, 'a', demo);
    await pathSide(authedPage, 'b', demo);
    await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('Region');
    await saveBOJob(authedPage, PDF_JOB);

    const ctx = await authedContext(adminToken);
    let mixedRunId: string;
    let pdfRunId: string;
    try {
      await createFileJob(ctx, FILE_JOB);
      ({ run_id: mixedRunId } = await triggerRun(ctx, [PDF_JOB, FILE_JOB]));
      await waitForTerminal(ctx, mixedRunId, 90_000);
      ({ run_id: pdfRunId } = await triggerRun(ctx, [PDF_JOB]));
      await waitForTerminal(ctx, pdfRunId, 90_000);
    } finally {
      await ctx.dispose();
    }

    // History list: the mixed run is not "as printed" as a whole...
    const mixedRow = await openRunDetail(authedPage, mixedRunId);
    const mixedStatus = authedPage.locator(`[data-testid="history-run-status-${mixedRunId}"]`);
    await expect(mixedStatus).toHaveText(/^[A-Z]+$/);
    await expect(mixedStatus).not.toContainText('as printed');
    // ...while a PDF-only run is.
    await expect(authedPage.locator(`[data-testid="history-run-status-${pdfRunId}"]`)).toHaveText(
      /\(as printed\)$/,
    );

    // Run Detail: per-row labels.
    await mixedRow.click();
    await expect(authedPage.locator('[data-testid="run-detail-back-btn"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="run-detail-status"]')).not.toContainText('as printed');
    await expect(authedPage.locator(`[data-testid="run-result-status-${PDF_JOB}"]`)).toHaveText(
      /\(as printed\)$/,
    );
    const fileStatus = authedPage.locator(`[data-testid="run-result-status-${FILE_JOB}"]`);
    await expect(fileStatus).toHaveText('FAILED');
  });

  test('saved PDF job with a bad key names the missing columns in History', async ({ authedPage, adminToken }) => {
    await openBO(authedPage);
    await pathSide(authedPage, 'a', demo);
    await pathSide(authedPage, 'b', demo);
    await authedPage.locator('[data-testid="compare-bo-key-columns-input"]').fill('Acct_ID');
    await saveBOJob(authedPage, BAD_KEY_JOB);

    const ctx = await authedContext(adminToken);
    let runId: string;
    try {
      ({ run_id: runId } = await triggerRun(ctx, [BAD_KEY_JOB]));
      await waitForTerminal(ctx, runId, 90_000);
    } finally {
      await ctx.dispose();
    }

    const row = await openRunDetail(authedPage, runId);
    await row.click();
    const err = authedPage.locator(`[data-testid="run-result-error-${BAD_KEY_JOB}"]`);
    await expect(err).toBeVisible();
    await expect(err).toContainText('Selected key_columns must exist in both sources');
    await expect(err).toContainText('missing_in_source_a: Acct_ID');
    await expect(err).toContainText('missing_in_source_b: Acct_ID');
  });
});
