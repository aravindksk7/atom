# Coverage Visibility & Mismatch Segment Drill-Down Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add (A) a test-coverage matrix with gap report and flaky-test detection, and (B) segment-based mismatch root-cause analysis (inline summary stored per result + on-demand re-query drill-down).

**Architecture:** Compute-on-read coverage service reading existing tables (`saved_jobs`, `schema_snapshots`, `column_profiles`, `test_results`), cached with the same TTL pattern as the trend cache. Segment drill-down enriches `MismatchRecord`s centrally in `ReconciliationEngine` (one place — backends untouched), the run executor builds a top-20 summary stored in one new nullable JSON column `test_results.segment_summary`. Spec: `docs/superpowers/specs/2026-07-05-coverage-and-segment-drilldown-design.md`.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite shim migrations in `database.py`), pandas, Alpine.js single-file frontend, pytest.

**Spec deviations (agreed rationale, same observable behavior):**
1. Segment values are attached in `ReconciliationEngine` after compare (not inside each of the 4 backends) — single insertion point, backends untouched.
2. Drilldown endpoint path is `POST /api/runs/{run_id}/results/{result_id}/drilldown` (repo convention — all result endpoints nest under runs), not `/api/results/{id}/drilldown`.
3. Drilldown executes the job query on both engines and groups with pandas instead of SQL `GROUP BY` pushdown — one code path works for both live DBs and simulation `DataFrameQueryEngine`.

---

### Task 1: Segments module (pure functions)

**Files:**
- Create: `etl_framework/reconciliation/segments.py`
- Test: `tests/unit/test_segments.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_segments.py`:

```python
import pytest
from etl_framework.reconciliation.models import MismatchRecord
from etl_framework.reconciliation.segments import (
    pick_auto_segment_columns,
    build_segment_summary,
)


class FakeProfile:
    def __init__(self, column_name, distinct_count):
        self.column_name = column_name
        self.distinct_count = distinct_count


def _mm(mtype="value_diff", segment_values=None):
    return MismatchRecord(
        key_values={"id": 1}, column_name="amt",
        source_value=1, target_value=2, mismatch_type=mtype,
        segment_values=segment_values,
    )


# --- pick_auto_segment_columns ---

def test_auto_pick_respects_distinct_count_cutoff():
    profiles = [FakeProfile("region", 4), FakeProfile("customer_id", 90000)]
    assert pick_auto_segment_columns(profiles, key_columns=["id"]) == ["region"]


def test_auto_pick_excludes_key_columns():
    profiles = [FakeProfile("id", 10), FakeProfile("region", 4)]
    assert pick_auto_segment_columns(profiles, key_columns=["id"]) == ["region"]


def test_auto_pick_max_three_lowest_distinct_first():
    profiles = [
        FakeProfile("a", 40), FakeProfile("b", 2),
        FakeProfile("c", 10), FakeProfile("d", 5),
    ]
    assert pick_auto_segment_columns(profiles, key_columns=[]) == ["b", "d", "c"]


def test_auto_pick_skips_none_distinct_count():
    profiles = [FakeProfile("x", None), FakeProfile("region", 3)]
    assert pick_auto_segment_columns(profiles, key_columns=[]) == ["region"]


def test_auto_pick_empty_profiles_returns_empty():
    assert pick_auto_segment_columns([], key_columns=["id"]) == []


# --- build_segment_summary ---

def test_summary_groups_by_segment_value_with_counts_and_pct():
    mismatches = [
        _mm("value_diff", {"region": "EMEA"}),
        _mm("value_diff", {"region": "EMEA"}),
        _mm("missing_in_target", {"region": "EMEA"}),
        _mm("missing_in_source", {"region": "APAC"}),
    ]
    summary = build_segment_summary(mismatches, ["region"])
    emea = summary["region"][0]
    assert emea["value"] == "EMEA"
    assert emea["mismatch_count"] == 3
    assert emea["value_diff"] == 2
    assert emea["missing_in_target"] == 1
    assert emea["missing_in_source"] == 0
    assert emea["pct_of_total"] == 75.0
    apac = summary["region"][1]
    assert apac["missing_in_source"] == 1


def test_summary_null_segment_value_bucketed_as_null_literal():
    mismatches = [_mm(segment_values={"region": None}), _mm(segment_values=None)]
    summary = build_segment_summary(mismatches, ["region"])
    assert summary["region"][0]["value"] == "(null)"
    assert summary["region"][0]["mismatch_count"] == 2


def test_summary_truncates_to_top_20_values():
    mismatches = []
    for i in range(25):
        # value i appears (i+1) times so ordering is deterministic
        mismatches.extend(_mm(segment_values={"day": f"d{i}"}) for _ in range(i + 1))
    summary = build_segment_summary(mismatches, ["day"])
    assert len(summary["day"]) == 20
    assert summary["day"][0]["value"] == "d24"  # most frequent first


def test_summary_empty_inputs_return_none():
    assert build_segment_summary([], ["region"]) is None
    assert build_segment_summary([_mm()], []) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_segments.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl_framework.reconciliation.segments'` (and `MismatchRecord` has no `segment_values` kwarg — that's Task 2's field; add it now as part of this task, see Step 3).

- [ ] **Step 3: Add `segment_values` field to `MismatchRecord`**

In `etl_framework/reconciliation/models.py`, add one field to `MismatchRecord` (after `relative_delta`):

```python
    segment_values: dict[str, Any] | None = None  # segment column -> value, for drill-down
```

- [ ] **Step 4: Write the segments module**

Create `etl_framework/reconciliation/segments.py`:

```python
"""Segment-based mismatch root-cause helpers.

Pure functions — no DB or engine dependencies.
"""
from __future__ import annotations

from etl_framework.reconciliation.models import MismatchRecord

MAX_AUTO_SEGMENT_COLUMNS = 3
MAX_AUTO_DISTINCT_COUNT = 50
TOP_N_SEGMENT_VALUES = 20


def pick_auto_segment_columns(
    profiles: list,
    key_columns: list[str],
    max_columns: int = MAX_AUTO_SEGMENT_COLUMNS,
    max_distinct: int = MAX_AUTO_DISTINCT_COUNT,
) -> list[str]:
    """Pick candidate segment columns from latest column profiles.

    Low-cardinality columns (distinct_count <= max_distinct) that are not
    key columns, at most max_columns, lowest distinct_count first.
    """
    keys = set(key_columns or [])
    candidates = [
        p for p in profiles
        if p.distinct_count is not None
        and p.distinct_count <= max_distinct
        and p.column_name not in keys
    ]
    candidates.sort(key=lambda p: p.distinct_count)
    return [p.column_name for p in candidates[:max_columns]]


def build_segment_summary(
    mismatches: list[MismatchRecord],
    segment_columns: list[str],
    top_n: int = TOP_N_SEGMENT_VALUES,
) -> dict | None:
    """Group mismatches by each segment column's value.

    Returns {segment_column: [{value, mismatch_count, missing_in_target,
    missing_in_source, value_diff, pct_of_total}, ...]} with the top_n most
    frequent values per column, or None when there is nothing to group.
    """
    total = len(mismatches)
    if not total or not segment_columns:
        return None

    summary: dict[str, list[dict]] = {}
    for col in segment_columns:
        buckets: dict[str, dict] = {}
        for m in mismatches:
            raw = (m.segment_values or {}).get(col)
            value = "(null)" if raw is None else str(raw)
            b = buckets.setdefault(value, {
                "value": value, "mismatch_count": 0,
                "missing_in_target": 0, "missing_in_source": 0, "value_diff": 0,
            })
            b["mismatch_count"] += 1
            if m.mismatch_type in ("missing_in_target", "missing_in_source", "value_diff"):
                b[m.mismatch_type] += 1
        rows = sorted(buckets.values(), key=lambda b: -b["mismatch_count"])[:top_n]
        for b in rows:
            b["pct_of_total"] = round(100.0 * b["mismatch_count"] / total, 2)
        summary[col] = rows
    return summary
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_segments.py -v`
Expected: all PASS

