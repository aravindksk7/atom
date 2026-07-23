# Multi-File Reconciliation — Phase 8: S3/SFTP Support in Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `POST /api/jobs/preview-file-mapping` (used by both the job editor's Preview Mapping button and the Compare tab's Multi-File preview) discover and pair `s3`/`sftp` sources, not just `local` — resolving the "open design question" every prior phase's limitations section has flagged since Phase 6.

**Architecture:** This is the second (and, per the Phase 6 AskUserQuestion, final) explicitly-deferred item from the original 6-phase roadmap (`docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md` §7). Phase 7 (Compare-tab ad-hoc multi-file support) was picked first and is merged; this is the other deferred item.

**The design question, answered (researched fresh for this plan, not assumed):** At real job-execution time, `credentials_ref` resolves against `config_snapshot["file_source_credentials"][credentials_ref]` (`api/services/multi_file_remote.py:33-38`, `resolve_file_source_credentials`) — a plain dict key, not a row in any dedicated connections table. Research for this plan (grepping the whole repo for `file_source_credentials`, `/api/connections`, `Connection` models) confirms **no pre-existing named-connections registry exists** that preview could reuse instead — the intended design was always "resolved through a saved job's `config_snapshot`," which by definition doesn't exist yet at preview time. There is no shortcut through something already built.

**The resolution this phase takes:** the preview endpoint accepts an optional `file_source_credentials` map **inline in the request body**, in the exact same shape `config_snapshot["file_source_credentials"]` already uses. The caller (job editor or Compare tab) supplies raw credentials for this one preview call only — they are never persisted, never written into the saved job's `params.file_mapping` (which still only stores `credentials_ref` as a string, unchanged), and never touch any `SavedConfig`/`ConfigRepository` row. This is not a new class of secret-handling risk: the exact same `resolve_file_source_credentials`/`build_s3_client`/`build_sftp_client` functions already handle real secrets this way for saved jobs — this phase just also invokes that path one step earlier in the lifecycle (preview time, not run time).

**Scope decisions made for this phase** (each deliberate, not oversights):
- **Job editor only, not the Compare tab.** The Compare tab's ad-hoc multi-file *run* (Phase 7) deliberately stays `local`-only — Phase 7's plan explicitly scoped remote ad-hoc execution out ("credentials-resolution-without-a-saved-job is an unsolved design question... explicitly not picked this round" — for *running*, not just previewing). Letting the Compare tab *preview* s3/sftp while still being unable to *run* them would be a confusing half-feature, so the Compare tab's Multi-File sub-tab keeps its hardcoded `kind: "local"` form exactly as Phase 7 left it. Only the job editor (where saved `multi_file` jobs already fully support s3/sftp execution, just not preview) gains this.
- **No named-connections registry.** Building a real `Connection`/`S3Connection` CRUD concept (new table, new API, new admin UI) would solve the "open design question" more durably, but is a much larger project than "let preview accept inline credentials," and nothing in this codebase's existing multi_file design commits to that direction yet (see the architecture-question research above). Inline ad-hoc credentials, scoped to the single preview call, is the YAGNI-correct choice here.
- **No SSH key auth for SFTP preview.** `build_sftp_client` (`multi_file_remote.py:57-65`) only ever does `transport.connect(username=..., password=...)` — there is no key-based auth path anywhere in this codebase today for *any* SFTP flow (saved job or preview). This phase matches existing capability, not extends it.
- **Preview credential fields are never round-tripped through `openEditJobModal`.** They default blank every time the job modal opens (new or edit) — there is nothing to hydrate from, since they're never saved.

**Tech Stack:** FastAPI, Pydantic, Alpine.js (no build step for JS — HTML partial changes need `npm run build:html`), Playwright.

