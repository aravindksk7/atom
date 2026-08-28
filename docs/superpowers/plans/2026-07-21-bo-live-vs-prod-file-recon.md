# BO Live-QA vs Prod-File Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a saved/scheduled `reconciliation` job pull its source live from SAP BO (QA) on every run and diff it against a fixed, already-uploaded prod file, via a new `source_mode = "bo_live"`.

**Architecture:** Add a third `source_mode` value (`bo_live`) alongside the existing `sql`/`files` modes on `reconciliation` jobs. Source dataframe comes from a live `BORestClient` pull (reusing the exact code path the existing `bo_report` job type uses). Target dataframe comes from the existing generic job-file machinery (`_load_job_file_frame`, path or base64-upload). Both are diffed through the existing `FrameEngine` + `_run_reconciliation_job` path that `files` mode already uses — no new diff engine, no new storage system.

**Tech Stack:** FastAPI + Pydantic (`api/schemas.py`), `RunExecutor` (`api/services/run_executor.py`), Alpine.js job editor (`frontend/features/launch.js`, `frontend/partials/tab-launch.html`), Playwright e2e (`tests/e2e/02-launch-jobs.spec.ts`).

---

### Task 1: Backend validation — `JobDefinition` accepts `source_mode: "bo_live"`

**Files:**
- Modify: `api/schemas.py:455-472`
- Test: `tests/unit/test_bo_live_reconciliation.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_bo_live_reconciliation.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import JobDefinition


def test_bo_live_reconciliation_requires_report_id() -> None:
    with pytest.raises(ValidationError, match="report_id"):
        JobDefinition(
            name="qa_vs_prod",
            job_type="reconciliation",
            query="",
            params={
                "source_mode": "bo_live",
                "bo_report_id": "1",
                "target_file_path": "prod_snapshot.xlsx",
            },
        )


def test_bo_live_reconciliation_requires_bo_report_id() -> None:
    with pytest.raises(ValidationError, match="bo_report_id"):
        JobDefinition(
            name="qa_vs_prod",
            job_type="reconciliation",
            query="",
            params={
                "source_mode": "bo_live",
                "report_id": "101",
                "target_file_path": "prod_snapshot.xlsx",
            },
        )


def test_bo_live_reconciliation_requires_target_file() -> None:
    with pytest.raises(ValidationError, match="target file"):
        JobDefinition(
            name="qa_vs_prod",
            job_type="reconciliation",
            query="",
            params={
                "source_mode": "bo_live",
                "report_id": "101",
                "bo_report_id": "1",
            },
        )


def test_bo_live_reconciliation_accepts_valid_config() -> None:
    job = JobDefinition(
        name="qa_vs_prod",
        job_type="reconciliation",
        query="",
        params={
            "source_mode": "bo_live",
            "report_id": "101",
            "bo_report_id": "1",
            "format": "csv",
            "target_file_path": "prod_snapshot.csv",
        },
    )
    assert job.params["source_mode"] == "bo_live"


def test_bo_live_reconciliation_accepts_uploaded_target_file() -> None:
    job = JobDefinition(
        name="qa_vs_prod",
        job_type="reconciliation",
        query="",
        params={
            "source_mode": "bo_live",
            "report_id": "101",
            "bo_report_id": "1",
            "target_file_content_b64": "aWQsdmFsdWUKMSxhbHBoYQo=",
            "target_file_name": "prod_snapshot.csv",
        },
    )
    assert job.params["target_file_name"] == "prod_snapshot.csv"


def test_bo_live_reconciliation_upload_without_name_rejected() -> None:
    with pytest.raises(ValidationError, match="file name"):
        JobDefinition(
            name="qa_vs_prod",
            job_type="reconciliation",
            query="",
            params={
                "source_mode": "bo_live",
                "report_id": "101",
                "bo_report_id": "1",
                "target_file_content_b64": "aWQsdmFsdWUKMSxhbHBoYQo=",
            },
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_bo_live_reconciliation.py -v`
Expected: all 6 tests FAIL (currently `bo_live` falls into the `sql` branch, so these raise `"reconciliation jobs require a query"` instead of the BO-specific messages, or don't raise at all).

- [ ] **Step 3: Implement the validator branch**

In `api/schemas.py`, replace the `elif self.job_type == "reconciliation":` block (currently lines 455-472):

```python
        elif self.job_type == "reconciliation":
            source_mode = self.params.get("source_mode")
            if source_mode == "bo_live":
                if not self.params.get("report_id"):
                    raise ValueError("bo_live reconciliation jobs require 'report_id' in params")
                if not self.params.get("bo_report_id"):
                    raise ValueError("bo_live reconciliation jobs require 'bo_report_id' in params")
                _validate_job_file_source(self.params, "target")
                if not _has_job_file_source(self.params, "target"):
                    raise ValueError("bo_live reconciliation jobs require a target file")
                # key_columns is optional: RunExecutor infers a shared ID column,
                # or falls back to positional row matching.
            elif (
                source_mode == "files"
                or _has_job_file_source(self.params, "source")
                or _has_job_file_source(self.params, "target")
            ):
                _validate_job_file_source(self.params, "source")
                _validate_job_file_source(self.params, "target")
                if not _has_job_file_source(self.params, "source") or not _has_job_file_source(self.params, "target"):
                    raise ValueError("file-backed reconciliation jobs require source and target files")
                # key_columns is optional for file-backed jobs: RunExecutor infers a
                # shared ID column, or falls back to positional row matching.
            else:
                if not self.query.strip():
                    raise ValueError("reconciliation jobs require a query")
                if not self.key_columns:
                    raise ValueError("reconciliation jobs require key_columns")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_bo_live_reconciliation.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Run the existing file-backed job tests to confirm no regression**

Run: `python -m pytest tests/unit/test_file_backed_jobs.py -v`
Expected: PASS (unchanged behavior for `files` mode and `sql` mode)

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py tests/unit/test_bo_live_reconciliation.py
git commit -m "feat(jobs): validate reconciliation jobs with source_mode=bo_live"
```

---

### Task 2: `RunExecutor` — live BO pull vs file diff

**Files:**
- Modify: `api/services/run_executor.py:456-459` (dispatch)
- Modify: `api/services/run_executor.py` — add `_build_case_bo_live_recon` after `_build_case_file_reconciliation` (currently ends at line 520)
- Test: `tests/unit/test_bo_live_reconciliation.py` (extend from Task 1)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_bo_live_reconciliation.py`:

```python
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import JobRepository, RunRepository
from etl_framework.runner.state import TestStatus


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


_BO_SNAPSHOT = {
    "bo_credentials": {
        "name": "bo",
        "db_host": "bo-host",
        "db_password": "bo-secret",
        "bo_url": "http://bo-server",
        "bo_user": "admin",
    },
}


def test_bo_live_recon_diffs_live_pull_against_target_file(tmp_path, monkeypatch):
    target = tmp_path / "prod_snapshot.csv"
    target.write_text("id,value\n1,alpha\n2,beta\n", encoding="utf-8")

    from api.services import file_source
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    db = _session()
    RunRepository(db).create_run("r-bo-live", "qa", "prod", {})
    JobRepository(db).create({
        "name": "qa_vs_prod",
        "description": "",
        "tags": [],
        "job_type": "reconciliation",
        "query": "",
        "key_columns": ["id"],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {
            "source_mode": "bo_live",
            "report_id": "101",
            "bo_report_id": "1",
            "format": "csv",
            "target_file_path": str(target),
        },
        "enabled": True,
    })
    executor = RunExecutor(
        db=db,
        run_id="r-bo-live",
        source_env="qa",
        target_env="prod",
        job_sequence=["qa_vs_prod"],
        run_settings=RunSettings(use_live_connections=True, metrics_enabled=False),
        config_snapshot=_BO_SNAPSHOT,
    )

    csv_bytes = b"id,value\n1,alpha\n2,gamma\n"
    with patch("api.services.run_executor.BORestClient") as MockBO:
        inst = MockBO.return_value
        inst.download_report.return_value = csv_bytes
        executor.execute()

    run = RunRepository(db).get_run("r-bo-live")
    result = run.results[0]
    assert result.source_row_count == 2
    assert result.target_row_count == 2
    assert result.value_mismatch_count == 1
    assert result.target_file_name == "prod_snapshot.csv"
    assert result.source_file_name is None
    assert result.status == TestStatus.FAILED.value


def test_bo_live_recon_raises_without_target_file():
    from api.services.run_executor import RunExecutor
    from api.schemas import JobDefinition

    job = JobDefinition(
        name="qa_vs_prod",
        job_type="reconciliation",
        query="",
        key_columns=["id"],
        params={
            "source_mode": "files",  # bypass model validator; exercise executor guard directly
            "source_file_path": "x.csv",
            "target_file_path": "y.csv",
        },
    )
    job = job.model_copy(update={"params": {**job.params, "source_mode": "bo_live"}})
    executor = RunExecutor(
        db=None,
        run_id="test-run",
        source_env="qa",
        target_env="prod",
        job_sequence=[],
        run_settings=RunSettings(use_live_connections=True, metrics_enabled=False),
        config_snapshot=_BO_SNAPSHOT,
    )
    executor._resolve_segment_columns = lambda _job: []

    with pytest.raises(ValueError, match="target file"):
        executor._build_case_bo_live_recon(job)()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_bo_live_reconciliation.py -v -k bo_live_recon`
Expected: FAIL — `AttributeError: 'RunExecutor' object has no attribute '_build_case_bo_live_recon'` (and the first test's job never actually pulls/diffs, since dispatch falls through to the default SQL-engine path).

- [ ] **Step 3: Wire the dispatch**

In `api/services/run_executor.py`, in `_build_case` (around line 456-459), insert a new branch before the existing file-reconciliation dispatch:

```python
        if job.job_type == "api_reconciliation" and self._settings.use_live_connections:
            return self._build_case_api_reconciliation(job)
        if (
            job.job_type == "reconciliation"
            and job.params.get("source_mode") == "bo_live"
            and self._settings.use_live_connections
        ):
            return self._build_case_bo_live_recon(job)
        if job.job_type == "reconciliation" and self._uses_file_sources(job):
            return self._build_case_file_reconciliation(job)
```

- [ ] **Step 4: Implement `_build_case_bo_live_recon`**

In `api/services/run_executor.py`, add this method immediately after `_build_case_file_reconciliation` (which currently ends at line 520):

```python
    def _build_case_bo_live_recon(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            if not self._has_file_source(job, "target"):
                raise ValueError("bo_live reconciliation jobs require a target file")

            from api.services.file_source import read_tabular

            creds = self._config_snapshot.get("bo_credentials", {})
            env = EnvironmentConfig(name=creds.get("name", "bo"), **{
                k: v for k, v in creds.items() if k != "name"
            })
            client = BORestClient(env)
            client.authenticate()
            doc_id = job.params.get("report_id", "")
            report_id = job.params.get("bo_report_id", "")
            fmt = job.params.get("format", "xlsx")
            try:
                data = client.download_report(doc_id, report_id, fmt)
            finally:
                client.logout()
            source_df = read_tabular(
                content_b64=base64.b64encode(data).decode("ascii"),
                file_name=f"bo_report_{doc_id}_{report_id}.{fmt}",
            )
            target_df = self._load_job_file_frame(job, "target")

            source_df, target_df, resolved_keys = resolve_key_columns(
                source_df,
                target_df,
                job.key_columns or self._settings.key_columns,
                job.exclude_columns or [],
            )
            run_job = job.model_copy(update={"key_columns": resolved_keys})
            source_label = job.params.get("source_file_label") or job.params.get("label_a") or self._source_env
            target_label = job.params.get("target_file_label") or job.params.get("label_b") or self._target_env
            source_engine = FrameEngine(source_df, source_label)
            target_engine = FrameEngine(target_df, target_label)
            result = self._run_reconciliation_job(
                run_job,
                source_engine,
                target_engine,
                query=FILE_SOURCE_QUERY,
                params={},
                chunk_size=0,
                use_hash_precheck=False,
            )
            return dataclasses.replace(
                result,
                source_file_name=None,
                target_file_name=self._job_file_name(job, "target"),
            )
        return run_job
```

No new imports are needed — `base64`, `dataclasses`, `BORestClient`, `EnvironmentConfig`, `FrameEngine`, `resolve_key_columns`, and `FILE_SOURCE_QUERY` are all already imported at the top of `run_executor.py` (lines 1-45).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_bo_live_reconciliation.py -v`
Expected: PASS (8 passed)

- [ ] **Step 6: Run the full existing RunExecutor + file-backed job suites to confirm no regression**

Run: `python -m pytest tests/unit/test_run_executor_live.py tests/unit/test_file_backed_jobs.py tests/unit/test_run_executor.py -v`
Expected: PASS, no changes to existing behavior

- [ ] **Step 7: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_bo_live_reconciliation.py
git commit -m "feat(run-executor): diff a live SAP BO pull against a target file (source_mode=bo_live)"
```

---

### Task 3: Job editor UI — `launch.js` state and payload building

**Files:**
- Modify: `frontend/features/launch.js:131-169` (`openNewJobModal`)
- Modify: `frontend/features/launch.js:240-293` (`openEditJobModal`)
- Modify: `frontend/features/launch.js:351-426` (`_buildJobRequestBody`)
- Modify: `frontend/features/launch.js:473-501` (`canSaveJob`)
- Modify: `frontend/features/launch.js` — add `handleJobTargetFileUpload` near `previewJobQuery`/`validateJob`

- [ ] **Step 1: Add jobModal fields to `openNewJobModal`**

In `frontend/features/launch.js`, in `openNewJobModal()` (around line 137-138), add the new fields to the `this.jobModal = {...}` object, right after the existing `source_file_path`/`target_file_path` lines:

```javascript
        source_file_path: '', target_file_path: '',
        source_file_label: '', target_file_label: '',
        target_source_mode: 'path', target_file_b64: '', target_file_name: '',
```

- [ ] **Step 2: Hydrate the new fields in `openEditJobModal`**

In `openEditJobModal(job)` (around line 246-249), add after the existing `target_file_label` line:

```javascript
        target_file_path: job.params?.target_file_path || job.params?.file_b_path || '',
        source_file_label: job.params?.source_file_label || job.params?.label_a || '',
        target_file_label: job.params?.target_file_label || job.params?.label_b || '',
        target_file_b64: job.params?.target_file_content_b64 || '',
        target_file_name: job.params?.target_file_name || '',
        target_source_mode: job.params?.target_file_content_b64 ? 'upload' : 'path',
```

- [ ] **Step 3: Add the upload handler**

In `frontend/features/launch.js`, add this method next to `previewJobQuery` (after its closing brace, around line 322):

```javascript
    handleJobTargetFileUpload(event) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        const bytes = new Uint8Array(e.target.result);
        let binary = '';
        for (let i = 0; i < bytes.length; i += 8192) {
          binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
        }
        this.jobModal.target_file_b64 = btoa(binary);
        this.jobModal.target_file_name = file.name;
      };
      reader.readAsArrayBuffer(file);
    },
