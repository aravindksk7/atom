"""Alignment of BusinessObjects PDF DataFrames for PDF-vs-PDF comparison."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypeVar

import pandas as pd

from etl_framework.bo_layout.model import ColumnInfo, PdfSourceInfo

_T = TypeVar("_T")
_WHITESPACE_RE = re.compile(r"\s+")


def align_column_case(df: pd.DataFrame, reference_names: Iterable[Any]) -> pd.DataFrame:
    """Rename columns to the reference spelling when names differ only by case.

    Skipped for a column when the match is ambiguous (several reference
    spellings, or several frame columns sharing the lower-cased name) or when
    the new name would duplicate an existing column.
    """
    spellings: dict[str, set[str]] = {}
    for name in reference_names:
        spellings.setdefault(str(name).lower(), set()).add(str(name))
    lowered = [str(c).lower() for c in df.columns]
    existing = {str(c) for c in df.columns}
    rename: dict[Any, str] = {}
    for column, low in zip(df.columns, lowered):
        options = spellings.get(low)
        if not options or len(options) != 1 or lowered.count(low) != 1:
            continue
        (wanted,) = options
        if wanted != str(column) and wanted not in existing:
            rename[column] = wanted
    return df.rename(columns=rename) if rename else df


def _build_ci_map(mapping: Mapping[str, _T]) -> dict[str, _T]:
    return {str(k).lower(): v for k, v in mapping.items()}


def _lookup_ci(mapping: Mapping[str, _T], ci_map: Mapping[str, _T], col: str) -> _T | None:
    if col in mapping:
        return mapping[col]
    return ci_map.get(col.lower())


def _effective_kind(info: ColumnInfo) -> str:
    if info.kind == "date" and info.ambiguous_formats:
        return "text"
    return info.kind


def _series_to_text(series: pd.Series, info: ColumnInfo, effective_kind: str) -> pd.Series:
    decimals = info.displayed_decimals
    values: list[Any] = []
    for val in series:
        if pd.isna(val):
            values.append(pd.NA)
        elif effective_kind == "number" and decimals is not None:
            try:
                values.append(f"{float(val):.{decimals}f}")
            except (TypeError, ValueError):
                values.append(str(val))
        else:
            values.append(str(val))
    return pd.Series(values, index=series.index, dtype="object")


def _rewrap_text_column(series_a: pd.Series, series_b: pd.Series) -> tuple[pd.Series, int]:
    grouped: dict[str, set[str]] = {}
    for val in series_a:
        if pd.isna(val):
            continue
        text = str(val)
        grouped.setdefault(_WHITESPACE_RE.sub("", text), set()).add(text)
    unique_map = {key: next(iter(vals)) for key, vals in grouped.items() if len(vals) == 1}
    if not unique_map:
        return series_b, 0

    replacements = 0
    new_values: list[Any] = []
    for val in series_b:
        if pd.isna(val):
            new_values.append(val)
            continue
        text = str(val)
        mapped = unique_map.get(_WHITESPACE_RE.sub("", text))
        if mapped is not None and text != mapped:
            new_values.append(mapped)
            replacements += 1
        else:
            new_values.append(val)
    if replacements == 0:
        return series_b, 0
    return pd.Series(new_values, index=series_b.index, dtype="object"), replacements


def align_pdf_frames(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    info_a: PdfSourceInfo,
    info_b: PdfSourceInfo,
    *,
    key_columns: Sequence[str] = (),
    exclude_columns: Sequence[str] = (),
    rewrap: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Align two PDF-extracted DataFrames and report printed-format differences."""
    fixed_refs = list(key_columns) + list(exclude_columns)
    out_a = align_column_case(df_a.copy(), fixed_refs + list(df_b.columns))
    out_b = align_column_case(df_b.copy(), fixed_refs + list(out_a.columns))

    ci_info_a = _build_ci_map(info_a.column_info)
    ci_info_b = _build_ci_map(info_b.column_info)
    widths_a_raw: Mapping[str, Any] = info_a.table.get("column_widths") or {}
    widths_b_raw: Mapping[str, Any] = info_b.table.get("column_widths") or {}
    ci_widths_a = _build_ci_map(widths_a_raw)
    ci_widths_b = _build_ci_map(widths_b_raw)

    rewrapped_values: dict[str, int] = {}
    printed_as: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    precision_parts: list[str] = []

    b_col_set = set(out_b.columns)
    for col in dict.fromkeys(out_a.columns):
        if col not in b_col_set:
            continue
        col_name = str(col)
        col_info_a = _lookup_ci(info_a.column_info, ci_info_a, col_name)
        col_info_b = _lookup_ci(info_b.column_info, ci_info_b, col_name)
        if col_info_a is None or col_info_b is None:
            continue

        kind_a = _effective_kind(col_info_a)
        kind_b = _effective_kind(col_info_b)

        if kind_a == "number" and kind_b == "number":
            da = col_info_a.displayed_decimals
            db = col_info_b.displayed_decimals
            if da is not None and db is not None and da != db:
                warnings.append(f"{col_name}: printed with different decimals (A {da} dp, B {db} dp)")
                printed_as[col_name] = {"a": f"{da} dp", "b": f"{db} dp"}
            decimals = [d for d in (da, db) if d is not None]
            if decimals:
                precision_parts.append(f"{col_name}: {min(decimals)} dp")
        elif kind_a == "date" and kind_b == "date":
            fa = col_info_a.date_format
            fb = col_info_b.date_format
            if fa != fb:
                warnings.append(f"{col_name}: printed in different date formats (A {fa}, B {fb})")
                printed_as[col_name] = {"a": fa, "b": fb}
        elif kind_a != kind_b:
            out_a[col] = _series_to_text(out_a[col], col_info_a, kind_a)
            out_b[col] = _series_to_text(out_b[col], col_info_b, kind_b)
            warnings.append(f"{col_name}: printed as {kind_a} in A and {kind_b} in B, compared as text")
        elif kind_a == "text" and rewrap:
            raw_wa = _lookup_ci(widths_a_raw, ci_widths_a, col_name)
            raw_wb = _lookup_ci(widths_b_raw, ci_widths_b, col_name)
            wa = float(raw_wa) if raw_wa is not None else 0.0
            wb = float(raw_wb) if raw_wb is not None else 0.0
            if abs(wa - wb) > 1.0:
                new_b, count = _rewrap_text_column(out_a[col], out_b[col])
                if count > 0:
                    out_b[col] = new_b
                    rewrapped_values[col_name] = count
                    warnings.append(
                        f"{col_name}: column widths differ (A {wa:.1f} pt, B {wb:.1f} pt); "
                        f"{count} values re-wrapped to A's form"
                    )

    precision_note = (
        "compared as printed; differences smaller than the printed decimals can't be seen ("
        + ", ".join(precision_parts)
        + ")"
        if precision_parts
        else None
    )

    return out_a, out_b, {
        "rewrapped_values": rewrapped_values,
        "printed_as": printed_as,
        "precision_note": precision_note,
        "warnings": warnings,
    }
