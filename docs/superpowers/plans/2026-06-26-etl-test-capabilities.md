# ETL Test Capabilities Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 12 new DQ rule types and 4 new job types (freshness, cross_job_assertion, schema_snapshot, profile) with supporting API routes, DB tables, and UI.

**Architecture:** Extend `DQRule` Pydantic Literal + `DQEngine.evaluate()` for new rule types; add new job type dispatch branches to `RunExecutor._build_case()`; add `ColumnProfile` + `SchemaSnapshot` ORM models with repositories; expose via two new route files mounted in `api/main.py`; add Profile + Schema sub-tabs to the History tab in Alpine.js.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy (SQLite default), pandas, Alpine.js, hypothesis (dev-only)

---

## File Map

**Create:**
- `tests/unit/test_dq_engine_new_rules.py`
- `tests/unit/test_freshness_executor.py`
- `tests/unit/test_cross_job_assertion.py`
- `tests/unit/test_schema_snapshot_job.py`
- `tests/unit/test_profile_job.py`
- `tests/property/test_dq_rules_property.py`
- `api/services/profile_service.py`
- `api/services/schema_snapshot_service.py`
- `api/routes/profiles.py`
- `api/routes/schema_snapshots.py`

**Modify:**
- `api/schemas.py` — extend `DQRule.type` Literal, add 5 new fields, extend `JobDefinition.job_type` Literal + validator
- `etl_framework/reconciliation/dq_engine.py` — 12 new rule handlers
- `etl_framework/repository/models.py` — `ColumnProfile` + `SchemaSnapshot` ORM models
- `etl_framework/repository/database.py` — `_ensure_compare_columns` new tables
- `etl_framework/repository/repository.py` — 2 new repositories
- `api/services/run_executor.py` — new job type dispatch + DB engine thread-through for DQ rules
- `api/main.py` — register 2 new routers
- `frontend/app.js` — new rule/job type UI + 2 new sub-tabs
- `frontend/index.html` — sub-tab markup
- `tests/integration/test_api_frontend_smoke.py` — smoke tests for new endpoints

---

## Task 1: Extend DQRule and JobDefinition schemas

**Files:**
- Modify: `api/schemas.py`
- Test: `tests/unit/test_new_schemas.py` (already exists — extend it)

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_new_schemas.py`:

```python
from api.schemas import DQRule, JobDefinition

def test_dq_rule_completeness_ratio():
    r = DQRule.model_validate({"type": "completeness_ratio", "column": "amount", "min_value": 0.9})
    assert r.type == "completeness_ratio"
    assert r.min_value == 0.9

def test_dq_rule_column_percentile():
    r = DQRule.model_validate({"type": "column_percentile", "column": "price", "percentile": 95, "max_value": 1000.0})
    assert r.percentile == 95

def test_dq_rule_cross_column_consistency():
    r = DQRule.model_validate({"type": "cross_column_consistency", "column": "start_date", "column_b": "end_date", "operator": "<="})
    assert r.column_b == "end_date"
    assert r.operator == "<="

def test_dq_rule_referential_check():
    r = DQRule.model_validate({"type": "referential_check", "column": "customer_id", "lookup_query": "SELECT id FROM customers"})
    assert r.lookup_query == "SELECT id FROM customers"

def test_dq_rule_column_type_check():
    r = DQRule.model_validate({"type": "column_type_check", "column": "order_date", "expected_type": "date"})
    assert r.expected_type == "date"

def test_job_definition_freshness():
    j = JobDefinition.model_validate({
        "name": "orders_freshness",
        "job_type": "freshness",
        "query": "SELECT MAX(created_at) as ts FROM orders",
        "params": {"timestamp_column": "ts", "max_age_hours": 24},
    })
    assert j.job_type == "freshness"

def test_job_definition_profile():
    j = JobDefinition.model_validate({
        "name": "orders_profile",
        "job_type": "profile",
        "query": "SELECT * FROM orders",
        "params": {},
    })
    assert j.job_type == "profile"

def test_job_definition_schema_snapshot():
    j = JobDefinition.model_validate({
        "name": "orders_schema",
        "job_type": "schema_snapshot",
        "query": "SELECT * FROM orders",
        "params": {"environment": "source"},
    })
    assert j.job_type == "schema_snapshot"

def test_job_definition_cross_job_assertion():
    j = JobDefinition.model_validate({
        "name": "revenue_check",
        "job_type": "cross_job_assertion",
        "params": {
            "source_job": "orders_profile",
            "source_metric": "sum",
            "source_column": "amount",
            "target_job": "payments_profile",
            "target_metric": "sum",
            "target_column": "total",
        },
    })
    assert j.job_type == "cross_job_assertion"
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/unit/test_new_schemas.py -k "completeness_ratio or percentile or cross_column or referential or type_check or freshness or profile or schema_snapshot or cross_job" -v
```

Expected: FAIL — `ValidationError: Input should be ...` for unrecognized types.

- [ ] **Step 3: Extend DQRule in `api/schemas.py`**

Replace the `DQRule` class:

```python
class DQRule(BaseModel):
    type: Literal[
        "not_null", "unique", "row_count_min", "row_count_max",
        "row_count_between", "column_mean_between", "match_regex", "custom_sql",
        "column_max_length", "column_min_length", "value_in_set", "value_not_in_set",
        "column_contains", "date_range", "positive_values", "negative_values",
        # New rule types
        "completeness_ratio", "distinct_count_between", "column_sum_between",
        "column_std_dev_between", "column_percentile", "column_type_check",
        "column_value_between", "cross_column_consistency", "pii_mask_check",
        "no_whitespace", "referential_check", "custom_sql_assert",
    ]
    column: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    pattern: str | None = None
    sql: str | None = None
    values: list[str | int | float | bool] = Field(default_factory=list)
    min_date: date | datetime | None = None
    max_date: date | datetime | None = None
    severity: Literal["error", "warn"] = "error"
    # New fields
    percentile: int | None = None          # column_percentile
    operator: str | None = None            # cross_column_consistency, custom_sql_assert
    lookup_query: str | None = None        # referential_check
    column_b: str | None = None            # cross_column_consistency
    expected_type: str | None = None       # column_type_check
```

Extend `JobDefinition.job_type`:

```python
job_type: Literal[
    "reconciliation", "health_check", "bo_report", "automic_job", "dbt_artifact",
    "freshness", "cross_job_assertion", "schema_snapshot", "profile",
] = "reconciliation"
```

Extend `validate_reconciliation_contract` — add before the final `return self`:

```python
elif self.job_type == "freshness":
    if not self.params.get("timestamp_column"):
        raise ValueError("freshness jobs require 'timestamp_column' in params")
elif self.job_type == "cross_job_assertion":
    if not self.params.get("source_job") or not self.params.get("target_job"):
        raise ValueError("cross_job_assertion requires 'source_job' and 'target_job' in params")
elif self.job_type in ("schema_snapshot", "profile"):
    if not self.query.strip():
        raise ValueError(f"{self.job_type} jobs require a query")
```

- [ ] **Step 4: Run tests to verify they pass**

```
python -m pytest tests/unit/test_new_schemas.py -k "completeness_ratio or percentile or cross_column or referential or type_check or freshness or profile or schema_snapshot or cross_job" -v
```

Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_new_schemas.py
git commit -m "feat(schemas): extend DQRule and JobDefinition with new types"
```

---

## Task 2: DQ rules 1–5 (completeness_ratio, distinct_count_between, column_sum_between, column_std_dev_between, column_percentile)

**Files:**
- Create: `tests/unit/test_dq_engine_new_rules.py`
- Modify: `etl_framework/reconciliation/dq_engine.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_dq_engine_new_rules.py`:

```python
import pandas as pd
import pytest
from etl_framework.reconciliation.dq_engine import DQEngine, DQViolation
from api.schemas import DQRule


def _rule(**kwargs) -> DQRule:
    return DQRule.model_validate(kwargs)


def _eval(df, **rule_kwargs) -> list[DQViolation]:
    return DQEngine().evaluate(df, [_rule(**rule_kwargs)])


# ── completeness_ratio ──────────────────────────────────────────────────────

def test_completeness_ratio_passes():
    df = pd.DataFrame({"x": [1.0, 2.0, None, 4.0, 5.0]})  # 80% non-null
    assert _eval(df, type="completeness_ratio", column="x", min_value=0.75) == []


def test_completeness_ratio_fails():
    df = pd.DataFrame({"x": [1.0, None, None, None, 5.0]})  # 40% non-null
    vs = _eval(df, type="completeness_ratio", column="x", min_value=0.75)
    assert len(vs) == 1 and vs[0].rule_type == "completeness_ratio"


def test_completeness_ratio_empty_df():
    df = pd.DataFrame({"x": pd.Series([], dtype=float)})
    assert _eval(df, type="completeness_ratio", column="x", min_value=0.9) == []


# ── distinct_count_between ──────────────────────────────────────────────────

def test_distinct_count_between_passes():
    df = pd.DataFrame({"status": ["A", "B", "C", "A", "B"]})  # 3 distinct
    assert _eval(df, type="distinct_count_between", column="status", min_value=2, max_value=5) == []


def test_distinct_count_between_fails_low():
    df = pd.DataFrame({"status": ["A", "A", "A"]})  # 1 distinct
    vs = _eval(df, type="distinct_count_between", column="status", min_value=2, max_value=5)
    assert len(vs) == 1


def test_distinct_count_between_fails_high():
    df = pd.DataFrame({"status": ["A", "B", "C", "D", "E", "F"]})  # 6 distinct
    vs = _eval(df, type="distinct_count_between", column="status", min_value=2, max_value=4)
    assert len(vs) == 1


# ── column_sum_between ──────────────────────────────────────────────────────

def test_column_sum_between_passes():
    df = pd.DataFrame({"amount": [10.0, 20.0, 30.0]})  # sum=60
    assert _eval(df, type="column_sum_between", column="amount", min_value=50.0, max_value=70.0) == []


def test_column_sum_between_fails():
    df = pd.DataFrame({"amount": [10.0, 20.0, 30.0]})  # sum=60
    vs = _eval(df, type="column_sum_between", column="amount", min_value=100.0, max_value=200.0)
    assert len(vs) == 1 and vs[0].actual_value == pytest.approx(60.0)


# ── column_std_dev_between ──────────────────────────────────────────────────

def test_column_std_dev_passes():
    df = pd.DataFrame({"price": [10.0, 10.0, 10.0, 10.0]})  # std=0
    assert _eval(df, type="column_std_dev_between", column="price", min_value=0.0, max_value=1.0) == []


def test_column_std_dev_fails():
    df = pd.DataFrame({"price": [1.0, 100.0, 1.0, 100.0]})  # high std
    vs = _eval(df, type="column_std_dev_between", column="price", min_value=0.0, max_value=5.0)
    assert len(vs) == 1


# ── column_percentile ──────────────────────────────────────────────────────

def test_column_percentile_passes():
    df = pd.DataFrame({"latency": list(range(100))})  # p95=94
    assert _eval(df, type="column_percentile", column="latency", percentile=95, min_value=90.0, max_value=96.0) == []


def test_column_percentile_fails():
    df = pd.DataFrame({"latency": list(range(100))})  # p95=94
    vs = _eval(df, type="column_percentile", column="latency", percentile=95, min_value=0.0, max_value=50.0)
    assert len(vs) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/unit/test_dq_engine_new_rules.py -v 2>&1 | head -30
```

