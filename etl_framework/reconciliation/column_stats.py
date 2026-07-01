from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd


@dataclass
class ColumnStats:
    column: str
    row_count: int
    null_count: int
    distinct_count: int
    min_value: Any = None
    max_value: Any = None
    mean: float | None = None
    std_dev: float | None = None
    sum: float | None = None


@dataclass
class ColumnStatsDiff:
    column: str
    metric: str
    source_value: Any
    target_value: Any
    delta: float | None = None


@dataclass
class ColumnStatsResult:
    """Result of an aggregate-level comparison.

    Suitable for very large tables where row-level diff is too expensive
    but distribution-level drift is still worth detecting.
    """
    query_name: str
    source_env: str
    target_env: str
    executed_at: datetime
    source_stats: dict[str, ColumnStats]   # column → stats
    target_stats: dict[str, ColumnStats]   # column → stats
    diffs: list[ColumnStatsDiff] = field(default_factory=list)

    @property
    def has_diffs(self) -> bool:
        return bool(self.diffs)

    @property
    def diff_by_column(self) -> dict[str, list[ColumnStatsDiff]]:
        out: dict[str, list[ColumnStatsDiff]] = {}
        for d in self.diffs:
            out.setdefault(d.column, []).append(d)
        return out


class ColumnStatsComparer:
    """Compare two DataFrames at the column aggregate level.

    For each column computes: row_count, null_count, distinct_count,
    min, max, mean, std_dev (numeric only), sum (numeric only).
    Flags any metric that differs between source and target.

    This is complementary to ``ReconciliationEngine`` — use it when you
    want a fast data-drift check without a full row-level reconciliation.
    """

    _NUMERIC_METRICS = ("mean", "std_dev", "sum", "min_value", "max_value")
    _ALL_METRICS = ("row_count", "null_count", "distinct_count") + _NUMERIC_METRICS

    def __init__(
        self,
        float_tolerance: float = 1e-9,
        row_count_tolerance: int = 0,
    ) -> None:
        self._float_tolerance = float_tolerance
        self._row_count_tolerance = row_count_tolerance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compare(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
        query_name: str = "stats_compare",
        source_env: str = "source",
        target_env: str = "target",
    ) -> ColumnStatsResult:
        src_stats = self._compute_stats(df_source)
        tgt_stats = self._compute_stats(df_target)
        diffs = self._diff_stats(src_stats, tgt_stats)
        return ColumnStatsResult(
            query_name=query_name,
            source_env=source_env,
            target_env=target_env,
            executed_at=datetime.now(timezone.utc),
            source_stats=src_stats,
            target_stats=tgt_stats,
            diffs=diffs,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compute_stats(self, df: pd.DataFrame) -> dict[str, ColumnStats]:
        stats: dict[str, ColumnStats] = {}
        for col in df.columns:
            series = df[col]
            row_count = len(series)
            null_count = int(series.isna().sum())
            distinct_count = int(series.nunique(dropna=True))
            min_v = max_v = mean_v = std_v = sum_v = None

            numeric = pd.to_numeric(series, errors="coerce")
            if not numeric.isna().all():
                non_null = numeric.dropna()
                min_v = float(non_null.min()) if len(non_null) > 0 else None
                max_v = float(non_null.max()) if len(non_null) > 0 else None
                mean_v = float(non_null.mean()) if len(non_null) > 0 else None
                std_v = float(non_null.std()) if len(non_null) > 1 else None
                sum_v = float(non_null.sum()) if len(non_null) > 0 else None
            elif pd.api.types.is_datetime64_any_dtype(series):
                non_null = series.dropna()
                if len(non_null) > 0:
                    min_v = non_null.min()
                    max_v = non_null.max()

            stats[col] = ColumnStats(
                column=col,
                row_count=row_count,
                null_count=null_count,
                distinct_count=distinct_count,
                min_value=min_v,
                max_value=max_v,
                mean=mean_v,
                std_dev=std_v,
                sum=sum_v,
            )
        return stats

    def _diff_stats(
        self,
        src: dict[str, ColumnStats],
        tgt: dict[str, ColumnStats],
    ) -> list[ColumnStatsDiff]:
        diffs: list[ColumnStatsDiff] = []
        all_cols = sorted(set(src) | set(tgt))
        for col in all_cols:
            s = src.get(col)
            t = tgt.get(col)
            if s is None or t is None:
                diffs.append(ColumnStatsDiff(
                    column=col, metric="present",
                    source_value=s is not None,
                    target_value=t is not None,
                ))
                continue
            for metric in self._ALL_METRICS:
                sv = getattr(s, metric)
                tv = getattr(t, metric)
                if not self._metric_matches(metric, sv, tv):
                    delta: float | None = None
                    try:
                        delta = float(tv) - float(sv)
                    except (TypeError, ValueError):
                        pass
                    diffs.append(ColumnStatsDiff(
                        column=col, metric=metric,
                        source_value=sv, target_value=tv,
                        delta=delta,
                    ))
        return diffs

    def _metric_matches(self, metric: str, sv: Any, tv: Any) -> bool:
        if sv is None and tv is None:
            return True
        if sv is None or tv is None:
            return False
        if metric == "row_count":
            return abs(int(sv) - int(tv)) <= self._row_count_tolerance
        if metric in self._NUMERIC_METRICS:
            try:
                import numpy as np
                return bool(np.isclose(float(sv), float(tv), rtol=0, atol=self._float_tolerance))
            except (TypeError, ValueError):
                pass
        return sv == tv
