# Compare / Job Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Compare (BO report, reconciliation, multi-file diff) entry points to the Job Catalog row, the New/Edit Job modal, and Job Selections' run-history bridge, backed by a new "run-reference" compare source that lets a past job run stand in for a live/path/upload source.

**Architecture:** Backend: `SourceConfig` gains a `"run"` source type (BO report), `MultiFileCompareRequest` gains `run_id`/`job_name` fields (multi-file), and `RunRepository` gains job-scoped result lookups. Multi-file's saved-job executor persists each file pair's source/target frames as CSV artifacts (new — two-sided jobs don't get one today) so a later run-reference compare can re-read them. Reconciliation already supports this via `ReconFileCompareRequest.stored_run_id`, no backend change needed there. Frontend: all three new entry points drive the *existing* Compare-tab Alpine state and methods (`boSourceA`/`runBOComparison()`, `fileRunIdA`/`runFileCompare()`, `mfCompare*`/`runMultiFileCompare()`) instead of duplicating any result-rendering code — a new `openCompareForJob()` helper sets that shared state and either switches `currentView` to Compare or, for the Job modal, runs inline and shows a compact status line with a "View full results" link into the Compare tab.

**Tech Stack:** FastAPI + Pydantic + SQLAlchemy (backend), Alpine.js feature-slice pattern (frontend), pytest (backend tests), Playwright (e2e).

Spec: `docs/superpowers/specs/2026-08-11-compare-job-integration-design.md`

---

## Phase 1 — Backend

### Task 1: RunRepository job-scoped result lookups

**Files:**
- Modify: `etl_framework/repository/repository.py:380` (after `add_test_result`)
- Test: `tests/unit/test_job_selections_repository.py` (append; this file already covers `RunRepository`/job-selection repository behavior)

- [ ] **Step 1: Write the failing tests**

```python
def test_get_result_for_job_returns_matching_test_result(db_session):
    from etl_framework.repository.repository import RunRepository
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus
    from datetime import datetime, timezone

    repo = RunRepository(db_session)
    repo.create_run("run-1", "dev", "prod")
    repo.add_test_result("run-1", ReconciliationResult(
        query_name="my_job", source_env="dev", target_env="prod",
        source_row_count=1, target_row_count=1, matched_count=1,
        missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
        mismatches=[], status=TestStatus.PASSED,
        executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
    ))

    result = repo.get_result_for_job("run-1", "my_job")

    assert result is not None
    assert result.query_name == "my_job"


def test_get_result_for_job_returns_none_when_no_match(db_session):
    from etl_framework.repository.repository import RunRepository

    repo = RunRepository(db_session)
    repo.create_run("run-1", "dev", "prod")

    assert repo.get_result_for_job("run-1", "nonexistent_job") is None


def test_list_results_for_job_orders_most_recent_run_first(db_session):
    from etl_framework.repository.repository import RunRepository
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus
    from datetime import datetime, timezone

    repo = RunRepository(db_session)
    for run_id in ("run-a", "run-b", "run-c"):
        repo.create_run(run_id, "dev", "prod")
        repo.add_test_result(run_id, ReconciliationResult(
            query_name="my_job", source_env="dev", target_env="prod",
            source_row_count=1, target_row_count=1, matched_count=1,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=TestStatus.PASSED,
            executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
        ))

    results = repo.list_results_for_job("my_job", limit=2)

    assert [r.run_id for r in results] == ["run-c", "run-b"]
```

Check this test file's existing `db_session` fixture name before pasting — if it uses a different fixture name (e.g. `db`), match it exactly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_job_selections_repository.py -k "result_for_job" -v`
Expected: FAIL with `AttributeError: 'RunRepository' object has no attribute 'get_result_for_job'`

- [ ] **Step 3: Implement the repository methods**

Add to `etl_framework/repository/repository.py` inside `class RunRepository`, right after `add_test_result` (ends at line ~400):

```python
    def get_result_for_job(self, run_id: str, job_name: str) -> TestResult | None:
        return (
            self._db.query(TestResult)
            .filter(TestResult.run_id == run_id, TestResult.query_name == job_name)
            .first()
        )

    def list_results_for_job(self, job_name: str, limit: int = 20, offset: int = 0) -> list[TestResult]:
        return (
            self._db.query(TestResult)
            .join(TestRun, TestResult.run_id == TestRun.run_id)
            .filter(TestResult.query_name == job_name)
            .order_by(TestRun.id.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_job_selections_repository.py -k "result_for_job" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/repository/repository.py tests/unit/test_job_selections_repository.py
git commit -m "feat: add job-scoped TestResult lookups to RunRepository"
```

---

### Task 2: Job-scoped run-data-artifact resolution

A single-job run already has `resolve_row_diffable_artifact(run)`, but it requires the *whole run* to have exactly one result — wrong for a job that ran inside a multi-job selection run. This task adds a job-scoped equivalent.

**Files:**
- Modify: `api/services/run_data_artifact.py`
- Test: `tests/unit/test_run_data_artifacts.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_resolve_job_result_artifact_finds_the_named_jobs_result(tmp_path, monkeypatch):
    from api.services import upload_store
    from api.services.run_data_artifact import resolve_job_result_artifact
    from etl_framework.repository.repository import RunRepository
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus
    from datetime import datetime, timezone

    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    artifact_path = upload_store.persist_run_data_artifact("run-1", _CSV, "report.csv")

    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-1", "dev", "prod")
    repo.add_test_result("run-1", ReconciliationResult(
        query_name="my_bo_job", source_env="dev", target_env="prod",
        source_row_count=1, target_row_count=1, matched_count=1,
        missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
        mismatches=[], status=TestStatus.PASSED,
        executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
        data_artifact_path=artifact_path,
    ))

    resolved = resolve_job_result_artifact(repo, "run-1", "my_bo_job")

    assert resolved is not None
    assert resolved.read_bytes() == _CSV


def test_resolve_job_result_artifact_returns_none_for_unknown_job(tmp_path, monkeypatch):
    from api.services import upload_store
    from api.services.run_data_artifact import resolve_job_result_artifact
    from etl_framework.repository.repository import RunRepository

    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-1", "dev", "prod")

    assert resolve_job_result_artifact(repo, "run-1", "no_such_job") is None


def test_load_job_result_frame_reads_the_artifact_as_a_dataframe(tmp_path, monkeypatch):
    from api.services import upload_store
    from api.services.run_data_artifact import load_job_result_frame
    from etl_framework.repository.repository import RunRepository
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus
    from datetime import datetime, timezone

    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    artifact_path = upload_store.persist_run_data_artifact("run-1", _CSV, "report.csv")

    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-1", "dev", "prod")
    repo.add_test_result("run-1", ReconciliationResult(
        query_name="my_bo_job", source_env="dev", target_env="prod",
        source_row_count=1, target_row_count=1, matched_count=1,
        missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
        mismatches=[], status=TestStatus.PASSED,
        executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
        data_artifact_path=artifact_path,
    ))

    frame = load_job_result_frame(repo, "run-1", "my_bo_job")

    assert frame is not None
    assert list(frame["value"]) == ["alpha", "beta"]
```

This file already defines `_session()` and `_CSV` (`id,value\n1,alpha\n2,beta\n`) at module level — reuse them, don't redefine.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_run_data_artifacts.py -k "job_result" -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_job_result_artifact'`

- [ ] **Step 3: Implement the resolvers**

Add to `api/services/run_data_artifact.py`, after `load_row_diffable_frame`:

```python
def resolve_job_result_artifact(repo, run_id: str, job_name: str) -> Path | None:
    """Path to one job's tabular artifact within a run, or None.

    Unlike resolve_row_diffable_artifact, this looks at a single job's own
    TestResult inside a possibly multi-job run, rather than requiring the
    whole run to have exactly one result.
    """
    result = repo.get_result_for_job(run_id, job_name)
    if result is None or not result.data_artifact_path:
        return None
    path = resolve_run_data_artifact(result.data_artifact_path)
    if path is None or path.suffix.lower() not in TABULAR_EXTS:
        return None
    return path


def load_job_result_frame(repo, run_id: str, job_name: str) -> pd.DataFrame | None:
    """Read a job's stored run artifact as a frame, or None if unavailable."""
    from api.services.file_source import _read_tabular_bytes

    path = resolve_job_result_artifact(repo, run_id, job_name)
    if path is None:
        return None
    try:
        return _read_tabular_bytes(path.read_bytes(), path.suffix.lower())
    except Exception:
        logger.warning("Unreadable job result artifact %s — falling back", path)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_run_data_artifacts.py -k "job_result" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/run_data_artifact.py tests/unit/test_run_data_artifacts.py
git commit -m "feat: add job-scoped run data artifact resolution"
```

---

### Task 3: `SourceConfig` gains a `"run"` source type

**Files:**
- Modify: `api/schemas.py:798-824`
- Test: `tests/unit/test_compare_api.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_source_config_run_type_requires_run_id_and_job_name():
    from api.schemas import SourceConfig
    import pytest as _pytest
    from pydantic import ValidationError

    with _pytest.raises(ValidationError, match="run_id and job_name required"):
        SourceConfig(source_type="run")


def test_source_config_run_type_accepts_run_id_and_job_name():
    from api.schemas import SourceConfig

    src = SourceConfig(source_type="run", run_id="run-1", job_name="my_job")

    assert src.run_id == "run-1"
    assert src.job_name == "my_job"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_api.py -k "source_config_run" -v`
Expected: FAIL with a Pydantic error about `source_type` not accepting `"run"` (literal mismatch)

- [ ] **Step 3: Add the `"run"` type**

In `api/schemas.py`, replace the `SourceConfig` class (lines 798-824):

```python
class SourceConfig(BaseModel):
    source_type: Literal["live", "path", "upload", "api", "run"]
    config_id: int | None = None
    doc_id: str | None = None
    report_id: str | None = None
    format: Literal["csv", "xlsx", "xls"] = "xlsx"
    file_path: str | None = None
    file_content_b64: str | None = None
    file_name: str | None = None
    api_endpoint_name: str | None = None
    # Prompt answers for a live source, answered before the export is pulled —
    # same contract as BOReportDownloadRequest.parameters and a bo_report job's
    # params["bo_parameters"]. Without these, a prompted report exports with
    # whatever answers were last saved on the document.
    bo_parameters: list[BOParamAnswer] = Field(default_factory=list)
    # For source_type == "run": re-use a past job's own persisted pull instead
    # of fetching live/path/upload/api. job_name picks which job's TestResult
    # inside that run to read — a run may hold many jobs' results.
    run_id: str | None = None
    job_name: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "SourceConfig":
        if self.source_type == "live" and self.config_id is None:
            raise ValueError("config_id required for live source")
        if self.source_type == "path" and not self.file_path:
            raise ValueError("file_path required for path source")
        if self.source_type == "upload" and not self.file_content_b64:
            raise ValueError("file_content_b64 required for upload source")
        if self.source_type == "api" and (self.config_id is None or not self.api_endpoint_name):
            raise ValueError("config_id and api_endpoint_name required for api source")
        if self.source_type == "run" and (not self.run_id or not self.job_name):
            raise ValueError("run_id and job_name required for run source")
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_api.py -k "source_config_run" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_compare_api.py
git commit -m "feat: add run-reference source type to SourceConfig"
```

---

### Task 4: `CompareService._load_bo_source` resolves `"run"` sources

**Files:**
- Modify: `api/services/compare_service.py:216-277`
- Test: `tests/unit/test_run_data_artifacts.py` (append — this file already has the `_bo_report_run` fixture producing exactly the run this task needs to reference)

- [ ] **Step 1: Write the failing test**

```python
def test_bo_run_reference_source_reads_the_stored_jobs_pull(tmp_path, monkeypatch):
    from api.schemas import SourceConfig, BOCompareRequest
    from api.services.compare_service import CompareService
    from etl_framework.repository.repository import ConfigRepository, RunRepository

    db = _session()
    run = _bo_report_run(db, tmp_path, monkeypatch)  # job name "sales_report"

    other_csv = b"id,value\n1,alpha\n2,beta\n"
    svc = CompareService(db, ConfigRepository(db))
    compare_run_id = "compare-run-1"
    RunRepository(db).create_run(compare_run_id, "Source A", "Source B")

    req = BOCompareRequest(
        source_a=SourceConfig(source_type="run", run_id=run.run_id, job_name="sales_report"),
        source_b=SourceConfig(source_type="upload", file_content_b64=__import__("base64").b64encode(other_csv).decode(), file_name="b.csv"),
        key_columns=["id"],
    )
    svc.run_bo_comparison(req, compare_run_id)

    result = RunRepository(db).get_run(compare_run_id)
    assert result.status == "PASSED"


def test_bo_run_reference_source_404s_when_job_never_ran_in_that_run(tmp_path, monkeypatch):
    from api.schemas import SourceConfig, BOCompareRequest
    from api.services.compare_service import CompareService
    from etl_framework.repository.repository import ConfigRepository, RunRepository
    from fastapi import HTTPException
    import pytest as _pytest

    db = _session()
    run = _bo_report_run(db, tmp_path, monkeypatch)

    svc = CompareService(db, ConfigRepository(db))
    req = BOCompareRequest(
        source_a=SourceConfig(source_type="run", run_id=run.run_id, job_name="no_such_job"),
        source_b=SourceConfig(source_type="upload", file_content_b64="aWQsdmFsdWUKMSxhbHBoYQo=", file_name="b.csv"),
        key_columns=["id"],
    )
    with _pytest.raises(HTTPException) as exc_info:
        svc._load_bo_source(req.source_a, None, None)
    assert exc_info.value.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_run_data_artifacts.py -k "bo_run_reference" -v`
Expected: FAIL — `_load_bo_source` doesn't accept `source_type == "run"` yet, falls through to `read_tabular(path=None, ...)` and errors

- [ ] **Step 3: Add the `"run"` branch**

In `api/services/compare_service.py`, modify `_load_bo_source` (starts line 216). Insert a new branch right after the `"live"` block's `finally: client.logout()` (line 270), before `if src.source_type == "api":` (line 271):

```python
        if src.source_type == "run":
            from api.services.run_data_artifact import load_job_result_frame

            frame = load_job_result_frame(self._repo, src.run_id, src.job_name)
            if frame is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"No comparable data artifact found for job '{src.job_name}' "
                        f"in run {src.run_id}"
                    ),
                )
            return frame
        if src.source_type == "api":
            return self._load_api_source(src, run_id, store_responses=store_responses)