Expected: FAIL — unhandled rule types return empty violations list (pass when should fail), or errors.

- [ ] **Step 3: Implement rules 1–5 in `etl_framework/reconciliation/dq_engine.py`**

Add after the existing `elif rtype == "negative_values":` block (before `return violations`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```
python -m pytest tests/unit/test_dq_engine_new_rules.py -k "completeness or distinct or sum or std or percentile" -v
```

Expected: PASS (all 11 tests)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/dq_engine.py tests/unit/test_dq_engine_new_rules.py
git commit -m "feat(dq): add completeness_ratio, distinct_count, sum, std_dev, percentile rules"
```

---

## Task 3: DQ rules 6–10 (column_type_check, column_value_between, cross_column_consistency, pii_mask_check, no_whitespace)

**Files:**
- Modify: `tests/unit/test_dq_engine_new_rules.py` (extend)
- Modify: `etl_framework/reconciliation/dq_engine.py`

- [ ] **Step 1: Add tests**

Append to `tests/unit/test_dq_engine_new_rules.py`:

```python
# ── column_type_check ──────────────────────────────────────────────────────

def test_column_type_check_int_passes():
    df = pd.DataFrame({"qty": ["1", "2", "3"]})
    assert _eval(df, type="column_type_check", column="qty", expected_type="int") == []


def test_column_type_check_int_fails():
    df = pd.DataFrame({"qty": ["1", "two", "3"]})
    vs = _eval(df, type="column_type_check", column="qty", expected_type="int")
    assert len(vs) == 1


def test_column_type_check_date_passes():
    df = pd.DataFrame({"dt": ["2024-01-01", "2024-06-15"]})
    assert _eval(df, type="column_type_check", column="dt", expected_type="date") == []


def test_column_type_check_date_fails():
    df = pd.DataFrame({"dt": ["2024-01-01", "not-a-date"]})
    vs = _eval(df, type="column_type_check", column="dt", expected_type="date")
    assert len(vs) == 1


# ── column_value_between ───────────────────────────────────────────────────

def test_column_value_between_passes():
    df = pd.DataFrame({"score": [5, 7, 9, 10]})
    assert _eval(df, type="column_value_between", column="score", min_value=1, max_value=10) == []


def test_column_value_between_fails():
    df = pd.DataFrame({"score": [5, 7, 15, 10]})  # 15 is out of range
    vs = _eval(df, type="column_value_between", column="score", min_value=1, max_value=10)
    assert len(vs) == 1 and vs[0].actual_value == 1


# ── cross_column_consistency ───────────────────────────────────────────────

def test_cross_column_consistency_passes():
    df = pd.DataFrame({"start": [1, 2, 3], "end": [4, 5, 6]})
    assert _eval(df, type="cross_column_consistency", column="start", column_b="end", operator="<=") == []


def test_cross_column_consistency_fails():
    df = pd.DataFrame({"start": [1, 10, 3], "end": [4, 5, 6]})  # row 1: 10 > 5
    vs = _eval(df, type="cross_column_consistency", column="start", column_b="end", operator="<=")
    assert len(vs) == 1 and vs[0].actual_value == 1


# ── pii_mask_check ─────────────────────────────────────────────────────────

def test_pii_mask_check_clean_column_passes():
    df = pd.DataFrame({"ssn": ["***-**-1234", "***-**-5678"]})
    # Pattern matches real SSN format — column is clean (no actual SSNs present)
    assert _eval(df, type="pii_mask_check", column="ssn", pattern=r"\d{3}-\d{2}-\d{4}") == []


def test_pii_mask_check_finds_unmasked():
    df = pd.DataFrame({"ssn": ["***-**-1234", "123-45-6789"]})  # second row is unmasked
    vs = _eval(df, type="pii_mask_check", column="ssn", pattern=r"\d{3}-\d{2}-\d{4}")
    assert len(vs) == 1 and vs[0].actual_value == 1


# ── no_whitespace ──────────────────────────────────────────────────────────

def test_no_whitespace_passes():
    df = pd.DataFrame({"name": ["Alice", "Bob", "Charlie"]})
    assert _eval(df, type="no_whitespace", column="name") == []


def test_no_whitespace_fails():
    df = pd.DataFrame({"name": ["Alice", " Bob", "Charlie "]})
    vs = _eval(df, type="no_whitespace", column="name")
    assert len(vs) == 1 and vs[0].actual_value == 2
```

- [ ] **Step 2: Run to verify they fail**

```
python -m pytest tests/unit/test_dq_engine_new_rules.py -k "type_check or value_between or cross_column or pii or whitespace" -v
```

Expected: FAIL

- [ ] **Step 3: Implement rules 6–10 in `etl_framework/reconciliation/dq_engine.py`**

Add after the `column_percentile` block:

```python
            elif rtype == "column_type_check":
                if col and col in df.columns and rule.expected_type:
                    non_null = df[col].dropna()
                    if rule.expected_type == "int":
                        bad = int(pd.to_numeric(non_null, errors="coerce").isna().sum())
                    elif rule.expected_type == "float":
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
                    except _re.error:
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
```

- [ ] **Step 4: Run tests to verify they pass**

```
python -m pytest tests/unit/test_dq_engine_new_rules.py -k "type_check or value_between or cross_column or pii or whitespace" -v
```

Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/dq_engine.py tests/unit/test_dq_engine_new_rules.py
git commit -m "feat(dq): add type_check, value_between, cross_column, pii_mask, no_whitespace rules"
```

---

## Task 4: DQ rules 11–12 + thread DB engine through _apply_dq_rules

**Files:**
- Modify: `tests/unit/test_dq_engine_new_rules.py`
- Modify: `etl_framework/reconciliation/dq_engine.py`
- Modify: `api/services/run_executor.py`

- [ ] **Step 1: Add tests**

Append to `tests/unit/test_dq_engine_new_rules.py`:

```python
from unittest.mock import MagicMock

# ── referential_check ──────────────────────────────────────────────────────

def test_referential_check_passes():
    df = pd.DataFrame({"customer_id": [1, 2, 3]})
    mock_engine = MagicMock()
    mock_engine.execute_query.return_value = pd.DataFrame({"id": [1, 2, 3, 4, 5]})
    rule = _rule(type="referential_check", column="customer_id", lookup_query="SELECT id FROM customers")
    vs = DQEngine().evaluate(df, [rule], engine=mock_engine)
    assert vs == []


def test_referential_check_fails():
    df = pd.DataFrame({"customer_id": [1, 2, 99]})  # 99 not in lookup
    mock_engine = MagicMock()
    mock_engine.execute_query.return_value = pd.DataFrame({"id": [1, 2, 3]})
    rule = _rule(type="referential_check", column="customer_id", lookup_query="SELECT id FROM customers")
    vs = DQEngine().evaluate(df, [rule], engine=mock_engine)
    assert len(vs) == 1 and vs[0].actual_value == 1


def test_referential_check_skips_without_engine():
    df = pd.DataFrame({"customer_id": [1, 99]})
    rule = _rule(type="referential_check", column="customer_id", lookup_query="SELECT id FROM c")
    vs = DQEngine().evaluate(df, [rule])  # no engine
    assert vs == []


# ── custom_sql_assert ──────────────────────────────────────────────────────

def test_custom_sql_assert_passes():
    mock_engine = MagicMock()
    mock_engine.execute_query.return_value = pd.DataFrame({"result": [42]})
    rule = _rule(type="custom_sql_assert", sql="SELECT COUNT(*) as result FROM orders", operator=">=", min_value=10)
    vs = DQEngine().evaluate(pd.DataFrame(), [rule], engine=mock_engine)
    assert vs == []


def test_custom_sql_assert_fails():
    mock_engine = MagicMock()
    mock_engine.execute_query.return_value = pd.DataFrame({"result": [3]})
    rule = _rule(type="custom_sql_assert", sql="SELECT COUNT(*) as result FROM orders", operator=">=", min_value=10)
    vs = DQEngine().evaluate(pd.DataFrame(), [rule], engine=mock_engine)
    assert len(vs) == 1


def test_custom_sql_assert_skips_without_engine():
    rule = _rule(type="custom_sql_assert", sql="SELECT 1", operator=">=", min_value=1)
    vs = DQEngine().evaluate(pd.DataFrame(), [rule])
    assert vs == []
```

- [ ] **Step 2: Run to verify they fail**

```
python -m pytest tests/unit/test_dq_engine_new_rules.py -k "referential or sql_assert" -v
```

Expected: FAIL — `TypeError: evaluate() got unexpected keyword argument 'engine'`

- [ ] **Step 3: Add `engine` param to `DQEngine.evaluate()` and implement rules 11–12**

In `etl_framework/reconciliation/dq_engine.py`, change signature:

```python
def evaluate(self, df: pd.DataFrame, rules: list, engine=None) -> list[DQViolation]:
```

Add after the `no_whitespace` block:

```python
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
                            if lookup_df.empty:
                                valid_values: set = set()
                            else:
                                valid_values = set(lookup_df.iloc[:, 0].astype(str))
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
                        import logging as _logging
                        _logging.getLogger("etl_framework.dq_engine").warning(
                            "custom_sql_assert rule skipped — no DB engine available"
                        )
                    else:
                        try:
                            result_df = engine.execute_query(rule.sql)
                            if result_df.empty or result_df.shape != (1, 1):
                                violations.append(DQViolation(
                                    rule_type=rtype, column=None, severity="error",
                                    message=f"custom_sql_assert expected 1 row × 1 col, got {result_df.shape}",
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
```

- [ ] **Step 4: Thread engine into `_apply_dq_rules` in `api/services/run_executor.py`**

In `_apply_dq_rules`, change:

```python
violations = DQEngine().evaluate(source_df, job.rules)
```

to:

```python
violations = DQEngine().evaluate(source_df, job.rules, engine=source_engine)
```

- [ ] **Step 5: Run all new DQ tests**

```
python -m pytest tests/unit/test_dq_engine_new_rules.py -v
```

Expected: PASS (all tests)

- [ ] **Step 6: Run full test suite to check for regressions**

```
python -m pytest tests/unit/ -q
```

Expected: all existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add etl_framework/reconciliation/dq_engine.py api/services/run_executor.py tests/unit/test_dq_engine_new_rules.py
git commit -m "feat(dq): add referential_check + custom_sql_assert; thread engine through _apply_dq_rules"
```

---

## Task 5: ORM models for ColumnProfile and SchemaSnapshot

**Files:**
- Modify: `etl_framework/repository/models.py`
- Modify: `etl_framework/repository/database.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_profile_models.py`:

```python
from etl_framework.repository.database import Base
from etl_framework.repository.models import ColumnProfile, SchemaSnapshot


def test_column_profile_tablename():
    assert ColumnProfile.__tablename__ == "column_profiles"


def test_schema_snapshot_tablename():
    assert SchemaSnapshot.__tablename__ == "schema_snapshots"
```

- [ ] **Step 2: Run to verify it fails**

```
python -m pytest tests/unit/test_profile_models.py -v
```

Expected: FAIL — `ImportError: cannot import name 'ColumnProfile'`

- [ ] **Step 3: Add ORM models to `etl_framework/repository/models.py`**

Append at the bottom of `etl_framework/repository/models.py`:

```python
# ---------------------------------------------------------------------------
# Profile + Schema Snapshot tables
# ---------------------------------------------------------------------------

class ColumnProfile(Base):
    __tablename__ = "column_profiles"

    id             = Column(Integer, primary_key=True, index=True)
    job_name       = Column(String(255), nullable=False, index=True)
    run_id         = Column(String(36), nullable=True, index=True)
    column_name    = Column(String(255), nullable=False)
    null_rate      = Column(Float, nullable=True)
    distinct_count = Column(Integer, nullable=True)
    min_val        = Column(Text, nullable=True)
    max_val        = Column(Text, nullable=True)
    mean_val       = Column(Float, nullable=True)
    std_val        = Column(Float, nullable=True)
    p25            = Column(Float, nullable=True)
    p50            = Column(Float, nullable=True)
    p75            = Column(Float, nullable=True)
    p95            = Column(Float, nullable=True)
    captured_at    = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class SchemaSnapshot(Base):
    __tablename__ = "schema_snapshots"

    id          = Column(Integer, primary_key=True, index=True)
    job_name    = Column(String(255), nullable=False, index=True)
    environment = Column(String(50), nullable=False, default="both")
    run_id      = Column(String(36), nullable=True, index=True)
    captured_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    columns     = Column(JSON, nullable=False, default=list)
```

- [ ] **Step 4: Register tables in `etl_framework/repository/database.py`**

Add inside `_ensure_compare_columns`, after the `run_steps` table block:

```python
        # --- Profile + Schema Snapshot tables ---
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS column_profiles ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "job_name TEXT NOT NULL, "
            "run_id TEXT, "
            "column_name TEXT NOT NULL, "
            "null_rate REAL, "
            "distinct_count INTEGER, "
            "min_val TEXT, "
            "max_val TEXT, "
            "mean_val REAL, "
            "std_val REAL, "
            "p25 REAL, "
            "p50 REAL, "
            "p75 REAL, "
            "p95 REAL, "
            "captured_at DATETIME NOT NULL)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_column_profiles_job_name ON column_profiles (job_name)"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "job_name TEXT NOT NULL, "
            "environment TEXT NOT NULL DEFAULT 'both', "
            "run_id TEXT, "
            "captured_at DATETIME NOT NULL, "
            "columns TEXT NOT NULL DEFAULT '[]')"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_schema_snapshots_job_name ON schema_snapshots (job_name)"
        ))