```

- [ ] **Step 4: Serialize `bo_live` params in `_buildJobRequestBody`**

In `_buildJobRequestBody(m)` (around line 351-426):

1. Right after the existing `usesFileSource` block (after the closing `}` at line 363), add a new block and a `usesBoLive` flag:

```javascript
      const usesBoLive = m.job_type === 'reconciliation' && m.source_mode === 'bo_live';
      if (usesBoLive) {
        params.source_mode = 'bo_live';
        if (m.bo_report_id) params.report_id = m.bo_report_id;
        if (m.bo_page_id) params.bo_report_id = m.bo_page_id;
        params.format = m.bo_format || 'xlsx';
        if (m.target_source_mode === 'upload' && m.target_file_b64) {
          params.target_file_content_b64 = m.target_file_b64;
          if (m.target_file_name) params.target_file_name = m.target_file_name;
        } else if (m.target_file_path) {
          params.target_file_path = m.target_file_path;
        }
        if (m.target_file_label) params.target_file_label = m.target_file_label;
      }
```

2. Update the `query` field at the bottom of the function (currently `query: ['reconciliation', 'freshness', 'profile', 'schema_snapshot'].includes(m.job_type) && !usesFileSource ? m.query : ''`) to also exclude `bo_live`:

```javascript
        query: ['reconciliation', 'freshness', 'profile', 'schema_snapshot'].includes(m.job_type) && !usesFileSource && !usesBoLive ? m.query : '',