```

(This replaces the existing `if src.source_type == "api":` line — keep its body unchanged, just add the new `if` block immediately before it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_run_data_artifacts.py -k "bo_run_reference" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/compare_service.py tests/unit/test_run_data_artifacts.py
git commit -m "feat: resolve BO compare run-reference sources from a past job result"
```

---

### Task 5: `GET /api/jobs/{name}/runs` endpoint

**Files:**
- Modify: `api/schemas.py` (add `JobRunSummaryOut` near `RunStatusOut`, line ~254)
- Modify: `api/routes/jobs.py` (add route after `list_jobs`, line ~116)
- Test: `tests/unit/test_api.py` (append — this file already covers `api/routes/jobs.py` basics)

- [ ] **Step 1: Write the failing test**

```python
def test_job_runs_endpoint_returns_recent_runs_for_that_job(client):
    from etl_framework.repository.repository import RunRepository, JobRepository
    from etl_framework.repository.database import SessionLocal
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus
    from datetime import datetime, timezone

    db = SessionLocal()
    JobRepository(db).create({
        "name": "job_runs_test", "description": "", "tags": [], "job_type": "bo_report",
        "query": "", "key_columns": [], "exclude_columns": [],
        "source_env": None, "target_env": None, "params": {}, "enabled": True,
    })
    repo = RunRepository(db)
    for run_id in ("run-x", "run-y"):
        repo.create_run(run_id, "dev", "prod")
        repo.add_test_result(run_id, ReconciliationResult(
            query_name="job_runs_test", source_env="dev", target_env="prod",
            source_row_count=1, target_row_count=1, matched_count=1,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=TestStatus.PASSED,
            executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
        ))
    db.close()

    resp = client.get("/api/jobs/job_runs_test/runs")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["run_id"] == "run-y"
    assert body[0]["status"] == "PASSED"


def test_job_runs_endpoint_returns_empty_list_for_unknown_job(client):
    resp = client.get("/api/jobs/no_such_job/runs")

    assert resp.status_code == 200
    assert resp.json() == []
```

