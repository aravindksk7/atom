"""Tests for schema_snapshot_service: capture_schema and diff_schemas."""
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
