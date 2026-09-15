# File Watcher UI Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `file_watcher` as a selectable job type in the Launch tab's Job modal — a fields panel (location kind/root/pattern/credentials_ref, content match, poll interval, max tries, time window), request-body building, edit-load hydration, and save-button validity — so a `file_watcher` job can be created/edited through the UI exactly like `bo_job`/`ds_job`/`dbt_artifact`/etc, and then dropped into a sequence step like any other job (no sequence-editor changes needed — confirmed it's job-name-agnostic to job_type).

**Architecture:** Follows the codebase's existing per-job-type pattern used by every other job type in `frontend/features/launch.js` and `frontend/partials/tab-launch.html`: one `jobModal` state block (defaults + edit-hydration), one `if (m.job_type === 'file_watcher')` branch each in `_buildJobRequestBody`/`canSaveJob`, one `<option>` in the job-type `<select>`, and one `x-show`-gated fields `<div>`. The closest structural precedent is the existing `multi_file` source/target location picker (`mf_source_kind`/`mf_source_root`/`mf_source_pattern`/`mf_source_credentials_ref`), simplified to a single location (no source/target pair, no preview-credentials UI — those aren't part of this feature).

**Tech Stack:** Alpine.js (vanilla, no build step for logic), HTML partials, Playwright e2e tests.

**Backend reference (already shipped, on `master`):** `file_watcher` job_type params shape is `{location: {kind: "local"|"s3"|"sftp"|"scp", root, pattern, credentials_ref?}, content_match: {text, is_regex}?, poll_interval_seconds?, max_tries?, window_start?, window_end?}`, validated by `api/schemas.py`/`etl_framework/runner/job_validation.py` (at least one of `max_tries`/`window_end` required; `credentials_ref` required for non-`local` kinds).

**Design spec:** `docs/superpowers/specs/2026-09-14-file-watcher-job-design.md` §6 (Frontend)

**Status: implemented.** Two deviations from this plan's literal text, both in their own commits: `frontend/index.html` (a generated build artifact from `frontend/index.template.html` + partials, via `npm run build:html`) needed regenerating after Task 3 changed the source partial — this plan didn't anticipate that build step, and the gap was caught when Task 4's e2e test found the option missing from the served page. And `frontend/help-content.js` — called for by the design spec §6 but omitted from this plan's task list — got a Launch-tab-reference entry and a task-guide scenario, added after final review flagged the gap. See `git log` on this branch for the fix commits.

---

### Task 1: `jobModal` state — defaults and edit-load hydration

**Files:**
- Modify: `frontend/features/launch.js` (`openNewJobModal()` around line 151-202, `openEditJobModal(job)` around line 285-370)

- [ ] **Step 1: Add default field values to `openNewJobModal()`**

In `frontend/features/launch.js`, find `openNewJobModal()`. Its `this.jobModal = { ... }` object currently ends with the multi-file target-preview-credentials block:

```javascript
        mf_source_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
        mf_target_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
        mfPreviewLoading: false,
        mfPreviewResult: null,
        mfPreviewError: '',
      };
```

Insert new `fw_*` (file_watcher) defaults right after `mfPreviewError: '',` and before the closing `};`:

```javascript
        mf_source_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
        mf_target_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
        mfPreviewLoading: false,
        mfPreviewResult: null,
        mfPreviewError: '',
        fw_location_kind: 'local', fw_location_root: '', fw_location_pattern: '', fw_credentials_ref: '',
        fw_content_match_text: '', fw_content_is_regex: false,
        fw_poll_interval_seconds: 30, fw_max_tries: '', fw_window_start: '', fw_window_end: '',
      };
```

- [ ] **Step 2: Add edit-load hydration to `openEditJobModal(job)`**

In the same file, find `openEditJobModal(job)`. Its `this.jobModal = { ... }` object currently ends with the multi-file target fields and preview-credentials comment/block:

```javascript
        mf_target_kind: job.params?.file_mapping?.target?.kind || 'local',
        mf_target_root: job.params?.file_mapping?.target?.root || '',
        mf_target_pattern: job.params?.file_mapping?.target?.pattern || '',
        mf_target_credentials_ref: job.params?.file_mapping?.target?.credentials_ref || '',
        // Preview-only credentials are never persisted with the job, so
        // there's nothing in `job.params` to hydrate them from -- always
        // start blank, same as newJobModal.
        mf_source_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
        mf_target_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
```

This is immediately followed by a few more closing fields and the object's closing `};` — you'll see them when you open the file (`mfPreviewLoading`/`mfPreviewResult`/`mfPreviewError` or similar, matching whatever pattern is there). Insert the new `fw_*` hydration lines right after the `mf_target_preview_creds:` line and before whatever comes next:

