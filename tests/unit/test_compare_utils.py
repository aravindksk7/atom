from __future__ import annotations

from datetime import timedelta

import pandas as pd

from etl_framework.reconciliation.compare_utils import (
    build_mismatch_summary,
    normalize_string_columns,
    numeric_delta,
    value_mismatch_mask,
    values_match,
)


def test_normalize_string_columns_applies_case_and_whitespace_rules():
    df = pd.DataFrame({"name": ["  A   B  "], "other": [" X "]})
    result = normalize_string_columns(df, case_insensitive_columns=["name"], whitespace_normalize_columns=["name"])
    assert result.loc[0, "name"] == "a b"
    assert result.loc[0, "other"] == " X "


def test_value_mismatch_mask_respects_float_and_null_tolerance():
    both = pd.DataFrame({"a": [1.0, None, 3.0], "b": [1.001, None, 4.0]})
    mask = value_mismatch_mask(both, "a", "b", both["a"], column_tolerance=0.01)
    assert mask.tolist() == [False, False, True]


def test_values_match_datetime_and_numeric():
    assert values_match(1.0, 1.01, is_float=True, float_tolerance=0.02)
    assert values_match(
        pd.Timestamp("2024-01-01 00:00:00"),
        pd.Timestamp("2024-01-01 00:00:01"),
        is_datetime=True,
        datetime_tolerance=timedelta(seconds=2),
    )


def test_numeric_delta_and_summary():
    assert numeric_delta(10, 12) == (2.0, 0.2)
    assert build_mismatch_summary(1, 2, 3, {"amount": 3}) == {
        "by_column": {"amount": 3, "<row>": 3},
        "compared_rows_by_column": {"<row>": 3},
        "by_type": {"value_diff": 3, "missing_in_target": 1, "missing_in_source": 2},
    }