- [ ] **Step 6: Run existing reconciliation tests (field addition must not break anything)**

Run: `python -m pytest tests/unit/test_reconciliation.py tests/unit/test_backends.py tests/unit/test_mismatch_storage.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add etl_framework/reconciliation/segments.py etl_framework/reconciliation/models.py tests/unit/test_segments.py
git commit -m "feat: add segment summary helpers and MismatchRecord.segment_values"
```

---

### Task 2: Engine attaches segment values to mismatches

**Files:**
- Modify: `etl_framework/reconciliation/engine.py` (constructor ~line 25-66, `reconcile` ~line 179-183)
- Test: `tests/unit/test_reconciliation.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_reconciliation.py`:

```python
# --- Segment value enrichment ---

def test_segment_values_attached_from_source_frame():
    source = pd.DataFrame({"id": [1, 2], "region": ["EMEA", "APAC"], "amt": [10, 20]})
    target = pd.DataFrame({"id": [1, 2], "region": ["EMEA", "APAC"], "amt": [10, 99]})
    engine = _make_engine(source, target, segment_columns=["region"])
    result = engine.reconcile("SELECT 1", "q")
    diff = [m for m in result.mismatches if m.mismatch_type == "value_diff"]
    assert diff and diff[0].segment_values == {"region": "APAC"}


def test_segment_values_fall_back_to_target_for_missing_in_source():
    source = pd.DataFrame({"id": [1], "region": ["EMEA"], "amt": [10]})
    target = pd.DataFrame({"id": [1, 2], "region": ["EMEA", "APAC"], "amt": [10, 20]})
    engine = _make_engine(source, target, segment_columns=["region"])
    result = engine.reconcile("SELECT 1", "q")
    miss = [m for m in result.mismatches if m.mismatch_type == "missing_in_source"]
    assert miss and miss[0].segment_values == {"region": "APAC"}


def test_no_segment_columns_leaves_segment_values_none():
    source = pd.DataFrame({"id": [1], "amt": [10]})
    target = pd.DataFrame({"id": [1], "amt": [99]})
    engine = _make_engine(source, target)
    result = engine.reconcile("SELECT 1", "q")
    assert result.mismatches[0].segment_values is None


def test_segment_column_absent_from_frames_is_skipped():
    source = pd.DataFrame({"id": [1], "amt": [10]})
    target = pd.DataFrame({"id": [1], "amt": [99]})
    engine = _make_engine(source, target, segment_columns=["nonexistent"])
    result = engine.reconcile("SELECT 1", "q")
    assert result.mismatches[0].segment_values is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_reconciliation.py -k segment -v`
Expected: FAIL — `TypeError: ReconciliationEngine.__init__() got an unexpected keyword argument 'segment_columns'`

- [ ] **Step 3: Implement engine changes**

In `etl_framework/reconciliation/engine.py`:

a) Add constructor parameter (after `parallel_workers: int = 4,`):

```python
        segment_columns: list[str] | None = None,
```

and in the body (after `self._parallel_workers = parallel_workers`):

```python
        self._segment_columns: list[str] = segment_columns or []
```

b) In `reconcile()`, immediately before `result = dataclasses.replace(result, duration_seconds=time.monotonic() - t0)` (~line 182), insert:

```python
            if self._segment_columns and result.mismatches:
                self._attach_segment_values(result.mismatches, df_source_norm, df_target_norm)
```

c) Add the method (place it after `_rows_to_mismatch_records`, ~line 399):

```python
    def _attach_segment_values(
        self,
        mismatches: list[MismatchRecord],
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
    ) -> None:
        """Set MismatchRecord.segment_values from source rows (target fallback).

        Never raises — drill-down enrichment must not fail a run.
        """
        try:
            seg_cols = [
                c for c in self._segment_columns
                if c in df_source.columns or c in df_target.columns
            ]
            if not seg_cols:
                return

            def build_lookup(df: pd.DataFrame) -> dict:
                cols = [c for c in seg_cols if c in df.columns]
                if not cols or df.empty or not all(k in df.columns for k in self._key_columns):
                    return {}
                lut = {}
                n_keys = len(self._key_columns)
                for row in df[self._key_columns + cols].itertuples(index=False, name=None):
                    lut[row[:n_keys]] = dict(zip(cols, row[n_keys:]))
                return lut

            src_lut = build_lookup(df_source)
            tgt_lut = build_lookup(df_target)
            for m in mismatches:
                key = tuple(m.key_values.get(k) for k in self._key_columns)
                vals = src_lut.get(key) or tgt_lut.get(key)
                if vals is not None:
                    m.segment_values = {c: vals.get(c) for c in seg_cols if c in vals}
        except Exception:  # pragma: no cover - defensive
            logger.warning("segment value enrichment failed; skipping", exc_info=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_reconciliation.py -v`
Expected: all PASS (new + pre-existing)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/engine.py tests/unit/test_reconciliation.py
git commit -m "feat: attach segment values to mismatches in ReconciliationEngine"
```

---

### Task 3: Persistence — `segment_summary` column end to end

**Files:**
- Modify: `etl_framework/reconciliation/models.py` (`ReconciliationResult`)
- Modify: `etl_framework/repository/models.py` (`TestResult`, ~line 150)
- Modify: `etl_framework/repository/database.py` (`_ensure_compare_columns`, ~line 82)
- Modify: `etl_framework/repository/repository.py` (`add_test_result`, ~line 309)
- Modify: `api/schemas.py` (`TestResultOut`, ~line 258)
- Modify: `api/routes/runs.py` (`_test_result_out`, ~line 54)
- Test: `tests/unit/test_mismatch_storage.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mismatch_storage.py` (reuse the file's existing session/repo fixture pattern — it already builds an in-memory DB and a `RunRepository`; follow the same fixture names used at the top of that file):

```python
def test_add_test_result_persists_segment_summary(repo, run_id):
    from datetime import datetime, timezone
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus

    summary = {"region": [{"value": "EMEA", "mismatch_count": 3,
                           "missing_in_target": 1, "missing_in_source": 0,
                           "value_diff": 2, "pct_of_total": 75.0}]}
    result = ReconciliationResult(
        query_name="q", source_env="dev", target_env="qa",
        source_row_count=4, target_row_count=4, matched_count=1,
        missing_in_target_count=1, missing_in_source_count=0,
        value_mismatch_count=2, mismatches=[], status=TestStatus.FAILED,
        executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
        segment_summary=summary,
    )
    tr = repo.add_test_result(run_id, result)
    assert tr.segment_summary == summary
