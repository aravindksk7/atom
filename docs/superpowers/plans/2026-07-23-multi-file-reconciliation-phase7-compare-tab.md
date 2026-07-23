# Multi-File Reconciliation — Phase 7: Compare Tab Ad-Hoc Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a QA engineer run a one-off multi-file reconciliation from the Compare tab — no saved job needed — the same way the existing `bo`/`recon`/`sql` sub-tabs already let them run one-off comparisons.

**Architecture:** This is a follow-on to the original 6-phase roadmap in `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md` §7 (all 6 phases are merged). It was explicitly deferred during Phase 6 planning ("Compare-tab ad-hoc multi-file support... a similarly-sized project to this one and is better scoped as its own phase") and picked as the next priority over the other deferred item (S3/SFTP in the preview endpoint).

**How the Compare tab actually works (researched fresh for this plan, not assumed):** `bo`/`sql`/`recon-file` are NOT stateless — each one creates a real `TestRun` DB row, queues a `BackgroundTasks` job, and the background task persists a real `TestResult` row (`RunRepository.add_test_result`) plus `MismatchDetail` rows, exactly like a saved job's run does. Only `colstats` and `mmdiff` are synchronous/stateless. Multi-file reconciliation is inherently the "real run" kind (potentially several file pairs, each needing a real `ReconciliationEngine.reconcile()` call), so this phase follows the `recon-file`/`sql` pattern exactly: `POST /api/compare/multi-file` → create `TestRun` → background task → `CompareService.run_multi_file_compare(...)` → persist one aggregate `TestResult` row. The frontend polls `GET /api/runs/{id}/status` until terminal, then fetches `GET /api/runs/{id}` — **identical polling code already exists** (`compare.js`'s `runFileCompare()`).

**Why this needs very little new backend infrastructure:** `TestResultOut.file_pairs` / `.unmatched_sources` / `.unmatched_targets` (`api/schemas.py:324-326`, backed by `FilePairSummaryOut`/`UnmatchedFileGroupOut`, `:284-300`) already exist and are already populated from `mismatch_summary` (`api/routes/runs.py`'s `_test_result_out`) — built for the saved-job Reports view in Phase 4. Since `aggregate_reconciliation_results()` returns a plain `ReconciliationResult` with that same `mismatch_summary` shape, persisting it via the existing `RunRepository.add_test_result(run_id, result)` gives the ad-hoc run the exact same wire format the Reports tab and job editor preview already use — **no new response schema, no changes to `api/routes/runs.py`**. Only a new request schema, a new service method, a new route, and new frontend markup are needed.

**Scope decisions made for this phase** (each deliberate, not oversights):
- **`kind: "local"` only.** Same restriction as the existing `POST /api/jobs/preview-file-mapping` (Phase 6). S3/SFTP ad-hoc support needs a real answer to "where do preview/ad-hoc-time credentials come from without a saved job's `config_snapshot`?" — that's the *other* deferred item from Phase 6, explicitly not picked this round. The ad-hoc form has no kind selector at all (hardcoded to `local`), simpler than the job editor's form.
- **No `readiness` polling** in the ad-hoc form. Readiness exists for scheduled/repeated live-spool jobs; a one-off ad-hoc comparison has no reason to wait for files that don't exist yet. (The backend `FileMappingSpec` still accepts a `readiness` block generically if a hand-crafted request includes one — this phase just doesn't expose it in the UI.)
- **Pairs run sequentially**, not through `RunExecutor`'s parallel `TestRunner`-based execution. Every existing `CompareService` method (`run_bo_comparison`, `run_sql_comparison`, `run_recon_file_compare`) is single-pair and sequential; there's no precedent for parallelism in this file, and ad-hoc comparisons are typically small (a handful of files), so simplicity wins over throughput here.
- **No lineage manifest write.** `FileMappingManifestWriter` keys its output by `run_id` *and* `job_name` — there is no `job_name` for an ad-hoc run, and there's no established need for one (the run's `TestResult.mismatch_summary` already carries the same per-pair information the manifest would).
- **Templates are not extended.** Research (this plan's author, not assumed) found the existing "Save/Load Template" feature only ever captures/restores BO-tab fields regardless of which sub-tab is active (`app.js`'s `saveCompareTemplate`/`loadCompareTemplate`) — a pre-existing gap affecting `sql`/`recon`/`colstats`/`mmdiff` today, not something this phase should silently try to fix by adding yet another sub-tab's fields to an already-broken generic mechanism.

**Tech Stack:** FastAPI, SQLAlchemy, Alpine.js (existing patterns, no build step for JS — HTML partial changes need `npm run build:html`), Playwright.

**Spec coverage in this phase:**
1. Ad-hoc multi_file comparison, run and persisted the same way `bo`/`sql`/`recon-file` are.
2. Preview-before-running, reusing the existing (Phase 6) `POST /api/jobs/preview-file-mapping` endpoint as-is — no backend changes needed for preview itself.
3. Result view with the per-pair breakdown (status/error/row counts/mismatch counts per pair, plus unmatched groups) — new frontend markup, reusing existing wire fields.
4. Playwright e2e coverage.

---

### Task 1: `MultiFileCompareRequest` schema

**Files:**
- Modify: `api/schemas.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_multi_file_compare_request.py`:

```python
# tests/unit/test_multi_file_compare_request.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import MultiFileCompareRequest


def test_multi_file_compare_request_requires_file_mapping() -> None:
    with pytest.raises(ValidationError):
        MultiFileCompareRequest()


def test_multi_file_compare_request_accepts_minimal_config() -> None:
    req = MultiFileCompareRequest(file_mapping={
        "match_on": ["region"],
        "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv"},
        "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
    })
    assert req.label_a == "Source A"
    assert req.label_b == "Source B"
    assert req.key_columns is None
    assert req.exclude_columns == []
    assert req.file_mapping["match_on"] == ["region"]
    assert req.advanced.float_tolerance == 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_multi_file_compare_request.py -v`
Expected: FAIL with `ImportError: cannot import name 'MultiFileCompareRequest'`

- [ ] **Step 3: Write minimal implementation**

In `api/schemas.py`, find `ReconFileCompareRequest`'s `validate_sources` method (it ends with `return self` right before `class SQLCompareRequest(BaseModel):`):

