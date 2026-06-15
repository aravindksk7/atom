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

        return violations
