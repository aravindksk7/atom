# Multi-File Reconciliation — Phase 3: Execution Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make multi_file reconciliation jobs run their pairs in parallel, survive one bad pair without losing every other pair's result, and (for local sources that are still being actively written to) wait for an expected number of files to land before pairing/comparing instead of racing a partial spool.

**Architecture:** This is Phase 3 of the roadmap in `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md` §7 ("Execution hardening: per-pair parallelism, per-pair exception isolation, live-spool readiness/polling"), building on Phase 1 (explicit pairing) and Phase 2 (automated pairing + lineage manifest), both merged to master.

Two existing, already-tested pieces of infrastructure are reused rather than reinvented:
- `etl_framework/runner/test_runner.py`'s `TestRunner` already runs a list of `(name, callable)` cases on a thread pool AND already catches per-case exceptions, converting them into a `TestCaseState` with `status=TestStatus.ERROR` instead of propagating (see `TestRunner._run_single`). This is exactly "per-pair parallelism + per-pair exception isolation" in one existing class — the per-pair loop in `RunExecutor._build_case_multi_file_reconciliation` is replaced with `TestRunner(...).run(cases)`, not with new concurrency code.
- `RunSettings.max_workers` (`api/schemas.py:72`, default 4) already exists in the run-settings schema but is currently unused by `RunExecutor` (the one place `TestRunner` is constructed today, `api/services/run_executor.py:240`, hardcodes `max_workers=1` for job-sequence-level execution — that is unrelated to this phase and is NOT changed here). This phase gives `max_workers` a real purpose: it becomes the pair-level concurrency knob for multi_file jobs.

**A real correctness issue found during design, fixed as a prerequisite (Task 1):** `RunExecutor._resolve_segment_columns` (`api/services/run_executor.py:1306-1317`) reads the shared SQLAlchemy session `self._db` via `ColumnProfileRepository(self._db).get_latest(job.name)`, and `_run_reconciliation_job` (`api/services/run_executor.py:682-725`) calls it internally on every invocation (line 693). If the per-pair loop is parallelized naively, every pair's thread would call this concurrently against the same non-thread-safe `Session` object. Since every pair of one job shares the same `job.name` (they're all `job.model_copy(update={"key_columns": ...})` of the same parent job — see `api/services/run_executor.py:664`), `_resolve_segment_columns` already returns the *same* result for every pair regardless. Task 1 pre-resolves it once, sequentially, before the parallel fan-out, and threads it through — fixing the thread-safety hazard and removing N redundant DB round-trips in one small, backward-compatible change.

**Tech Stack:** Python 3, pytest, the existing `TestRunner`/`TestCaseState` stack, `time.sleep`-based polling (injectable for tests, same shape as the existing `_poll_for_release`/`_sleep_with_cancel_check` methods already in `RunExecutor`).