```python
    @model_validator(mode="after")
    def validate_sources(self) -> "ReconFileCompareRequest":
        sources_a = [self.stored_run_id, self.file_a_path, self.file_a_content_b64]
        sources_b = [self.stored_run_id_b, self.file_b_path, self.file_b_content_b64]
        if sum(bool(value) for value in sources_a) != 1:
            raise ValueError("Source A requires exactly one stored run, file path, or upload")
        if sum(bool(value) for value in sources_b) != 1:
            raise ValueError("Source B requires exactly one stored run, file path, or upload")
        return self


class SQLCompareRequest(BaseModel):
```

Insert a new class between them:

```python
    @model_validator(mode="after")
    def validate_sources(self) -> "ReconFileCompareRequest":
        sources_a = [self.stored_run_id, self.file_a_path, self.file_a_content_b64]
        sources_b = [self.stored_run_id_b, self.file_b_path, self.file_b_content_b64]
        if sum(bool(value) for value in sources_a) != 1:
            raise ValueError("Source A requires exactly one stored run, file path, or upload")
        if sum(bool(value) for value in sources_b) != 1:
            raise ValueError("Source B requires exactly one stored run, file path, or upload")
        return self


class MultiFileCompareRequest(BaseModel):
    """Ad-hoc (no saved job) multi-file reconciliation, run once from the
    Compare tab. ``file_mapping`` is the same config shape a saved
    ``multi_file`` job's ``params.file_mapping`` uses (see
    ``etl_framework.reconciliation.file_mapping.FileMappingSpec.from_params``),
    but this phase only supports ``kind: "local"`` on both sides -- see the
    Phase 7 plan doc for why.
    """
    label_a: str = "Source A"
    label_b: str = "Source B"
    key_columns: list[str] | None = None
    exclude_columns: list[str] = Field(default_factory=list)
    file_mapping: dict[str, Any] = Field(...)
    advanced: AdvancedCompareOptions = Field(default_factory=AdvancedCompareOptions)


class SQLCompareRequest(BaseModel):
```

