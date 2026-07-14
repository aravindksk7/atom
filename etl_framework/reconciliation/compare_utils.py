from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Iterable

import numpy as np
import pandas as pd


def normalize_string_columns(
    df: pd.DataFrame,
    case_insensitive_columns: Iterable[str] | None = None,
    whitespace_normalize_columns: Iterable[str] | None = None,
) -> pd.DataFrame:
    case_cols = set(case_insensitive_columns or [])
    whitespace_cols = set(whitespace_normalize_columns or [])
    if not case_cols and not whitespace_cols:
        return df
    result = df.copy()
    for col in case_cols | whitespace_cols:
        if col not in result.columns:
            continue
        if pd.api.types.is_object_dtype(result[col]) or pd.api.types.is_string_dtype(result[col]):
            if col in whitespace_cols:
                result[col] = result[col].str.strip().str.replace(r"\s+", " ", regex=True)
            if col in case_cols:
                result[col] = result[col].str.lower()
    return result


def value_columns(
    source_columns: Iterable[Any],
    target_columns: Iterable[Any],
    key_columns: Iterable[Any],
) -> list[Any]:
    target_set = set(target_columns)
    key_set = set(key_columns)
    return [col for col in source_columns if col not in key_set and col in target_set]


def value_mismatch_mask(
    both: pd.DataFrame,
    src_col: str,
    tgt_col: str,
    source_series: pd.Series,
    *,
    null_equals_null: bool = True,
    float_tolerance: float = 1e-9,
    column_tolerance: float | None = None,
    datetime_tolerance_seconds: float = 0.0,
) -> pd.Series:
    s = both[src_col]
    t = both[tgt_col]
    src_na = s.isna()
    tgt_na = t.isna()
    both_na = src_na & tgt_na
    neither_na = ~src_na & ~tgt_na
    tolerance = float_tolerance if column_tolerance is None else column_tolerance

    if pd.api.types.is_datetime64_any_dtype(source_series) and datetime_tolerance_seconds > 0:
        val_eq = pd.Series(False, index=both.index, dtype=bool)
        if neither_na.any():
            delta_ns = (s[neither_na] - t[neither_na]).abs()
            val_eq[neither_na] = delta_ns <= pd.Timedelta(seconds=datetime_tolerance_seconds)
    elif pd.api.types.is_float_dtype(source_series):
        val_eq = pd.Series(False, index=both.index, dtype=bool)
        if neither_na.any():
            val_eq[neither_na] = np.isclose(
                s[neither_na].to_numpy(dtype=float),
                t[neither_na].to_numpy(dtype=float),
                rtol=0,
                atol=tolerance,
            )
    else:
        val_eq = s.eq(t).fillna(False)
    return ~((both_na & null_equals_null) | (neither_na & val_eq))


def values_match(
    source_value: Any,
    target_value: Any,
    *,
    is_float: bool = False,
    is_datetime: bool = False,
    null_equals_null: bool = True,
    float_tolerance: float = 1e-9,
    datetime_tolerance: timedelta | None = None,
) -> bool:
    source_na = source_value is None or (isinstance(source_value, float) and math.isnan(source_value))
    target_na = target_value is None or (isinstance(target_value, float) and math.isnan(target_value))
    if source_na and target_na:
        return null_equals_null
    if source_na or target_na:
        return False
    if is_datetime and datetime_tolerance and datetime_tolerance.total_seconds() > 0:
        try:
            return abs((pd.Timestamp(source_value) - pd.Timestamp(target_value)).total_seconds()) <= datetime_tolerance.total_seconds()
        except Exception:
            return source_value == target_value
    if is_float:
        return abs(float(source_value) - float(target_value)) <= float_tolerance
    return source_value == target_value


def numeric_delta(source_value: Any, target_value: Any) -> tuple[float | None, float | None]:
    try:
        source = float(source_value)
        target = float(target_value)
    except (TypeError, ValueError):
        return None, None
    delta = target - source
    return delta, (delta / source if source != 0 else None)


def build_mismatch_summary(
    missing_in_target_count: int,
    missing_in_source_count: int,
    value_mismatch_count: int,
    value_counts_by_column: dict[str, int],
    compared_rows_by_column: dict[str, int] | None = None,
) -> dict[str, dict[str, int]]:
    by_column = {
        str(column): int(count)
        for column, count in value_counts_by_column.items()
        if int(count) > 0
    }
    compared = {
        str(column): int(count)
        for column, count in (compared_rows_by_column or {}).items()
        if int(count) >= 0
    }
    missing_rows = int(missing_in_target_count or 0) + int(missing_in_source_count or 0)
    if missing_rows > 0:
        by_column["<row>"] = by_column.get("<row>", 0) + missing_rows
        compared["<row>"] = compared.get("<row>", 0) + missing_rows
    return {
        "by_column": by_column,
        "compared_rows_by_column": compared,
        "by_type": {
            "value_diff": int(value_mismatch_count or 0),
            "missing_in_target": int(missing_in_target_count or 0),
            "missing_in_source": int(missing_in_source_count or 0),
        },
    }
