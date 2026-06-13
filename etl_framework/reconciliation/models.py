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

    @property
    def total_issues(self) -> int:
        return (self.missing_in_target_count
                + self.missing_in_source_count
                + self.value_mismatch_count)