```

- [ ] **Step 5: Update `canSaveJob` for `bo_live`**

In `canSaveJob()` (around line 477-483), change:

```javascript
      if (m.job_type === 'reconciliation') {
        if (m.source_mode === 'files') {
          // key_columns is optional for file-backed jobs: the backend infers a
          // shared ID column, or falls back to positional row matching.
          return Boolean(m.source_file_path && m.target_file_path);
        }
        return Boolean(m.query?.trim() && hasKeys);
      }
```

to:

```javascript
      if (m.job_type === 'reconciliation') {
        if (m.source_mode === 'files') {
          // key_columns is optional for file-backed jobs: the backend infers a
          // shared ID column, or falls back to positional row matching.
          return Boolean(m.source_file_path && m.target_file_path);
        }
        if (m.source_mode === 'bo_live') {
          const hasTarget = m.target_source_mode === 'upload'
            ? Boolean(m.target_file_b64)
            : Boolean(m.target_file_path);
          return Boolean(m.bo_report_id && m.bo_page_id && hasTarget);
        }
        return Boolean(m.query?.trim() && hasKeys);
      }
```

- [ ] **Step 6: Manual smoke check (no unit test harness for this Alpine file)**

This file has no existing unit test suite (it's Alpine.js view-model code, covered by the e2e suite instead — see Task 5). Skip straight to Task 4 (markup) before verifying end-to-end.

- [ ] **Step 7: Commit**

```bash
git add frontend/features/launch.js
git commit -m "feat(launch-ui): add bo_live source_mode state and payload building to job editor"
```

---

### Task 4: Job editor UI — `tab-launch.html` markup

**Files:**
- Modify: `frontend/partials/tab-launch.html:350-460`

- [ ] **Step 1: Add the "Live BO Report" option to the Input Source select**

Change (around line 352-355):

```html
          <select x-model="jobModal.source_mode" class="field-input field-select" data-testid="job-modal-source-mode-select">
            <option value="sql">SQL Query</option>
            <option value="files">Files</option>
          </select>
