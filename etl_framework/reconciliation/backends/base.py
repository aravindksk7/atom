from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
import pandas as pd
from etl_framework.reconciliation.models import MismatchRecord


@dataclass
class BackendCompareResult:
    matched_count: int
    missing_in_target_count: int
    missing_in_source_count: int
    value_mismatch_count: int
    mismatches: list[MismatchRecord]
    mismatch_summary: dict[str, Any] | None = None


@runtime_checkable
class ComparisonBackend(Protocol):
    def compare(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
    ) -> list[MismatchRecord]:
        ...

    def compare_with_counts(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
    ) -> BackendCompareResult:
        ...
