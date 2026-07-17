# Optional Key Columns + Real File Names in Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** File-backed `reconciliation` Jobs can be created and run without `key_columns` (matching the ad-hoc Compare tab's infer-then-positional-fallback behavior), and the HTML report shows the real file names that were compared, alongside the existing environment/source labels.

**Architecture:** Extract the Compare tab's existing key-inference/positional-fallback logic out of `CompareService` into shared functions in `etl_framework/reconciliation/compare_utils.py`, then reuse it from both `CompareService` (no behavior change) and a new code path in `RunExecutor` for file-backed Jobs. Two schema/validation layers currently block Jobs with empty `key_columns` — both get relaxed for the file-backed case only. File names travel to the report via two different channels depending on run type: ad-hoc Compare-tab runs already store the uploaded file name in `run.config_snapshot`, so the report reads it from there; Job runs attach the resolved file name into the existing `mismatch_summary` JSON blob on the result at execution time (no DB migration needed).

**Tech Stack:** Python (FastAPI, Pydantic, pandas), Jinja2 templates, pytest, Playwright (TypeScript e2e).

---

## Task 1: Shared key-resolution utilities

**Files:**
- Modify: `etl_framework/reconciliation/compare_utils.py`
- Test: `tests/unit/test_compare_utils.py`

This pulls the key-inference / validation / positional-fallback logic that today lives only as private static methods on `CompareService` (`api/services/compare_service.py:57-67` and `:262-327`) into framework-level functions, so a second caller (the Job executor, Task 5) can reuse it instead of duplicating it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_compare_utils.py`:

```python
import pytest

from etl_framework.reconciliation.compare_utils import (
    infer_key_columns,
    resolve_key_columns,
    sort_for_positional_compare,
    validate_key_columns,
)


def test_infer_key_columns_finds_known_candidate():
    df_a = pd.DataFrame({"id": [1, 2], "amount": [10, 20]})
    df_b = pd.DataFrame({"id": [1, 2], "amount": [10, 25]})
    assert infer_key_columns(df_a, df_b) == ["id"]


def test_infer_key_columns_falls_back_to_single_common_column():
    df_a = pd.DataFrame({"order_ref": [1, 2], "amount": [10, 20]})
    df_b = pd.DataFrame({"order_ref": [1, 2], "other": [1, 1]})
    assert infer_key_columns(df_a, df_b) == ["order_ref"]


def test_infer_key_columns_raises_when_ambiguous():
    df_a = pd.DataFrame({"colA": [1], "colB": [2]})
    df_b = pd.DataFrame({"colA": [1], "colB": [2]})
    with pytest.raises(ValueError, match="key_columns are required"):
        infer_key_columns(df_a, df_b)


def test_validate_key_columns_raises_when_missing():
    df_a = pd.DataFrame({"id": [1]})
    df_b = pd.DataFrame({"other": [1]})
    with pytest.raises(ValueError, match="must exist in both sources"):
        validate_key_columns(df_a, df_b, ["id"])


def test_sort_for_positional_compare_orders_by_common_columns():
    df_a = pd.DataFrame({"amount": [30, 10, 20]})
    df_b = pd.DataFrame({"amount": [20, 30, 10], "extra": ["x", "y", "z"]})
    sorted_a, sorted_b = sort_for_positional_compare(df_a, df_b, exclude_columns=[])
    assert sorted_a["amount"].tolist() == [10, 20, 30]
    assert sorted_b["amount"].tolist() == [10, 20, 30]


def test_resolve_key_columns_uses_explicit_keys():
    df_a = pd.DataFrame({"id": [1], "val": ["a"]})
    df_b = pd.DataFrame({"id": [1], "val": ["a"]})
    out_a, out_b, keys = resolve_key_columns(df_a, df_b, ["id"], [])
    assert keys == ["id"]
    assert out_a is df_a and out_b is df_b


def test_resolve_key_columns_infers_when_empty():
    df_a = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    df_b = pd.DataFrame({"id": [2, 1], "val": ["b", "a"]})
    out_a, out_b, keys = resolve_key_columns(df_a, df_b, [], [])
    assert keys == ["id"]


def test_resolve_key_columns_falls_back_to_positional_row_match():
    df_a = pd.DataFrame({"colA": [1, 2], "colB": [2, 1]})
    df_b = pd.DataFrame({"colA": [1, 2], "colB": [2, 1]})
    out_a, out_b, keys = resolve_key_columns(df_a, df_b, [], [])
    assert keys == ["__row__"]
    assert "__row__" in out_a.columns
    assert "__row__" in out_b.columns
    assert out_a["__row__"].tolist() == [1, 2]