```

to:

```html
          <select x-model="jobModal.source_mode" class="field-input field-select" data-testid="job-modal-source-mode-select">
            <option value="sql">SQL Query</option>
            <option value="files">Files</option>
            <option value="bo_live">Live BO Report</option>
          </select>
```

(The option is always present in the list, same as `sql`/`files`; it's only meaningful when `job_type === 'reconciliation'`, matching how the backend validator only branches on `source_mode` for that job type.)

- [ ] **Step 2: Hide the SQL Query textarea when `source_mode === 'bo_live'`**

Change the SQL Query block's `x-show` (currently line 363):

```html
        <div x-show="jobModal.job_type === 'reconciliation' && jobModal.source_mode !== 'files'">
```

to:

```html
        <div x-show="jobModal.job_type === 'reconciliation' && !['files', 'bo_live'].includes(jobModal.source_mode)">
```

- [ ] **Step 3: Show the existing BO doc/report/format fields for `bo_live` too**

Change (currently line 443):

```html
        <div x-show="jobModal.job_type === 'bo_report'" class="grid-2">
```

to:

```html
        <div x-show="jobModal.job_type === 'bo_report' || (jobModal.job_type === 'reconciliation' && jobModal.source_mode === 'bo_live')" class="grid-2">
```

(Leave the three fields inside — BO Document ID, BO Report/Page ID, Format — unchanged; they already bind to `jobModal.bo_report_id` / `jobModal.bo_page_id` / `jobModal.bo_format`, the same fields Task 3 wires into `_buildJobRequestBody`'s `usesBoLive` block.)

- [ ] **Step 4: Add the target-file block for `bo_live`**

Insert this new block immediately after the BO fields block from Step 3 (i.e., after its closing `</div>`, before the `automic_job` block):

```html
        <div x-show="jobModal.job_type === 'reconciliation' && jobModal.source_mode === 'bo_live'"
             class="border border-slate-200 rounded-lg p-3">
          <div class="mode-row mb-2">
            <button type="button" @click="jobModal.target_source_mode = 'path'"
                    :class="jobModal.target_source_mode !== 'upload' ? 'pill active' : 'pill'"
                    data-testid="job-modal-bo-live-target-mode-path">Path</button>
            <button type="button" @click="jobModal.target_source_mode = 'upload'"
                    :class="jobModal.target_source_mode === 'upload' ? 'pill active' : 'pill'"
                    data-testid="job-modal-bo-live-target-mode-upload">Upload</button>
          </div>
          <div class="grid-2">
            <div x-show="jobModal.target_source_mode !== 'upload'">
              <label class="field-label">Target File Path (prod snapshot)</label>
              <input x-model="jobModal.target_file_path" class="field-input"
                     placeholder="C:\snapshots\prod_report.xlsx"
                     data-testid="job-modal-bo-live-target-path-input" />
            </div>
            <div x-show="jobModal.target_source_mode === 'upload'">
              <label class="field-label">Target File Upload (prod snapshot)</label>
              <input type="file" accept=".csv,.xlsx,.xls,.json,.xml,.tsv,.txt"
                     @change="handleJobTargetFileUpload($event)" class="field-input"
                     data-testid="job-modal-bo-live-target-upload-input" />
              <p x-show="jobModal.target_file_name" class="text-xs text-slate-500 mt-1"
                 x-text="'Selected: ' + jobModal.target_file_name"></p>
            </div>
            <div>
              <label class="field-label">Target Label</label>
              <input x-model="jobModal.target_file_label" class="field-input" placeholder="Prod snapshot" />
            </div>
          </div>
        </div>
