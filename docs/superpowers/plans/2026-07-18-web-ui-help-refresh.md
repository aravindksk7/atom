# Web UI Help Center Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh the web UI Help Center so users can discover and use the latest Atom CLI, CI/CD, JUnit export, reporting, scheduler, and API workflows.

**Architecture:** This is a content-only Help Center update using the existing `window.ETL_HELP.sections[]` data model. The existing Alpine renderer in `frontend/partials/tab-help.html` remains unchanged; tests verify the new content is visible and searchable.

**Tech Stack:** JavaScript data module, Alpine-rendered static frontend, Playwright E2E tests, Node syntax validation.

## Global Constraints

- Do not redesign the Help Center layout.
- Do not add markdown/code-block rendering in this pass.
- Keep all content as plain strings supported by the existing `title`, `text`, `where`, `tip`, and `warn` fields.
- Preserve the existing searchable sidebar behavior.
- Keep CLI guidance aligned with `docs/cli.md` and the merged `atom` CLI behavior.

---

### Task 1: Add Help Center Coverage for CLI and Latest Automation Features

**Files:**
- Modify: `frontend/help-content.js`
- Test: `tests/e2e/11-help.spec.ts`

**Interfaces:**
- Consumes: `window.ETL_HELP.sections[]` objects with `{ id, title, intro, steps }`.
- Produces: A new searchable section with `id: 'cli-cicd'`, `title: 'CLI & CI/CD'`, plus refreshed step text in existing sections.

- [ ] **Step 1: Write the failing E2E test**

Append assertions to `tests/e2e/11-help.spec.ts`:

```ts
test('CLI and JUnit help is visible and searchable', async ({ authedPage }) => {
  await authedPage.goto('/');
  await authedPage.locator('[data-testid="nav-tab-help"]').click();

  await expect(authedPage.locator('text=CLI & CI/CD').first()).toBeVisible();

  await authedPage.locator('[data-testid="help-search-input"]').fill('atom run');
  await expect(authedPage.locator('text=Launch and gate with atom run')).toBeVisible();

  await authedPage.locator('[data-testid="help-search-input"]').fill('JUnit');
  await expect(authedPage.locator('text=Collect JUnit and run artifacts')).toBeVisible();

  await authedPage.locator('[data-testid="help-search-input"]').fill('exit code 6');
  await expect(authedPage.locator('text=Read gate exit codes')).toBeVisible();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx playwright test tests/e2e/11-help.spec.ts --grep "CLI and JUnit"`
Expected: FAIL because `CLI & CI/CD` and the new step titles do not exist yet.

- [ ] **Step 3: Add the `CLI & CI/CD` section**

Insert a new section in `frontend/help-content.js` after `job-automation`:

