from __future__ import annotations

import pytest

from etl_framework.aws_s3.formats import compare_expected_schema, normalize_schema_type, validate_format
from etl_framework.exceptions import SchemaValidationError


class FakeS3Client:
    def __init__(self, objects: dict[tuple[str, str], bytes]) -> None:
        self.objects = objects

    def get_object(self, bucket: str, key: str) -> bytes:
        return self.objects[(bucket, key)]


def test_normalize_schema_type_collapses_safe_aliases():
    assert normalize_schema_type(" INTEGER ") == "int64"
    assert normalize_schema_type("long") == "int64"
    assert normalize_schema_type("DOUBLE") == "float64"
    assert normalize_schema_type("str") == "string"
    assert normalize_schema_type("boolean") == "bool"
    assert normalize_schema_type("decimal(12, 2)") == "decimal(12,2)"


def test_compare_expected_schema_reports_missing_extra_and_type_mismatch():
    result = compare_expected_schema(
        {"id": "int64", "amount": "decimal(12,2)", "email": "string"},
        {"id": "integer", "amount": "string", "name": "string"},
    )

    assert result == {
        "missing_in_target": ["email"],
        "extra_in_target": ["name"],
        "type_mismatches": [
            {"column": "amount", "expected_type": "decimal(12,2)", "actual_type": "string"}
        ],
    }


def test_validate_format_raises_type_mismatches_for_csv_schema():
    client = FakeS3Client({("b", "orders.csv"): b"id,amount\n1,10.5\n"})

    with pytest.raises(SchemaValidationError) as err:
        validate_format(
            client,
            "b",
            "orders.csv",
            "csv",
            expected_schema={"id": "string", "amount": "decimal(12,2)"},
        )

    assert err.value.missing_in_target == []
    assert err.value.extra_in_target == []
    assert err.value.type_mismatches == [
        {"column": "amount", "expected_type": "decimal(12,2)", "actual_type": "string"}
    ]
