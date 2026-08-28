# Compare Docs Help Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh README and web UI Help Center content so users understand every Compare tab option, including advanced options and SAP BO all-tabs comparisons.

**Architecture:** This is a content-only update. `README.md` remains the detailed operator/API reference; `frontend/help-content.js` remains the concise, searchable in-app guide rendered by the existing Help Center UI.

**Tech Stack:** Markdown, browser JavaScript, Alpine-rendered Help Center content, Node syntax checking.

## Global Constraints

- Do not change Compare tab runtime behavior.
- Do not redesign the Help Center layout or renderer.
- Do not add markdown rendering inside the Help Center.
- Do not add new Compare API options.
- Keep Help Center content valid JavaScript in `window.ETL_HELP.sections[]` shape.
- Keep edits ASCII unless the existing nearby content requires otherwise.
- Validate `frontend/help-content.js` with `node --check frontend/help-content.js` if Node is available.
- Confirm required terms are searchable in both docs surfaces: `All tabs`, `Advanced Options`, `Column Stats`, `Mismatch Diff`, `Multi-File`, `sample_frac`, and `parallel_columns`.

---

## File Structure

- Modify: `README.md`
  - Responsibility: full project/operator reference, including API examples and detailed Compare option semantics.
  - Change scope: update only the existing Compare Tab section around `## Compare Tab` through the Cross-Run Mismatch Diff subsection; do not reorganize unrelated README sections.

- Modify: `frontend/help-content.js`
  - Responsibility: searchable in-app Help Center content.
  - Change scope: insert a dedicated `compare` section between the existing automation/API/history-oriented sections and later sections, using the existing object schema `{ id, title, intro, steps }`.

- Optional Test: `tests/e2e/11-help.spec.ts`
  - Responsibility: Help Center visibility/search coverage.
  - Change scope: only extend if the existing test already has a simple search assertion pattern; otherwise skip automated E2E and rely on syntax/search validation.

---

### Task 1: README Compare Reference Refresh

**Files:**
- Modify: `README.md:1998`

**Interfaces:**
- Consumes: Existing README anchors under `## Compare Tab`; existing Compare UI labels from `frontend/partials/tab-compare.html`.
- Produces: Updated Markdown sections and anchors for users and Help Center text to reference by name.

- [ ] **Step 1: Inspect the current Compare section before editing**

Run: `git diff -- README.md`
Expected: either no output for `README.md`, or only changes you intentionally made earlier in this task.

- [ ] **Step 2: Replace the Compare Tab overview**

In `README.md`, replace the paragraph immediately under `## Compare Tab` with this text and table:

```markdown
The **Compare** tab provides ad-hoc comparison workflows that do not need to start from the standard job-driven Launch flow. Most compare modes create a durable `TestRun` record visible in History with mismatch details, exports, and baseline support; lighter analysis modes return focused diff views directly in the Compare tab.

| Subtab | Use when | Primary output |
|---|---|---|
| **BO Report** | You need to compare SAP BusinessObjects exports, uploaded report files, server-side report files, API rows, or a previously stored run side-by-side. | Row-level compare run with mismatch details. |
| **Reconciliation** | You need to launch the same saved jobs against two environment/config pairs and compare run outcomes. | Paired runs linked by `pair_id`. |
| **SQL** | You need an ad-hoc row diff between two SQL queries without saving a reconciliation job. | Row-level compare run with mismatch details. |
| **Column Stats** | You need fast distribution/aggregate drift checks for large tables where full row diffs are too expensive. | Per-column metric diffs. |
| **Mismatch Diff** | You need to compare mismatch sets between two historical runs. | New, resolved, and persistent mismatch groups. |
| **Multi-File** | You need a one-off local folder-to-folder file reconciliation without saving a job first. | Persisted multi-file run with per-pair breakdown. |
```

- [ ] **Step 3: Update BO Report Compare source types**

In the BO Report Compare source type table, replace the table with this version:

```markdown
| Source Type | When to use | Required inputs |
|---|---|---|
| `live` | Fetch the report directly from a live SAP BO server | Saved config with BO URL/credentials, Document ID, Report/Page selection or **All tabs (whole document)**, and export format (`csv`/`xlsx`/`xls`) |
| `path` | Read a previously downloaded file from a server-side file path | Absolute file path allowed by the server file policy |
| `upload` | Upload a file directly from your browser | File contents (`.csv`, `.xlsx`, `.xls`, `.json`, `.xml`, `.tsv`, or `.txt`) |
| `api` | Fetch data from a named REST API endpoint defined on a saved config | Saved config and endpoint name from the config's `api_endpoints` map; see [API Endpoints (REST API Data Sources)](#api-endpoints-rest-api-data-sources) |
| `run` | Reuse data from a previous run selected in the Compare UI | Run ID and job/result context selected from History/Compare run pickers |
```

- [ ] **Step 4: Update BO Report UI steps**

Replace the existing BO Report UI steps with this list:

```markdown
1. Open the **Compare** tab and select **BO Report**.
2. For **Source A**: select `Live`, `Path`, `Upload`, `API`, or `Run`, then fill in the fields shown for that mode.
   - For `live`: pick a saved config, select a document, then choose a specific report tab or **All tabs (whole document)**.
   - For `path`: enter the full server-side path to the report file.
   - For `upload`: click **Browse** and select a supported tabular file from your computer.
   - For `api`: select the config and named API endpoint to fetch rows from.
   - For `run`: select a previous run/result from the UI picker when comparing against stored output.
3. Repeat for **Source B**; source types can be mixed, such as live QA BO vs uploaded prod export.
4. Enter **Key Columns** when the row identity is known. If left blank, the engine attempts to infer a key column from common ID-like column names (`id`, `employee_id`, `order_id`, etc.). If no key can be inferred automatically, key columns are required.
5. Enter **Exclude Columns** for values that should not participate in value comparison, such as extract timestamp, report runtime, batch ID, or sheet label columns.
6. Open **Advanced Options** when you need tolerance, normalization, sampling, backend, or storage-volume controls; see [Advanced Compare Options](#advanced-compare-options).
7. Optionally set **Label A** and **Label B** so History and mismatch details show meaningful names.
8. Click **Run BO Compare**. The comparison runs and the result is stored as a run with `run_type = bo_comparison`.
```

- [ ] **Step 5: Add BO all-tabs guidance**

After the BO Report UI steps, add this subsection:

```markdown
**Compare across all BO tabs:**

Choose **All tabs (whole document)** in the live report selector when the business question is document-level parity: every tab/page in the BO document should match between environments or versions. This is useful for multi-tab WebI documents where downstream users consume the whole workbook, not one report tab.

Use a specific report tab instead when you only need one page, when the document export is too large for an ad-hoc check, or when different tabs have unrelated schemas that would make a whole-document row diff noisy.

All-tabs exports can be larger and slower than single-tab exports. They can also include tab-specific columns, blank sections, or differently ordered sheets, so set **Key Columns**, **Exclude Columns**, and string normalization deliberately before treating mismatches as defects.

In the UI, **All tabs (whole document)** is distinct from leaving the report selector blank. Blank means no report was selected and the UI should warn you. **All tabs (whole document)** intentionally requests the document-level export. API callers should only omit or send an empty `report_id` when they intentionally want the whole-document export.
```

- [ ] **Step 6: Update the BO Report options table**

Replace the BO Report options table with this version:

