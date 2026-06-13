from __future__ import annotations
from typing import Protocol, runtime_checkable
import pandas as pd
from etl_framework.reconciliation.models import MismatchRecord


@runtime_checkable
class ComparisonBackend(Protocol):
    def compare(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
    ) -> list[MismatchRecord]:
        ...