```

- [ ] **Step 5: Commit**

```bash
git add frontend/partials/tab-launch.html
git commit -m "feat(launch-ui): add Live BO Report source mode fields to job editor markup"
```

---

### Task 5: Compare tab UX hint

**Files:**
- Modify: `frontend/partials/tab-compare.html:47-52` (Source A card title area)

- [ ] **Step 1: Add a clarifying hint above the BO Report source cards**

The BO Report compare tab (`frontend/partials/tab-compare.html`) already supports Source A=Live / Source B=Upload independently — this was found to already work during design, but wasn't discoverable. Add a one-line hint. Insert immediately before the `<div class="compare-card-title">Source A</div>` line (around line 47):

```html
      <p class="text-xs text-slate-500 mb-2">
        Mix modes freely — e.g. Source A = Live (pull now from SAP BO) vs Source B = Upload
        (an already-downloaded file, such as a prod snapshot) to reconcile a live QA pull
        against a static prod export.
      </p>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/partials/tab-compare.html
git commit -m "docs(compare-ui): clarify that BO Report sources can mix Live and Upload"
```

---

### Task 6: End-to-end test

**Files:**
- Modify: `tests/e2e/02-launch-jobs.spec.ts`

- [ ] **Step 1: Add the e2e test**

Add this test to the `test.describe('02 launch/jobs', ...)` block in `tests/e2e/02-launch-jobs.spec.ts`, after the existing `'file-mode reconciliation job can be saved with no key columns'` test:

```typescript
  test('bo_live reconciliation job (live QA pull vs uploaded prod file) can be saved', async ({ authedPage }) => {
    const name = `e2e-bo-live-job-${Date.now()}`;
    createdJobNames.push(name);

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeVisible();

    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(name);
    await authedPage.locator('[data-testid="job-modal-source-mode-select"]').selectOption('bo_live');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();

    await authedPage.locator('input.field-input[placeholder="101"]').fill('9001');
    await authedPage.locator('input.field-input[placeholder="1"]').fill('2');

    await authedPage.locator('[data-testid="job-modal-bo-live-target-mode-upload"]').click();
    await authedPage.locator('[data-testid="job-modal-bo-live-target-upload-input"]')
      .setInputFiles(path.join(__dirname, 'fixtures', 'data', 'target.csv'));
    await expect(authedPage.getByText('Selected: target.csv')).toBeVisible();

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();

    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();
    await expect(authedPage.locator(`[data-testid="job-row-${name}"]`)).toBeVisible();
  });
