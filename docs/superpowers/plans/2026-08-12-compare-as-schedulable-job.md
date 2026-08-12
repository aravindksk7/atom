# Compare as a Schedulable Job — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a comparison configured in the Compare tab be saved as a job, then launched from the Job Catalog or fired by cron through a selection + schedule.

**Architecture:** A new `job_type: "compare"` stores the verbatim compare request body under `params.request`, so `RunExecutor._build_case_compare` can revalidate it into `BOCompareRequest`/`ReconFileCompareRequest` and hand it to the same code the HTTP endpoint calls. To make that possible, each `CompareService.run_*` method is split into a pure core that *returns* a `ReconciliationResult` and a thin wrapper that keeps today's run bookkeeping — the three endpoints keep their exact behavior. Multi-file is not part of the new job type: its Save-as-Job emits the `reconciliation` + `source_mode=multi_file` job that already exists and already schedules.

**Tech Stack:** FastAPI + Pydantic v2 + SQLAlchemy (backend), Alpine.js feature-slice pattern (frontend), pytest (backend tests), Playwright (e2e).

Spec: `docs/superpowers/specs/2026-08-12-compare-as-schedulable-job-design.md`

---

## File Structure

**Backend — modified**

| File | Responsibility after this work |
|---|---|
| `api/services/scheduler.py` | Scheduled runs carry the selection's `config_id` (Task 1) |
| `api/services/compare_service.py` | Gains pure cores (`compare_bo`, `compare_recon_file`), shared helpers (`_persist_single_result`, `_tabular_file_result`, `_require_matching_recon_kinds`) and two module functions (`_compare_report_stats`, `aggregate_stat_results`). The three `run_*` wrappers keep their signatures and behavior |
| `api/schemas.py` | `compare` added to `JobDefinition.job_type`; validator rejects non-repeatable sources and mirrors column config |
| `etl_framework/runner/job_validation.py` | `compare` branch: error issues for a malformed params contract, warning issues for ignored fields |
| `api/services/run_executor.py` | `_build_case_compare` + one dispatch line |

**Frontend — modified**

| File | Responsibility after this work |
|---|---|
| `frontend/features/compare.js` | Payload builders extracted from the three `run*` methods; Save-as-Job state, validation mirror, and `saveCompareAsJob()` |
| `frontend/partials/tab-compare.html` | Save as Job button per sub-tab, one shared save dialog, `data-testid`s on the BO path inputs |
| `frontend/partials/tab-launch.html` | `compare` in the job-type dropdown |

**Tests — created**

- `tests/unit/test_compare_cores.py` — the pure cores and the two module functions
- `tests/unit/test_compare_job_type.py` — `compare` job validation, both layers
- `tests/unit/test_run_executor_compare.py` — dispatch and result naming
- `tests/e2e/26-compare-save-as-job.spec.ts` — save from the Compare tab, appear in the Job Catalog

**Tests — appended**

- `tests/unit/test_scheduler.py`, `tests/unit/test_compare_api.py`

---

## Phase 1 — Scheduler prerequisite

### Task 1: Scheduled runs carry the selection's Saved Config

`_run_schedule` builds its `RunTrigger` without `config_id`, so `_snapshot_from_trigger` never resolves the selection's Saved Config and no live job in a scheduled run gets credentials. Selection *launch* already does this (`api/routes/selections.py:223`). Nothing downstream in this plan works on a schedule until this is fixed.

**Files:**
- Modify: `api/services/scheduler.py:140-145`
- Test: `tests/unit/test_scheduler.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_scheduler.py`:

```python
def test_run_schedule_passes_the_selections_config_id_into_the_run_snapshot(monkeypatch):
    from api.services import scheduler as svc
    from etl_framework.repository.database import Base
    import etl_framework.repository.database as _db_module
    from etl_framework.repository.repository import ConfigRepository

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)
    previous = _db_module.SessionLocal
    _db_module.SessionLocal = testing_session
    try:
        from api.routes import runs as runs_route

        db = testing_session()
        cfg = ConfigRepository(db).create("bo prod", "prod", {"host": "bo.example.com"})
        selection = JobSelectionRepository(db).create(
            name="nightly selection",
            description="",
            tags=[],
            job_sequence=["orders"],
            run_settings={},
            config_id=cfg.id,
        )
        schedule = ScheduleRepository(db).create(_sched_data(
            selection_id=selection.id,
            selection_version=1,
        ))
        schedule_id, schedule_name, config_id = schedule.id, schedule.name, cfg.id
        db.close()

        executed = []
        monkeypatch.setattr(runs_route, "_execute_run", lambda **kwargs: executed.append(kwargs))
        svc._run_schedule(schedule_id, schedule_name)

        assert len(executed) == 1
        assert executed[0]["config_snapshot"]["config_id"] == config_id
    finally:
        _db_module.SessionLocal = previous
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/test_scheduler.py -k "selections_config_id" -v`
Expected: FAIL with `KeyError: 'config_id'` — the snapshot has no config because the trigger never carried one.

- [ ] **Step 3: Pass the config through**

In `api/services/scheduler.py`, replace the `trigger = RunTrigger(...)` block (lines 140-145):

```python
        trigger = RunTrigger(
            source_env=sched.source_env,
            target_env=sched.target_env,
            job_sequence=version.job_sequence or [],
            run_settings=version.run_settings_json or {},
            # Without this, a scheduled run resolves no Saved Config and every
            # live job in it (bo_report, automic_job, compare) runs without
            # credentials. Selection launch already does this — see
            # api/routes/selections.py's launch handler.
            config_id=version.config_id,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/test_scheduler.py -k "selections_config_id" -v`
Expected: PASS

- [ ] **Step 5: Run the whole scheduler test file**

Run: `python -m pytest tests/unit/test_scheduler.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/services/scheduler.py tests/unit/test_scheduler.py
git commit -m "fix(scheduler): pass the selection's saved config into scheduled runs"
```

---

## Phase 2 — Compare cores

### Task 2: `compare_bo` pure core

**Files:**
- Modify: `api/services/compare_service.py:139-189`
- Test: `tests/unit/test_compare_cores.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_compare_cores.py`:

```python
"""Pure compare cores: return a result, touch no run bookkeeping."""
from __future__ import annotations

import base64

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import ConfigRepository, RunRepository


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_compare_bo_returns_a_result_and_writes_no_run():
    from api.schemas import BOCompareRequest, SourceConfig
    from api.services.compare_service import CompareService

    db = _session()
    svc = CompareService(db, ConfigRepository(db))
    req = BOCompareRequest(
        source_a=SourceConfig(
            source_type="upload", file_content_b64=_b64("id,value\n1,alpha\n"), file_name="a.csv",
        ),
        source_b=SourceConfig(
            source_type="upload", file_content_b64=_b64("id,value\n1,beta\n"), file_name="b.csv",
        ),
        key_columns=["id"],
    )

    result = svc.compare_bo(req, None)

    assert result.value_mismatch_count == 1
    assert RunRepository(db).list_runs() == []


def test_compare_bo_falls_back_to_positional_keys_when_no_shared_id_column():
    from api.schemas import BOCompareRequest, SourceConfig
    from api.services.compare_service import CompareService

    db = _session()
    svc = CompareService(db, ConfigRepository(db))
    req = BOCompareRequest(
        source_a=SourceConfig(
            source_type="upload", file_content_b64=_b64("value\nalpha\n"), file_name="a.csv",
        ),
        source_b=SourceConfig(
            source_type="upload", file_content_b64=_b64("value\nalpha\n"), file_name="b.csv",
        ),
    )

    result = svc.compare_bo(req, None)

    assert result.value_mismatch_count == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_cores.py -v`