**Spec coverage in this phase:**
1. `POST /api/jobs/preview-file-mapping` discovers, reads (for automated-strategy pairing), and pairs `s3`/`sftp` sources using inline credentials, reusing the existing `RemoteFileSourceSession` abstraction (built in an earlier phase, already proven by `RunExecutor`/`difference_export`/`CompareService`) instead of duplicating local-only discovery logic.
2. Job editor UI: Preview Mapping button is no longer disabled for `s3`/`sftp` kinds; new preview-scoped credential input fields appear per side, clearly labeled as not persisted.
3. Playwright e2e coverage (network-mocked, since no real S3/SFTP infrastructure exists in this test environment — a deliberate, documented deviation from this suite's usual real-backend convention, explained in Task 5).
4. Documentation updated.

---

### Task 1: `PreviewFileMappingRequest` schema

**Files:**
- Modify: `api/schemas.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_preview_file_mapping_request.py`:

```python
# tests/unit/test_preview_file_mapping_request.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import PreviewFileMappingRequest


def test_preview_file_mapping_request_requires_file_mapping() -> None:
    with pytest.raises(ValidationError):
        PreviewFileMappingRequest()


def test_preview_file_mapping_request_defaults_credentials_to_empty_dict() -> None:
    req = PreviewFileMappingRequest(file_mapping={
        "match_on": ["region"],
        "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv"},
        "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
    })
    assert req.file_source_credentials == {}


def test_preview_file_mapping_request_accepts_inline_credentials() -> None:
    req = PreviewFileMappingRequest(
        file_mapping={
            "match_on": ["region"],
            "source": {"kind": "s3", "root": "s3://bucket/prefix", "pattern": "sales_{region}.csv", "credentials_ref": "aws_source"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
        },
        file_source_credentials={"aws_source": {"aws_access_key_id": "AKIA...", "aws_secret_access_key": "s3cr3t"}},
    )
    assert req.file_source_credentials["aws_source"]["aws_access_key_id"] == "AKIA..."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_preview_file_mapping_request.py -v`
Expected: FAIL with `ImportError: cannot import name 'PreviewFileMappingRequest'`

- [ ] **Step 3: Write minimal implementation**

In `api/schemas.py`, find `MultiFileCompareRequest` (it ends with the `advanced: AdvancedCompareOptions = Field(default_factory=AdvancedCompareOptions)` line right before `class SQLCompareRequest(BaseModel):`). Add a new class right after it:

```python
class PreviewFileMappingRequest(BaseModel):
    """Body for POST /api/jobs/preview-file-mapping. ``file_mapping`` is the
    same config shape used inside a saved multi_file job's
    ``params.file_mapping`` (see FileMappingSpec.from_params). Local sources
    need nothing else; s3/sftp sources need ``credentials_ref`` set on the
    relevant side AND a matching entry in ``file_source_credentials`` --
    there's no saved job yet at preview time to resolve a persisted
    credentials_ref against (see
    ``config_snapshot["file_source_credentials"]`` for the saved-job
    equivalent, ``api/services/multi_file_remote.py``'s
    ``resolve_file_source_credentials``), so the caller supplies raw
    credentials inline instead, keyed the same way. These credentials are
    used for this one preview call only -- never persisted anywhere.
    """
    file_mapping: dict[str, Any] = Field(...)
    file_source_credentials: dict[str, dict[str, Any]] = Field(default_factory=dict)
```

`BaseModel`, `Field`, and `Any` are already imported and used by `MultiFileCompareRequest` immediately above — no new imports needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_preview_file_mapping_request.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_preview_file_mapping_request.py
git commit -m "feat(schemas): add PreviewFileMappingRequest with inline file_source_credentials"
```

---

### Task 2: Relax the preview endpoint's local-only gate; support s3/sftp via `RemoteFileSourceSession`

**Files:**
- Modify: `api/routes/jobs.py`
- Modify: `tests/unit/test_api.py`

**Verified current implementation** (`api/routes/jobs.py:151-217`, confirmed by direct read — not assumed):

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

- [ ] **Step 1: Write the failing tests**

The endpoint's request body is changing from a raw `dict` to a typed `PreviewFileMappingRequest` — this changes `test_preview_file_mapping_rejects_missing_file_mapping`'s expected status (FastAPI/Pydantic now rejects a missing required field with 422 automatically, before the route body ever runs, instead of the route's own `ValueError` → 400 path). It also removes the premise of `test_preview_file_mapping_rejects_remote_kinds` (remote kinds are no longer rejected) — that test is replaced by new success-path tests below.

In `tests/unit/test_api.py`, replace the existing block from `test_preview_file_mapping_rejects_remote_kinds` through `test_preview_file_mapping_rejects_missing_file_mapping` (lines 682-696, confirmed by direct read):

```python
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

with:

```python
def test_preview_file_mapping_rejects_missing_file_mapping(client):
    resp = client.post("/api/jobs/preview-file-mapping", json={})
    assert resp.status_code == 422  # file_mapping is required on PreviewFileMappingRequest


def test_preview_file_mapping_supports_s3_pairs(client, monkeypatch):
    class _FakeBody:
        def __init__(self, raw: bytes) -> None:
            self._raw = raw

        def read(self) -> bytes:
            return self._raw

    class _FakeS3Client:
        objects = {
            "source/sales_east.csv": b"id,value\n1,alpha\n",
            "source/sales_west.csv": b"id,value\n2,beta\n",
            "target/financials_east.csv": b"id,value\n1,alpha\n",
            "target/financials_west.csv": b"id,value\n2,BETA\n",
        }

        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return self

        def paginate(self, **kwargs):
            prefix = kwargs["Prefix"]
            return [{"Contents": [{"Key": key} for key in self.objects if key.startswith(prefix)]}]

        def get_object(self, **kwargs):
            return {"Body": _FakeBody(self.objects[kwargs["Key"]])}

    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", lambda config_snapshot, spec: _FakeS3Client())

    resp = client.post("/api/jobs/preview-file-mapping", json={
        "file_mapping": {
            "strategy": "explicit",
            "match_on": ["region"],
            "source": {"kind": "s3", "root": "s3://finance/source", "pattern": "sales_{region}.csv", "credentials_ref": "aws_source"},
            "target": {"kind": "s3", "root": "s3://finance/target", "pattern": "financials_{region}.csv", "credentials_ref": "aws_target"},
        },
        "file_source_credentials": {
            "aws_source": {"aws_access_key_id": "AKIA_FAKE", "aws_secret_access_key": "s3cr3t"},
            "aws_target": {"aws_access_key_id": "AKIA_FAKE", "aws_secret_access_key": "s3cr3t"},
        },
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["pairs_total"] == 2
    by_region = {p["key"]["region"]: p for p in body["pairs"]}
    assert by_region["east"]["source_files"] == ["sales_east.csv"]
    assert by_region["west"]["target_files"] == ["financials_west.csv"]


def test_preview_file_mapping_supports_sftp_pairs(client, monkeypatch):
    class _FakeSFTPFile:
        def __init__(self, raw: bytes) -> None:
            self._raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self) -> bytes:
            return self._raw

    class _FakeSFTPClient:
        objects = {
            "/source/sales_east.csv": b"id,value\n1,alpha\n",
            "/target/financials_east.csv": b"id,value\n1,alpha\n",
        }

        def listdir_attr(self, path):
            prefix = path.rstrip("/") + "/"
            names = sorted(
                key[len(prefix):] for key in self.objects
                if key.startswith(prefix) and "/" not in key[len(prefix):]
            )
            return [type("Attr", (), {"filename": name, "st_mode": 0o100644})() for name in names]

        def open(self, path, mode):
            return _FakeSFTPFile(self.objects[path])

        def close(self):
            pass

    monkeypatch.setattr("api.services.multi_file_remote.build_sftp_client", lambda config_snapshot, spec: _FakeSFTPClient())

    resp = client.post("/api/jobs/preview-file-mapping", json={
        "file_mapping": {
            "strategy": "explicit",
            "match_on": ["region"],
            "source": {"kind": "sftp", "root": "/source", "pattern": "sales_{region}.csv", "credentials_ref": "sftp_source"},
            "target": {"kind": "sftp", "root": "/target", "pattern": "financials_{region}.csv", "credentials_ref": "sftp_target"},
        },
        "file_source_credentials": {
            "sftp_source": {"host": "sftp.internal", "username": "svc", "password": "secret"},
            "sftp_target": {"host": "sftp.internal", "username": "svc", "password": "secret"},
        },
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["pairs_total"] == 1
    assert body["pairs"][0]["key"] == {"region": "east"}


def test_preview_file_mapping_surfaces_remote_connection_error_as_400(client, monkeypatch):
    def _raise(config_snapshot, spec):
        raise RuntimeError("could not connect")

    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", _raise)

    resp = client.post("/api/jobs/preview-file-mapping", json={
        "file_mapping": {
            "match_on": ["region"],
            "source": {"kind": "s3", "root": "s3://bucket/prefix", "pattern": "sales_{region}.csv", "credentials_ref": "aws_source"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
        },
    })
    assert resp.status_code == 400
    assert "could not connect" in resp.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_api.py -k preview_file_mapping -v`
Expected: `test_preview_file_mapping_rejects_missing_file_mapping` FAILs (still gets 400, not yet 422); the three new tests FAIL with 400 "Preview only supports 'local'..." (the gate hasn't been relaxed yet).

- [ ] **Step 3: Write minimal implementation**

In `api/routes/jobs.py`, change the top-of-file import (currently `from api.schemas import JobDefinition`) to:

```python
from api.schemas import JobDefinition, PreviewFileMappingRequest
```

Replace the entire `preview_file_mapping` function (`jobs.py:151-217`, shown in full above) with:

```python
@router.post("/preview-file-mapping")
def preview_file_mapping(body: PreviewFileMappingRequest):
    from etl_framework.reconciliation.file_mapping import FileMappingSpec, pair_files, pair_files_automated
    from api.services.multi_file_remote import RemoteFileSourceSession

    try:
        spec = FileMappingSpec.from_params({"file_mapping": body.file_mapping})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        with RemoteFileSourceSession({"file_source_credentials": body.file_source_credentials}) as session:
            source_files = session.discover(spec.source)
            target_files = session.discover(spec.target)

            if spec.strategy == "automated":
                source_frames = {f.path: session.read_file(f, spec.source) for f in source_files}
                target_frames = {f.path: session.read_file(f, spec.target) for f in target_files}
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

Notes on this change:
- The `kind != "local"` gate (old lines 166-170) is gone entirely — `RemoteFileSourceSession.discover`/`.read_file` already dispatch on `spec.kind` for all three kinds (`local`/`s3`/`sftp`), and raise `ValueError` for anything else (caught by the same `except Exception` → 400 as before).
- `resolve_allowed_path`, `discover_local_files`, `read_tabular` are no longer imported directly in this function — `RemoteFileSourceSession`'s `local` branch already calls `resolve_allowed_path`/`discover_local_files`/`read_tabular` internally (`api/services/multi_file_remote.py:101-113`), so nothing is lost, just no longer duplicated here.
- `RemoteFileSourceSession({"file_source_credentials": body.file_source_credentials})` builds an ephemeral config-snapshot-shaped dict containing only the caller-supplied inline credentials — `resolve_file_source_credentials` (part of the session's client-building path) reads exactly this key, so this is a drop-in substitute for a real job's `config_snapshot` for the duration of this one preview call.
- The pairing/aggregation/response-building logic below the `try` block is byte-for-byte unchanged — only how files get discovered and read changed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api.py -k preview_file_mapping -v`
Expected: PASS (5 tests: the local-explicit happy path, missing-mapping-422, s3-pairs, sftp-pairs, connection-error-400)

- [ ] **Step 5: Run the broader jobs-route suite to confirm no regression**

Run: `python -m pytest tests/unit/test_api.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/routes/jobs.py tests/unit/test_api.py
git commit -m "feat(jobs): support s3/sftp in preview-file-mapping via inline credentials"
```

---

### Task 3: Job editor UI — enable preview for s3/sftp, add preview-only credential fields

**Files:**
- Modify: `frontend/partials/tab-launch.html`

**Verified current markup** (`tab-launch.html:536-598`, confirmed by direct read):

```html
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
```

- [ ] **Step 1: Add `data-testid`s to the kind selects, and preview-credential fields to the Source block**

Change:

```html
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
```

to:

```html
              <select x-model="jobModal.mf_source_kind" class="field-input field-select" data-testid="job-modal-mf-source-kind-select">
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
              <input x-model="jobModal.mf_source_preview_creds.aws_access_key_id" class="field-input" placeholder="AWS Access Key ID (preview only)"
                     x-show="jobModal.mf_source_kind === 's3'" data-testid="job-modal-mf-source-s3-access-key-input" />
              <input x-model="jobModal.mf_source_preview_creds.aws_secret_access_key" type="password" class="field-input" placeholder="AWS Secret Access Key (preview only)"
                     x-show="jobModal.mf_source_kind === 's3'" data-testid="job-modal-mf-source-s3-secret-key-input" />
              <input x-model="jobModal.mf_source_preview_creds.region_name" class="field-input" placeholder="Region (optional)"
                     x-show="jobModal.mf_source_kind === 's3'" />
              <input x-model="jobModal.mf_source_preview_creds.endpoint_url" class="field-input" placeholder="Endpoint URL (optional)"
                     x-show="jobModal.mf_source_kind === 's3'" />
              <input x-model="jobModal.mf_source_preview_creds.host" class="field-input" placeholder="SFTP host (preview only)"
                     x-show="jobModal.mf_source_kind === 'sftp'" data-testid="job-modal-mf-source-sftp-host-input" />
              <input x-model="jobModal.mf_source_preview_creds.port" class="field-input" placeholder="Port (default 22)"
                     x-show="jobModal.mf_source_kind === 'sftp'" />
              <input x-model="jobModal.mf_source_preview_creds.username" class="field-input" placeholder="Username"
                     x-show="jobModal.mf_source_kind === 'sftp'" />
              <input x-model="jobModal.mf_source_preview_creds.password" type="password" class="field-input" placeholder="Password"
                     x-show="jobModal.mf_source_kind === 'sftp'" data-testid="job-modal-mf-source-sftp-password-input" />
            </div>
            <p x-show="jobModal.mf_source_kind !== 'local'" class="text-xs text-slate-400 mt-1">
              Credentials above are used only for Preview Mapping and are never saved with the job.
            </p>
          </div>
```

- [ ] **Step 2: Mirror the same change for the Target block**

Change:

```html
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
```

to:

```html
              <select x-model="jobModal.mf_target_kind" class="field-input field-select" data-testid="job-modal-mf-target-kind-select">
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
              <input x-model="jobModal.mf_target_preview_creds.aws_access_key_id" class="field-input" placeholder="AWS Access Key ID (preview only)"
                     x-show="jobModal.mf_target_kind === 's3'" data-testid="job-modal-mf-target-s3-access-key-input" />
              <input x-model="jobModal.mf_target_preview_creds.aws_secret_access_key" type="password" class="field-input" placeholder="AWS Secret Access Key (preview only)"
                     x-show="jobModal.mf_target_kind === 's3'" data-testid="job-modal-mf-target-s3-secret-key-input" />
              <input x-model="jobModal.mf_target_preview_creds.region_name" class="field-input" placeholder="Region (optional)"
                     x-show="jobModal.mf_target_kind === 's3'" />
              <input x-model="jobModal.mf_target_preview_creds.endpoint_url" class="field-input" placeholder="Endpoint URL (optional)"
                     x-show="jobModal.mf_target_kind === 's3'" />
              <input x-model="jobModal.mf_target_preview_creds.host" class="field-input" placeholder="SFTP host (preview only)"
                     x-show="jobModal.mf_target_kind === 'sftp'" data-testid="job-modal-mf-target-sftp-host-input" />
              <input x-model="jobModal.mf_target_preview_creds.port" class="field-input" placeholder="Port (default 22)"
                     x-show="jobModal.mf_target_kind === 'sftp'" />
              <input x-model="jobModal.mf_target_preview_creds.username" class="field-input" placeholder="Username"
                     x-show="jobModal.mf_target_kind === 'sftp'" />
              <input x-model="jobModal.mf_target_preview_creds.password" type="password" class="field-input" placeholder="Password"
                     x-show="jobModal.mf_target_kind === 'sftp'" data-testid="job-modal-mf-target-sftp-password-input" />
            </div>
            <p x-show="jobModal.mf_target_kind !== 'local'" class="text-xs text-slate-400 mt-1">
              Credentials above are used only for Preview Mapping and are never saved with the job.
            </p>
          </div>
```

- [ ] **Step 3: Relax the Preview Mapping button's disabled condition and remove the local-only hint**

Change:

```html
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
```

to:

```html
              <button @click="previewFileMapping()"
                      :disabled="jobModal.mfPreviewLoading"
                      class="btn-sm btn-secondary text-xs px-3 py-1 disabled:opacity-40"
                      data-testid="job-modal-mf-preview-btn">
                <span x-show="!jobModal.mfPreviewLoading">Preview Mapping</span>
                <span x-show="jobModal.mfPreviewLoading">Loading…</span>
              </button>
            </div>
```

- [ ] **Step 4: Rebuild `index.html`**

Run: `npm run build:html`
Then run: `git diff --stat frontend/index.html` and confirm it shows a change.

- [ ] **Step 5: Smoke-check existing Launch/job-editor specs still pass**

Run: `npx playwright test tests/e2e/02-launch-jobs.spec.ts tests/e2e/17-multi-file-reconciliation.spec.ts --reporter=list`
Expected: all still PASS (confirms the added markup doesn't break the existing local-kind job-creation flow, including 17-multi-file-reconciliation.spec.ts's UI test which creates a `local`-kind multi_file job through this same modal).

- [ ] **Step 6: Commit**

```bash
git add frontend/partials/tab-launch.html frontend/index.html
git commit -m "feat(jobs): enable Preview Mapping for s3/sftp job-editor sources"
```

---

### Task 4: Job editor JS — preview-credential state and payload building

**Files:**
- Modify: `frontend/features/launch.js`

**Verified current state/method** (confirmed by direct read):

`launch.js:171-172` (new-job defaults):
```js
        mf_source_kind: 'local', mf_source_root: '', mf_source_pattern: '', mf_source_credentials_ref: '',
        mf_target_kind: 'local', mf_target_root: '', mf_target_pattern: '', mf_target_credentials_ref: '',
```

`launch.js:310-317` (edit-job hydration):
```js
        mf_source_kind: job.params?.file_mapping?.source?.kind || 'local',
        mf_source_root: job.params?.file_mapping?.source?.root || '',
        mf_source_pattern: job.params?.file_mapping?.source?.pattern || '',
        mf_source_credentials_ref: job.params?.file_mapping?.source?.credentials_ref || '',
        mf_target_kind: job.params?.file_mapping?.target?.kind || 'local',
        mf_target_root: job.params?.file_mapping?.target?.root || '',
        mf_target_pattern: job.params?.file_mapping?.target?.pattern || '',
        mf_target_credentials_ref: job.params?.file_mapping?.target?.credentials_ref || '',
```

`launch.js:386-400` (`previewFileMapping`, full method):
```js
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

- [ ] **Step 1: Add blank preview-credential state to both `newJobModal` and `openEditJobModal`**

In `newJobModal` (the method containing the block at `launch.js:171-172`), change:

```js
        mf_source_kind: 'local', mf_source_root: '', mf_source_pattern: '', mf_source_credentials_ref: '',
        mf_target_kind: 'local', mf_target_root: '', mf_target_pattern: '', mf_target_credentials_ref: '',
```

to:

```js
        mf_source_kind: 'local', mf_source_root: '', mf_source_pattern: '', mf_source_credentials_ref: '',
        mf_target_kind: 'local', mf_target_root: '', mf_target_pattern: '', mf_target_credentials_ref: '',
        mf_source_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
        mf_target_preview_creds: { aws_access_key_id: '', aws_secret_access_key: '', region_name: '', endpoint_url: '', host: '', port: '', username: '', password: '' },
```

In `openEditJobModal` (the block at `launch.js:310-317`), change:

```js
        mf_source_kind: job.params?.file_mapping?.source?.kind || 'local',
        mf_source_root: job.params?.file_mapping?.source?.root || '',
        mf_source_pattern: job.params?.file_mapping?.source?.pattern || '',
        mf_source_credentials_ref: job.params?.file_mapping?.source?.credentials_ref || '',
        mf_target_kind: job.params?.file_mapping?.target?.kind || 'local',
        mf_target_root: job.params?.file_mapping?.target?.root || '',
        mf_target_pattern: job.params?.file_mapping?.target?.pattern || '',
        mf_target_credentials_ref: job.params?.file_mapping?.target?.credentials_ref || '',
```

to:

```js
        mf_source_kind: job.params?.file_mapping?.source?.kind || 'local',
        mf_source_root: job.params?.file_mapping?.source?.root || '',
        mf_source_pattern: job.params?.file_mapping?.source?.pattern || '',
        mf_source_credentials_ref: job.params?.file_mapping?.source?.credentials_ref || '',
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

- [ ] **Step 2: Add `_previewCredsForKind()` helper and rewrite `previewFileMapping()`**

Find `_buildFileMappingConfig(m)` (`launch.js:358-384` — do not modify this method; it must keep producing the real, persisted `params.file_mapping` shape unchanged). Insert a new helper right after it ends, then replace `previewFileMapping()`:

Change:

```js
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

to:

```js
    _previewCredsForKind(kind, creds) {
      const out = {};
      if (kind === 's3') {
        if (creds.aws_access_key_id) out.aws_access_key_id = creds.aws_access_key_id;
        if (creds.aws_secret_access_key) out.aws_secret_access_key = creds.aws_secret_access_key;
        if (creds.region_name) out.region_name = creds.region_name;
        if (creds.endpoint_url) out.endpoint_url = creds.endpoint_url;
      } else if (kind === 'sftp') {
        if (creds.host) out.host = creds.host;
        if (creds.port !== '' && creds.port !== null && creds.port !== undefined) {
          const port = Number(creds.port);
          if (Number.isFinite(port)) out.port = port;
        }
        if (creds.username) out.username = creds.username;
        if (creds.password) out.password = creds.password;
      }
      return out;
    },

    async previewFileMapping() {
      const m = this.jobModal;
      m.mfPreviewLoading = true;
      m.mfPreviewResult = null;
      m.mfPreviewError = '';
      try {
        // _buildFileMappingConfig(m) returns a fresh object every call (not
        // a reference into jobModal state), so mutating its credentials_ref
        // below for this one preview request is safe -- it never touches
        // the persisted job config the Save button will later write.
        const fileMapping = this._buildFileMappingConfig(m);
        const fileSourceCredentials = {};
        if (m.mf_source_kind !== 'local') {
          fileMapping.source.credentials_ref = '__preview_source__';
          fileSourceCredentials.__preview_source__ = this._previewCredsForKind(m.mf_source_kind, m.mf_source_preview_creds);
        }
        if (m.mf_target_kind !== 'local') {
          fileMapping.target.credentials_ref = '__preview_target__';
          fileSourceCredentials.__preview_target__ = this._previewCredsForKind(m.mf_target_kind, m.mf_target_preview_creds);
        }
        m.mfPreviewResult = await api('POST', '/api/jobs/preview-file-mapping', {
          file_mapping: fileMapping,
          file_source_credentials: fileSourceCredentials,
        });
      } catch (e) {
        m.mfPreviewError = e.message || 'Preview failed';
      } finally {
        m.mfPreviewLoading = false;
      }
    },
```

The `credentials_ref` override to `__preview_source__`/`__preview_target__` is deliberate and always applied (regardless of what the user typed into the real `mf_source_credentials_ref`/`mf_target_credentials_ref` fields, which are for the *saved job's* eventual real execution): it guarantees the preview call's `file_source_credentials` map keys line up with what gets sent, without requiring the user to have already decided a real `credentials_ref` name just to preview, and without colliding if source and target happen to share a typed-in ref.

- [ ] **Step 3: Run the smoke specs again**

Run: `npx playwright test tests/e2e/02-launch-jobs.spec.ts tests/e2e/17-multi-file-reconciliation.spec.ts --reporter=list`
Expected: all PASS (no HTML build step needed for this task — only JS was touched)

- [ ] **Step 4: Commit**

```bash
git add frontend/features/launch.js
git commit -m "feat(jobs): send inline preview credentials for s3/sftp mapping preview"
```

---

### Task 5: Playwright e2e coverage

**Files:**
- Create: `tests/e2e/02b-launch-jobs-remote-preview.spec.ts`

**Why network-mocked, not a real S3/SFTP round-trip:** every other e2e spec in this suite (`17-multi-file-reconciliation.spec.ts`, `08g-compare-multi-file.spec.ts`) exercises the real backend against real local fixture files — there is no real S3 bucket or SFTP server available in this test environment, and standing one up is out of scope for what is otherwise a small UI-wiring phase. This test instead uses Playwright's `page.route()` to intercept the `POST /api/jobs/preview-file-mapping` call, asserting on the **outgoing request body** (proving the frontend correctly builds `file_source_credentials` with the `__preview_source__`/`__preview_target__` sentinel keys) and returning a canned response (proving the result renders). This is a deliberate, one-time deviation from this suite's usual convention, scoped to exactly this test file — Task 2's backend unit tests (hand-rolled fake S3/SFTP clients, same pattern already established by `test_multi_file_remote.py`/`test_multi_file_jobs.py`) are what actually prove the discovery/pairing logic works end-to-end against s3/sftp; this e2e test only proves the UI wiring.

- [ ] **Step 1: Write the test**

```ts
// tests/e2e/02b-launch-jobs-remote-preview.spec.ts
import { test, expect } from './fixtures';

test.describe('02b launch jobs / remote preview credentials', () => {
  test('s3 kind: preview is enabled, sends inline credentials, and renders the result', async ({ authedPage }) => {
    let capturedBody: any = null;
    await authedPage.route('**/api/jobs/preview-file-mapping', async (route) => {
      capturedBody = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          pairs_total: 1,
          pairs: [{ key: { region: 'east' }, source_files: ['sales_east.csv'], target_files: ['financials_east.csv'], similarity_score: null }],
          unmatched_sources: [],
          unmatched_targets: [],
        }),
      });
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeVisible();

    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(`e2e-remote-preview-${Date.now()}`);
    await authedPage.locator('[data-testid="job-modal-source-mode-select"]').selectOption('multi_file');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();

    await authedPage.locator('[data-testid="job-modal-mf-source-kind-select"]').selectOption('s3');
    await authedPage.locator('[data-testid="job-modal-mf-source-root-input"]').fill('s3://finance/source');
    await authedPage.locator('[data-testid="job-modal-mf-source-pattern-input"]').fill('sales_{region}.csv');
    await authedPage.locator('[data-testid="job-modal-mf-source-s3-access-key-input"]').fill('AKIA_TEST');
    await authedPage.locator('[data-testid="job-modal-mf-source-s3-secret-key-input"]').fill('test-secret');

    await authedPage.locator('[data-testid="job-modal-mf-target-root-input"]').fill('/baseline');
    await authedPage.locator('[data-testid="job-modal-mf-target-pattern-input"]').fill('financials_{region}.csv');

    // Preview must be enabled even though source kind is 's3', not 'local'.
    await expect(authedPage.locator('[data-testid="job-modal-mf-preview-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-mf-preview-btn"]').click();

    await expect(authedPage.locator('[data-testid="job-modal-mf-preview-result"]')).toContainText('1 pair(s) matched');
    await expect(authedPage.locator('[data-testid="job-modal-mf-preview-pair"]')).toHaveCount(1);

    expect(capturedBody.file_mapping.source.kind).toBe('s3');
    expect(capturedBody.file_mapping.source.credentials_ref).toBe('__preview_source__');
    expect(capturedBody.file_source_credentials.__preview_source__).toEqual({
      aws_access_key_id: 'AKIA_TEST',
      aws_secret_access_key: 'test-secret',
    });

    await authedPage.locator('[data-testid="job-modal-close-btn"]').click().catch(() => {});
  });

  test('sftp kind: preview credential fields appear and are sent', async ({ authedPage }) => {
    let capturedBody: any = null;
    await authedPage.route('**/api/jobs/preview-file-mapping', async (route) => {
      capturedBody = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ pairs_total: 0, pairs: [], unmatched_sources: [], unmatched_targets: [] }),
      });
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(`e2e-remote-preview-sftp-${Date.now()}`);
    await authedPage.locator('[data-testid="job-modal-source-mode-select"]').selectOption('multi_file');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();

    await authedPage.locator('[data-testid="job-modal-mf-target-kind-select"]').selectOption('sftp');
    await authedPage.locator('[data-testid="job-modal-mf-source-root-input"]').fill('/spool');
    await authedPage.locator('[data-testid="job-modal-mf-source-pattern-input"]').fill('sales_{region}.csv');
    await authedPage.locator('[data-testid="job-modal-mf-target-root-input"]').fill('/baseline');
    await authedPage.locator('[data-testid="job-modal-mf-target-pattern-input"]').fill('financials_{region}.csv');

    await expect(authedPage.locator('[data-testid="job-modal-mf-target-sftp-host-input"]')).toBeVisible();
    await authedPage.locator('[data-testid="job-modal-mf-target-sftp-host-input"]').fill('sftp.internal');
    await authedPage.locator('[data-testid="job-modal-mf-target-sftp-password-input"]').fill('sftp-secret');

    await authedPage.locator('[data-testid="job-modal-mf-preview-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal-mf-preview-result"]')).toContainText('0 pair(s) matched');

    expect(capturedBody.file_mapping.target.kind).toBe('sftp');
    expect(capturedBody.file_mapping.target.credentials_ref).toBe('__preview_target__');
    expect(capturedBody.file_source_credentials.__preview_target__.host).toBe('sftp.internal');
    expect(capturedBody.file_source_credentials.__preview_target__.password).toBe('sftp-secret');
  });
});
```

Check `job-modal-close-btn` is the correct testid for closing the modal without saving (grep `02-launch-jobs.spec.ts` for how it dismisses a modal it doesn't intend to save) — adjust the cleanup line in the first test if the real testid differs, or drop that line if there's no clean no-save close action and instead just leave the modal open (Playwright's test isolation via a fresh page/context per test makes this harmless either way).

- [ ] **Step 2: Run the new tests**

Run: `npx playwright test tests/e2e/02b-launch-jobs-remote-preview.spec.ts --reporter=list`
Expected: all PASS (2 tests)

If a selector doesn't match (e.g. `job-modal-tab-settings` naming, or the settings tab isn't where `mf_source_kind` fields actually live), inspect the real DOM via the failing test's trace/screenshot and correct the locator to match reality rather than guessing twice — this mirrors the exact troubleshooting approach used for `17-multi-file-reconciliation.spec.ts` in an earlier phase.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/02b-launch-jobs-remote-preview.spec.ts
git commit -m "test(e2e): cover s3/sftp preview credential wiring in the job editor"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/multi_file_reconciliation.md`