Check `test_api.py`'s existing `client` fixture — it should already set up an in-memory DB the same way `test_compare_api.py`'s does (see Task 4's `_session()` for the pattern if it doesn't).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_api.py -k "job_runs_endpoint" -v`
Expected: FAIL with 404 (route doesn't exist)

- [ ] **Step 3: Add the schema**

In `api/schemas.py`, after `RunStatusOut` (ends line 254):

```python
class JobRunSummaryOut(BaseModel):
    run_id: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    has_data_artifact: bool = False

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Add the route**

In `api/routes/jobs.py`, after `list_jobs` (ends line 116):

```python
@router.get("/{name}/runs", response_model=list[JobRunSummaryOut])
def list_job_runs(name: str, limit: int = 20, db: Session = Depends(get_session)):
    from etl_framework.repository.repository import RunRepository

    results = RunRepository(db).list_results_for_job(name, limit=limit)
    return [
        JobRunSummaryOut(
            run_id=r.run_id,
            status=r.effective_status if hasattr(r, "effective_status") else r.status,
            started_at=r.run.started_at if r.run else None,
            completed_at=r.run.completed_at if r.run else None,
            has_data_artifact=bool(r.data_artifact_path),
        )
        for r in results
    ]
```

Add `JobRunSummaryOut` to the `from api.schemas import ...` line at the top of `api/routes/jobs.py` (currently `JobDefinition, PreviewFileMappingRequest`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api.py -k "job_runs_endpoint" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py api/routes/jobs.py tests/unit/test_api.py
git commit -m "feat: add GET /api/jobs/{name}/runs endpoint"
```

---

### Task 6: `upload_store.persist_pair_artifacts` helper

**Files:**
- Modify: `api/services/upload_store.py` (add after `persist_run_data_artifact`, line ~99)
- Test: `tests/unit/test_run_data_artifacts.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_persist_pair_artifacts_writes_source_and_target_csvs(tmp_path, monkeypatch):
    from api.services import upload_store
    import pandas as pd

    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    source_df = pd.DataFrame({"id": [1], "value": ["alpha"]})
    target_df = pd.DataFrame({"id": [1], "value": ["alpha"]})

    source_path, target_path = upload_store.persist_pair_artifacts(
        "run-1", "regional_sales_recon", 0, source_df, target_df,
    )

    assert source_path is not None and target_path is not None
    assert Path(source_path).read_text().strip().splitlines() == ["id,value", "1,alpha"]
    assert Path(target_path).parent == tmp_path.resolve() / "run-1"


def test_persist_pair_artifacts_sanitizes_job_name_in_filenames(tmp_path, monkeypatch):
    from api.services import upload_store
    import pandas as pd

    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    df = pd.DataFrame({"id": [1]})

    source_path, _ = upload_store.persist_pair_artifacts(
        "run-1", "../../etc/passwd", 0, df, df,
    )

    assert Path(source_path).parent == tmp_path.resolve() / "run-1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_run_data_artifacts.py -k "persist_pair_artifacts" -v`
Expected: FAIL with `AttributeError: module 'api.services.upload_store' has no attribute 'persist_pair_artifacts'`

- [ ] **Step 3: Implement the helper**

Add to `api/services/upload_store.py`, after `persist_run_data_artifact` (ends line 99):

```python
def persist_pair_artifacts(
    run_id: str, job_name: str, pair_index: int, source_df: Any, target_df: Any,
) -> tuple[str | None, str | None]:
    """Store a multi-file job's per-pair source/target frames as CSV, so a
    later compare can reference this run+job as a source without re-reading
    the original file set. Best-effort, like persist_run_data_artifact: a
    frame past the size cap is simply skipped for that side.
    """
    safe_job = safe_filename(job_name, "job")
    source_path = persist_run_data_artifact(
        run_id, source_df.to_csv(index=False).encode("utf-8"),
        f"{safe_job}_pair{pair_index}_source.csv",
    )
    target_path = persist_run_data_artifact(
        run_id, target_df.to_csv(index=False).encode("utf-8"),
        f"{safe_job}_pair{pair_index}_target.csv",
    )
    return source_path, target_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_run_data_artifacts.py -k "persist_pair_artifacts" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/upload_store.py tests/unit/test_run_data_artifacts.py
git commit -m "feat: add persist_pair_artifacts helper for multi-file pair storage"
```

---

### Task 7: `RunExecutor` persists multi-file pair artifacts

**Files:**
- Modify: `api/services/run_executor.py:657-802` (`_build_case_multi_file_reconciliation`)
- Test: `tests/unit/test_multi_file_jobs.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_multi_file_pairs_persist_source_and_target_artifacts(tmp_path, monkeypatch):
    from api.services import file_source, upload_store

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", artifact_root.resolve())

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_data_east_20260101.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (target_dir / "financials_east_20260101.dat").write_text("id,value\n1,alpha\n", encoding="utf-8")

    job = JobDefinition(
        name="regional_sales_recon",
        job_type="reconciliation",
        query="",
        key_columns=["id"],
        params={
            "source_mode": "multi_file",
            "file_mapping": {
                "strategy": "explicit",
                "match_on": ["region", "date"],
                "source": {"kind": "local", "root": str(source_dir), "pattern": "sales_data_{region}_{date:%Y%m%d}.csv"},
                "target": {"kind": "local", "root": str(target_dir), "pattern": "financials_{region}_{date:%Y%m%d}.dat"},
            },
        },
    )
    executor = RunExecutor(
        db=None, run_id="test-run", source_env="source", target_env="target",
        job_sequence=[], run_settings=RunSettings(chunk_size=100, use_hash_precheck=True),
        config_snapshot={},
    )
    executor._resolve_segment_columns = lambda _job: []

    result = executor._build_case(job)()

    pair = result.mismatch_summary["file_pairs"][0]
    assert pair["source_artifact_path"]
    assert pair["target_artifact_path"]
    assert Path(pair["source_artifact_path"]).read_text() == "id,value\n1,alpha\n"
```

`Path` and `JobDefinition`/`RunExecutor`/`RunSettings`/`TestStatus` are already imported at the top of this file per the existing tests above it — add `from pathlib import Path` at the top only if not already present.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py -k "persist_source_and_target" -v`
Expected: FAIL with `KeyError: 'source_artifact_path'`

- [ ] **Step 3: Persist artifacts in the pair executor**

In `api/services/run_executor.py`, modify `_build_case_multi_file_reconciliation` (starts line 657):

1. Change `_make_pair_case(pair)` to `_make_pair_case(pair, pair_index)`:

```python
            def _make_pair_case(pair, pair_index):
                def run_pair() -> ReconciliationResult:
```

2. Right after `target_df = pd.concat(...)` (the block ending at line 748) and before `source_df, target_df, resolved_keys = resolve_key_columns(...)` (line 749), add:

```python
                    from api.services import upload_store
                    source_artifact, target_artifact = upload_store.persist_pair_artifacts(
                        self._run_id, job.name, pair_index, source_df, target_df,
                    )
```

3. Change the `return self._run_reconciliation_job(...)` (lines 760-769) to capture and attach the artifact paths:

```python
                    pair_result = self._run_reconciliation_job(
                        pair_job,
                        source_engine,
                        target_engine,
                        query=FILE_SOURCE_QUERY,
                        params={},
                        chunk_size=0,
                        use_hash_precheck=False,
                        segment_columns=segment_columns,
                    )
                    return dataclasses.replace(
                        pair_result,
                        mismatch_summary={
                            **(pair_result.mismatch_summary or {}),
                            "pair_source_artifact_path": source_artifact,
                            "pair_target_artifact_path": target_artifact,
                        },
                    )
```

4. Change the `cases` list comprehension (line 772) to pass the index:

```python
            cases = [(f"pair_{i}", _make_pair_case(pair, i)) for i, pair in enumerate(mapping.pairs)]
```

`dataclasses` is already imported at module level in `run_executor.py` (used elsewhere for `dataclasses.replace`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py -k "persist_source_and_target" -v`
Expected: PASS

- [ ] **Step 5: Run the full multi-file test file to check nothing else broke**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_multi_file_jobs.py
git commit -m "feat: persist multi-file job pair artifacts for later run-reference compare"
```

---

### Task 8: `aggregate_reconciliation_results` carries artifact paths into `file_pairs`

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py:474-486`
- Test: `tests/unit/test_multi_file_jobs.py` — Task 7's test already asserts on `pair["source_artifact_path"]`/`pair["target_artifact_path"]`, so this task's job is to make that pass; no new test needed here, but re-run Task 7's test after this change since it depends on both.

- [ ] **Step 1: Confirm Task 7's test is still failing for the right reason**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py -k "persist_source_and_target" -v`
Expected: FAIL with `KeyError: 'source_artifact_path'` (Task 7 wrote the per-pair `pair_source_artifact_path`/`pair_target_artifact_path` into each pair's own `mismatch_summary`, but `aggregate_reconciliation_results` doesn't read them into the rolled-up `file_pairs` list yet)

- [ ] **Step 2: Carry the paths into `pair_summaries`**

In `etl_framework/reconciliation/file_mapping.py`, modify the `pair_summaries.append({...})` block (lines 474-486):

```python
        pair_summaries.append({
            "key": pair_key,
            "status": result.status.value,
            "error": error_message,
            "source_files": [f.file_name for f in pair.source.files],
            "target_files": [f.file_name for f in pair.target.files],
            "source_row_count": result.source_row_count,
            "target_row_count": result.target_row_count,
            "matched_count": result.matched_count,
            "missing_in_target_count": result.missing_in_target_count,
            "missing_in_source_count": result.missing_in_source_count,
            "value_mismatch_count": result.value_mismatch_count,
            "source_artifact_path": (
                result.mismatch_summary.get("pair_source_artifact_path")
                if isinstance(result.mismatch_summary, dict) else None
            ),
            "target_artifact_path": (
                result.mismatch_summary.get("pair_target_artifact_path")
                if isinstance(result.mismatch_summary, dict) else None
            ),
        })
```

- [ ] **Step 3: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py
git commit -m "feat: carry per-pair artifact paths into aggregated multi-file results"
```

---

### Task 9: `MultiFileCompareRequest` gains a run-reference mode

**Files:**
- Modify: `api/schemas.py:902-915`
- Test: `tests/unit/test_multi_file_compare_request.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_multi_file_compare_request_accepts_run_reference():
    from api.schemas import MultiFileCompareRequest

    req = MultiFileCompareRequest(run_id="run-1", job_name="regional_sales_recon")

    assert req.run_id == "run-1"
    assert req.file_mapping is None


def test_multi_file_compare_request_rejects_run_id_without_job_name():
    from api.schemas import MultiFileCompareRequest
    import pytest as _pytest
    from pydantic import ValidationError

    with _pytest.raises(ValidationError, match="run_id and job_name must both be set"):
        MultiFileCompareRequest(run_id="run-1")


def test_multi_file_compare_request_rejects_both_file_mapping_and_run_reference():
    from api.schemas import MultiFileCompareRequest
    import pytest as _pytest
    from pydantic import ValidationError

    with _pytest.raises(ValidationError, match="mutually exclusive"):
        MultiFileCompareRequest(
            run_id="run-1", job_name="job",
            file_mapping={"strategy": "explicit", "source": {}, "target": {}},
        )


def test_multi_file_compare_request_rejects_neither_file_mapping_nor_run_reference():
    from api.schemas import MultiFileCompareRequest
    import pytest as _pytest
    from pydantic import ValidationError

    with _pytest.raises(ValidationError, match="requires either file_mapping or run_id"):
        MultiFileCompareRequest()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_multi_file_compare_request.py -k "run_reference or run_id" -v`
Expected: FAIL — `file_mapping` is currently a required field, so `MultiFileCompareRequest(run_id=...)` fails with the wrong error ("field required" for `file_mapping`, not the new validator message)

- [ ] **Step 3: Update the schema**

In `api/schemas.py`, replace `MultiFileCompareRequest` (lines 902-915):

```python
class MultiFileCompareRequest(BaseModel):
    """Ad-hoc (no saved job) multi-file reconciliation, run once from the
    Compare tab. ``file_mapping`` is the same config shape a saved
    ``multi_file`` job's ``params.file_mapping`` uses (see
    ``etl_framework.reconciliation.file_mapping.FileMappingSpec.from_params``),
    but this phase only supports ``kind: "local"`` on both sides -- see the
    Phase 7 plan doc for why.

    Alternatively, ``run_id`` + ``job_name`` re-compares the file pairs a
    saved multi_file job's run already persisted, instead of re-discovering
    files from ``file_mapping``'s source/target roots. Exactly one of
    ``file_mapping`` or ``run_id``+``job_name`` must be set.
    """
    label_a: str = "Source A"
    label_b: str = "Source B"
    key_columns: list[str] | None = None
    exclude_columns: list[str] = Field(default_factory=list)
    file_mapping: dict[str, Any] | None = None
    run_id: str | None = None
    job_name: str | None = None
    advanced: AdvancedCompareOptions = Field(default_factory=AdvancedCompareOptions)

    @model_validator(mode="after")
    def validate_source(self) -> "MultiFileCompareRequest":
        has_run_ref = bool(self.run_id or self.job_name)
        if has_run_ref and not (self.run_id and self.job_name):
            raise ValueError("run_id and job_name must both be set for a run-reference multi-file compare")
        if has_run_ref and self.file_mapping:
            raise ValueError("file_mapping and run_id/job_name are mutually exclusive")
        if not has_run_ref and not self.file_mapping:
            raise ValueError("multi-file compare requires either file_mapping or run_id + job_name")
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_multi_file_compare_request.py -v`
Expected: all PASS (including pre-existing tests in this file — they used `file_mapping`, still supported)

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_multi_file_compare_request.py
git commit -m "feat: add run-reference mode to MultiFileCompareRequest"
```

---

### Task 10: `CompareService.run_multi_file_compare` resolves run-reference requests

**Files:**
- Modify: `api/services/compare_service.py:687-790`
- Test: `tests/unit/test_compare_service_multi_file.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_run_multi_file_compare_from_run_reference(tmp_path, monkeypatch):
    from api.services import upload_store
    from api.services.compare_service import CompareService
    from api.schemas import MultiFileCompareRequest
    from etl_framework.repository.repository import ConfigRepository, RunRepository
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus
    from datetime import datetime, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401

    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    source_path = upload_store.persist_run_data_artifact("prior-run", b"id,value\n1,alpha\n", "job_pair0_source.csv")
    target_path = upload_store.persist_run_data_artifact("prior-run", b"id,value\n1,alpha\n", "job_pair0_target.csv")

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine)

    repo = RunRepository(db)
    repo.create_run("prior-run", "source", "target")
    repo.add_test_result("prior-run", ReconciliationResult(
        query_name="regional_sales_recon", source_env="source", target_env="target",
        source_row_count=1, target_row_count=1, matched_count=1,
        missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
        mismatches=[], status=TestStatus.FAILED,
        executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
        mismatch_summary={
            "file_pairs": [{
                "key": {"region": "east"},
                "source_files": ["sales_east.csv"], "target_files": ["fin_east.csv"],
                "source_artifact_path": source_path, "target_artifact_path": target_path,
            }],
        },
    ))

    svc = CompareService(db, ConfigRepository(db))
    compare_run_id = "compare-run-1"
    repo.create_run(compare_run_id, "Source A", "Source B")
    req = MultiFileCompareRequest(run_id="prior-run", job_name="regional_sales_recon", key_columns=["id"])

    svc.run_multi_file_compare(req, compare_run_id)

    run = repo.get_run(compare_run_id)
    assert run.status == "PASSED"


def test_run_multi_file_compare_from_run_reference_404s_on_unknown_job(tmp_path, monkeypatch):
    from api.services import upload_store
    from api.services.compare_service import CompareService
    from api.schemas import MultiFileCompareRequest
    from etl_framework.repository.repository import ConfigRepository, RunRepository
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401

    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine)
    repo = RunRepository(db)
    repo.create_run("prior-run", "source", "target")

    svc = CompareService(db, ConfigRepository(db))
    compare_run_id = "compare-run-1"
    repo.create_run(compare_run_id, "Source A", "Source B")
    req = MultiFileCompareRequest(run_id="prior-run", job_name="no_such_job")

    svc.run_multi_file_compare(req, compare_run_id)

    run = repo.get_run(compare_run_id)
    assert run.status == "ERROR"
```

Check whether `test_compare_service_multi_file.py` already has its own DB-session fixture/helper before pasting the inline setup above — reuse it if present instead of duplicating the engine/session boilerplate.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_service_multi_file.py -k "run_reference" -v`
Expected: FAIL — `run_multi_file_compare` calls `FileMappingSpec.from_params({"file_mapping": None})` and raises before ever looking at `req.run_id`

- [ ] **Step 3: Add the run-reference branch and resolver**

In `api/services/compare_service.py`, add a new private method right before `run_multi_file_compare` (line 687):

```python
    def _load_multi_file_pairs_from_run(self, run_id: str, job_name: str) -> list[dict]:
        result = self._repo.get_result_for_job(run_id, job_name)
        if result is None:
            raise HTTPException(status_code=404, detail=f"No result for job '{job_name}' in run {run_id}")
        pairs = (result.mismatch_summary or {}).get("file_pairs") or []
        usable = [p for p in pairs if p.get("source_artifact_path") and p.get("target_artifact_path")]
        if not usable:
            raise HTTPException(
                status_code=422,
                detail=f"Job '{job_name}' in run {run_id} has no stored pair artifacts to compare against",
            )
        return usable
```

Then modify `run_multi_file_compare` itself. Replace the body from `try:` through the ad-hoc discovery block (lines 702-744, up to `pair_results = []`) with a branch:

```python
    def run_multi_file_compare(self, req: MultiFileCompareRequest, run_id: str) -> None:
        """Ad-hoc multi-file reconciliation: discover, pair, reconcile every
        pair sequentially, then persist ONE aggregate TestResult -- the same
        result shape RunExecutor's saved-job multi_file path already
        produces, so the Reports-tab rendering (Phase 4) works unchanged.

        When req.run_id/req.job_name are set instead of req.file_mapping,
        re-compares the pairs a saved multi_file job's run already persisted
        (Task 7/8), instead of re-discovering files from source/target roots.
        """
        from etl_framework.reconciliation.compare_utils import resolve_key_columns
        from etl_framework.reconciliation.file_mapping import (
            DiscoveredFile, FileGroup, FileMappingResult, FilePair,
            FileMappingSpec, aggregate_reconciliation_results, pair_files, pair_files_automated,
        )
        from api.services.multi_file_remote import RemoteFileSourceSession

        try:
            self._repo.update_run_status(run_id, "RUNNING", started_at=datetime.now(timezone.utc))

            if req.run_id and req.job_name:
                stored_pairs = self._load_multi_file_pairs_from_run(req.run_id, req.job_name)
                match_on = tuple((stored_pairs[0].get("key") or {}).keys())
                mapping = FileMappingResult(
                    match_on=match_on,
                    pairs=[
                        FilePair(
                            key=tuple((p.get("key") or {}).values()),
                            source=FileGroup(key=(), files=[DiscoveredFile(
                                path=p["source_artifact_path"],
                                file_name=(p.get("source_files") or ["source.csv"])[0],
                                tokens={},
                            )]),
                            target=FileGroup(key=(), files=[DiscoveredFile(
                                path=p["target_artifact_path"],
                                file_name=(p.get("target_files") or ["target.csv"])[0],
                                tokens={},
                            )]),
                        )
                        for p in stored_pairs
                    ],
                    unmatched_sources=[],
                    unmatched_targets=[],
                )
                pair_results = []
                for pair in mapping.pairs:
                    source_df = read_tabular(path=pair.source.files[0].path)
                    target_df = read_tabular(path=pair.target.files[0].path)
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
            else:
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

This is a full replacement of the method (through its existing `except` block, which is unchanged) — the ad-hoc branch's code is moved as-is into the `else:`, not rewritten.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_service_multi_file.py -v`
Expected: all PASS (including pre-existing ad-hoc tests in this file — unaffected)

- [ ] **Step 5: Commit**

```bash
git add api/services/compare_service.py tests/unit/test_compare_service_multi_file.py
git commit -m "feat: resolve multi-file compare run-reference requests from stored pair artifacts"
```

---

### Task 11: `compare_multi_file` route skips file-mapping validation for run-reference requests

**Files:**
- Modify: `api/routes/compare.py:386-402`
- Test: `tests/unit/test_compare_api.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_multi_file_compare_run_reference_returns_202(client, monkeypatch):
    import api.routes.compare as cmp_module
    monkeypatch.setattr(cmp_module, "_run_multi_file_bg", lambda *a, **kw: None)

    resp = client.post("/api/compare/multi-file", json={
        "run_id": "prior-run", "job_name": "regional_sales_recon",
    })

    assert resp.status_code == 202
    assert resp.json()["run_type"] == "multi_file"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_compare_api.py -k "run_reference_returns_202" -v`
Expected: FAIL with 400 — `FileMappingSpec.from_params({"file_mapping": None})` raises because `file_mapping` is `None`

- [ ] **Step 3: Skip validation for run-reference requests**

In `api/routes/compare.py`, modify `compare_multi_file` (lines 386-402):

```python
def compare_multi_file(
    body: MultiFileCompareRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
) -> RunStatusOut:
    from etl_framework.reconciliation.file_mapping import FileMappingSpec

    if not (body.run_id and body.job_name):
        try:
            spec = FileMappingSpec.from_params({"file_mapping": body.file_mapping})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if spec.source.kind != "local" or spec.target.kind != "local":
            raise HTTPException(
                status_code=400,
                detail="Ad-hoc multi-file compare only supports 'local' source/target kinds.",
            )
```

(The rest of the function — `run_id = str(uuid.uuid4())` onward — is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_compare_api.py -k "run_reference_returns_202" -v`
Expected: PASS

- [ ] **Step 5: Run the full backend test suite**

Run: `python -m pytest tests/unit/ -v`
Expected: all PASS — this confirms Tasks 1-11 compose correctly

- [ ] **Step 6: Commit**

```bash
git add api/routes/compare.py tests/unit/test_compare_api.py
git commit -m "feat: skip file_mapping validation for run-reference multi-file compares"
```

---

## Phase 2 — Frontend

All three entry points drive the *existing* Compare-tab Alpine state (`boSourceA`/`fileRunIdA`/`mfCompare*`) and methods (`runBOComparison()`/`runFileCompare()`/`runMultiFileCompare()`) — no new result-rendering UI, just new ways to populate that state and trigger those methods.

### Task 12: `compare.js` — BO source objects support `"run"` type

**Files:**
- Modify: `frontend/features/compare.js:28-30` (state), `:387-413` (`_buildBOSource`)

- [ ] **Step 1: Add `runId`/`jobName` to the BO source shape**

In `frontend/features/compare.js`, replace lines 29-30:

```javascript
    boSourceA: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source A', endpointName: '', parameters: [], runId: '', jobName: '' },
    boSourceB: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source B', endpointName: '', parameters: [], runId: '', jobName: '' },
```

- [ ] **Step 2: Add the `"run"` branch to `_buildBOSource`**

In `_buildBOSource` (line 387), add a branch before the final `return { source_type: 'upload', ... }` fallback:

```javascript
      if (type === 'run') {
        return { source_type: 'run', run_id: src.runId, job_name: src.jobName };
      }
```

- [ ] **Step 3: Manual check**

Run: `npm run test:e2e -- 08a-compare-bo-report` (existing BO e2e spec must still pass — confirms this change didn't regress the live/path/upload branches)
Expected: all existing tests in that file PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/features/compare.js
git commit -m "feat: support run-reference BO sources in compare.js"
```

---

### Task 13: `launch.js` — shared `openCompareForJob` helper and job-runs fetch

**Files:**
- Modify: `frontend/features/launch.js` (add near `compareSelectedRuns`, line ~1174)

- [ ] **Step 1: Add the helper**

Add to `frontend/features/launch.js`, right after `compareSelectedRuns()` (ends line 1188):

```javascript
    async loadJobRuns(jobName) {
      try {
        return await api('GET', `/api/jobs/${encodeURIComponent(jobName)}/runs`);
      } catch (e) {
        this.toast('error', 'Could not load run history', e.message);
        return [];
      }
    },

    // Determine which Compare sub-tab a job's own compare type maps to.
    // Only bo_report and reconciliation (incl. multi_file source_mode) jobs
    // have a compare type at all -- callers must gate visibility on this too.
    _compareSubTabForJob(job) {
      if (job.job_type === 'bo_report') return 'bo';
      if (job.job_type === 'reconciliation' && job.params?.source_mode === 'multi_file') return 'multi_file';
      if (job.job_type === 'reconciliation') return 'recon';
      return null;
    },

    // Shared prefill + navigate used by the Job Catalog Compare button, the
    // Job modal's inline Test Compare section, and the Job Selections
    // History bridge. `opts.runIdA`/`opts.runIdB` (run-vs-run) and
    // `opts.navigate` (false keeps the caller on the current view, for the
    // Job modal's inline run) are optional.
    openCompareForJob(job, opts = {}) {
      const subTab = this._compareSubTabForJob(job);
      if (!subTab) {
        this.toast('warn', 'No compare type for this job', `job_type '${job.job_type}' has no compare equivalent`);
        return;
      }
      if (subTab === 'bo') {
        this.boSourceAType = opts.runIdA ? 'run' : 'live';
        this.boSourceA = {
          ...this.boSourceA,
          runId: opts.runIdA || '', jobName: job.name,
          docId: job.params?.report_id || '', reportId: job.params?.bo_report_id || '',
          configId: this.boSourceA.configId,
        };
        this.boSourceBType = opts.runIdB ? 'run' : 'upload';
        if (opts.runIdB) this.boSourceB = { ...this.boSourceB, runId: opts.runIdB, jobName: job.name };
        this.boKeyColumns = (job.key_columns || []).join(', ');
      } else if (subTab === 'multi_file') {
        this.mfCompareKeyColumns = (job.key_columns || []).join(', ');
        if (opts.runIdA) {
          this.mfCompareSourceMode = 'run';
          this.mfCompareRunId = opts.runIdA;
          this.mfCompareJobName = job.name;
        }
      } else if (subTab === 'recon') {
        this.reconMode = 'file';
        this.fileSourceAType = opts.runIdA ? 'run' : 'path';
        if (opts.runIdA) this.fileRunIdA = opts.runIdA;
        this.fileSourceBType = opts.runIdB ? 'run' : 'path';
        if (opts.runIdB) this.fileRunIdB = opts.runIdB;
        this.fileCompareKeyColumns = (job.key_columns || []).join(', ');
      }
      if (opts.navigate !== false) {
        this.currentView = 'compare';
        this.compareSubTab = subTab;
      }
    },
```

`mfCompareSourceMode`/`mfCompareRunId`/`mfCompareJobName` are new state fields added in Task 14 alongside the multi_file sub-tab's "past run" UI — `openCompareForJob` referencing them here is fine since Alpine reads them off `this` at call time, not at parse time.

- [ ] **Step 2: Manual check**

Open the app, navigate to Jobs → Job Selections → History on a selection with runs, confirm the existing "Compare Selected" mismatch-diff flow still works unchanged (this task only adds new methods, doesn't touch `compareSelectedRuns`).

- [ ] **Step 3: Commit**

```bash
git add frontend/features/launch.js
git commit -m "feat: add openCompareForJob shared prefill helper"
```

---

### Task 14: Job Catalog row — Compare button

**Files:**
- Modify: `frontend/partials/tab-launch.html:270-278` (row actions)
- Modify: `frontend/features/compare.js` (add `mfCompareSourceMode`/`mfCompareRunId`/`mfCompareJobName` state + wire into `runMultiFileCompare`)
- Modify: `frontend/partials/tab-compare.html:1350-1446` (multi_file sub-tab — add a "past run" mode toggle, mirroring the recon sub-tab's `fileSourceAType` pattern)

- [ ] **Step 1: Add multi_file run-mode state**

In `frontend/features/compare.js`, near the other `mfCompare*` state declarations (around line 24, alongside `compareSubTab: 'bo'`), add:

```javascript
    mfCompareSourceMode: 'files', // 'files' | 'run'
    mfCompareRunId: '',
    mfCompareJobName: '',
```

- [ ] **Step 2: Wire the run mode into `runMultiFileCompare`**

In `frontend/features/compare.js`, modify `runMultiFileCompare` (line 689) — replace the payload construction:

```javascript
        const payload = {
          label_a: this.mfCompareLabelA || 'Source A',
          label_b: this.mfCompareLabelB || 'Source B',
        };
        if (this.mfCompareSourceMode === 'run') {
          payload.run_id = this.mfCompareRunId;
          payload.job_name = this.mfCompareJobName;
        } else {
          payload.file_mapping = this._buildMfCompareFileMapping();
        }
```

(Keep the existing `key_columns`/`exclude_columns` block below it unchanged.)

- [ ] **Step 3: Add the multi_file sub-tab's run-mode toggle**

In `frontend/partials/tab-compare.html`, inside the `compareSubTab === 'multi_file'` block (starts line 1350), insert a mode toggle right after the Label A/B row (after line 1360, before the Strategy row):

```html
    <div class="mode-row mb-3">
      <button data-testid="compare-mf-mode-files" @click="mfCompareSourceMode = 'files'" :class="mfCompareSourceMode === 'files' ? 'pill active' : 'pill'">Server File Sets</button>
      <button data-testid="compare-mf-mode-run" @click="mfCompareSourceMode = 'run'" :class="mfCompareSourceMode === 'run' ? 'pill active' : 'pill'">Past Run</button>
    </div>
    <div x-show="mfCompareSourceMode === 'run'" class="mb-3">
      <span class="text-xs text-slate-400">Job: <span x-text="mfCompareJobName"></span>, Run: <span x-text="mfCompareRunId ? mfCompareRunId.substring(0, 8) + '…' : '(none selected)'"></span></span>
    </div>
```

Wrap the existing Strategy/Unmatched-Policy row and everything through the "Target server file set" block (lines 1361-1429) in `<div x-show="mfCompareSourceMode === 'files'">...</div>` — those fields are meaningless in run mode.

- [ ] **Step 4: Add the Job Catalog row button**

In `frontend/partials/tab-launch.html`, modify the row actions div (lines 270-278):

```html
          <div class="flex items-center gap-1 flex-shrink-0">
            <span x-show="jobGateVerdicts[job.name]" class="badge text-xs"
                  :class="(jobGateVerdicts[job.name] || {}).verdict === 'PROMOTE' ? 'text-emerald-700 bg-emerald-50' : 'text-rose-700 bg-rose-50'"
                  :data-testid="'job-row-' + job.name + '-gate-verdict'"
                  x-text="(jobGateVerdicts[job.name] || {}).verdict"></span>
            <button @click.stop="checkJobGate(job.name)" class="btn-secondary btn-sm text-xs" :data-testid="'job-row-' + job.name + '-gate-btn'">Gate</button>
            <button x-show="job.job_type === 'bo_report' || job.job_type === 'reconciliation'"
                    @click.stop="openCompareForJob(job)" class="btn-secondary btn-sm text-xs"
                    :data-testid="'job-row-' + job.name + '-compare-btn'">Compare</button>
            <button @click.stop="openEditJobModal(job)" class="btn-secondary btn-sm text-xs" :data-testid="'job-row-' + job.name + '-edit-btn'">Edit</button>
            <button @click.stop="deleteJob(job.name)" class="btn-danger btn-sm text-xs" :data-testid="'job-row-' + job.name + '-delete-btn'">Del</button>
          </div>
```

This ships the ad-hoc path first (click Compare → jumps to Compare tab prefilled from the job's own params, live source-A picker otherwise empty for the user to fill Source B). The "past run" picker (fetching `/api/jobs/{name}/runs` and setting `runIdA`/`runIdB` before calling `openCompareForJob`) is a small follow-on UI (a dropdown next to the button) — add it here too if time allows, otherwise track as a fast-follow: the backend and `openCompareForJob(job, {runIdA, runIdB})` already support it, only the picker widget is missing.

- [ ] **Step 5: Manual check**

Start the dev server, open Job Catalog, create or use an existing `bo_report` job, click its new Compare button, confirm it navigates to the Compare tab's BO sub-tab with Source A's doc/report id and key columns prefilled from the job.

- [ ] **Step 6: Commit**

```bash
git add frontend/features/compare.js frontend/partials/tab-compare.html frontend/partials/tab-launch.html
git commit -m "feat: add Compare button to Job Catalog rows"
```

---

### Task 15: New/Edit Job modal — inline Test Compare section

**Files:**
- Modify: `frontend/partials/tab-launch.html` (inside the Job Modal, after its existing fields — find the modal's closing action-buttons row to insert before)
- Modify: `frontend/features/launch.js` (add `runInlineJobCompare()`)

- [ ] **Step 1: Add the inline-compare method**

In `frontend/features/launch.js`, right after `openCompareForJob` (Task 13):

```javascript
    // Test Compare inside the Job modal: builds a job-shaped object from the
    // in-progress (possibly unsaved) form state and drives the same Compare
    // state/methods openCompareForJob uses, without navigating away.
    async runInlineJobCompare() {
      const m = this.jobModal;
      const job = {
        name: m.name, job_type: m.job_type,
        key_columns: (m.key_columns_raw || '').split(',').map(s => s.trim()).filter(Boolean),
        params: m.job_type === 'bo_report'
          ? { report_id: m.bo_report_id, bo_report_id: m.bo_page_id }
          : { source_mode: m.source_mode },
      };
      this.openCompareForJob(job, { navigate: false });
      const subTab = this._compareSubTabForJob(job);
      if (subTab === 'bo') await this.runBOComparison();
      else if (subTab === 'multi_file') await this.runMultiFileCompare();
      else if (subTab === 'recon') await this.runFileCompare();
    },
```

- [ ] **Step 2: Add the modal section**

In `frontend/partials/tab-launch.html`, inside the Job Modal (starts line 285), find where the modal's field sections end and its footer action-button row begins (search for the modal's `Save`/`Cancel` buttons near the end of the `showJobModal` block). Insert immediately before that footer:

```html
              <div x-show="jobModal.job_type === 'bo_report' || jobModal.job_type === 'reconciliation'" class="border-t pt-3 mt-3">
                <button type="button" @click="showJobModalCompare = !showJobModalCompare" class="text-sm font-medium text-slate-600" data-testid="job-modal-compare-toggle">
                  Test Compare <span x-text="showJobModalCompare ? '▲' : '▼'"></span>
                </button>
                <div x-show="showJobModalCompare" class="mt-2 space-y-2">
                  <button type="button" @click="runInlineJobCompare()" class="btn-secondary btn-sm text-xs" data-testid="job-modal-compare-run-btn">
                    Run Compare
                  </button>
                  <div x-show="boCompareResult && jobModal.job_type === 'bo_report'" class="text-xs" data-testid="job-modal-compare-result">
                    <span class="badge" :class="statusBadgeClass(boCompareResult?.status)" x-text="boCompareResult?.status"></span>
                    <button type="button" @click="showJobModal = false; currentView = 'compare'; compareSubTab = 'bo'" class="btn-link btn-sm ml-2">View full results →</button>
                  </div>
                  <div x-show="mfCompareResult && jobModal.job_type === 'reconciliation' && jobModal.source_mode === 'multi_file'" class="text-xs" data-testid="job-modal-compare-result">
                    <span class="badge" :class="statusBadgeClass(mfCompareResult?.status)" x-text="mfCompareResult?.status"></span>
                    <button type="button" @click="showJobModal = false; currentView = 'compare'; compareSubTab = 'multi_file'" class="btn-link btn-sm ml-2">View full results →</button>
                  </div>
                  <div x-show="fileCompareResult && jobModal.job_type === 'reconciliation' && jobModal.source_mode !== 'multi_file'" class="text-xs" data-testid="job-modal-compare-result">
                    <span class="badge" :class="statusBadgeClass(fileCompareResult?.status)" x-text="fileCompareResult?.status"></span>
                    <button type="button" @click="showJobModal = false; currentView = 'compare'; compareSubTab = 'recon'" class="btn-link btn-sm ml-2">View full results →</button>
                  </div>
                </div>
              </div>
```

Add `showJobModalCompare: false` to the root Alpine state's initial data (same place `showJobModal` itself is declared — search `launch.js` for `showJobModal:` and add the new key alongside it), reset to `false` inside `openEditJobModal`/`openNewJobModal` so it doesn't stay open across a fresh modal open.

- [ ] **Step 3: Manual check**

Open the app, click "+ New Job", set Job Type to `bo_report`, fill in a doc/report id, open "Test Compare", click "Run Compare" — for a source needing live BO credentials this will fail without a real config, which is expected; confirm the status badge and "View full results →" link render once `boCompareResult` is set (can verify against an existing config in a dev environment, or defer full verification to the e2e task).

- [ ] **Step 4: Commit**

```bash
git add frontend/features/launch.js frontend/partials/tab-launch.html
git commit -m "feat: add inline Test Compare section to Job modal"
```

---

### Task 16: Job Selections History bridge — extend beyond mismatch-diff

**Files:**
- Modify: `frontend/features/launch.js:1174-1188` (`compareSelectedRuns`)
- Modify: `frontend/partials/tab-launch.html:1308-1333` (History modal)

- [ ] **Step 1: Add job selection + compare-type state**

In `frontend/features/launch.js`, near `compareRunIds` (search for its declaration in the root state), add:

```javascript
    selectionCompareJobName: '',
    selectionCompareType: 'mismatch_diff', // 'mismatch_diff' | 'bo' | 'recon' | 'multi_file'
```

- [ ] **Step 2: Add a getter for jobs common to both picked runs**

In `frontend/features/launch.js`, near `compareSelectedRuns` (line 1174):

```javascript
    get selectionCompareJobOptions() {
      const sel = this.selectionRunsPanel;
      if (!sel || this.compareRunIds.length !== 2) return [];
      return (sel.job_sequence || [])
        .map(item => item.job_name || item)
        .filter(name => {
          const job = (this.jobs || []).find(j => j.name === name);
          return job && (job.job_type === 'bo_report' || job.job_type === 'reconciliation');
        });
    },
```

- [ ] **Step 3: Extend `compareSelectedRuns`**

Replace `compareSelectedRuns()` (lines 1174-1188):

```javascript
    compareSelectedRuns() {
      if (this.compareRunIds.length !== 2) {
        this.toast('warn', 'Select exactly two runs', 'Pick two runs to compare');
        return;
      }
      this.showSelectionRunsModal = false;
      if (this.selectionCompareType === 'mismatch_diff' || !this.selectionCompareJobName) {
        this.mismatchDiffRunIdA = this.compareRunIds[0];
        this.mismatchDiffRunIdB = this.compareRunIds[1];
        this.mismatchDiffRunLabelA = 'Run A';
        this.mismatchDiffRunLabelB = 'Run B';
        this.mismatchDiffQueryName = this.selectionCompareJobName || '';
        this.currentView = 'compare';
        this.compareSubTab = 'mmdiff';
        this.runMismatchDiff();
        return;
      }
      const job = (this.jobs || []).find(j => j.name === this.selectionCompareJobName);
      if (!job) return;
      this.openCompareForJob(job, { runIdA: this.compareRunIds[0], runIdB: this.compareRunIds[1] });
    },
```

- [ ] **Step 4: Add the job dropdown and compare-type selector to the History modal**

In `frontend/partials/tab-launch.html`, inside the History modal (starts line 1308), find the existing "Compare Selected" button (around line 1330) and add the picker immediately before it:

```html
              <div x-show="compareRunIds.length === 2" class="flex items-center gap-2 mb-2">
                <select x-model="selectionCompareJobName" class="field-input field-select text-xs" data-testid="selection-compare-job-select">
                  <option value="">Whole run (mismatch diff)</option>
                  <template x-for="name in selectionCompareJobOptions" :key="name">
                    <option :value="name" x-text="name"></option>
                  </template>
                </select>
                <select x-show="selectionCompareJobName" x-model="selectionCompareType" class="field-input field-select text-xs" data-testid="selection-compare-type-select">
                  <option value="mismatch_diff">Mismatch Diff</option>
                  <option value="bo">BO Report</option>
                  <option value="recon">Reconciliation</option>
                  <option value="multi_file">Multi-File</option>
                </select>
              </div>
```

- [ ] **Step 5: Manual check**

Open the app, go to Jobs → Job Selections, open History on a selection that includes a `bo_report` job with at least two runs, pick two runs, pick that job in the new dropdown, choose "BO Report", click "Compare Selected", confirm it lands on the Compare tab's BO sub-tab with both sides set to `source_type: run`.

- [ ] **Step 6: Commit**

```bash
git add frontend/features/launch.js frontend/partials/tab-launch.html
git commit -m "feat: extend Job Selections History bridge beyond mismatch-diff"
```

---

## Phase 3 — e2e coverage

### Task 17: e2e tests for the three new entry points

**Files:**
- Modify: `tests/e2e/08g-compare-multi-file.spec.ts` (append — covers Job Catalog + run-reference)
- Reference: `tests/e2e/api-helpers.ts` (`createMultiFileJob`, `triggerRun`, `waitForTerminal`, `deleteJob`)

- [ ] **Step 1: Write the e2e test**

Append to `tests/e2e/08g-compare-multi-file.spec.ts`:

```typescript
test.describe('08g compare / job catalog run-reference', () => {
  let jobName: string;
  let runId: string;

  test.beforeAll(async ({ adminToken }) => {
    const { authedContext } = await import('./api-helpers');
    const ctx = await authedContext(adminToken);
    try {
      jobName = `e2e-mf-run-ref-${Date.now()}`;
      const { createMultiFileJob, triggerRun, waitForTerminal } = await import('./api-helpers');
      await createMultiFileJob(ctx, jobName);
      const { run_id } = await triggerRun(ctx, [jobName]);
      await waitForTerminal(ctx, run_id);
      runId = run_id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    if (!jobName) return;
    const { authedContext, deleteJob } = await import('./api-helpers');
    const ctx = await authedContext(adminToken);
    try {
      await deleteJob(ctx, jobName);
    } finally {
      await ctx.dispose();
    }
  });

  test('Job Catalog Compare button jumps to the multi-file sub-tab prefilled from the job', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-search-input"]').fill(jobName);
    await authedPage.locator(`[data-testid="job-row-${jobName}-compare-btn"]`).click();

    await expect(authedPage.locator('[data-testid="compare-subtab-multifile"]')).toHaveClass(/active/);
    await expect(authedPage.locator('[data-testid="compare-mf-key-columns-input"]')).toHaveValue('id');
  });
});
```

Check the actual `data-testid` on the multi_file sub-tab nav button (`compare-subtab-multifile` was inferred from `openMultiFile()`'s `.click()` target at the top of this file — confirm it matches exactly) and on the Jobs nav tab (`nav-tab-jobs`) before running; adjust if the real attribute differs.

- [ ] **Step 2: Run the test**

Run: `npm run test:e2e -- 08g-compare-multi-file`
Expected: all tests in the file PASS, including the new one

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/08g-compare-multi-file.spec.ts
git commit -m "test: add e2e coverage for Job Catalog compare run-reference entry point"
```

---

## Self-Review Notes

- **Spec coverage:** Task 3-4 cover BO run-reference; Task 9-11 cover multi-file run-reference (new persistence: Tasks 6-8); Task 5 covers the job-runs endpoint; reconciliation run-vs-run needed no backend task per the spec (already works via `ReconFileCompareRequest.stored_run_id`) — Task 14/16 wire it on the frontend only. Tasks 14-16 cover the three UI surfaces. Task 17 covers e2e; unit e2e coverage for Tasks 15-16 (Job modal, Selections bridge) is called out as manual-check-only in this pass — a fast follow-on can extend Task 17's pattern to them.
- **Placeholder scan:** no TBD/TODO; every step has real code or an exact command.
- **Type consistency:** `openCompareForJob(job, opts)` signature is identical across Tasks 13, 14, 15, 16. `_compareSubTabForJob` return values (`'bo' | 'multi_file' | 'recon' | null`) are used consistently in Task 15's `runInlineJobCompare`.