Expected: FAIL with `AttributeError: 'CompareService' object has no attribute 'compare_bo'`

- [ ] **Step 3: Split the method**

In `api/services/compare_service.py`, replace `run_bo_comparison` (lines 139-189) with the core, the shared persistence helper, and the thin wrapper:

```python
    def compare_bo(self, req: BOCompareRequest, run_id: str | None = None) -> ReconciliationResult:
        """Compare two BO-report sources and return the result.

        Pure on purpose: no run status, no persistence. A saved compare job
        calls this under RunExecutor's one-case-one-result contract, while the
        HTTP endpoint keeps its own bookkeeping in run_bo_comparison().
        run_id is still passed down because a live/api source stores its pull
        under that run.
        """
        df_a = self._load_bo_source(req.source_a, req.doc_id, req.report_id, run_id)
        df_b = self._load_bo_source(req.source_b, req.doc_id, req.report_id, run_id)
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

        engine_a = FrameEngine(df_a, req.label_a)
        engine_b = FrameEngine(df_b, req.label_b)
        reconciler = _build_engine(
            engine_a, engine_b,
            key_columns=key_columns,
            exclude_columns=req.exclude_columns or [],
            mismatch_row_limit=_compare_mismatch_row_limit(getattr(req, "advanced", None)),
            adv=getattr(req, "advanced", None),
        )
        return reconciler.reconcile(_SENTINEL_QUERY, req.label_a or "bo_comparison")

    def _persist_single_result(self, run_id: str, result) -> None:
        """Store one result and close out its run as PASSED/FAILED."""
        tr = self._repo.add_test_result(run_id, result)
        if result.mismatches:
            self._repo.add_mismatch_details(tr.id, result.mismatches)
        MetricsWriter(f"logs/metrics_{run_id}.json").write(run_id, [result])
        passed = 1 if result.status == TestStatus.PASSED else 0
        self._repo.update_run_status(
            run_id, "PASSED" if passed else "FAILED",
            completed_at=datetime.now(timezone.utc),
            total_tests=1, passed=passed, failed=0 if passed else 1,
        )

    def run_bo_comparison(self, req: BOCompareRequest, run_id: str) -> None:
        """Execute BO comparison and persist as TestRun/TestResult/MismatchDetail."""
        try:
            self._repo.update_run_status(run_id, "RUNNING", started_at=datetime.now(timezone.utc))
            self._persist_single_result(run_id, self.compare_bo(req, run_id))
        except Exception as exc:
            logger.exception("BO comparison failed for run %s", run_id)
            self._add_error_result(run_id, req.label_a or "bo_comparison", exc)
            self._repo.update_run_status(
                run_id, "ERROR",
                completed_at=datetime.now(timezone.utc),
                total_tests=1,
                error=1,
            )
            raise
```

`ReconciliationResult` needs no import for the annotation — this module has `from __future__ import annotations` at line 1, so annotations are never evaluated.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_cores.py -v`
Expected: PASS

- [ ] **Step 5: Verify the endpoint is unchanged**

Run: `python -m pytest tests/unit/test_compare_api.py tests/unit/test_bo_compare_prompts.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/services/compare_service.py tests/unit/test_compare_cores.py
git commit -m "refactor: split a pure compare_bo core out of run_bo_comparison"
```

---

### Task 3: `_compare_report_stats` shared helper

The report-shaped branch of `run_recon_file_compare` builds N synthetic results inline. Extract it so the job path can reuse it without inheriting the endpoint's persistence.

**Files:**
- Modify: `api/services/compare_service.py:517-588`
- Test: `tests/unit/test_compare_cores.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_compare_cores.py`:

```python
def test_compare_report_stats_returns_one_result_per_test_name():
    from api.services.compare_service import _compare_report_stats

    stats_a = {
        "orders": {"status": "PASSED", "source_row_count": 10, "target_row_count": 10, "total_issues": 0},
        "customers": {"status": "PASSED", "source_row_count": 5, "target_row_count": 5, "total_issues": 0},
    }
    stats_b = {
        "orders": {"status": "PASSED", "source_row_count": 10, "target_row_count": 10, "total_issues": 0},
        "customers": {"status": "FAILED", "source_row_count": 5, "target_row_count": 4, "total_issues": 1},
    }

    pairs = _compare_report_stats(stats_a, stats_b, "Run A", "Report B")

    assert [result.query_name for result, _ in pairs] == ["customers", "orders"]
    by_name = {result.query_name: (result, records) for result, records in pairs}
    assert by_name["orders"][0].status.value == "PASSED"
    assert by_name["orders"][1] == []
    assert by_name["customers"][0].status.value == "FAILED"
    assert {r.column_name for r in by_name["customers"][1]} == {
        "status", "target_row_count", "total_issues",
    }