```javascript
        mf_source_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
        mf_target_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
        fw_location_kind: job.params?.location?.kind || 'local',
        fw_location_root: job.params?.location?.root || '',
        fw_location_pattern: job.params?.location?.pattern || '',
        fw_credentials_ref: job.params?.location?.credentials_ref || '',
        fw_content_match_text: job.params?.content_match?.text || '',
        fw_content_is_regex: job.params?.content_match?.is_regex || false,
        fw_poll_interval_seconds: job.params?.poll_interval_seconds ?? 30,
        fw_max_tries: job.params?.max_tries ?? '',
        fw_window_start: job.params?.window_start || '',
        fw_window_end: job.params?.window_end || '',
```

(If the file's actual content after `mf_target_preview_creds` in `openEditJobModal` differs slightly from what's shown above — e.g. different trailing fields before the closing `};` — insert the `fw_*` block right after the `mf_target_preview_creds:` line regardless; exact position relative to any trailing `mfPreviewLoading`-style fields doesn't matter, only that it's inside the same object literal.)

- [ ] **Step 3: Manual sanity check (no test framework for this file's logic)**

There's no JS unit-test harness for `launch.js` in this repo — verification for this task happens indirectly in Task 4's e2e test and Task 4's manual browser check. For now, just confirm the file has no syntax errors: open it and check the edited regions are well-formed JS object literals (matching commas, no stray braces). If a JS linter is configured (check for `.eslintrc*` at repo root), run it; otherwise skip.

- [ ] **Step 4: Commit**

```bash
git add frontend/features/launch.js
git commit -m "feat(file-watcher): add jobModal state for file_watcher job type"
```

---

### Task 2: Request-body building and save-button validity

**Files:**
- Modify: `frontend/features/launch.js` (`_buildJobRequestBody(m)` around line 596-724, `canSaveJob()` around line 771-813)

- [ ] **Step 1: Add the `file_watcher` branch to `_buildJobRequestBody(m)`**

Find this exact block (the `cross_job_assertion` branch, immediately followed by the `keyColumns` computation):

```javascript
      if (m.job_type === 'cross_job_assertion') {
        params.source_job = m.cja_source_job;
        params.source_metric = m.cja_source_metric || 'count';
        if (m.cja_source_col) params.source_column = m.cja_source_col;
        params.target_job = m.cja_target_job;
        params.target_metric = m.cja_target_metric || 'count';
        if (m.cja_target_col) params.target_column = m.cja_target_col;
        params.tolerance = Number(m.cja_tolerance) || 0;
        params.tolerance_type = m.cja_tolerance_type || 'absolute';
      }
      const keyColumns = ['reconciliation', 'bo_report', 'api_reconciliation'].includes(m.job_type)
```

Insert a new `file_watcher` branch between them:

```javascript
      if (m.job_type === 'cross_job_assertion') {
        params.source_job = m.cja_source_job;
        params.source_metric = m.cja_source_metric || 'count';
        if (m.cja_source_col) params.source_column = m.cja_source_col;
        params.target_job = m.cja_target_job;
        params.target_metric = m.cja_target_metric || 'count';
        if (m.cja_target_col) params.target_column = m.cja_target_col;
        params.tolerance = Number(m.cja_tolerance) || 0;
        params.tolerance_type = m.cja_tolerance_type || 'absolute';
      }
      if (m.job_type === 'file_watcher') {
        params.location = {
          kind: m.fw_location_kind || 'local',
          root: m.fw_location_root,
          pattern: m.fw_location_pattern,
        };
        if (m.fw_location_kind !== 'local' && m.fw_credentials_ref) {
          params.location.credentials_ref = m.fw_credentials_ref;
        }
        if (m.fw_content_match_text) {
          params.content_match = { text: m.fw_content_match_text, is_regex: Boolean(m.fw_content_is_regex) };
        }
        if (m.fw_poll_interval_seconds !== '') params.poll_interval_seconds = Number(m.fw_poll_interval_seconds);
        if (m.fw_max_tries !== '') params.max_tries = Number(m.fw_max_tries);
        if (m.fw_window_start) params.window_start = m.fw_window_start;
        if (m.fw_window_end) params.window_end = m.fw_window_end;
      }
      const keyColumns = ['reconciliation', 'bo_report', 'api_reconciliation'].includes(m.job_type)
```

Do NOT add `'file_watcher'` to the `keyColumns`/`excludeColumns`/`query`/`fileCapableJob` allowlists a few lines below (or at line ~598/714) — `file_watcher` uses neither `query` nor `key_columns`, so it must stay excluded from all of those, same as `automic_job`/`bo_job`/`ds_job`/`dbt_artifact`/`api_reconciliation`/`cross_job_assertion` already are.

- [ ] **Step 2: Add the `file_watcher` branch to `canSaveJob()`**

Find this exact block (the last two `if` branches before the fallback `return true;`):

```javascript
      if (m.job_type === 'cross_job_assertion') return Boolean(m.cja_source_job && m.cja_target_job);
      return true;
    },
```

Replace with:

```javascript
      if (m.job_type === 'cross_job_assertion') return Boolean(m.cja_source_job && m.cja_target_job);
      if (m.job_type === 'file_watcher') {
        const hasBound = Boolean(m.fw_max_tries !== '' || m.fw_window_end);
        const hasCreds = m.fw_location_kind === 'local' || Boolean(m.fw_credentials_ref);
        return Boolean(m.fw_location_root && m.fw_location_pattern && hasCreds && hasBound);
      }
      return true;
    },
```

This mirrors the backend's own `file_watcher` validation (`api/schemas.py`'s `elif self.job_type == "file_watcher":` branch): `location.root`/`location.pattern` required, `credentials_ref` required for non-`local` kinds, and at least one of `max_tries`/`window_end` required.

