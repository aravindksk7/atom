from __future__ import annotations
import math
from datetime import timedelta

import numpy as np
import pandas as pd
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
        if not self._case_insensitive_columns and not self._whitespace_normalize_columns:
            return df
        df = df.copy()
        for col in self._case_insensitive_columns | self._whitespace_normalize_columns:
            if col not in df.columns:
                continue
            if pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col]):
                if col in self._whitespace_normalize_columns:
                    df[col] = df[col].str.strip().str.replace(r"\s+", " ", regex=True)
                if col in self._case_insensitive_columns:
                    df[col] = df[col].str.lower()
        return df

    def compare(self, df_source: pd.DataFrame, df_target: pd.DataFrame) -> list[MismatchRecord]:
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

        for df_miss, mtype, sv, tv in (
            (left_only,  "missing_in_target", "present", "missing"),
            (right_only, "missing_in_source", "missing", "present"),
        ):
            for row in df_miss[self._key_columns].itertuples(index=False):
                if len(mismatches) >= self._mismatch_row_limit:
                    return mismatches
                mismatches.append(MismatchRecord(
                    key_values=dict(zip(self._key_columns, row)),
                    column_name="<row>",
                    source_value=sv, target_value=tv,
                    mismatch_type=mtype,
                ))

        # --- value mismatches (vectorized per column) ---
        both = merged[merged["_merge"] == "both"].copy()
        if both.empty:
            return mismatches

        for col in value_cols:
            if len(mismatches) >= self._mismatch_row_limit:
                break
            src_col = f"{col}_src" if f"{col}_src" in both.columns else col
            tgt_col = f"{col}_tgt" if f"{col}_tgt" in both.columns else col
            if src_col not in both.columns or tgt_col not in both.columns:
                continue

            s = both[src_col]
            t = both[tgt_col]
            src_na = s.isna()
            tgt_na = t.isna()
            both_na = src_na & tgt_na
            neither_na = ~src_na & ~tgt_na
            tol = self._column_tolerances.get(col, self._float_tolerance)

            if pd.api.types.is_datetime64_any_dtype(s) and self._datetime_tolerance.total_seconds() > 0:
                val_eq = pd.Series(False, index=both.index, dtype=bool)
                if neither_na.any():
                    delta_ns = (s[neither_na] - t[neither_na]).abs()
                    tol_td = pd.Timedelta(seconds=self._datetime_tolerance.total_seconds())
                    val_eq[neither_na] = delta_ns <= tol_td
            elif pd.api.types.is_float_dtype(s):
                val_eq = pd.Series(False, index=both.index, dtype=bool)
                if neither_na.any():
                    val_eq[neither_na] = np.isclose(
                        s[neither_na].to_numpy(dtype=float),
                        t[neither_na].to_numpy(dtype=float),
                        rtol=0, atol=tol,
                    )
            else:
                val_eq = s.eq(t).fillna(False)

            match_mask = (both_na & self._null_equals_null) | (neither_na & val_eq)
            mismatch_rows = both[~match_mask]

            budget = self._mismatch_row_limit - len(mismatches)
            row_cols = list(mismatch_rows.columns)
            for row in mismatch_rows.iloc[:budget].itertuples(index=False, name=None):
                row_dict = dict(zip(row_cols, row))
                sv_val = row_dict.get(src_col)
                tv_val = row_dict.get(tgt_col)
                mismatches.append(MismatchRecord(
                    key_values={k: row_dict[k] for k in self._key_columns},
                    column_name=col,
                    source_value=sv_val,
                    target_value=tv_val,
                    mismatch_type="value_diff",
                ))

        return mismatches