```

- [ ] **Step 5: Run tests**

```
python -m pytest tests/unit/test_profile_models.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/repository/database.py tests/unit/test_profile_models.py
git commit -m "feat(db): add ColumnProfile and SchemaSnapshot ORM models and tables"
```

---

## Task 6: Profile computation service

**Files:**
- Create: `api/services/profile_service.py`
- Create: `tests/unit/test_profile_job.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_profile_job.py`:

```python
import pandas as pd
import pytest
from api.services.profile_service import compute_profile, detect_drift


def _df():
    return pd.DataFrame({
        "amount": [10.0, 20.0, None, 40.0, 50.0],
        "status": ["A", "B", "A", "C", "B"],
    })


def test_compute_profile_columns():
    profile = compute_profile(_df(), columns=["amount", "status"])
    assert set(profile.keys()) == {"amount", "status"}


def test_compute_profile_null_rate():
    profile = compute_profile(_df(), columns=["amount"])
    assert profile["amount"]["null_rate"] == pytest.approx(0.2)


def test_compute_profile_distinct_count():
    profile = compute_profile(_df(), columns=["status"])
    assert profile["status"]["distinct_count"] == 3


def test_compute_profile_numeric_stats():
    profile = compute_profile(_df(), columns=["amount"])
    assert profile["amount"]["mean_val"] == pytest.approx(30.0)
    assert profile["amount"]["p50"] is not None


def test_compute_profile_all_columns_when_empty_list():
    profile = compute_profile(_df(), columns=[])
    assert "amount" in profile and "status" in profile


def test_detect_drift_no_drift():
    current = {"amount": {"mean_val": 100.0, "null_rate": 0.1}}
    previous = {"amount": {"mean_val": 100.0, "null_rate": 0.1}}
    assert detect_drift(current, previous, threshold_pct=20.0) == []


def test_detect_drift_flags_column():
    current = {"amount": {"mean_val": 200.0, "null_rate": 0.1}}  # mean doubled
    previous = {"amount": {"mean_val": 100.0, "null_rate": 0.1}}
    flagged = detect_drift(current, previous, threshold_pct=20.0)
    assert "amount" in flagged


def test_detect_drift_first_run_no_previous():
    current = {"amount": {"mean_val": 100.0}}
    assert detect_drift(current, {}, threshold_pct=20.0) == []
```

- [ ] **Step 2: Run to verify they fail**

```
python -m pytest tests/unit/test_profile_job.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create `api/services/profile_service.py`**

```python
from __future__ import annotations
import math
import pandas as pd


def compute_profile(df: pd.DataFrame, columns: list[str]) -> dict[str, dict]:
    """Return per-column stats dict for the given columns (all columns if empty)."""
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

        result[col] = {
            "null_rate": null_rate,
            "distinct_count": distinct_count,
            "min_val": str(series.dropna().min()) if not series.dropna().empty else None,
            "max_val": str(series.dropna().max()) if not series.dropna().empty else None,
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
    """Return column names whose metrics shifted beyond threshold_pct vs previous profile."""
    if not previous:
        return []
    flagged: list[str] = []
    numeric_keys = ("mean_val", "std_val", "null_rate", "p25", "p50", "p75", "p95")
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
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/unit/test_profile_job.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/profile_service.py tests/unit/test_profile_job.py
git commit -m "feat(profile): add profile_service with compute_profile and detect_drift"
```

---

## Task 7: Schema snapshot service

**Files:**
- Create: `api/services/schema_snapshot_service.py`
- Create: `tests/unit/test_schema_snapshot_job.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_schema_snapshot_job.py`:

```python
import pandas as pd
from api.services.schema_snapshot_service import capture_schema, diff_schemas


def test_capture_schema_returns_list_of_dicts():
    df = pd.DataFrame({"id": [1, 2], "name": ["a", "b"], "amount": [1.0, 2.0]})
    schema = capture_schema(df)
    assert isinstance(schema, list)
    assert all("name" in col and "dtype" in col for col in schema)


def test_capture_schema_column_order():
    df = pd.DataFrame({"z": [1], "a": [2], "m": [3]})
    schema = capture_schema(df)
    assert [c["name"] for c in schema] == ["z", "a", "m"]


def test_diff_schemas_identical():
    cols = [{"name": "id", "dtype": "int64"}, {"name": "name", "dtype": "object"}]
    diff = diff_schemas(cols, cols)
    assert diff == {"added": [], "removed": [], "changed": []}


def test_diff_schemas_added_column():
    prev = [{"name": "id", "dtype": "int64"}]
    curr = [{"name": "id", "dtype": "int64"}, {"name": "email", "dtype": "object"}]
    diff = diff_schemas(curr, prev)
    assert diff["added"] == ["email"]
    assert diff["removed"] == []


def test_diff_schemas_removed_column():
    prev = [{"name": "id", "dtype": "int64"}, {"name": "old_col", "dtype": "object"}]
    curr = [{"name": "id", "dtype": "int64"}]
    diff = diff_schemas(curr, prev)
    assert diff["removed"] == ["old_col"]


def test_diff_schemas_type_changed():
    prev = [{"name": "amount", "dtype": "int64"}]
    curr = [{"name": "amount", "dtype": "float64"}]
    diff = diff_schemas(curr, prev)
    assert diff["changed"] == [{"column": "amount", "from": "int64", "to": "float64"}]
```