```

Note: if `test_mismatch_storage.py` uses different fixture names (e.g. a `db` fixture and inline `RunRepository(db)`), adapt the test signature to match — the assertion body stays identical.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_mismatch_storage.py -k segment_summary -v`
Expected: FAIL — `TypeError: ReconciliationResult.__init__() got an unexpected keyword argument 'segment_summary'`

- [ ] **Step 3: Implement the five edits**

a) `etl_framework/reconciliation/models.py` — add field to `ReconciliationResult` (after `sample_rows`):

```python
    segment_summary: dict | None = None  # segment col -> top-N mismatch buckets
```

b) `etl_framework/repository/models.py` — in `TestResult`, after `sample_rows = Column(JSON, nullable=True)`:

```python
    segment_summary = Column(JSON, nullable=True)
```

c) `etl_framework/repository/database.py` — in `_ensure_compare_columns`, after the `sample_rows` shim (line 81-82):

```python
        if "segment_summary" not in test_result_cols:
            conn.execute(text("ALTER TABLE test_results ADD COLUMN segment_summary JSON"))
```

d) `etl_framework/repository/repository.py` — in `add_test_result`, after `sample_rows=result.sample_rows,`:

```python
            segment_summary=result.segment_summary,
```

e) `api/schemas.py` — in `TestResultOut`, after `sample_rows`:

```python
    segment_summary: dict | None = None
```

f) `api/routes/runs.py` — in `_test_result_out`, after `sample_rows=result.sample_rows,`:

```python
        segment_summary=result.segment_summary,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_mismatch_storage.py tests/unit/test_repository.py tests/unit/test_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/models.py etl_framework/repository/models.py etl_framework/repository/database.py etl_framework/repository/repository.py api/schemas.py api/routes/runs.py tests/unit/test_mismatch_storage.py
git commit -m "feat: persist segment_summary on test results"
```

---

### Task 4: Executor wiring — resolve segment columns, build summary

**Files:**
- Modify: `api/services/run_executor.py` (`_build_case` ~line 407-433; new helper method)
- Test: `tests/unit/test_run_executor.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_run_executor.py` (follow the file's existing fixture pattern for building a `RunExecutor` with an in-memory DB — reuse its session fixture; the tests below show intent and exact assertions):

```python
def test_resolve_segment_columns_manual_wins(executor_factory):
    from api.schemas import JobDefinition
    ex = executor_factory()
    job = JobDefinition(name="j", query="SELECT 1", key_columns=["id"],
                        params={"segment_columns": ["region", "day"]})
    assert ex._resolve_segment_columns(job) == ["region", "day"]


def test_resolve_segment_columns_auto_from_profiles(executor_factory, db_session):
    from api.schemas import JobDefinition
    from etl_framework.repository.repository import ColumnProfileRepository
    repo = ColumnProfileRepository(db_session)
    repo.save("j", None, "region", 0.0, 4, None, None, None, None, None, None, None, None)
    repo.save("j", None, "customer_id", 0.0, 90000, None, None, None, None, None, None, None, None)
    db_session.commit()
    ex = executor_factory()
    job = JobDefinition(name="j", query="SELECT 1", key_columns=["id"])
    assert ex._resolve_segment_columns(job) == ["region"]


def test_resolve_segment_columns_no_profiles_returns_empty(executor_factory):
    from api.schemas import JobDefinition
    ex = executor_factory()
    job = JobDefinition(name="j", query="SELECT 1", key_columns=["id"])
    assert ex._resolve_segment_columns(job) == []


def test_run_with_segment_columns_persists_summary(executor_factory, db_session):
    """End-to-end in simulation mode: mismatching frames + manual segment_columns
    -> TestResult.segment_summary populated."""
    from etl_framework.repository.models import TestResult
    ex = executor_factory(jobs=[{
        "name": "seg_job", "job_type": "reconciliation",
        "query": "SELECT * FROM t", "key_columns": ["id"],
        "params": {
            "segment_columns": ["region"],
            "source_rows": [{"id": 1, "region": "EMEA", "amt": 10},
                             {"id": 2, "region": "APAC", "amt": 20}],
            "target_rows": [{"id": 1, "region": "EMEA", "amt": 10},
                             {"id": 2, "region": "APAC", "amt": 99}],
        },
    }])
    ex.execute()
    tr = db_session.query(TestResult).filter_by(query_name="seg_job").one()
    assert tr.segment_summary is not None
    assert tr.segment_summary["region"][0]["value"] == "APAC"
    assert tr.segment_summary["region"][0]["mismatch_count"] == 1
```

Adapt fixture names (`executor_factory`, `db_session`) to whatever `test_run_executor.py` already provides for constructing an executor against saved jobs; if no factory exists, build the executor inline the same way that file's existing end-to-end tests do (in-memory DB, `JobRepository` upsert of the job dict, `RunExecutor(db=..., run_id=..., source_env="dev", target_env="qa", job_sequence=["seg_job"], run_settings=RunSettings())`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_run_executor.py -k segment -v`
Expected: FAIL — `AttributeError: 'RunExecutor' object has no attribute '_resolve_segment_columns'`

- [ ] **Step 3: Implement executor changes**

In `api/services/run_executor.py`:

a) Add imports at the top (near the other `etl_framework.reconciliation` imports):

```python
import dataclasses

from etl_framework.reconciliation.segments import (
    build_segment_summary,
    pick_auto_segment_columns,
)
```

b) Add helper method (place near `_build_engines`):

```python
    def _resolve_segment_columns(self, job: JobDefinition) -> list[str]:
        """Manual params.segment_columns wins; else auto-pick from latest profile."""
        manual = job.params.get("segment_columns") or []
        if manual:
            return [str(c) for c in manual]
        try:
            from etl_framework.repository.repository import ColumnProfileRepository
            profiles = ColumnProfileRepository(self._db).get_latest(job.name)
        except Exception:
            return []
        return pick_auto_segment_columns(profiles, job.key_columns or self._settings.key_columns or [])
```

c) In `_build_case`'s inner `run_job()` (~line 407): resolve columns before engine construction, pass to engine, attach summary after DQ/pass-condition:

```python
        def run_job() -> ReconciliationResult:
            source_engine, target_engine = self._build_engines(job)
            segment_columns = self._resolve_segment_columns(job)
            engine = ReconciliationEngine(
                source_engine=source_engine,
                target_engine=target_engine,
                key_columns=job.key_columns or self._settings.key_columns,
                exclude_columns=job.exclude_columns or self._settings.exclude_columns,
                float_tolerance=self._settings.float_tolerance,
                mismatch_row_limit=self._settings.mismatch_row_limit,
                schema_mismatch_policy=self._settings.schema_mismatch_policy,
                null_equals_null=self._settings.null_equals_null,
                chunk_size=self._settings.chunk_size,
                use_hash_precheck=self._settings.use_hash_precheck,
                backend=self._build_backend(job),
                segment_columns=segment_columns,
            )
            max_duration = self._settings.max_duration_seconds or None
            result = engine.reconcile(
                query=job.query,
                query_name=job.name,
                params=job.params,
                max_duration_seconds=max_duration,
            )
            if job.rules:
                result = self._apply_dq_rules(result, job, source_engine)
            if job.pass_condition:
                result = self._apply_pass_condition(result, job, source_engine)
            if segment_columns:
                try:
                    summary = build_segment_summary(result.mismatches, segment_columns)
                    result = dataclasses.replace(result, segment_summary=summary)
                except Exception:
                    logger.warning("segment summary failed for %s", job.name, exc_info=True)
            return result
```

Note: the engine's `_attach_segment_values` only runs on the internal `_compare` path when no backend is set — with a backend, mismatches come from `self._backend.compare(...)` and the engine's enrichment block (Task 2 step 3b) runs for both branches because it sits after the `if/else`. Verify the insertion point from Task 2 is indeed after both branches (before `dataclasses.replace`), which covers backend results too.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_run_executor.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_executor.py
git commit -m "feat: wire segment column resolution and summary into run executor"
```

---

### Task 5: On-demand drilldown endpoint

**Files:**
- Modify: `api/schemas.py` (add request/response models, after `TestResultOut` block ~line 277)
- Modify: `api/routes/runs.py` (new endpoint, place after the mismatch-accept endpoint ~line 700)
- Test: `tests/unit/test_runs_extensions.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_runs_extensions.py` (reuse its existing `client` fixture; if that file lacks one, copy the `client` fixture from `tests/unit/test_api.py` lines 17-39):

```python
def _create_seg_job_and_run(client):
    job = {
        "name": "drill_job", "job_type": "reconciliation",
        "query": "SELECT * FROM t", "key_columns": ["id"],
        "params": {
            "segment_columns": ["region"],
            "source_rows": [{"id": 1, "region": "EMEA", "amt": 10},
                             {"id": 2, "region": "APAC", "amt": 20}],
            "target_rows": [{"id": 1, "region": "EMEA", "amt": 10},
                             {"id": 2, "region": "APAC", "amt": 99},
                             {"id": 3, "region": "APAC", "amt": 5}],
        },
    }
    assert client.post("/api/jobs", json=job).status_code in (200, 201)
    resp = client.post("/api/runs/trigger", json={
        "source_env": "dev", "target_env": "qa",
        "job_names": ["drill_job"], "run_settings": {},
    })
    assert resp.status_code in (200, 201, 202)
    return resp.json()["run_id"]