def test_compare_report_stats_marks_a_test_present_on_only_one_side_as_failed():
    from api.services.compare_service import _compare_report_stats

    pairs = _compare_report_stats(
        {"orders": {"status": "PASSED", "source_row_count": 1, "target_row_count": 1, "total_issues": 0}},
        {},
        "Run A",
        "Report B",
    )

    assert [result.status.value for result, _ in pairs] == ["FAILED"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_cores.py -k "report_stats" -v`
Expected: FAIL with `ImportError: cannot import name '_compare_report_stats'`

- [ ] **Step 3: Add the module function**

In `api/services/compare_service.py`, add after `_build_engine` (which ends at line 126, just before `class CompareService`):

```python
def _compare_report_stats(
    stats_a: dict[str, dict],
    stats_b: dict[str, dict],
    label_a: str,
    label_b: str,
) -> list[tuple["ReconciliationResult", list["MismatchRecord"]]]:
    """Compare two report-shaped sources test by test.

    Returns one (result, mismatch records) pair per test name found on either
    side. Two consumers with different needs: the HTTP endpoint persists every
    pair, which is what the Compare tab renders today, while a saved compare
    job folds the results into one via aggregate_stat_results() because
    RunExecutor gives each job case exactly one result.
    """
    from etl_framework.reconciliation.models import ReconciliationResult, MismatchRecord
    from etl_framework.runner.state import TestStatus as TS

    compared_metrics = ("status", "source_row_count", "target_row_count", "total_issues")
    pairs: list[tuple[ReconciliationResult, list[MismatchRecord]]] = []
    for name in sorted(set(stats_a) | set(stats_b)):
        a = stats_a.get(name, {})
        b = stats_b.get(name, {})
        differing = [metric for metric in compared_metrics if a.get(metric) != b.get(metric)]
        ok = bool(a) and bool(b) and not differing
        result = ReconciliationResult(
            query_name=name,
            source_env=label_a,
            target_env=label_b,
            source_row_count=a.get("source_row_count", 0),
            target_row_count=b.get("target_row_count", 0),
            matched_count=0,
            missing_in_target_count=0,
            missing_in_source_count=0,
            value_mismatch_count=0 if ok else max(1, len(differing)),
            mismatches=[],
            status=TS.PASSED if ok else TS.FAILED,
            executed_at=datetime.now(timezone.utc),
            duration_seconds=0.0,
            mismatch_summary={
                "by_column": {metric: 1 for metric in differing},
                "compared_rows_by_column": {metric: 1 for metric in compared_metrics},
                "by_type": {
                    "value_diff": len(differing),
                    "missing_in_target": 0,
                    "missing_in_source": 0,
                },
            },
        )
        records = [] if ok else [
            MismatchRecord(
                key_values={"test_name": name},
                column_name=metric,
                source_value=str(a.get(metric)) if a.get(metric) is not None else "",
                target_value=str(b.get(metric)) if b.get(metric) is not None else "",
                mismatch_type="stat_diff",
            )
            for metric in differing
        ]
        pairs.append((result, records))
    return pairs
```

- [ ] **Step 4: Use it in the endpoint path**

In `run_recon_file_compare`, replace everything from `all_names = sorted(...)` (line 517) through the `MetricsWriter(...)` line (line 588) with:

```python
            pairs = _compare_report_stats(stats_a, stats_b, req.label_a, req.label_b)
            passed = failed = 0
            results = []
            for result, records in pairs:
                if result.status == TestStatus.PASSED:
                    passed += 1
                else:
                    failed += 1
                tr = self._repo.add_test_result(run_id, result)
                if records:
                    self._repo.add_mismatch_details(tr.id, records)
                results.append(result)

            self._repo.update_run_status(
                run_id, "PASSED" if failed == 0 else "FAILED",
                completed_at=datetime.now(timezone.utc),
                total_tests=len(pairs), passed=passed, failed=failed,
            )
            MetricsWriter(f"logs/metrics_{run_id}.json").write(run_id, results)
```

The now-unused local imports of `ReconciliationResult`, `MismatchRecord`, and `TestStatus as TS` inside the deleted loop go away with it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_cores.py -k "report_stats" -v`
Expected: PASS

- [ ] **Step 6: Verify the endpoint still behaves identically**

Run: `python -m pytest tests/unit/test_compare_api.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add api/services/compare_service.py tests/unit/test_compare_cores.py
git commit -m "refactor: extract _compare_report_stats from run_recon_file_compare"
```

---

### Task 4: `aggregate_stat_results`

**Files:**
- Modify: `api/services/compare_service.py` (add after `_compare_report_stats`)
- Test: `tests/unit/test_compare_cores.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_compare_cores.py`:

```python
def test_aggregate_stat_results_folds_per_test_results_into_one():
    from api.services.compare_service import _compare_report_stats, aggregate_stat_results

    stats_a = {
        "orders": {"status": "PASSED", "source_row_count": 10, "target_row_count": 10, "total_issues": 0},
        "customers": {"status": "PASSED", "source_row_count": 5, "target_row_count": 5, "total_issues": 0},
    }
    stats_b = {
        "orders": {"status": "PASSED", "source_row_count": 10, "target_row_count": 10, "total_issues": 0},
        "customers": {"status": "FAILED", "source_row_count": 5, "target_row_count": 4, "total_issues": 1},
    }
    results = [result for result, _ in _compare_report_stats(stats_a, stats_b, "A", "B")]

    aggregate = aggregate_stat_results("nightly_report_diff", results, "A", "B")

    assert aggregate.query_name == "nightly_report_diff"
    assert aggregate.status.value == "FAILED"
    assert aggregate.matched_count == 1
    tests = aggregate.mismatch_summary["report_tests"]
    assert [t["test_name"] for t in tests] == ["customers", "orders"]
    assert tests[0]["differing_metrics"] == ["status", "target_row_count", "total_issues"]


def test_aggregate_stat_results_passes_when_every_test_matched():
    from api.services.compare_service import _compare_report_stats, aggregate_stat_results

    stats = {"orders": {"status": "PASSED", "source_row_count": 1, "target_row_count": 1, "total_issues": 0}}
    results = [result for result, _ in _compare_report_stats(stats, stats, "A", "B")]

    aggregate = aggregate_stat_results("job", results, "A", "B")

    assert aggregate.status.value == "PASSED"
    assert aggregate.value_mismatch_count == 0


def test_aggregate_stat_results_fails_when_neither_side_had_any_tests():
    from api.services.compare_service import aggregate_stat_results

    aggregate = aggregate_stat_results("job", [], "A", "B")

    assert aggregate.status.value == "FAILED"
    assert aggregate.mismatch_summary["report_tests"] == []
```

An empty comparison fails deliberately: two sources that parsed to no tests at all is a broken job, not a clean pass, and a scheduled job that silently passes on unparseable input is worse than one that goes red.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_cores.py -k "aggregate_stat_results" -v`
Expected: FAIL with `ImportError: cannot import name 'aggregate_stat_results'`

- [ ] **Step 3: Add the function**

In `api/services/compare_service.py`, add immediately after `_compare_report_stats`:

```python
def aggregate_stat_results(
    job_name: str,
    results: list["ReconciliationResult"],
    label_a: str,
    label_b: str,
) -> "ReconciliationResult":
    """Fold per-test report comparisons into the one result a job case returns.

    RunExecutor gives each job case exactly one ReconciliationResult, but a
    report-shaped compare produces one per test name. Per-test detail is kept
    under mismatch_summary["report_tests"] so Reports can still show which
    test differed. No results at all is a failure, not a pass: two sources
    that parsed to nothing is a broken job, and a scheduled job should go red
    rather than silently green.
    """
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus as TS

    failed = [r for r in results if r.status != TS.PASSED]
    return ReconciliationResult(
        query_name=job_name,
        source_env=label_a,
        target_env=label_b,
        source_row_count=sum(r.source_row_count for r in results),
        target_row_count=sum(r.target_row_count for r in results),
        matched_count=len(results) - len(failed),
        missing_in_target_count=0,
        missing_in_source_count=0,
        value_mismatch_count=sum(r.value_mismatch_count for r in results) or (0 if results else 1),
        mismatches=[],
        status=TS.PASSED if (results and not failed) else TS.FAILED,
        executed_at=datetime.now(timezone.utc),
        duration_seconds=0.0,
        mismatch_summary={
            "report_tests": [
                {
                    "test_name": r.query_name,
                    "status": r.status.value,
                    "source_row_count": r.source_row_count,
                    "target_row_count": r.target_row_count,
                    "differing_metrics": sorted((r.mismatch_summary or {}).get("by_column", {})),
                }
                for r in results
            ],
        },
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_cores.py -k "aggregate_stat_results" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/compare_service.py tests/unit/test_compare_cores.py
git commit -m "feat: add aggregate_stat_results for report-shaped compare jobs"
```

---

### Task 5: `_tabular_file_result` pure helper

**Files:**
- Modify: `api/services/compare_service.py:392-435`
- Test: `tests/unit/test_compare_cores.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_compare_cores.py`:

```python
def test_tabular_file_result_returns_a_result_and_writes_no_run():
    import pandas as pd
    from api.schemas import ReconFileCompareRequest
    from api.services.compare_service import CompareService

    db = _session()
    svc = CompareService(db, ConfigRepository(db))
    req = ReconFileCompareRequest(
        file_a_path="/allowed/a.csv",
        file_b_path="/allowed/b.csv",
        key_columns=["id"],
    )
    df_a = pd.DataFrame({"id": [1, 2], "value": ["alpha", "beta"]})
    df_b = pd.DataFrame({"id": [1, 2], "value": ["alpha", "GAMMA"]})

    result = svc._tabular_file_result(req, df_a, df_b)

    assert result.value_mismatch_count == 1
    assert RunRepository(db).list_runs() == []
```

`ReconFileCompareRequest`'s validator only requires exactly one source per side; the paths are never read here because the frames are passed in directly.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/test_compare_cores.py -k "tabular_file_result" -v`
Expected: FAIL with `AttributeError: 'CompareService' object has no attribute '_tabular_file_result'`

- [ ] **Step 3: Split the method**

In `api/services/compare_service.py`, replace `_run_tabular_file_compare` (lines 392-435) with:

```python
    def _tabular_file_result(
        self, req: ReconFileCompareRequest, df_a: "pd.DataFrame", df_b: "pd.DataFrame",
    ) -> "ReconciliationResult":
        """Reconcile two frames from a recon-file compare and return the result."""
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
        engine_a = FrameEngine(df_a, req.label_a)
        engine_b = FrameEngine(df_b, req.label_b)
        reconciler = _build_engine(
            engine_a, engine_b,
            key_columns=key_columns,
            exclude_columns=req.exclude_columns or [],
            mismatch_row_limit=_compare_mismatch_row_limit(getattr(req, "advanced", None)),
            adv=getattr(req, "advanced", None),
        )
        return reconciler.reconcile(_SENTINEL_QUERY, req.label_a or "file_a")

    def _run_tabular_file_compare(
        self, req: ReconFileCompareRequest, run_id: str,
        df_a: "pd.DataFrame", df_b: "pd.DataFrame",
    ) -> None:
        """Compare two DataFrames via ReconciliationEngine and store results."""
        self._persist_single_result(run_id, self._tabular_file_result(req, df_a, df_b))
```

The `import pandas as pd` that opened the old method is dropped — `pd` is already imported at module level (line 7).

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/test_compare_cores.py -k "tabular_file_result" -v`
Expected: PASS

- [ ] **Step 5: Verify the endpoint still behaves identically**

Run: `python -m pytest tests/unit/test_compare_api.py tests/unit/test_tabular_file_compare.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/services/compare_service.py tests/unit/test_compare_cores.py
git commit -m "refactor: split a pure _tabular_file_result out of _run_tabular_file_compare"
```

---

### Task 6: `compare_recon_file` pure core

**Files:**
- Modify: `api/services/compare_service.py:492-516` (the kind-mismatch check and the head of `run_recon_file_compare`)
- Test: `tests/unit/test_compare_cores.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_compare_cores.py`:

```python
def test_compare_recon_file_returns_one_result_for_tabular_sources(tmp_path, monkeypatch):
    from api.services import file_source
    from api.schemas import ReconFileCompareRequest
    from api.services.compare_service import CompareService

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    (tmp_path / "a.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,value\n1,beta\n", encoding="utf-8")

    db = _session()
    svc = CompareService(db, ConfigRepository(db))
    req = ReconFileCompareRequest(
        file_a_path=str(tmp_path / "a.csv"),
        file_b_path=str(tmp_path / "b.csv"),
        key_columns=["id"],
    )

    result = svc.compare_recon_file(req, job_name="nightly_file_diff")

    assert result.value_mismatch_count == 1
    assert RunRepository(db).list_runs() == []


def test_compare_recon_file_aggregates_report_sources_into_one_result(monkeypatch):
    from api.schemas import ReconFileCompareRequest
    from api.services.compare_service import CompareService

    db = _session()
    svc = CompareService(db, ConfigRepository(db))
    stats_a = {
        "orders": {"status": "PASSED", "source_row_count": 10, "target_row_count": 10, "total_issues": 0},
    }
    stats_b = {
        "orders": {"status": "FAILED", "source_row_count": 10, "target_row_count": 9, "total_issues": 1},
    }
    monkeypatch.setattr(
        CompareService, "_load_recon_source",
        lambda self, req, side: stats_a if side == "a" else stats_b,
    )
    req = ReconFileCompareRequest(file_a_path="/x/a.html", file_b_path="/x/b.html")

    result = svc.compare_recon_file(req, job_name="nightly_report_diff")

    assert result.query_name == "nightly_report_diff"
    assert result.status.value == "FAILED"
    assert [t["test_name"] for t in result.mismatch_summary["report_tests"]] == ["orders"]


def test_compare_recon_file_rejects_mixed_source_kinds(monkeypatch):
    import pandas as pd
    import pytest
    from fastapi import HTTPException
    from api.schemas import ReconFileCompareRequest
    from api.services.compare_service import CompareService

    db = _session()
    svc = CompareService(db, ConfigRepository(db))
    monkeypatch.setattr(
        CompareService, "_load_recon_source",
        lambda self, req, side: pd.DataFrame({"id": [1]}) if side == "a" else {"orders": {}},
    )
    req = ReconFileCompareRequest(file_a_path="/x/a.csv", file_b_path="/x/b.html")

    with pytest.raises(HTTPException) as exc_info:
        svc.compare_recon_file(req, job_name="job")

    assert exc_info.value.status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_cores.py -k "compare_recon_file" -v`
Expected: FAIL with `AttributeError: 'CompareService' object has no attribute 'compare_recon_file'`

- [ ] **Step 3: Extract the kind check and add the core**

In `api/services/compare_service.py`, add these two methods immediately before `run_recon_file_compare` (line 492):

```python
    def _require_matching_recon_kinds(self, stats_a, stats_b) -> bool:
        """Reject a tabular-vs-report compare; return True when both are frames."""
        is_df_a = isinstance(stats_a, pd.DataFrame)
        is_df_b = isinstance(stats_b, pd.DataFrame)
        if is_df_a != is_df_b:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Source A resolved to {_recon_kind_label(is_df_a)} and "
                    f"Source B resolved to {_recon_kind_label(is_df_b)}. "
                    "Both sources must be the same type: two tabular files "
                    "(.csv/.xlsx/.xls/.json/.xml/.tsv/.txt), or two report-shaped "
                    "sources (HTML report or stored run)."
                ),
            )
        return is_df_a

    def compare_recon_file(
        self, req: ReconFileCompareRequest, job_name: str | None = None,
    ) -> "ReconciliationResult":
        """Compare two recon-file sources and return exactly ONE result.

        Tabular sources reconcile directly. Report-shaped sources (HTML, or a
        stored run's per-test stats) compare test by test and are folded by
        aggregate_stat_results(), because RunExecutor gives each job case one
        result. The endpoint keeps its own per-test persistence in
        run_recon_file_compare().
        """
        stats_a = self._load_recon_source(req, "a")
        stats_b = self._load_recon_source(req, "b")
        if self._require_matching_recon_kinds(stats_a, stats_b):
            return self._tabular_file_result(req, stats_a, stats_b)
        pairs = _compare_report_stats(stats_a, stats_b, req.label_a, req.label_b)
        return aggregate_stat_results(
            job_name or req.label_a or "recon_file",
            [result for result, _ in pairs],
            req.label_a,
            req.label_b,
        )
