from __future__ import annotations
import pandas as pd

try:
    import polars as pl
    _POLARS_AVAILABLE = True
except (ImportError, TypeError):
    _POLARS_AVAILABLE = False

from etl_framework.reconciliation.models import MismatchRecord


class PolarsBackend:
    def __init__(
        self,
        key_columns: list[str],
        float_tolerance: float = 1e-9,
        null_equals_null: bool = True,
        mismatch_row_limit: int = 1000,
    ) -> None:
        self._key_columns = key_columns
        self._float_tolerance = float_tolerance
        self._null_equals_null = null_equals_null
        self._mismatch_row_limit = mismatch_row_limit

    def compare(self, df_source: pd.DataFrame, df_target: pd.DataFrame) -> list[MismatchRecord]:
        if not _POLARS_AVAILABLE:
            raise ImportError(
                "polars is required for PolarsBackend. "
                "Install it with: pip install 'etl_framework[polars]'"
            )
        # Convert to Polars, do outer join, collect mismatches
        src = pl.from_pandas(df_source)
        tgt = pl.from_pandas(df_target)
        mismatches: list[MismatchRecord] = []
        joined = src.join(tgt, on=self._key_columns, how="outer", suffix="_tgt")
        for row in joined.iter_rows(named=True):
            if len(mismatches) >= self._mismatch_row_limit:
                break
            key_vals = {k: row[k] for k in self._key_columns}
            value_cols = [c for c in df_source.columns if c not in self._key_columns]
            for col in value_cols:
                tgt_col = f"{col}_tgt"
                a = row.get(col)
                b = row.get(tgt_col, row.get(col))
                if a != b:
                    mismatches.append(MismatchRecord(
                        key_values=key_vals, column_name=col,
                        source_value=a, target_value=b,
                        mismatch_type="value_mismatch",
                    ))
        return mismatches
