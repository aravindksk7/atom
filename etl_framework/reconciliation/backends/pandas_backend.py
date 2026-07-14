from __future__ import annotations
from datetime import timedelta

import pandas as pd
from etl_framework.reconciliation.backends.base import BackendCompareResult
from etl_framework.reconciliation.compare_utils import (
    build_mismatch_summary,
    normalize_string_columns,
    value_mismatch_mask,
)
from etl_framework.reconciliation.models import MismatchRecord


class PandasBackend:
    def __init__(
        self,
        key_columns: list[str],
        float_tolerance: float = 1e-9,
        null_equals_null: bool = True,
        mismatch_row_limit: int = 1000,
        column_tolerances: dict[str, float] | None = None,
        datetime_tolerance_seconds: float = 0.0,
        case_insensitive_columns: list[str] | None = None,
        whitespace_normalize_columns: list[str] | None = None,
    ) -> None:
        self._key_columns = key_columns
        self._float_tolerance = float_tolerance
        self._null_equals_null = null_equals_null
        self._mismatch_row_limit = mismatch_row_limit
        self._column_tolerances: dict[str, float] = column_tolerances or {}
        self._datetime_tolerance = timedelta(seconds=datetime_tolerance_seconds)
        self._case_insensitive_columns: frozenset[str] = frozenset(case_insensitive_columns or [])
        self._whitespace_normalize_columns: frozenset[str] = frozenset(whitespace_normalize_columns or [])

    def _normalize_df(self, df: pd.DataFrame) -> pd.DataFrame:
        return normalize_string_columns(
            df,
            self._case_insensitive_columns,
            self._whitespace_normalize_columns,
        )

    def compare(self, df_source: pd.DataFrame, df_target: pd.DataFrame) -> list[MismatchRecord]:
        return self.compare_with_counts(df_source, df_target).mismatches

    def compare_with_counts(self, df_source: pd.DataFrame, df_target: pd.DataFrame) -> BackendCompareResult:
        df_source = self._normalize_df(df_source)
        df_target = self._normalize_df(df_target)
        merged = pd.merge(
            df_source, df_target,
            on=self._key_columns, how="outer", indicator=True, suffixes=("_src", "_tgt"),
        )

        mismatches: list[MismatchRecord] = []
        key_set = set(self._key_columns)
        value_cols = [c for c in df_source.columns if c not in key_set]

        # --- missing rows (vectorized) ---
        left_only = merged[merged["_merge"] == "left_only"]
        right_only = merged[merged["_merge"] == "right_only"]
        missing_in_target_count = len(left_only)
        missing_in_source_count = len(right_only)

        for df_miss, mtype, sv, tv in (
            (left_only,  "missing_in_target", "present", "missing"),
            (right_only, "missing_in_source", "missing", "present"),
        ):
            remaining = self._mismatch_row_limit - len(mismatches)
            if remaining <= 0:
                break
            for row in df_miss[self._key_columns].head(remaining).itertuples(index=False):
                if len(mismatches) >= self._mismatch_row_limit:
                    break
                mismatches.append(MismatchRecord(
                    key_values=dict(zip(self._key_columns, row)),
                    column_name="<row>",
                    source_value=sv, target_value=tv,
                    mismatch_type=mtype,
                ))

        # --- value mismatches (vectorized per column) ---
        both = merged[merged["_merge"] == "both"].copy()
        if both.empty:
            return BackendCompareResult(
                matched_count=0,
                missing_in_target_count=missing_in_target_count,
                missing_in_source_count=missing_in_source_count,
                value_mismatch_count=0,
                mismatches=mismatches,
                mismatch_summary=build_mismatch_summary(
                    missing_in_target_count,
                    missing_in_source_count,
                    0,
                    {},
                    {str(col): 0 for col in value_cols},
                ),
            )

        value_mismatch_count = 0
        value_counts_by_column: dict[str, int] = {}
        key_count = len(self._key_columns)
        for col in value_cols:
            src_col = f"{col}_src" if f"{col}_src" in both.columns else col
            tgt_col = f"{col}_tgt" if f"{col}_tgt" in both.columns else col
            if src_col not in both.columns or tgt_col not in both.columns:
                continue

            tol = self._column_tolerances.get(col, self._float_tolerance)
            mismatch_mask = value_mismatch_mask(
                both,
                src_col,
                tgt_col,
                both[src_col],
                null_equals_null=self._null_equals_null,
                float_tolerance=self._float_tolerance,
                column_tolerance=tol,
                datetime_tolerance_seconds=self._datetime_tolerance.total_seconds(),
            )
            col_count = int(mismatch_mask.sum())
            value_mismatch_count += col_count
            if col_count:
                value_counts_by_column[str(col)] = col_count

            budget = self._mismatch_row_limit - len(mismatches)
            if budget <= 0:
                continue
            record_cols = self._key_columns + [src_col, tgt_col]
            for row in both.loc[mismatch_mask, record_cols].head(budget).itertuples(index=False, name=None):
                key_values = dict(zip(self._key_columns, row[:key_count]))
                mismatches.append(MismatchRecord(
                    key_values=key_values,
                    column_name=col,
                    source_value=row[key_count],
                    target_value=row[key_count + 1],
                    mismatch_type="value_diff",
                ))

        return BackendCompareResult(
            matched_count=len(both),
            missing_in_target_count=missing_in_target_count,
            missing_in_source_count=missing_in_source_count,
            value_mismatch_count=value_mismatch_count,
            mismatches=mismatches,
            mismatch_summary=build_mismatch_summary(
                missing_in_target_count,
                missing_in_source_count,
                value_mismatch_count,
                value_counts_by_column,
                {str(col): len(both) for col in value_cols},
            ),
        )

