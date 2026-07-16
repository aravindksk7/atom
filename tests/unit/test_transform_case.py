import pandas as pd
import pytest

from etl_framework.transform_testing.harness import TransformCase


def test_passing_transform_returns_no_mismatches():
    case = TransformCase(
        transform_sql="SELECT id, amount * 2 AS doubled FROM orders",
        inputs={"orders": pd.DataFrame({"id": [1, 2], "amount": [10.0, 20.0]})},
        expected=pd.DataFrame({"id": [1, 2], "doubled": [20.0, 40.0]}),
        key_columns=["id"],
    )
    assert case.run() == []


def test_wrong_output_reports_value_mismatch():
    case = TransformCase(
        transform_sql="SELECT id, amount FROM orders",
        inputs={"orders": pd.DataFrame({"id": [1], "amount": [10.0]})},
        expected=pd.DataFrame({"id": [1], "amount": [99.0]}),
        key_columns=["id"],
    )
    mismatches = case.run()
    assert len(mismatches) == 1
    assert mismatches[0].column_name == "amount"
    assert mismatches[0].mismatch_type == "value_diff"


def test_multiple_input_tables_joinable():
    case = TransformCase(
        transform_sql="""
            SELECT o.id, c.region
            FROM orders o JOIN customers c ON o.customer_id = c.id
        """,
        inputs={
            "orders": pd.DataFrame({"id": [1], "customer_id": [7]}),
            "customers": pd.DataFrame({"id": [7], "region": ["EU"]}),
        },
        expected=pd.DataFrame({"id": [1], "region": ["EU"]}),
        key_columns=["id"],
    )
    assert case.run() == []


def test_bad_sql_raises_with_context():
    case = TransformCase(
        transform_sql="SELECT nope FROM missing_table",
        inputs={},
        expected=pd.DataFrame(),
        key_columns=[],
    )
    with pytest.raises(Exception):
        case.run()
