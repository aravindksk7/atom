from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class DQViolation:
    rule_type: str
    column: str | None
    message: str
    severity: str
    actual_value: Any = None


class DQEngine:
    def evaluate(self, df: pd.DataFrame, rules: list) -> list[DQViolation]:
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
                        pass  # Skip if numeric conversion fails

        return violations