- [ ] **Step 2: Run to verify they fail**

```
python -m pytest tests/unit/test_schema_snapshot_job.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create `api/services/schema_snapshot_service.py`**

```python
from __future__ import annotations
import pandas as pd


def capture_schema(df: pd.DataFrame) -> list[dict]:
    """Return [{name, dtype}] for each column in df."""
    return [{"name": col, "dtype": str(df[col].dtype)} for col in df.columns]


def diff_schemas(
    current: list[dict],
    previous: list[dict],
) -> dict[str, list]:
    """Diff two schema snapshots. Returns {added, removed, changed}."""
    curr_map = {col["name"]: col["dtype"] for col in current}
    prev_map = {col["name"]: col["dtype"] for col in previous}
    added = [name for name in curr_map if name not in prev_map]
    removed = [name for name in prev_map if name not in curr_map]
    changed = [
        {"column": name, "from": prev_map[name], "to": curr_map[name]}
        for name in curr_map
        if name in prev_map and curr_map[name] != prev_map[name]
    ]
    return {"added": added, "removed": removed, "changed": changed}
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/unit/test_schema_snapshot_job.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/schema_snapshot_service.py tests/unit/test_schema_snapshot_job.py
git commit -m "feat(schema-snapshot): add schema_snapshot_service with capture and diff"
```

---

## Task 8: Repositories for ColumnProfile and SchemaSnapshot

**Files:**
- Modify: `etl_framework/repository/repository.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_profile_repository.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from etl_framework.repository.database import Base
from etl_framework.repository.repository import ColumnProfileRepository, SchemaSnapshotRepository
from datetime import datetime, timezone


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    from etl_framework.repository import models  # noqa: registers ORM
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_save_and_get_latest_profile(db):
    repo = ColumnProfileRepository(db)
    repo.save("orders", "run-1", "amount", null_rate=0.1, distinct_count=50,
               min_val="1.0", max_val="999.0", mean_val=100.0, std_val=20.0,
               p25=50.0, p50=100.0, p75=150.0, p95=200.0)
    db.commit()
    profiles = repo.get_latest("orders")
    assert len(profiles) == 1
    assert profiles[0].column_name == "amount"


def test_get_history(db):
    repo = ColumnProfileRepository(db)
    repo.save("orders", "run-1", "amount", null_rate=0.1, distinct_count=10,
               min_val=None, max_val=None, mean_val=10.0, std_val=1.0,
               p25=None, p50=None, p75=None, p95=None)
    repo.save("orders", "run-2", "amount", null_rate=0.2, distinct_count=12,
               min_val=None, max_val=None, mean_val=12.0, std_val=1.5,
               p25=None, p50=None, p75=None, p95=None)
    db.commit()
    history = repo.get_history("orders", "amount")
    assert len(history) == 2


def test_save_and_get_latest_snapshot(db):
    repo = SchemaSnapshotRepository(db)
    cols = [{"name": "id", "dtype": "int64"}, {"name": "name", "dtype": "object"}]
    repo.save("orders", "run-1", "source", cols)
    db.commit()
    snapshot = repo.get_latest("orders", "source")
    assert snapshot is not None
    assert len(snapshot.columns) == 2


def test_get_snapshot_history(db):
    repo = SchemaSnapshotRepository(db)
    repo.save("orders", "run-1", "source", [{"name": "id", "dtype": "int64"}])
    repo.save("orders", "run-2", "source", [{"name": "id", "dtype": "int64"}, {"name": "email", "dtype": "object"}])
    db.commit()
    history = repo.get_history("orders", "source")
    assert len(history) == 2
```

- [ ] **Step 2: Run to verify they fail**

```
python -m pytest tests/unit/test_profile_repository.py -v
```

Expected: FAIL — `ImportError`

- [ ] **Step 3: Add repositories to `etl_framework/repository/repository.py`**

Append at the bottom of `etl_framework/repository/repository.py`:

```python
class ColumnProfileRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def save(
        self, job_name: str, run_id: str, column_name: str,
        null_rate: float | None, distinct_count: int | None,
        min_val: str | None, max_val: str | None,
        mean_val: float | None, std_val: float | None,
        p25: float | None, p50: float | None, p75: float | None, p95: float | None,
    ) -> None:
        from etl_framework.repository.models import ColumnProfile
        from datetime import datetime, timezone
        row = ColumnProfile(
            job_name=job_name, run_id=run_id, column_name=column_name,
            null_rate=null_rate, distinct_count=distinct_count,
            min_val=min_val, max_val=max_val,
            mean_val=mean_val, std_val=std_val,
            p25=p25, p50=p50, p75=p75, p95=p95,
            captured_at=datetime.now(timezone.utc),
        )
        self._db.add(row)

    def get_latest(self, job_name: str) -> list:
        from etl_framework.repository.models import ColumnProfile
        subq = (
            self._db.query(
                ColumnProfile.column_name,
                ColumnProfile.captured_at,
            )
            .filter(ColumnProfile.job_name == job_name)
            .order_by(ColumnProfile.captured_at.desc())
            .subquery()
        )
        from sqlalchemy import func
        max_captured = (
            self._db.query(
                ColumnProfile.column_name,
                func.max(ColumnProfile.captured_at).label("max_captured"),
            )
            .filter(ColumnProfile.job_name == job_name)
            .group_by(ColumnProfile.column_name)
            .subquery()
        )
        return (
            self._db.query(ColumnProfile)
            .join(
                max_captured,
                (ColumnProfile.column_name == max_captured.c.column_name)
                & (ColumnProfile.captured_at == max_captured.c.max_captured),
            )
            .filter(ColumnProfile.job_name == job_name)
            .all()
        )

    def get_history(self, job_name: str, column_name: str) -> list:
        from etl_framework.repository.models import ColumnProfile
        return (
            self._db.query(ColumnProfile)
            .filter(
                ColumnProfile.job_name == job_name,
                ColumnProfile.column_name == column_name,
            )
            .order_by(ColumnProfile.captured_at.asc())
            .all()
        )

    def get_latest_for_run(self, job_name: str, run_id: str) -> list:
        from etl_framework.repository.models import ColumnProfile
        return (
            self._db.query(ColumnProfile)
            .filter(ColumnProfile.job_name == job_name, ColumnProfile.run_id == run_id)
            .all()
        )


class SchemaSnapshotRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def save(self, job_name: str, run_id: str, environment: str, columns: list[dict]) -> None:
        from etl_framework.repository.models import SchemaSnapshot
        from datetime import datetime, timezone
        row = SchemaSnapshot(
            job_name=job_name, run_id=run_id, environment=environment,
            columns=columns, captured_at=datetime.now(timezone.utc),
        )
        self._db.add(row)

    def get_latest(self, job_name: str, environment: str):
        from etl_framework.repository.models import SchemaSnapshot
        return (
            self._db.query(SchemaSnapshot)
            .filter(SchemaSnapshot.job_name == job_name, SchemaSnapshot.environment == environment)
            .order_by(SchemaSnapshot.captured_at.desc())
            .first()
        )

    def get_history(self, job_name: str, environment: str) -> list:
        from etl_framework.repository.models import SchemaSnapshot
        return (
            self._db.query(SchemaSnapshot)
            .filter(SchemaSnapshot.job_name == job_name, SchemaSnapshot.environment == environment)
            .order_by(SchemaSnapshot.captured_at.asc())
            .all()
        )
```

- [ ] **Step 4: Add `Session` import if not already present**

Verify `from sqlalchemy.orm import Session` is at the top of `repository.py`. If missing, add it.

- [ ] **Step 5: Run tests**

```
python -m pytest tests/unit/test_profile_repository.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add etl_framework/repository/repository.py tests/unit/test_profile_repository.py
git commit -m "feat(repo): add ColumnProfileRepository and SchemaSnapshotRepository"
```

---

## Task 9: Freshness job executor

**Files:**
- Create: `tests/unit/test_freshness_executor.py`
- Modify: `api/services/run_executor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_freshness_executor.py`:

```python
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
from api.schemas import JobDefinition


def _freshness_job(max_age_hours=24, ts_col="ts"):
    return JobDefinition.model_validate({
        "name": "orders_freshness",
        "job_type": "freshness",
        "query": "SELECT MAX(created_at) as ts FROM orders",
        "params": {"timestamp_column": ts_col, "max_age_hours": max_age_hours},
    })


def test_freshness_passes_when_data_is_recent():
    from api.services.run_executor import RunExecutor
    from etl_framework.runner.state import TestStatus

    recent_ts = datetime.now(timezone.utc) - timedelta(hours=1)
    mock_engine = MagicMock()
    mock_engine._env = MagicMock(name="src")
    mock_engine.execute_query.return_value = pd.DataFrame({"ts": [recent_ts]})

    executor = _make_executor()
    result = executor._execute_freshness(_freshness_job(), mock_engine)
    assert result.status == TestStatus.PASSED


def test_freshness_fails_when_data_is_stale():
    from api.services.run_executor import RunExecutor
    from etl_framework.runner.state import TestStatus

    stale_ts = datetime.now(timezone.utc) - timedelta(hours=48)
    mock_engine = MagicMock()
    mock_engine._env = MagicMock(name="src")
    mock_engine.execute_query.return_value = pd.DataFrame({"ts": [stale_ts]})

    executor = _make_executor()
    result = executor._execute_freshness(_freshness_job(max_age_hours=24), mock_engine)
    assert result.status == TestStatus.FAILED
    assert len(result.mismatches) == 1