```

Add `import path from 'node:path';` to the top of the file if it isn't already imported (check the existing `import` lines first — `08b-compare-reconciliation.spec.ts` uses this same pattern for its `dataFile` helper; `02-launch-jobs.spec.ts` currently only imports from `./fixtures` and `./api-helpers`, so this import needs to be added).

- [ ] **Step 2: Run the new e2e test**

Run: `npx playwright test tests/e2e/02-launch-jobs.spec.ts -g "bo_live"`
Expected: PASS

- [ ] **Step 3: Run the full 02-launch-jobs suite to confirm no regression**

Run: `npx playwright test tests/e2e/02-launch-jobs.spec.ts`
Expected: PASS (all tests)

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/02-launch-jobs.spec.ts
git commit -m "test(e2e): cover creating a bo_live reconciliation job via the job editor"
```

---

### Task 7: Full regression pass

- [ ] **Step 1: Run the full backend unit suite**

Run: `python -m pytest tests/unit -v`
Expected: PASS, no regressions

- [ ] **Step 2: Run the full e2e suite touching Launch and Compare**

Run: `npx playwright test tests/e2e/02-launch-jobs.spec.ts tests/e2e/08a-compare-bo-report.spec.ts tests/e2e/08b-compare-reconciliation.spec.ts`
Expected: PASS, no regressions

- [ ] **Step 3: Final commit if anything was left uncommitted**

```bash
git status
```

Expected: clean tree (everything already committed in Tasks 1-6).