- [ ] **Step 3: Commit**

```bash
git add frontend/features/launch.js
git commit -m "feat(file-watcher): build request body and validate file_watcher job saves"
```

---

### Task 3: Job Type dropdown option and fields panel

**Files:**
- Modify: `frontend/partials/tab-launch.html` (job-type `<select>` around line 342-355; new fields panel inserted around line 752, between the `dbt_artifact` block and the `<!-- Freshness params -->` comment)

- [ ] **Step 1: Add the `file_watcher` option to the Job Type `<select>`**

Find:

```html
            <option value="cross_job_assertion">cross_job_assertion</option>
            <option value="api_reconciliation">api_reconciliation</option>
          </select>
```

Replace with:

```html
            <option value="cross_job_assertion">cross_job_assertion</option>
            <option value="api_reconciliation">api_reconciliation</option>
            <option value="file_watcher">file_watcher</option>
          </select>
```

- [ ] **Step 2: Add the File Watcher fields panel**

Find this exact boundary (end of the `dbt_artifact` block, start of the Freshness comment):

```html
        <div x-show="jobModal.job_type === 'dbt_artifact'" class="grid-2">
          <div>
            <label  class="field-label" for="a11y-launch-manifest-json-path">manifest.json path</label>
            <input x-model="jobModal.dbt_manifest_path" class="field-input" placeholder="target/manifest.json" id="a11y-launch-manifest-json-path" />
          </div>
          <div>
            <label  class="field-label" for="a11y-launch-run-results-json-path">run_results.json path</label>
            <input x-model="jobModal.dbt_run_results_path" class="field-input" placeholder="target/run_results.json" id="a11y-launch-run-results-json-path" />
          </div>
        </div>
        <!-- Freshness params -->
```

Insert a new panel between the closing `</div>` of the `dbt_artifact` block and the `<!-- Freshness params -->` comment:

