from __future__ import annotations
import math
from datetime import timedelta

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
        column_tolerances: dict[str, float] | None = None,
        datetime_tolerance_seconds: float = 0.0,
    ) -> None:
        self._key_columns = key_columns
        self._float_tolerance = float_tolerance
        self._null_equals_null = null_equals_null
        self._mismatch_row_limit = mismatch_row_limit
        self._column_tolerances: dict[str, float] = column_tolerances or {}
        self._datetime_tolerance = timedelta(seconds=datetime_tolerance_seconds)

    def compare(self, df_source: pd.DataFrame, df_target: pd.DataFrame) -> list[MismatchRecord]:
        if not _POLARS_AVAILABLE:
            raise ImportError(
                "polars is required for PolarsBackend. "
                "Install it with: pip install polars pyarrow"
            )
        value_cols = [c for c in df_source.columns if c not in self._key_columns]
        mismatches: list[MismatchRecord] = []

        src = pl.from_pandas(df_source).with_columns(pl.lit(True).alias("__in_src__"))
        tgt = pl.from_pandas(df_target).with_columns(pl.lit(True).alias("__in_tgt__"))

        joined = src.join(tgt, on=self._key_columns, how="full", coalesce=True, suffix="_tgt")

        for row in joined.iter_rows(named=True):
            if len(mismatches) >= self._mismatch_row_limit:
                break
            key_vals = {k: row[k] for k in self._key_columns}
            in_src = row.get("__in_src__") is True
            in_tgt = row.get("__in_tgt__") is True

            if in_src and not in_tgt:
                mismatches.append(MismatchRecord(
                    key_values=key_vals, column_name="<row>",
                    source_value="present", target_value="missing",
                    mismatch_type="missing_in_target",
                ))
            elif in_tgt and not in_src:
                mismatches.append(MismatchRecord(
                    key_values=key_vals, column_name="<row>",
                    source_value="missing", target_value="present",
                    mismatch_type="missing_in_source",
                ))
            else:
                for col in value_cols:
                    a = row.get(col)
                    b = row.get(f"{col}_tgt")
                    is_float = isinstance(a, float) or isinstance(b, float)
                    is_dt = hasattr(a, "isoformat") or hasattr(b, "isoformat")
                    tol = self._column_tolerances.get(col, self._float_tolerance)
                    if not self._values_match(a, b, is_float=is_float, is_dt=is_dt, tolerance=tol):
                        mismatches.append(MismatchRecord(
                            key_values=key_vals, column_name=col,
                            source_value=a, target_value=b,
                            mismatch_type="value_diff",
                        ))
                        if len(mismatches) >= self._mismatch_row_limit:
                            break

        return mismatches

    def _values_match(self, a, b, is_float: bool, is_dt: bool = False, tolerance: float | None = None) -> bool:
        a_na = a is None or (isinstance(a, float) and math.isnan(a))
        b_na = b is None or (isinstance(b, float) and math.isnan(b))
        if a_na and b_na:
            return self._null_equals_null
        if a_na or b_na:
            return False
        if is_dt and self._datetime_tolerance.total_seconds() > 0:
            try:
                import pandas as _pd
                return abs((_pd.Timestamp(a) - _pd.Timestamp(b)).total_seconds()) <= self._datetime_tolerance.total_seconds()
            except Exception:
                return a == b
        if is_float:
            atol = tolerance if tolerance is not None else self._float_tolerance
            return abs(float(a) - float(b)) <= atol
        return a == b
