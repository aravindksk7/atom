# Matrix Compare: Save as Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Save as Job" button to the Compare tab's Matrix sub-tab, wired through the existing `job_type: "compare"` machinery (third `compare_type: "matrix"`), so a Matrix comparison can be saved, edited, launched from the Job Catalog, scheduled, and its Full HTML Report contains real diff rows.

**Architecture:** Reuse the `job_type: "compare"` pattern already built for `compare_type` `"bo"`/`"recon_file"` (`docs/superpowers/specs/2026-08-12-compare-as-schedulable-job-design.md`). Four backend validation/dispatch/export sites each gain a `matrix` branch alongside their existing `bo`/`recon_file` branches; three frontend sites (save, edit-prefill, button) do the same. `compare_matrix()` (`api/services/compare_service.py:961`) is already a pure core — no wrapper split needed, unlike the original bo/recon_file work.

**Tech Stack:** FastAPI + Pydantic (backend), Alpine.js (frontend), pytest (unit tests), Playwright (e2e).

**Full design:** `docs/superpowers/specs/2026-08-30-matrix-compare-save-as-job-design.md`

---

## File Structure

| File | Change |
|---|---|
| `api/schemas.py` | `JobDefinition` validator: accept `compare_type: "matrix"`, validate via `MatrixCompareRequest`, reject upload sources |
| `etl_framework/runner/job_validation.py` | Same checks, mirrored (existing duplication pattern for job validation) |
| `api/services/run_executor.py` | `_build_case_compare`: dispatch `compare_type == "matrix"` to `service.compare_matrix(...)` |
| `api/services/difference_export.py` | `_write_compare_job`: matrix branch so Full HTML Report isn't empty for matrix jobs |
| `frontend/features/compare.js` | `_assertCompareJobSourcesAreRepeatable`, `_compareJobBody`, new `_hydrateMatrixSourceFromConfig` |
| `frontend/features/launch.js` | `_compareSubTabForJob`, `openCompareForJob`: matrix edit-prefill |
| `frontend/partials/tab-compare.html` | New `compare-matrix-save-job-btn` beside `btn-run-matrix-compare` |
| `tests/unit/test_compare_job_type.py` | `JobDefinition` matrix acceptance/rejection cases |
| `tests/unit/test_job_validation.py` | `job_validation.py` matrix acceptance/rejection cases |
| `tests/unit/test_run_executor_compare.py` | `_build_case_compare` matrix dispatch case |
| `tests/unit/test_difference_export.py` | `_write_compare_job` matrix case (non-empty diff rows) |
| `tests/e2e/43-live-docker-matrix-save-as-job.spec.ts` | New — save/launch, upload-rejection, edit round-trip, Full HTML Report |

---

### Task 1: `JobDefinition` accepts and validates `compare_type: "matrix"`

**Files:**
- Modify: `api/schemas.py:723-758`
- Test: `tests/unit/test_compare_job_type.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_compare_job_type.py`:

```python
def _matrix_request(**overrides) -> dict:
    request = {
        "source_a": {"source_type": "file", "file_path": "/data/a.csv"},
        "source_b": {"source_type": "sql", "config_id": 1, "query_or_table": "SELECT * FROM t"},
        "key_columns": ["id"],
    }
    return {**request, **overrides}


def test_compare_job_accepts_a_matrix_request_with_repeatable_sources():
    job = JobDefinition(
        name="nightly_matrix",
        job_type="compare",
        params={"compare_type": "matrix", "request": _matrix_request()},
    )

    assert job.params["compare_type"] == "matrix"


def test_compare_job_rejects_a_matrix_upload_source():
    with pytest.raises(ValidationError, match="Source B"):
        JobDefinition(
            name="nightly_matrix",
            job_type="compare",
            params={"compare_type": "matrix", "request": _matrix_request(
                source_b={"source_type": "file", "file_b64": "aWQK", "file_name": "b.csv"},
            )},
        )


def test_compare_job_mirrors_key_columns_from_a_matrix_request():
    job = JobDefinition(
        name="nightly_matrix",
        job_type="compare",
        params={"compare_type": "matrix", "request": _matrix_request(
            key_columns=["region", "product"],
        )},
    )

    assert job.key_columns == ["region", "product"]
```

`source_a`/`source_b` use `source_type: "file"` (with `file_path` set) rather than a BO-style `"path"` — Matrix's `DataSourceSpec` (`api/schemas.py:1235`) has no `"path"`/`"live"`/`"upload"` mode split like BO's `SourceConfig`; a file source is always `"file"`, distinguished as repeatable-or-not purely by whether `file_path` or `file_b64` is set.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_job_type.py -v -k matrix`
Expected: FAIL — `test_compare_job_accepts_a_matrix_request_with_repeatable_sources` and `test_compare_job_mirrors_key_columns_from_a_matrix_request` fail with `ValidationError: ... compare_type ...`; `test_compare_job_rejects_a_matrix_upload_source` fails because no `ValidationError` is raised at all (matrix isn't validated as a source-carrying type yet, and `"matrix"` isn't in the accepted tuple so it raises for the wrong reason before ever reaching the source check — confirm the failure message mentions `compare_type`, not `Source B`).

- [ ] **Step 3: Implement**

In `api/schemas.py`, replace lines 723-758:

```python
        elif self.job_type == "compare":
            compare_type = self.params.get("compare_type")
            if compare_type not in ("bo", "recon_file"):
                raise ValueError(
                    "compare jobs require params.compare_type of 'bo' or 'recon_file'"
                )
            request = self.params.get("request")
            if not isinstance(request, dict):
                raise ValueError(
                    "compare jobs require params.request holding the compare request body"
                )
            if compare_type == "bo":
                parsed_bo = BOCompareRequest.model_validate(request)
                for side, src in (("A", parsed_bo.source_a), ("B", parsed_bo.source_b)):
                    if src.source_type in ("upload", "run"):
                        raise ValueError(
                            f"compare job Source {side} uses a "
                            f"{'past run' if src.source_type == 'run' else 'file upload'}, "
                            "which cannot be re-run on a schedule - use a live, path, or api source"
                        )
                    if src.source_type == "live" and not (src.doc_id or parsed_bo.doc_id):
                        raise ValueError(
                            f"compare job Source {side} live source requires doc_id"
                        )
            else:
                parsed_file = ReconFileCompareRequest.model_validate(request)
                for side, stored, content in (
                    ("A", parsed_file.stored_run_id, parsed_file.file_a_content_b64),
                    ("B", parsed_file.stored_run_id_b, parsed_file.file_b_content_b64),
                ):
                    if stored or content:
                        raise ValueError(
                            f"compare job Source {side} uses a "
                            f"{'stored run' if stored else 'file upload'}, "
                            "which cannot be re-run on a schedule - use a file path"
                        )