def test_resolve_key_columns_raises_when_explicit_keys_missing():
    df_a = pd.DataFrame({"id": [1]})
    df_b = pd.DataFrame({"other": [1]})
    with pytest.raises(ValueError, match="must exist in both sources"):
        resolve_key_columns(df_a, df_b, ["id"], [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_compare_utils.py -v`
Expected: FAIL with `ImportError: cannot import name 'infer_key_columns'` (and friends) from `etl_framework.reconciliation.compare_utils`.

- [ ] **Step 3: Implement the shared functions**

Append to `etl_framework/reconciliation/compare_utils.py` (after the existing `build_mismatch_summary` function, keeping the existing imports as-is):

```python
_KEY_CANDIDATES = (
    "id",
    "employee id",
    "employee_id",
    "order id",
    "order_id",
    "customer id",
    "customer_id",
    "account id",
    "account_id",
)


def _column_key(column: Any) -> str:
    return "".join(ch for ch in str(column).lower() if ch.isalnum())


def infer_key_columns(df_a: pd.DataFrame, df_b: pd.DataFrame) -> list[str]:
    """Find a common ID-like column shared by both frames.

    Prefers a known candidate name (id, employee_id, ...); falls back to the
    single shared column when there's exactly one. Raises ValueError when no
    unambiguous key can be determined.
    """
    common_by_lower = {
        str(col).strip().lower(): str(col)
        for col in df_a.columns
        if str(col).strip().lower() in {str(c).strip().lower() for c in df_b.columns}
    }
    for candidate in _KEY_CANDIDATES:
        if candidate in common_by_lower:
            return [common_by_lower[candidate]]
    if len(common_by_lower) == 1:
        return [next(iter(common_by_lower.values()))]
    raise ValueError("key_columns are required when no unique common ID column can be inferred")


def validate_key_columns(df_a: pd.DataFrame, df_b: pd.DataFrame, key_columns: list[str]) -> None:
    missing_a = [col for col in key_columns if col not in df_a.columns]
    missing_b = [col for col in key_columns if col not in df_b.columns]
    if missing_a or missing_b:
        raise ValueError(
            "Selected key_columns must exist in both sources "
            f"(missing in source A: {missing_a}, missing in source B: {missing_b})"
        )


def sort_for_positional_compare(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    exclude_columns: list[str] | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sort both sides before row-position fallback alignment."""
    excluded = {_column_key(col) for col in (exclude_columns or [])}
    common_columns = [
        col for col in df_a.columns
        if col in df_b.columns and _column_key(col) not in excluded
    ]
    if not common_columns:
        return df_a.reset_index(drop=True), df_b.reset_index(drop=True)

    def _sort_frame(df: pd.DataFrame) -> pd.DataFrame:
        try:
            return df.sort_values(
                by=common_columns,
                kind="mergesort",
                na_position="first",
            ).reset_index(drop=True)
        except TypeError:
            sort_keys = pd.DataFrame(index=df.index)
            for idx, col in enumerate(common_columns):
                sort_keys[f"__sort_{idx}"] = df[col].map(
                    lambda value: "" if pd.isna(value) else str(value)
                )
            order = sort_keys.sort_values(
                by=list(sort_keys.columns),
                kind="mergesort",
                na_position="first",
            ).index
            return df.loc[order].reset_index(drop=True)

    return _sort_frame(df_a), _sort_frame(df_b)


def resolve_key_columns(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    key_columns: list[str] | None,
    exclude_columns: list[str] | None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Resolve the key columns to compare two frames by.

    If `key_columns` is given, validates it exists on both sides and returns
    it unchanged. If empty, tries to infer a common ID column; if none can be
    inferred, falls back to positional (row-order) matching via a synthetic
    `__row__` column, after sorting both frames by their common columns for
    stability.
    """
    if key_columns:
        validate_key_columns(df_a, df_b, key_columns)
        return df_a, df_b, list(key_columns)
    try:
        return df_a, df_b, infer_key_columns(df_a, df_b)
    except ValueError:
        sorted_a, sorted_b = sort_for_positional_compare(df_a, df_b, exclude_columns)
        sorted_a = sorted_a.copy()
        sorted_b = sorted_b.copy()
        sorted_a.insert(0, "__row__", range(1, len(sorted_a) + 1))
        sorted_b.insert(0, "__row__", range(1, len(sorted_b) + 1))
        return sorted_a, sorted_b, ["__row__"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_compare_utils.py -v`
Expected: PASS (all tests in the file, existing + new).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/compare_utils.py tests/unit/test_compare_utils.py
git commit -m "feat: add shared key-resolution utilities for reconciliation compares"
```

---

## Task 2: Refactor CompareService to use the shared utilities

**Files:**
- Modify: `api/services/compare_service.py:55-67` (remove `_KEY_CANDIDATES`/`_column_key`), `:146-196` (`run_bo_comparison`), `:262-327` (remove static methods), `:329-372` (`_run_tabular_file_compare`)
- Test: `tests/unit/test_compare_api.py` (existing tests must still pass — no new tests needed, this is a pure refactor)

This removes the duplicated inline key-inference logic (it existed in both `run_bo_comparison` and `_run_tabular_file_compare`) in favor of `resolve_key_columns` from Task 1. Behavior is unchanged — this task is refactor-only, verified by the existing test suite.

- [ ] **Step 1: Add the import**

Edit `api/services/compare_service.py`, add to the import block (after the `AdvancedCompareOptions,` line closing the `api.schemas` import, i.e. right after line 16's `)`):

```python
from etl_framework.reconciliation.compare_utils import resolve_key_columns
```

- [ ] **Step 2: Update `run_bo_comparison` to use `resolve_key_columns`**

In `api/services/compare_service.py`, replace this block inside `run_bo_comparison` (currently lines 152-162):

```python
            key_columns = req.key_columns
            if not key_columns:
                try:
                    key_columns = self._infer_key_columns(df_a, df_b)
                except HTTPException:
                    df_a = df_a.copy()
                    df_b = df_b.copy()
                    df_a.insert(0, "__row__", range(1, len(df_a) + 1))
                    df_b.insert(0, "__row__", range(1, len(df_b) + 1))
                    key_columns = ["__row__"]
            self._validate_key_columns(df_a, df_b, key_columns)
```

with:

```python
            try:
                df_a, df_b, key_columns = resolve_key_columns(
                    df_a, df_b, req.key_columns, req.exclude_columns or [],
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
```

- [ ] **Step 3: Update `_run_tabular_file_compare` to use `resolve_key_columns`**

In `api/services/compare_service.py`, replace this block inside `_run_tabular_file_compare` (currently lines 335-351):

```python
        key_columns = req.key_columns
        if not key_columns:
            try:
                key_columns = self._infer_key_columns(df_a, df_b)
            except HTTPException:
                # No identifiable key column — compare row-by-row using position
                df_a, df_b = self._sort_for_positional_compare(
                    df_a,
                    df_b,
                    req.exclude_columns or [],
                )
                df_a = df_a.copy()
                df_b = df_b.copy()
                df_a.insert(0, "__row__", range(1, len(df_a) + 1))
                df_b.insert(0, "__row__", range(1, len(df_b) + 1))
                key_columns = ["__row__"]
        self._validate_key_columns(df_a, df_b, key_columns)
```

with:

```python
        try:
            df_a, df_b, key_columns = resolve_key_columns(
                df_a, df_b, key_columns, req.exclude_columns or [],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
```

Note: the line just above this block (`key_columns = req.key_columns`) stays — it's still needed as the input to `resolve_key_columns`.

- [ ] **Step 4: Remove the now-unused module-level helpers and static methods**

Both call sites now go through `resolve_key_columns` (Task 1), so the private duplicates can go. In `api/services/compare_service.py`:

Delete the `_KEY_CANDIDATES` tuple and `_column_key` function (currently lines 57-71):

```python
_KEY_CANDIDATES = (
    "id",
    "employee id",
    "employee_id",
    "order id",
    "order_id",
    "customer id",
    "customer_id",
    "account id",
    "account_id",
)


def _column_key(column: object) -> str:
    return "".join(ch for ch in str(column).lower() if ch.isalnum())
```

Delete this whole block. Leave `_SENTINEL_QUERY`, `_DEFAULT_COMPARE_MISMATCH_ROW_LIMIT`, and `_compare_mismatch_row_limit` (the surrounding functions) untouched.

Delete `_infer_key_columns`, `_validate_key_columns`, and `_sort_for_positional_compare` entirely (currently lines 261-327, i.e. from the `@staticmethod` above `_infer_key_columns` through the end of `_sort_for_positional_compare`'s body, up to but not including `def _run_tabular_file_compare`).

- [ ] **Step 5: Run the existing compare test suite to confirm no regression**

Run: `pytest tests/unit/test_compare_api.py tests/unit/test_compare_utils.py -v`
Expected: PASS (all tests — this proves the refactor preserved behavior for both the recon-file and BO-report ad-hoc compare paths).

- [ ] **Step 6: Commit**

```bash
git add api/services/compare_service.py
git commit -m "refactor: reuse shared key-resolution utilities in CompareService"
```

---

## Task 3: Relax JobDefinition schema validation for file-backed reconciliation jobs

**Files:**
- Modify: `api/schemas.py:450-464`
- Test: `tests/unit/test_file_backed_jobs.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_file_backed_jobs.py`:

```python
def test_file_backed_reconciliation_does_not_require_key_columns() -> None:
    job = JobDefinition(
        name="orders_file_recon_no_key",
        job_type="reconciliation",
        query="",
        params={
            "source_mode": "files",
            "source_file_path": "source.csv",
            "target_file_path": "target.csv",
        },
    )
    assert job.key_columns == []


def test_query_based_reconciliation_still_requires_key_columns() -> None:
    with pytest.raises(ValidationError, match="key_columns"):
        JobDefinition(
            name="orders_query_recon_no_key",
            job_type="reconciliation",
            query="SELECT * FROM orders",
        )
```

- [ ] **Step 2: Run tests to verify the first one fails**

Run: `pytest tests/unit/test_file_backed_jobs.py -v`
Expected: `test_file_backed_reconciliation_does_not_require_key_columns` FAILS with a `pydantic.ValidationError` ("reconciliation jobs require key_columns"). `test_query_based_reconciliation_still_requires_key_columns` already PASSES (this documents existing, unchanged behavior).

- [ ] **Step 3: Relax the validator**

In `api/schemas.py`, replace the `reconciliation` branch inside `validate_reconciliation_contract` (currently lines 450-464):

```python
        elif self.job_type == "reconciliation":
            uses_files = (
                self.params.get("source_mode") == "files"
                or _has_job_file_source(self.params, "source")
                or _has_job_file_source(self.params, "target")
            )
            if uses_files:
                _validate_job_file_source(self.params, "source")
                _validate_job_file_source(self.params, "target")
                if not _has_job_file_source(self.params, "source") or not _has_job_file_source(self.params, "target"):
                    raise ValueError("file-backed reconciliation jobs require source and target files")
            elif not self.query.strip():
                raise ValueError("reconciliation jobs require a query")
            if not self.key_columns:
                raise ValueError("reconciliation jobs require key_columns")
```

with:

```python
        elif self.job_type == "reconciliation":
            uses_files = (
                self.params.get("source_mode") == "files"
                or _has_job_file_source(self.params, "source")
                or _has_job_file_source(self.params, "target")
            )
            if uses_files:
                _validate_job_file_source(self.params, "source")
                _validate_job_file_source(self.params, "target")
                if not _has_job_file_source(self.params, "source") or not _has_job_file_source(self.params, "target"):
                    raise ValueError("file-backed reconciliation jobs require source and target files")
                # key_columns is optional for file-backed jobs: RunExecutor infers a
                # common ID column, or falls back to positional row matching, the same
                # way the ad-hoc Compare tab does (see resolve_key_columns).
            else:
                if not self.query.strip():
                    raise ValueError("reconciliation jobs require a query")
                if not self.key_columns:
                    raise ValueError("reconciliation jobs require key_columns")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_file_backed_jobs.py tests/unit/test_api.py -v`
Expected: PASS. In particular `test_job_definition_requires_key_columns_for_reconciliation` (query-based, `tests/unit/test_api.py:413`) and `test_create_job_rejects_missing_key_columns` (`tests/unit/test_api.py:669`) must still pass unchanged — both are query-based jobs, untouched by this change.

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_file_backed_jobs.py
git commit -m "feat: allow file-backed reconciliation jobs without key_columns"
```

---

## Task 4: Relax the parallel job_validation.py validator

**Files:**
- Modify: `etl_framework/runner/job_validation.py:29-39`
- Test: `tests/unit/test_job_validation.py`

`validate_job_definition` (used by `POST /api/jobs/validate`, `POST /api/jobs/{name}/validate`, and — critically — `_validate_saved_jobs_for_launch` in `api/routes/runs.py:294-310`, which gates every run launch for saved jobs) duplicates the same key_columns requirement Task 3 just relaxed. Without this task, a file-backed job saved without `key_columns` would pass creation (Task 3) but still be rejected the moment someone tries to run it.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_job_validation.py`:

```python
def test_file_backed_reconciliation_does_not_require_key_columns():
    issues = validate_job_definition({
        "name": "files",
        "job_type": "reconciliation",
        "params": {
            "source_mode": "files",
            "source_file_path": r"c:\temp\RMS_FUT_20260601_qa.xml",
            "target_file_path": r"c:\temp\RMS_FUT_20260601_prod.xml",
        },
        "key_columns": [],
    })
    assert issues == []


def test_query_based_reconciliation_still_reports_missing_key_columns():
    issues = validate_job_definition({
        "name": "orders",
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "key_columns": [],
    })
    assert {issue.field for issue in issues} == {"key_columns"}
```

- [ ] **Step 2: Run tests to verify the first one fails**

Run: `pytest tests/unit/test_job_validation.py -v`
Expected: `test_file_backed_reconciliation_does_not_require_key_columns` FAILS — `issues` contains a `key_columns` issue. `test_query_based_reconciliation_still_reports_missing_key_columns` already PASSES.

- [ ] **Step 3: Relax the validator**

In `etl_framework/runner/job_validation.py`, replace (currently lines 29-39):

```python
    if job_type == "reconciliation":
        uses_files = params.get("source_mode") == "files" or _has_file_source(params, "source") or _has_file_source(params, "target")
        if uses_files:
            _validate_file_source(params, "source", issues)
            _validate_file_source(params, "target", issues)
            if not _has_file_source(params, "source") or not _has_file_source(params, "target"):
                issues.append(ValidationIssue("params", "file-backed reconciliation jobs require source and target files"))
        elif not query.strip():
            issues.append(ValidationIssue("query", "reconciliation jobs require a query"))
        if not key_columns:
            issues.append(ValidationIssue("key_columns", "reconciliation jobs require key_columns"))
```

with:

```python
    if job_type == "reconciliation":
        uses_files = params.get("source_mode") == "files" or _has_file_source(params, "source") or _has_file_source(params, "target")
        if uses_files:
            _validate_file_source(params, "source", issues)
            _validate_file_source(params, "target", issues)
            if not _has_file_source(params, "source") or not _has_file_source(params, "target"):
                issues.append(ValidationIssue("params", "file-backed reconciliation jobs require source and target files"))
            # key_columns is optional for file-backed jobs -- RunExecutor infers a
            # common ID column or falls back to positional row matching.
        else:
            if not query.strip():
                issues.append(ValidationIssue("query", "reconciliation jobs require a query"))
            if not key_columns:
                issues.append(ValidationIssue("key_columns", "reconciliation jobs require key_columns"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_job_validation.py -v`
Expected: PASS (all tests in the file, including the pre-existing `test_file_backed_reconciliation_accepts_job_file_paths`, which already passes `key_columns=["id"]` and must keep passing unchanged).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/runner/job_validation.py tests/unit/test_job_validation.py
git commit -m "feat: allow file-backed reconciliation jobs without key_columns in dry-run validation"
```

---

## Task 5: RunExecutor resolves keys and attaches file names for file-backed jobs

**Files:**
- Modify: `api/services/run_executor.py:1-38` (imports), `:446-463` (`_build_case_file_reconciliation`), `:465-507` (`_run_reconciliation_job`)
- Test: `tests/unit/test_file_backed_jobs.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_file_backed_jobs.py`:

```python
def test_run_executor_infers_key_when_none_given(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.csv"
    target = tmp_path / "target.csv"
    source.write_text("id,value\n1,alpha\n2,beta\n", encoding="utf-8")
    target.write_text("id,value\n1,alpha\n2,changed\n", encoding="utf-8")

    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    job = JobDefinition(
        name="orders_file_recon_infer",
        job_type="reconciliation",
        query="",
        params={
            "source_mode": "files",
            "source_file_path": str(source),
            "target_file_path": str(target),
        },
    )
    executor = RunExecutor(
        db=None,
        run_id="test-run-infer",
        source_env="source",
        target_env="target",
        job_sequence=[],
        run_settings=RunSettings(chunk_size=0, use_hash_precheck=False),
        config_snapshot={},
    )
    executor._resolve_segment_columns = lambda _job: []

    result = executor._build_case(job)()

    assert result.status == TestStatus.FAILED
    assert result.value_mismatch_count == 1
    assert result.mismatch_summary["file_names"] == {"source": "source.csv", "target": "target.csv"}


def test_run_executor_falls_back_to_positional_match_when_no_shared_id(tmp_path, monkeypatch) -> None:
    source = tmp_path / "no_id_source.csv"
    target = tmp_path / "no_id_target.csv"
    source.write_text("colA,colB\n1,2\n3,4\n", encoding="utf-8")
    target.write_text("colA,colB\n1,2\n3,5\n", encoding="utf-8")

    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    job = JobDefinition(
        name="orders_file_recon_positional",
        job_type="reconciliation",
        query="",
        params={
            "source_mode": "files",
            "source_file_path": str(source),
            "target_file_path": str(target),
        },
    )
    executor = RunExecutor(
        db=None,
        run_id="test-run-positional",
        source_env="source",
        target_env="target",
        job_sequence=[],
        run_settings=RunSettings(chunk_size=0, use_hash_precheck=False),
        config_snapshot={},
    )
    executor._resolve_segment_columns = lambda _job: []

    result = executor._build_case(job)()

    assert result.status == TestStatus.FAILED
    assert result.value_mismatch_count == 1
    assert result.mismatch_summary["file_names"] == {"source": "no_id_source.csv", "target": "no_id_target.csv"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_file_backed_jobs.py -v`
Expected: Both new tests FAIL. `test_run_executor_infers_key_when_none_given` fails inside `ReconciliationEngine` because `key_columns=[]` is passed through (empty key list breaks grouping). `test_run_executor_falls_back_to_positional_match_when_no_shared_id` fails the same way.

- [ ] **Step 3: Add the `Path` and `resolve_key_columns` imports**

In `api/services/run_executor.py`, add two lines to the import block — `Path` after the existing `from typing import Any` line (line 12), and `resolve_key_columns` after the `from etl_framework.reconciliation.engine import ReconciliationEngine` line (line 25):

```python
from pathlib import Path
```

```python
from etl_framework.reconciliation.compare_utils import resolve_key_columns
```

- [ ] **Step 4: Rewrite `_build_case_file_reconciliation` to resolve keys and attach file names**

In `api/services/run_executor.py`, replace `_build_case_file_reconciliation` (currently lines 446-463):

```python
    def _build_case_file_reconciliation(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            if not self._has_file_source(job, "source") or not self._has_file_source(job, "target"):
                raise ValueError("file-backed reconciliation jobs require source and target files")
            engines = self._build_file_engines(job)
            if engines is None:
                raise ValueError("file-backed reconciliation jobs require source and target files")
            source_engine, target_engine = engines
            return self._run_reconciliation_job(
                job,
                source_engine,
                target_engine,
                query=FILE_SOURCE_QUERY,
                params={},
                chunk_size=0,
                use_hash_precheck=False,
            )
        return run_job
```

with:

```python
    def _build_case_file_reconciliation(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            if not self._has_file_source(job, "source") or not self._has_file_source(job, "target"):
                raise ValueError("file-backed reconciliation jobs require source and target files")
            source_df = self._load_job_file_frame(job, "source")
            target_df = self._load_job_file_frame(job, "target")
            source_df, target_df, key_columns = resolve_key_columns(
                source_df, target_df, job.key_columns or [], job.exclude_columns or [],
            )
            source_label = job.params.get("source_file_label") or job.params.get("label_a") or self._source_env
            target_label = job.params.get("target_file_label") or job.params.get("label_b") or self._target_env
            source_engine = FrameEngine(source_df, source_label)
            target_engine = FrameEngine(target_df, target_label)
            result = self._run_reconciliation_job(
                job,
                source_engine,
                target_engine,
                query=FILE_SOURCE_QUERY,
                params={},
                chunk_size=0,
                use_hash_precheck=False,
                key_columns=key_columns,
            )
            return self._attach_file_names(result, job)
        return run_job

    def _file_display_name(self, job: JobDefinition, prefix: str) -> str | None:
        name = self._job_file_value(job, prefix, "name")
        if name:
            return str(name)
        path = self._job_file_value(job, prefix, "path")
        if path:
            return Path(str(path)).name
        return None

    def _attach_file_names(self, result: ReconciliationResult, job: JobDefinition) -> ReconciliationResult:
        source_name = self._file_display_name(job, "source")
        target_name = self._file_display_name(job, "target")
        if not source_name and not target_name:
            return result
        summary = dict(result.mismatch_summary or {})
        summary["file_names"] = {"source": source_name, "target": target_name}
        return dataclasses.replace(result, mismatch_summary=summary)
```

- [ ] **Step 5: Add the `key_columns` override param to `_run_reconciliation_job`**

In `api/services/run_executor.py`, update the signature and body of `_run_reconciliation_job` (currently lines 465-490). Change the signature (currently):

```python
    def _run_reconciliation_job(
        self,
        job: JobDefinition,
        source_engine,
        target_engine,
        *,
        query: str,
        params: dict[str, Any] | None,
        chunk_size: int,
        use_hash_precheck: bool,
    ) -> ReconciliationResult:
        segment_columns = self._resolve_segment_columns(job)
        engine = ReconciliationEngine(
            source_engine=source_engine,
            target_engine=target_engine,
            key_columns=job.key_columns or self._settings.key_columns,
```

to:

```python
    def _run_reconciliation_job(
        self,
        job: JobDefinition,
        source_engine,
        target_engine,
        *,
        query: str,
        params: dict[str, Any] | None,
        chunk_size: int,
        use_hash_precheck: bool,
        key_columns: list[str] | None = None,
    ) -> ReconciliationResult:
        segment_columns = self._resolve_segment_columns(job)
        engine = ReconciliationEngine(
            source_engine=source_engine,
            target_engine=target_engine,
            key_columns=key_columns if key_columns is not None else (job.key_columns or self._settings.key_columns),
```

Leave the rest of `_run_reconciliation_job` (everything after the `key_columns=` line, through the closing of the `ReconciliationEngine(...)` call and the rest of the method body) unchanged.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_file_backed_jobs.py -v`
Expected: PASS (all tests in the file, including the pre-existing `test_run_executor_reconciles_file_backed_csv`, which passes explicit `key_columns=["id"]` and must keep passing unchanged).

- [ ] **Step 7: Run the full unit suite to catch any missed caller**

Run: `pytest tests/unit -v -k "run_executor or file_backed or job_validation or api_reconciliation"`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_file_backed_jobs.py
git commit -m "feat: resolve key columns and attach file names for file-backed reconciliation jobs"
```

---

## Task 6: Surface file names in RunReportSnapshot and ReportResult

**Files:**
- Modify: `api/services/run_report.py`
- Test: `tests/unit/test_run_report.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_run_report.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from api.services.run_report import build_run_report_snapshot


def _run(**overrides):
    base = dict(
        run_id="run-1",
        status="PASSED",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        source_env="Source A",
        target_env="Production Report",
        config_snapshot=None,
        run_type="recon_file",
        pair_id=None,
        total_tests=0,
        passed=0,
        failed=0,
        slow=0,
        error=0,
        results=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _result(**overrides):
    base = dict(
        id=1,
        query_name="q1",
        status="PASSED",
        effective_status="PASSED",
        duration_seconds=1.0,
        source_row_count=1,
        target_row_count=1,
        value_mismatch_count=0,
        missing_in_target_count=0,
        missing_in_source_count=0,
        mismatch_summary=None,
        mismatches=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_recon_file_upload_populates_file_names_from_config_snapshot():
    run = _run(config_snapshot={
        "compare_request_type": "recon_file",
        "request": {"file_a_name": "march_invoices.xml", "file_b_name": "march_invoices_prod.xml"},
    })
    snapshot = build_run_report_snapshot(run)
    assert snapshot.file_name_a == "march_invoices.xml"
    assert snapshot.file_name_b == "march_invoices_prod.xml"


def test_recon_file_path_falls_back_to_basename():
    run = _run(config_snapshot={
        "compare_request_type": "recon_file",
        "request": {"file_a_path": r"c:\uploads\run-1\source.csv", "file_b_path": r"c:\uploads\run-1\target.csv"},
    })
    snapshot = build_run_report_snapshot(run)
    assert snapshot.file_name_a == "source.csv"
    assert snapshot.file_name_b == "target.csv"


def test_bo_report_reads_file_names_from_source_configs():
    run = _run(config_snapshot={
        "compare_request_type": "bo_report",
        "request": {
            "source_a": {"file_name": "sales_a.xlsx"},
            "source_b": {"file_path": r"c:\uploads\run-1\sales_b.xlsx"},
        },
    })
    snapshot = build_run_report_snapshot(run)
    assert snapshot.file_name_a == "sales_a.xlsx"
    assert snapshot.file_name_b == "sales_b.xlsx"


def test_no_file_names_when_config_snapshot_has_none():
    run = _run(config_snapshot={"compare_request_type": "sql", "request": {"query_a": "SELECT 1"}})
    snapshot = build_run_report_snapshot(run)
    assert snapshot.file_name_a is None
    assert snapshot.file_name_b is None


def test_missing_config_snapshot_yields_no_file_names():
    run = _run(config_snapshot=None)
    snapshot = build_run_report_snapshot(run)
    assert snapshot.file_name_a is None
    assert snapshot.file_name_b is None


def test_result_file_names_come_from_mismatch_summary():
    run = _run(results=[
        _result(mismatch_summary={"file_names": {"source": "orders_qa.xml", "target": "orders_prod.xml"}}),
    ])
    snapshot = build_run_report_snapshot(run)
    assert snapshot.results[0].file_name_source == "orders_qa.xml"
    assert snapshot.results[0].file_name_target == "orders_prod.xml"


def test_result_file_names_absent_when_no_mismatch_summary():
    run = _run(results=[_result(mismatch_summary=None)])
    snapshot = build_run_report_snapshot(run)
    assert snapshot.results[0].file_name_source is None
    assert snapshot.results[0].file_name_target is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_run_report.py -v`
Expected: FAIL with `AttributeError: 'RunReportSnapshot' object has no attribute 'file_name_a'` (and similarly for `file_name_source` on `ReportResult`).

- [ ] **Step 3: Add fields and extraction helpers**

In `api/services/run_report.py`, add `file_name_source` and `file_name_target` to the `ReportResult` dataclass. Change the field list (currently ending at line 32 with `mismatches: list[Any] = field(default_factory=list)` before `schema_diff` and `total_issues_override`):

```python
    mismatches: list[Any] = field(default_factory=list)
    schema_diff: Any = None
    total_issues_override: int | None = None
```

to:

```python
    mismatches: list[Any] = field(default_factory=list)
    schema_diff: Any = None
    total_issues_override: int | None = None
    file_name_source: str | None = None
    file_name_target: str | None = None
```

Add `file_name_a` and `file_name_b` to the `RunReportSnapshot` dataclass. Change the field list (currently ending at line 155 with `has_result_rows: bool`):

```python
    results: list[ReportResult]
    has_result_rows: bool
```

to:

```python
    results: list[ReportResult]
    has_result_rows: bool
    file_name_a: str | None = None
    file_name_b: str | None = None
```

- [ ] **Step 4: Add the extraction helper functions**

In `api/services/run_report.py`, add these functions right before `def build_run_report_snapshot(...)` (currently line 239):

```python
def _basename(path: Any) -> str | None:
    if not path or path in ("__sql__", "__file_source__"):
        return None
    from pathlib import Path
    return Path(str(path)).name


def _extract_compare_file_names(run: Any) -> tuple[str | None, str | None]:
    snapshot = getattr(run, "config_snapshot", None)
    if not isinstance(snapshot, dict):
        return None, None
    request = snapshot.get("request")
    if not isinstance(request, dict):
        return None, None
    request_type = snapshot.get("compare_request_type")
    if request_type == "recon_file":
        name_a = request.get("file_a_name") or _basename(request.get("file_a_path"))
        name_b = request.get("file_b_name") or _basename(request.get("file_b_path"))
        return name_a, name_b
    if request_type == "bo_report":
        source_a = request.get("source_a") if isinstance(request.get("source_a"), dict) else {}
        source_b = request.get("source_b") if isinstance(request.get("source_b"), dict) else {}
        name_a = source_a.get("file_name") or _basename(source_a.get("file_path"))
        name_b = source_b.get("file_name") or _basename(source_b.get("file_path"))
        return name_a, name_b
    return None, None


def _extract_result_file_names(result: Any) -> tuple[str | None, str | None]:
    summary = getattr(result, "mismatch_summary", None)
    if not isinstance(summary, dict):
        return None, None
    file_names = summary.get("file_names")
    if not isinstance(file_names, dict):
        return None, None
    return file_names.get("source"), file_names.get("target")
```

- [ ] **Step 5: Populate the new fields in `build_run_report_snapshot`**

In `api/services/run_report.py`, inside the `results = [...]` list comprehension (currently lines 240-265), add the two new fields to the `ReportResult(...)` constructor call, right after `total_issues_override=getattr(result, "total_issues", None),` (currently line 262):

```python
            total_issues_override=getattr(result, "total_issues", None),
        )
        for result in (getattr(run, "results", []) or [])
    ]
```

to:

```python
            total_issues_override=getattr(result, "total_issues", None),
            file_name_source=_extract_result_file_names(result)[0],
            file_name_target=_extract_result_file_names(result)[1],
        )
        for result in (getattr(run, "results", []) or [])
    ]
```

Then, in the `return RunReportSnapshot(...)` call at the end of the function (currently lines 281-304), add the two new fields right after `has_result_rows=bool(results),` (currently line 303):

```python
        results=results,
        has_result_rows=bool(results),
    )
```

to:

```python
        results=results,
        has_result_rows=bool(results),
        file_name_a=_extract_compare_file_names(run)[0],
        file_name_b=_extract_compare_file_names(run)[1],
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_run_report.py -v`
Expected: PASS.

- [ ] **Step 7: Run the broader run_report / compare / jobs suites for regressions**

Run: `pytest tests/unit/test_run_report.py tests/unit/test_compare_api.py tests/unit/test_file_backed_jobs.py tests/unit/test_api.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add api/services/run_report.py tests/unit/test_run_report.py
git commit -m "feat: surface compared file names in RunReportSnapshot and ReportResult"
```

---

## Task 7: Render file names in the HTML report template

**Files:**
- Modify: `etl_framework/reporting/templates/report.html.j2:202-208` (header), `:327-340` (results table row)

No unit test for this task (Jinja2 template rendering) — verified via the Task 8 e2e test, plus a manual local check in Step 3 below.

- [ ] **Step 1: Add the file-names line to the report header**

In `etl_framework/reporting/templates/report.html.j2`, replace (currently lines 202-208):

```html
    <div id="header">
      <h1>ETL Framework Execution Report</h1>
      <p><strong>Run ID:</strong> <span style="font-family: monospace;">{{ suite.run_id }}</span></p>
      <p><strong>Started:</strong> {{ suite.started_at }}</p>
      <p><strong>Environments:</strong> {{ suite.source_env }} &rarr; {{ suite.target_env }}</p>
      <hr>
    </div>
```

with:

```html
    <div id="header">
      <h1>ETL Framework Execution Report</h1>
      <p><strong>Run ID:</strong> <span style="font-family: monospace;">{{ suite.run_id }}</span></p>
      <p><strong>Started:</strong> {{ suite.started_at }}</p>
      <p><strong>Environments:</strong> {{ suite.source_env }} &rarr; {{ suite.target_env }}</p>
      {% if suite.file_name_a or suite.file_name_b %}
      <p><strong>Files:</strong> {{ suite.file_name_a or '—' }} &rarr; {{ suite.file_name_b or '—' }}</p>
      {% endif %}
      <hr>
    </div>
```

- [ ] **Step 2: Add per-row file names to the Reconciliation Results table**

In `etl_framework/reporting/templates/report.html.j2`, replace the test-name cell inside the results-table loop (currently lines 327-330):

```html
          {% for result in suite.reconciliation_results %}
          <tr>
            <td style="font-family: monospace;">{{ result.query_name }}</td>
            <td>
```

with:

```html
          {% for result in suite.reconciliation_results %}
          <tr>
            <td style="font-family: monospace;">
              {{ result.query_name }}
              {% if result.file_name_source or result.file_name_target %}
              <div style="font-size:0.75em;color:var(--muted);font-weight:normal">{{ result.file_name_source or '—' }} &rarr; {{ result.file_name_target or '—' }}</div>
              {% endif %}
            </td>
            <td>
```

- [ ] **Step 3: Manually verify the template still renders**

Run: `pytest tests/unit/test_reporting.py -v` (or, if that file doesn't exist, skip to the grep below)

Run: `grep -rl "report.html.j2" tests/unit` — if this returns a test file, run it with pytest and confirm PASS. This confirms the Jinja2 template still parses and renders with the new conditional blocks (undefined `suite.file_name_a` / `result.file_name_source` on any `suite`/`result` object without those attributes would raise `jinja2.exceptions.UndefinedError` under strict undefined settings — since `ReportGenerator`'s `Environment` doesn't set `undefined=StrictUndefined`, Jinja2's default `Undefined` renders missing attributes as empty string, so this is safe even for callers not yet updated to pass the new fields).

- [ ] **Step 4: Commit**

```bash
git add etl_framework/reporting/templates/report.html.j2
git commit -m "feat: render compared file names in the HTML report"
```

---

## Task 8: E2E coverage

**Files:**
- Modify: `tests/e2e/api-helpers.ts` (new helper)
- Modify: `tests/e2e/06-reports.spec.ts` (extend existing report-content test)
- Create: `tests/e2e/08g-compare-job-no-key-columns.spec.ts`

- [ ] **Step 1: Add a helper for creating a file-backed job without key_columns**

In `tests/e2e/api-helpers.ts`, add this function right after `createFileJob` (after its closing `}` at line 78):

```typescript
/**
 * Creates a file-mode reconciliation job comparing fixtures/data/source.csv vs
 * target.csv WITHOUT specifying key_columns — exercises the infer-a-common-ID-column
 * fallback (both files share an `id` column). Same deterministic mismatch counts as
 * createFileJob: 1 value_diff, 1 missing_in_target, 1 missing_in_source.
 */
export async function createFileJobWithoutKeyColumns(ctx: APIRequestContext, name: string) {
  const resp = await ctx.post('/api/jobs', {
    data: {
      name,
      job_type: 'reconciliation',
      params: {
        source_mode: 'files',
        source_file_path: path.join(FIXTURE_DIR, 'source.csv'),
        target_file_path: path.join(FIXTURE_DIR, 'target.csv'),
      },
    },
  });
  if (!resp.ok()) throw new Error(`createFileJobWithoutKeyColumns(${name}) failed: ${resp.status()} ${await resp.text()}`);
  return resp.json();
}
```

- [ ] **Step 2: Write the new e2e spec**

Create `tests/e2e/08g-compare-job-no-key-columns.spec.ts`:

```typescript
import { test, expect } from './fixtures';
import { authedContext, createFileJobWithoutKeyColumns, deleteJob, triggerRun, waitForTerminal } from './api-helpers';

test.describe('08g job compare without key_columns', () => {
  test('file-backed reconciliation job runs to completion without key_columns', async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    const jobName = `e2e-no-key-job-${Date.now()}`;
    try {
      await createFileJobWithoutKeyColumns(ctx, jobName);
      const { run_id } = await triggerRun(ctx, [jobName]);
      const status = await waitForTerminal(ctx, run_id);

      // Deterministic per fixtures/data/source.csv vs target.csv (see createFileJob's
      // doc comment): infer_key_columns finds the shared `id` column, so this reaches
      // the same FAILED-with-3-mismatches outcome as the explicit-key_columns job.
      expect(status.status).toBe('FAILED');
    } finally {
      await deleteJob(ctx, jobName);
      await ctx.dispose();
    }
  });
});
```

- [ ] **Step 3: Run the new spec**

Run: `npx playwright test tests/e2e/08g-compare-job-no-key-columns.spec.ts`
Expected: PASS. (Requires the backend dev server running per this repo's e2e setup — see `tests/e2e/fixtures.ts` / `playwright.config.ts` if it isn't already running.)

- [ ] **Step 4: Extend the existing report-content e2e test to assert real file names appear**

In `tests/e2e/06-reports.spec.ts`, extend the `'loads the HTML report for the seeded run and renders real report content'` test (currently ending at line 53) by adding this assertion right after the existing `await expect(frame.locator('#header p', { hasText: 'Run ID:' })).toContainText(runId);` line (line 40):

```typescript
    // seedBaselineRun() uses createFileJob(), whose fixture files are source.csv /
    // target.csv (fixtures/data/) with no file_a_name/file_b_name override, so the
    // report falls back to the file path's basename (api/services/run_report.py's
    // _extract_result_file_names / RunExecutor._file_display_name).
    await expect(frame.locator('#recon table')).toContainText('source.csv');
    await expect(frame.locator('#recon table')).toContainText('target.csv');
```

- [ ] **Step 5: Run the extended spec**

Run: `npx playwright test tests/e2e/06-reports.spec.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/api-helpers.ts tests/e2e/06-reports.spec.ts tests/e2e/08g-compare-job-no-key-columns.spec.ts
git commit -m "test: add e2e coverage for key-less job compares and file names in reports"
```

---

## Final verification

- [ ] Run the full unit suite: `pytest tests/unit -v`
- [ ] Run the full e2e suite (if time allows; at minimum the specs touched in Task 8): `npx playwright test`
- [ ] Confirm the spec at `docs/superpowers/specs/2026-07-17-optional-key-columns-and-file-names-design.md` is fully implemented: file-backed reconciliation Jobs no longer require `key_columns` (Tasks 3-5), and the HTML report shows real file names for both ad-hoc Compare-tab runs and Job runs (Tasks 6-7).
