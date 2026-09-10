import { test, expect } from './fixtures';
import { authedContext, createFileJob, deleteJob, waitForTerminal } from './api-helpers';

let token: string;
let jobName: string;
let selectionName: string;
let selectionId: number;
let manualRunId: string;
let ciRunId: string;
let unsafeRunId: string;
const commitSha = '1234567890abcdef1234567890abcdef12345678';
const pipelineUrl = 'https://gitlab.example.test/group/project/-/pipelines/2468';
const gitRef = 'feature/e2e-ci-dashboard';

test.beforeAll(async ({ adminToken }) => {
  token = adminToken;
  const ctx = await authedContext(token);
  jobName = `e2e-ci-dashboard-job-${Date.now()}`;
  selectionName = `e2e-ci-dashboard-selection-${Date.now()}`;
  try {
    await createFileJob(ctx, jobName);
    const selectionResponse = await ctx.post('/api/selections', {
      data: { name: selectionName, description: '', tags: [], job_sequence: [jobName] },
    });
    if (!selectionResponse.ok()) throw new Error(`create selection failed: ${selectionResponse.status()} ${await selectionResponse.text()}`);
    selectionId = (await selectionResponse.json()).id;
    const manualResponse = await ctx.post(`/api/selections/${selectionId}/launch`, { data: { source_env: 'dev', target_env: 'dev' } });
    if (!manualResponse.ok()) throw new Error(`manual launch failed: ${manualResponse.status()} ${await manualResponse.text()}`);
    manualRunId = (await manualResponse.json()).run_id;
    const ciResponse = await ctx.post(`/api/selections/${selectionId}/launch`, {
      data: { source_env: 'dev', target_env: 'dev', ci_context: { commit_sha: commitSha, pipeline_url: pipelineUrl, ref: gitRef } },
    });
    if (!ciResponse.ok()) throw new Error(`CI launch failed: ${ciResponse.status()} ${await ciResponse.text()}`);
    ciRunId = (await ciResponse.json()).run_id;
    const unsafeResponse = await ctx.post(`/api/selections/${selectionId}/launch`, {
      data: { source_env: 'dev', target_env: 'dev', ci_context: { commit_sha: 'unsafe', pipeline_url: 'javascript:alert(1)', ref: gitRef } },
    });
    if (!unsafeResponse.ok()) throw new Error(`unsafe CI launch failed: ${unsafeResponse.status()} ${await unsafeResponse.text()}`);
    unsafeRunId = (await unsafeResponse.json()).run_id;
    await Promise.all([waitForTerminal(ctx, manualRunId), waitForTerminal(ctx, ciRunId), waitForTerminal(ctx, unsafeRunId)]);
  } finally { await ctx.dispose(); }
});

test.afterAll(async () => {
  const ctx = await authedContext(token);
  try {
    for (const runId of [manualRunId, ciRunId, unsafeRunId]) {
      if (!runId) continue;
      const response = await ctx.delete(`/api/runs/${runId}`);
      if (!response.ok() && response.status() !== 404) {
        throw new Error(`run cleanup failed for ${runId}: ${response.status()} ${await response.text()}`);
      }
    }
    if (selectionId) {
      const response = await ctx.delete(`/api/selections/${selectionId}`);
      if (!response.ok() && response.status() !== 404) {
        throw new Error(`selection cleanup failed: ${response.status()} ${await response.text()}`);
      }
    }
    if (jobName) await deleteJob(ctx, jobName);
  } finally { await ctx.dispose(); }
});

test('shows only CI runs, keeps summary aligned, and navigates to target and history', async ({ authedPage }) => {
  const pageErrors: string[] = [];
  const alpineErrors: string[] = [];
  authedPage.on('pageerror', (error) => pageErrors.push(error.message));
  authedPage.on('console', (message) => {
    if (/Alpine Expression Error/.test(message.text())) alpineErrors.push(message.text());
  });
  await authedPage.goto('/#ci-runs');
  const ciRow = authedPage.locator(`[data-testid="ci-runs-row-${ciRunId}"]`);
  await expect(authedPage.locator('[data-testid="ci-runs-tab"]')).toBeVisible();
  await expect(ciRow).toBeVisible();
  await expect(authedPage.locator(`[data-testid="ci-runs-row-${manualRunId}"]`)).toHaveCount(0);
  await expect(ciRow).toContainText(`Selection: ${selectionName}`);
  await expect(ciRow).toContainText(commitSha.slice(0, 8));
  await expect(ciRow).toContainText(gitRef);
  await expect(authedPage.locator(`[data-testid="ci-runs-duration-${ciRunId}"]`)).toHaveText(/^\d+(?:m \d+)?s$/);
  const unsafeRow = authedPage.locator(`[data-testid="ci-runs-row-${unsafeRunId}"]`);
  await expect(unsafeRow).toBeVisible();
  await expect(unsafeRow.locator('td').nth(3).locator('span')).toHaveText('—');
  await expect(unsafeRow.locator('td').nth(3).locator('a')).toBeHidden();
  const pipelineLink = authedPage.locator(`[data-testid="ci-runs-pipeline-link-${ciRunId}"]`);
  await expect(pipelineLink).toHaveAttribute('href', pipelineUrl);
  await expect(pipelineLink).toHaveAttribute('target', '_blank');
  await expect(pipelineLink).toHaveAttribute('rel', 'noopener noreferrer');
  expect(pageErrors).toEqual([]);
  expect(alpineErrors).toEqual([]);

  await authedPage.getByLabel('CI run target type').selectOption('selection');
  await authedPage.getByLabel('CI run status').selectOption('FAILED');
  await expect(ciRow).toBeVisible();
  const total = Number(await authedPage.getByText('Total CI Runs').locator('..').locator('.metric-value').textContent());
  const selections = Number(await authedPage.getByText('Selections', { exact: true }).locator('..').locator('.metric-value').textContent());
  const renderedRows = await authedPage.locator('[data-testid^="ci-runs-row-"]').count();
  expect(total).toBeGreaterThanOrEqual(renderedRows);
  expect(selections).toBeGreaterThanOrEqual(renderedRows);
  await expect(authedPage.getByText('Pass Rate').locator('..').locator('.metric-value')).toHaveText('0%');

  await authedPage.locator(`[data-testid="ci-runs-target-${ciRunId}"]`).click();
  await expect(authedPage.locator('[data-testid="nav-tab-jobs"]')).toHaveClass(/active/);
  await expect(authedPage.locator('[data-testid="job-selections-panel"]')).toBeVisible();
  await expect(authedPage.locator('[data-testid="job-selection-search-input"]')).toHaveValue(selectionName);
  await expect(authedPage.locator(`[data-testid="job-selection-${selectionId}"]`)).toBeVisible();

  await authedPage.goBack();
  await expect(ciRow).toBeVisible();
  await authedPage.locator(`[data-testid="ci-runs-details-${ciRunId}"]`).click();
  await expect(authedPage.locator('[data-testid="run-detail-back-btn"]')).toBeVisible();
  await expect(authedPage.evaluate(() => window.Alpine.$data(document.body).selectedRun.run_id)).resolves.toBe(ciRunId);
});