- [ ] **Step 1: Update the "Remote sources" and limitations sections**

Read the file first to confirm current section boundaries (the "Remote sources (S3 and SFTP)" section and the "Current limitations (Phase 7)" section, per Phase 7's final state). Add a new paragraph to the end of "Remote sources (S3 and SFTP)" documenting that Preview Mapping (job editor only, not the Compare tab) now supports `s3`/`sftp`, via an inline `file_source_credentials` request field (not the persisted `credentials_ref`), and that these preview-time credentials are never saved. Remove the limitations bullet that says preview is local-only (it's now stale); retitle the limitations section header to "(Phase 8)"; keep every other still-true bullet (readiness local-only, automated-matching single-file-only, ad-hoc Compare-tab run being local/sequential-only, template gap) unchanged, and add a bullet noting the Compare tab's own Multi-File preview is still local-only by deliberate choice (see this phase's scope decisions) even though the job editor's preview now supports s3/sftp.

- [ ] **Step 2: Commit**

```bash
git add docs/multi_file_reconciliation.md
git commit -m "docs: document s3/sftp support in job-editor preview-file-mapping"
```

---

## Self-review notes

- **Spec coverage:** Task 1-2 deliver the backend (typed request schema with inline credentials, endpoint relaxed to use the already-proven `RemoteFileSourceSession` for all 3 kinds instead of duplicating local-only logic). Task 3-4 deliver the job-editor frontend (credential fields, disabled-state relaxation, sentinel-keyed request building that never touches the persisted job config). Task 5 proves the UI wiring via network-mocked e2e (justified deviation from the suite's real-backend norm, explained inline). Task 6 documents it.
- **The "open design question" is answered, not dodged:** every prior phase's limitations section flagged this without answering it. This plan's header traces the actual codebase (no connections registry exists, `credentials_ref` is a config_snapshot dict key) before proposing inline-credentials-at-preview-time as the resolution, rather than assuming a registry could be reused.
- **Reuses proven code, doesn't reinvent:** `RemoteFileSourceSession`, `resolve_file_source_credentials`, `build_s3_client`/`build_sftp_client` (all from earlier phases) are used completely unchanged — this phase only changes what config-snapshot-shaped dict gets passed to the session's constructor (inline request data instead of a real `TestRun.config_snapshot`), and removes the kind gate + duplicate local-only discovery code that used to sit in front of them.
- **Scope decisions stated up front:** job-editor-only (not Compare tab), no connections registry, no SSH-key auth, preview creds never hydrated on edit — each with reasoning in the header, matching the documentation discipline established in every prior phase's plan.
- **Type/name consistency:** `PreviewFileMappingRequest`, `file_source_credentials`, `_previewCredsForKind`, `mf_source_preview_creds`/`mf_target_preview_creds`, `__preview_source__`/`__preview_target__` are spelled identically at every definition and call site across Tasks 1-5.
- **Security framing addressed directly, not ignored:** the header explicitly argues this isn't a new class of credential-handling risk (same resolver functions, just invoked earlier in the lifecycle) rather than leaving a reviewer to wonder whether sending raw AWS/SFTP secrets from the browser for a preview call is a new problem introduced by this phase.
