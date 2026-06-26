"""Property-based tests for DQ engine rules using hypothesis."""
import pandas as pd
from hypothesis import given, assume, settings
from hypothesis import strategies as st

from etl_framework.reconciliation.dq_engine import DQEngine
from api.schemas import DQRule


def _df(col, values):
    return pd.DataFrame({col: values})


def _rule(**kwargs):
    return DQRule.model_validate(kwargs)


# ── completeness_ratio ────────────────────────────────────────────────────────

@given(
    values=st.lists(st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False)), min_size=1),
    min_val=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
@settings(max_examples=60)
def test_completeness_ratio_property(values, min_val):
    """completeness_ratio fires iff non-null ratio < min_value."""
    df = _df("v", values)
    rule = [_rule(type="completeness_ratio", column="v", min_value=min_val, severity="warn")]
    violations = DQEngine().evaluate(df, rule)
    actual_ratio = sum(1 for v in values if v is not None) / len(values)
    if actual_ratio < min_val:
        assert len(violations) == 1
    else:
        assert len(violations) == 0


# ── distinct_count_between ────────────────────────────────────────────────────

@given(
    values=st.lists(st.integers(min_value=0, max_value=5), min_size=1, max_size=20),
    lo=st.integers(min_value=0, max_value=3),
    hi=st.integers(min_value=3, max_value=10),
)
@settings(max_examples=60)
def test_distinct_count_between_property(values, lo, hi):
    df = _df("v", values)
    rule = [_rule(type="distinct_count_between", column="v", min_value=lo, max_value=hi, severity="error")]
    violations = DQEngine().evaluate(df, rule)
    n = df["v"].nunique()
    if lo <= n <= hi:
        assert len(violations) == 0
    else:
        assert len(violations) == 1


# ── column_sum_between ────────────────────────────────────────────────────────

@given(
    values=st.lists(st.floats(min_value=-100, max_value=100, allow_nan=False, allow_infinity=False), min_size=1, max_size=20),
    lo=st.floats(min_value=-200, max_value=0, allow_nan=False),
    hi=st.floats(min_value=100, max_value=2000, allow_nan=False),
)
@settings(max_examples=60)
def test_column_sum_between_property(values, lo, hi):
    assume(lo <= hi)
    df = _df("v", values)
    rule = [_rule(type="column_sum_between", column="v", min_value=lo, max_value=hi, severity="warn")]
    violations = DQEngine().evaluate(df, rule)
    total = sum(v for v in values if v == v)  # exclude NaN
    if lo <= total <= hi:
        assert len(violations) == 0
    else:
        assert len(violations) == 1


# ── no_whitespace ─────────────────────────────────────────────────────────────

@given(
    clean=st.lists(st.from_regex(r"[A-Za-z0-9]+", fullmatch=True), min_size=0, max_size=5),
    dirty=st.lists(
        st.one_of(
            st.from_regex(r" [A-Za-z0-9]+", fullmatch=True),   # leading space
            st.from_regex(r"[A-Za-z0-9]+ ", fullmatch=True),   # trailing space
        ),
        min_size=0, max_size=5,
    ),
)
@settings(max_examples=50)
def test_no_whitespace_property(clean, dirty):
    values = clean + dirty
    assume(len(values) > 0)
    df = _df("s", values)
    rule = [_rule(type="no_whitespace", column="s", severity="error")]
    violations = DQEngine().evaluate(df, rule)
    if dirty:
        assert len(violations) == 1
    else:
        assert len(violations) == 0


# ── not_null ─────────────────────────────────────────────────────────────────

@given(
    values=st.lists(st.one_of(st.none(), st.integers()), min_size=1, max_size=20)
)
@settings(max_examples=60)
def test_not_null_property(values):
    df = _df("v", values)
    rule = [_rule(type="not_null", column="v", severity="error")]
    violations = DQEngine().evaluate(df, rule)
    has_null = any(v is None for v in values)
    if has_null:
        assert len(violations) == 1
    else:
        assert len(violations) == 0


# ── column_value_between ──────────────────────────────────────────────────────

@given(
    lo=st.floats(min_value=0, max_value=50, allow_nan=False),
    hi=st.floats(min_value=50, max_value=100, allow_nan=False),
    extra=st.floats(min_value=101, max_value=200, allow_nan=False, allow_infinity=False),
    include_extra=st.booleans(),
)
@settings(max_examples=50)
def test_column_value_between_property(lo, hi, extra, include_extra):
    assume(lo <= hi)
    values = [lo, (lo + hi) / 2, hi]
    if include_extra:
        values.append(extra)
    df = _df("v", values)
    rule = [_rule(type="column_value_between", column="v", min_value=lo, max_value=hi, severity="warn")]
    violations = DQEngine().evaluate(df, rule)
    if include_extra:
        assert len(violations) == 1
    else:
        assert len(violations) == 0


# ── match_regex ───────────────────────────────────────────────────────────────

@given(
    matching=st.lists(st.from_regex(r"[A-Z]{3}", fullmatch=True), min_size=1, max_size=5),
    non_matching=st.lists(st.from_regex(r"[a-z]{3}", fullmatch=True), min_size=0, max_size=5),
)
@settings(max_examples=50)
def test_match_regex_property(matching, non_matching):
    values = matching + non_matching
    df = _df("code", values)
    rule = [_rule(type="match_regex", column="code", pattern=r"^[A-Z]{3}$", severity="error")]
    violations = DQEngine().evaluate(df, rule)
    if non_matching:
        assert len(violations) == 1
    else:
        assert len(violations) == 0


# ── column_std_dev_between ────────────────────────────────────────────────────

@given(
    n=st.integers(min_value=3, max_value=20),
    lo=st.floats(min_value=0.0, max_value=0.5, allow_nan=False),
    hi=st.floats(min_value=10.0, max_value=50.0, allow_nan=False),
)
@settings(max_examples=40)
def test_column_std_dev_between_constant_is_zero(n, lo, hi):
    """A constant column always has std_dev=0; rule fires iff 0 < lo."""
    assume(lo <= hi)
    df = _df("v", [42.0] * n)
    rule = [_rule(type="column_std_dev_between", column="v", min_value=lo, max_value=hi, severity="warn")]
    violations = DQEngine().evaluate(df, rule)
    if lo > 0:
        assert len(violations) == 1
    else:
        assert len(violations) == 0
