from __future__ import annotations
import math
from datetime import timedelta

import pandas as pd

try:
    import polars as pl
    _POLARS_AVAILABLE = True
except (ImportError, TypeError):
    _POLARS_AVAILABLE = False

from etl_framework.reconciliation.backends.base import BackendCompareResult
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
        return self.compare_with_counts(df_source, df_target).mismatches

    def compare_with_counts(self, df_source: pd.DataFrame, df_target: pd.DataFrame) -> BackendCompareResult:
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
        in_src = pl.col("__in_src__").fill_null(False)
        in_tgt = pl.col("__in_tgt__").fill_null(False)

        matched_count, missing_in_target_count, missing_in_source_count = joined.select(
            (in_src & in_tgt).sum().alias("matched"),
            (in_src & ~in_tgt).sum().alias("missing_in_target"),
            (~in_src & in_tgt).sum().alias("missing_in_source"),
        ).row(0)

        self._append_missing_records(
            joined,
            in_src & ~in_tgt,
            "missing_in_target",
            "present",
            "missing",
            mismatches,
        )
        self._append_missing_records(
            joined,
            ~in_src & in_tgt,
            "missing_in_source",
            "missing",
            "present",
            mismatches,
        )

        value_mismatch_count = 0
        value_counts_by_column: dict[str, int] = {}
        key_count = len(self._key_columns)
        both_sides = in_src & in_tgt
        for col in value_cols:
            src_col = col
            tgt_col = f"{col}_tgt"
            if src_col not in joined.columns or tgt_col not in joined.columns:
                continue

            mismatch_expr = both_sides & self._value_mismatch_expr(
                col,
                src_col,
                tgt_col,
                df_source[col],
                df_target[col],
            )
            col_count = int(joined.select(mismatch_expr.sum()).item())
            value_mismatch_count += col_count
            if col_count:
                value_counts_by_column[str(col)] = col_count

            remaining = self._mismatch_row_limit - len(mismatches)
            if remaining <= 0 or col_count == 0:
                continue
            record_cols = self._key_columns + [src_col, tgt_col]
            for row in joined.filter(mismatch_expr).select(record_cols).head(remaining).iter_rows(named=False):
                key_values = dict(zip(self._key_columns, row[:key_count]))
                mismatches.append(MismatchRecord(
                    key_values=key_values,
                    column_name=col,
                    source_value=row[key_count],
                    target_value=row[key_count + 1],
                    mismatch_type="value_diff",
                ))

        return BackendCompareResult(
            matched_count=int(matched_count),
            missing_in_target_count=int(missing_in_target_count),
            missing_in_source_count=int(missing_in_source_count),
            value_mismatch_count=value_mismatch_count,
            mismatches=mismatches,
            mismatch_summary=self._build_mismatch_summary(
                int(missing_in_target_count),
                int(missing_in_source_count),
                value_mismatch_count,
                value_counts_by_column,
            ),
        )

    @staticmethod
    def _build_mismatch_summary(
        missing_in_target_count: int,
        missing_in_source_count: int,
        value_mismatch_count: int,
        value_counts_by_column: dict[str, int],
    ) -> dict[str, dict[str, int]]:
        by_column = {
            str(column): int(count)
            for column, count in value_counts_by_column.items()
            if int(count) > 0
        }
        missing_rows = int(missing_in_target_count or 0) + int(missing_in_source_count or 0)
        if missing_rows > 0:
            by_column["<row>"] = by_column.get("<row>", 0) + missing_rows
        return {
            "by_column": by_column,
            "by_type": {
                "value_diff": int(value_mismatch_count or 0),
                "missing_in_target": int(missing_in_target_count or 0),
                "missing_in_source": int(missing_in_source_count or 0),
            },
        }

    def _append_missing_records(
        self,
        joined,
        mask,
        mismatch_type: str,
        source_value: str,
        target_value: str,
        mismatches: list[MismatchRecord],
    ) -> None:
        remaining = self._mismatch_row_limit - len(mismatches)
        if remaining <= 0:
            return
        for row in joined.filter(mask).select(self._key_columns).head(remaining).iter_rows(named=True):
            mismatches.append(MismatchRecord(
                key_values={k: row[k] for k in self._key_columns},
                column_name="<row>",
                source_value=source_value,
                target_value=target_value,
                mismatch_type=mismatch_type,
            ))

    def _value_mismatch_expr(
        self,
        col: str,
        src_col: str,
        tgt_col: str,
        source_series: pd.Series,
        target_series: pd.Series,
    ):
        s = pl.col(src_col)
        t = pl.col(tgt_col)
        src_null = s.is_null()
        tgt_null = t.is_null()
        both_null = src_null & tgt_null
        neither_null = ~src_null & ~tgt_null
        tol = self._column_tolerances.get(col, self._float_tolerance)

        if (
            (
                pd.api.types.is_datetime64_any_dtype(source_series)
                or pd.api.types.is_datetime64_any_dtype(target_series)
            )
            and self._datetime_tolerance.total_seconds() > 0
        ):
            tol_us = int(self._datetime_tolerance.total_seconds() * 1_000_000)
            val_eq = ((s - t).abs() <= pl.duration(microseconds=tol_us)).fill_null(False)
        elif pd.api.types.is_float_dtype(source_series) or pd.api.types.is_float_dtype(target_series):
            val_eq = (
                (s.cast(pl.Float64, strict=False) - t.cast(pl.Float64, strict=False)).abs() <= tol
            ).fill_null(False)
        elif self._can_compare_exactly(source_series, target_series):
            val_eq = (s == t).fill_null(False)
        elif self._string_like(source_series) and self._string_like(target_series):
            val_eq = (
                s.cast(pl.Utf8, strict=False) == t.cast(pl.Utf8, strict=False)
            ).fill_null(False)
        else:
            val_eq = pl.lit(False)

        return ~((both_null & pl.lit(self._null_equals_null)) | (neither_null & val_eq))

    @staticmethod
    def _can_compare_exactly(source_series: pd.Series, target_series: pd.Series) -> bool:
        return (
            source_series.dtype == target_series.dtype
            or (
                pd.api.types.is_numeric_dtype(source_series)
                and pd.api.types.is_numeric_dtype(target_series)
            )
        )

    @staticmethod
    def _string_like(series: pd.Series) -> bool:
        return pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)

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