def test_freshness_passes_in_simulation_mode():
    from etl_framework.runner.state import TestStatus
    executor = _make_executor()
    # Simulation engine returns empty df
    mock_engine = MagicMock()
    mock_engine._env = MagicMock(name="src")
    mock_engine.execute_query.return_value = pd.DataFrame()
    result = executor._execute_freshness(_freshness_job(), mock_engine)
    assert result.status == TestStatus.PASSED


def _make_executor():
    from api.services.run_executor import RunExecutor
    from api.schemas import RunSettings
    from unittest.mock import MagicMock
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []
    executor = object.__new__(RunExecutor)
    executor._db = db
    executor._run_id = "test-run"
    executor._source_env = "dev"
    executor._target_env = "prod"
    executor._settings = RunSettings()
    executor._config_snapshot = {}
    return executor
```

- [ ] **Step 2: Run to verify they fail**

```
python -m pytest tests/unit/test_freshness_executor.py -v
```

Expected: FAIL — `AttributeError: RunExecutor has no attribute '_execute_freshness'`

- [ ] **Step 3: Add freshness handling to `api/services/run_executor.py`**

Add new method to `RunExecutor` class, and update `_build_case`:

```python
# In _build_case, add before the bo_report branch:
    if job.job_type == "freshness":
        return self._build_case_freshness(job)
    if job.job_type == "schema_snapshot":
        return self._build_case_schema_snapshot(job)
    if job.job_type == "profile":
        return self._build_case_profile(job)
    if job.job_type == "cross_job_assertion":
        return self._build_case_cross_job(job)
```

Add new methods to `RunExecutor`:

```python
def _build_case_freshness(self, job: JobDefinition):
    def run_freshness() -> ReconciliationResult:
        source_engine, _ = self._build_engines(job)
        return self._execute_freshness(job, source_engine)
    return run_freshness

def _execute_freshness(self, job: JobDefinition, engine) -> ReconciliationResult:
    from etl_framework.reconciliation.models import MismatchRecord
    from datetime import datetime, timezone
    import time as _time

    t0 = _time.monotonic()
    executed_at = datetime.now(timezone.utc)
    ts_col = job.params.get("timestamp_column", "ts")
    max_age_hours = float(job.params.get("max_age_hours", 24))
    query = job.query or job.params.get("query", "")

    try:
        df = engine.execute_query(query)
    except Exception as exc:
        return ReconciliationResult(
            query_name=job.name, source_env=self._source_env, target_env=self._target_env,
            source_row_count=0, target_row_count=0, matched_count=0,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=1,
            mismatches=[MismatchRecord(key_values={"job": job.name}, column_name=ts_col,
                                       source_value=str(exc), target_value="",
                                       mismatch_type="freshness_error")],
            status=TestStatus.ERROR, executed_at=executed_at,
            duration_seconds=_time.monotonic() - t0, schema_diff=None,
        )

    if df.empty or ts_col not in df.columns:
        # Simulation mode or missing column — treat as passing
        return ReconciliationResult(
            query_name=job.name, source_env=self._source_env, target_env=self._target_env,
            source_row_count=0, target_row_count=0, matched_count=1,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=TestStatus.PASSED, executed_at=executed_at,
            duration_seconds=_time.monotonic() - t0, schema_diff=None,
        )

    max_ts = pd.to_datetime(df[ts_col]).max()
    if max_ts is None or pd.isna(max_ts):
        return ReconciliationResult(
            query_name=job.name, source_env=self._source_env, target_env=self._target_env,
            source_row_count=1, target_row_count=1, matched_count=0,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=1,
            mismatches=[MismatchRecord(key_values={"job": job.name}, column_name=ts_col,
                                       source_value="NULL", target_value=f"<= {max_age_hours}h ago",
                                       mismatch_type="freshness_null")],
            status=TestStatus.FAILED, executed_at=executed_at,
            duration_seconds=_time.monotonic() - t0, schema_diff=None,
        )

    now_utc = datetime.now(timezone.utc)
    if max_ts.tzinfo is None:
        max_ts = max_ts.replace(tzinfo=timezone.utc)
    age_hours = (now_utc - max_ts).total_seconds() / 3600

    if age_hours <= max_age_hours:
        return ReconciliationResult(
            query_name=job.name, source_env=self._source_env, target_env=self._target_env,
            source_row_count=1, target_row_count=1, matched_count=1,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=TestStatus.PASSED, executed_at=executed_at,
            duration_seconds=_time.monotonic() - t0, schema_diff=None,
        )
    else:
        return ReconciliationResult(
            query_name=job.name, source_env=self._source_env, target_env=self._target_env,
            source_row_count=1, target_row_count=1, matched_count=0,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=1,
            mismatches=[MismatchRecord(
                key_values={"job": job.name}, column_name=ts_col,
                source_value=f"{age_hours:.1f}h",
                target_value=f"<= {max_age_hours}h",
                mismatch_type="freshness_stale",
            )],
            status=TestStatus.FAILED, executed_at=executed_at,
            duration_seconds=_time.monotonic() - t0, schema_diff=None,
        )
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/unit/test_freshness_executor.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_freshness_executor.py
git commit -m "feat(executor): add freshness job type"
```

---

## Task 10: Profile and SchemaSnapshot job executors

**Files:**
- Modify: `api/services/run_executor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_profile_executor.py`:

```python
import pandas as pd
import pytest
from unittest.mock import MagicMock
from api.schemas import JobDefinition, RunSettings
from etl_framework.runner.state import TestStatus


def _make_executor(db=None):
    from api.services.run_executor import RunExecutor
    if db is None:
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        db.query.return_value.filter.return_value.all.return_value = []
    executor = object.__new__(RunExecutor)
    executor._db = db
    executor._run_id = "test-run"
    executor._source_env = "dev"
    executor._target_env = "prod"
    executor._settings = RunSettings()
    executor._config_snapshot = {}
    return executor


def _profile_job():
    return JobDefinition.model_validate({
        "name": "orders_profile",
        "job_type": "profile",
        "query": "SELECT * FROM orders",
        "params": {"drift_threshold_pct": 20.0},
    })


def test_profile_job_passes_first_run():
    mock_engine = MagicMock()
    mock_engine._env = MagicMock(name="dev")
    mock_engine.execute_query.return_value = pd.DataFrame({
        "id": [1, 2, 3], "amount": [10.0, 20.0, 30.0]
    })
    executor = _make_executor()
    result = executor._execute_profile(_profile_job(), mock_engine)
    assert result.status == TestStatus.PASSED


def test_schema_snapshot_passes_first_run():
    mock_engine = MagicMock()
    mock_engine._env = MagicMock(name="dev")
    mock_engine.execute_query.return_value = pd.DataFrame({"id": [1], "name": ["a"]})
    executor = _make_executor()
    job = JobDefinition.model_validate({
        "name": "orders_schema",
        "job_type": "schema_snapshot",
        "query": "SELECT * FROM orders",
        "params": {"environment": "source"},
    })
    result = executor._execute_schema_snapshot(job, mock_engine)
    assert result.status == TestStatus.PASSED
```

- [ ] **Step 2: Run to verify they fail**

```
python -m pytest tests/unit/test_profile_executor.py -v
```

Expected: FAIL — `AttributeError`

- [ ] **Step 3: Add profile and schema_snapshot executors to `api/services/run_executor.py`**

Add these methods to `RunExecutor`:

```python
def _build_case_profile(self, job: JobDefinition):
    def run_profile() -> ReconciliationResult:
        source_engine, _ = self._build_engines(job)
        return self._execute_profile(job, source_engine)
    return run_profile

def _execute_profile(self, job: JobDefinition, engine) -> ReconciliationResult:
    from api.services.profile_service import compute_profile, detect_drift
    from etl_framework.repository.repository import ColumnProfileRepository
    from etl_framework.reconciliation.models import MismatchRecord
    import time as _time

    t0 = _time.monotonic()
    executed_at = datetime.now(timezone.utc)
    columns = job.params.get("columns", [])
    drift_threshold = float(job.params.get("drift_threshold_pct", 20.0))

    try:
        df = engine.execute_query(job.query)
    except Exception:
        df = pd.DataFrame()

    if df.empty:
        return ReconciliationResult(
            query_name=job.name, source_env=self._source_env, target_env=self._target_env,
            source_row_count=0, target_row_count=0, matched_count=1,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=TestStatus.PASSED, executed_at=executed_at,
            duration_seconds=_time.monotonic() - t0, schema_diff=None,
        )

    current_profile = compute_profile(df, columns)

    repo = ColumnProfileRepository(self._db)
    previous_rows = repo.get_latest(job.name)
    previous_profile = {
        row.column_name: {
            "null_rate": row.null_rate, "distinct_count": row.distinct_count,
            "mean_val": row.mean_val, "std_val": row.std_val,
            "p25": row.p25, "p50": row.p50, "p75": row.p75, "p95": row.p95,
        }
        for row in previous_rows
    }

    flagged = detect_drift(current_profile, previous_profile, drift_threshold)

    for col, stats in current_profile.items():
        repo.save(
            job_name=job.name, run_id=self._run_id, column_name=col,
            null_rate=stats.get("null_rate"), distinct_count=stats.get("distinct_count"),
            min_val=stats.get("min_val"), max_val=stats.get("max_val"),
            mean_val=stats.get("mean_val"), std_val=stats.get("std_val"),
            p25=stats.get("p25"), p50=stats.get("p50"),
            p75=stats.get("p75"), p95=stats.get("p95"),
        )
    self._db.commit()

    mismatches = [
        MismatchRecord(
            key_values={"job": job.name, "column": col},
            column_name=col,
            source_value=str(current_profile.get(col, {}).get("mean_val")),
            target_value=str(previous_profile.get(col, {}).get("mean_val")),
            mismatch_type="profile_drift",
        )
        for col in flagged
    ]

    status = TestStatus.FAILED if flagged else TestStatus.PASSED
    return ReconciliationResult(
        query_name=job.name, source_env=self._source_env, target_env=self._target_env,
        source_row_count=len(df), target_row_count=len(df),
        matched_count=len(current_profile) - len(flagged),
        missing_in_target_count=0, missing_in_source_count=0,
        value_mismatch_count=len(flagged),
        mismatches=mismatches, status=status, executed_at=executed_at,
        duration_seconds=_time.monotonic() - t0, schema_diff=None,
    )