```

Then in `run_recon_file_compare`, replace its own inline kind check (lines 499-515, from `import pandas as pd` through the `if _is_df_a:` block) with:

```python
            if self._require_matching_recon_kinds(stats_a, stats_b):
                self._run_tabular_file_compare(req, run_id, stats_a, stats_b)
                return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_cores.py -v`
Expected: all PASS

- [ ] **Step 5: Verify the endpoint still behaves identically**

Run: `python -m pytest tests/unit/test_compare_api.py tests/unit/test_tabular_file_compare.py tests/unit/test_report_template.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/services/compare_service.py tests/unit/test_compare_cores.py
git commit -m "refactor: add a compare_recon_file core returning one result"
```

---

## Phase 3 — The `compare` job type

### Task 7: `compare` job type and its params contract

**Files:**
- Modify: `api/schemas.py:472-477` (the `job_type` Literal), `api/schemas.py:489-580` (the validator)
- Test: `tests/unit/test_compare_job_type.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_compare_job_type.py`:

```python
"""Validation for the `compare` job type."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import JobDefinition


def _bo_request(**overrides) -> dict:
    request = {
        "source_a": {"source_type": "path", "file_path": "/data/a.csv"},
        "source_b": {"source_type": "path", "file_path": "/data/b.csv"},
        "key_columns": ["id"],
    }
    return {**request, **overrides}


def test_compare_job_accepts_a_bo_request_with_repeatable_sources():
    job = JobDefinition(
        name="nightly_compare",
        job_type="compare",
        params={"compare_type": "bo", "request": _bo_request()},
    )

    assert job.params["compare_type"] == "bo"


def test_compare_job_requires_a_known_compare_type():
    with pytest.raises(ValidationError, match="compare_type"):
        JobDefinition(
            name="nightly_compare",
            job_type="compare",
            params={"compare_type": "sql", "request": _bo_request()},
        )


def test_compare_job_requires_a_request_body():
    with pytest.raises(ValidationError, match="params.request"):
        JobDefinition(
            name="nightly_compare",
            job_type="compare",
            params={"compare_type": "bo"},
        )


def test_compare_job_rejects_an_upload_source():
    with pytest.raises(ValidationError, match="Source B"):
        JobDefinition(
            name="nightly_compare",
            job_type="compare",
            params={"compare_type": "bo", "request": _bo_request(
                source_b={"source_type": "upload", "file_content_b64": "aWQK", "file_name": "b.csv"},
            )},
        )


def test_compare_job_rejects_a_past_run_source():
    with pytest.raises(ValidationError, match="Source A"):
        JobDefinition(
            name="nightly_compare",
            job_type="compare",
            params={"compare_type": "bo", "request": _bo_request(
                source_a={"source_type": "run", "run_id": "run-1", "job_name": "prior"},
            )},
        )


def test_compare_job_rejects_a_recon_file_stored_run_source():
    with pytest.raises(ValidationError, match="Source A"):
        JobDefinition(
            name="nightly_file_diff",
            job_type="compare",
            params={"compare_type": "recon_file", "request": {
                "stored_run_id": "run-1",
                "file_b_path": "/data/b.csv",
            }},
        )


def test_compare_job_accepts_two_recon_file_paths():
    job = JobDefinition(
        name="nightly_file_diff",
        job_type="compare",
        params={"compare_type": "recon_file", "request": {
            "file_a_path": "/data/a.csv",
            "file_b_path": "/data/b.csv",
        }},
    )

    assert job.params["compare_type"] == "recon_file"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_job_type.py -v`
Expected: FAIL — `job_type` does not accept `"compare"` (Pydantic literal mismatch)

- [ ] **Step 3: Add `compare` to the job type Literal**

In `api/schemas.py`, replace the `job_type` field (lines 472-477):

```python
    job_type: Literal[
        "reconciliation", "health_check", "bo_report", "automic_job", "dbt_artifact",
        "freshness", "cross_job_assertion", "schema_snapshot", "profile", "api_reconciliation",
        "bo_job", "ds_job", "s3_row_count", "s3_format_validation", "s3_partition_check",
        "aws_glue_catalog_compare", "aws_athena_query", "compare",
    ] = "reconciliation"
```

- [ ] **Step 4: Add the validator branch**

In `api/schemas.py`, inside `validate_reconciliation_contract`, add a branch immediately before `elif self.job_type == "freshness":` (line 567):

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
            # Uploads and past-run references cannot be re-run on a schedule:
            # upload bytes live only in the original request, and run artifacts
            # are removed by the UPLOAD_ROOT retention sweep. Rejecting them here
            # beats a schedule that fails months later.
            if compare_type == "bo":
                parsed_bo = BOCompareRequest.model_validate(request)
                for side, src in (("A", parsed_bo.source_a), ("B", parsed_bo.source_b)):
                    if src.source_type in ("upload", "run"):
                        raise ValueError(
                            f"compare job Source {side} uses a "
                            f"{'past run' if src.source_type == 'run' else 'file upload'}, "
                            "which cannot be re-run on a schedule — use a live, path, or api source"
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
                            "which cannot be re-run on a schedule — use a file path"
                        )
```

`BOCompareRequest` and `ReconFileCompareRequest` are defined further down this same module; the names resolve at call time, so no import or forward reference is needed.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_job_type.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py tests/unit/test_compare_job_type.py
git commit -m "feat: add the compare job type and its params contract"
```

---

### Task 8: Mirror the compare's column config onto the job

**Files:**
- Modify: `api/schemas.py` (end of `validate_reconciliation_contract`)
- Test: `tests/unit/test_compare_job_type.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_compare_job_type.py`:

```python
def test_compare_job_mirrors_key_and_exclude_columns_from_the_request():
    job = JobDefinition(
        name="nightly_compare",
        job_type="compare",
        params={"compare_type": "bo", "request": _bo_request(
            key_columns=["region", "product"],
            exclude_columns=["loaded_at"],
        )},
    )

    assert job.key_columns == ["region", "product"]
    assert job.exclude_columns == ["loaded_at"]


def test_compare_job_mirroring_clears_stale_top_level_columns():
    job = JobDefinition(
        name="nightly_compare",
        job_type="compare",
        key_columns=["stale"],
        params={"compare_type": "bo", "request": _bo_request(key_columns=["id"])},
    )

    assert job.key_columns == ["id"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_job_type.py -k "mirror" -v`
Expected: FAIL with `assert [] == ['region', 'product']`

- [ ] **Step 3: Mirror the columns**

In `api/schemas.py`, in `validate_reconciliation_contract`, replace the final `return self` (line 580) with:

```python
        if self.job_type == "compare":
            # params.request stays the single source of truth — RunExecutor reads
            # it and never these. Mirroring keeps the Job Catalog, job list, and
            # coverage views rendering a compare job like every other job.
            request = self.params.get("request") or {}
            self.key_columns = list(request.get("key_columns") or [])
            self.exclude_columns = list(request.get("exclude_columns") or [])
        return self
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_job_type.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_compare_job_type.py
git commit -m "feat: mirror a compare job's key and exclude columns onto the job"
```

---

### Task 9: `job_validation` issues for compare jobs

`POST /api/jobs/validate` and the pre-save check in `api/routes/jobs.py` run `validate_job_definition`, which returns structured issues instead of raising. It needs the same contract checks plus warnings for the fields a compare job ignores.

**Files:**
- Modify: `etl_framework/runner/job_validation.py:151-233`
- Test: `tests/unit/test_job_validation.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_job_validation.py`:

```python
def test_compare_job_without_compare_type_reports_an_error():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_compare",
        "job_type": "compare",
        "params": {"request": {}},
    })

    assert any(
        i.field == "params.compare_type" and i.severity == ValidationSeverity.ERROR
        for i in issues
    )


def test_compare_job_without_a_request_reports_an_error():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_compare",
        "job_type": "compare",
        "params": {"compare_type": "bo"},
    })

    assert any(
        i.field == "params.request" and i.severity == ValidationSeverity.ERROR
        for i in issues
    )


def test_compare_job_warns_that_rules_are_ignored():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_compare",
        "job_type": "compare",
        "params": {
            "compare_type": "bo",
            "request": {"source_a": {}, "source_b": {}},
            "rules": [{"rule_type": "not_null", "column": "id"}],
        },
    })

    warnings = [i for i in issues if i.severity == ValidationSeverity.WARNING]
    assert any("rules" in i.field for i in warnings)


def test_a_valid_compare_job_reports_no_errors():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_compare",
        "job_type": "compare",
        "params": {
            "compare_type": "bo",
            "request": {
                "source_a": {"source_type": "path", "file_path": "/data/a.csv"},
                "source_b": {"source_type": "path", "file_path": "/data/b.csv"},
            },
        },
    })

    assert [i for i in issues if i.severity == ValidationSeverity.ERROR] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_job_validation.py -k "compare_job" -v`
Expected: FAIL — no branch produces these issues, so the `any(...)` assertions are False

- [ ] **Step 3: Add the branch**

In `etl_framework/runner/job_validation.py`, inside `validate_job_definition`, add a branch immediately before `elif job_type == "dbt_artifact":` (line 230):

```python
    elif job_type == "compare":
        if params.get("compare_type") not in ("bo", "recon_file"):
            issues.append(ValidationIssue(
                "params.compare_type",
                "compare jobs require compare_type of 'bo' or 'recon_file'",
            ))
        if not isinstance(params.get("request"), dict):
            issues.append(ValidationIssue(
                "params.request",
                "compare jobs require the compare request body in params.request",
            ))
        # A compare job runs through CompareService, not _run_reconciliation_job,
        # so these three never execute. Warn rather than error: the job itself is
        # still valid and runnable.
        for field in ("rules", "pass_condition", "depends_on"):
            if params.get(field) or _get(job, field, None):
                issues.append(ValidationIssue(
                    f"params.{field}",
                    f"{field} is ignored for compare jobs — compare runs do not go "
                    "through the reconciliation job path",
                    ValidationSeverity.WARNING,
                ))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_job_validation.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/runner/job_validation.py tests/unit/test_job_validation.py
git commit -m "feat: validate compare jobs in job_validation"
```

---

## Phase 4 — Executor dispatch

### Task 10: `_build_case_compare`

**Files:**
- Modify: `api/services/run_executor.py:463-521` (dispatch), plus a new method near `_build_case_dbt`
- Test: `tests/unit/test_run_executor_compare.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_run_executor_compare.py`:

```python
"""RunExecutor dispatch for the `compare` job type."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _executor(db) -> RunExecutor:
    return RunExecutor(
        db=db,
        run_id="run-1",
        source_env="Source A",
        target_env="Source B",
        job_sequence=[],
        run_settings=RunSettings(),
        config_snapshot={},
    )