def test_drilldown_returns_side_by_side_counts(client):
    run_id = _create_seg_job_and_run(client)
    results = client.get(f"/api/runs/{run_id}").json()["results"]
    result_id = results[0]["id"]
    resp = client.post(
        f"/api/runs/{run_id}/results/{result_id}/drilldown",
        json={"segment_column": "region"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["segment_column"] == "region"
    by_value = {row["value"]: row for row in data["rows"]}
    assert by_value["APAC"]["source_count"] == 1
    assert by_value["APAC"]["target_count"] == 2
    assert by_value["APAC"]["delta"] == 1


def test_drilldown_rejects_non_reconciliation_job(client):
    job = {"name": "fresh_job", "job_type": "freshness",
           "query": "SELECT * FROM t", "params": {"timestamp_column": "ts"}}
    assert client.post("/api/jobs", json=job).status_code in (200, 201)
    run = client.post("/api/runs/trigger", json={
        "source_env": "dev", "target_env": "qa",
        "job_names": ["fresh_job"], "run_settings": {},
    }).json()
    results = client.get(f"/api/runs/{run['run_id']}").json()["results"]
    resp = client.post(
        f"/api/runs/{run['run_id']}/results/{results[0]['id']}/drilldown",
        json={"segment_column": "region"},
    )
    assert resp.status_code == 400


def test_drilldown_404_on_unknown_result(client):
    resp = client.post("/api/runs/nope/results/99999/drilldown",
                       json={"segment_column": "region"})
    assert resp.status_code == 404
```

Note: `test_api.py`'s shared fixture monkeypatches `runs_module._execute_run` to a no-op — these tests need the run to actually execute. Follow whatever pattern `test_runs_extensions.py` already uses for synchronous run execution (it tests trends/badges against completed runs); if it relies on the no-op patch, execute the run inline instead: build `RunExecutor` directly as in `test_run_executor.py` and then call the drilldown endpoint. Check the exact trigger endpoint path/payload used elsewhere in that file (`/api/runs/trigger` vs `/api/runs`) and match it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_runs_extensions.py -k drilldown -v`
Expected: FAIL — 404/405 (endpoint does not exist)

- [ ] **Step 3: Add schemas**

In `api/schemas.py`, after the `MismatchOut` block:

```python
class DrilldownRequest(BaseModel):
    segment_column: str = Field(min_length=1)


class DrilldownRow(BaseModel):
    value: str
    source_count: int
    target_count: int
    delta: int


class DrilldownOut(BaseModel):
    segment_column: str
    job_name: str
    rows: list[DrilldownRow]
```

- [ ] **Step 4: Implement the endpoint**

In `api/routes/runs.py` (import `DrilldownRequest`, `DrilldownOut`, `DrilldownRow` in the existing schemas import block), add:

```python
@router.post("/{run_id}/results/{result_id}/drilldown", response_model=DrilldownOut)
def drilldown_result(
    run_id: str,
    result_id: int,
    payload: DrilldownRequest,
    db: Session = Depends(get_session),
):
    """Re-query both sides grouped by a segment column — live counts."""
    from etl_framework.repository.models import TestResult, TestRun, SavedJob
    from api.services.run_executor import RunExecutor
    from api.schemas import RunSettings

    tr = db.get(TestResult, result_id)
    if tr is None or tr.run_id != run_id:
        raise HTTPException(status_code=404, detail="Result not found")
    run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    saved = db.query(SavedJob).filter(SavedJob.name == tr.query_name).first()
    if saved is None:
        raise HTTPException(status_code=404, detail=f"Job '{tr.query_name}' not found")
    if saved.job_type != "reconciliation":
        raise HTTPException(status_code=400,
                            detail="Drill-down is only supported for reconciliation jobs")

    snapshot = run.config_snapshot or {}
    ex = RunExecutor(
        db=db, run_id=f"drilldown-{run_id}",
        source_env=run.source_env or "source",
        target_env=run.target_env or "target",
        job_sequence=[],
        run_settings=RunSettings(
            use_live_connections=bool(snapshot.get("source_credentials")),
        ),
        config_snapshot=snapshot,
    )
    job_def = ex._job_to_definition(saved)
    seg = payload.segment_column

    try:
        src_engine, tgt_engine = ex._build_engines(job_def)
        df_src = src_engine.execute_query(job_def.query, job_def.params)
        df_tgt = tgt_engine.execute_query(job_def.query, job_def.params)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Drill-down query failed: {exc}")

    def counts(df) -> dict[str, int]:
        if df is None or df.empty or seg not in df.columns:
            return {}
        grouped = df.groupby(df[seg].astype(object).where(df[seg].notna(), "(null)")).size()
        return {str(k): int(v) for k, v in grouped.items()}

    src_counts = counts(df_src)
    tgt_counts = counts(df_tgt)
    if not src_counts and not tgt_counts:
        raise HTTPException(status_code=400,
                            detail=f"Segment column '{seg}' not present in either side")

    rows = []
    for value in sorted(set(src_counts) | set(tgt_counts)):
        s, t = src_counts.get(value, 0), tgt_counts.get(value, 0)
        rows.append(DrilldownRow(value=value, source_count=s, target_count=t, delta=t - s))
    rows.sort(key=lambda r: -abs(r.delta))
    return DrilldownOut(segment_column=seg, job_name=tr.query_name, rows=rows)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_runs_extensions.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py api/routes/runs.py tests/unit/test_runs_extensions.py
git commit -m "feat: add on-demand segment drilldown endpoint"
```

---

### Task 6: Coverage service

**Files:**
- Create: `api/services/coverage_service.py`
- Test: `tests/unit/test_coverage_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_coverage_service.py`:

```python
import pandas as pd
import pytest
from api.services.coverage_service import (
    extract_tables,
    compute_flakiness,
    classify_level,
)


# --- extract_tables ---

def test_extract_simple_from():
    assert extract_tables("SELECT * FROM orders") == {"orders"}


def test_extract_join_and_schema_prefix():
    sql = "SELECT * FROM dbo.orders o JOIN [dbo].[customers] c ON o.cid = c.id"
    assert extract_tables(sql) == {"dbo.orders", "dbo.customers"}


def test_extract_strips_quotes():
    assert extract_tables('SELECT * FROM "orders"') == {"orders"}


def test_extract_excludes_cte_names():
    sql = "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent"
    assert extract_tables(sql) == {"orders"}


def test_extract_empty_query():
    assert extract_tables("") == set()


# --- classify_level ---

def test_tested_when_dq_rule_targets_column():
    assert classify_level(
        column="amt", rule_columns={"amt"}, reconciled_columns=set(), observed_columns=set()
    ) == "tested"


def test_tested_when_reconciled():
    assert classify_level(
        column="amt", rule_columns=set(), reconciled_columns={"amt"}, observed_columns={"amt"}
    ) == "tested"


def test_observed_when_only_profiled():
    assert classify_level(
        column="amt", rule_columns=set(), reconciled_columns=set(), observed_columns={"amt"}
    ) == "observed"


def test_untested_otherwise():
    assert classify_level(
        column="amt", rule_columns=set(), reconciled_columns=set(), observed_columns=set()
    ) == "untested"


# --- compute_flakiness ---

def test_flakiness_score_counts_transitions():
    # PASSED,FAILED,PASSED,FAILED = 3 transitions over window 4 -> 3/3 = 1.0
    statuses = ["PASSED", "FAILED", "PASSED", "FAILED"]
    assert compute_flakiness(statuses) == pytest.approx(1.0)


def test_flakiness_stable_history_is_zero():
    assert compute_flakiness(["PASSED"] * 10) == 0.0


def test_flakiness_short_history_is_zero():
    assert compute_flakiness(["PASSED"]) == 0.0
    assert compute_flakiness([]) == 0.0


def test_flakiness_one_transition():
    # 1 transition / 3 = 0.333...
    assert compute_flakiness(["PASSED", "PASSED", "FAILED", "FAILED"]) == pytest.approx(1 / 3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_coverage_service.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement the service**

Create `api/services/coverage_service.py`:

```python
"""Test-coverage matrix, gap report, and flaky-test detection.

Compute-on-read from existing tables (saved_jobs, schema_snapshots,
column_profiles, test_results). Cached in-process with a short TTL,
same pattern as the trend cache in api/routes/runs.py.
"""
from __future__ import annotations

import re
import time

from sqlalchemy import func
from sqlalchemy.orm import Session

from etl_framework.repository.models import (
    ColumnProfile,
    SavedJob,
    SchemaSnapshot,
    TestResult,
    TestRun,
)

_CACHE_TTL_SECONDS = 30
_CACHE: dict[tuple, tuple[float, dict]] = {}

FLAKY_THRESHOLD = 0.3
DEFAULT_FLAKY_WINDOW = 20

# FROM/JOIN followed by an identifier that may be schema-prefixed,
# double-quoted, or [bracketed].  Stops before aliases and parens.
_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+((?:\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][\w$]*)"
    r"(?:\.(?:\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][\w$]*))*)",
    re.IGNORECASE,
)
_CTE_RE = re.compile(r"(?:\bWITH\b|,)\s*([A-Za-z_][\w]*)\s+AS\s*\(", re.IGNORECASE)


def _clean_ident(raw: str) -> str:
    parts = [p.strip('[]"') for p in re.split(r"\.", raw)]
    return ".".join(p for p in parts if p).lower()


def extract_tables(sql: str) -> set[str]:
    """Extract table names referenced in FROM/JOIN clauses.

    Handles schema prefixes, double quotes, and [brackets]; excludes CTE names.
    Intentionally regex-based (no full AST) per the design spec.
    """
    if not sql or not sql.strip():
        return set()
    ctes = {m.group(1).lower() for m in _CTE_RE.finditer(sql)}
    tables = set()
    for m in _TABLE_RE.finditer(sql):
        name = _clean_ident(m.group(1))
        if name and name not in ctes and name != "(":
            tables.add(name)
    return tables


def classify_level(
    column: str,
    rule_columns: set[str],
    reconciled_columns: set[str],
    observed_columns: set[str],
) -> str:
    if column in rule_columns or column in reconciled_columns:
        return "tested"
    if column in observed_columns:
        return "observed"
    return "untested"


def compute_flakiness(statuses: list[str]) -> float:
    """Transitions / (window - 1). Statuses ordered oldest -> newest."""
    if len(statuses) < 2:
        return 0.0
    transitions = sum(1 for a, b in zip(statuses, statuses[1:]) if a != b)
    return transitions / (len(statuses) - 1)


# ---------------------------------------------------------------------------
# DB-backed builders
# ---------------------------------------------------------------------------

_SQL_JOB_TYPES = {"reconciliation", "freshness", "profile", "schema_snapshot"}


def build_coverage(db: Session) -> dict:
    """Build the full coverage matrix response."""
    sig = db.query(func.count(SavedJob.id), func.max(SavedJob.updated_at)).first()
    cache_key = ("coverage", id(db.get_bind()), sig[0], str(sig[1]))
    now = time.monotonic()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    jobs = db.query(SavedJob).filter(SavedJob.enabled.is_(True)).all()

    # Per-job column knowledge
    snapshot_cols: dict[str, set[str]] = {}
    for snap in _latest_snapshots(db):
        cols = {c.get("name", "").lower() for c in (snap.columns or []) if c.get("name")}
        snapshot_cols.setdefault(snap.job_name, set()).update(cols)

    profile_cols: dict[str, set[str]] = {}
    for job_name, column_name in db.query(
        ColumnProfile.job_name, ColumnProfile.column_name
    ).distinct():
        profile_cols.setdefault(job_name, set()).add(column_name.lower())

    tables: dict[str, dict] = {}
    for job in jobs:
        if job.job_type not in _SQL_JOB_TYPES:
            continue
        job_tables = extract_tables(job.query or "")
        rules = (job.params or {}).get("rules") or []
        rule_columns = {str(r.get("column", "")).lower() for r in rules if r.get("column")}
        keys = {k.lower() for k in (job.key_columns or [])}
        excludes = {c.lower() for c in (job.exclude_columns or [])}
        observed = snapshot_cols.get(job.name, set()) | profile_cols.get(job.name, set())
        if job.job_type == "reconciliation":
            reconciled = (observed - excludes) | keys
        else:
            reconciled = set()
        all_columns = observed | rule_columns | keys

        for table in job_tables:
            entry = tables.setdefault(table, {"table": table, "columns": {}, "jobs": set()})
            entry["jobs"].add(job.name)
            for col in all_columns:
                level = classify_level(col, rule_columns, reconciled, observed)
                cur = entry["columns"].get(col)
                rank = {"tested": 2, "observed": 1, "untested": 0}
                if cur is None or rank[level] > rank[cur["level"]]:
                    entry["columns"][col] = {
                        "column": col, "level": level,
                        "jobs": sorted({job.name} | set(cur["jobs"] if cur else [])),
                        "rules": sorted(
                            {r.get("type") for r in rules
                             if str(r.get("column", "")).lower() == col}
                            | set(cur["rules"] if cur else [])
                        ),
                    }
                elif cur is not None and job.name not in cur["jobs"]:
                    cur["jobs"] = sorted(set(cur["jobs"]) | {job.name})

    out_tables = []
    total_cols = tested_cols = observed_only = 0
    for table in sorted(tables):
        entry = tables[table]
        cols = sorted(entry["columns"].values(), key=lambda c: c["column"])
        n = len(cols)
        t = sum(1 for c in cols if c["level"] == "tested")
        o = sum(1 for c in cols if c["level"] == "observed")
        total_cols += n
        tested_cols += t
        observed_only += o
        out_tables.append({
            "table": table,
            "columns": cols,
            "job_count": len(entry["jobs"]),
            "tested_pct": round(100.0 * t / n, 1) if n else 0.0,
        })

    result = {
        "tables": out_tables,
        "summary": {
            "tables": len(out_tables),
            "columns": total_cols,
            "tested_pct": round(100.0 * tested_cols / total_cols, 1) if total_cols else 0.0,
            "observed_pct": round(100.0 * observed_only / total_cols, 1) if total_cols else 0.0,
        },
    }
    _CACHE[cache_key] = (now, result)
    if len(_CACHE) > 100:
        cutoff = now - _CACHE_TTL_SECONDS
        for k in [k for k, (ts, _) in _CACHE.items() if ts < cutoff]:
            _CACHE.pop(k, None)
    return result


def _latest_snapshots(db: Session) -> list[SchemaSnapshot]:
    sub = (
        db.query(
            SchemaSnapshot.job_name,
            func.max(SchemaSnapshot.captured_at).label("max_captured"),
        )
        .group_by(SchemaSnapshot.job_name)
        .subquery()
    )
    return (
        db.query(SchemaSnapshot)
        .join(sub, (SchemaSnapshot.job_name == sub.c.job_name)
              & (SchemaSnapshot.captured_at == sub.c.max_captured))
        .all()
    )


def build_flaky_report(db: Session, window: int = DEFAULT_FLAKY_WINDOW) -> list[dict]:
    """Flakiness per query_name over the last `window` completed runs."""
    rows = (
        db.query(TestResult.query_name, TestResult.status,
                 TestResult.override_status, TestRun.completed_at)
        .join(TestRun, TestRun.run_id == TestResult.run_id)
        .filter(TestRun.completed_at.isnot(None))
        .filter(~TestResult.status.in_(["SKIPPED", "CANCELLED"]))
        .order_by(TestResult.query_name, TestRun.completed_at.desc())
        .all()
    )
    by_job: dict[str, list[str]] = {}
    for query_name, status, override_status, _completed in rows:
        history = by_job.setdefault(query_name, [])
        if len(history) < window:
            history.append(override_status or status)

    report = []
    for query_name, newest_first in by_job.items():
        statuses = list(reversed(newest_first))  # oldest -> newest
        score = compute_flakiness(statuses)
        if score <= 0:
            continue
        report.append({
            "job": query_name,
            "query_name": query_name,
            "score": round(score, 3),
            "transitions": sum(1 for a, b in zip(statuses, statuses[1:]) if a != b),
            "window": len(statuses),
            "flaky": score >= FLAKY_THRESHOLD,
            "recent_statuses": newest_first[:10],
        })
    report.sort(key=lambda r: -r["score"])
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_coverage_service.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/coverage_service.py tests/unit/test_coverage_service.py
git commit -m "feat: add coverage service with matrix, levels, and flakiness"
```

---

### Task 7: Coverage routes

**Files:**
- Create: `api/routes/coverage.py`
- Modify: `api/main.py` (register router, after line 58)
- Test: `tests/unit/test_coverage_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_coverage_routes.py` (copy the `client` fixture from `tests/unit/test_api.py` lines 17-39 verbatim):

```python
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.routes import runs as runs_module
from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository
from api.main import app


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(runs_module, "_execute_run", lambda *args, **kwargs: None)
    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


def test_coverage_empty_db_returns_empty_shape(client):
    resp = client.get("/api/coverage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tables"] == []
    assert data["summary"]["tables"] == 0


def test_coverage_reflects_saved_job(client):
    job = {
        "name": "cov_job", "job_type": "reconciliation",
        "query": "SELECT * FROM orders", "key_columns": ["id"],
        "rules": [{"type": "not_null", "column": "amt"}],
    }
    assert client.post("/api/jobs", json=job).status_code in (200, 201)
    data = client.get("/api/coverage").json()
    assert data["summary"]["tables"] == 1
    table = data["tables"][0]
    assert table["table"] == "orders"
    cols = {c["column"]: c for c in table["columns"]}
    assert cols["amt"]["level"] == "tested"
    assert "not_null" in cols["amt"]["rules"]
    assert cols["id"]["level"] == "tested"  # key column


def test_flaky_empty_db(client):
    resp = client.get("/api/coverage/flaky")
    assert resp.status_code == 200
    assert resp.json() == []


def test_flaky_window_param_validated(client):
    assert client.get("/api/coverage/flaky?window=1").status_code == 422
    assert client.get("/api/coverage/flaky?window=500").status_code == 422


def test_coverage_requires_auth(client):
    # a bare request without the bearer header
    resp = client.get("/api/coverage", headers={"Authorization": ""})
    assert resp.status_code in (401, 403)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_coverage_routes.py -v`
Expected: FAIL — 404 on `/api/coverage`

- [ ] **Step 3: Implement the route**

Create `api/routes/coverage.py`:

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.services.coverage_service import (
    DEFAULT_FLAKY_WINDOW,
    build_coverage,
    build_flaky_report,
)

router = APIRouter(tags=["coverage"])


@router.get("")
def get_coverage(db: Session = Depends(get_session)):
    """Test-coverage matrix: tables/columns vs jobs, rules, and coverage level."""
    return build_coverage(db)


@router.get("/flaky")
def get_flaky(
    window: int = Query(DEFAULT_FLAKY_WINDOW, ge=2, le=200),
    db: Session = Depends(get_session),
):
    """Flaky tests: status flip-flop score over the last `window` runs."""
    return build_flaky_report(db, window=window)
```

In `api/main.py`, add the import beside the other route imports and register after line 58:

```python
app.include_router(coverage_routes.router, prefix="/api/coverage")
```

(import as `from api.routes import coverage as coverage_routes` in the existing import block).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_coverage_routes.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add api/routes/coverage.py api/main.py tests/unit/test_coverage_routes.py
git commit -m "feat: add /api/coverage and /api/coverage/flaky endpoints"
```

---

### Task 8: Frontend — Coverage sub-tab

**Files:**
- Modify: `frontend/index.html` (sub-tab buttons ~line 2060-2065; new panel after the Schema panel ~line 2268)
- Modify: `frontend/app.js` (state ~line 245; loader functions near `loadLineage` ~line 3488)

No automated UI tests in this repo (Alpine single-file app); verification is manual via the running app (Step 4).

- [ ] **Step 1: Add state and loaders to `frontend/app.js`**

Near `historySubTab: 'runs',` (line 245), add:

```javascript
    coverageData: null,
    coverageLoading: false,
    coverageGapsOnly: false,
    flakyData: null,
```

After the `loadLineage()` function (~line 3497), add:

```javascript
    // ===========================================================
    // COVERAGE
    // ===========================================================
    async loadCoverage() {
      this.coverageLoading = true;
      try {
        this.coverageData = await api('GET', '/api/coverage');
        this.flakyData = await api('GET', '/api/coverage/flaky');
      } catch (e) {
        if (!this.handleAuthError(e)) this.toast('error', 'Coverage load failed', e.message);
      } finally {
        this.coverageLoading = false;
      }
    },

    coverageColumns(table) {
      const cols = table.columns || [];
      return this.coverageGapsOnly ? cols.filter(c => c.level === 'untested') : cols;
    },

    coverageLevelClass(level) {
      return {
        tested: 'bg-emerald-100 text-emerald-700',
        observed: 'bg-amber-100 text-amber-700',
        untested: 'bg-rose-100 text-rose-700',
      }[level] || 'bg-slate-100 text-slate-600';
    },
```

- [ ] **Step 2: Add the sub-tab button and panel to `frontend/index.html`**

After the Schema button (line 2065), add:

```html
        <button @click="historySubTab='coverage'; loadCoverage()" :class="historySubTab==='coverage' ? 'text-indigo-600 border-b-2 border-indigo-600 pb-2 -mb-2' : 'text-slate-500'" class="px-3 py-1 text-sm font-medium">Coverage</button>
```

After the Schema panel's closing `</div>` (the `x-show="historySubTab==='schema'"` block starting at line 2268 — find its matching close), add:

```html
      <!-- Coverage sub-tab -->
      <div x-show="historySubTab==='coverage'">
        <div class="flex items-center gap-4 mb-3">
          <template x-if="coverageData">
            <div class="text-sm text-muted">
              <span x-text="coverageData.summary.tables"></span> tables ·
              <span x-text="coverageData.summary.columns"></span> columns ·
              <span class="text-emerald-600 font-semibold" x-text="coverageData.summary.tested_pct + '% tested'"></span> ·
              <span class="text-amber-600" x-text="coverageData.summary.observed_pct + '% observed only'"></span>
            </div>
          </template>
          <label class="flex items-center gap-1 text-xs text-muted ml-auto">
            <input type="checkbox" x-model="coverageGapsOnly"> Gaps only
          </label>
          <button @click="coverageData=null; loadCoverage()" class="btn-secondary btn-sm text-xs">Refresh</button>
        </div>
        <div x-show="coverageLoading" class="text-muted text-sm">Loading…</div>
        <template x-if="coverageData && coverageData.tables.length === 0">
          <div class="text-muted text-sm">No coverage data yet — save jobs with SQL queries first.</div>
        </template>
        <template x-for="t in (coverageData?.tables || [])" :key="t.table">
          <div class="mb-4 border border-slate-200 rounded-lg overflow-hidden">
            <div class="px-3 py-2 bg-slate-50 flex items-center gap-3">
              <span class="font-mono font-semibold text-sm" x-text="t.table"></span>
              <span class="text-xs text-muted" x-text="t.job_count + ' job(s)'"></span>
              <span class="ml-auto text-xs font-semibold"
                    :class="t.tested_pct >= 70 ? 'text-emerald-600' : (t.tested_pct >= 30 ? 'text-amber-600' : 'text-rose-600')"
                    x-text="t.tested_pct + '% tested'"></span>
            </div>
            <div class="px-3 py-2 flex flex-wrap gap-1">
              <template x-for="c in coverageColumns(t)" :key="c.column">
                <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono"
                      :class="coverageLevelClass(c.level)"
                      :title="'jobs: ' + c.jobs.join(', ') + (c.rules.length ? ' | rules: ' + c.rules.join(', ') : '')">
                  <span x-text="c.column"></span>
                </span>
              </template>
              <span x-show="coverageColumns(t).length === 0" class="text-xs text-muted">No gaps 🎉</span>
            </div>
          </div>
        </template>
        <!-- Flaky tests -->
        <template x-if="flakyData && flakyData.length > 0">
          <div class="mt-6">
            <div class="text-sm font-semibold mb-2">Flaky tests (last 20 runs)</div>
            <template x-for="f in flakyData" :key="f.query_name">
              <div class="flex items-center gap-3 text-xs py-1">
                <span class="font-mono w-48 truncate" x-text="f.query_name"></span>
                <div class="flex-1 bg-slate-100 rounded h-2 max-w-48">
                  <div class="h-2 rounded" :class="f.flaky ? 'bg-rose-500' : 'bg-amber-400'"
                       :style="'width:' + Math.min(100, f.score * 100) + '%'"></div>
                </div>
                <span class="font-semibold w-12" x-text="f.score"></span>
                <span class="text-muted" x-text="f.transitions + ' flips / ' + f.window + ' runs'"></span>
              </div>
            </template>
          </div>
        </template>
      </div>
```

- [ ] **Step 3: Run the API smoke test (catches template syntax errors that break the page)**

Run: `python -m pytest tests/integration/test_api_frontend_smoke.py -q`
Expected: PASS

- [ ] **Step 4: Manual verify**

Start the app (`python -m uvicorn api.main:app --port 8000`), open History → Coverage. Confirm: summary line renders, table cards render with color-coded column chips, "Gaps only" filter works, flaky section appears when history exists.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "feat: add Coverage sub-tab with matrix, gaps filter, and flaky list"
```

---

### Task 9: Frontend — Segments panel on result detail

**Files:**
- Modify: `frontend/index.html` (after the Mismatch Distribution block, lines 1986-2010)
- Modify: `frontend/app.js` (state + drilldown loader near `loadMismatchDist` ~line 3475)

- [ ] **Step 1: Add state and loader to `frontend/app.js`**

Near `mismatchDist` state declaration (search for `mismatchDist: {}` in the data object), add:

```javascript
    segmentDrill: {},
    segmentDrillLoading: {},
```

After `loadMismatchDist` (~line 3483), add:

```javascript
    async loadSegmentDrill(runId, result, segmentColumn) {
      const key = result.id + ':' + segmentColumn;
      this.segmentDrillLoading = { ...this.segmentDrillLoading, [key]: true };
      try {
        const data = await api('POST', `/api/runs/${runId}/results/${result.id}/drilldown`,
                               { segment_column: segmentColumn });
        this.segmentDrill = { ...this.segmentDrill, [key]: data.rows };
      } catch (e) {
        if (!this.handleAuthError(e)) this.toast('error', 'Drill-down failed', e.message);
      } finally {
        this.segmentDrillLoading = { ...this.segmentDrillLoading, [key]: false };
      }
    },

    segmentMax(rows) {
      return Math.max(1, ...(rows || []).map(r => r.mismatch_count));
    },
```

- [ ] **Step 2: Add the Segments panel to `frontend/index.html`**

Immediately after the Mismatch Distribution `</div>` (line 2010), inside the same expanded-result container, add:

```html
                        <!-- Segment breakdown (stored at run time) -->
                        <template x-if="r.segment_summary">
                          <div class="px-3 py-2 border-t border-slate-100 mt-2">
                            <div class="text-xs text-muted font-semibold mb-1">Mismatches by segment</div>
                            <template x-for="[segCol, buckets] in Object.entries(r.segment_summary || {})" :key="segCol">
                              <div class="mb-2">
                                <div class="flex items-center gap-2 mb-1">
                                  <span class="text-xs font-mono text-indigo-600" x-text="segCol"></span>
                                  <button @click="loadSegmentDrill(selectedRun.run_id, r, segCol)"
                                          :disabled="segmentDrillLoading[r.id + ':' + segCol]"
                                          class="text-indigo-500 hover:underline text-xs ml-auto">
                                    <span x-show="!segmentDrillLoading[r.id + ':' + segCol]">🔄 Re-query now</span>
                                    <span x-show="segmentDrillLoading[r.id + ':' + segCol]">Loading…</span>
                                  </button>
                                </div>
                                <template x-for="b in buckets" :key="b.value">
                                  <div class="flex items-center gap-2 text-xs py-0.5">
                                    <span class="font-mono w-28 truncate" x-text="b.value"></span>
                                    <div class="flex-1 bg-slate-100 rounded h-2 max-w-56">
                                      <div class="h-2 rounded bg-rose-400"
                                           :style="'width:' + Math.min(100, 100 * b.mismatch_count / segmentMax(buckets)) + '%'"></div>
                                    </div>
                                    <span class="text-slate-500 font-semibold" x-text="b.mismatch_count + 'x'"></span>
                                    <span class="text-muted" x-text="'(' + b.pct_of_total + '%)'"></span>
                                  </div>
                                </template>
                                <template x-if="segmentDrill[r.id + ':' + segCol]">
                                  <div class="mt-1 pl-2 border-l-2 border-indigo-200">
                                    <div class="text-xs text-muted mb-0.5">Live counts (source vs target)</div>
                                    <template x-for="row in segmentDrill[r.id + ':' + segCol]" :key="row.value">
                                      <div class="flex items-center gap-2 text-xs py-0.5">
                                        <span class="font-mono w-28 truncate" x-text="row.value"></span>
                                        <span class="text-slate-600" x-text="row.source_count"></span>
                                        <span class="text-muted">vs</span>
                                        <span class="text-slate-600" x-text="row.target_count"></span>
                                        <span class="font-semibold"
                                              :class="row.delta === 0 ? 'text-emerald-600' : 'text-rose-500'"
                                              x-text="(row.delta > 0 ? '+' : '') + row.delta"></span>
                                      </div>
                                    </template>
                                  </div>
                                </template>
                              </div>
                            </template>
                          </div>
                        </template>
```

- [ ] **Step 3: Run the API smoke test**

Run: `python -m pytest tests/integration/test_api_frontend_smoke.py -q`
Expected: PASS

- [ ] **Step 4: Manual verify**

Create a reconciliation job with `params.segment_columns = ["region"]` and mismatching `source_rows`/`target_rows` (as in Task 5's test payload), run it, open History → run → expand the failed result. Confirm: "Mismatches by segment" bars render; "Re-query now" shows live source-vs-target counts with deltas.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "feat: add segment breakdown panel with live drill-down to result view"
```

---

### Task 10: README + full suite

**Files:**
- Modify: `README.md` (Capabilities list ~line 96; API usage section)

- [ ] **Step 1: Add capabilities bullets**

In `README.md` Capabilities section (after the Profile API bullet, ~line 96), add:

```markdown
- **Coverage matrix** — `GET /api/coverage` maps every table/column seen by the framework to the jobs and DQ rules covering it, with `tested` / `observed` / `untested` levels and a gap filter in the History → Coverage sub-tab.
- **Flaky-test detection** — `GET /api/coverage/flaky?window=20` scores each job by pass/fail flip-flops across recent runs (transitions ÷ window); scores ≥ 0.3 are flagged.
- **Mismatch segment drill-down** — configure `params.segment_columns` on a reconciliation job (or let the framework auto-pick low-cardinality columns from the latest profile) and each failed result stores a per-segment mismatch summary; `POST /api/runs/{run_id}/results/{result_id}/drilldown` re-queries live per-segment row counts on both sides.
```

- [ ] **Step 2: Add API usage entries**

In the README's API Usage section, add the three endpoints with one-line curl examples following the section's existing format:

```markdown
# Coverage matrix and flaky tests
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/coverage
curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8000/api/coverage/flaky?window=20"

# Segment drill-down (live re-query)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"segment_column": "region"}' \
  http://127.0.0.1:8000/api/runs/<run_id>/results/<result_id>/drilldown
```

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document coverage, flaky detection, and segment drill-down"
```
