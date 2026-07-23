# Multi-File Reconciliation — Phase 6: Job Editor UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a QA engineer create and edit a `multi_file` reconciliation job entirely through the web UI — no more hand-written JSON — and preview which files a config would discover/pair before saving the job.

**Architecture:** This is Phase 6 of the roadmap in `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md` §7. Phases 1-5 (merged) built the whole backend: discovery, explicit/automated pairing, aggregation, the lineage manifest, parallel execution with failure isolation, readiness polling, and S3/SFTP sources — all reachable only via a hand-written `params.file_mapping` JSON blob through the API, because the job editor (`frontend/partials/tab-launch.html` + `frontend/features/launch.js`) has never had a `multi_file` option.

**Scope correction from the original roadmap note:** the Phase 1 architecture doc speculated a QA engineer would need an "add/remove row" *repeater* UI (like the existing DQ-rules list) for file-mapping config, modeled on `newDQRule`/`addDQRule`/`removeDQRule`. Now that `FileMappingSpec` (`etl_framework/reconciliation/file_mapping.py`) is fully built, it's clear no repeater is actually needed: a job has exactly **one** source spec and **one** target spec (not an array), and `match_on` is just a list of token names — the same shape as the job editor's existing `key_columns_raw`/`tags_raw`/`depends_on_raw` comma-separated-string fields. This plan builds a flat form (mirroring the existing `bo_live` fields block's structure and `data-testid` conventions), not a repeater.

**Also explicitly out of scope for this phase** (deferred, not silently dropped):
- **Ad-hoc multi-file support in the Compare tab** (`frontend/partials/tab-compare.html`'s `bo`/`recon`/`sql`/`colstats`/`mmdiff` sub-tabs) — the roadmap's "per-pair result view in Compare tab" line. The Compare tab is a wholly separate ad-hoc (no-saved-job) comparison feature with its own backend endpoints; adding a sixth `multi_file` sub-tab there is a similarly-sized project to this one and is better scoped as its own phase. Phase 4 (already merged) already gives per-pair results a home in the **Reports** tab (the HTML report's "File pairs" section, with Playwright coverage), which is where a *saved job's* results belong — this phase's UI work is what's missing is job *creation*, not result *viewing*.
- **S3/SFTP in the preview endpoint** — the new preview endpoint (Task 1) only discovers `kind: "local"` sources. Previewing S3/SFTP would need credential resolution before a job exists to attach `config_snapshot`-sourced credentials to, which is a real design question (where do preview-time credentials come from?) deserving its own decision, not a rushed answer here. The job editor form still lets a user configure `kind: "s3"`/`"sftp"` and save the job — the job runs identically either way — the preview button is just local-only for now.

**Tech Stack:** FastAPI (backend endpoint), Alpine.js (existing frontend reactivity pattern, no build step), Playwright (e2e).

**Spec coverage in this phase:**
1. *Job editor UI* — done: a `multi_file` option in the Input Source select, plus a full config form (source/target kind+root+pattern+credentials_ref+readiness, strategy+match_on+automated_mapping, unmatched_policy).
2. *Automated-mapping preview endpoint* — done: `POST /api/jobs/preview-file-mapping`, local-only, runs real discovery + pairing (both strategies) and returns the same pair/unmatched shape the lineage manifest already uses, without executing any reconciliation or writing anything.
3. *Compare tab per-pair view* — explicitly deferred (see above).

---

### Task 1: Backend preview endpoint

**Files:**
- Modify: `api/routes/jobs.py`
- Test: `tests/unit/test_api.py` (verified: this file already has `POST /api/jobs/validate` route tests, e.g. `test_validate_job_definition_endpoint` around line 629, using a `client(monkeypatch)` fixture with a real in-memory-SQLite-backed `TestClient`)

- [ ] **Step 1: Write the failing test**

APPEND to `tests/unit/test_api.py`, right after `test_validate_job_definition_endpoint_accepts_valid_job`:

```python
def test_preview_file_mapping_local_explicit_returns_pairs_and_unmatched(client, tmp_path, monkeypatch):
    import api.services.file_source as file_source
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_east.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (source_dir / "sales_north.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (target_dir / "financials_east.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")

    resp = client.post("/api/jobs/preview-file-mapping", json={
        "file_mapping": {
            "strategy": "explicit",
            "match_on": ["region"],
            "source": {"kind": "local", "root": str(source_dir), "pattern": "sales_{region}.csv"},
            "target": {"kind": "local", "root": str(target_dir), "pattern": "financials_{region}.csv"},
        }
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["pairs_total"] == 1
    assert body["pairs"][0]["key"] == {"region": "east"}
    assert body["pairs"][0]["source_files"] == ["sales_east.csv"]
    assert body["pairs"][0]["target_files"] == ["financials_east.csv"]
    assert len(body["unmatched_sources"]) == 1
    assert body["unmatched_sources"][0]["files"] == ["sales_north.csv"]
    assert body["unmatched_targets"] == []


def test_preview_file_mapping_rejects_remote_kinds(client):
    resp = client.post("/api/jobs/preview-file-mapping", json={
        "file_mapping": {
            "match_on": ["region"],
            "source": {"kind": "s3", "root": "s3://bucket/prefix", "pattern": "sales_{region}.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
        }
    })
    assert resp.status_code == 400
    assert "local" in resp.json()["detail"].lower()


def test_preview_file_mapping_rejects_missing_file_mapping(client):
    resp = client.post("/api/jobs/preview-file-mapping", json={})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -k preview_file_mapping -v`
Expected: FAIL with a 404 (route doesn't exist yet)

- [ ] **Step 3: Write minimal implementation**

In `api/routes/jobs.py`, add this new route immediately after `validate_job_definition_body` (which currently ends at the line `return {"ok": not any(issue["severity"] == "error" for issue in issues), "issues": issues}`):

```python
@router.post("/preview-file-mapping")
def preview_file_mapping(body: dict):
    from etl_framework.reconciliation.file_mapping import (
        FileMappingSpec,
        discover_local_files,
        pair_files,
        pair_files_automated,
    )
    from api.services.file_source import read_tabular, resolve_allowed_path

    try:
        spec = FileMappingSpec.from_params(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if spec.source.kind != "local" or spec.target.kind != "local":
        raise HTTPException(
            status_code=400,
            detail="Preview only supports 'local' source/target kinds; s3 and sftp jobs can still be saved and run normally.",
        )

    try:
        source_root = resolve_allowed_path(spec.source.root)
        target_root = resolve_allowed_path(spec.target.root)
        source_files = discover_local_files(source_root, spec.source.pattern)
        target_files = discover_local_files(target_root, spec.target.pattern)

        if spec.strategy == "automated":
            source_frames = {f.path: read_tabular(path=f.path, file_name=f.file_name) for f in source_files}
            target_frames = {f.path: read_tabular(path=f.path, file_name=f.file_name) for f in target_files}
            mapping, scores = pair_files_automated(
                source_files, source_frames, target_files, target_frames, spec.automated,
            )
            scores_by_pair = {(s.source.path, s.target.path): s for s in scores}
        else:
            mapping = pair_files(source_files, target_files, spec.match_on)
            scores_by_pair = {}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    def _group(group) -> dict:
        return {"key": dict(zip(mapping.match_on, group.key)), "files": [f.file_name for f in group.files]}

    pairs_out = []
    for pair in mapping.pairs:
        pair_key = dict(zip(mapping.match_on, pair.key)) if mapping.match_on else {
            "source_file": pair.source.files[0].file_name if pair.source.files else None,
            "target_file": pair.target.files[0].file_name if pair.target.files else None,
        }
        score = None
        if pair.source.files and pair.target.files:
            score = scores_by_pair.get((pair.source.files[0].path, pair.target.files[0].path))
        pairs_out.append({
            "key": pair_key,
            "source_files": [f.file_name for f in pair.source.files],
            "target_files": [f.file_name for f in pair.target.files],
            "similarity_score": score.score if score is not None else None,
        })

    return {
        "pairs_total": len(mapping.pairs),
        "pairs": pairs_out,
        "unmatched_sources": [_group(g) for g in mapping.unmatched_sources],
        "unmatched_targets": [_group(g) for g in mapping.unmatched_targets],
    }
```

Check the top of `api/routes/jobs.py` for its existing imports -- `HTTPException` should already be imported (used by other routes in this file); if not, add `from fastapi import HTTPException` to the existing FastAPI import line.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -k preview_file_mapping -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the broader route suite to confirm no regression**

Run: `python -m pytest tests/unit/test_api.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/routes/jobs.py tests/unit/test_api.py
git commit -m "feat(jobs): add local-only file-mapping preview endpoint"
```

---

### Task 2: Job editor — Input Source option and config form

**Files:**
- Modify: `frontend/partials/tab-launch.html`

- [ ] **Step 1: Add the `multi_file` option to the Input Source select**

Find (around line 350-357):

```html
        <div x-show="['reconciliation','freshness','schema_snapshot','profile'].includes(jobModal.job_type)">
          <label class="field-label">Input Source</label>
          <select x-model="jobModal.source_mode" class="field-input field-select" data-testid="job-modal-source-mode-select">
            <option value="sql">SQL Query</option>
            <option value="files">Files</option>
            <option value="bo_live">Live BO Report</option>
          </select>
        </div>
```

Change to (only reconciliation jobs support `multi_file` -- freshness/schema_snapshot/profile don't, matching how `bo_live` is already reconciliation-only elsewhere in this file):

```html
        <div x-show="['reconciliation','freshness','schema_snapshot','profile'].includes(jobModal.job_type)">
          <label class="field-label">Input Source</label>
          <select x-model="jobModal.source_mode" class="field-input field-select" data-testid="job-modal-source-mode-select">
            <option value="sql">SQL Query</option>
            <option value="files">Files</option>
            <option value="bo_live">Live BO Report</option>
            <option value="multi_file" x-show="jobModal.job_type === 'reconciliation'">Multiple Files</option>
          </select>
        </div>
```

- [ ] **Step 2: Add the config form block**

Find the end of the existing `bo_live` fields block (it ends with the closing `</div>` right before the `automic_job` block):

```html
            <div>
              <label class="field-label">Target Label</label>
              <input x-model="jobModal.target_file_label" class="field-input" placeholder="Prod snapshot" />
            </div>
          </div>
        </div>
        <div x-show="jobModal.job_type === 'automic_job'" class="grid-2">
```

Insert a new block between them:

```html
            <div>
              <label class="field-label">Target Label</label>
              <input x-model="jobModal.target_file_label" class="field-input" placeholder="Prod snapshot" />
            </div>
          </div>
        </div>
        <div x-show="jobModal.job_type === 'reconciliation' && jobModal.source_mode === 'multi_file'"
             class="border border-slate-200 rounded-lg p-3 space-y-3">
          <div class="grid-2">
            <div>
              <label class="field-label">Strategy</label>
              <select x-model="jobModal.mf_strategy" class="field-input field-select" data-testid="job-modal-mf-strategy-select">
                <option value="explicit">Explicit (match on tokens)</option>
                <option value="automated">Automated (guess by similarity)</option>
              </select>
            </div>
            <div>
              <label class="field-label">Unmatched Policy</label>
              <select x-model="jobModal.mf_unmatched_policy" class="field-input field-select">
                <option value="fail">Fail job</option>
                <option value="warn">Warn and proceed</option>
                <option value="ignore">Ignore silently</option>
              </select>
            </div>
          </div>
          <div x-show="jobModal.mf_strategy === 'explicit'">
            <label class="field-label">Match On (comma-separated tokens)</label>
            <input x-model="jobModal.mf_match_on_raw" class="field-input" placeholder="region, date"
                   data-testid="job-modal-mf-match-on-input" />
          </div>
          <div x-show="jobModal.mf_strategy === 'automated'" class="grid-2">
            <div>
              <label class="field-label">Similarity Threshold</label>
              <input x-model="jobModal.mf_similarity_threshold" type="number" min="0" max="1" step="0.05"
                     class="field-input" placeholder="0.7" />
            </div>
            <div class="flex items-end gap-3">
              <label class="flex items-center gap-1 text-xs">
                <input type="checkbox" x-model="jobModal.mf_signal_filename" class="rounded" /> filename
              </label>
              <label class="flex items-center gap-1 text-xs">
                <input type="checkbox" x-model="jobModal.mf_signal_columns" class="rounded" /> columns
              </label>
              <label class="flex items-center gap-1 text-xs">
                <input type="checkbox" x-model="jobModal.mf_signal_rowcount" class="rounded" /> row count
              </label>
            </div>
          </div>
          <div class="border-t border-slate-200 pt-3">
            <p class="text-xs font-medium text-slate-500 mb-2">Source</p>
            <div class="grid-2">
              <select x-model="jobModal.mf_source_kind" class="field-input field-select">
                <option value="local">local</option>
                <option value="s3">s3</option>
                <option value="sftp">sftp</option>
              </select>
              <input x-model="jobModal.mf_source_root" class="field-input" placeholder="/spool/exports or s3://bucket/prefix"
                     data-testid="job-modal-mf-source-root-input" />
              <input x-model="jobModal.mf_source_pattern" class="field-input" placeholder="sales_{region}_{date:%Y%m%d}.csv"
                     data-testid="job-modal-mf-source-pattern-input" />
              <input x-model="jobModal.mf_source_credentials_ref" class="field-input" placeholder="credentials_ref (s3/sftp only)"
                     x-show="jobModal.mf_source_kind !== 'local'" />
            </div>
          </div>
          <div class="border-t border-slate-200 pt-3">
            <p class="text-xs font-medium text-slate-500 mb-2">Target</p>
            <div class="grid-2">
              <select x-model="jobModal.mf_target_kind" class="field-input field-select">
                <option value="local">local</option>
                <option value="s3">s3</option>
                <option value="sftp">sftp</option>
              </select>
              <input x-model="jobModal.mf_target_root" class="field-input" placeholder="/exports/finance or s3://bucket/prefix"
                     data-testid="job-modal-mf-target-root-input" />
              <input x-model="jobModal.mf_target_pattern" class="field-input" placeholder="financials_{region}_{date:%Y%m%d}.dat"
                     data-testid="job-modal-mf-target-pattern-input" />
              <input x-model="jobModal.mf_target_credentials_ref" class="field-input" placeholder="credentials_ref (s3/sftp only)"
                     x-show="jobModal.mf_target_kind !== 'local'" />
            </div>
          </div>
          <div class="border-t border-slate-200 pt-3">
            <div class="flex items-center gap-2">
              <button @click="previewFileMapping()"
                      :disabled="jobModal.mfPreviewLoading || jobModal.mf_source_kind !== 'local' || jobModal.mf_target_kind !== 'local'"
                      class="btn-sm btn-secondary text-xs px-3 py-1 disabled:opacity-40"
                      data-testid="job-modal-mf-preview-btn">
                <span x-show="!jobModal.mfPreviewLoading">Preview Mapping</span>
                <span x-show="jobModal.mfPreviewLoading">Loading…</span>
              </button>
              <span x-show="jobModal.mf_source_kind !== 'local' || jobModal.mf_target_kind !== 'local'"
                    class="text-xs text-slate-400">Preview only supports local sources.</span>
            </div>
            <p x-show="jobModal.mfPreviewError" x-text="jobModal.mfPreviewError" class="text-xs text-red-600 mt-2"></p>
            <div x-show="jobModal.mfPreviewResult" class="mt-2 text-xs space-y-1" data-testid="job-modal-mf-preview-result">
              <p x-text="`${jobModal.mfPreviewResult?.pairs_total ?? 0} pair(s) matched`"></p>
              <template x-for="(pair, idx) in (jobModal.mfPreviewResult?.pairs || [])" :key="idx">
                <div class="border border-slate-200 rounded p-2" data-testid="job-modal-mf-preview-pair">
                  <span x-text="Object.entries(pair.key || {}).map(([k,v]) => `${k}=${v}`).join(', ')"></span>
                  — <span x-text="(pair.source_files || []).join(', ')"></span>
                  → <span x-text="(pair.target_files || []).join(', ')"></span>
                </div>
              </template>
              <p x-show="(jobModal.mfPreviewResult?.unmatched_sources || []).length"
                 x-text="'Unmatched sources: ' + jobModal.mfPreviewResult.unmatched_sources.map(g => (g.files||[]).join(', ')).join('; ')"
                 class="text-amber-600"></p>
              <p x-show="(jobModal.mfPreviewResult?.unmatched_targets || []).length"
                 x-text="'Unmatched targets: ' + jobModal.mfPreviewResult.unmatched_targets.map(g => (g.files||[]).join(', ')).join('; ')"
                 class="text-amber-600"></p>
            </div>
          </div>
        </div>
        <div x-show="jobModal.job_type === 'automic_job'" class="grid-2">
```

- [ ] **Step 3: Manually smoke-check the template renders**

Run: `npx playwright test tests/e2e/02-launch-jobs.spec.ts --reporter=list`
Expected: all still PASS (confirms this HTML addition doesn't break the existing job-editor Alpine bindings or Playwright's ability to open the modal)

- [ ] **Step 4: Commit**

```bash
git add frontend/partials/tab-launch.html
git commit -m "feat(launch): add multi_file config form to the job editor"
```

---

### Task 3: Job editor — state, request-body assembly, and hydration

**Files:**
- Modify: `frontend/features/launch.js`

- [ ] **Step 1: Add multi_file fields to `openNewJobModal`'s default state**

Find (in `openNewJobModal`):

```js
        previewConfigId: String(this.launchSettings.config_id || ''),
        previewLoading: false,
        previewResult: null,
        previewError: '',
      };
      this.jobModalEditing = false;
```

Change to:

```js
        previewConfigId: String(this.launchSettings.config_id || ''),
        previewLoading: false,
        previewResult: null,
        previewError: '',
        mf_strategy: 'explicit',
        mf_match_on_raw: '',
        mf_unmatched_policy: 'fail',
        mf_similarity_threshold: 0.7,
        mf_signal_filename: true,
        mf_signal_columns: true,
        mf_signal_rowcount: true,
        mf_source_kind: 'local', mf_source_root: '', mf_source_pattern: '', mf_source_credentials_ref: '',
        mf_target_kind: 'local', mf_target_root: '', mf_target_pattern: '', mf_target_credentials_ref: '',
        mfPreviewLoading: false,
        mfPreviewResult: null,
        mfPreviewError: '',
      };
      this.jobModalEditing = false;
```

- [ ] **Step 2: Add hydration in `openEditJobModal`**

Find (in `openEditJobModal`):

```js
        previewConfigId: String(job.config_id || this.launchSettings.config_id || ''),
        previewLoading: false,
        previewResult: null,
        previewError: '',
      };
      this.jobModalEditing = true;
```

Change to:

```js
        previewConfigId: String(job.config_id || this.launchSettings.config_id || ''),
        previewLoading: false,
        previewResult: null,
        previewError: '',
        mf_strategy: job.params?.file_mapping?.strategy || 'explicit',
        mf_match_on_raw: (job.params?.file_mapping?.match_on || []).join(', '),
        mf_unmatched_policy: job.params?.file_mapping?.unmatched_policy || 'fail',
        mf_similarity_threshold: job.params?.file_mapping?.automated_mapping?.similarity_threshold ?? 0.7,
        mf_signal_filename: (job.params?.file_mapping?.automated_mapping?.signals || ['filename_tokens', 'column_signature', 'row_count_ratio']).includes('filename_tokens'),
        mf_signal_columns: (job.params?.file_mapping?.automated_mapping?.signals || ['filename_tokens', 'column_signature', 'row_count_ratio']).includes('column_signature'),
        mf_signal_rowcount: (job.params?.file_mapping?.automated_mapping?.signals || ['filename_tokens', 'column_signature', 'row_count_ratio']).includes('row_count_ratio'),
        mf_source_kind: job.params?.file_mapping?.source?.kind || 'local',
        mf_source_root: job.params?.file_mapping?.source?.root || '',
        mf_source_pattern: job.params?.file_mapping?.source?.pattern || '',
        mf_source_credentials_ref: job.params?.file_mapping?.source?.credentials_ref || '',
        mf_target_kind: job.params?.file_mapping?.target?.kind || 'local',
        mf_target_root: job.params?.file_mapping?.target?.root || '',
        mf_target_pattern: job.params?.file_mapping?.target?.pattern || '',
        mf_target_credentials_ref: job.params?.file_mapping?.target?.credentials_ref || '',
        mfPreviewLoading: false,
        mfPreviewResult: null,
        mfPreviewError: '',
      };
      this.jobModalEditing = true;
```

- [ ] **Step 3: Add `previewFileMapping()` method**

Find `previewJobQuery()` (it ends with a closing brace before `handleJobTargetFileUpload`). Insert a new method right after it:

```js
    async previewJobQuery() {
      const query = this.jobModal.query?.trim();
      const configId = this.jobModal.previewConfigId;
      if (!query || !configId) {
        this.jobModal.previewError = !configId ? 'Select a config to preview against.' : 'Enter a query first.';
        return;
      }
      this.jobModal.previewLoading = true;
      this.jobModal.previewResult = null;
      this.jobModal.previewError = '';
      try {
        const resp = await fetch(`/api/configs/${configId}/preview-query`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.storedToken}` },
          body: JSON.stringify({ query, limit: 50 }),
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          this.jobModal.previewError = err.detail || `Error ${resp.status}`;
        } else {
          this.jobModal.previewResult = await resp.json();
        }
      } catch (e) {
        this.jobModal.previewError = e.message || 'Network error';
      } finally {
        this.jobModal.previewLoading = false;
      }
    },

    _buildFileMappingConfig(m) {
      const match_on = m.mf_match_on_raw.split(',').map(s => s.trim()).filter(Boolean);
      const config = {
        strategy: m.mf_strategy,
        unmatched_policy: m.mf_unmatched_policy,
        source: { kind: m.mf_source_kind, root: m.mf_source_root, pattern: m.mf_source_pattern },
        target: { kind: m.mf_target_kind, root: m.mf_target_root, pattern: m.mf_target_pattern },
      };
      if (m.mf_strategy === 'explicit') config.match_on = match_on;
      if (m.mf_strategy === 'automated') {
        const signals = [];
        if (m.mf_signal_filename) signals.push('filename_tokens');
        if (m.mf_signal_columns) signals.push('column_signature');
        if (m.mf_signal_rowcount) signals.push('row_count_ratio');
        config.automated_mapping = {
          similarity_threshold: Number(m.mf_similarity_threshold) || 0.7,
          signals,
        };
      }
      if (m.mf_source_kind !== 'local' && m.mf_source_credentials_ref) config.source.credentials_ref = m.mf_source_credentials_ref;
      if (m.mf_target_kind !== 'local' && m.mf_target_credentials_ref) config.target.credentials_ref = m.mf_target_credentials_ref;
      return config;
    },

    async previewFileMapping() {
      const m = this.jobModal;
      m.mfPreviewLoading = true;
      m.mfPreviewResult = null;
      m.mfPreviewError = '';
      try {
        m.mfPreviewResult = await api('POST', '/api/jobs/preview-file-mapping', {
          file_mapping: this._buildFileMappingConfig(m),
        });
      } catch (e) {
        m.mfPreviewError = e.message || 'Preview failed';
      } finally {
        m.mfPreviewLoading = false;
      }
    },

```

(Leave `handleJobTargetFileUpload` and everything after it unchanged -- this just inserts two new methods between `previewJobQuery` and `handleJobTargetFileUpload`.)

- [ ] **Step 4: Wire `_buildJobRequestBody`**

Find (in `_buildJobRequestBody`):

```js
      const usesBoLive = m.job_type === 'reconciliation' && m.source_mode === 'bo_live';
```

Insert a new branch right before it:

```js
      const usesMultiFile = m.job_type === 'reconciliation' && m.source_mode === 'multi_file';
      if (usesMultiFile) {
        params.source_mode = 'multi_file';
        params.file_mapping = this._buildFileMappingConfig(m);
      }
      const usesBoLive = m.job_type === 'reconciliation' && m.source_mode === 'bo_live';
```

Then find the final `return` statement's `query` field:

```js
        query: ['reconciliation', 'freshness', 'profile', 'schema_snapshot'].includes(m.job_type) && !usesFileSource && !usesBoLive ? m.query : '',
```

Change to also exclude `usesMultiFile` (a multi_file job needs no `query`, same as `files`/`bo_live`):

```js
        query: ['reconciliation', 'freshness', 'profile', 'schema_snapshot'].includes(m.job_type) && !usesFileSource && !usesBoLive && !usesMultiFile ? m.query : '',
```

- [ ] **Step 5: Run the job-editor e2e spec to confirm no regression**

Run: `npx playwright test tests/e2e/02-launch-jobs.spec.ts --reporter=list`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/features/launch.js
git commit -m "feat(launch): assemble and hydrate multi_file job params in the editor"
```

---

### Task 4: Playwright coverage for creating a multi_file job through the real UI

**Files:**
- Modify: `tests/e2e/17-multi-file-reconciliation.spec.ts`

- [ ] **Step 1: Write the new test**

APPEND a new test to `tests/e2e/17-multi-file-reconciliation.spec.ts` (inside the existing `test.describe` block, alongside the two tests already there from Phase 4/5):

```ts
  test('creates, previews, and runs a multi_file job entirely through the job editor', async ({ authedPage, adminToken }) => {
    const uiJobName = `e2e-multi-file-ui-${Date.now()}`;

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeVisible();

    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(uiJobName);
    // source_mode lives on the Basic tab (the modal's default tab); the
    // mf_* fields and key_columns live on Settings -- select source_mode
    // first, then switch tabs, matching the existing files-mode job test
    // in 02-launch-jobs.spec.ts.
    await authedPage.locator('[data-testid="job-modal-source-mode-select"]').selectOption('multi_file');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="job-modal-mf-match-on-input"]').fill('region');
    await authedPage.locator('[data-testid="job-modal-mf-source-root-input"]').fill('tests/e2e/fixtures/data/multi_source');
    await authedPage.locator('[data-testid="job-modal-mf-source-pattern-input"]').fill('sales_{region}.csv');
    await authedPage.locator('[data-testid="job-modal-mf-target-root-input"]').fill('tests/e2e/fixtures/data/multi_target');
    await authedPage.locator('[data-testid="job-modal-mf-target-pattern-input"]').fill('financials_{region}.csv');

    // Preview before saving -- proves the preview endpoint and UI wiring both
    // work against the same deterministic fixtures used by the API-driven
    // test above (1 PASSED pair region=east, 1 FAILED pair region=west).
    await authedPage.locator('[data-testid="job-modal-mf-preview-btn"]').click();
    const previewResult = authedPage.locator('[data-testid="job-modal-mf-preview-result"]');
    await expect(previewResult).toContainText('2 pair(s) matched');
    await expect(authedPage.locator('[data-testid="job-modal-mf-preview-pair"]')).toHaveCount(2);

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();
    await expect(authedPage.locator(`[data-testid="job-row-${uiJobName}"]`)).toBeVisible();

    try {
      const ctx = await authedContext(adminToken);
      try {
        const { run_id } = await triggerRun(ctx, [uiJobName]);
        const status = await waitForTerminal(ctx, run_id);
        expect(status.status).toBe('FAILED'); // same deterministic fixtures as the API test: 1 passed pair, 1 failed pair
      } finally {
        await ctx.dispose();
      }
    } finally {
      const ctx = await authedContext(adminToken);
      try {
        await deleteJob(ctx, uiJobName);
      } finally {
        await ctx.dispose();
      }
    }
  });
```

Add the missing imports at the top of the file if not already present:

```ts
import { authedContext, createMultiFileJob, deleteJob, triggerRun, waitForTerminal } from './api-helpers';
```

(This file already imports these from earlier tasks in this same spec file -- check first and only add what's missing.)

All selectors above (`nav-tab-jobs`, `job-new-btn`, `job-modal`, `job-modal-name-input`, `job-modal-source-mode-select`, `job-modal-tab-settings`, `job-modal-key-columns-input`, `job-modal-save-btn`, `job-row-${uiJobName}`) were verified against the real, current `tests/e2e/02-launch-jobs.spec.ts` (its files-mode job test, lines 60-82) before writing this task -- they are not guesses. The tab-switch ordering (select `source_mode` on the Basic tab, then click Settings before touching `mf_*`/key-columns fields) mirrors that same test exactly, including its own comment explaining why the order matters.

- [ ] **Step 2: Run the new test**

Run: `npx playwright test tests/e2e/17-multi-file-reconciliation.spec.ts --reporter=list`
Expected: all PASS (3 tests total in this file: the two from Phase 4/5, plus this new one)

If it fails on a selector, inspect the actual rendered DOM (Playwright's trace viewer or `--debug`) rather than guessing again -- the job editor has many similarly-named fields and getting the exact `data-testid`/`x-model` selector right matters more than matching this plan's placeholder text verbatim.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/17-multi-file-reconciliation.spec.ts
git commit -m "test(e2e): cover creating a multi_file job through the real job editor UI"
```

---

### Task 5: Documentation

**Files:**
- Modify: `docs/multi_file_reconciliation.md`

- [ ] **Step 1: Update the doc**

Find the "Current limitations (Phase 3)" section header (it may already have been renamed if a later phase updated it -- check the current heading text first) and its bullet about no UI. Replace the "no dedicated web UI" bullet with a note that the job editor now supports creating/editing multi_file jobs (local/s3/sftp, both strategies), that a "Preview Mapping" button exists for local sources, and that the Compare tab's ad-hoc flows and S3/SFTP preview remain future work. Retitle the limitations section to reflect this phase (e.g. "Current limitations (Phase 6)") and update the trailing roadmap pointer line to still reference `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md` §7.

- [ ] **Step 2: Commit**

```bash
git add docs/multi_file_reconciliation.md
git commit -m "docs: document the job editor UI and file-mapping preview endpoint"
```

---

## Self-review notes

- **Spec coverage:** Task 1 delivers the preview endpoint; Tasks 2-3 deliver the job editor UI (form + state + request assembly + hydration); Task 4 proves the whole flow works end-to-end in a real browser, including the preview button, using the exact same deterministic fixtures Phase 4/5's e2e test already established (`fixtures/data/multi_source`/`multi_target`, region=east PASSED / region=west FAILED) so the assertions are consistent with existing coverage rather than inventing new fixture data.
- **No repeater built:** explicitly corrected from the Phase 1 architecture doc's speculation, with the reasoning recorded in this plan's header rather than silently deviating from what that earlier doc said.
- **Deferred, not dropped:** Compare-tab ad-hoc multi-file support and S3/SFTP preview are both named explicitly as out of scope, with the reasoning for each.
- **Selector verification pass:** Task 4's e2e test initially used unverified selector guesses (a raw Alpine `x-model` selector for the name input, `nav-tab-launch`, a toast-based save assertion, and no tab-switch step). Before finalizing this plan, `tests/e2e/02-launch-jobs.spec.ts` was actually read and every selector/ordering was corrected to match its real, existing files-mode job test verbatim -- this document's Task 4 now contains verified selectors, not placeholders.