def _allow(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))


def test_compare_job_runs_a_bo_compare_and_names_the_result_after_the_job(tmp_path, monkeypatch):
    _allow(tmp_path, monkeypatch)
    (tmp_path / "a.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,value\n1,beta\n", encoding="utf-8")

    job = JobDefinition(
        name="nightly_compare",
        job_type="compare",
        params={"compare_type": "bo", "request": {
            "source_a": {"source_type": "path", "file_path": str(tmp_path / "a.csv")},
            "source_b": {"source_type": "path", "file_path": str(tmp_path / "b.csv")},
            "key_columns": ["id"],
        }},
    )

    result = _executor(_session())._build_case(job)()

    assert result.query_name == "nightly_compare"
    assert result.value_mismatch_count == 1


def test_compare_job_runs_a_recon_file_compare(tmp_path, monkeypatch):
    _allow(tmp_path, monkeypatch)
    (tmp_path / "a.csv").write_text("id,value\n1,alpha\n2,beta\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,value\n1,alpha\n2,beta\n", encoding="utf-8")

    job = JobDefinition(
        name="nightly_file_diff",
        job_type="compare",
        params={"compare_type": "recon_file", "request": {
            "file_a_path": str(tmp_path / "a.csv"),
            "file_b_path": str(tmp_path / "b.csv"),
            "key_columns": ["id"],
        }},
    )

    result = _executor(_session())._build_case(job)()

    assert result.query_name == "nightly_file_diff"
    assert result.status.value == "PASSED"


