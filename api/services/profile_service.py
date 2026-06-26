"""Column profiling utilities: compute per-column stats and detect metric drift."""
from __future__ import annotations
import pandas as pd


def compute_profile(df: pd.DataFrame, columns: list[str]) -> dict[str, dict]:
    """Return per-column stats dict. Uses all columns when columns list is empty."""
    cols = columns if columns else list(df.columns)
    result: dict[str, dict] = {}
    for col in cols:
        if col not in df.columns:
            continue
        series = df[col]
        total = len(series)
        null_count = int(series.isna().sum())
        null_rate = null_count / total if total > 0 else 0.0
        distinct_count = int(series.nunique())

        numeric = pd.to_numeric(series, errors="coerce")
        has_numeric = numeric.notna().any()
        non_null = series.dropna()

        result[col] = {
            "null_rate": null_rate,
            "distinct_count": distinct_count,
            "min_val": str(non_null.min()) if not non_null.empty else None,
            "max_val": str(non_null.max()) if not non_null.empty else None,
            "mean_val": float(numeric.mean()) if has_numeric else None,
            "std_val": float(numeric.std()) if has_numeric else None,
            "p25": float(numeric.quantile(0.25)) if has_numeric else None,
            "p50": float(numeric.quantile(0.50)) if has_numeric else None,
            "p75": float(numeric.quantile(0.75)) if has_numeric else None,
            "p95": float(numeric.quantile(0.95)) if has_numeric else None,
        }
    return result


def detect_drift(
    current: dict[str, dict],
    previous: dict[str, dict],
    threshold_pct: float,
) -> list[str]:
    """Return column names whose numeric metrics shifted beyond threshold_pct vs previous profile."""
    if not previous:
        return []
    numeric_keys = ("mean_val", "std_val", "null_rate", "p25", "p50", "p75", "p95")
    flagged: list[str] = []
    for col, stats in current.items():
        if col not in previous:
            continue
        prev_stats = previous[col]
        for key in numeric_keys:
            cur_val = stats.get(key)
            prev_val = prev_stats.get(key)
            if cur_val is None or prev_val is None:
                continue
            if prev_val == 0:
                if cur_val != 0:
                    flagged.append(col)
                    break
            else:
                pct_change = abs(cur_val - prev_val) / abs(prev_val) * 100
                if pct_change > threshold_pct:
                    flagged.append(col)
                    break
    return flagged