```html
        <div x-show="jobModal.job_type === 'dbt_artifact'" class="grid-2">
          <div>
            <label  class="field-label" for="a11y-launch-manifest-json-path">manifest.json path</label>
            <input x-model="jobModal.dbt_manifest_path" class="field-input" placeholder="target/manifest.json" id="a11y-launch-manifest-json-path" />
          </div>
          <div>
            <label  class="field-label" for="a11y-launch-run-results-json-path">run_results.json path</label>
            <input x-model="jobModal.dbt_run_results_path" class="field-input" placeholder="target/run_results.json" id="a11y-launch-run-results-json-path" />
          </div>
        </div>
        <!-- File Watcher params -->
        <div x-show="jobModal.job_type === 'file_watcher'" class="space-y-3">
          <div class="grid-2">
            <div>
              <label  class="field-label" for="a11y-launch-fw-location-kind">Location Kind</label>
              <select x-model="jobModal.fw_location_kind" class="field-input field-select" data-testid="job-modal-fw-location-kind-select" id="a11y-launch-fw-location-kind">
                <option value="local">local</option>
                <option value="s3">s3</option>
                <option value="sftp">sftp</option>
                <option value="scp">scp</option>
              </select>
            </div>
            <div>
              <label  class="field-label" for="a11y-launch-fw-root">Folder / Bucket Path</label>
              <input x-model="jobModal.fw_location_root" class="field-input" placeholder="/data/inbound or s3://bucket/prefix"
                     data-testid="job-modal-fw-root-input" id="a11y-launch-fw-root" />
            </div>
            <div>
              <label  class="field-label" for="a11y-launch-fw-pattern">File Name Pattern</label>
              <input x-model="jobModal.fw_location_pattern" class="field-input" placeholder="SALES_*.csv"
                     data-testid="job-modal-fw-pattern-input" id="a11y-launch-fw-pattern" />
            </div>
            <div x-show="jobModal.fw_location_kind !== 'local'">
              <label  class="field-label" for="a11y-launch-fw-credentials-ref">Credentials Ref</label>
              <input x-model="jobModal.fw_credentials_ref" class="field-input" placeholder="credentials_ref (s3/sftp/scp only)"
                     data-testid="job-modal-fw-credentials-ref-input" id="a11y-launch-fw-credentials-ref" />
            </div>
          </div>
          <div class="border-t border-slate-200 pt-3">
            <p class="text-xs font-medium text-slate-500 mb-2">Also require file to contain (optional)</p>
            <div class="grid-2">
              <input x-model="jobModal.fw_content_match_text" class="field-input" placeholder="STATUS=COMPLETE"
                     data-testid="job-modal-fw-content-text-input" aria-label="content match text" />
              <label class="flex items-center gap-1 text-xs">
                <input type="checkbox" x-model="jobModal.fw_content_is_regex" class="rounded" aria-label="content match is regex" /> Treat as regex
              </label>
            </div>
          </div>
          <div class="grid-2">
            <div>
              <label  class="field-label" for="a11y-launch-fw-poll-interval">Poll Interval (seconds)</label>
              <input type="number" x-model="jobModal.fw_poll_interval_seconds" min="1" class="field-input" placeholder="30" id="a11y-launch-fw-poll-interval" />
            </div>
            <div>
              <label  class="field-label" for="a11y-launch-fw-max-tries">Max Tries</label>
              <input type="number" x-model="jobModal.fw_max_tries" min="1" class="field-input" placeholder="10"
                     data-testid="job-modal-fw-max-tries-input" id="a11y-launch-fw-max-tries" />
            </div>
            <div>
              <label  class="field-label" for="a11y-launch-fw-window-start">Window Start (optional)</label>
              <input x-model="jobModal.fw_window_start" class="field-input" placeholder="22:00 or ISO datetime" id="a11y-launch-fw-window-start" />
            </div>
            <div>
              <label  class="field-label" for="a11y-launch-fw-window-end">Window End (required if no Max Tries)</label>
              <input x-model="jobModal.fw_window_end" class="field-input" placeholder="23:30 or ISO datetime"
                     data-testid="job-modal-fw-window-end-input" id="a11y-launch-fw-window-end" />
            </div>
          </div>
          <p class="text-xs text-slate-400">
            At least one of Max Tries or Window End is required — an unbounded watch is not allowed. "scp" targets are treated as SFTP under the hood (same credentials shape).
          </p>
        </div>
        <!-- Freshness params -->
```

- [ ] **Step 3: Commit**

```bash
git add frontend/partials/tab-launch.html
git commit -m "feat(file-watcher): add file_watcher job type option and fields panel to Launch tab"
```

---

### Task 4: E2E round-trip test and manual verification

