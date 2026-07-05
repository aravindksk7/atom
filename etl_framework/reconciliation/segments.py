"""Segment-based mismatch root-cause helpers.

Pure functions — no DB or engine dependencies.
"""
from __future__ import annotations

from etl_framework.reconciliation.models import MismatchRecord

MAX_AUTO_SEGMENT_COLUMNS = 3
MAX_AUTO_DISTINCT_COUNT = 50
TOP_N_SEGMENT_VALUES = 20


def pick_auto_segment_columns(
    profiles: list,
    key_columns: list[str],
    max_columns: int = MAX_AUTO_SEGMENT_COLUMNS,
    max_distinct: int = MAX_AUTO_DISTINCT_COUNT,
) -> list[str]:
    """Pick candidate segment columns from latest column profiles.

    Low-cardinality columns (distinct_count <= max_distinct) that are not
    key columns, at most max_columns, lowest distinct_count first.
    """
    keys = set(key_columns or [])
    candidates = [
        p for p in profiles
        if p.distinct_count is not None
        and p.distinct_count <= max_distinct
        and p.column_name not in keys
    ]
    candidates.sort(key=lambda p: p.distinct_count)
    return [p.column_name for p in candidates[:max_columns]]


def build_segment_summary(
    mismatches: list[MismatchRecord],
    segment_columns: list[str],
    top_n: int = TOP_N_SEGMENT_VALUES,
) -> dict | None:
    """Group mismatches by each segment column's value.

    Returns {segment_column: [{value, mismatch_count, missing_in_target,
    missing_in_source, value_diff, pct_of_total}, ...]} with the top_n most
    frequent values per column, or None when there is nothing to group.
    """
    total = len(mismatches)
    if not total or not segment_columns:
        return None

    summary: dict[str, list[dict]] = {}
    for col in segment_columns:
        buckets: dict[str, dict] = {}
        for m in mismatches:
            raw = (m.segment_values or {}).get(col)
            value = "(null)" if raw is None else str(raw)
            b = buckets.setdefault(value, {
                "value": value, "mismatch_count": 0,
                "missing_in_target": 0, "missing_in_source": 0, "value_diff": 0,
            })
            b["mismatch_count"] += 1
            if m.mismatch_type in ("missing_in_target", "missing_in_source", "value_diff"):
                b[m.mismatch_type] += 1
        rows = sorted(buckets.values(), key=lambda b: -b["mismatch_count"])[:top_n]
        for b in rows:
            b["pct_of_total"] = round(100.0 * b["mismatch_count"] / total, 2)
        summary[col] = rows
    return summary