**Spec coverage in this phase** (against the Phase 3 roadmap line "per-pair parallelism (reuse TestRunner's worker pool), per-pair exception isolation (one bad pair doesn't fail the whole job), live-spool readiness/polling for bo_live and actively-written local roots"):
1. *Per-pair parallelism* — done: `TestRunner(max_workers=self._settings.max_workers)` runs all pairs of one job concurrently.
2. *Per-pair exception isolation* — done: a pair that raises becomes a synthetic ERROR-status pair result folded into the aggregate (job status becomes ERROR, but every other pair's real result is still present), instead of crashing the whole job.
3. *Live-spool readiness/polling* — done for the `kind: "local"` source that Phase 1/2 already support (an actively-written local root). **Not done for `bo_live`**, because `bo_live` is not yet a supported `FileSourceSpec.kind` in `multi_file` jobs at all (only `"local"` is implemented — see `etl_framework/reconciliation/file_mapping.py:258-263`); wiring `bo_live` into multi_file discovery is itself a later-phase item (S3/SFTP/bo_live discovery, roadmap §7 Phase 5), not something this phase can meaningfully harden yet. This is named explicitly here so it isn't silently dropped.

---

### Task 1: Pre-resolve segment columns once per job (thread-safety prerequisite)

**Files:**
- Modify: `api/services/run_executor.py`
- Test: `tests/unit/test_multi_file_jobs.py`

- [ ] **Step 1: Write the failing test**

APPEND to `tests/unit/test_multi_file_jobs.py`:

```python
def test_run_reconciliation_job_accepts_segment_columns_override(monkeypatch) -> None:
    """When segment_columns is passed explicitly, _resolve_segment_columns
    must not be called at all -- this is the thread-safety fix that lets
    multiple pairs run in parallel without hitting the shared DB session.
    """
    job = JobDefinition(
        name="orders_recon", job_type="reconciliation", query="",
        key_columns=["id"], params={"source_mode": "sql"},
    )
    executor = RunExecutor(
        db=None, run_id="test-run", source_env="source", target_env="target",
        job_sequence=[], run_settings=RunSettings(chunk_size=100, use_hash_precheck=True),
        config_snapshot={},
    )

    def _boom(_job):
        raise AssertionError("_resolve_segment_columns should not be called when an override is given")

    executor._resolve_segment_columns = _boom

    source_df = pd.DataFrame({"id": [1], "value": ["a"]})
    target_df = pd.DataFrame({"id": [1], "value": ["a"]})
    source_engine = FrameEngine(source_df, "source")
    target_engine = FrameEngine(target_df, "target")

    result = executor._run_reconciliation_job(
        job, source_engine, target_engine,
        query="__file_source__", params={}, chunk_size=0, use_hash_precheck=False,
        segment_columns=[],
    )

    assert result.status == TestStatus.PASSED
```

Add the two new imports this test needs to the top of `tests/unit/test_multi_file_jobs.py` if not already present:
```python
import pandas as pd
from api.services.frame_engine import FrameEngine
```
(Check the top of the file first -- `JobDefinition`, `RunSettings`, `RunExecutor`, `TestStatus` are already imported from earlier tasks.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py::test_run_reconciliation_job_accepts_segment_columns_override -v`
Expected: FAIL with `TypeError: _run_reconciliation_job() got an unexpected keyword argument 'segment_columns'`

- [ ] **Step 3: Write minimal implementation**

In `api/services/run_executor.py`, find `_run_reconciliation_job`. It currently reads:

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
```

Change it to:

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
        segment_columns: list[str] | None = None,
    ) -> ReconciliationResult:
        if segment_columns is None:
            segment_columns = self._resolve_segment_columns(job)
        engine = ReconciliationEngine(
```

Every other call site (`_build_case_file_reconciliation`, `_build_case_bo_live_recon`, the generic SQL `run_job`, `_build_case_multi_file_reconciliation`'s current sequential loop, etc.) doesn't pass `segment_columns`, so `segment_columns=None` there and the method falls back to calling `self._resolve_segment_columns(job)` exactly as before -- this change is purely additive and backward compatible.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py::test_run_reconciliation_job_accepts_segment_columns_override -v`
Expected: PASS

- [ ] **Step 5: Run the broader suite to confirm no regression**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py tests/unit/test_file_backed_jobs.py tests/unit/test_bo_live_reconciliation.py -v`
Expected: all PASS (this change only adds an optional parameter with a backward-compatible default; no existing call site is touched)

- [ ] **Step 6: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_multi_file_jobs.py
git commit -m "feat(run-executor): allow pre-resolved segment_columns to bypass shared DB read"
```

---

### Task 2: Aggregate rollup handles per-pair ERROR status

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Modify: `tests/unit/test_file_mapping.py`

- [ ] **Step 1: Write the failing test**

APPEND to `tests/unit/test_file_mapping.py`:

```python
def test_aggregate_reconciliation_results_escalates_to_error_when_a_pair_errors() -> None:
    from datetime import datetime, timezone
    from etl_framework.reconciliation.file_mapping import (
        DiscoveredFile as _DF,
        FileGroup as _FG,
        FilePair as _FP,
        FileMappingResult as _FMR,
        aggregate_reconciliation_results,
    )
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus

    def _result(status, mismatch_summary=None):
        return ReconciliationResult(
            query_name="pair", source_env="s", target_env="t",
            source_row_count=1, target_row_count=1, matched_count=1 if status == TestStatus.PASSED else 0,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=status,
            executed_at=datetime(2026, 7, 23, tzinfo=timezone.utc), duration_seconds=0.1,
            mismatch_summary=mismatch_summary,
        )

    east_source = _FG(key=("east",), files=[_DF("/s/e.csv", "e.csv", {"region": "east"})])
    east_target = _FG(key=("east",), files=[_DF("/t/e.dat", "e.dat", {"region": "east"})])
    west_source = _FG(key=("west",), files=[_DF("/s/w.csv", "w.csv", {"region": "west"})])
    west_target = _FG(key=("west",), files=[_DF("/t/w.dat", "w.dat", {"region": "west"})])
    mapping = _FMR(
        match_on=("region",),
        pairs=[
            _FP(key=("east",), source=east_source, target=east_target),
            _FP(key=("west",), source=west_source, target=west_target),
        ],
        unmatched_sources=[], unmatched_targets=[],
    )
    pair_results = [
        _result(TestStatus.PASSED),
        _result(TestStatus.ERROR, mismatch_summary={"error": "target file was truncated mid-write"}),
    ]

    aggregate = aggregate_reconciliation_results("regional_sales_recon", mapping, pair_results)

    assert aggregate.status == TestStatus.ERROR
    assert aggregate.mismatch_summary["pairs_total"] == 2
    assert aggregate.mismatch_summary["pairs_passed"] == 1
    assert aggregate.mismatch_summary["pairs_errored"] == 1
    by_region = {p["key"]["region"]: p for p in aggregate.mismatch_summary["file_pairs"]}
    assert by_region["east"]["error"] is None
    assert by_region["west"]["error"] == "target file was truncated mid-write"


def test_aggregate_reconciliation_results_all_passed_still_reports_zero_errored() -> None:
    from datetime import datetime, timezone
    from etl_framework.reconciliation.file_mapping import (
        DiscoveredFile as _DF,
        FileGroup as _FG,
        FilePair as _FP,
        FileMappingResult as _FMR,
        aggregate_reconciliation_results,
    )
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus

    def _result(status):
        return ReconciliationResult(
            query_name="pair", source_env="s", target_env="t",
            source_row_count=1, target_row_count=1, matched_count=1,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=status,
            executed_at=datetime(2026, 7, 23, tzinfo=timezone.utc), duration_seconds=0.1,
        )

    group = _FG(key=("east",), files=[_DF("/s/e.csv", "e.csv", {"region": "east"})])
    mapping = _FMR(match_on=("region",), pairs=[_FP(key=("east",), source=group, target=group)], unmatched_sources=[], unmatched_targets=[])

    aggregate = aggregate_reconciliation_results("job", mapping, [_result(TestStatus.PASSED)])

    assert aggregate.status == TestStatus.PASSED
    assert aggregate.mismatch_summary["pairs_errored"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_file_mapping.py::test_aggregate_reconciliation_results_escalates_to_error_when_a_pair_errors -v`
Expected: FAIL — `aggregate.status == TestStatus.FAILED` (not ERROR) today, since the current code only distinguishes PASSED vs not-PASSED, and `aggregate.mismatch_summary` has no `"pairs_errored"` key, and `pair_summaries` entries have no `"error"` key.

- [ ] **Step 3: Write minimal implementation**

In `etl_framework/reconciliation/file_mapping.py`, find `aggregate_reconciliation_results`. Its current body (from the `all_mismatches: list[MismatchRecord] = []` line through the `mismatch_summary={...}` dict) reads:

```python
    all_mismatches: list[MismatchRecord] = []
    pair_summaries: list[dict[str, Any]] = []
    pairs_passed = 0
    for pair, result in zip(mapping.pairs, pair_results):
        if mapping.match_on:
            pair_key = dict(zip(mapping.match_on, pair.key))
        else:
            pair_key = {
                "source_file": pair.source.files[0].file_name if pair.source.files else None,
                "target_file": pair.target.files[0].file_name if pair.target.files else None,
            }
        for mismatch in result.mismatches:
            all_mismatches.append(dataclasses.replace(
                mismatch,
                key_values={**mismatch.key_values, "__pair__": pair_key},
            ))
        if result.status == TestStatus.PASSED:
            pairs_passed += 1
        pair_summaries.append({
            "key": pair_key,
            "status": result.status.value,
            "source_files": [f.file_name for f in pair.source.files],
            "target_files": [f.file_name for f in pair.target.files],
            "source_row_count": result.source_row_count,
            "target_row_count": result.target_row_count,
            "matched_count": result.matched_count,
            "missing_in_target_count": result.missing_in_target_count,
            "missing_in_source_count": result.missing_in_source_count,
            "value_mismatch_count": result.value_mismatch_count,
        })

    total_pairs = len(mapping.pairs)
    total_source_files = sum(len(p.source.files) for p in mapping.pairs)
    total_target_files = sum(len(p.target.files) for p in mapping.pairs)

    return ReconciliationResult(
        query_name=job_name,
        source_env=pair_results[0].source_env if pair_results else "",
        target_env=pair_results[0].target_env if pair_results else "",
        source_row_count=sum(r.source_row_count for r in pair_results),
        target_row_count=sum(r.target_row_count for r in pair_results),
        matched_count=sum(r.matched_count for r in pair_results),
        missing_in_target_count=sum(r.missing_in_target_count for r in pair_results),
        missing_in_source_count=sum(r.missing_in_source_count for r in pair_results),
        value_mismatch_count=sum(r.value_mismatch_count for r in pair_results),
        mismatches=all_mismatches,
        status=TestStatus.PASSED if pairs_passed == total_pairs else TestStatus.FAILED,
        executed_at=min((r.executed_at for r in pair_results), default=datetime.now(timezone.utc)),
        duration_seconds=sum(r.duration_seconds for r in pair_results),
        mismatch_summary={
            "file_pairs": pair_summaries,
            "unmatched_sources": [_group_summary(g, mapping.match_on) for g in mapping.unmatched_sources],
            "unmatched_targets": [_group_summary(g, mapping.match_on) for g in mapping.unmatched_targets],
            "pairs_total": total_pairs,
            "pairs_passed": pairs_passed,
            "pairs_failed": total_pairs - pairs_passed,
        },
        source_file_name=f"{total_source_files} file(s) across {total_pairs} pair(s)",
        target_file_name=f"{total_target_files} file(s) across {total_pairs} pair(s)",
    )
```

Replace it with:

```python
    all_mismatches: list[MismatchRecord] = []
    pair_summaries: list[dict[str, Any]] = []
    pairs_passed = 0
    pairs_errored = 0
    for pair, result in zip(mapping.pairs, pair_results):
        if mapping.match_on:
            pair_key = dict(zip(mapping.match_on, pair.key))
        else:
            pair_key = {
                "source_file": pair.source.files[0].file_name if pair.source.files else None,
                "target_file": pair.target.files[0].file_name if pair.target.files else None,
            }
        for mismatch in result.mismatches:
            all_mismatches.append(dataclasses.replace(
                mismatch,
                key_values={**mismatch.key_values, "__pair__": pair_key},
            ))
        if result.status == TestStatus.PASSED:
            pairs_passed += 1
        elif result.status == TestStatus.ERROR:
            pairs_errored += 1
        error_message = (
            result.mismatch_summary.get("error")
            if isinstance(result.mismatch_summary, dict)
            else None
        )
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
        })

    total_pairs = len(mapping.pairs)
    total_source_files = sum(len(p.source.files) for p in mapping.pairs)
    total_target_files = sum(len(p.target.files) for p in mapping.pairs)

    if pairs_errored:
        overall_status = TestStatus.ERROR
    elif pairs_passed == total_pairs:
        overall_status = TestStatus.PASSED
    else:
        overall_status = TestStatus.FAILED

    return ReconciliationResult(
        query_name=job_name,
        source_env=pair_results[0].source_env if pair_results else "",
        target_env=pair_results[0].target_env if pair_results else "",
        source_row_count=sum(r.source_row_count for r in pair_results),
        target_row_count=sum(r.target_row_count for r in pair_results),
        matched_count=sum(r.matched_count for r in pair_results),
        missing_in_target_count=sum(r.missing_in_target_count for r in pair_results),
        missing_in_source_count=sum(r.missing_in_source_count for r in pair_results),
        value_mismatch_count=sum(r.value_mismatch_count for r in pair_results),
        mismatches=all_mismatches,
        status=overall_status,
        executed_at=min((r.executed_at for r in pair_results), default=datetime.now(timezone.utc)),
        duration_seconds=sum(r.duration_seconds for r in pair_results),
        mismatch_summary={
            "file_pairs": pair_summaries,
            "unmatched_sources": [_group_summary(g, mapping.match_on) for g in mapping.unmatched_sources],
            "unmatched_targets": [_group_summary(g, mapping.match_on) for g in mapping.unmatched_targets],
            "pairs_total": total_pairs,
            "pairs_passed": pairs_passed,
            "pairs_failed": total_pairs - pairs_passed,
            "pairs_errored": pairs_errored,
        },
        source_file_name=f"{total_source_files} file(s) across {total_pairs} pair(s)",
        target_file_name=f"{total_target_files} file(s) across {total_pairs} pair(s)",
    )
```

Note: `pairs_failed` keeps its old meaning (`total_pairs - pairs_passed`, a coarse "not passed" count that still includes errored pairs) so nothing reading that existing field breaks; `pairs_errored` is new, additive detail.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_file_mapping.py -v`
Expected: PASS, including both new tests. Also confirm the PRE-EXISTING `test_aggregate_reconciliation_results_rolls_up_pairs` and `test_aggregate_reconciliation_results_gives_automated_pairs_distinct_keys` tests (from Phase 1/2) still pass unchanged -- they never construct an ERROR-status pair result, so `pairs_errored` stays 0 and behavior is identical for them.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_file_mapping.py
git commit -m "feat(reconciliation): roll up per-pair ERROR status into the aggregate result"
```

---

### Task 3: Parallel, isolated per-pair execution in `RunExecutor`

**Files:**
- Modify: `api/services/run_executor.py`
- Modify: `tests/unit/test_multi_file_jobs.py`

- [ ] **Step 1: Write the failing test**

APPEND to `tests/unit/test_multi_file_jobs.py`:

```python
def test_run_executor_multi_file_reconciliation_isolates_one_failing_pair(tmp_path, monkeypatch) -> None:
    """One pair whose target file is unreadable must not crash the whole
    job -- the other pair's real result is still computed, and the
    aggregate status becomes ERROR (not a raised exception)."""
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_data_east_20260101.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (source_dir / "sales_data_west_20260101.csv").write_text("id,value\n1,bravo\n", encoding="utf-8")
    (target_dir / "financials_east_20260101.dat").write_text("id,value\n1,alpha\n", encoding="utf-8")
    # west's target file is discoverable (it's a real file matching the
    # pattern, so it still pairs normally) but empty -- pandas raises
    # EmptyDataError reading it, which is NOT caught by _read_csv_bytes's
    # narrower `except ParserError` fallback, so it propagates out of this
    # one pair's closure and is caught by TestRunner instead. A directory
    # would NOT work here: discover_local_files skips non-files outright
    # (`if not candidate.is_file(): continue`), so a directory never gets
    # discovered/paired at all -- it would show up as an unmatched source
    # instead, testing the wrong code path entirely.
    (target_dir / "financials_west_20260101.dat").write_text("", encoding="utf-8")

    job = JobDefinition(
        name="regional_sales_recon", job_type="reconciliation", query="",
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
        job_sequence=[], run_settings=RunSettings(chunk_size=100, use_hash_precheck=True, max_workers=2),
        config_snapshot={},
    )
    executor._resolve_segment_columns = lambda _job: []

    result = executor._build_case(job)()

    assert result.status == TestStatus.ERROR
    assert result.mismatch_summary["pairs_total"] == 2
    assert result.mismatch_summary["pairs_passed"] == 1
    assert result.mismatch_summary["pairs_errored"] == 1
    by_region = {p["key"]["region"]: p for p in result.mismatch_summary["file_pairs"]}
    assert by_region["east"]["status"] == "PASSED"
    assert by_region["west"]["status"] == "ERROR"
    assert by_region["west"]["error"] is not None


def test_run_executor_multi_file_reconciliation_runs_pairs_via_test_runner(tmp_path, monkeypatch) -> None:
    """Sanity check that the per-pair loop now goes through TestRunner:
    patch TestRunner.run and confirm it's invoked with one case per pair."""
    from api.services import file_source
    from etl_framework.runner import test_runner as test_runner_module

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_data_east_20260101.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (target_dir / "financials_east_20260101.dat").write_text("id,value\n1,alpha\n", encoding="utf-8")

    job = JobDefinition(
        name="regional_sales_recon", job_type="reconciliation", query="",
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
        job_sequence=[], run_settings=RunSettings(chunk_size=100, use_hash_precheck=True, max_workers=3),
        config_snapshot={},
    )
    executor._resolve_segment_columns = lambda _job: []

    captured_cases = []
    original_run = test_runner_module.TestRunner.run

    def _spy_run(self, cases):
        captured_cases.extend(cases)
        return original_run(self, cases)

    monkeypatch.setattr(test_runner_module.TestRunner, "run", _spy_run)

    result = executor._build_case(job)()

    assert result.status == TestStatus.PASSED
    assert len(captured_cases) == 1  # one pair in this job
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py::test_run_executor_multi_file_reconciliation_isolates_one_failing_pair tests/unit/test_multi_file_jobs.py::test_run_executor_multi_file_reconciliation_runs_pairs_via_test_runner -v`
Expected: FAIL — the isolation test currently raises an uncaught exception out of `run_job()` (the whole job crashes instead of reporting an ERROR-status aggregate); the TestRunner-spy test finds `captured_cases` empty because the per-pair loop is still a plain `for` loop, not routed through `TestRunner`.

- [ ] **Step 3: Write minimal implementation**

In `api/services/run_executor.py`, find `_build_case_multi_file_reconciliation`. The per-pair section currently reads (everything from `if not mapping.pairs:` through `return aggregate_reconciliation_results(...)`):

```python
            if not mapping.pairs:
                raise ValueError(f"multi_file reconciliation for '{job.name}' matched zero file pairs")

            pair_results: list[ReconciliationResult] = []
            for pair in mapping.pairs:
                source_df = pd.concat(
                    [read_tabular(path=f.path, file_name=f.file_name) for f in pair.source.files],
                    ignore_index=True,
                )
                target_df = pd.concat(
                    [read_tabular(path=f.path, file_name=f.file_name) for f in pair.target.files],
                    ignore_index=True,
                )
                source_df, target_df, resolved_keys = resolve_key_columns(
                    source_df,
                    target_df,
                    job.key_columns or self._settings.key_columns,
                    job.exclude_columns or [],
                )
                pair_job = job.model_copy(update={"key_columns": resolved_keys})
                source_label = "/".join(f.file_name for f in pair.source.files)
                target_label = "/".join(f.file_name for f in pair.target.files)
                source_engine = FrameEngine(source_df, source_label)
                target_engine = FrameEngine(target_df, target_label)
                pair_results.append(self._run_reconciliation_job(
                    pair_job,
                    source_engine,
                    target_engine,
                    query=FILE_SOURCE_QUERY,
                    params={},
                    chunk_size=0,
                    use_hash_precheck=False,
                ))

            return aggregate_reconciliation_results(job.name, mapping, pair_results)
        return run_job
```

Replace it with:

```python
            if not mapping.pairs:
                raise ValueError(f"multi_file reconciliation for '{job.name}' matched zero file pairs")

            segment_columns = self._resolve_segment_columns(job)

            def _make_pair_case(pair):
                def run_pair() -> ReconciliationResult:
                    source_df = pd.concat(
                        [read_tabular(path=f.path, file_name=f.file_name) for f in pair.source.files],
                        ignore_index=True,
                    )
                    target_df = pd.concat(
                        [read_tabular(path=f.path, file_name=f.file_name) for f in pair.target.files],
                        ignore_index=True,
                    )
                    source_df, target_df, resolved_keys = resolve_key_columns(
                        source_df,
                        target_df,
                        job.key_columns or self._settings.key_columns,
                        job.exclude_columns or [],
                    )
                    pair_job = job.model_copy(update={"key_columns": resolved_keys})
                    source_label = "/".join(f.file_name for f in pair.source.files)
                    target_label = "/".join(f.file_name for f in pair.target.files)
                    source_engine = FrameEngine(source_df, source_label)
                    target_engine = FrameEngine(target_df, target_label)
                    return self._run_reconciliation_job(
                        pair_job,
                        source_engine,
                        target_engine,
                        query=FILE_SOURCE_QUERY,
                        params={},
                        chunk_size=0,
                        use_hash_precheck=False,
                        segment_columns=segment_columns,
                    )
                return run_pair

            cases = [(f"pair_{i}", _make_pair_case(pair)) for i, pair in enumerate(mapping.pairs)]
            states = TestRunner(max_workers=self._settings.max_workers).run(cases)
            states_by_name = {state.name: state for state in states}

            pair_results: list[ReconciliationResult] = []
            for i, pair in enumerate(mapping.pairs):
                state = states_by_name[f"pair_{i}"]
                if state.result is not None:
                    pair_results.append(state.result)
                else:
                    source_label = "/".join(f.file_name for f in pair.source.files)
                    target_label = "/".join(f.file_name for f in pair.target.files)
                    pair_results.append(ReconciliationResult(
                        query_name=job.name,
                        source_env=source_label,
                        target_env=target_label,
                        source_row_count=0,
                        target_row_count=0,
                        matched_count=0,
                        missing_in_target_count=0,
                        missing_in_source_count=0,
                        value_mismatch_count=0,
                        mismatches=[],
                        status=state.status,
                        executed_at=state.completed_at or datetime.now(timezone.utc),
                        duration_seconds=state.duration_seconds or 0.0,
                        mismatch_summary={"error": state.error_message},
                    ))

            return aggregate_reconciliation_results(job.name, mapping, pair_results)
        return run_job
```

`TestRunner` is already imported at the top of `api/services/run_executor.py` (`from etl_framework.runner.test_runner import TestRunner`, used elsewhere for job-sequence execution) — no new import needed. `datetime`/`timezone` are already imported (`from datetime import datetime, timezone`).

Why this is correct: `TestRunner.run` returns results in *completion* order (`as_completed`), not submission order, so results are looked up by the `f"pair_{i}"` name key and re-assembled into `mapping.pairs` order before calling `aggregate_reconciliation_results` (which requires positional correspondence between `mapping.pairs` and `pair_results`). A pair whose closure raised gets `state.result is None` and `state.status == TestStatus.ERROR` (set by `TestRunner._run_single`'s except branch) with `state.error_message` populated — this becomes a synthetic zero-count `ReconciliationResult` carrying the error message in `mismatch_summary["error"]`, which Task 2's aggregate rollup already knows how to fold in (escalating overall status to ERROR, surfacing the message in that pair's `file_pairs[i]["error"]`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Run the full multi-file test surface to confirm no regression**

Run: `python -m pytest tests/unit/test_file_mapping.py tests/unit/test_file_mapping_similarity.py tests/unit/test_pair_files_automated.py tests/unit/test_file_mapping_manifest.py tests/unit/test_multi_file_jobs.py tests/unit/test_file_backed_jobs.py tests/unit/test_bo_live_reconciliation.py tests/property/test_file_mapping_property.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_multi_file_jobs.py
git commit -m "feat(run-executor): run multi_file pairs in parallel with per-pair failure isolation"
```

---

### Task 4: `ReadinessSpec` config parsing

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Modify: `tests/unit/test_file_mapping.py`

- [ ] **Step 1: Write the failing test**

APPEND to `tests/unit/test_file_mapping.py`:

```python
from etl_framework.reconciliation.file_mapping import ReadinessSpec


def test_file_mapping_spec_parses_readiness_on_a_source() -> None:
    spec = FileMappingSpec.from_params({
        "file_mapping": {
            "match_on": ["region"],
            "source": {
                "kind": "local", "root": "/spool", "pattern": "sales_{region}.csv",
                "readiness": {"expected_count": 3, "poll_interval_seconds": 2, "timeout_seconds": 60},
            },
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
        }
    })

    assert spec.source.readiness == ReadinessSpec(expected_count=3, poll_interval_seconds=2.0, timeout_seconds=60.0)
    assert spec.target.readiness is None


def test_file_mapping_spec_readiness_defaults_poll_interval_and_timeout() -> None:
    spec = FileMappingSpec.from_params({
        "file_mapping": {
            "match_on": ["region"],
            "source": {
                "kind": "local", "root": "/spool", "pattern": "sales_{region}.csv",
                "readiness": {"expected_count": 2},
            },
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
        }
    })

    assert spec.source.readiness == ReadinessSpec(expected_count=2, poll_interval_seconds=5.0, timeout_seconds=300.0)


def test_file_mapping_spec_rejects_readiness_without_expected_count() -> None:
    with pytest.raises(ValueError, match="expected_count must be a positive integer"):
        FileMappingSpec.from_params({
            "file_mapping": {
                "match_on": ["region"],
                "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv", "readiness": {}},
                "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
            }
        })


def test_file_mapping_spec_rejects_non_positive_poll_interval() -> None:
    with pytest.raises(ValueError, match="poll_interval_seconds must be a positive number"):
        FileMappingSpec.from_params({
            "file_mapping": {
                "match_on": ["region"],
                "source": {
                    "kind": "local", "root": "/spool", "pattern": "sales_{region}.csv",
                    "readiness": {"expected_count": 1, "poll_interval_seconds": 0},
                },
                "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
            }
        })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_file_mapping.py -v`
Expected: FAIL with `ImportError: cannot import name 'ReadinessSpec'`

- [ ] **Step 3: Write minimal implementation**

**Part A:** In `etl_framework/reconciliation/file_mapping.py`, find the `FileSourceSpec` dataclass:

```python
@dataclass(frozen=True)
class FileSourceSpec:
    kind: str
    root: str
    pattern: str
```

Add a `readiness` field:

```python
@dataclass(frozen=True)
class FileSourceSpec:
    kind: str
    root: str
    pattern: str
    readiness: "ReadinessSpec | None" = None
```

(This is safe regardless of where `ReadinessSpec` ends up being defined in the file: the module has `from __future__ import annotations` at the top, so the annotation `"ReadinessSpec | None"` is never evaluated at class-definition time -- only the literal default value `None` is, which needs no prior definition. This is a simpler situation than `AutomatedMappingSpec`'s `signals` field in Task 4 of Phase 2, which used a real name -- `KNOWN_SIMILARITY_SIGNALS` -- as its default value and therefore did need strict ordering.)

**Part B:** Find `_parse_file_source`:

```python
def _parse_file_source(raw: Any, side: str) -> FileSourceSpec:
    if not isinstance(raw, dict):
        raise ValueError(
            f"file_mapping.{side} requires an object with 'kind', 'root', and 'pattern'"
        )
    kind = raw.get("kind", "local")
    if kind != "local":
        raise ValueError(
            f"file_mapping.{side}.kind '{kind}' is not supported yet; "
            "only 'local' is implemented in this phase"
        )
    root = raw.get("root")
    pattern = raw.get("pattern")
    if not root or not pattern:
        raise ValueError(f"file_mapping.{side} requires both 'root' and 'pattern'")
    return FileSourceSpec(kind=kind, root=root, pattern=pattern)
```

Replace the final line to also parse readiness:

```python
def _parse_file_source(raw: Any, side: str) -> FileSourceSpec:
    if not isinstance(raw, dict):
        raise ValueError(
            f"file_mapping.{side} requires an object with 'kind', 'root', and 'pattern'"
        )
    kind = raw.get("kind", "local")
    if kind != "local":
        raise ValueError(
            f"file_mapping.{side}.kind '{kind}' is not supported yet; "
            "only 'local' is implemented in this phase"
        )
    root = raw.get("root")
    pattern = raw.get("pattern")
    if not root or not pattern:
        raise ValueError(f"file_mapping.{side} requires both 'root' and 'pattern'")
    readiness = _parse_readiness(raw.get("readiness"), side)
    return FileSourceSpec(kind=kind, root=root, pattern=pattern, readiness=readiness)
```

**Part C:** APPEND to the very end of `etl_framework/reconciliation/file_mapping.py`:

```python
@dataclass(frozen=True)
class ReadinessSpec:
    expected_count: int
    poll_interval_seconds: float = 5.0
    timeout_seconds: float = 300.0


def _parse_readiness(raw: Any, side: str) -> "ReadinessSpec | None":
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"file_mapping.{side}.readiness must be an object")
    expected_count = raw.get("expected_count")
    if not isinstance(expected_count, int) or isinstance(expected_count, bool) or expected_count < 1:
        raise ValueError(f"file_mapping.{side}.readiness.expected_count must be a positive integer")
    poll_interval = raw.get("poll_interval_seconds", 5.0)
    if not isinstance(poll_interval, (int, float)) or isinstance(poll_interval, bool) or poll_interval <= 0:
        raise ValueError(f"file_mapping.{side}.readiness.poll_interval_seconds must be a positive number")
    timeout = raw.get("timeout_seconds", 300.0)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError(f"file_mapping.{side}.readiness.timeout_seconds must be a positive number")
    return ReadinessSpec(
        expected_count=int(expected_count),
        poll_interval_seconds=float(poll_interval),
        timeout_seconds=float(timeout),
    )
```

`_parse_readiness` is called from `_parse_file_source` (defined earlier in the file) even though `_parse_readiness` itself is defined later -- this is safe for the same reason `_parse_automated_mapping` being called from `FileMappingSpec.from_params` was safe in Phase 2: Python only resolves a name used inside a function body when that function is *called*, long after the whole module has finished importing.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_file_mapping.py -v`
Expected: PASS (all tests, including the 4 new ones)

- [ ] **Step 5: Run the broader suite to confirm no regression**

Run: `python -m pytest tests/unit/test_file_mapping.py tests/unit/test_file_mapping_similarity.py tests/unit/test_pair_files_automated.py tests/unit/test_file_mapping_manifest.py tests/unit/test_multi_file_jobs.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_file_mapping.py
git commit -m "feat(reconciliation): parse optional per-source readiness (poll for expected file count)"
```

---

### Task 5: `wait_for_ready_files` polling function

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Create: `tests/unit/test_wait_for_ready_files.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_wait_for_ready_files.py
from __future__ import annotations

import pytest

from etl_framework.reconciliation.file_mapping import (
    DiscoveredFile,
    ReadinessSpec,
    wait_for_ready_files,
)


def _df(name: str) -> DiscoveredFile:
    return DiscoveredFile(path=f"/x/{name}", file_name=name, tokens={})


def test_wait_for_ready_files_returns_immediately_when_already_satisfied() -> None:
    calls = []

    def discover():
        calls.append(1)
        return [_df("a.csv"), _df("b.csv")]

    sleeps = []
    result = wait_for_ready_files(
        discover, ReadinessSpec(expected_count=2, poll_interval_seconds=1, timeout_seconds=10),
        sleep=sleeps.append,
    )

    assert [f.file_name for f in result] == ["a.csv", "b.csv"]
    assert len(calls) == 1  # no polling needed
    assert sleeps == []  # never slept


def test_wait_for_ready_files_polls_until_expected_count_reached() -> None:
    responses = [
        [_df("a.csv")],
        [_df("a.csv"), _df("b.csv")],
        [_df("a.csv"), _df("b.csv"), _df("c.csv")],
    ]

    def discover():
        return responses.pop(0)

    sleeps = []
    result = wait_for_ready_files(
        discover, ReadinessSpec(expected_count=3, poll_interval_seconds=1, timeout_seconds=10),
        sleep=sleeps.append,
    )

    assert len(result) == 3
    assert sleeps == [1, 1]  # slept twice (after the 1st and 2nd insufficient discoveries)


def test_wait_for_ready_files_raises_timeout_error_when_never_satisfied() -> None:
    def discover():
        return [_df("a.csv")]

    elapsed = {"total": 0.0}

    def fake_sleep(seconds: float) -> None:
        elapsed["total"] += seconds

    with pytest.raises(TimeoutError, match="only 1 of 5 expected file"):
        wait_for_ready_files(
            discover, ReadinessSpec(expected_count=5, poll_interval_seconds=2, timeout_seconds=5),
            sleep=fake_sleep,
        )

    assert elapsed["total"] >= 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_wait_for_ready_files.py -v`
Expected: FAIL with `ImportError: cannot import name 'wait_for_ready_files'`

- [ ] **Step 3: Write minimal implementation**

Update the top import block of `etl_framework/reconciliation/file_mapping.py`. It currently has (after prior tasks' edits) `Any, Sequence` from `typing`. Add `Callable`:

```python
from typing import Any, Callable, Sequence
```

Also add `import time` alongside the other stdlib imports (near `import dataclasses`, `import difflib`, `import json`, `import os`, `import re`):

```python
import dataclasses
import difflib
import json
import os
import re
import time
```

Then APPEND to the very end of `etl_framework/reconciliation/file_mapping.py`:

```python
def wait_for_ready_files(
    discover: Callable[[], list[DiscoveredFile]],
    readiness: ReadinessSpec,
    sleep: Callable[[float], None] = time.sleep,
) -> list[DiscoveredFile]:
    """Poll ``discover`` until it returns at least ``readiness.expected_count``
    files, or raise ``TimeoutError`` once ``readiness.timeout_seconds`` has
    elapsed. For a local root a live process is still actively writing into
    (e.g. several files spooled by one DB job), this avoids pairing/comparing
    against a partial write. ``sleep`` is injectable for tests; production
    callers use the real ``time.sleep``.
    """
    waited = 0.0
    discovered = discover()
    while len(discovered) < readiness.expected_count:
        if waited >= readiness.timeout_seconds:
            raise TimeoutError(
                f"only {len(discovered)} of {readiness.expected_count} expected file(s) "
                f"appeared within {readiness.timeout_seconds}s"
            )
        sleep(readiness.poll_interval_seconds)
        waited += readiness.poll_interval_seconds
        discovered = discover()
    return discovered
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_wait_for_ready_files.py -v`
Expected: PASS (3 tests, running instantly since `sleep` is faked -- no real waiting)

- [ ] **Step 5: Run the broader suite to confirm no regression**

Run: `python -m pytest tests/unit/test_file_mapping.py tests/unit/test_wait_for_ready_files.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_wait_for_ready_files.py
git commit -m "feat(reconciliation): add readiness polling for actively-written local sources"
```

---

### Task 6: Wire readiness polling into `RunExecutor` discovery

**Files:**
- Modify: `api/services/run_executor.py`
- Modify: `tests/unit/test_multi_file_jobs.py`

- [ ] **Step 1: Write the failing tests**

APPEND to `tests/unit/test_multi_file_jobs.py`:

```python
def test_run_executor_multi_file_readiness_satisfied_without_waiting(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_data_east_20260101.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (target_dir / "financials_east_20260101.dat").write_text("id,value\n1,alpha\n", encoding="utf-8")

    job = JobDefinition(
        name="regional_sales_recon", job_type="reconciliation", query="",
        key_columns=["id"],
        params={
            "source_mode": "multi_file",
            "file_mapping": {
                "strategy": "explicit",
                "match_on": ["region", "date"],
                "source": {
                    "kind": "local", "root": str(source_dir),
                    "pattern": "sales_data_{region}_{date:%Y%m%d}.csv",
                    "readiness": {"expected_count": 1, "poll_interval_seconds": 0.01, "timeout_seconds": 1},
                },
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

    assert result.status == TestStatus.PASSED


def test_run_executor_multi_file_readiness_times_out_when_files_never_arrive(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_data_east_20260101.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (target_dir / "financials_east_20260101.dat").write_text("id,value\n1,alpha\n", encoding="utf-8")

    job = JobDefinition(
        name="regional_sales_recon", job_type="reconciliation", query="",
        key_columns=["id"],
        params={
            "source_mode": "multi_file",
            "file_mapping": {
                "strategy": "explicit",
                "match_on": ["region", "date"],
                "source": {
                    "kind": "local", "root": str(source_dir),
                    "pattern": "sales_data_{region}_{date:%Y%m%d}.csv",
                    # Only 1 file will ever exist -- expecting 5 must time out quickly.
                    "readiness": {"expected_count": 5, "poll_interval_seconds": 0.05, "timeout_seconds": 0.15},
                },
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

    with pytest.raises(TimeoutError, match="only 1 of 5 expected file"):
        executor._build_case(job)()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py::test_run_executor_multi_file_readiness_satisfied_without_waiting tests/unit/test_multi_file_jobs.py::test_run_executor_multi_file_readiness_times_out_when_files_never_arrive -v`
Expected: FAIL — `readiness` is parsed into the config but nothing in `RunExecutor` reads `spec.source.readiness`/`spec.target.readiness` yet, so the timeout test finds no `TimeoutError` raised (it just discovers 1 file and proceeds).

- [ ] **Step 3: Write minimal implementation**

In `api/services/run_executor.py`, find `_build_case_multi_file_reconciliation`. Its discovery lines currently read:

```python
            spec = FileMappingSpec.from_params(job.params)
            source_root = resolve_allowed_path(spec.source.root)
            target_root = resolve_allowed_path(spec.target.root)
            source_files = discover_local_files(source_root, spec.source.pattern)
            target_files = discover_local_files(target_root, spec.target.pattern)
```

Replace the last two lines with:

```python
            spec = FileMappingSpec.from_params(job.params)
            source_root = resolve_allowed_path(spec.source.root)
            target_root = resolve_allowed_path(spec.target.root)

            if spec.source.readiness is not None:
                source_files = wait_for_ready_files(
                    lambda: discover_local_files(source_root, spec.source.pattern), spec.source.readiness,
                )
            else:
                source_files = discover_local_files(source_root, spec.source.pattern)

            if spec.target.readiness is not None:
                target_files = wait_for_ready_files(
                    lambda: discover_local_files(target_root, spec.target.pattern), spec.target.readiness,
                )
            else:
                target_files = discover_local_files(target_root, spec.target.pattern)
```

And add `wait_for_ready_files` to the existing `from etl_framework.reconciliation.file_mapping import (...)` block inside the method, which currently reads:

```python
            from etl_framework.reconciliation.file_mapping import (
                FileMappingManifestWriter,
                FileMappingSpec,
                aggregate_reconciliation_results,
                discover_local_files,
                pair_files,
                pair_files_automated,
            )
```

Change to:

```python
            from etl_framework.reconciliation.file_mapping import (
                FileMappingManifestWriter,
                FileMappingSpec,
                aggregate_reconciliation_results,
                discover_local_files,
                pair_files,
                pair_files_automated,
                wait_for_ready_files,
            )
```

A `TimeoutError` raised by `wait_for_ready_files` propagates out of `run_job()` uncaught, exactly like the existing `"multi_file reconciliation for '...' matched zero file pairs"` `ValueError` a few lines later already does -- this is deliberate: readiness failing means pairing never even starts, so it's a whole-job failure (caught at the job level by the sequence loop's own `TestRunner(max_workers=1).run(...)` call, `api/services/run_executor.py:240`), not a per-pair concern.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_multi_file_jobs.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Run the full multi-file test surface to confirm no regression**

Run: `python -m pytest tests/unit/test_file_mapping.py tests/unit/test_file_mapping_similarity.py tests/unit/test_pair_files_automated.py tests/unit/test_file_mapping_manifest.py tests/unit/test_multi_file_jobs.py tests/unit/test_wait_for_ready_files.py tests/unit/test_file_backed_jobs.py tests/unit/test_bo_live_reconciliation.py tests/property/test_file_mapping_property.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_multi_file_jobs.py
git commit -m "feat(run-executor): poll for readiness before discovering multi_file sources"
```

---

### Task 7: Documentation

**Files:**
- Modify: `docs/multi_file_reconciliation.md`

- [ ] **Step 1: Update the doc**

In `docs/multi_file_reconciliation.md`, find the "Current limitations (Phase 2)" section header and its bullet list, and the roadmap pointer line right after it:

```markdown
## Current limitations (Phase 2)

- `kind: "local"` only — S3 and SFTP sources are on the roadmap.
- Automated matching pairs single files only; shard-collapsing (many files
  on one side sharing a key) is `strategy: "explicit"` only.
- Pairs are compared sequentially; per-pair parallelism and per-pair failure
  isolation are on the roadmap.
- No dedicated web UI repeater yet; multi-file jobs are created via the API
  (or a hand-written JSON/YAML payload) until the job editor's file-mapping
  UI ships. The lineage manifest is a JSON file on disk, not yet surfaced in
  the UI or run report.

See `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md`
§7 for the full phased roadmap.
```

Replace it with:

```markdown
## Parallel execution and failure isolation

Pairs within one job run concurrently, using the run's `max_workers` setting
(the same setting used elsewhere for job-level parallelism, default 4). If
one pair's files can't be read or compared, that pair's result becomes an
`ERROR`-status entry in `mismatch_summary["file_pairs"]` (with the failure
message under `"error"`) instead of crashing the whole job — every other
pair's real result is still computed and reported. The aggregate job status
becomes `ERROR` whenever at least one pair errored (`mismatch_summary`
gains a `pairs_errored` count alongside the existing `pairs_total` /
`pairs_passed` / `pairs_failed`).

## Readiness (waiting for a live spool to finish writing)

For a local root a live process is still actively writing into, add a
`readiness` block to either side's source spec:

```json
{
  "source": {
    "kind": "local",
    "root": "/spool/live_exports",
    "pattern": "sales_data_{region}_{date:%Y%m%d}.csv",
    "readiness": {
      "expected_count": 6,
      "poll_interval_seconds": 5,
      "timeout_seconds": 300
    }
  }
}
```

Discovery polls that side every `poll_interval_seconds` until at least
`expected_count` files match the pattern, or fails the whole job with a
clear error once `timeout_seconds` elapses — so the job doesn't race a
partial spool and compare against files that haven't all landed yet.
`poll_interval_seconds` defaults to 5, `timeout_seconds` to 300.

## Current limitations (Phase 3)

- `kind: "local"` only — S3 and SFTP sources are on the roadmap.
- Readiness polling only applies to `kind: "local"` sources; `bo_live` isn't
  a supported multi_file source kind yet (a separate, later-phase item), so
  it has no readiness support here either.
- Automated matching pairs single files only; shard-collapsing (many files
  on one side sharing a key) is `strategy: "explicit"` only.
- No dedicated web UI repeater yet; multi-file jobs are created via the API
  (or a hand-written JSON/YAML payload) until the job editor's file-mapping
  UI ships. The lineage manifest is a JSON file on disk, not yet surfaced in
  the UI or run report.

See `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md`
§7 for the full phased roadmap.
```

- [ ] **Step 2: Commit**

```bash
git add docs/multi_file_reconciliation.md
git commit -m "docs: document parallel pair execution, failure isolation, and readiness polling"
```

---

## Self-review notes

- **Spec coverage:** Task 3 delivers per-pair parallelism and per-pair exception isolation together (both come from the same `TestRunner` reuse), matching the roadmap line exactly. Tasks 4-6 deliver readiness/polling for the one source kind (`local`) that actually exists in `multi_file` jobs today; `bo_live` readiness is explicitly named as out of scope (bo_live isn't a supported multi_file source kind at all yet) rather than silently skipped.
- **Thread-safety, not just a feature add:** Task 1 is a prerequisite fix (shared, non-thread-safe DB session read inside `_resolve_segment_columns`) discovered during design, not an afterthought — without it, Task 3's parallelism would be a real, if narrow, concurrency bug. Verified by reading `_resolve_segment_columns` and `_run_reconciliation_job` directly, not assumed.
- **Backward compatibility:** every existing call site of `_run_reconciliation_job` keeps working unchanged (new `segment_columns` parameter defaults to `None`, preserving the old self-resolve behavior). Every existing `aggregate_reconciliation_results` consumer keeps working unchanged (new `pairs_errored` field is additive; `pairs_failed`'s arithmetic is untouched). Explicit- and automated-strategy Phase 1/2 tests are re-run at the end of Tasks 3 and 6 to confirm.
- **Type/name consistency:** `ReadinessSpec`, `_parse_readiness`, `wait_for_ready_files`, `segment_columns` (parameter name) are spelled identically at every definition and call site across Tasks 1, 3, 4, 5, 6.
- **Ordering pitfalls avoided:** Task 4's `FileSourceSpec.readiness` field default is a literal `None` (not a name requiring prior definition), unlike Phase 2's `AutomatedMappingSpec.signals` case — called out explicitly in Task 4 Step 3 so whoever implements this doesn't over-apply the "must define first" lesson from Phase 2 where it doesn't actually apply.
- **Deferred, not silently dropped:** `bo_live` readiness, S3/SFTP sources, automated-strategy shard-collapse, and web UI surfacing are named in the "Current limitations (Phase 3)" doc section, matching the architecture doc's roadmap.
