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
    mismatch_summary: dict[str, Any] | None = None
    source_file_name: str | None = None
    target_file_name: str | None = None

    @property
    def total_issues(self) -> int:
        return (self.missing_in_target_count
                + self.missing_in_source_count
                + self.value_mismatch_count)

    def _summary_counts(self, key: str) -> dict[str, int]:
        if not isinstance(self.mismatch_summary, dict):
            return {}
        raw_counts = self.mismatch_summary.get(key)
        if not isinstance(raw_counts, dict):
            return {}
        counts: dict[str, int] = {}
        for raw_key, raw_value in raw_counts.items():
            try:
                count = int(raw_value or 0)
            except (TypeError, ValueError):
                continue
            if count > 0:
                counts[str(raw_key)] = count
        return counts

    @property
    def mismatch_by_column(self) -> dict[str, int]:
        """Count of mismatches grouped by column name."""
        summary_counts = self._summary_counts("by_column")
        if summary_counts:
            return summary_counts

        counts: dict[str, int] = {}
        for m in self.mismatches:
            counts[m.column_name] = counts.get(m.column_name, 0) + 1
        missing_rows = (self.missing_in_target_count or 0) + (self.missing_in_source_count or 0)
        if missing_rows > 0:
            counts["<row>"] = missing_rows
        return counts

    @property
    def mismatch_by_type(self) -> dict[str, int]:
        """Count of mismatches grouped by mismatch type."""
        counts = {
            "value_diff": 0,
            "missing_in_target": 0,
            "missing_in_source": 0,
        }
        summary_counts = self._summary_counts("by_type")
        if summary_counts:
            counts.update(summary_counts)
            return counts
        counts.update({
            "value_diff": self.value_mismatch_count or 0,
            "missing_in_target": self.missing_in_target_count or 0,
            "missing_in_source": self.missing_in_source_count or 0,
        })
        return counts