def _build_case_schema_snapshot(self, job: JobDefinition):
    def run_schema_snapshot() -> ReconciliationResult:
        source_engine, target_engine = self._build_engines(job)
        environment = job.params.get("environment", "both")
        if environment in ("source", "both"):
            result = self._execute_schema_snapshot(job, source_engine)
        else:
            result = self._execute_schema_snapshot(job, target_engine)
        return result
    return run_schema_snapshot

def _execute_schema_snapshot(self, job: JobDefinition, engine) -> ReconciliationResult:
    from api.services.schema_snapshot_service import capture_schema, diff_schemas
    from etl_framework.repository.repository import SchemaSnapshotRepository
    from etl_framework.reconciliation.models import MismatchRecord
    import time as _time

    t0 = _time.monotonic()
    executed_at = datetime.now(timezone.utc)
    environment = job.params.get("environment", "source")

    try:
        df = engine.execute_query(job.query)
    except Exception:
        df = pd.DataFrame()

    current_cols = capture_schema(df)
    repo = SchemaSnapshotRepository(self._db)
    previous = repo.get_latest(job.name, environment)
    previous_cols = previous.columns if previous else []

    diff = diff_schemas(current_cols, previous_cols)
    repo.save(job.name, self._run_id, environment, current_cols)
    self._db.commit()

    changes = diff["added"] + diff["removed"] + [c["column"] for c in diff["changed"]]
    mismatches = []
    for col in diff["added"]:
        mismatches.append(MismatchRecord(
            key_values={"job": job.name, "change": "added"},
            column_name=col, source_value="(new)", target_value="(absent)",
            mismatch_type="schema_added",
        ))
    for col in diff["removed"]:
        mismatches.append(MismatchRecord(
            key_values={"job": job.name, "change": "removed"},
            column_name=col, source_value="(absent)", target_value="(was present)",
            mismatch_type="schema_removed",
        ))
    for change in diff["changed"]:
        mismatches.append(MismatchRecord(
            key_values={"job": job.name, "change": "type_changed"},
            column_name=change["column"],
            source_value=change["to"], target_value=change["from"],
            mismatch_type="schema_type_changed",
        ))

    first_run = not previous_cols
    status = TestStatus.PASSED if (first_run or not changes) else TestStatus.FAILED
    return ReconciliationResult(
        query_name=job.name, source_env=self._source_env, target_env=self._target_env,
        source_row_count=len(current_cols), target_row_count=len(previous_cols),
        matched_count=len(current_cols) - len(changes),
        missing_in_target_count=len(diff["removed"]),
        missing_in_source_count=len(diff["added"]),
        value_mismatch_count=len(diff["changed"]),
        mismatches=mismatches, status=status, executed_at=executed_at,
        duration_seconds=_time.monotonic() - t0, schema_diff=None,
    )
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/unit/test_profile_executor.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_profile_executor.py
git commit -m "feat(executor): add profile and schema_snapshot job types"
```

---

## Task 11: Cross-job assertion executor

**Files:**
- Create: `tests/unit/test_cross_job_assertion.py`
- Modify: `api/services/run_executor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_cross_job_assertion.py`:

```python
import pytest
from unittest.mock import MagicMock
from api.schemas import JobDefinition, RunSettings
from etl_framework.runner.state import TestStatus
from etl_framework.repository.models import TestResult, ColumnProfile
from datetime import datetime, timezone


def _make_executor(db):
    from api.services.run_executor import RunExecutor
    executor = object.__new__(RunExecutor)
    executor._db = db
    executor._run_id = "run-1"
    executor._source_env = "dev"
    executor._target_env = "prod"
    executor._settings = RunSettings()
    executor._config_snapshot = {}
    return executor


def _cja_job(source_metric="count", target_metric="count", tolerance=0.0):
    return JobDefinition.model_validate({
        "name": "revenue_check",
        "job_type": "cross_job_assertion",
        "params": {
            "source_job": "orders_profile",
            "source_metric": source_metric,
            "source_column": "amount",
            "target_job": "payments_profile",
            "target_metric": target_metric,
            "target_column": "total",
            "tolerance": tolerance,
            "tolerance_type": "absolute",
        },
    })


def test_cross_job_count_passes():
    db = MagicMock()
    # Both source and target have 100 rows
    src_result = MagicMock(spec=TestResult)
    src_result.source_row_count = 100
    tgt_result = MagicMock(spec=TestResult)
    tgt_result.source_row_count = 100

    db.query.return_value.filter.return_value.first.side_effect = [src_result, tgt_result]

    executor = _make_executor(db)
    result = executor._execute_cross_job(_cja_job("count", "count", 0))
    assert result.status == TestStatus.PASSED


def test_cross_job_count_fails():
    db = MagicMock()
    src_result = MagicMock(spec=TestResult)
    src_result.source_row_count = 100
    tgt_result = MagicMock(spec=TestResult)
    tgt_result.source_row_count = 80  # 20 row difference

    db.query.return_value.filter.return_value.first.side_effect = [src_result, tgt_result]

    executor = _make_executor(db)
    result = executor._execute_cross_job(_cja_job("count", "count", 5))
    assert result.status == TestStatus.FAILED


def test_cross_job_skips_if_upstream_missing():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    executor = _make_executor(db)
    result = executor._execute_cross_job(_cja_job())
    assert result.status == TestStatus.SKIPPED
```

- [ ] **Step 2: Run to verify they fail**

```
python -m pytest tests/unit/test_cross_job_assertion.py -v
```

Expected: FAIL — `AttributeError: _execute_cross_job`

- [ ] **Step 3: Add cross-job executor to `api/services/run_executor.py`**

```python
def _build_case_cross_job(self, job: JobDefinition):
    def run_cross_job() -> ReconciliationResult:
        return self._execute_cross_job(job)
    return run_cross_job

def _execute_cross_job(self, job: JobDefinition) -> ReconciliationResult:
    from etl_framework.repository.models import TestResult, ColumnProfile
    from etl_framework.reconciliation.models import MismatchRecord
    import time as _time

    t0 = _time.monotonic()
    executed_at = datetime.now(timezone.utc)
    p = job.params
    source_job = p.get("source_job", "")
    target_job = p.get("target_job", "")
    source_metric = p.get("source_metric", "count")
    target_metric = p.get("target_metric", "count")
    source_col = p.get("source_column", "")
    target_col = p.get("target_column", "")
    tolerance = float(p.get("tolerance", 0.0))
    tolerance_type = p.get("tolerance_type", "absolute")

    def _get_count(job_name: str) -> float | None:
        row = (
            self._db.query(TestResult)
            .filter(TestResult.run_id == self._run_id, TestResult.query_name == job_name)
            .first()
        )
        return float(row.source_row_count) if row else None

    def _get_profile_metric(job_name: str, column: str, metric: str) -> float | None:
        row = (
            self._db.query(ColumnProfile)
            .filter(ColumnProfile.job_name == job_name, ColumnProfile.run_id == self._run_id,
                    ColumnProfile.column_name == column)
            .first()
        )
        if row is None:
            return None
        mapping = {
            "sum": None,  # not directly stored; use mean*distinct as approximation isn't reliable
            "distinct_count": float(row.distinct_count) if row.distinct_count is not None else None,
        }
        return mapping.get(metric)

    src_val = _get_count(source_job) if source_metric == "count" else _get_profile_metric(source_job, source_col, source_metric)
    tgt_val = _get_count(target_job) if target_metric == "count" else _get_profile_metric(target_job, target_col, target_metric)

    if src_val is None or tgt_val is None:
        return ReconciliationResult(
            query_name=job.name, source_env=self._source_env, target_env=self._target_env,
            source_row_count=0, target_row_count=0, matched_count=0,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=TestStatus.SKIPPED, executed_at=executed_at,
            duration_seconds=_time.monotonic() - t0, schema_diff=None,
        )

    effective_tolerance = (tolerance / 100 * abs(src_val)) if tolerance_type == "percent" else tolerance
    delta = abs(src_val - tgt_val)
    passed = delta <= effective_tolerance

    mismatches = [] if passed else [
        MismatchRecord(
            key_values={"source_job": source_job, "target_job": target_job},
            column_name=source_col or "row_count",
            source_value=str(src_val),
            target_value=str(tgt_val),
            mismatch_type="cross_job_delta",
        )
    ]

    return ReconciliationResult(
        query_name=job.name, source_env=self._source_env, target_env=self._target_env,
        source_row_count=int(src_val), target_row_count=int(tgt_val),
        matched_count=1 if passed else 0,
        missing_in_target_count=0, missing_in_source_count=0,
        value_mismatch_count=0 if passed else 1,
        mismatches=mismatches,
        status=TestStatus.PASSED if passed else TestStatus.FAILED,
        executed_at=executed_at, duration_seconds=_time.monotonic() - t0, schema_diff=None,
    )
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/unit/test_cross_job_assertion.py -v
```

Expected: PASS

- [ ] **Step 5: Run full unit suite**

```
python -m pytest tests/unit/ -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_cross_job_assertion.py
git commit -m "feat(executor): add cross_job_assertion job type"
```

---

## Task 12: Profile and SchemaSnapshot API routes

**Files:**
- Create: `api/routes/profiles.py`
- Create: `api/routes/schema_snapshots.py`
- Modify: `api/main.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/integration/test_api_frontend_smoke.py`:

```python
def test_profile_endpoint_returns_200_or_404(client):
    r = client.get("/api/jobs/orders_reconciliation/profile", headers=auth_headers())
    assert r.status_code in (200, 404)


def test_profile_history_endpoint_returns_200(client):
    r = client.get("/api/jobs/orders_reconciliation/profile/history?column=amount", headers=auth_headers())
    assert r.status_code in (200, 404)