def test_compare_job_with_an_unknown_compare_type_raises():
    job = JobDefinition.model_construct(
        name="broken",
        job_type="compare",
        description="",
        tags=[],
        query="",
        key_columns=[],
        exclude_columns=[],
        source_env=None,
        target_env=None,
        params={"compare_type": "sql", "request": {}},
        enabled=True,
        rules=[],
        depends_on=[],
        pass_condition=None,
    )

    with pytest.raises(ValueError, match="unknown compare_type"):
        _executor(_session())._build_case(job)()
```

The last test uses `model_construct` deliberately: a bad `compare_type` cannot survive `JobDefinition`'s validator (Task 7), so the only way to reach the executor's own guard is to skip validation, which is exactly the state a hand-edited DB row could be in.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_run_executor_compare.py -v`
Expected: FAIL — `_build_case` has no `compare` branch, so it falls through to the default reconciliation path and errors on the empty query

- [ ] **Step 3: Add the case builder**

In `api/services/run_executor.py`, add this method immediately before `_build_case_dbt` (line 1895):

```python
    def _build_case_compare(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            from api.schemas import BOCompareRequest, ReconFileCompareRequest
            from api.services.compare_service import CompareService
            from etl_framework.repository.repository import ConfigRepository

            params = job.params or {}
            compare_type = params.get("compare_type")
            request = params.get("request") or {}
            service = CompareService(self._db, ConfigRepository(self._db))
            if compare_type == "bo":
                result = service.compare_bo(
                    BOCompareRequest.model_validate(request), self._run_id,
                )
            elif compare_type == "recon_file":
                result = service.compare_recon_file(
                    ReconFileCompareRequest.model_validate(request), job_name=job.name,
                )
            else:
                raise ValueError(
                    f"unknown compare_type '{compare_type}' for compare job '{job.name}'"
                )
            # Reports, cross-job assertions, and job-scoped result lookup all key
            # on query_name == job name; the compare cores name results after
            # label_a instead.
            return dataclasses.replace(result, query_name=job.name)
        return run_job
```

- [ ] **Step 4: Add the dispatch line**

In `api/services/run_executor.py`, in `_build_case`, add immediately after the `dbt_artifact` branch (line 483-484):

```python
        if job.job_type == "compare":
            return self._build_case_compare(job)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_run_executor_compare.py -v`
Expected: all PASS

- [ ] **Step 6: Run the executor test suite to check nothing else broke**

Run: `python -m pytest tests/unit/test_run_executor.py tests/unit/test_multi_file_jobs.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_executor_compare.py
git commit -m "feat: run compare jobs through RunExecutor"
```

---

## Phase 5 — Frontend

### Task 11: Extract the compare payload builders

Save-as-Job must send exactly the body the Run button sends. Extracting the builders means one source of truth rather than two drifting copies.

**Files:**
- Modify: `frontend/features/compare.js:456-485` (`runBOComparison`), `:602-655` (`runFileCompare`), `:695-736` (`runMultiFileCompare`)

- [ ] **Step 1: Add the three builders**

In `frontend/features/compare.js`, add these methods immediately before `runBOComparison()` (line 456):