```

with:

```python
        elif self.job_type == "compare":
            compare_type = self.params.get("compare_type")
            if compare_type not in ("bo", "recon_file", "matrix"):
                raise ValueError(
                    "compare jobs require params.compare_type of 'bo', 'recon_file', or 'matrix'"
                )
            request = self.params.get("request")
            if not isinstance(request, dict):
                raise ValueError(
                    "compare jobs require params.request holding the compare request body"
                )
            if compare_type == "bo":
                parsed_bo = BOCompareRequest.model_validate(request)
                for side, src in (("A", parsed_bo.source_a), ("B", parsed_bo.source_b)):
                    if src.source_type in ("upload", "run"):
                        raise ValueError(
                            f"compare job Source {side} uses a "
                            f"{'past run' if src.source_type == 'run' else 'file upload'}, "
                            "which cannot be re-run on a schedule - use a live, path, or api source"
                        )
                    if src.source_type == "live" and not (src.doc_id or parsed_bo.doc_id):
                        raise ValueError(
                            f"compare job Source {side} live source requires doc_id"
                        )
            elif compare_type == "recon_file":
                parsed_file = ReconFileCompareRequest.model_validate(request)
                for side, stored, content in (
                    ("A", parsed_file.stored_run_id, parsed_file.file_a_content_b64),
                    ("B", parsed_file.stored_run_id_b, parsed_file.file_b_content_b64),
                ):
                    if stored or content:
                        raise ValueError(
                            f"compare job Source {side} uses a "
                            f"{'stored run' if stored else 'file upload'}, "
                            "which cannot be re-run on a schedule - use a file path"
                        )
            else:
                parsed_matrix = MatrixCompareRequest.model_validate(request)
                for side, src in (("A", parsed_matrix.source_a), ("B", parsed_matrix.source_b)):
                    if src.source_type == "file" and src.file_b64 and not src.file_path:
                        raise ValueError(
                            f"compare job Source {side} is an upload, "
                            "which cannot be re-run on a schedule - use a file path or another repeatable source"
                        )
```

`MatrixCompareRequest` is defined later in the same module (`api/schemas.py:1252`) — this is the same forward-reference pattern already used for `BOCompareRequest` (defined at line 1107, referenced from this validator at line 624), which works because the validator method body only runs when a `JobDefinition` is instantiated, long after the whole module has finished loading. No new import needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_job_type.py -v`
Expected: PASS (all tests in the file, including the pre-existing bo/recon_file ones)

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_compare_job_type.py
git commit -m "feat: accept compare_type 'matrix' in the compare job validator"
```

---

### Task 2: Mirror the matrix validation in `job_validation.py`

**Files:**
- Modify: `etl_framework/runner/job_validation.py:1-9,234-241,292`
- Test: `tests/unit/test_job_validation.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_job_validation.py`:

```python
def test_compare_matrix_job_reports_no_errors_for_repeatable_sources():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_matrix",
        "job_type": "compare",
        "params": {
            "compare_type": "matrix",
            "request": {
                "source_a": {"source_type": "file", "file_path": "/data/a.csv"},
                "source_b": {"source_type": "sql", "config_id": 1, "query_or_table": "SELECT * FROM t"},
            },
        },
    })

    assert [i for i in issues if i.severity == ValidationSeverity.ERROR] == []


def test_compare_matrix_job_with_upload_source_reports_an_error():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_matrix",
        "job_type": "compare",
        "params": {
            "compare_type": "matrix",
            "request": {
                "source_a": {"source_type": "file", "file_path": "/data/a.csv"},
                "source_b": {"source_type": "file", "file_b64": "aWQK", "file_name": "b.csv"},
            },
        },
    })

    errors = [i for i in issues if i.severity == ValidationSeverity.ERROR]
    assert any(i.field == "params.request.source_b" and "upload" in i.message for i in errors)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_job_validation.py -v -k matrix`
Expected: FAIL — first test fails because `compare_type not in ("bo", "recon_file")` reports an error for `"matrix"`; second fails because no `params.request.source_b` issue is reported (the matrix branch doesn't exist yet).

- [ ] **Step 3: Implement**

In `etl_framework/runner/job_validation.py`, change line 9:

```python
from api.schemas import BOCompareRequest, ReconFileCompareRequest
```

to:

```python
from api.schemas import BOCompareRequest, MatrixCompareRequest, ReconFileCompareRequest
```

Then replace lines 234-241:

```python
    elif job_type == "compare":
        compare_type = params.get("compare_type")
        request = params.get("request")
        if compare_type not in ("bo", "recon_file"):
            issues.append(ValidationIssue(
                "params.compare_type",
                "compare jobs require compare_type of 'bo' or 'recon_file'",
            ))
```

with:

```python
    elif job_type == "compare":
        compare_type = params.get("compare_type")
        request = params.get("request")
        if compare_type not in ("bo", "recon_file", "matrix"):
            issues.append(ValidationIssue(
                "params.compare_type",
                "compare jobs require compare_type of 'bo', 'recon_file', or 'matrix'",
            ))