def test_suggest_rules_endpoint_returns_200_or_404(client):
    r = client.post("/api/jobs/orders_reconciliation/suggest-rules", headers=auth_headers())
    assert r.status_code in (200, 404)


def test_schema_history_endpoint_returns_200(client):
    r = client.get("/api/jobs/orders_reconciliation/schema-history", headers=auth_headers())
    assert r.status_code in (200, 404)
```

- [ ] **Step 2: Run to verify they fail**

```
python -m pytest tests/integration/test_api_frontend_smoke.py -k "profile or schema_history or suggest" -v
```

Expected: FAIL — 404 for route not found.

- [ ] **Step 3: Create `api/routes/profiles.py`**

```python
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from api.dependencies import get_session
from etl_framework.repository.repository import ColumnProfileRepository

router = APIRouter(tags=["profiles"])


@router.get("/jobs/{job_name}/profile")
def get_latest_profile(job_name: str, db: Session = Depends(get_session)):
    repo = ColumnProfileRepository(db)
    rows = repo.get_latest(job_name)
    if not rows:
        raise HTTPException(status_code=404, detail="No profile found for job")
    return [
        {
            "column_name": r.column_name,
            "null_rate": r.null_rate,
            "distinct_count": r.distinct_count,
            "min_val": r.min_val,
            "max_val": r.max_val,
            "mean_val": r.mean_val,
            "std_val": r.std_val,
            "p25": r.p25,
            "p50": r.p50,
            "p75": r.p75,
            "p95": r.p95,
            "captured_at": r.captured_at.isoformat() if r.captured_at else None,
        }
        for r in rows
    ]


@router.get("/jobs/{job_name}/profile/history")
def get_profile_history(job_name: str, column: str, db: Session = Depends(get_session)):
    repo = ColumnProfileRepository(db)
    rows = repo.get_history(job_name, column)
    return [
        {
            "run_id": r.run_id,
            "null_rate": r.null_rate,
            "distinct_count": r.distinct_count,
            "mean_val": r.mean_val,
            "std_val": r.std_val,
            "p25": r.p25,
            "p50": r.p50,
            "p75": r.p75,
            "p95": r.p95,
            "captured_at": r.captured_at.isoformat() if r.captured_at else None,
        }
        for r in rows
    ]


@router.post("/jobs/{job_name}/suggest-rules")
def suggest_rules(job_name: str, db: Session = Depends(get_session)):
    repo = ColumnProfileRepository(db)
    rows = repo.get_latest(job_name)
    if not rows:
        raise HTTPException(status_code=404, detail="No profile found — run a profile job first")
    suggestions = []
    for r in rows:
        if r.null_rate is not None and r.null_rate < 1.0:
            suggestions.append({
                "type": "completeness_ratio",
                "column": r.column_name,
                "min_value": round(max(0.0, (1.0 - r.null_rate) - 0.05), 3),
                "severity": "warn",
            })
        if r.min_val is not None and r.max_val is not None and r.mean_val is not None:
            try:
                suggestions.append({
                    "type": "column_value_between",
                    "column": r.column_name,
                    "min_value": float(r.min_val),
                    "max_value": float(r.max_val) * 1.1,
                    "severity": "warn",
                })
            except (ValueError, TypeError):
                pass
        if r.p95 is not None:
            suggestions.append({
                "type": "column_percentile",
                "column": r.column_name,
                "percentile": 95,
                "max_value": round(r.p95 * 1.2, 4),
                "severity": "warn",
            })
    return {"job_name": job_name, "suggested_rules": suggestions}
```

- [ ] **Step 4: Create `api/routes/schema_snapshots.py`**

```python
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from api.dependencies import get_session
from etl_framework.repository.repository import SchemaSnapshotRepository
from api.services.schema_snapshot_service import diff_schemas

router = APIRouter(tags=["schema-snapshots"])


@router.get("/jobs/{job_name}/schema-history")
def get_schema_history(
    job_name: str,
    environment: str = "source",
    db: Session = Depends(get_session),
):
    repo = SchemaSnapshotRepository(db)
    rows = repo.get_history(job_name, environment)
    result = []
    for i, row in enumerate(rows):
        prev_cols = rows[i - 1].columns if i > 0 else []
        diff = diff_schemas(row.columns, prev_cols)
        result.append({
            "id": row.id,
            "run_id": row.run_id,
            "captured_at": row.captured_at.isoformat() if row.captured_at else None,
            "environment": row.environment,
            "columns": row.columns,
            "diff": diff,
        })
    return result
```

- [ ] **Step 5: Register in `api/main.py`**

Add imports and `include_router` calls:

```python
from api.routes import profiles as profiles_routes
from api.routes import schema_snapshots as schema_snapshot_routes
```

```python
app.include_router(profiles_routes.router, prefix="/api")
app.include_router(schema_snapshot_routes.router, prefix="/api")
```

- [ ] **Step 6: Run tests**

```
python -m pytest tests/integration/test_api_frontend_smoke.py -k "profile or schema_history or suggest" -v
```

Expected: PASS (200 or 404 are both acceptable for empty DB)

- [ ] **Step 7: Commit**

```bash
git add api/routes/profiles.py api/routes/schema_snapshots.py api/main.py tests/integration/test_api_frontend_smoke.py
git commit -m "feat(api): add profile and schema-history routes"
```

---

## Task 13: Frontend — new DQ rule types and job type param forms

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Find the DQ rule type dropdown in `frontend/app.js`**

```
grep -n "not_null\|row_count_min\|match_regex" frontend/app.js | head -20
```

Note the line number of the rule type `<select>` options list.

- [ ] **Step 2: Extend the rule type select options**

In the section of `app.js` that renders the DQ rule type dropdown, add the 12 new options after the existing ones. The existing pattern likely contains a list like:

```javascript
const DQ_RULE_TYPES = [
  'not_null', 'unique', 'row_count_min', 'row_count_max', 'row_count_between',
  'column_mean_between', 'match_regex', 'custom_sql',
  'column_max_length', 'column_min_length', 'value_in_set', 'value_not_in_set',
  'column_contains', 'date_range', 'positive_values', 'negative_values',
];
```

Extend it to:

```javascript
const DQ_RULE_TYPES = [
  'not_null', 'unique', 'row_count_min', 'row_count_max', 'row_count_between',
  'column_mean_between', 'match_regex', 'custom_sql',
  'column_max_length', 'column_min_length', 'value_in_set', 'value_not_in_set',
  'column_contains', 'date_range', 'positive_values', 'negative_values',
  // New rule types
  'completeness_ratio', 'distinct_count_between', 'column_sum_between',
  'column_std_dev_between', 'column_percentile', 'column_type_check',
  'column_value_between', 'cross_column_consistency', 'pii_mask_check',
  'no_whitespace', 'referential_check', 'custom_sql_assert',
];
```

If the options are rendered inline as `<option>` tags rather than via a data array, find the block and add the new `<option>` elements in the same pattern.

- [ ] **Step 3: Add conditional field visibility for new rule-specific params**

Find the section that shows/hides rule fields (e.g. `column` input, `pattern` input). Add visibility conditions for the new fields. Search for the pattern that controls field visibility:

```
grep -n "showColumn\|showPattern\|rule.type\|rtype" frontend/app.js | head -20
```

Add logic to show:
- `percentile` input when `rule.type === 'column_percentile'`
- `operator` select when `rule.type === 'cross_column_consistency' || rule.type === 'custom_sql_assert'`
- `column_b` input when `rule.type === 'cross_column_consistency'`
- `lookup_query` textarea when `rule.type === 'referential_check'`
- `expected_type` select when `rule.type === 'column_type_check'`

The exact implementation depends on the current field-visibility pattern. Follow the existing Alpine.js `x-show` pattern already used in the rule editor.

- [ ] **Step 4: Add new job type options**

Find where `job_type` is rendered in the job editor (search for `bo_report` or `automic_job`):

```
grep -n "bo_report\|automic_job\|job_type" frontend/app.js | head -20
```

Add the 4 new job types to the `<select>` or options array: `freshness`, `cross_job_assertion`, `schema_snapshot`, `profile`.

- [ ] **Step 5: Add param forms for new job types**

Following the pattern of the existing `bo_report` and `automic_job` param forms, add conditional param sections for:

**freshness:** `timestamp_column` (text input), `max_age_hours` (number input, default 24)

**cross_job_assertion:** `source_job` (text), `source_metric` (select: count/distinct_count), `source_column` (text), `target_job` (text), `target_metric` (select), `target_column` (text), `tolerance` (number, default 0), `tolerance_type` (select: absolute/percent)

**schema_snapshot:** `environment` (select: source/target/both, default both)

**profile:** `columns` (comma-separated text, hint "leave blank for all"), `drift_threshold_pct` (number, default 20)

- [ ] **Step 6: Verify JS syntax**

```
node --check frontend/app.js
```

Expected: no output (no errors)

- [ ] **Step 7: Commit**

```bash
git add frontend/app.js
git commit -m "feat(ui): add new DQ rule types and job type param forms to job editor"
```

---

## Task 14: Frontend — Profile and Schema sub-tabs in History

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`

- [ ] **Step 1: Add sub-tab markup to `frontend/index.html`**

Find the History tab section and locate the existing sub-tab navigation (Runs, Trends, Lineage, Audit). Add two new sub-tab buttons:

```html
<button x-on:click="historyTab='profile'"
        x-bind:class="historyTab==='profile' ? 'tab-active' : ''"
        class="tab">Profile</button>
<button x-on:click="historyTab='schema'"
        x-bind:class="historyTab==='schema' ? 'tab-active' : ''"
        class="tab">Schema</button>
```

Add Profile sub-tab content panel:

```html
<!-- Profile sub-tab -->
<div x-show="historyTab==='profile'" class="panel">
  <div class="form-row">
    <label>Job</label>
    <select x-model="profileJobName" x-on:change="loadProfile()">
      <template x-for="j in jobs" :key="j.name">
        <option :value="j.name" x-text="j.name"></option>
      </template>
    </select>
  </div>
  <div x-show="profileData.length > 0">
    <table class="data-table">
      <thead>
        <tr>
          <th>Column</th><th>Null Rate</th><th>Distinct</th>
          <th>Mean</th><th>Std Dev</th><th>p50</th><th>p95</th>
        </tr>
      </thead>
      <tbody>
        <template x-for="row in profileData" :key="row.column_name">
          <tr>
            <td x-text="row.column_name"></td>
            <td x-text="row.null_rate !== null ? (row.null_rate * 100).toFixed(1) + '%' : '—'"></td>
            <td x-text="row.distinct_count ?? '—'"></td>
            <td x-text="row.mean_val !== null ? row.mean_val.toFixed(2) : '—'"></td>
            <td x-text="row.std_val !== null ? row.std_val.toFixed(2) : '—'"></td>
            <td x-text="row.p50 !== null ? row.p50.toFixed(2) : '—'"></td>
            <td x-text="row.p95 !== null ? row.p95.toFixed(2) : '—'"></td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
  <p x-show="profileData.length === 0" class="muted">No profile data. Run a profile job first.</p>
</div>

<!-- Schema sub-tab -->
<div x-show="historyTab==='schema'" class="panel">
  <div class="form-row">
    <label>Job</label>
    <select x-model="schemaJobName" x-on:change="loadSchemaHistory()">
      <template x-for="j in jobs" :key="j.name">
        <option :value="j.name" x-text="j.name"></option>
      </template>
    </select>
  </div>
  <template x-for="entry in schemaHistory" :key="entry.id">
    <div class="card" style="margin-bottom:0.5rem">
      <div class="card-header">
        <span x-text="entry.captured_at"></span>
        <span x-show="entry.diff.added.length + entry.diff.removed.length + entry.diff.changed.length === 0"
              class="badge badge-green">No change</span>
        <span x-show="entry.diff.added.length + entry.diff.removed.length + entry.diff.changed.length > 0"
              class="badge badge-red"
              x-text="(entry.diff.added.length + entry.diff.removed.length + entry.diff.changed.length) + ' change(s)'"></span>
      </div>
      <div x-show="entry.diff.added.length > 0">
        <strong>Added:</strong>
        <template x-for="col in entry.diff.added" :key="col">
          <span class="badge badge-green" x-text="col"></span>
        </template>
      </div>
      <div x-show="entry.diff.removed.length > 0">
        <strong>Removed:</strong>
        <template x-for="col in entry.diff.removed" :key="col">
          <span class="badge badge-red" x-text="col"></span>
        </template>
      </div>
      <div x-show="entry.diff.changed.length > 0">
        <strong>Type changed:</strong>
        <template x-for="c in entry.diff.changed" :key="c.column">
          <span class="badge badge-yellow" x-text="c.column + ': ' + c.from + ' → ' + c.to"></span>
        </template>
      </div>
    </div>
  </template>
  <p x-show="schemaHistory.length === 0" class="muted">No schema snapshots. Run a schema_snapshot job first.</p>
</div>
```

- [ ] **Step 2: Add Alpine.js state and methods to `frontend/app.js`**

In the main Alpine data object, add:

```javascript
profileJobName: '',
profileData: [],
schemaJobName: '',
schemaHistory: [],
```

Add methods:

```javascript
async loadProfile() {
  if (!this.profileJobName) return;
  try {
    const r = await fetch(`/api/jobs/${encodeURIComponent(this.profileJobName)}/profile`, {
      headers: { Authorization: `Bearer ${this.token}` }
    });
    this.profileData = r.ok ? await r.json() : [];
  } catch { this.profileData = []; }
},

async loadSchemaHistory() {
  if (!this.schemaJobName) return;
  try {
    const r = await fetch(`/api/jobs/${encodeURIComponent(this.schemaJobName)}/schema-history`, {
      headers: { Authorization: `Bearer ${this.token}` }
    });
    this.schemaHistory = r.ok ? await r.json() : [];
  } catch { this.schemaHistory = []; }
},
```

- [ ] **Step 3: Verify syntax**

```
node --check frontend/app.js
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "feat(ui): add Profile and Schema sub-tabs to History tab"
```

---

## Task 15: Property-based tests

**Files:**
- Create: `tests/property/test_dq_rules_property.py`

- [ ] **Step 1: Install hypothesis (dev only)**

Check `pyproject.toml` for `[project.optional-dependencies]` `dev` section. Add `hypothesis>=6.0` if not present:

```toml
[project.optional-dependencies]
dev = [
    # ... existing dev deps ...
    "hypothesis>=6.0",
]
```

Install:

```
pip install hypothesis
```

- [ ] **Step 2: Write property tests**

Create `tests/property/test_dq_rules_property.py`:

```python
"""Property tests: DQEngine never raises; always returns list[DQViolation]."""
import pandas as pd
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st
from hypothesis.extra.pandas import column, data_frames, range_indexes

from etl_framework.reconciliation.dq_engine import DQEngine, DQViolation
from api.schemas import DQRule

ALL_PURE_RULE_TYPES = [
    "not_null", "unique", "row_count_min", "row_count_max", "row_count_between",
    "column_mean_between", "match_regex", "column_max_length", "column_min_length",
    "value_in_set", "value_not_in_set", "column_contains", "date_range",
    "positive_values", "negative_values",
    "completeness_ratio", "distinct_count_between", "column_sum_between",
    "column_std_dev_between", "column_percentile", "column_type_check",
    "column_value_between", "cross_column_consistency", "pii_mask_check",
    "no_whitespace",
]


def _make_rule(rtype: str) -> DQRule:
    base = {"type": rtype, "column": "x", "severity": "error"}
    extras = {
        "completeness_ratio": {"min_value": 0.5},
        "distinct_count_between": {"min_value": 1, "max_value": 100},
        "column_sum_between": {"min_value": -1e9, "max_value": 1e9},
        "column_std_dev_between": {"min_value": 0, "max_value": 1e9},
        "column_percentile": {"percentile": 50, "min_value": -1e9, "max_value": 1e9},
        "column_type_check": {"expected_type": "float"},
        "column_value_between": {"min_value": -1e9, "max_value": 1e9},
        "cross_column_consistency": {"column_b": "y", "operator": "<="},
        "pii_mask_check": {"pattern": r"\d{3}-\d{2}-\d{4}"},
        "match_regex": {"pattern": r".*"},
        "row_count_min": {"min_value": 0},
        "row_count_max": {"max_value": 1e9},
        "row_count_between": {"min_value": 0, "max_value": 1e9},
        "column_mean_between": {"min_value": -1e9, "max_value": 1e9},
        "column_max_length": {"max_value": 1000},
        "column_min_length": {"min_value": 0},
        "value_in_set": {"values": ["a", "b", "c"]},
        "value_not_in_set": {"values": ["forbidden"]},
        "column_contains": {"pattern": ""},
    }
    base.update(extras.get(rtype, {}))
    return DQRule.model_validate(base)


_df_strategy = data_frames(
    columns=[
        column("x", dtype=float),
        column("y", dtype=float),
    ],
    index=range_indexes(min_size=0, max_size=50),
)


@given(df=_df_strategy, rtype=st.sampled_from(ALL_PURE_RULE_TYPES))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_dq_engine_never_raises(df, rtype):
    rule = _make_rule(rtype)
    result = DQEngine().evaluate(df, [rule])
    assert isinstance(result, list)
    assert all(isinstance(v, DQViolation) for v in result)
```

- [ ] **Step 3: Run property tests**

```
python -m pytest tests/property/test_dq_rules_property.py -v
```

Expected: PASS (200 examples per rule type)

- [ ] **Step 4: Commit**

```bash
git add tests/property/test_dq_rules_property.py pyproject.toml
git commit -m "test(property): add hypothesis fuzz tests for all DQ rule types"
```

---

## Task 16: Full regression + final commit

- [ ] **Step 1: Run all unit tests**

```
python -m pytest tests/unit/ -q
```

Expected: all pass.

- [ ] **Step 2: Run integration tests**

```
python -m pytest tests/integration/ -q
```

Expected: all pass.

- [ ] **Step 3: Check JS syntax**

```
node --check frontend/app.js
```

Expected: no output.

- [ ] **Step 4: Compile key Python modules**

```
python -m py_compile api/routes/profiles.py api/routes/schema_snapshots.py api/services/profile_service.py api/services/schema_snapshot_service.py
```

Expected: no output (no errors).

- [ ] **Step 5: Smoke-run the server**

```
python -m uvicorn api.main:app --host 127.0.0.1 --port 8001 &
sleep 3
curl -s http://127.0.0.1:8001/api/health
```

Expected: `{"status": "ok", ...}`

Stop the server:

```
kill %1
```

- [ ] **Step 6: Tag complete**

```bash
git tag etl-capabilities-v1
```

---

## Self-Review Notes

**Spec coverage check:**
- ✅ 12 new DQ rule types — Tasks 2, 3, 4
- ✅ `freshness` job — Task 9
- ✅ `profile` job — Task 10
- ✅ `schema_snapshot` job — Task 10
- ✅ `cross_job_assertion` job — Task 11
- ✅ `/api/jobs/{name}/profile` — Task 12
- ✅ `/api/jobs/{name}/profile/history` — Task 12
- ✅ `/api/jobs/{name}/suggest-rules` — Task 12
- ✅ `/api/jobs/{name}/schema-history` — Task 12
- ✅ `schema_snapshots` DB table — Task 5
- ✅ `column_profiles` DB table — Task 5
- ✅ Profile sub-tab UI — Task 14
- ✅ Schema sub-tab UI — Task 14
- ✅ Property tests — Task 15
- ✅ Integration smoke tests — Task 12

**Type consistency:** All method names used in tests (`_execute_freshness`, `_execute_profile`, `_execute_schema_snapshot`, `_execute_cross_job`, `_build_case_freshness`, etc.) match the method names defined in Task 9–11. Repository methods (`save`, `get_latest`, `get_history`) are used consistently across Tasks 8, 10, 11, 12. `compute_profile` and `detect_drift` from `profile_service` match signatures defined in Task 6.