Check the top of `api/schemas.py` for an existing `from typing import Any` (or similar) import -- `Any` is almost certainly already imported (used elsewhere in this large schema file); if not, add it.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_multi_file_compare_request.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_multi_file_compare_request.py
git commit -m "feat(schemas): add MultiFileCompareRequest for ad-hoc multi-file compare"
```

---

### Task 2: `CompareService.run_multi_file_compare`

**Files:**
- Modify: `api/services/compare_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_compare_service_multi_file.py`:

```python
# tests/unit/test_compare_service_multi_file.py
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import MultiFileCompareRequest
from etl_framework.repository.database import Base
from etl_framework.repository.repository import ConfigRepository, RunRepository
from etl_framework.runner.state import TestStatus


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_run_multi_file_compare_persists_aggregate_result(tmp_path, monkeypatch) -> None:
    from api.services import file_source
    from api.services.compare_service import CompareService

    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_east.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (source_dir / "sales_west.csv").write_text("id,value\n2,beta\n", encoding="utf-8")
    (target_dir / "financials_east.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (target_dir / "financials_west.csv").write_text("id,value\n2,BETA\n", encoding="utf-8")

    db = _make_db()
    try:
        run_id = "test-run-mf-compare"
        RunRepository(db).create_run(run_id=run_id, source_env="Source A", target_env="Source B", run_type="multi_file")

        req = MultiFileCompareRequest(
            key_columns=["id"],
            file_mapping={
                "strategy": "explicit",
                "match_on": ["region"],
                "source": {"kind": "local", "root": str(source_dir), "pattern": "sales_{region}.csv"},
                "target": {"kind": "local", "root": str(target_dir), "pattern": "financials_{region}.csv"},
            },
        )
        svc = CompareService(db, ConfigRepository(db))
        svc.run_multi_file_compare(req, run_id)

        run = RunRepository(db).get_run(run_id)
        assert run.status == "FAILED"  # region=west mismatches
        assert len(run.results) == 1
        result = run.results[0]
        assert result.mismatch_summary["pairs_total"] == 2
        assert result.mismatch_summary["pairs_passed"] == 1
        by_region = {p["key"]["region"]: p for p in result.mismatch_summary["file_pairs"]}
        assert by_region["east"]["status"] == "PASSED"
        assert by_region["west"]["status"] == "FAILED"
    finally:
        db.close()


def test_run_multi_file_compare_ignore_policy_proceeds_with_unmatched(tmp_path, monkeypatch) -> None:
    """Regression test: an earlier draft of run_multi_file_compare had
    `if mapping.unmatched_sources or mapping.unmatched_targets and spec.unmatched_policy == "fail":`
    -- Python's `and` binds tighter than `or`, so that raised on ANY unmatched
    source regardless of policy, meaning `unmatched_policy: "ignore"` was
    silently never honored whenever a source was unmatched. This test only
    passes if that condition is correctly parenthesized as two separate checks.
    """
    from api.services import file_source
    from api.services.compare_service import CompareService

    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_east.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (source_dir / "sales_north.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")  # no target match
    (target_dir / "financials_east.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")

    db = _make_db()
    try:
        run_id = "test-run-mf-compare-ignore"
        RunRepository(db).create_run(run_id=run_id, source_env="Source A", target_env="Source B", run_type="multi_file")

        req = MultiFileCompareRequest(
            key_columns=["id"],
            file_mapping={
                "strategy": "explicit",
                "match_on": ["region"],
                "source": {"kind": "local", "root": str(source_dir), "pattern": "sales_{region}.csv"},
                "target": {"kind": "local", "root": str(target_dir), "pattern": "financials_{region}.csv"},
                "unmatched_policy": "ignore",
            },
        )
        svc = CompareService(db, ConfigRepository(db))
        svc.run_multi_file_compare(req, run_id)

        run = RunRepository(db).get_run(run_id)
        assert run.status == "PASSED"  # must NOT be ERROR -- ignore policy must be honored
        result = run.results[0]
        assert result.mismatch_summary["pairs_total"] == 1
        assert len(result.mismatch_summary["unmatched_sources"]) == 1
        assert result.mismatch_summary["unmatched_sources"][0]["key"] == {"region": "north"}
    finally:
        db.close()


def test_run_multi_file_compare_rejects_remote_kinds(tmp_path) -> None:
    from api.services.compare_service import CompareService

    db = _make_db()
    try:
        run_id = "test-run-mf-compare-s3"
        RunRepository(db).create_run(run_id=run_id, source_env="Source A", target_env="Source B", run_type="multi_file")

        req = MultiFileCompareRequest(file_mapping={
            "match_on": ["region"],
            "source": {"kind": "s3", "root": "s3://bucket/prefix", "pattern": "sales_{region}.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
        })
        svc = CompareService(db, ConfigRepository(db))
        svc.run_multi_file_compare(req, run_id)

        run = RunRepository(db).get_run(run_id)
        assert run.status == "ERROR"
        assert len(run.results) == 1
        assert "local" in (run.results[0].error_message or "").lower()
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_compare_service_multi_file.py -v`
Expected: FAIL with `AttributeError: 'CompareService' object has no attribute 'run_multi_file_compare'`

- [ ] **Step 3: Write minimal implementation**

In `api/services/compare_service.py`, find `run_recon_file_compare` (it ends right before the comment block `# ------------------------------------------------------------------` that precedes whatever section follows it -- read the file to find that exact spot, since the plan's earlier research only confirmed the method starts at line 411, not exactly where it ends). Add a new method to the `CompareService` class right after `run_recon_file_compare` ends:

```python
    # ------------------------------------------------------------------
    # Multi-file reconciliation (ad-hoc)
    # ------------------------------------------------------------------

    def run_multi_file_compare(self, req: MultiFileCompareRequest, run_id: str) -> None:
        """Ad-hoc multi-file reconciliation: discover, pair, reconcile every
        pair sequentially, then persist ONE aggregate TestResult -- the same
        result shape RunExecutor's saved-job multi_file path already
        produces, so the Reports-tab rendering (Phase 4) works unchanged.
        """
        from etl_framework.reconciliation.compare_utils import resolve_key_columns
        from etl_framework.reconciliation.file_mapping import (
            FileMappingSpec,
            aggregate_reconciliation_results,
            pair_files,
            pair_files_automated,
        )
        from api.services.multi_file_remote import RemoteFileSourceSession

        try:
            self._repo.update_run_status(run_id, "RUNNING", started_at=datetime.now(timezone.utc))

            spec = FileMappingSpec.from_params({"file_mapping": req.file_mapping})
            if spec.source.kind != "local" or spec.target.kind != "local":
                raise ValueError(
                    "Ad-hoc multi-file compare only supports 'local' source/target kinds; "
                    "save a job instead for s3/sftp sources."
                )

            with RemoteFileSourceSession({}) as session:
                source_files = session.discover(spec.source)
                target_files = session.discover(spec.target)

                if spec.strategy == "automated":
                    source_frames = {f.path: session.read_file(f, spec.source) for f in source_files}
                    target_frames = {f.path: session.read_file(f, spec.target) for f in target_files}
                    mapping, _ = pair_files_automated(
                        source_files, source_frames, target_files, target_frames, spec.automated,
                    )
                else:
                    mapping = pair_files(source_files, target_files, spec.match_on)

                if mapping.unmatched_sources or mapping.unmatched_targets:
                    # NOTE: parenthesize the OR before the AND -- `a or b and c`
                    # evaluates as `a or (b and c)` in Python, which would raise
                    # on ANY unmatched source regardless of policy. Keep this as
                    # two separate ifs (as below), not one combined expression.
                    if spec.unmatched_policy == "fail":
                        raise ValueError(
                            f"multi-file compare has {len(mapping.unmatched_sources)} unmatched source "
                            f"group(s) and {len(mapping.unmatched_targets)} unmatched target group(s)"
                        )
                    if spec.unmatched_policy == "warn":
                        logger.warning(
                            "multi-file compare for run '%s' proceeding with %d unmatched source "
                            "group(s) and %d unmatched target group(s)",
                            run_id, len(mapping.unmatched_sources), len(mapping.unmatched_targets),
                        )
                if not mapping.pairs:
                    raise ValueError("multi-file compare matched zero file pairs")

                pair_results = []
                for pair in mapping.pairs:
                    source_df = pd.concat(
                        [session.read_file(f, spec.source) for f in pair.source.files], ignore_index=True,
                    )
                    target_df = pd.concat(
                        [session.read_file(f, spec.target) for f in pair.target.files], ignore_index=True,
                    )
                    source_df, target_df, resolved_keys = resolve_key_columns(
                        source_df, target_df, req.key_columns or [], req.exclude_columns or [],
                    )
                    engine_a = FrameEngine(source_df, req.label_a)
                    engine_b = FrameEngine(target_df, req.label_b)
                    reconciler = _build_engine(
                        engine_a, engine_b,
                        key_columns=resolved_keys,
                        exclude_columns=req.exclude_columns or [],
                        mismatch_row_limit=_compare_mismatch_row_limit(req.advanced),
                        adv=req.advanced,
                    )
                    pair_results.append(reconciler.reconcile(_SENTINEL_QUERY, req.label_a or "multi_file_compare"))

            result = aggregate_reconciliation_results(req.label_a or "multi_file_compare", mapping, pair_results)
            tr = self._repo.add_test_result(run_id, result)
            if result.mismatches:
                self._repo.add_mismatch_details(tr.id, result.mismatches)
            MetricsWriter(f"logs/metrics_{run_id}.json").write(run_id, [result])
            passed = 1 if result.status == TestStatus.PASSED else 0
            failed = 0 if passed else 1
            self._repo.update_run_status(
                run_id, "PASSED" if passed else "FAILED",
                completed_at=datetime.now(timezone.utc),
                total_tests=1, passed=passed, failed=failed,
            )
        except Exception as exc:
            logger.exception("Multi-file comparison failed for run %s", run_id)
            self._add_error_result(run_id, req.label_a or "multi_file_compare", exc)
            self._repo.update_run_status(
                run_id, "ERROR",
                completed_at=datetime.now(timezone.utc),
                total_tests=1, error=1,
            )
```

Add `MultiFileCompareRequest` to the existing `from api.schemas import (...)` block at the top of the file:

```python
from api.schemas import (
    BOCompareRequest, ReconFileCompareRequest, SQLCompareRequest,
    ColumnStatsRequest, ColumnStatsOut, ColumnStatsDiffOut,
    MismatchDiffRequest, MismatchDiffOut, MismatchRecordOut,
    AdvancedCompareOptions, MultiFileCompareRequest,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_compare_service_multi_file.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the broader compare-service suite to confirm no regression**

Run: `python -m pytest -k compare_service -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/services/compare_service.py tests/unit/test_compare_service_multi_file.py
git commit -m "feat(compare): add ad-hoc multi-file reconciliation to CompareService"
```

---

### Task 3: `POST /api/compare/multi-file` route

**Files:**
- Modify: `api/routes/compare.py`
- Test: `tests/unit/test_compare_api.py` (verified: this is the file covering `api/routes/compare.py`'s other endpoints; it has its own `client(monkeypatch)` fixture)

- [ ] **Step 1: Write the failing test**

**Verified convention in this file (do not deviate):** every existing route-level test (e.g. `test_sql_compare_unknown_connection_returns_422`, `test_sql_compare_valid_connection_accepted`) monkeypatches the module-level background function to a no-op (`monkeypatch.setattr(compare_module, "_run_sql_bg", lambda *a, **kw: None)`) and only asserts on the ROUTE's own synchronous behavior (response status code, run_id presence, validation errors) -- it does NOT rely on `BackgroundTasks` executing synchronously within `TestClient`, and does NOT assert on the run reaching a terminal status. Full end-to-end execution (discovery → pairing → reconcile → persist) is already covered at the `CompareService` level by Task 2's tests. Follow this exact same convention -- do not write a test that waits for the background task to complete.

APPEND to `tests/unit/test_compare_api.py`:

```python
def test_compare_multi_file_endpoint_creates_run(client, monkeypatch):
    import api.routes.compare as compare_module
    monkeypatch.setattr(compare_module, "_run_multi_file_bg", lambda *a, **kw: None)

    resp = client.post("/api/compare/multi-file", json={
        "key_columns": ["id"],
        "file_mapping": {
            "match_on": ["region"],
            "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
        },
    })
    assert resp.status_code == 202
    body = resp.json()
    assert body["run_id"]


def test_compare_multi_file_endpoint_rejects_remote_kinds_synchronously(client, monkeypatch):
    import api.routes.compare as compare_module
    monkeypatch.setattr(compare_module, "_run_multi_file_bg", lambda *a, **kw: None)

    resp = client.post("/api/compare/multi-file", json={
        "file_mapping": {
            "match_on": ["region"],
            "source": {"kind": "s3", "root": "s3://bucket/prefix", "pattern": "sales_{region}.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
        },
    })
    assert resp.status_code == 400
```

Note the second test still monkeypatches `_run_multi_file_bg` even though it expects a 400 before the background task would ever be queued -- this is just defensive consistency with the file's convention (harmless either way since the route's synchronous validation raises before `background_tasks.add_task(...)` is ever reached), not because it's required for this specific test to pass.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -k compare_multi_file -v`
Expected: FAIL (route doesn't exist -- 404 or 405)

- [ ] **Step 3: Write minimal implementation**

In `api/routes/compare.py`, add `MultiFileCompareRequest` to the existing `from api.schemas import (...)` block:

```python
from api.schemas import (
    BOCompareRequest,
    ColumnStatsOut,
    ColumnStatsRequest,
    DualEnvLaunchOut,
    DualEnvLaunchRequest,
    MismatchDiffOut,
    MismatchDiffRequest,
    MultiFileCompareRequest,
    PairSummaryOut,
    ReconFileCompareRequest,
    RunStatusOut,
    SQLCompareRequest,
)
```

Add a new background function alongside `_run_recon_file_bg` (which ends right before `def _run_sql_bg`):

```python
def _run_recon_file_bg(req: ReconFileCompareRequest, run_id: str) -> None:
    from etl_framework.repository.database import SessionLocal
    from etl_framework.utils.context import set_run_id

    set_run_id(run_id)
    db = SessionLocal()
    try:
        from api.services.compare_service import CompareService
        from etl_framework.repository.repository import ConfigRepository
        svc = CompareService(db, ConfigRepository(db))
        svc.run_recon_file_compare(req, run_id)
    except Exception:
        logger.exception("Recon-file comparison background task failed for run_id=%s", run_id)
    finally:
        set_run_id("")
        db.close()


def _run_multi_file_bg(req: MultiFileCompareRequest, run_id: str) -> None:
    from etl_framework.repository.database import SessionLocal
    from etl_framework.utils.context import set_run_id

    set_run_id(run_id)
    db = SessionLocal()
    try:
        from api.services.compare_service import CompareService
        from etl_framework.repository.repository import ConfigRepository
        svc = CompareService(db, ConfigRepository(db))
        svc.run_multi_file_compare(req, run_id)
    except Exception:
        logger.exception("Multi-file comparison background task failed for run_id=%s", run_id)
    finally:
        set_run_id("")
        db.close()
```

Add the new route right after `compare_recon_file` (which ends with `return _status_out(run)` right before `@router.post("/column-stats", ...)`):

```python
@router.post("/recon-file", response_model=RunStatusOut, status_code=202)
def compare_recon_file(
    body: ReconFileCompareRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
) -> RunStatusOut:
    run_id = str(uuid.uuid4())
    snapshot = sanitize_compare_request(run_id, "recon_file", body.model_dump(mode="json"))
    compare_body = ReconFileCompareRequest(**snapshot["request"])
    repo = RunRepository(db)
    repo.create_run(
        run_id=run_id,
        source_env=body.label_a,
        target_env=body.label_b,
        config_snapshot=snapshot,
        run_type="recon_file",
    )
    AuditService(db).log(
        request,
        "run.created",
        "run",
        run_id,
        {"run_type": "recon_file", "label_a": body.label_a, "label_b": body.label_b},
    )
    background_tasks.add_task(_run_recon_file_bg, compare_body, run_id)
    run = repo.get_run(run_id)
    return _status_out(run)


@router.post("/multi-file", response_model=RunStatusOut, status_code=202)
def compare_multi_file(
    body: MultiFileCompareRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
) -> RunStatusOut:
    from etl_framework.reconciliation.file_mapping import FileMappingSpec

    try:
        spec = FileMappingSpec.from_params({"file_mapping": body.file_mapping})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if spec.source.kind != "local" or spec.target.kind != "local":
        raise HTTPException(
            status_code=400,
            detail="Ad-hoc multi-file compare only supports 'local' source/target kinds.",
        )

    run_id = str(uuid.uuid4())
    snapshot = {
        "compare_request_type": "multi_file",
        "request": body.model_dump(mode="json"),
    }
    repo = RunRepository(db)
    repo.create_run(
        run_id=run_id,
        source_env=body.label_a,
        target_env=body.label_b,
        config_snapshot=snapshot,
        run_type="multi_file",
    )
    AuditService(db).log(
        request,
        "run.created",
        "run",
        run_id,
        {"run_type": "multi_file", "label_a": body.label_a, "label_b": body.label_b},
    )
    background_tasks.add_task(_run_multi_file_bg, body, run_id)
    run = repo.get_run(run_id)
    return _status_out(run)


@router.post("/column-stats", response_model=ColumnStatsOut)
```

Validating synchronously in the route (before creating any DB row) mirrors `_check_sql_connection_names(body, db)`'s placement in `compare_sql` -- immediate 400 feedback instead of a wasted `TestRun` row that would just immediately error in the background task.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -k compare_multi_file -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the broader compare-route suite to confirm no regression**

Run: `python -m pytest tests/unit/test_compare_api.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/routes/compare.py tests/unit/test_compare_api.py
git commit -m "feat(compare): add POST /api/compare/multi-file route"
```

---

### Task 4: Compare tab — sub-tab button and config form

**Files:**
- Modify: `frontend/partials/tab-compare.html`

- [ ] **Step 1: Add the sub-tab button**

Find (the sub-tab button row):

```html
  <div class="compare-subtabs mb-4">
    <button data-testid="compare-subtab-bo" @click="compareSubTab = 'bo'" :class="compareSubTab === 'bo' ? 'sub-tab active' : 'sub-tab'">BO Report</button>
    <button data-testid="compare-subtab-recon" @click="compareSubTab = 'recon'" :class="compareSubTab === 'recon' ? 'sub-tab active' : 'sub-tab'">Reconciliation</button>
    <button data-testid="compare-subtab-sql" @click="compareSubTab = 'sql'" :class="compareSubTab === 'sql' ? 'sub-tab active' : 'sub-tab'">SQL</button>
    <button data-testid="compare-subtab-colstats" @click="compareSubTab = 'colstats'" :class="compareSubTab === 'colstats' ? 'sub-tab active' : 'sub-tab'">Column Stats</button>
    <button data-testid="compare-subtab-mmdiff" @click="compareSubTab = 'mmdiff'" :class="compareSubTab === 'mmdiff' ? 'sub-tab active' : 'sub-tab'">Mismatch Diff</button>
  </div>
```

Change to add a 6th button:

```html
  <div class="compare-subtabs mb-4">
    <button data-testid="compare-subtab-bo" @click="compareSubTab = 'bo'" :class="compareSubTab === 'bo' ? 'sub-tab active' : 'sub-tab'">BO Report</button>
    <button data-testid="compare-subtab-recon" @click="compareSubTab = 'recon'" :class="compareSubTab === 'recon' ? 'sub-tab active' : 'sub-tab'">Reconciliation</button>
    <button data-testid="compare-subtab-sql" @click="compareSubTab = 'sql'" :class="compareSubTab === 'sql' ? 'sub-tab active' : 'sub-tab'">SQL</button>
    <button data-testid="compare-subtab-colstats" @click="compareSubTab = 'colstats'" :class="compareSubTab === 'colstats' ? 'sub-tab active' : 'sub-tab'">Column Stats</button>
    <button data-testid="compare-subtab-mmdiff" @click="compareSubTab = 'mmdiff'" :class="compareSubTab === 'mmdiff' ? 'sub-tab active' : 'sub-tab'">Mismatch Diff</button>
    <button data-testid="compare-subtab-multifile" @click="compareSubTab = 'multi_file'" :class="compareSubTab === 'multi_file' ? 'sub-tab active' : 'sub-tab'">Multi-File</button>
  </div>
```

Read the file first to confirm this matches exactly (line ~9-15).

- [ ] **Step 2: Add the sub-tab content block**

Find the very end of the file (it currently ends with the `mmdiff` block's closing, then two closing `</div>` tags for the outer view wrapper):

```html
            <div x-show="mismatchDiffResult.persistent.length > mismatchDiffVisible.persistent" class="mt-2">
              <button data-testid="compare-mmdiff-loadmore-persistent-btn" @click="showMoreMismatchDiff('persistent')" class="btn-secondary btn-sm text-xs">Load more</button>
              <span class="text-xs text-slate-400 ml-2" x-text="'Showing ' + mismatchDiffVisible.persistent + ' of ' + mismatchDiffResult.persistent.length"></span>
            </div>
          </div>
        </template>
      </div>
    </template>
  </div>

</div>
```

Insert the new sub-tab block right after the `mmdiff` block's closing `</div>` and before the final `</div>`:

```html
            <div x-show="mismatchDiffResult.persistent.length > mismatchDiffVisible.persistent" class="mt-2">
              <button data-testid="compare-mmdiff-loadmore-persistent-btn" @click="showMoreMismatchDiff('persistent')" class="btn-secondary btn-sm text-xs">Load more</button>
              <span class="text-xs text-slate-400 ml-2" x-text="'Showing ' + mismatchDiffVisible.persistent + ' of ' + mismatchDiffResult.persistent.length"></span>
            </div>
          </div>
        </template>
      </div>
    </template>
  </div>

  <div x-show="compareSubTab === 'multi_file'" class="space-y-4">
    <div class="grid-2">
      <div>
        <label class="field-label">Label A</label>
        <input x-model="mfCompareLabelA" class="field-input" placeholder="Source A" />
      </div>
      <div>
        <label class="field-label">Label B</label>
        <input x-model="mfCompareLabelB" class="field-input" placeholder="Source B" />
      </div>
    </div>
    <div class="grid-2">
      <div>
        <label class="field-label">Strategy</label>
        <select x-model="mfCompareStrategy" class="field-input field-select" data-testid="compare-mf-strategy-select">
          <option value="explicit">Explicit (match on tokens)</option>
          <option value="automated">Automated (guess by similarity)</option>
        </select>
      </div>
      <div>
        <label class="field-label">Unmatched Policy</label>
        <select x-model="mfCompareUnmatchedPolicy" class="field-input field-select">
          <option value="fail">Fail</option>
          <option value="warn">Warn and proceed</option>
          <option value="ignore">Ignore silently</option>
        </select>
      </div>
    </div>
    <div x-show="mfCompareStrategy === 'explicit'">
      <label class="field-label">Match On (comma-separated tokens)</label>
      <input x-model="mfCompareMatchOnRaw" class="field-input" placeholder="region, date"
             data-testid="compare-mf-match-on-input" />
    </div>
    <div x-show="mfCompareStrategy === 'automated'" class="grid-2">
      <div>
        <label class="field-label">Similarity Threshold</label>
        <input x-model="mfCompareSimilarityThreshold" type="number" min="0" max="1" step="0.05" class="field-input" placeholder="0.7" />
      </div>
      <div class="flex items-end gap-3">
        <label class="flex items-center gap-1 text-xs">
          <input type="checkbox" x-model="mfCompareSignalFilename" class="rounded" /> filename
        </label>
        <label class="flex items-center gap-1 text-xs">
          <input type="checkbox" x-model="mfCompareSignalColumns" class="rounded" /> columns
        </label>
        <label class="flex items-center gap-1 text-xs">
          <input type="checkbox" x-model="mfCompareSignalRowcount" class="rounded" /> row count
        </label>
      </div>
    </div>
    <div class="grid-2">
      <div>
        <label class="field-label">Key Columns (comma-separated)</label>
        <input x-model="mfCompareKeyColumns" class="field-input" placeholder="id" data-testid="compare-mf-key-columns-input" />
      </div>
      <div>
        <label class="field-label">Exclude Columns (comma-separated)</label>
        <input x-model="mfCompareExcludeColumns" class="field-input" placeholder="note" />
      </div>
    </div>
    <div class="border-t border-slate-200 pt-3">
      <p class="text-xs font-medium text-slate-500 mb-2">Source (local only)</p>
      <div class="grid-2">
        <input x-model="mfCompareSourceRoot" class="field-input" placeholder="/spool/exports"
               data-testid="compare-mf-source-root-input" />
        <input x-model="mfCompareSourcePattern" class="field-input" placeholder="sales_{region}_{date:%Y%m%d}.csv"
               data-testid="compare-mf-source-pattern-input" />
      </div>
    </div>
    <div class="border-t border-slate-200 pt-3">
      <p class="text-xs font-medium text-slate-500 mb-2">Target (local only)</p>
      <div class="grid-2">
        <input x-model="mfCompareTargetRoot" class="field-input" placeholder="/exports/finance"
               data-testid="compare-mf-target-root-input" />
        <input x-model="mfCompareTargetPattern" class="field-input" placeholder="financials_{region}_{date:%Y%m%d}.dat"
               data-testid="compare-mf-target-pattern-input" />
      </div>
    </div>
    <div class="flex items-center gap-2">
      <button @click="previewMfCompareMapping()"
              :disabled="mfComparePreviewLoading"
              class="btn-secondary btn-sm text-xs px-3 py-1 disabled:opacity-40"
              data-testid="compare-mf-preview-btn">
        <span x-show="!mfComparePreviewLoading">Preview Mapping</span>
        <span x-show="mfComparePreviewLoading">Loading…</span>
      </button>
      <button @click="runMultiFileCompare()"
              :disabled="mfCompareLoading"
              class="btn-primary btn-sm text-xs px-3 py-1 disabled:opacity-40"
              data-testid="compare-mf-run-btn">
        <span x-show="!mfCompareLoading">Run Comparison</span>
        <span x-show="mfCompareLoading">Running…</span>
      </button>
    </div>
    <p x-show="mfComparePreviewError" x-text="mfComparePreviewError" class="text-xs text-red-600"></p>
    <div x-show="mfComparePreviewResult" class="text-xs space-y-1" data-testid="compare-mf-preview-result">
      <p x-text="`${mfComparePreviewResult?.pairs_total ?? 0} pair(s) matched`"></p>
      <template x-for="(pair, idx) in (mfComparePreviewResult?.pairs || [])" :key="idx">
        <div class="border border-slate-200 rounded p-2" data-testid="compare-mf-preview-pair">
          <span x-text="Object.entries(pair.key || {}).map(([k,v]) => `${k}=${v}`).join(', ')"></span>
          — <span x-text="(pair.source_files || []).join(', ')"></span>
          → <span x-text="(pair.target_files || []).join(', ')"></span>
        </div>
      </template>
    </div>

    <div x-show="mfCompareError" class="text-sm text-red-600" x-text="mfCompareError"></div>
    <div x-show="mfCompareResult" data-testid="compare-mf-results" class="border border-slate-200 rounded-lg p-4 space-y-3">
      <div class="flex items-center gap-3">
        <span class="font-medium">Results</span>
        <span class="chip" :class="mfCompareResult?.status === 'PASSED' ? 'chip-success' : 'chip-danger'"
              x-text="mfCompareResult?.status"></span>
      </div>
      <template x-if="mfCompareResult?.results?.[0]">
        <div class="text-xs space-y-2">
          <p x-text="`${mfCompareResult.results[0].mismatch_summary?.pairs_passed ?? 0} of ${mfCompareResult.results[0].mismatch_summary?.pairs_total ?? 0} pair(s) passed`"></p>
          <template x-for="(pair, idx) in (mfCompareResult.results[0].file_pairs || [])" :key="idx">
            <div class="border border-slate-200 rounded p-2" data-testid="compare-mf-result-pair" :data-status="pair.status">
              <div><strong x-text="pair.status"></strong> <span x-text="Object.entries(pair.key || {}).map(([k,v]) => `${k}=${v}`).join(', ')"></span></div>
              <div>Source: <span x-text="(pair.source_files || []).join(', ')"></span></div>
              <div>Target: <span x-text="(pair.target_files || []).join(', ')"></span></div>
              <div>Rows: <span x-text="pair.source_row_count ?? 0"></span> / <span x-text="pair.target_row_count ?? 0"></span>; mismatches: <span x-text="pair.value_mismatch_count ?? 0"></span></div>
              <div x-show="pair.error" class="text-red-600" x-text="'Error: ' + pair.error"></div>
            </div>
          </template>
          <div x-show="(mfCompareResult.results[0].unmatched_sources || []).length" class="text-amber-600"
               x-text="'Unmatched sources: ' + mfCompareResult.results[0].unmatched_sources.map(g => (g.files||[]).join(', ')).join('; ')"></div>
          <div x-show="(mfCompareResult.results[0].unmatched_targets || []).length" class="text-amber-600"
               x-text="'Unmatched targets: ' + mfCompareResult.results[0].unmatched_targets.map(g => (g.files||[]).join(', ')).join('; ')"></div>
        </div>
      </template>
    </div>
  </div>

</div>
```

- [ ] **Step 3: Rebuild `index.html` -- do this now, not as an afterthought**

Run: `npm run build:html`
Then run: `git diff --stat frontend/index.html` and confirm it shows a change (if it shows nothing, the include markers didn't match -- re-check Step 1/2's edits landed in `frontend/partials/tab-compare.html`, not some other file).

(This exact mistake -- editing a partial and forgetting to rebuild `index.html`, the file the browser actually serves -- was made and caught during Phase 6's Task 2. Do not repeat it. `npm run build:html` only assembles `frontend/index.template.html` + `frontend/partials/*.html`; it does not touch JS feature files.)

- [ ] **Step 4: Smoke-check existing Compare specs still pass**

Run: `npx playwright test tests/e2e/08b-compare-reconciliation.spec.ts tests/e2e/08f-compare-templates.spec.ts --reporter=list`
Expected: all still PASS (confirms this HTML addition doesn't break the existing sub-tabs' Alpine bindings)

- [ ] **Step 5: Commit**

```bash
git add frontend/partials/tab-compare.html frontend/index.html
git commit -m "feat(compare): add multi_file sub-tab config form and result view"
```

---

### Task 5: Compare tab — state and methods

**Files:**
- Modify: `frontend/features/compare.js`

- [ ] **Step 1: Add state fields**

Read the file first to find its state object (near the top, alongside `fileSourceAType`/`fileLabelA` etc. per the researched line ranges ~50-106). Add a new block of state fields near the existing `file*` state (exact insertion point: read the file and pick a spot after the last `file*`-prefixed or `sql*`-prefixed state field, before the methods section begins):

```js
    mfCompareLabelA: 'Source A',
    mfCompareLabelB: 'Source B',
    mfCompareStrategy: 'explicit',
    mfCompareMatchOnRaw: '',
    mfCompareUnmatchedPolicy: 'fail',
    mfCompareSimilarityThreshold: 0.7,
    mfCompareSignalFilename: true,
    mfCompareSignalColumns: true,
    mfCompareSignalRowcount: true,
    mfCompareSourceRoot: '',
    mfCompareSourcePattern: '',
    mfCompareTargetRoot: '',
    mfCompareTargetPattern: '',
    mfCompareKeyColumns: '',
    mfCompareExcludeColumns: '',
    mfComparePreviewLoading: false,
    mfComparePreviewResult: null,
    mfComparePreviewError: '',
    mfCompareLoading: false,
    mfCompareResult: null,
    mfCompareError: '',
```

- [ ] **Step 2: Add `_buildMfCompareFileMapping()`, `previewMfCompareMapping()`, and `runMultiFileCompare()` methods**

Find `runFileCompare()` in full (it ends with the closing `},` right after its `catch` block's `this.fileCompareLoading = false;`). Insert three new methods right after it:

```js
    _buildMfCompareFileMapping() {
      const match_on = this.mfCompareMatchOnRaw.split(',').map(s => s.trim()).filter(Boolean);
      const config = {
        strategy: this.mfCompareStrategy,
        unmatched_policy: this.mfCompareUnmatchedPolicy,
        source: { kind: 'local', root: this.mfCompareSourceRoot, pattern: this.mfCompareSourcePattern },
        target: { kind: 'local', root: this.mfCompareTargetRoot, pattern: this.mfCompareTargetPattern },
      };
      if (this.mfCompareStrategy === 'explicit') config.match_on = match_on;
      if (this.mfCompareStrategy === 'automated') {
        const signals = [];
        if (this.mfCompareSignalFilename) signals.push('filename_tokens');
        if (this.mfCompareSignalColumns) signals.push('column_signature');
        if (this.mfCompareSignalRowcount) signals.push('row_count_ratio');
        const parsedThreshold = Number(this.mfCompareSimilarityThreshold);
        config.automated_mapping = {
          similarity_threshold: Number.isFinite(parsedThreshold) && this.mfCompareSimilarityThreshold !== '' ? parsedThreshold : 0.7,
          signals,
        };
      }
      return config;
    },

    async previewMfCompareMapping() {
      this.mfComparePreviewLoading = true;
      this.mfComparePreviewResult = null;
      this.mfComparePreviewError = '';
      try {
        this.mfComparePreviewResult = await api('POST', '/api/jobs/preview-file-mapping', {
          file_mapping: this._buildMfCompareFileMapping(),
        });
      } catch (e) {
        this.mfComparePreviewError = e.message || 'Preview failed';
      } finally {
        this.mfComparePreviewLoading = false;
      }
    },

    async runMultiFileCompare() {
      this.mfCompareLoading = true;
      this.mfCompareResult = null;
      this.mfCompareError = '';
      try {
        const payload = {
          label_a: this.mfCompareLabelA || 'Source A',
          label_b: this.mfCompareLabelB || 'Source B',
          file_mapping: this._buildMfCompareFileMapping(),
        };
        if (this.mfCompareKeyColumns.trim()) {
          payload.key_columns = this.mfCompareKeyColumns.split(',').map(s => s.trim()).filter(Boolean);
        }
        if (this.mfCompareExcludeColumns.trim()) {
          payload.exclude_columns = this.mfCompareExcludeColumns.split(',').map(s => s.trim()).filter(Boolean);
        }
        const run = await api('POST', '/api/compare/multi-file', payload);
        const poll = setInterval(async () => {
          try {
            const st = await api('GET', `/api/runs/${run.run_id}/status`);
            if (this.isTerminalStatus(st.status)) {
              clearInterval(poll);
              this.mfCompareResult = await api('GET', `/api/runs/${run.run_id}`);
              this.mfCompareLoading = false;
              await this.loadRuns();
            }
          } catch (e) {
            clearInterval(poll);
            this.mfCompareLoading = false;
          }
        }, 3000);
      } catch (e) {
        this.mfCompareError = e.message || 'Multi-file compare failed';
        this.toast('error', 'Multi-file compare failed', e.message);
        this.mfCompareLoading = false;
      }
    },
```

`api`, `this.isTerminalStatus`, `this.loadRuns`, and `this.toast` are all already used identically by `runFileCompare()` immediately above -- confirm they're in scope the same way (this file is part of the same Alpine component as `runFileCompare`, so no new imports are needed).

- [ ] **Step 3: Run the smoke specs again**

Run: `npx playwright test tests/e2e/08b-compare-reconciliation.spec.ts --reporter=list`
Expected: all PASS (no build step needed for this task -- only JS was touched)

- [ ] **Step 4: Commit**

```bash
git add frontend/features/compare.js
git commit -m "feat(compare): wire multi_file preview and run methods"
```

---

### Task 6: Playwright e2e coverage

**Files:**
- Create: `tests/e2e/08g-compare-multi-file.spec.ts`

- [ ] **Step 1: Write the tests**

```ts
// tests/e2e/08g-compare-multi-file.spec.ts
import { test, expect } from './fixtures';
import path from 'node:path';

// Mirrors 17-multi-file-reconciliation.spec.ts's FIXTURE_DIR construction --
// resolve_allowed_path() (api/services/file_source.py) resolves a relative
// root against its allowed base dir itself, not the server's cwd, so an
// absolute path built the same way as the job-editor e2e test is required.
const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');

async function openMultiFile(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-multifile"]').click();
}

test.describe('08g compare / multi-file', () => {
  test('previews and runs an ad-hoc multi-file comparison, showing the per-pair breakdown', async ({ authedPage }) => {
    await openMultiFile(authedPage);

    await authedPage.locator('[data-testid="compare-mf-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="compare-mf-match-on-input"]').fill('region');
    await authedPage.locator('[data-testid="compare-mf-source-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_source'));
    await authedPage.locator('[data-testid="compare-mf-source-pattern-input"]').fill('sales_{region}.csv');
    await authedPage.locator('[data-testid="compare-mf-target-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_target'));
    await authedPage.locator('[data-testid="compare-mf-target-pattern-input"]').fill('financials_{region}.csv');

    await authedPage.locator('[data-testid="compare-mf-preview-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-mf-preview-result"]')).toContainText('2 pair(s) matched');
    await expect(authedPage.locator('[data-testid="compare-mf-preview-pair"]')).toHaveCount(2);

    await authedPage.locator('[data-testid="compare-mf-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-mf-results"]')).toBeVisible({ timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="compare-mf-results"]')).toContainText('FAILED');

    const resultPairs = authedPage.locator('[data-testid="compare-mf-result-pair"]');
    await expect(resultPairs).toHaveCount(2);
    await expect(authedPage.locator('[data-testid="compare-mf-result-pair"][data-status="PASSED"]')).toContainText('region=east');
    await expect(authedPage.locator('[data-testid="compare-mf-result-pair"][data-status="FAILED"]')).toContainText('region=west');
  });

  test('negative: running with no source root shows an error toast', async ({ authedPage }) => {
    await openMultiFile(authedPage);
    await authedPage.locator('[data-testid="compare-mf-match-on-input"]').fill('region');
    await authedPage.locator('[data-testid="compare-mf-target-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_target'));
    await authedPage.locator('[data-testid="compare-mf-target-pattern-input"]').fill('financials_{region}.csv');
    // source root/pattern left empty
    await authedPage.locator('[data-testid="compare-mf-run-btn"]').click();
    await expect(authedPage.locator('.toast-title')).toContainText('Multi-file compare failed');
  });
});
```

- [ ] **Step 2: Run the new tests**

Run: `npx playwright test tests/e2e/08g-compare-multi-file.spec.ts --reporter=list`
Expected: all PASS (2 tests)

If the negative test doesn't produce the expected error, check what the backend actually returns for an empty `root`/`pattern` (likely a 422 from Pydantic validation on `FileSourceSpec` construction, or a `ValueError` from `_parse_file_source`'s `"requires both 'root' and 'pattern'"` check) and adjust the expected toast text to match reality rather than guessing twice.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/08g-compare-multi-file.spec.ts
git commit -m "test(e2e): cover ad-hoc multi-file compare in the Compare tab"
```

---

### Task 7: Documentation

**Files:**
- Modify: `docs/multi_file_reconciliation.md`

- [ ] **Step 1: Add a section**

Find the "Current limitations (Phase 6)" section header and add a new section right before it (after whatever section precedes it) documenting the new ad-hoc Compare tab flow: `POST /api/compare/multi-file`, local-only, sequential pair execution, reuses the same `preview-file-mapping` endpoint, result viewable directly in the Compare tab without saving a job. Update the limitations section heading to "(Phase 7)" and add a bullet noting ad-hoc compare is local-only and sequential (not parallel like saved jobs), and that Compare-tab templates don't capture multi_file fields (pre-existing gap affecting every non-BO sub-tab, not fixed here).

- [ ] **Step 2: Commit**

```bash
git add docs/multi_file_reconciliation.md
git commit -m "docs: document ad-hoc multi-file compare in the Compare tab"
```

---

## Self-review notes

- **Spec coverage:** Task 1-3 deliver the backend (request schema, service method, route) using the EXACT persistence pattern already proven by `bo`/`sql`/`recon-file` (`RunRepository.create_run` → background task → `add_test_result`/`add_mismatch_details`/`MetricsWriter`/`update_run_status`), reusing `TestResultOut.file_pairs`/`.unmatched_sources`/`.unmatched_targets` wire fields that already exist from Phase 4 with zero response-schema changes. Task 4-5 deliver the frontend, directly adapting Phase 6's job-editor `mf_*` form fields (proven working, e2e-tested markup) to a `mfCompare*`-prefixed sub-tab, plus a new result view reusing the already-wire-compatible `file_pairs` shape. Task 6 proves it end-to-end in a real browser. Task 7 documents it.
- **Learned from Phase 6, applied here without being asked twice:** Task 4 explicitly calls out the `npm run build:html` step as its own verification checkpoint (Step 3, with an explicit "confirm it shows a change" check), specifically because Phase 6 made and had to catch this exact mistake.
- **Reuses proven code, doesn't reinvent:** the `preview-file-mapping` endpoint (Phase 6) is reused as-is for the new sub-tab's preview button -- no new preview endpoint needed. `RemoteFileSourceSession`, `pair_files`/`pair_files_automated`, `aggregate_reconciliation_results` (Phases 1-5) are reused as-is in `CompareService.run_multi_file_compare` -- no reconciliation logic is duplicated or reinvented, only the discovery/pairing/persistence orchestration is new (and even that closely mirrors `run_sql_comparison`'s existing shape).
- **Scope decisions stated up front, not discovered as gaps later:** local-only, sequential pairs, no readiness, no lineage manifest, no template support -- each with its reasoning in this document's header, so a reviewer (or future-you) doesn't have to guess whether these were oversights.
- **Type/name consistency:** `MultiFileCompareRequest`, `run_multi_file_compare`, `_run_multi_file_bg`, `compare_multi_file`, `mfCompare*` (frontend prefix), `_buildMfCompareFileMapping`/`previewMfCompareMapping`/`runMultiFileCompare` are spelled identically at every definition and call site across Tasks 1-6.
- **Bug caught during self-review, not left in:** Task 2's first draft had `if mapping.unmatched_sources or mapping.unmatched_targets and spec.unmatched_policy == "fail":` -- Python's `and` binds tighter than `or`, so this evaluated as `unmatched_sources or (unmatched_targets and policy == "fail")`, meaning ANY unmatched source would raise regardless of `unmatched_policy`, silently breaking `"warn"`/`"ignore"` whenever only the *source* side had unmatched groups. Fixed to two separate `if` checks, and a regression test (`test_run_multi_file_compare_ignore_policy_proceeds_with_unmatched`) was added specifically because neither of the task's original two tests exercised unmatched groups at all -- both would have passed against the buggy version unchanged, which is exactly how a bug like this survives "tests pass."
