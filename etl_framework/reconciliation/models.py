from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from etl_framework.runner.state import TestStatus


@dataclass
class MismatchRecord:
    key_values: dict[str, Any]
    column_name: str
    source_value: Any
    target_value: Any
    mismatch_type: str  # "value_diff" | "missing_in_target" | "missing_in_source"
    delta: float | None = None           # target_value - source_value (numeric only)
    relative_delta: float | None = None  # delta / source_value (numeric only, None when source is 0)
    segment_values: dict[str, Any] | None = None  # segment column -> value, for drill-down


@dataclass
class ReconciliationResult:
    query_name: str
    source_env: str
    target_env: str
    source_row_count: int
    target_row_count: int
    matched_count: int
    missing_in_target_count: int
    missing_in_source_count: int
    value_mismatch_count: int
    mismatches: list[MismatchRecord]
    status: TestStatus
    executed_at: datetime
    duration_seconds: float
    schema_diff: dict[str, list[str]] | None = None
    sample_rows: list[dict] | None = None
    segment_summary: dict | None = None  # segment col -> top-N mismatch buckets

    @property
    def total_issues(self) -> int:
        return (self.missing_in_target_count
                + self.missing_in_source_count
                + self.value_mismatch_count)

    @property
    def mismatch_by_column(self) -> dict[str, int]:
        """Count of mismatches grouped by column name."""
        counts: dict[str, int] = {}
        for m in self.mismatches:
            counts[m.column_name] = counts.get(m.column_name, 0) + 1
        return counts