```markdown
| Option | Description |
|---|---|
| `source_a` / `source_b` | Source descriptors for each side; each side chooses one source type and its required fields. |
| `source_type` | One of `live`, `path`, `upload`, `api`, or `run`, depending on the UI/API path in use. |
| `key_columns` | Join columns; auto-inferred from well-known names if omitted. |
| `exclude_columns` | Columns to skip during value comparison. |
| `label_a` / `label_b` | Display names for each source in mismatch details. |
| `doc_id` | Document-level BO ID for live BO sources. |
| `report_id` | Report/page-level BO ID; omit or send empty only when intentionally comparing **All tabs (whole document)**. |
| `api_endpoint_name` | Endpoint name from the config's `api_endpoints` map when a side uses `source_type: "api"`. |
| `advanced` | Optional [Advanced compare options](#advanced-compare-options) block. |
```

- [ ] **Step 7: Add an all-tabs API note below the BO API example**

After the existing BO API example, add:

```markdown
For a live BO whole-document comparison, intentionally request the all-tabs export by leaving `report_id` empty on the live source:

```powershell
$body = @{
  source_a = @{ source_type = "live"; config_id = 1; doc_id = "FI_DOC_001"; report_id = ""; format = "xlsx" }
  source_b = @{ source_type = "live"; config_id = 2; doc_id = "FI_DOC_001"; report_id = ""; format = "xlsx" }
  key_columns = @("account_id", "period")
  exclude_columns = @("refresh_time", "sheet_name")
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/compare/bo-report" -Body $body -ContentType "application/json" -Headers $h
```
```

When inserting this into Markdown, remove the outermost fence from this plan step so the nested PowerShell fence is valid in `README.md`.

- [ ] **Step 8: Expand Advanced Compare Options intro**

Replace the first paragraph under `### Advanced Compare Options` with:

```markdown
Tabular compare modes that run row-level diffs, including BO Report, Recon File, and SQL Direct, accept an optional `advanced` block that controls the comparison engine in detail. In the UI these options appear in an **Advanced Options** accordion on each compare panel. Use them when the default strict row comparison is too noisy, too slow, or stores more mismatch detail than you need.
```

- [ ] **Step 9: Add an advanced options use-case guide**

After the advanced options field table and before `**Mismatch delta fields**`, insert:

```markdown
**Which advanced option should I use?**

| Situation | Option(s) to use | Guidance |
|---|---|---|
| You want the safest default compare. | `comparison_backend: "pandas"` | Use Pandas unless you have installed and validated another backend. It supports all normalization options and works in the default environment. |
| Large row counts make Pandas slow. | `comparison_backend: "polars"` | Use Polars when `polars` is installed and the workload is mostly straightforward typed columns. Re-run a known sample before making it the default for a workflow. |
| Very wide tables or SQL-friendly local analysis are slow. | `comparison_backend: "duckdb"` | Use DuckDB when `duckdb` is installed and the data is wide enough that an in-process SQL engine is faster. Keep Pandas for small ad-hoc checks. |
| Numeric values differ by harmless rounding. | `float_tolerance` | Set a global tolerance such as `1e-6` for general floating-point noise. Keep it as small as the business rule allows. |
| Different numeric columns have different precision rules. | `column_tolerances` | Use per-column overrides for currency, percentages, weights, rates, or tax fields. Example: `price:0.01, tax:0.005`. |
| Timestamps differ by rounding or extract timing. | `datetime_tolerance_seconds` | Set the allowed seconds of drift, such as `1` for sub-second rounding or `60` for minute-bucketed exports. |
| Human-entered text differs only by casing. | `case_insensitive_columns` | Enable only for columns where `Open`, `OPEN`, and `open` mean the same business value. Pandas backend only. |
| Vendor exports add inconsistent spaces. | `whitespace_normalize_columns` | Enable for names, descriptions, or status labels where leading/trailing or repeated internal spaces are not material. Pandas backend only. |
| A large compare produces too many stored detail rows. | `mismatch_row_limit` | Lower this to cap stored detail volume. Aggregate mismatch counts still report the full issue count. |
| You need a quick smoke check before a full compare. | `sample_frac` | Use a fraction from `0.01` to `1.0`. Sampling is not a full reconciliation and should not be used as final evidence for high-risk releases. |
| A table has hundreds of columns. | `parallel_columns`, `parallel_workers` | Enable parallel column comparison for wide tables. Leave it off for small or narrow compares to avoid thread overhead. |
```

- [ ] **Step 10: Update nearby wording for Column Stats and Multi-File consistency**

Read the `### Column Stats Compare`, `### Cross-Run Mismatch Diff`, and `### Multi-File Compare` subsections. Make only terminology-level edits needed so subtab names match the overview exactly: `Column Stats`, `Mismatch Diff`, and `Multi-File`.

- [ ] **Step 11: Search README for required terms**

Run: `rg -n "All tabs|Advanced Options|Column Stats|Mismatch Diff|Multi-File|sample_frac|parallel_columns" README.md`
Expected: every term appears at least once inside or near the Compare Tab section.

- [ ] **Step 12: Review README diff**

Run: `git diff -- README.md`
Expected: diff only changes Compare Tab documentation and contains no unrelated README edits.

- [ ] **Step 13: Commit README change**

Run these commands only if the user requested commits for implementation work. If not requested, skip this step and leave the file uncommitted.

```powershell
git add README.md
git commit -m "docs: refresh compare tab reference"
```

Expected when run: one docs commit containing only `README.md`.

---

### Task 2: Web UI Help Compare Section

**Files:**
- Modify: `frontend/help-content.js:1`
- Optional Test: `tests/e2e/11-help.spec.ts`

**Interfaces:**
- Consumes: Existing Help Center schema from `frontend/help-content.js`, rendered by `frontend/partials/tab-help.html`.
- Produces: New `sections` entry with `id: 'compare'`, `title: 'Compare'`, searchable text for every Compare workflow, and valid JavaScript syntax.

- [ ] **Step 1: Inspect current Help Center content and tests**

Run: `git diff -- frontend/help-content.js tests/e2e/11-help.spec.ts`
Expected: either no output for these files, or only changes you intentionally made earlier in this task.

- [ ] **Step 2: Insert the Compare help section**

In `frontend/help-content.js`, insert the following object into the `sections` array before the existing `adapters` section:

```javascript
    {
      id: 'compare',
      title: 'Compare',
      intro: 'Compare is the ad-hoc analysis area for BO reports, direct SQL queries, historical run output, column statistics, mismatch deltas, and one-off local multi-file checks.',
      steps: [
        {
          title: 'Choose the right Compare subtab',
          text: 'Use BO Report for report files, live SAP BO exports, API rows, or stored run output. Use Reconciliation to launch the same saved jobs against two config/environment pairs. Use SQL for two ad-hoc queries. Use Column Stats for aggregate drift on large tables. Use Mismatch Diff to compare two previous runs. Use Multi-File for a one-off local folder-to-folder reconciliation.',
          where: 'Tabs -> Compare -> subtab row',
        },
        {
          title: 'Compare two BO report sources',
          text: 'Open BO Report. For each side choose Live, Path, Upload, API, or Run. Mix source types when needed, such as live QA BO against an uploaded production export. Set labels so History and mismatch details are readable, then run the compare.',
          where: 'Compare -> BO Report',
          tip: 'Use Key Columns when you know the row identity. Use Exclude Columns for refresh timestamps, batch ids, generated-at fields, sheet labels, or other non-business differences.',
        },
        {
          title: 'Compare all tabs in a BO document',
          text: 'For a Live BO source, select the config and document, then choose All tabs (whole document) in the report selector. Use this when the whole WebI document or workbook must match across environments, not just one report tab.',
          where: 'Compare -> BO Report -> Live -> report selector',
          warn: 'All-tabs exports can be larger and slower than single-tab exports. They may include tab-specific columns or sections, so tune Key Columns, Exclude Columns, and normalization before treating every difference as a defect.',
        },
        {
          title: 'Tune Advanced Options',
          text: 'Open Advanced Options for row-level BO, Recon File, or SQL compares. Keep Backend as pandas unless polars or duckdb is installed and validated. Use Float Tolerance for general numeric rounding, Per-column tolerances for fields like price or tax, Datetime Tolerance for timestamp drift, case-insensitive and whitespace-normalize columns for non-material text differences, Sample Fraction for smoke checks, Mismatch Row Limit to cap stored detail, and Parallel column comparison for very wide tables.',
          where: 'Compare -> BO Report / SQL / Recon File -> Advanced Options',
          warn: 'Sampling is not a full reconciliation. Text normalization can hide real casing or spacing defects if enabled on the wrong columns.',
        },
        {
          title: 'Run a SQL Direct Compare',
          text: 'Open SQL. Pick Source A and Source B configs, optionally choose named connections, enter the two queries, set Key Columns and Exclude Columns, then run the compare. Use this for quick cross-database checks without creating a saved job.',
          where: 'Compare -> SQL',
        },
        {
          title: 'Use Column Stats for large tables',
          text: 'Open Column Stats when a full row-level diff is too expensive. Configure the two sources, set Float Tolerance and Row Count Tolerance, then compute statistics. Review row count, null count, distinct count, min, max, mean, standard deviation, and sum differences by column.',
          where: 'Compare -> Column Stats',
          tip: 'Column Stats is best for drift detection and smoke checks. Use a row-level compare when you need exact mismatching records.',
        },
        {
          title: 'Compare mismatches across runs',
          text: 'Open Mismatch Diff. Enter Run A as the baseline and Run B as the newer run, optionally filter by query name, then run the diff. New mismatches are regressions, resolved mismatches are fixes, and persistent mismatches still need attention.',
          where: 'Compare -> Mismatch Diff',
        },
        {
          title: 'Run a one-off Multi-File compare',
          text: 'Open Multi-File to compare local folders without saving a job. Choose Explicit matching when filenames share tokens such as region and date. Choose Automated matching when names differ and similarity scoring should guess pairs. Preview Mapping first, then Run Comparison when the pairs look right.',
          where: 'Compare -> Multi-File',
          warn: 'The Compare tab Multi-File flow supports local sources only and runs pairs sequentially. Use a saved multi_file reconciliation job for reusable or remote s3/sftp workflows.',
        },
        {
          title: 'Save reusable compare settings',
          text: 'Use Save Template for common BO Report compare settings. Use Save as Job when a compare should become schedulable and reusable through Launch, schedules, or automation. Template coverage for non-BO subtabs is limited, so verify loaded fields before re-running a saved template.',
          where: 'Compare -> template bar / Save as Job',
        },
      ],
    },
```

- [ ] **Step 3: Validate JavaScript syntax**

Run: `node --check frontend/help-content.js`
Expected: no output and exit code 0.

If `node` is unavailable, run: `python - <<'PY'
from pathlib import Path
p = Path('frontend/help-content.js')
text = p.read_text()
for term in ['id: \'compare\'', 'All tabs', 'Advanced Options', 'Column Stats', 'Mismatch Diff', 'Multi-File']:
    assert term in text, term
print('help content term check passed')
PY`
Expected: `help content term check passed`.

- [ ] **Step 4: Search Help Center content for required terms**

Run: `rg -n "All tabs|Advanced Options|Column Stats|Mismatch Diff|Multi-File|sample_frac|parallel_columns|Parallel column" frontend/help-content.js`
Expected: `All tabs`, `Advanced Options`, `Column Stats`, `Mismatch Diff`, `Multi-File`, and `Parallel column` appear in the new Compare section. `sample_frac` and `parallel_columns` may appear only in README because the Help Center uses end-user labels instead of API field names.

- [ ] **Step 5: Decide whether to extend the existing Help E2E test**

Open `tests/e2e/11-help.spec.ts`. If it already tests Help search by entering a term and asserting visible content, add exactly these assertions using the same helper/style already present in that file:

```typescript
await page.getByTestId('help-search-input').fill('All tabs');
await expect(page.getByText('Compare all tabs in a BO document')).toBeVisible();
await page.getByTestId('help-search-input').fill('Advanced Options');
await expect(page.getByText('Tune Advanced Options')).toBeVisible();
await page.getByTestId('help-search-input').fill('Mismatch Diff');
await expect(page.getByText('Compare mismatches across runs')).toBeVisible();
```

If the file does not already have a straightforward Help search test, do not create new Playwright infrastructure for this docs-only change.

- [ ] **Step 6: Run targeted Help E2E only if Step 5 changed the test**

Run: `npx playwright test tests/e2e/11-help.spec.ts`
Expected: Help Center E2E passes.

If Playwright dependencies or browsers are not installed, record the exact failure in the final implementation summary and keep the syntax/search checks as the required validation.

- [ ] **Step 7: Review Help Center diff**

Run: `git diff -- frontend/help-content.js tests/e2e/11-help.spec.ts`
Expected: diff only adds Compare help content and optional matching E2E assertions.

- [ ] **Step 8: Commit Help Center change**

Run these commands only if the user requested commits for implementation work. If not requested, skip this step and leave the files uncommitted.

```powershell
git add frontend/help-content.js tests/e2e/11-help.spec.ts
git commit -m "docs: add compare help guidance"
```

Expected when run: one docs/test commit containing only `frontend/help-content.js` and optionally `tests/e2e/11-help.spec.ts`.

---

### Task 3: Final Validation and Documentation Review

**Files:**
- Modify: none expected
- Review: `README.md`
- Review: `frontend/help-content.js`
- Review: `tests/e2e/11-help.spec.ts` if changed

**Interfaces:**
- Consumes: README changes from Task 1 and Help Center changes from Task 2.
- Produces: Verification evidence for final handoff.

- [ ] **Step 1: Run JavaScript syntax validation**

Run: `node --check frontend/help-content.js`
Expected: no output and exit code 0.

- [ ] **Step 2: Run required term search across both docs surfaces**

Run: `rg -n "All tabs|Advanced Options|Column Stats|Mismatch Diff|Multi-File|sample_frac|parallel_columns" README.md frontend/help-content.js`
Expected: all terms appear at least once across the two files; `sample_frac` and `parallel_columns` must appear in `README.md`, and user-facing equivalents for those controls must appear in `frontend/help-content.js`.

- [ ] **Step 3: Confirm no runtime files changed**

Run: `git diff --name-only`
Expected: changed implementation-relevant files are limited to:

```text
README.md
frontend/help-content.js
tests/e2e/11-help.spec.ts
```

The existing design/plan docs may also appear if they are part of the current planning workflow. No `api/`, `etl_framework/`, or Compare runtime JS files should be changed for this docs-only implementation.

- [ ] **Step 4: Review final diff**

Run: `git diff -- README.md frontend/help-content.js tests/e2e/11-help.spec.ts`
Expected: README has detailed Compare docs, Help Center has a searchable Compare section, and any E2E changes only assert Help content visibility/searchability.

- [ ] **Step 5: Optional final commit**

Run these commands only if the user requested commits for implementation work and Tasks 1-2 were not already committed separately. If not requested, skip this step and leave the files uncommitted.

```powershell
git add README.md frontend/help-content.js tests/e2e/11-help.spec.ts
git commit -m "docs: document compare options in readme and help"
```

Expected when run: one docs commit containing only the intended docs/help/test files.

- [ ] **Step 6: Prepare final implementation summary**

Report these exact evidence items:

```text
Changed files: README.md, frontend/help-content.js[, tests/e2e/11-help.spec.ts if changed]
Validation: node --check frontend/help-content.js -> passed
Validation: rg required Compare terms -> passed
Validation: npx playwright test tests/e2e/11-help.spec.ts -> passed/skipped/failed with reason
Caveat: no runtime Compare behavior changed
```