```js
{
  id: 'cli-cicd',
  title: 'CLI & CI/CD',
  intro: 'Use the HTTP-only atom CLI to launch saved Job Selections from any CI system, wait for completion, gate the pipeline with exit codes, and collect JUnit/JSON/HTML artifacts.',
  steps: [
    {
      title: 'Install and configure the CLI',
      text: 'Install the package, then set ATOM_API_URL to the FastAPI base URL and ATOM_API_TOKEN to a bearer token. You can also pass --api-url and --token on each command. Use --output json when a pipeline needs machine-readable output.',
      where: 'Terminal: pip install -e .; atom --api-url http://127.0.0.1:8000 --token <token> selections',
      warn: 'Keep tokens in CI secret variables. Do not hard-code tokens, DB passwords, BO credentials, or Automic credentials in pipeline YAML.',
    },
    {
      title: 'Discover selections and recent runs',
      text: 'Run atom selections to list saved Job Selections and atom runs --limit N to list recent runs. These commands help CI jobs resolve what can be launched and inspect recent status without direct database access.',
      where: 'CLI: atom selections; atom --output json runs --limit 5',
    },
    {
      title: 'Launch and gate with atom run',
      text: 'Run atom run SELECTION --source-env dev --target-env qa to launch a saved Job Selection by id or exact name. The CLI posts to /api/selections/{id}/launch, polls /api/runs/{run_id}/status, prints a summary, and exits with the gate code.',
      where: 'CLI: atom run "Nightly Regression" --source-env dev --target-env qa --poll-interval 10 --timeout 3600',
      tip: '--no-wait launches the run, prints the run id, and exits 0 so another job can poll or collect reports later.',
    },
    {
      title: 'Attach CI context',
      text: 'Pass --ci-commit-sha, --ci-pipeline-url, and --ci-ref so the launched run records the commit, pipeline URL, and branch or tag that produced it.',
      where: 'CLI: atom run "Nightly Regression" --source-env dev --ci-commit-sha "$CI_COMMIT_SHA" --ci-pipeline-url "$CI_PIPELINE_URL" --ci-ref "$CI_COMMIT_REF_NAME"',
    },
    {
      title: 'Collect JUnit and run artifacts',
      text: 'Use --junit-out for CI test reports, --json-out for the run detail payload, and --html-out for the generated HTML report when one exists. Later, use atom report RUN_ID --format junit|json|csv|html --out PATH to fetch artifacts for an existing run.',
      where: 'CLI: atom run "Nightly Regression" --source-env dev --junit-out atom-junit.xml --json-out atom-run.json --html-out atom-report.html',
      tip: 'The API endpoint GET /api/runs/{run_id}/junit returns application/xml and maps each job result to a JUnit testcase.',
    },
    {
      title: 'Read gate exit codes',
      text: 'Exit code 0 means passed, 1 failed, 2 cancelled, 3 run error, 4 selection or run not found, 5 auth or connection failure after retries, and exit code 6 timed out while waiting. On timeout, the CLI prints the run id so you can fetch reports later.',
      where: 'Pipeline shell: atom run "Nightly Regression" --source-env dev --junit-out atom-junit.xml',
    },
    {
      title: 'Publish artifacts in CI',
      text: 'In GitLab, Jenkins, or GitHub Actions, install the package, set API URL and token secrets, run atom run with --junit-out, and publish the JUnit file as a test report artifact. The CLI is HTTP-only, so the CI runner does not need direct DB access.',
      where: 'GitLab example: artifacts:reports:junit -> atom-junit.xml',
    },
  ],
}
```

- [ ] **Step 4: Refresh existing automation/reporting guidance**

In `frontend/help-content.js`:

1. In section `job-automation`, replace the `Gate CI/CD pipelines` step text with:

```js
text: 'In CI/CD, prefer the HTTP-only atom CLI: store ATOM_API_URL and ATOM_API_TOKEN as secrets, run atom run against a saved Job Selection, publish --junit-out as a test artifact, and let exit codes gate promotion. Use python -m etl_framework.runner.cli --gate-run <run_id> only for legacy jobs that can access the same app database/storage.',
where: 'Pipeline stage -> atom run / atom report',
warn: 'Do not hard-code tokens, DB passwords, BO/Automic credentials, or pipeline-only secrets in job definitions or pipeline YAML.',
```

2. In the `history` or `reports` section, add or update a step titled `Export JUnit for CI tools`:

```js
{
  title: 'Export JUnit for CI tools',
  text: 'Every completed run can be rendered as JUnit XML with GET /api/runs/{run_id}/junit or atom report RUN_ID --format junit --out junit.xml. CI viewers group each Atom job as a testcase and show failures/errors with mismatch counts and error messages.',
  where: 'History/Reports -> run id; CLI: atom report <run_id> --format junit --out junit.xml',
  tip: 'Use atom run --junit-out during the pipeline when you want the launch and artifact collection in one command.',
}
```

- [ ] **Step 5: Validate JavaScript syntax**

Run: `node --check frontend/help-content.js`
Expected: no output and exit 0.

- [ ] **Step 6: Run help tests**

Run: `npx playwright test tests/e2e/11-help.spec.ts --grep "help"`
Expected: all Help Center tests pass. If Playwright browsers/server are unavailable, record the exact failure and run `node --check frontend/help-content.js` as the fallback syntax verification.

- [ ] **Step 7: Commit**

```bash
git add frontend/help-content.js tests/e2e/11-help.spec.ts docs/superpowers/specs/2026-07-18-web-ui-help-refresh-design.md docs/superpowers/plans/2026-07-18-web-ui-help-refresh.md
git commit -m "docs(ui): refresh help center for CLI and CI/CD"
```

---

## Self-Review Notes

- **Spec coverage:** CLI/JUnit/CI/CD content is Task 1 Step 3; automation/reporting refresh is Task 1 Step 4; tests are Task 1 Steps 1, 5, and 6.
- **Placeholder scan:** No TBD/TODO placeholders.
- **Type consistency:** Uses the existing Help Center fields only: `id`, `title`, `intro`, `steps`, `text`, `where`, `tip`, `warn`.