**Files:**
- Modify: `tests/e2e/32-launch-remaining-job-types.spec.ts` (add one new test; update the file's top comment)

- [ ] **Step 1: Update the file's top comment**

Find:

```typescript
// Closes out Launch-tab editor coverage for every remaining job_type option
// (job-modal-type-select) that had zero e2e coverage before this file plus
// 28-launch-automic-job-type.spec.ts / 30-launch-ds-job-type.spec.ts: bo_job,
// api_reconciliation, dbt_artifact, freshness, profile, schema_snapshot,
// cross_job_assertion. Each test creates the job through the New Job modal, confirms
// canSaveJob()'s type-specific requirement actually enables Save, then re-opens the
// row's Edit modal and confirms openEditJobModal() reads the saved params back into
// the same fields -- proving the full round-trip, not just that the POST succeeded.
```

Replace with:

```typescript
// Closes out Launch-tab editor coverage for every remaining job_type option
// (job-modal-type-select) that had zero e2e coverage before this file plus
// 28-launch-automic-job-type.spec.ts / 30-launch-ds-job-type.spec.ts: bo_job,
// api_reconciliation, dbt_artifact, freshness, profile, schema_snapshot,
// cross_job_assertion, file_watcher. Each test creates the job through the New Job modal,
// confirms canSaveJob()'s type-specific requirement actually enables Save, then re-opens the
// row's Edit modal and confirms openEditJobModal() reads the saved params back into
// the same fields -- proving the full round-trip, not just that the POST succeeded.
```

- [ ] **Step 2: Add the `file_watcher` test**

Find the end of the file:

```typescript
    await authedPage.locator(`[data-testid="job-row-${name}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('#a11y-launch-source-job-name')).toHaveValue('orders_profile');
    await expect(authedPage.locator('#a11y-launch-target-job-name')).toHaveValue('payments_profile');
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });
});
```

Replace with (adding a new test before the closing `});`):

```typescript
    await authedPage.locator(`[data-testid="job-row-${name}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('#a11y-launch-source-job-name')).toHaveValue('orders_profile');
    await expect(authedPage.locator('#a11y-launch-target-job-name')).toHaveValue('payments_profile');
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });

  test('file_watcher: location and max tries round-trip', async ({ authedPage }) => {
    const name = `e2e-file-watcher-${Date.now()}`;
    createdJobNames.push(name);

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('file_watcher');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-fw-root-input"]').fill('/data/inbound');
    await authedPage.locator('[data-testid="job-modal-fw-pattern-input"]').fill('SALES_*.csv');
    await authedPage.locator('[data-testid="job-modal-fw-max-tries-input"]').fill('10');
    // location kind defaults to 'local', so no credentials_ref field is shown/needed;
    // max_tries alone satisfies the "max_tries and/or window_end" bound requirement.

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${name}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('[data-testid="job-modal-fw-root-input"]')).toHaveValue('/data/inbound');
    await expect(authedPage.locator('[data-testid="job-modal-fw-pattern-input"]')).toHaveValue('SALES_*.csv');
    await expect(authedPage.locator('[data-testid="job-modal-fw-max-tries-input"]')).toHaveValue('10');
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });
});
```

- [ ] **Step 3: Run the new e2e test**

Per this session's own memory: `npx playwright` has a version-mismatch issue in this environment — run via `node node_modules/@playwright/test/cli.js test tests/e2e/32-launch-remaining-job-types.spec.ts` (not `npx playwright test`), and if the output looks mangled through any `rtk`-wrapped shell, re-run via the PowerShell tool directly to get raw output. Consult the `run` skill first if you need to start the dev server / know the right base URL — don't guess at how this project's app is launched.

Expected: all tests in the file pass, including the new `file_watcher` one, with 0 regressions to the existing 6 tests in this file.

- [ ] **Step 4: Manual browser verification**

Per this project's standing instruction for UI changes: start the dev server (use the `run` skill to find the right way to launch this project rather than guessing), open the Launch tab, click "New Job", select `file_watcher` from the Job Type dropdown, confirm the new fields panel appears with all fields from Task 3 visible and correctly toggling (credentials_ref field only appears when Location Kind is not `local`), fill in a root/pattern/max_tries, confirm Save becomes enabled, save it, reopen it for edit, confirm the values round-trip. Also spot-check that switching Job Type away from `file_watcher` and back doesn't leak stale values into other type's hidden fields (this codebase has a documented past bug class here — see the comment at `launch.js` line ~306-309 about colliding param keys across job types; `file_watcher`'s field names (`fw_*` prefix) don't collide with any existing job type's field names, so this should be a non-issue, but confirm visually).

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/32-launch-remaining-job-types.spec.ts
git commit -m "test(file-watcher): add e2e round-trip coverage for file_watcher job type"
```

---

### Task 5: Full regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full e2e suite's Launch-tab-related specs**

Run (adjust the exact invocation per what Task 4 Step 3 established works in this environment): the launch-tab spec files (`17-sequences.spec.ts`, `28-launch-automic-job-type.spec.ts`, `30-launch-ds-job-type.spec.ts`, `32-launch-remaining-job-types.spec.ts`, and any other `tests/e2e/*launch*.spec.ts`) to confirm no regressions to other job types' Launch-tab behavior.

Expected: ALL PASS.

- [ ] **Step 2: Confirm scope**

Run: `git diff master --stat` (from the repo root, not this worktree's branch point — compare against whatever base this branch was cut from). Expected: only `frontend/features/launch.js`, `frontend/partials/tab-launch.html`, `tests/e2e/32-launch-remaining-job-types.spec.ts`, and this plan doc itself changed.

If both checks pass, nothing further to commit here — Task 4's commit is the final code commit.