```js
    // Payload builders are shared by the Run buttons and Save as Job, so a
    // saved job always sends exactly what an ad-hoc run would.
    _buildBOComparePayload() {
      return {
        source_a: this._buildBOSource(this.boSourceAType, this.boSourceA),
        source_b: this._buildBOSource(this.boSourceBType, this.boSourceB),
        key_columns: this.boKeyColumns.split(',').map(s => s.trim()).filter(Boolean),
        exclude_columns: this.boExcludeColumns.split(',').map(s => s.trim()).filter(Boolean),
        label_a: this.boSourceA.label || 'Source A',
        label_b: this.boSourceB.label || 'Source B',
        advanced: this._buildAdvanced('bo'),
      };
    },

    _buildReconFilePayload() {
      const payload = {
        label_a: this.fileLabelA || 'Source A',
        label_b: this.fileLabelB || 'Production Report',
      };
      if (this.fileCompareKeyColumns.trim()) {
        payload.key_columns = this.fileCompareKeyColumns.split(',').map(s => s.trim()).filter(Boolean);
      }
      if (this.fileCompareExcludeColumns.trim()) {
        payload.exclude_columns = this.fileCompareExcludeColumns.split(',').map(s => s.trim()).filter(Boolean);
      }
      const applySource = (side, type, runId, path, content, fname) => {
        const label = side === 'a' ? 'Source A' : 'Source B';
        const suffix = side === 'a' ? '' : '_b';
        if (type === 'run') {
          if (!runId) throw new Error(`${label}: select a stored run`);
          payload[`stored_run_id${suffix}`] = runId;
        } else if (type === 'path') {
          if (!(path || '').trim()) throw new Error(`${label}: enter a file path`);
          payload[`file_${side}_path`] = path.trim();
        } else {
          if (!content) throw new Error(`${label}: upload a file`);
          payload[`file_${side}_content_b64`] = content;
          if (fname) payload[`file_${side}_name`] = fname;
        }
      };
      applySource('a', this.fileSourceAType, this.fileRunIdA, this.filePathA, this.fileB64A, this.fileNameA);
      applySource('b', this.fileSourceBType, this.fileRunIdB, this.filePathB, this.fileB64B, this.fileNameB);
      if (this.reconSourceKindMismatch()) throw new Error(this.reconSourceKindWarning());
      payload.advanced = this._buildAdvanced('file');
      return payload;
    },

    _buildMultiFilePayload() {
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
      if (this.mfCompareKeyColumns.trim()) {
        payload.key_columns = this.mfCompareKeyColumns.split(',').map(s => s.trim()).filter(Boolean);
      }
      if (this.mfCompareExcludeColumns.trim()) {
        payload.exclude_columns = this.mfCompareExcludeColumns.split(',').map(s => s.trim()).filter(Boolean);
      }
      return payload;
    },
```

- [ ] **Step 2: Use them in the three run methods**

In `runBOComparison()`, replace the `const payload = { ... };` literal (lines 467-475) with:

```js
        const payload = this._buildBOComparePayload();
```

In `runFileCompare()`, replace everything from `const payload = {` through `payload.advanced = this._buildAdvanced('file');` (lines 607-635) with:

```js
        const payload = this._buildReconFilePayload();
```

In `runMultiFileCompare()`, replace everything from `const payload = {` through the second `if (this.mfCompareExcludeColumns.trim()) { ... }` block (lines 700-715) with:

```js
        const payload = this._buildMultiFilePayload();
```

- [ ] **Step 3: Verify the compare e2e suite still passes**

Run: `rtk proxy npx playwright test tests/e2e/08a-compare-bo-report.spec.ts tests/e2e/08b-compare-reconciliation.spec.ts tests/e2e/08g-compare-multi-file.spec.ts`
Expected: all pass. (Use `rtk proxy` — plain `rtk` forces a JSON reporter and truncates the output.)

- [ ] **Step 4: Commit**

```bash
git add frontend/features/compare.js
git commit -m "refactor: extract compare payload builders for reuse"
```

---

### Task 12: Save as Job

**Files:**
- Modify: `frontend/features/compare.js` (state near line 24, methods after the builders)
- Modify: `frontend/partials/tab-compare.html:93-95` and `:159-161` (BO path input testids), `:253`, `:588`, `:1461` (buttons), end of file (dialog)

- [ ] **Step 1: Add the Alpine state**

In `frontend/features/compare.js`, add to the state object immediately after `compareSubTab: 'bo',` (line 24):

```js
    saveJobModalOpen: false,
    saveJobCompareType: '',
    saveJobName: '',
    saveJobDescription: '',
    saveJobTags: '',
    saveJobError: '',
    saveJobSaving: false,
```

- [ ] **Step 2: Add the save methods**

In `frontend/features/compare.js`, add immediately after `_buildMultiFilePayload()`:

```js
    openSaveCompareAsJob(compareType) {
      this.saveJobCompareType = compareType;
      this.saveJobName = '';
      this.saveJobDescription = '';
      this.saveJobTags = '';
      this.saveJobError = '';
      this.saveJobModalOpen = true;
    },

    // Mirror of the server-side validator (api/schemas.py's compare branch), so
    // a non-repeatable source is caught before the round trip. The server stays
    // authoritative.
    _assertCompareJobSourcesAreRepeatable(compareType, payload) {
      if (compareType === 'bo') {
        [['A', payload.source_a], ['B', payload.source_b]].forEach(([side, src]) => {
          if (!src) return;
          if (src.source_type === 'upload' || src.source_type === 'run') {
            const what = src.source_type === 'upload' ? 'an upload' : 'a past run';
            throw new Error(`Source ${side} is ${what} — a job that re-runs needs a live, path, or API source.`);
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
          throw new Error(`Source ${side} is ${what} — a job that re-runs needs a file path.`);
        }
      });
    },

    _compareJobBody() {
      // Multi-file saves as the reconciliation/multi_file job that already runs
      // and already schedules — not as a `compare` job.
      if (this.saveJobCompareType === 'multi_file') {
        const payload = this._buildMultiFilePayload();
        if (payload.run_id) {
          throw new Error('A run-reference multi-file compare cannot be saved as a job — pick source and target roots instead.');
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

    async saveCompareAsJob() {
      const name = (this.saveJobName || '').trim();
      if (!name) { this.saveJobError = 'Enter a job name'; return; }
      this.saveJobSaving = true;
      this.saveJobError = '';
      try {
        const body = {
          ...this._compareJobBody(),
          name,
          description: this.saveJobDescription || '',
          tags: (this.saveJobTags || '').split(',').map(s => s.trim()).filter(Boolean),
        };
        await api('POST', '/api/jobs', body);
        this.saveJobModalOpen = false;
        this.toast('success', 'Saved as job',
          `"${name}" is in the Job Catalog — add it to a selection to schedule it.`);
        if (this.loadJobs) await this.loadJobs();
      } catch (e) {
        this.saveJobError = e.message || 'Could not save this compare as a job';
      } finally {
        this.saveJobSaving = false;
      }
    },
```

- [ ] **Step 3: Add testids to the BO path inputs**

The e2e test drives BO path sources, which have no testids today. In `frontend/partials/tab-compare.html`, replace line 94:

```html
          <input data-testid="compare-bo-source-a-path-input" x-model="boSourceA.filePath" class="field-input" placeholder="C:\reports\a.csv" aria-label="c reports a csv" />
```

and the matching Source B path input (the `x-model="boSourceB.filePath"` line, inside the `boSourceBType === 'path'` template around line 160):

```html
          <input data-testid="compare-bo-source-b-path-input" x-model="boSourceB.filePath" class="field-input" placeholder="C:\reports\b.csv" aria-label="c reports b csv" />
```

- [ ] **Step 4: Add the three buttons**

In `frontend/partials/tab-compare.html`, add a Save as Job button immediately after each run button.

