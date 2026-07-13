from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

try:
    from scipy import stats as scipy_stats
except ImportError:  # pragma: no cover - optional dependency
    scipy_stats = None


logger = logging.getLogger(__name__)


@dataclass
class DQViolation:
    rule_type: str
    column: str | None
    message: str
    severity: str
    actual_value: Any = None


class DQEngine:
    def evaluate(self, df: pd.DataFrame, rules: list, engine=None) -> list[DQViolation]:
        violations: list[DQViolation] = []
        for rule in rules:
            rtype = rule.type
            col = rule.column
            sev = rule.severity

            if rtype == "not_null":
                if col and col in df.columns:
                    null_count = int(df[col].isna().sum())
                    if null_count:
                        violations.append(DQViolation(
                            rule_type=rtype, column=col, severity=sev,
                            message=f"{null_count} null value(s) in '{col}'",
                            actual_value=null_count,
                        ))

            elif rtype == "unique":
                if col and col in df.columns:
                    dup_count = int(df[col].duplicated().sum())
                    if dup_count:
                        violations.append(DQViolation(
                            rule_type=rtype, column=col, severity=sev,
                            message=f"{dup_count} duplicate value(s) in '{col}'",
                            actual_value=dup_count,
                        ))

            elif rtype == "row_count_min":
                actual = len(df)
                if rule.min_value is not None and actual < rule.min_value:
                    violations.append(DQViolation(
                        rule_type=rtype, column=None, severity=sev,
                        message=f"Row count {actual} < min {rule.min_value}",
                        actual_value=actual,
                    ))

            elif rtype == "row_count_max":
                actual = len(df)
                if rule.max_value is not None and actual > rule.max_value:
                    violations.append(DQViolation(
                        rule_type=rtype, column=None, severity=sev,
                        message=f"Row count {actual} > max {rule.max_value}",
                        actual_value=actual,
                    ))

            elif rtype == "row_count_between":
                actual = len(df)
                lo = rule.min_value if rule.min_value is not None else float("-inf")
                hi = rule.max_value if rule.max_value is not None else float("inf")
                if not (lo <= actual <= hi):
                    violations.append(DQViolation(
                        rule_type=rtype, column=None, severity=sev,
                        message=f"Row count {actual} not in [{lo}, {hi}]",
                        actual_value=actual,
                    ))

            elif rtype == "column_mean_between":
                if col and col in df.columns:
                    try:
                        actual = float(pd.to_numeric(df[col], errors="coerce").mean())
                        lo = rule.min_value if rule.min_value is not None else float("-inf")
                        hi = rule.max_value if rule.max_value is not None else float("inf")
                        if not (lo <= actual <= hi):
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"Mean of '{col}' ({actual:.4g}) not in [{lo}, {hi}]",
                                actual_value=actual,
                            ))
                    except Exception:
                        pass

            elif rtype == "match_regex":
                if col and col in df.columns and rule.pattern:
                    try:
                        pattern = re.compile(rule.pattern)
                        bad = int((~df[col].astype(str).str.match(pattern)).sum())
                        if bad:
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"{bad} value(s) in '{col}' do not match /{rule.pattern}/",
                                actual_value=bad,
                            ))
                    except re.error:
                        pass

            elif rtype == "custom_sql":
                pass  # custom_sql requires query engine access; skipped in pure-DF mode

            elif rtype == "column_max_length":
                if col and col in df.columns and rule.max_value is not None:
                    try:
                        lengths = df[col].dropna().astype(str).str.len()
                        bad = int((lengths > rule.max_value).sum())
                        if bad:
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"{bad} value(s) in '{col}' exceed max length {rule.max_value}",
                                actual_value=bad,
                            ))
                    except Exception:
                        pass  # Skip if conversion fails

            elif rtype == "column_min_length":
                if col and col in df.columns and rule.min_value is not None:
                    try:
                        lengths = df[col].dropna().astype(str).str.len()
                        bad = int((lengths < rule.min_value).sum())
                        if bad:
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"{bad} value(s) in '{col}' below min length {rule.min_value}",
                                actual_value=bad,
                            ))
                    except Exception:
                        pass  # Skip if conversion fails

            elif rtype == "value_in_set":
                configured_values = rule.values or ([v.strip() for v in rule.sql.split(",")] if rule.sql else [])
                if col and col in df.columns and configured_values:
                    try:
                        allowed_values = {str(v) for v in configured_values}
                        bad = int((~df[col].astype(str).isin(allowed_values)).sum())
                        if bad:
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"{bad} value(s) in '{col}' not in allowed set",
                                actual_value=bad,
                            ))
                    except Exception:
                        pass  # Skip if parsing fails

            elif rtype == "value_not_in_set":
                configured_values = rule.values or ([v.strip() for v in rule.sql.split(",")] if rule.sql else [])
                if col and col in df.columns and configured_values:
                    try:
                        forbidden_values = {str(v) for v in configured_values}
                        bad = int((df[col].astype(str).isin(forbidden_values)).sum())
                        if bad:
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"{bad} value(s) in '{col}' are in forbidden set",
                                actual_value=bad,
                            ))
                    except Exception:
                        pass  # Skip if parsing fails

            elif rtype == "column_contains":
                if col and col in df.columns and rule.pattern:
                    try:
                        values = df[col].dropna().astype(str)
                        bad = int((~values.str.contains(rule.pattern, regex=False)).sum())
                        if bad:
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"{bad} value(s) in '{col}' do not contain '{rule.pattern}'",
                                actual_value=bad,
                            ))
                    except Exception:
                        pass  # Skip if regex fails

            elif rtype == "date_range":
                if col and col in df.columns:
                    try:
                        date_col = pd.to_datetime(df[col], errors='coerce')
                        min_bound = rule.min_date if rule.min_date is not None else rule.min_value
                        max_bound = rule.max_date if rule.max_date is not None else rule.max_value
                        min_date = pd.to_datetime(min_bound) if min_bound is not None else None
                        max_date = pd.to_datetime(max_bound) if max_bound is not None else None
                        null_dates = int(date_col.isna().sum())
                        if min_date is not None and max_date is not None:
                            bad = int(((date_col < min_date) | (date_col > max_date)).sum())
                        elif min_date is not None:
                            bad = int((date_col < min_date).sum())
                        elif max_date is not None:
                            bad = int((date_col > max_date).sum())
                        else:
                            bad = 0

                        total_bad = bad + null_dates
                        if total_bad:
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"{total_bad} value(s) in '{col}' are outside date range or invalid",
                                actual_value=total_bad,
                            ))
                    except Exception:
                        pass  # Skip if date parsing fails

            elif rtype == "positive_values":
                if col and col in df.columns:
                    try:
                        numeric_col = pd.to_numeric(df[col], errors='coerce')
                        null_count = int(numeric_col.isna().sum())
                        bad_count = int((numeric_col <= 0).sum())  # <= 0 includes negatives and zero
                        total_bad = null_count + bad_count
                        if total_bad:
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"{total_bad} value(s) in '{col}' are not positive (null, zero, or negative)",
                                actual_value=total_bad,
                            ))
                    except Exception:
                        pass  # Skip if numeric conversion fails

            elif rtype == "negative_values":
                if col and col in df.columns:
                    try:
                        numeric_col = pd.to_numeric(df[col], errors='coerce')
                        null_count = int(numeric_col.isna().sum())
                        bad_count = int((numeric_col >= 0).sum())  # >= 0 includes positives and zero
                        total_bad = null_count + bad_count
                        if total_bad:
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"{total_bad} value(s) in '{col}' are not negative (null, zero, or positive)",
                                actual_value=total_bad,
                            ))
                    except Exception:
                        pass

            elif rtype == "completeness_ratio":
                if col and col in df.columns and rule.min_value is not None:
                    total = len(df)
                    if total > 0:
                        ratio = float(df[col].notna().sum()) / total
                        if ratio < rule.min_value:
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"Completeness of '{col}' ({ratio:.2%}) < min {rule.min_value:.2%}",
                                actual_value=ratio,
                            ))

            elif rtype == "distinct_count_between":
                if col and col in df.columns:
                    actual = int(df[col].nunique())
                    lo = rule.min_value if rule.min_value is not None else float("-inf")
                    hi = rule.max_value if rule.max_value is not None else float("inf")
                    if not (lo <= actual <= hi):
                        violations.append(DQViolation(
                            rule_type=rtype, column=col, severity=sev,
                            message=f"Distinct count of '{col}' ({actual}) not in [{lo}, {hi}]",
                            actual_value=actual,
                        ))

            elif rtype == "column_sum_between":
                if col and col in df.columns:
                    try:
                        actual = float(pd.to_numeric(df[col], errors="coerce").sum())
                        lo = rule.min_value if rule.min_value is not None else float("-inf")
                        hi = rule.max_value if rule.max_value is not None else float("inf")
                        if not (lo <= actual <= hi):
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"Sum of '{col}' ({actual:.4g}) not in [{lo}, {hi}]",
                                actual_value=actual,
                            ))
                    except Exception:
                        pass

            elif rtype == "column_std_dev_between":
                if col and col in df.columns:
                    try:
                        actual = float(pd.to_numeric(df[col], errors="coerce").std())
                        lo = rule.min_value if rule.min_value is not None else float("-inf")
                        hi = rule.max_value if rule.max_value is not None else float("inf")
                        if not (lo <= actual <= hi):
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"Std dev of '{col}' ({actual:.4g}) not in [{lo}, {hi}]",
                                actual_value=actual,
                            ))
                    except Exception:
                        pass

            elif rtype == "column_percentile":
                if col and col in df.columns and rule.percentile is not None:
                    try:
                        actual = float(pd.to_numeric(df[col], errors="coerce").quantile(rule.percentile / 100))
                        lo = rule.min_value if rule.min_value is not None else float("-inf")
                        hi = rule.max_value if rule.max_value is not None else float("inf")
                        if not (lo <= actual <= hi):
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"p{rule.percentile} of '{col}' ({actual:.4g}) not in [{lo}, {hi}]",
                                actual_value=actual,
                            ))
                    except Exception:
                        pass

            elif rtype == "column_type_check":
                if col and col in df.columns and rule.expected_type:
                    non_null = df[col].dropna()
                    if rule.expected_type in ("int", "float"):
                        bad = int(pd.to_numeric(non_null, errors="coerce").isna().sum())
                    elif rule.expected_type == "date":
                        bad = int(pd.to_datetime(non_null, errors="coerce").isna().sum())
                    elif rule.expected_type == "uuid":
                        import re as _re
                        _uuid_re = _re.compile(
                            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                            _re.IGNORECASE,
                        )
                        bad = int((~non_null.astype(str).str.match(_uuid_re)).sum())
                    else:
                        bad = 0
                    if bad:
                        violations.append(DQViolation(
                            rule_type=rtype, column=col, severity=sev,
                            message=f"{bad} value(s) in '{col}' cannot be cast to {rule.expected_type}",
                            actual_value=bad,
                        ))

            elif rtype == "column_value_between":
                if col and col in df.columns:
                    try:
                        numeric = pd.to_numeric(df[col], errors="coerce")
                        lo = rule.min_value if rule.min_value is not None else float("-inf")
                        hi = rule.max_value if rule.max_value is not None else float("inf")
                        bad = int(((numeric < lo) | (numeric > hi)).sum())
                        if bad:
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"{bad} value(s) in '{col}' outside [{lo}, {hi}]",
                                actual_value=bad,
                            ))
                    except Exception:
                        pass

            elif rtype == "cross_column_consistency":
                col_a = col
                col_b = rule.column_b
                op = rule.operator or "<="
                if col_a and col_b and col_a in df.columns and col_b in df.columns:
                    try:
                        a = pd.to_numeric(df[col_a], errors="coerce")
                        b = pd.to_numeric(df[col_b], errors="coerce")
                        if op == "<=":
                            mask = a > b
                        elif op == "<":
                            mask = a >= b
                        elif op == ">=":
                            mask = a < b
                        elif op == ">":
                            mask = a <= b
                        elif op == "==":
                            mask = a != b
                        else:
                            mask = pd.Series([False] * len(df))
                        bad = int(mask.sum())
                        if bad:
                            violations.append(DQViolation(
                                rule_type=rtype, column=col_a, severity=sev,
                                message=f"{bad} row(s) violate {col_a} {op} {col_b}",
                                actual_value=bad,
                            ))
                    except Exception:
                        pass

            elif rtype == "pii_mask_check":
                if col and col in df.columns and rule.pattern:
                    try:
                        import re as _re
                        pat = _re.compile(rule.pattern)
                        bad = int(df[col].astype(str).str.match(pat).sum())
                        if bad:
                            violations.append(DQViolation(
                                rule_type=rtype, column=col, severity=sev,
                                message=f"{bad} value(s) in '{col}' match PII pattern — column may be unmasked",
                                actual_value=bad,
                            ))
                    except Exception:
                        pass

            elif rtype == "no_whitespace":
                if col and col in df.columns:
                    vals = df[col].dropna().astype(str)
                    bad = int((vals != vals.str.strip()).sum())
                    if bad:
                        violations.append(DQViolation(
                            rule_type=rtype, column=col, severity=sev,
                            message=f"{bad} value(s) in '{col}' have leading/trailing whitespace",
                            actual_value=bad,
                        ))

            elif rtype == "referential_check":
                if col and col in df.columns and rule.lookup_query:
                    if engine is None:
                        import logging as _logging
                        _logging.getLogger("etl_framework.dq_engine").warning(
                            "referential_check rule skipped — no DB engine available"
                        )
                    else:
                        try:
                            lookup_df = engine.execute_query(rule.lookup_query)
                            valid_values: set = set(lookup_df.iloc[:, 0].astype(str)) if not lookup_df.empty else set()
                            col_vals = df[col].dropna().astype(str)
                            bad = int((~col_vals.isin(valid_values)).sum())
                            if bad:
                                violations.append(DQViolation(
                                    rule_type=rtype, column=col, severity=sev,
                                    message=f"{bad} value(s) in '{col}' not found in lookup query result",
                                    actual_value=bad,
                                ))
                        except Exception as exc:
                            import logging as _logging
                            _logging.getLogger("etl_framework.dq_engine").warning(
                                "referential_check failed: %s", exc
                            )

            elif rtype == "custom_sql_assert":
                if rule.sql and rule.operator:
                    if engine is None:
                        logger.warning(
                            "custom_sql_assert rule skipped — no DB engine available"
                        )
                    else:
                        try:
                            result_df = engine.execute_query(rule.sql)
                            if result_df.empty or result_df.shape != (1, 1):
                                violations.append(DQViolation(
                                    rule_type=rtype, column=None, severity="error",
                                    message=f"custom_sql_assert expected 1 row x 1 col, got {result_df.shape}",
                                    actual_value=None,
                                ))
                            else:
                                scalar = float(result_df.iloc[0, 0])
                                threshold = rule.min_value if rule.min_value is not None else 0.0
                                op = rule.operator
                                passed = (
                                    (op == ">=" and scalar >= threshold) or
                                    (op == ">" and scalar > threshold) or
                                    (op == "<=" and scalar <= threshold) or
                                    (op == "<" and scalar < threshold) or
                                    (op == "==" and scalar == threshold) or
                                    (op == "!=" and scalar != threshold)
                                )
                                if not passed:
                                    violations.append(DQViolation(
                                        rule_type=rtype, column=None, severity=sev,
                                        message=f"SQL scalar {scalar} did not satisfy {op} {threshold}",
                                        actual_value=scalar,
                                    ))
                        except Exception as exc:
                            violations.append(DQViolation(
                                rule_type=rtype, column=None, severity=sev,
                                message=f"custom_sql_assert error: {exc}",
                                actual_value=None,
                            ))

            elif rtype == "outlier_zscore":
                if col and col in df.columns:
                    try:
                        numeric = pd.to_numeric(df[col], errors="coerce").dropna()
                        if len(numeric) > 1:
                            std = float(numeric.std())
                            if std > 0:
                                threshold = rule.threshold if rule.threshold is not None else 3.0
                                zscores = ((numeric - float(numeric.mean())) / std).abs()
                                bad = int((zscores > threshold).sum())
                                if bad:
                                    violations.append(DQViolation(
                                        rule_type=rtype, column=col, severity=sev,
                                        message=f"{bad} value(s) in '{col}' exceed z-score threshold {threshold}",
                                        actual_value=bad,
                                    ))
                    except Exception:
                        pass

            elif rtype == "outlier_iqr":
                if col and col in df.columns:
                    try:
                        numeric = pd.to_numeric(df[col], errors="coerce").dropna()
                        if len(numeric) > 0:
                            q1 = float(numeric.quantile(0.25))
                            q3 = float(numeric.quantile(0.75))
                            iqr = q3 - q1
                            multiplier = rule.iqr_multiplier if rule.iqr_multiplier is not None else 1.5
                            if rule.fence_type == "outer":
                                multiplier = max(multiplier, 3.0)
                            lo = q1 - multiplier * iqr
                            hi = q3 + multiplier * iqr
                            bad = int(((numeric < lo) | (numeric > hi)).sum())
                            if bad:
                                violations.append(DQViolation(
                                    rule_type=rtype, column=col, severity=sev,
                                    message=f"{bad} value(s) in '{col}' fall outside IQR fence [{lo:.4g}, {hi:.4g}]",
                                    actual_value=bad,
                                ))
                    except Exception:
                        pass

            elif rtype == "outlier_grubbs":
                if col and col in df.columns:
                    if scipy_stats is None:
                        logger.warning("outlier_grubbs skipped — scipy not installed")
                    else:
                        try:
                            numeric = pd.to_numeric(df[col], errors="coerce").dropna()
                            n = len(numeric)
                            if n > 2:
                                std = float(numeric.std())
                                if std > 0:
                                    alpha = rule.alpha if rule.alpha is not None else 0.05
                                    mean = float(numeric.mean())
                                    g_stat = float((numeric - mean).abs().max() / std)
                                    t_crit = float(scipy_stats.t.ppf(1 - alpha / (2 * n), n - 2))
                                    g_crit = ((n - 1) / (n ** 0.5)) * (((t_crit ** 2) / (n - 2 + t_crit ** 2)) ** 0.5)
                                    if g_stat > g_crit:
                                        violations.append(DQViolation(
                                            rule_type=rtype, column=col, severity=sev,
                                            message=f"Grubbs test detected an outlier in '{col}' (G={g_stat:.4g} > critical {g_crit:.4g})",
                                            actual_value=g_stat,
                                        ))
                        except Exception:
                            pass

            elif rtype == "distribution_ks_test":
                if col and col in df.columns:
                    if scipy_stats is None:
                        logger.warning("distribution_ks_test skipped — scipy not installed")
                    else:
                        try:
                            numeric = pd.to_numeric(df[col], errors="coerce").dropna()
                            if len(numeric) > 1:
                                alpha = rule.alpha if rule.alpha is not None else 0.05
                                dist = rule.distribution or "normal"
                                params = rule.distribution_params or {}
                                if dist == "normal":
                                    loc = params.get("mean", float(numeric.mean()))
                                    scale = params.get("std", float(numeric.std()) or 1.0)
                                    statistic, p_value = scipy_stats.kstest(numeric, "norm", args=(loc, scale))
                                elif dist == "uniform":
                                    lo = params.get("min", float(numeric.min()))
                                    hi = params.get("max", float(numeric.max()))
                                    span = hi - lo if hi > lo else 1.0
                                    statistic, p_value = scipy_stats.kstest(numeric, "uniform", args=(lo, span))
                                else:
                                    scale = 1.0 / params.get("lam", 1.0)
                                    statistic, p_value = scipy_stats.kstest(numeric, "expon", args=(0.0, scale))
                                if p_value < alpha:
                                    violations.append(DQViolation(
                                        rule_type=rtype, column=col, severity=sev,
                                        message=f"KS test rejected {dist} distribution for '{col}' (p={p_value:.4g} < {alpha})",
                                        actual_value=float(p_value),
                                    ))
                        except Exception:
                            pass

            elif rtype == "distribution_chi_square":
                if col and col in df.columns:
                    if scipy_stats is None:
                        logger.warning("distribution_chi_square skipped — scipy not installed")
                    else:
                        try:
                            numeric = pd.to_numeric(df[col], errors="coerce").dropna()
                            expected = rule.expected_frequencies or []
                            bins = rule.bins if rule.bins is not None else len(expected)
                            if len(numeric) > 0 and expected and bins > 0:
                                observed, _ = np.histogram(numeric, bins=bins)
                                scale = len(numeric) / sum(expected)
                                expected_scaled = [v * scale for v in expected]
                                statistic, p_value = scipy_stats.chisquare(observed, expected_scaled)
                                alpha = rule.alpha if rule.alpha is not None else 0.05
                                if p_value < alpha:
                                    violations.append(DQViolation(
                                        rule_type=rtype, column=col, severity=sev,
                                        message=f"Chi-square test rejected expected distribution for '{col}' (p={p_value:.4g} < {alpha})",
                                        actual_value=float(p_value),
                                    ))
                        except Exception:
                            pass

            elif rtype == "distribution_anderson_darling":
                if col and col in df.columns:
                    if scipy_stats is None:
                        logger.warning("distribution_anderson_darling skipped — scipy not installed")
                    else:
                        try:
                            numeric = pd.to_numeric(df[col], errors="coerce").dropna()
                            if len(numeric) > 1:
                                result = scipy_stats.anderson(numeric, dist="norm")
                                alpha = rule.alpha if rule.alpha is not None else 0.05
                                significance_map = {0.15: 0, 0.10: 1, 0.05: 2, 0.025: 3, 0.01: 4}
                                index = significance_map.get(alpha, 2)
                                critical = float(result.critical_values[index])
                                if float(result.statistic) > critical:
                                    violations.append(DQViolation(
                                        rule_type=rtype, column=col, severity=sev,
                                        message=f"Anderson-Darling test rejected normality for '{col}' ({result.statistic:.4g} > {critical:.4g})",
                                        actual_value=float(result.statistic),
                                    ))
                        except Exception:
                            pass

            elif rtype == "hypothesis_test_proportion":
                if col and col in df.columns and rule.expected_proportion is not None and rule.condition is not None:
                    if scipy_stats is None:
                        logger.warning("hypothesis_test_proportion skipped — scipy not installed")
                    else:
                        try:
                            series = df[col].dropna().astype(str)
                            if len(series) > 0:
                                observed = int((series == str(rule.condition)).sum())
                                total = len(series)
                                p0 = float(rule.expected_proportion)
                                se = ((p0 * (1 - p0)) / total) ** 0.5
                                if se > 0:
                                    z_stat = ((observed / total) - p0) / se
                                    p_value = float(2 * (1 - scipy_stats.norm.cdf(abs(z_stat))))
                                    alpha = rule.alpha if rule.alpha is not None else 0.05
                                    if p_value < alpha:
                                        violations.append(DQViolation(
                                            rule_type=rtype, column=col, severity=sev,
                                            message=f"Proportion test rejected expected ratio for '{col}' (p={p_value:.4g} < {alpha})",
                                            actual_value=p_value,
                                        ))
                        except Exception:
                            pass

            elif rtype == "anomaly_detection_sigma":
                if col and col in df.columns:
                    try:
                        numeric = pd.to_numeric(df[col], errors="coerce")
                        threshold = rule.threshold if rule.threshold is not None else 3.0
                        window = rule.window if rule.window is not None else 10
                        if len(numeric.dropna()) > 1 and window > 1:
                            baseline = numeric.shift(1)
                            rolling_mean = baseline.rolling(window=window, min_periods=2).mean()
                            rolling_std = baseline.rolling(window=window, min_periods=2).std()
                            zscores = ((numeric - rolling_mean) / rolling_std).abs()
                            bad = int((zscores > threshold).fillna(False).sum())
                            if bad:
                                violations.append(DQViolation(
                                    rule_type=rtype, column=col, severity=sev,
                                    message=f"{bad} value(s) in '{col}' exceed rolling sigma threshold {threshold}",
                                    actual_value=bad,
                                ))
                    except Exception:
                        pass

        return violations
