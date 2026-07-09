from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from etl_framework.repository.models import TERMINAL_STATUSES


@dataclass
class ReportResult:
    id: int | None
    query_name: str
    status: str
    effective_status: str
    duration_seconds: float
    source_row_count: int
    target_row_count: int
    value_mismatch_count: int
    missing_in_target_count: int
    missing_in_source_count: int
    error_message: str | None = None
    executed_at: datetime | None = None
    override_reason: str | None = None
    override_by: str | None = None
    override_at: datetime | None = None
    sample_rows: list[dict] | None = None
    segment_summary: dict | None = None
    mismatches: list[Any] = field(default_factory=list)
    schema_diff: Any = None
    total_issues_override: int | None = None

    @property
    def total_issues(self) -> int:
        if self.total_issues_override is not None:
            return self.total_issues_override
        return (
            (self.value_mismatch_count or 0)
            + (self.missing_in_target_count or 0)
            + (self.missing_in_source_count or 0)
        )


@dataclass
class RunReportSnapshot:
    run_id: str
    status: str
    raw_status: str
    started_at: datetime | None
    completed_at: datetime | None
    source_env: str | None
    target_env: str | None
    config_snapshot: dict | None
    run_type: str
    pair_id: str | None
    total_tests: int
    passed: int
    failed: int
    slow: int
    error: int
    raw_total_tests: int
    raw_passed: int
    raw_failed: int
    raw_slow: int
    raw_error: int
    results: list[ReportResult]
    has_result_rows: bool

    @property
    def test_cases(self) -> list[ReportResult]:
        return self.results

    @property
    def reconciliation_results(self) -> list[ReportResult]:
        return self.results

    @property
    def total_passed(self) -> int:
        return self.passed

    @property
    def total_failed(self) -> int:
        return self.failed + self.error

    @property
    def total_skipped(self) -> int:
        return 0

    def to_metrics(self) -> dict:
        total_duration = sum(float(result.duration_seconds or 0) for result in self.results)
        return {
            "run_id": self.run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_tests": self.total_tests,
            "passed": self.passed,
            "failed": self.failed,
            "slow": self.slow,
            "error": self.error,
            "total_duration_seconds": round(total_duration, 6),
            "tests": [
                {
                    "name": result.query_name,
                    "status": result.effective_status,
                    "raw_status": result.status,
                    "duration_seconds": float(result.duration_seconds or 0),
                    "source_row_count": result.source_row_count or 0,
                    "target_row_count": result.target_row_count or 0,
                    "total_issues": result.total_issues,
                }
                for result in self.results
            ],
            "source": "database",
        }


def _result_status(result: Any) -> str:
    return str(getattr(result, "status", None) or "UNKNOWN")


def _result_effective_status(result: Any) -> str:
    return str(getattr(result, "effective_status", None) or _result_status(result))


def _as_int(value: Any) -> int:
    return int(value or 0)


def _as_float(value: Any) -> float:
    return float(value or 0)


def _snapshot_status(raw_status: str, results: list[ReportResult]) -> str:
    if raw_status not in TERMINAL_STATUSES or not results:
        return raw_status
    statuses = [result.effective_status for result in results]
    if "ERROR" in statuses:
        return "ERROR"
    if "FAILED" in statuses:
        return "FAILED"
    if "SLOW" in statuses:
        return "SLOW"
    if statuses and all(status in {"PASSED", "SKIPPED"} for status in statuses):
        return "PASSED"
    return raw_status


def build_run_report_snapshot(run: Any, include_mismatches: bool = False) -> RunReportSnapshot:
    results = [
        ReportResult(
            id=getattr(result, "id", None),
            query_name=str(getattr(result, "query_name", "")),
            status=_result_status(result),
            effective_status=_result_effective_status(result),
            duration_seconds=_as_float(getattr(result, "duration_seconds", 0)),
            source_row_count=_as_int(getattr(result, "source_row_count", 0)),
            target_row_count=_as_int(getattr(result, "target_row_count", 0)),
            value_mismatch_count=_as_int(getattr(result, "value_mismatch_count", 0)),
            missing_in_target_count=_as_int(getattr(result, "missing_in_target_count", 0)),
            missing_in_source_count=_as_int(getattr(result, "missing_in_source_count", 0)),
            error_message=getattr(result, "error_message", None),
            executed_at=getattr(result, "executed_at", None),
            override_reason=getattr(result, "override_reason", None),
            override_by=getattr(result, "override_by", None),
            override_at=getattr(result, "override_at", None),
            sample_rows=getattr(result, "sample_rows", None),
            segment_summary=getattr(result, "segment_summary", None),
            mismatches=list(getattr(result, "mismatches", []) or []) if include_mismatches else [],
            schema_diff=getattr(result, "schema_diff", None),
            total_issues_override=getattr(result, "total_issues", None),
        )
        for result in (getattr(run, "results", []) or [])
    ]

    if results:
        passed = sum(1 for result in results if result.effective_status in {"PASSED", "SKIPPED"})
        failed = sum(1 for result in results if result.effective_status == "FAILED")
        slow = sum(1 for result in results if result.effective_status == "SLOW")
        error = sum(1 for result in results if result.effective_status == "ERROR")
        total_tests = len(results)
    else:
        passed = _as_int(getattr(run, "passed", 0))
        failed = _as_int(getattr(run, "failed", 0))
        slow = _as_int(getattr(run, "slow", 0))
        error = _as_int(getattr(run, "error", 0))
        total_tests = _as_int(getattr(run, "total_tests", 0))

    raw_status = str(getattr(run, "status", "UNKNOWN"))
    return RunReportSnapshot(
        run_id=str(getattr(run, "run_id", "")),
        status=_snapshot_status(raw_status, results),
        raw_status=raw_status,
        started_at=getattr(run, "started_at", None),
        completed_at=getattr(run, "completed_at", None),
        source_env=getattr(run, "source_env", None),
        target_env=getattr(run, "target_env", None),
        config_snapshot=getattr(run, "config_snapshot", None),
        run_type=str(getattr(run, "run_type", "reconciliation")),
        pair_id=getattr(run, "pair_id", None),
        total_tests=total_tests,
        passed=passed,
        failed=failed,
        slow=slow,
        error=error,
        raw_total_tests=_as_int(getattr(run, "total_tests", 0)),
        raw_passed=_as_int(getattr(run, "passed", 0)),
        raw_failed=_as_int(getattr(run, "failed", 0)),
        raw_slow=_as_int(getattr(run, "slow", 0)),
        raw_error=_as_int(getattr(run, "error", 0)),
        results=results,
        has_result_rows=bool(results),
    )