After the BO run button (line 253's `</button>`):

```html
        <button data-testid="compare-bo-save-job-btn" @click="openSaveCompareAsJob('bo')"
                class="btn-secondary btn-sm text-xs">Save as Job</button>
```

After the recon-file run button (line 588's `</button>`):

```html
        <button data-testid="compare-file-save-job-btn" @click="openSaveCompareAsJob('recon_file')"
                class="btn-secondary btn-sm text-xs">Save as Job</button>
```

After the multi-file run button (line 1461's `</button>`):

```html
        <button data-testid="compare-mf-save-job-btn" @click="openSaveCompareAsJob('multi_file')"
                class="btn-secondary btn-sm text-xs">Save as Job</button>
```

- [ ] **Step 5: Add the shared dialog**

In `frontend/partials/tab-compare.html`, add immediately before the file's final `</div></template>`:

```html
  <!-- Save compare as Job -->
  <div x-show="saveJobModalOpen" x-cloak class="modal-backdrop" @click.self="saveJobModalOpen = false"
       data-testid="compare-save-job-modal">
    <div class="modal-box w-full max-w-lg" role="dialog" aria-modal="true" aria-labelledby="saveCompareJobTitle">
      <h2 id="saveCompareJobTitle" class="text-lg font-bold mb-4">Save Compare as Job</h2>
      <div class="space-y-3">
        <div>
          <label class="field-label" for="compare-save-job-name">Job name</label>
          <input id="compare-save-job-name" data-testid="compare-save-job-name" x-model="saveJobName"
                 class="field-input" placeholder="nightly_sales_bo_vs_prod" />
        </div>
        <div>
          <label class="field-label" for="compare-save-job-description">Description</label>
          <input id="compare-save-job-description" data-testid="compare-save-job-description"
                 x-model="saveJobDescription" class="field-input" />
        </div>
        <div>
          <label class="field-label" for="compare-save-job-tags">Tags (comma separated)</label>
          <input id="compare-save-job-tags" data-testid="compare-save-job-tags"
                 x-model="saveJobTags" class="field-input" placeholder="nightly, sales" />
        </div>
        <div x-show="saveJobError" data-testid="compare-save-job-error"
             class="text-sm text-rose-600" x-text="saveJobError"></div>
      </div>
      <div class="flex justify-end gap-2 mt-4">
        <button @click="saveJobModalOpen = false" class="btn-outline btn-sm">Cancel</button>
        <button data-testid="compare-save-job-confirm" @click="saveCompareAsJob()"
                :disabled="saveJobSaving" class="btn-primary btn-sm">Save Job</button>
      </div>
    </div>
  </div>
```

- [ ] **Step 6: Commit**

```bash
git add frontend/features/compare.js frontend/partials/tab-compare.html
git commit -m "feat: save a compare from the Compare tab as a job"
```

---

### Task 13: `compare` in the job-type dropdown

**Files:**
- Modify: `frontend/partials/tab-launch.html:342-351`

- [ ] **Step 1: Add the option**

In `frontend/partials/tab-launch.html`, add immediately after the `reconciliation` option (line 342):

```html
                    <option value="compare">compare</option>
```

The Job modal does not render an A/B source form for this type — a compare job is created from the Compare tab. The option exists so an existing compare job's type displays correctly when the modal opens.

- [ ] **Step 2: Commit**

```bash
git add frontend/partials/tab-launch.html
git commit -m "feat: show the compare job type in the job editor dropdown"
```

---

### Task 14: End-to-end coverage

**Files:**
- Create: `tests/e2e/26-compare-save-as-job.spec.ts`

- [ ] **Step 1: Write the spec**

Create `tests/e2e/26-compare-save-as-job.spec.ts`:

```ts
// tests/e2e/26-compare-save-as-job.spec.ts
import { test, expect } from './fixtures';
import path from 'node:path';
import { authedContext, deleteJob } from './api-helpers';

// resolve_allowed_path() (api/services/file_source.py) resolves paths against
// its allowed base dirs, so build absolute fixture paths the same way
// 08g-compare-multi-file.spec.ts does.
const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');
const JOB_NAME = 'e2e_saved_bo_compare';

async function openBOCompare(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-compare"]').click();
  await page.locator('[data-testid="compare-subtab-bo"]').click();
}

test.describe('26 compare / save as job', () => {
  test.afterEach(async ({ playwright }) => {
    const ctx = await authedContext(playwright);
    await deleteJob(ctx, JOB_NAME);
    await ctx.dispose();
  });

  test('saves a path-vs-path BO compare as a job that appears in the Job Catalog', async ({ authedPage }) => {
    await openBOCompare(authedPage);

    await authedPage.locator('[data-testid="compare-bo-source-a-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-a-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'multi_source', 'sales_east.csv'));
    await authedPage.locator('[data-testid="compare-bo-source-b-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-b-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'multi_target', 'financials_east.csv'));

    await authedPage.locator('[data-testid="compare-bo-save-job-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeVisible();
    await authedPage.locator('[data-testid="compare-save-job-name"]').fill(JOB_NAME);
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();
    await expect(authedPage.locator('[data-testid="compare-save-job-modal"]')).toBeHidden();

    await authedPage.locator('[data-testid="nav-tab-launch"]').click();
    await expect(authedPage.locator(`[data-testid="job-row-${JOB_NAME}-name"]`)).toBeVisible();
  });

  test('refuses to save a compare whose source is an upload', async ({ authedPage }) => {
    await openBOCompare(authedPage);

    await authedPage.locator('[data-testid="compare-bo-source-a-mode-path"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-a-path-input"]')
      .fill(path.join(FIXTURE_DIR, 'multi_source', 'sales_east.csv'));
    await authedPage.locator('[data-testid="compare-bo-source-b-mode-upload"]').click();
    await authedPage.locator('[data-testid="compare-bo-source-b-upload-input"]')
      .setInputFiles(path.join(FIXTURE_DIR, 'multi_target', 'financials_east.csv'));

    await authedPage.locator('[data-testid="compare-bo-save-job-btn"]').click();
    await authedPage.locator('[data-testid="compare-save-job-name"]').fill(JOB_NAME);
    await authedPage.locator('[data-testid="compare-save-job-confirm"]').click();

    await expect(authedPage.locator('[data-testid="compare-save-job-error"]')).toContainText('Source B is an upload');
  });
});
```

Before running, confirm two things and adjust the selectors rather than the app: that `[data-testid="job-row-<name>-name"]` matches the Job Catalog row markup in `frontend/partials/tab-launch.html` (grep for `job-row-`), and that `tests/e2e/fixtures/data/multi_source/sales_east.csv` and `multi_target/financials_east.csv` exist — 08g uses them.

- [ ] **Step 2: Run the spec**

Run: `rtk proxy npx playwright test tests/e2e/26-compare-save-as-job.spec.ts`
Expected: both tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/26-compare-save-as-job.spec.ts
git commit -m "test: cover saving a compare as a job end to end"
```

---

### Task 15: Full-suite verification

- [ ] **Step 1: Run the backend suite**

Run: `python -m pytest tests/unit -q`
Expected: all PASS. Use raw `python -m pytest`, not `rtk` — its cached summary can report a stale result.

- [ ] **Step 2: Run the compare and launch e2e specs**

Run: `rtk proxy npx playwright test tests/e2e/08a-compare-bo-report.spec.ts tests/e2e/08b-compare-reconciliation.spec.ts tests/e2e/08g-compare-multi-file.spec.ts tests/e2e/02-launch-jobs.spec.ts tests/e2e/26-compare-save-as-job.spec.ts`
Expected: all pass.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: address fallout from compare-as-job integration"
```

(Skip this commit if the suites were green with no changes.)