```

Then insert a new `elif compare_type == "matrix":` branch right after the existing `elif compare_type == "recon_file":` block ends (immediately before the `for field in ("rules", "pass_condition", "depends_on"):` loop, currently at line 292):

```python
        elif compare_type == "matrix":
            try:
                parsed_matrix = MatrixCompareRequest.model_validate(request)
            except ValidationError as exc:
                issues.append(ValidationIssue(
                    "params.request",
                    f"compare matrix request is invalid: {_validation_error_message(exc)}",
                ))
            else:
                for field, source, label in (
                    ("params.request.source_a", parsed_matrix.source_a, "Source A"),
                    ("params.request.source_b", parsed_matrix.source_b, "Source B"),
                ):
                    if source.source_type == "file" and source.file_b64 and not source.file_path:
                        issues.append(ValidationIssue(
                            field,
                            f"compare job {label} is an upload, which cannot be re-run on a "
                            "schedule - use a file path or another repeatable source",
                        ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_job_validation.py -v`
Expected: PASS (all tests in the file, including the pre-existing bo/recon_file ones)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/runner/job_validation.py tests/unit/test_job_validation.py
git commit -m "feat: mirror matrix compare-job validation in job_validation.py"
```

---

### Task 3: `RunExecutor` dispatches `compare_type: "matrix"` to `compare_matrix()`

**Files:**
- Modify: `api/services/run_executor.py:1925-1957`
- Test: `tests/unit/test_run_executor_compare.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_run_executor_compare.py`:

```python
def test_compare_job_runs_a_matrix_compare_and_names_the_result_after_the_job(tmp_path, monkeypatch):
    _allow(tmp_path, monkeypatch)
    (tmp_path / "a.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,value\n1,beta\n", encoding="utf-8")

    job = JobDefinition(
        name="nightly_matrix",
        job_type="compare",
        params={"compare_type": "matrix", "request": {
            "source_a": {"source_type": "file", "file_path": str(tmp_path / "a.csv")},
            "source_b": {"source_type": "file", "file_path": str(tmp_path / "b.csv")},
            "key_columns": ["id"],
        }},
    )

    result = _executor(_session())._build_case(job)()

    assert result.query_name == "nightly_matrix"
    assert result.value_mismatch_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_run_executor_compare.py -v -k matrix`
Expected: FAIL with `ValueError: unknown compare_type 'matrix' for compare job 'nightly_matrix'`

- [ ] **Step 3: Implement**

In `api/services/run_executor.py`, inside `_build_case_compare`, change:

```python
            from api.schemas import BOCompareRequest, ReconFileCompareRequest
```

to:

```python
            from api.schemas import BOCompareRequest, MatrixCompareRequest, ReconFileCompareRequest
```

and replace:

```python
            elif compare_type == "recon_file":
                result = service.compare_recon_file(
                    ReconFileCompareRequest.model_validate(request), job_name=job.name,
                )
            else:
                raise ValueError(
                    f"unknown compare_type '{compare_type}' for compare job '{job.name}'"
                )
```

with:

```python
            elif compare_type == "recon_file":
                result = service.compare_recon_file(
                    ReconFileCompareRequest.model_validate(request), job_name=job.name,
                )
            elif compare_type == "matrix":
                result = service.compare_matrix(MatrixCompareRequest.model_validate(request))
            else:
                raise ValueError(
                    f"unknown compare_type '{compare_type}' for compare job '{job.name}'"
                )
```

`compare_matrix()` (`api/services/compare_service.py:961`) takes only `req` — no `run_id`/`job_name` argument like `compare_bo`/`compare_recon_file` need, because it doesn't persist a BO-pull artifact or look up a stored run.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_run_executor_compare.py -v`
Expected: PASS (all tests in the file, including the pre-existing bo/recon_file/unknown-type ones)

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_executor_compare.py
git commit -m "feat: dispatch compare_type 'matrix' jobs to CompareService.compare_matrix"
```

---

### Task 4: Full HTML Report / differences export covers matrix compare jobs

**Files:**
- Modify: `api/services/difference_export.py:17-23,29-31,640-676`
- Test: `tests/unit/test_difference_export.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_difference_export.py`:

```python
def test_write_reconciliation_run_recomputes_a_matrix_compare_job_instead_of_skipping_it(tmp_path, monkeypatch):
    """Same regression class as the recon_file case above, for compare_type
    'matrix': without a matrix branch in _write_compare_job, a matrix job's
    Full HTML Report / differences export produces zero rows even when the
    run itself found real mismatches."""
    from datetime import datetime, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from api.services import difference_export as de
    from api.services import file_source
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.database import Base
    from etl_framework.repository.models import TestRun
    from etl_framework.repository.repository import JobRepository, RunRepository
    from etl_framework.runner.state import TestStatus

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    src = tmp_path / "src.csv"
    tgt = tmp_path / "tgt.csv"
    src.write_text("id,amount\n1,10\n2,20\n", encoding="utf-8")
    tgt.write_text("id,amount\n1,10\n2,99\n", encoding="utf-8")
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    with _db_module.SessionLocal() as db:
        JobRepository(db).create({
            "name": "matrix_compare_job",
            "description": "",
            "tags": [],
            "job_type": "compare",
            "query": "",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None, "target_env": None,
            "params": {
                "compare_type": "matrix",
                "request": {
                    "source_a": {"source_type": "file", "file_path": str(src)},
                    "source_b": {"source_type": "file", "file_path": str(tgt)},
                    "label_a": "Source A",
                    "label_b": "Source B",
                    "key_columns": ["id"],
                    "exclude_columns": [],
                },
            },
            "enabled": True,
        })

        repo = RunRepository(db)
        run = repo.create_run(
            run_id="run-matrix-compare-job-export",
            source_env="qa",
            target_env="prod",
            config_snapshot={
                "compare_request_type": "unknown",
                "request": {},
                "job_sequence": ["matrix_compare_job"],
            },
        )
        repo.add_test_result(run.run_id, ReconciliationResult(
            query_name="matrix_compare_job",
            source_env="qa",
            target_env="prod",
            source_row_count=2,
            target_row_count=2,
            matched_count=1,
            missing_in_target_count=0,
            missing_in_source_count=0,
            value_mismatch_count=1,
            mismatches=[],
            status=TestStatus.FAILED,
            executed_at=datetime.now(timezone.utc),
            duration_seconds=0.1,
        ))
        run_id = run.run_id

    out_path = tmp_path / "matrix_diffs.jsonl"
    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        row_count = de.write_recomputed_differences(db, run, "json", out_path)

    lines = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert row_count == len(lines) == 1
    row = json.loads(lines[0])
    assert row["test_name"] == "matrix_compare_job"
    assert row["column_name"] == "amount"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_difference_export.py -v -k matrix_compare_job`
Expected: FAIL — `row_count == 0` (the `_write_compare_job` `else: return` branch silently produces nothing for `compare_type == "matrix"`)

- [ ] **Step 3: Implement**

In `api/services/difference_export.py`, change the `from api.schemas import (...)` block (lines 17-23):

```python
from api.schemas import (
    BOCompareRequest,
    DifferenceExportStatusOut,
    ReconFileCompareRequest,
    RunSettings,
    SQLCompareRequest,
)
```

to:

```python
from api.schemas import (
    AdvancedCompareOptions,
    BOCompareRequest,
    DifferenceExportStatusOut,
    MatrixCompareRequest,
    ReconFileCompareRequest,
    RunSettings,
    SQLCompareRequest,
)
```

Add an import for `extract_data_source` right after the existing `etl_framework.reconciliation.compare_utils` import block (currently lines 31-36):

```python
from etl_framework.reconciliation.compare_utils import (
    normalize_string_columns,
    numeric_delta,
    resolve_key_columns,
    value_mismatch_mask,
)
from etl_framework.reconciliation.data_sources import extract_data_source
```

Then replace `_write_compare_job` (lines 640-676):

```python
def _write_compare_job(db: Session, saved: SavedJob, writer: DifferenceWriter) -> None:
    """Recompute a job saved via the Compare tab's "Save as Job" button
    (job_type == 'compare') from its own params, the same way a
    'compare_request_type'-tagged ad-hoc run does. Job-executed compare runs
    never get that snapshot key set (only the ad-hoc /api/compare/* endpoints
    do), so without this the caller's job_type == 'reconciliation' filter
    would silently drop the job and export zero rows for it.
    """
    params = saved.params or {}
    compare_type = params.get("compare_type")
    payload = dict(params.get("request") or {})
    svc = CompareService(db, ConfigRepository(db))
    if compare_type == "bo":
        req = BOCompareRequest(**payload)
        df_a = svc._load_bo_source(req.source_a, req.doc_id, req.report_id, store_responses=False)
        df_b = svc._load_bo_source(req.source_b, req.doc_id, req.report_id, store_responses=False)
    elif compare_type == "recon_file":
        req = ReconFileCompareRequest(**payload)
        source_a = svc._load_recon_source(req, "a")
        source_b = svc._load_recon_source(req, "b")
        if not (isinstance(source_a, pd.DataFrame) and isinstance(source_b, pd.DataFrame)):
            # Report-shaped (HTML/stored-run stats) sources compare test-by-test,
            # not row-by-row -- there is no tabular difference set to add here.
            return
        df_a, df_b = source_a, source_b
    else:
        return
    _write_tabular_differences(
        df_a,
        df_b,
        key_columns=req.key_columns or [],
        exclude_columns=req.exclude_columns or [],
        options=req.advanced,
        test_name=saved.name,
        writer=writer,
    )
```

with:

```python
def _write_compare_job(db: Session, saved: SavedJob, writer: DifferenceWriter) -> None:
    """Recompute a job saved via the Compare tab's "Save as Job" button
    (job_type == 'compare') from its own params, the same way a
    'compare_request_type'-tagged ad-hoc run does. Job-executed compare runs
    never get that snapshot key set (only the ad-hoc /api/compare/* endpoints
    do), so without this the caller's job_type == 'reconciliation' filter
    would silently drop the job and export zero rows for it.
    """
    params = saved.params or {}
    compare_type = params.get("compare_type")
    payload = dict(params.get("request") or {})
    svc = CompareService(db, ConfigRepository(db))
    if compare_type == "bo":
        req = BOCompareRequest(**payload)
        df_a = svc._load_bo_source(req.source_a, req.doc_id, req.report_id, store_responses=False)
        df_b = svc._load_bo_source(req.source_b, req.doc_id, req.report_id, store_responses=False)
        options = req.advanced
    elif compare_type == "recon_file":
        req = ReconFileCompareRequest(**payload)
        source_a = svc._load_recon_source(req, "a")
        source_b = svc._load_recon_source(req, "b")
        if not (isinstance(source_a, pd.DataFrame) and isinstance(source_b, pd.DataFrame)):
            # Report-shaped (HTML/stored-run stats) sources compare test-by-test,
            # not row-by-row -- there is no tabular difference set to add here.
            return
        df_a, df_b = source_a, source_b
        options = req.advanced
    elif compare_type == "matrix":
        req = MatrixCompareRequest(**payload)
        df_a = extract_data_source(req.source_a.model_dump(), db)
        df_b = extract_data_source(req.source_b.model_dump(), db)
        # Mirrors CompareService.compare_matrix's own AdvancedCompareOptions
        # construction (compare_service.py:986) -- Matrix has no .advanced
        # field of its own, just numeric_tolerance/ignore_case/trim_whitespace.
        compare_cols = list(set(df_a.columns).union(set(df_b.columns)))
        options = AdvancedCompareOptions(
            float_tolerance=req.numeric_tolerance if req.numeric_tolerance > 0 else 1e-9,
            case_insensitive_columns=compare_cols if req.ignore_case else [],
            whitespace_normalize_columns=compare_cols if req.trim_whitespace else [],
        )
    else:
        return
    _write_tabular_differences(
        df_a,
        df_b,
        key_columns=req.key_columns or [],
        exclude_columns=req.exclude_columns or [],
        options=options,
        test_name=saved.name,
        writer=writer,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_difference_export.py -v`
Expected: PASS (all tests in the file, including the pre-existing bo/recon_file compare-job export ones)

- [ ] **Step 5: Commit**

```bash
git add api/services/difference_export.py tests/unit/test_difference_export.py
git commit -m "fix: recompute differences for job-executed matrix compare runs"
```

---

### Task 5: Frontend — Matrix payload builds a saveable job body

**Files:**
- Modify: `frontend/features/compare.js:623-643,645-670`

No standalone unit test framework exists for this frontend (`package.json` only has `test:e2e`); this task's correctness is verified by the e2e spec in Task 9. Keep the diff small and mirror the existing bo/recon_file code exactly.

- [ ] **Step 1: Implement `_assertCompareJobSourcesAreRepeatable`**

In `frontend/features/compare.js`, replace:

```javascript
    _assertCompareJobSourcesAreRepeatable(compareType, payload) {
      if (compareType === 'bo') {
        [['A', payload.source_a], ['B', payload.source_b]].forEach(([side, src]) => {
          if (!src) return;
          if (src.source_type === 'upload' || src.source_type === 'run') {
            const what = src.source_type === 'upload' ? 'an upload' : 'a past run';
            throw new Error(`Source ${side} is ${what} - a job that re-runs needs a live, path, or API source.`);
          }
        });
        return;
      }
      [
        ['A', payload.stored_run_id, payload.file_a_content_b64],
        ['B', payload.stored_run_id_b, payload.file_b_content_b64],
      ].forEach(([side, storedRun, content]) => {
        if (storedRun || content) {
          const what = storedRun ? 'a stored run' : 'an upload';
          throw new Error(`Source ${side} is ${what} - a job that re-runs needs a file path.`);
        }
      });
    },
```

with:

```javascript
    _assertCompareJobSourcesAreRepeatable(compareType, payload) {
      if (compareType === 'bo') {
        [['A', payload.source_a], ['B', payload.source_b]].forEach(([side, src]) => {
          if (!src) return;
          if (src.source_type === 'upload' || src.source_type === 'run') {
            const what = src.source_type === 'upload' ? 'an upload' : 'a past run';
            throw new Error(`Source ${side} is ${what} - a job that re-runs needs a live, path, or API source.`);
          }
        });
        return;
      }
      if (compareType === 'matrix') {
        [['A', payload.source_a], ['B', payload.source_b]].forEach(([side, src]) => {
          if (!src) return;
          if (src.source_type === 'file' && src.file_b64 && !src.file_path) {
            throw new Error(`Source ${side} is an upload - a job that re-runs needs a file path or another repeatable source.`);
          }
        });
        return;
      }
      [
        ['A', payload.stored_run_id, payload.file_a_content_b64],
        ['B', payload.stored_run_id_b, payload.file_b_content_b64],
      ].forEach(([side, storedRun, content]) => {
        if (storedRun || content) {
          const what = storedRun ? 'a stored run' : 'an upload';
          throw new Error(`Source ${side} is ${what} - a job that re-runs needs a file path.`);
        }
      });
    },
```

- [ ] **Step 2: Implement `_compareJobBody`**

Replace:

```javascript
    _compareJobBody() {
      // Multi-file saves as the reconciliation/multi_file job that already runs
      // and already schedules - not as a `compare` job.
      if (this.saveJobCompareType === 'multi_file') {
        const payload = this._buildMultiFilePayload();
        if (payload.run_id) {
          throw new Error('A run-reference multi-file compare cannot be saved as a job - pick source and target roots instead.');
        }
        return {
          job_type: 'reconciliation',
          key_columns: payload.key_columns || [],
          exclude_columns: payload.exclude_columns || [],
          params: { source_mode: 'multi_file', file_mapping: payload.file_mapping },
        };
      }
      const payload = this.saveJobCompareType === 'bo'
        ? this._buildBOComparePayload()
        : this._buildReconFilePayload();
      this._assertCompareJobSourcesAreRepeatable(this.saveJobCompareType, payload);
      return {
        job_type: 'compare',
        key_columns: payload.key_columns || [],
        exclude_columns: payload.exclude_columns || [],
        params: { compare_type: this.saveJobCompareType, request: payload },
      };
    },
```

with:

```javascript
    _compareJobBody() {
      // Multi-file saves as the reconciliation/multi_file job that already runs
      // and already schedules - not as a `compare` job.
      if (this.saveJobCompareType === 'multi_file') {
        const payload = this._buildMultiFilePayload();
        if (payload.run_id) {
          throw new Error('A run-reference multi-file compare cannot be saved as a job - pick source and target roots instead.');
        }
        return {
          job_type: 'reconciliation',
          key_columns: payload.key_columns || [],
          exclude_columns: payload.exclude_columns || [],
          params: { source_mode: 'multi_file', file_mapping: payload.file_mapping },
        };
      }
      if (this.saveJobCompareType === 'matrix') {
        const payload = this._buildMatrixComparePayload();
        this._assertCompareJobSourcesAreRepeatable('matrix', payload);
        return {
          job_type: 'compare',
          key_columns: payload.key_columns || [],
          exclude_columns: payload.exclude_columns || [],
          params: { compare_type: 'matrix', request: payload },
        };
      }
      const payload = this.saveJobCompareType === 'bo'
        ? this._buildBOComparePayload()
        : this._buildReconFilePayload();
      this._assertCompareJobSourcesAreRepeatable(this.saveJobCompareType, payload);
      return {
        job_type: 'compare',
        key_columns: payload.key_columns || [],
        exclude_columns: payload.exclude_columns || [],
        params: { compare_type: this.saveJobCompareType, request: payload },
      };
    },
```

`_buildMatrixComparePayload()` already exists (`compare.js:1032`, used by `runMatrixCompare()`) and needs no changes — it already returns the exact `MatrixCompareRequest` shape the backend validator expects.

- [ ] **Step 3: Commit**

```bash
git add frontend/features/compare.js
git commit -m "feat: build a saveable job body for Matrix compare"
```

---

### Task 6: Frontend — hydrate a saved matrix job back into the form (edit flow)

**Files:**
- Modify: `frontend/features/compare.js` (new method near `_hydrateBOSourceFromConfig`, line 474)

- [ ] **Step 1: Implement `_hydrateMatrixSourceFromConfig`**

In `frontend/features/compare.js`, immediately after the closing `},` of `_hydrateBOSourceFromConfig` (line 474, right before the `// Reverse of _buildAdvanced...` comment at line 476), insert:

```javascript
    // Reverse of _buildMatrixSourceSpec -- turns a saved compare job's
    // DataSourceSpec (params.request.source_a/source_b) back into
    // matrixSourceAType/matrixSourceA shape. Unlike BO, Matrix's UI type
    // strings ('sql'/'file'/'aws_athena'/'sap_bo'/'api') already equal
    // DataSourceSpec.source_type values directly (_buildMatrixSourceSpec sets
    // spec.source_type = type verbatim), so this is a near-identity mapping,
    // and -- like BO -- only ever needs to handle repeatable sources, since
    // _assertCompareJobSourcesAreRepeatable rejects an upload at save time.
    _hydrateMatrixSourceFromConfig(cfg) {
      const base = { configId: '', connectionName: '', queryOrTable: '', filePath: '', fileB64: '', fileName: '', athenaQuery: '', docId: '', reportId: '', endpointUrl: '', httpMethod: 'GET', label: '' };
      if (!cfg) return { type: 'file', src: base };
      const type = cfg.source_type || 'file';
      if (type === 'sql') {
        return { type, src: { ...base, configId: cfg.config_id ?? '', connectionName: cfg.connection_name || '', queryOrTable: cfg.query_or_table || '' } };
      }
      if (type === 'aws_athena') {
        return { type, src: { ...base, configId: cfg.config_id ?? '', athenaQuery: cfg.query_or_table || '' } };
      }
      if (type === 'sap_bo') {
        return { type, src: { ...base, configId: cfg.config_id ?? '', docId: cfg.bo_doc_id || '', reportId: cfg.bo_report_id || '' } };
      }
      if (type === 'api') {
        return { type, src: { ...base, endpointUrl: cfg.endpoint_url || '', httpMethod: cfg.http_method || 'GET' } };
      }
      return { type: 'file', src: { ...base, filePath: cfg.file_path || '' } };
    },

```

- [ ] **Step 2: Commit**

```bash
git add frontend/features/compare.js
git commit -m "feat: hydrate a saved matrix compare job's sources for editing"
```

---

### Task 7: Frontend — Job Catalog edit routes a matrix job back to the Matrix sub-tab

**Files:**
- Modify: `frontend/features/launch.js:1323-1333,1352-1376`

- [ ] **Step 1: Extend `_compareSubTabForJob`**

In `frontend/features/launch.js`, replace:

```javascript
    _compareSubTabForJob(job) {
      if (job.job_type === 'bo_report') return 'bo';
      if (job.job_type === 'reconciliation' && job.params?.source_mode === 'multi_file') return 'multi_file';
      if (job.job_type === 'reconciliation') return 'recon';
      if (job.job_type === 'compare') {
        if (job.params?.compare_type === 'bo') return 'bo';
        if (job.params?.compare_type === 'recon_file') return 'recon';
        return null;
      }
      return null;
    },
```

with:

```javascript
    _compareSubTabForJob(job) {
      if (job.job_type === 'bo_report') return 'bo';
      if (job.job_type === 'reconciliation' && job.params?.source_mode === 'multi_file') return 'multi_file';
      if (job.job_type === 'reconciliation') return 'recon';
      if (job.job_type === 'compare') {
        if (job.params?.compare_type === 'bo') return 'bo';
        if (job.params?.compare_type === 'recon_file') return 'recon';
        if (job.params?.compare_type === 'matrix') return 'matrix';
        return null;
      }
      return null;
    },
```

- [ ] **Step 2: Add the matrix prefill branch in `openCompareForJob`**

In `frontend/features/launch.js`, insert a new `else if` branch right after the existing `} else if (job.job_type === 'compare' && subTab === 'recon') { ... }` block ends and right before `} else if (subTab === 'bo') {`. The block to change is:

```javascript
      } else if (job.job_type === 'compare' && subTab === 'recon') {
        const req = job.params?.request || {};
        this.reconMode = 'file';
        this.fileSourceAType = 'path';
        this.filePathA = req.file_a_path || ''; this.fileB64A = ''; this.fileNameA = '';
        this.fileSourceBType = 'path';
        this.filePathB = req.file_b_path || ''; this.fileB64B = ''; this.fileNameB = '';
        this.fileLabelA = req.label_a || 'Source A';
        this.fileLabelB = req.label_b || 'Production Report';
        this.fileCompareKeyColumns = (req.key_columns || []).join(', ');
        this.fileCompareExcludeColumns = (req.exclude_columns || []).join(', ');
        this._applyAdvancedToPrefix('file', req.advanced);
      } else if (subTab === 'bo') {
```

Replace it with:

```javascript
      } else if (job.job_type === 'compare' && subTab === 'recon') {
        const req = job.params?.request || {};
        this.reconMode = 'file';
        this.fileSourceAType = 'path';
        this.filePathA = req.file_a_path || ''; this.fileB64A = ''; this.fileNameA = '';
        this.fileSourceBType = 'path';
        this.filePathB = req.file_b_path || ''; this.fileB64B = ''; this.fileNameB = '';
        this.fileLabelA = req.label_a || 'Source A';
        this.fileLabelB = req.label_b || 'Production Report';
        this.fileCompareKeyColumns = (req.key_columns || []).join(', ');
        this.fileCompareExcludeColumns = (req.exclude_columns || []).join(', ');
        this._applyAdvancedToPrefix('file', req.advanced);
      } else if (job.job_type === 'compare' && subTab === 'matrix') {
        const req = job.params?.request || {};
        const a = this._hydrateMatrixSourceFromConfig(req.source_a);
        const b = this._hydrateMatrixSourceFromConfig(req.source_b);
        this.matrixSourceAType = a.type;
        this.matrixSourceA = { ...a.src, label: req.label_a || 'Source A' };
        this.matrixSourceBType = b.type;
        this.matrixSourceB = { ...b.src, label: req.label_b || 'Source B' };
        this.matrixKeyColumns = (req.key_columns || []).join(', ');
        this.matrixExcludeColumns = (req.exclude_columns || []).join(', ');
        this.matrixNumericTolerance = req.numeric_tolerance != null ? String(req.numeric_tolerance) : '0.0';
        this.matrixIgnoreCase = Boolean(req.ignore_case);
        this.matrixTrimWhitespace = req.trim_whitespace !== false;
      } else if (subTab === 'bo') {
```

- [ ] **Step 3: Commit**

```bash
git add frontend/features/launch.js
git commit -m "feat: route Job Catalog edit for a matrix compare job to the Matrix sub-tab"
```

---

### Task 8: Frontend — "Save as Job" button on the Matrix sub-tab

**Files:**
- Modify: `frontend/partials/tab-compare.html:1716-1722`

- [ ] **Step 1: Add the button**

In `frontend/partials/tab-compare.html`, replace:

```html
      <div class="flex justify-end">
        <button id="btn-run-matrix-compare" data-testid="btn-run-matrix-compare" @click="runMatrixCompare()" :disabled="matrixCompareLoading" class="btn-primary">
          <span x-show="!matrixCompareLoading">Run Matrix Compare</span>
          <span x-show="matrixCompareLoading">Comparing...</span>
        </button>
      </div>
```

with:

```html
      <div class="flex justify-end gap-2">
        <button id="btn-run-matrix-compare" data-testid="btn-run-matrix-compare" @click="runMatrixCompare()" :disabled="matrixCompareLoading" class="btn-primary">
          <span x-show="!matrixCompareLoading">Run Matrix Compare</span>
          <span x-show="matrixCompareLoading">Comparing...</span>
        </button>
        <button data-testid="compare-matrix-save-job-btn" @click="openSaveCompareAsJob('matrix')"
                class="btn-secondary btn-sm text-xs">Save as Job</button>
      </div>
```

- [ ] **Step 2: Manually sanity-check the button renders**

Run: `python -m uvicorn api.main:app --host 127.0.0.1 --port 8010` (from repo root, separate terminal), then open `http://127.0.0.1:8010/#compare` in a browser, click the "Matrix" sub-tab.
Expected: a "Save as Job" button appears to the right of "Run Matrix Compare"; clicking it opens the "Save Compare as Job" modal (same modal BO/recon-file/multi-file already use). Stop the server (Ctrl+C) once confirmed.

- [ ] **Step 3: Commit**

```bash
git add frontend/partials/tab-compare.html
git commit -m "feat: add Save as Job button to the Matrix compare sub-tab"
```

---

### Task 9: E2E — save, launch, reject-upload, edit round-trip, Full HTML Report

**Files:**
- Create: `tests/e2e/43-live-docker-matrix-save-as-job.spec.ts`

Named `43-live-docker-matrix-save-as-job.spec.ts` to sit alongside `42-live-docker-matrix-reconciliation.spec.ts` (the existing ad-hoc Matrix e2e coverage) and follow this repo's numbering convention for Matrix-tab specs. Despite the "live-docker" filename prefix used by specs 38-42, none of them actually gate on `E2E_LIVE_BACKENDS` or invoke `docker compose` (verified: no `E2E_LIVE_BACKENDS`/`docker compose`/`execSync` in any of 38-42) — the prefix is this repo's naming convention for "runs against the real backend through the browser," not literally requiring Docker. This spec follows the same convention: plain `npx playwright test`, no live-backend flag, mirroring `26-compare-save-as-job.spec.ts`'s structure and this project's `source.csv`/`target.csv` fixture pair (`id`=2 amount differs, `id`=3 missing in target, `id`=4 missing in source — same pair `42-live-docker-matrix-reconciliation.spec.ts` and `api-helpers.ts`'s `createFileJob()` already rely on).

- [ ] **Step 1: Write the spec file**

```typescript
// tests/e2e/43-live-docker-matrix-save-as-job.spec.ts
import { test, expect } from './fixtures';
import path from 'node:path';
import { authedContext, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');
const JOB_NAME = 'e2e_saved_matrix_compare';
const REPORT_JOB_NAME = 'e2e_saved_matrix_compare_report';

async function openMatrixCompare(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-matrix"]').click();
}

test.describe('43 compare / matrix save as job', () => {
  test.afterEach(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    await deleteJob(ctx, JOB_NAME);
    await deleteJob(ctx, REPORT_JOB_NAME);
    await ctx.dispose();
  });

  test('saves and launches a path-vs-path Matrix compare job', async ({ authedPage, adminToken }) => {
    await openMatrixCompare(authedPage);

    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'source.csv'));
    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'target.csv'));
    await authedPage.locator('[data-testid="matrix-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="compare-matrix-save-job-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeVisible();
    await authedPage.locator('[data-testid="compare-save-job-name"]').fill(JOB_NAME);
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeHidden();

    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await expect(authedPage.locator(`[data-testid="job-row-${JOB_NAME}"]`)).toBeVisible();

    const ctx = await authedContext(adminToken);
    try {
      const { run_id } = await triggerRun(ctx, [JOB_NAME]);
      const terminal = await waitForTerminal(ctx, run_id, 60_000);
      expect(String(terminal.status).toUpperCase()).not.toBe('ERROR');
    } finally {
      await ctx.dispose();
    }
  });

  test('refuses to save a Matrix compare whose source is an upload', async ({ authedPage }) => {
    await openMatrixCompare(authedPage);

    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'source.csv'));
    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-upload-input"]')
      .setInputFiles(path.join(FIXTURE_DIR, 'target.csv'));

    await authedPage.locator('[data-testid="compare-matrix-save-job-btn"]').click();
    await authedPage.locator('[data-testid="compare-save-job-name"]').fill(JOB_NAME);
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();

    await expect(authedPage.locator('[data-testid="compare-save-job-error"]')).toContainText('Source B is an upload');
  });

  test('editing a saved matrix compare job reflects and persists key/exclude columns', async ({ authedPage, adminToken }) => {
    await openMatrixCompare(authedPage);

    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'source.csv'));
    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'target.csv'));
    await authedPage.locator('[data-testid="matrix-key-columns-input"]').fill('id');
    await expect(authedPage.locator('[data-testid="matrix-exclude-columns-input"]')).toHaveValue('');

    await authedPage.locator('[data-testid="compare-matrix-save-job-btn"]').click();
    await authedPage.locator('[data-testid="compare-save-job-name"]').fill(JOB_NAME);
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeHidden();

    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await expect(authedPage.locator(`[data-testid="job-row-${JOB_NAME}"]`)).toBeVisible();

    await authedPage.locator(`[data-testid="job-row-${JOB_NAME}-edit-btn"]`).click();
    await expect(authedPage.locator('[data-testid="compare-subtab-matrix"]')).toHaveClass(/active/);
    await expect(authedPage.locator('[data-testid="compare-matrix-source-a-path-input"]')).toHaveValue(path.join(FIXTURE_DIR, 'source.csv'));
    await expect(authedPage.locator('[data-testid="compare-matrix-source-b-path-input"]')).toHaveValue(path.join(FIXTURE_DIR, 'target.csv'));
    await expect(authedPage.locator('[data-testid="matrix-key-columns-input"]')).toHaveValue('id');
    await expect(authedPage.locator('[data-testid="matrix-exclude-columns-input"]')).toHaveValue('');

    await authedPage.locator('[data-testid="matrix-exclude-columns-input"]').fill('amount');
    await authedPage.locator('[data-testid="compare-matrix-save-job-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-editing-note"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="compare-save-job-name"]')).toHaveValue(JOB_NAME);
    await expect(authedPage.locator('[data-testid="compare-save-job-name"]')).toBeDisabled();
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeHidden();

    const ctx = await authedContext(adminToken);
    try {
      const jobsResp = await ctx.get('/api/jobs');
      expect(jobsResp.ok()).toBeTruthy();
      const jobs = await jobsResp.json();
      const job = jobs.find((j: { name: string }) => j.name === JOB_NAME);
      expect(job).toBeTruthy();
      expect(job.key_columns).toEqual(['id']);
      expect(job.exclude_columns).toEqual(['amount']);

      // Functional proof: source.csv/target.csv differ on `amount` for id=2 --
      // excluding that column must make the value_diff disappear while id=3/id=4
      // (missing rows, unaffected by exclude_columns) remain.
      const { run_id } = await triggerRun(ctx, [JOB_NAME]);
      await waitForTerminal(ctx, run_id, 60_000);
      const runResp = await ctx.get(`/api/runs/${run_id}`);
      const run = await runResp.json();
      const result = run.results.find((r: { query_name: string }) => r.query_name === JOB_NAME);
      expect(result).toBeTruthy();
      expect(result.value_mismatch_count).toBe(0);
      expect(result.missing_in_target_count).toBe(1);
      expect(result.missing_in_source_count).toBe(1);
    } finally {
      await ctx.dispose();
    }
  });

  test('a matrix job saved via Save as Job downloads a non-empty Full HTML Report', async ({ authedPage, adminToken }) => {
    await openMatrixCompare(authedPage);
    await authedPage.locator('[data-testid="compare-matrix-source-a-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-a-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'source.csv'));
    await authedPage.locator('[data-testid="compare-matrix-source-b-mode-file"]').click();
    await authedPage.locator('[data-testid="compare-matrix-source-b-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'target.csv'));
    await authedPage.locator('[data-testid="matrix-key-columns-input"]').fill('id');

    await authedPage.locator('[data-testid="compare-matrix-save-job-btn"]').click();
    await authedPage.locator('[data-testid="compare-save-job-name"]').fill(REPORT_JOB_NAME);
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeHidden();

    const ctx = await authedContext(adminToken);
    try {
      const { run_id } = await triggerRun(ctx, [REPORT_JOB_NAME]);
      await waitForTerminal(ctx, run_id, 60_000);

      const jobResp = await ctx.post(`/api/runs/${run_id}/exports`, { data: { format: 'html' } });
      const exportJob = await jobResp.json();
      expect(exportJob.status).toBe('COMPLETED');
      expect(exportJob.row_count).toBeGreaterThan(0);
      const artifactResp = await ctx.get(`/api/runs/${run_id}/exports/${exportJob.export_id}/download`);
      expect(artifactResp.ok()).toBeTruthy();
      const html = await artifactResp.text();
      expect(html).toContain('data-mismatch');
    } finally {
      await ctx.dispose();
    }
  });
});
```

- [ ] **Step 2: Run the new spec**

Run: `rtk proxy npx playwright test tests/e2e/43-live-docker-matrix-save-as-job.spec.ts`

(per this session's memory: `rtk`'s default output wrapping mangles Playwright's own reporter output — `rtk proxy` bypasses that and shows the real report.)

Expected: 4 passed. If `job-row-${JOB_NAME}-edit-btn` isn't found, re-check Task 7's `_compareSubTabForJob`/`openCompareForJob` wiring — that's the most likely gap given the other three tests exercise save/launch/report but not the edit round-trip.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/43-live-docker-matrix-save-as-job.spec.ts
git commit -m "test: add e2e coverage for Matrix compare save-as-job"
```

---

### Task 10: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend unit suite**

Run: `python -m pytest tests/unit/ -v`
Expected: PASS, no regressions in `test_compare_job_type.py`, `test_job_validation.py`, `test_run_executor_compare.py`, `test_difference_export.py`, or any other file.

- [ ] **Step 2: Run the full Compare-tab e2e coverage**

Run: `rtk proxy npx playwright test tests/e2e/26-compare-save-as-job.spec.ts tests/e2e/42-live-docker-matrix-reconciliation.spec.ts tests/e2e/43-live-docker-matrix-save-as-job.spec.ts`
Expected: all PASS — confirms the bo/recon_file save-as-job flow, the pre-existing ad-hoc Matrix flow, and the new matrix save-as-job flow all still work together.

- [ ] **Step 3: No commit for this task** — it is verification only; if either run fails, return to the relevant earlier task and fix forward with a new commit there.
