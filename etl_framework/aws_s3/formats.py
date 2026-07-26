from __future__ import annotations

import io
import json

import pyarrow.orc as orc
import pyarrow.parquet as pq

from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.models import FormatValidationResult
from etl_framework.exceptions import (
    FileFormatValidationError,
    SchemaValidationError,
    UnsupportedFormatError,
)


_TYPE_ALIASES = {
    "integer": "int64",
    "long": "int64",
    "double": "float64",
    "str": "string",
    "boolean": "bool",
}


def normalize_schema_type(type_name: str) -> str:
    normalized = "".join(str(type_name).strip().lower().split())
    return _TYPE_ALIASES.get(normalized, normalized)


def compare_expected_schema(expected: dict[str, str], actual: dict[str, str]) -> dict[str, object]:
    expected_names = {str(name) for name in expected}
    actual_names = {str(name) for name in actual}
    common = sorted(expected_names & actual_names)
    type_mismatches: list[dict[str, str]] = []
    for column in common:
        expected_type = normalize_schema_type(expected[column])
        actual_type = normalize_schema_type(actual[column])
        if expected_type != actual_type:
            type_mismatches.append({
                "column": column,
                "expected_type": expected_type,
                "actual_type": actual_type,
            })
    return {
        "missing_in_target": sorted(expected_names - actual_names),
        "extra_in_target": sorted(actual_names - expected_names),
        "type_mismatches": type_mismatches,
    }


def _actual_schema(fmt: str, data: bytes) -> dict[str, str]:
    """Return {column: type_string} for the object's inferred schema."""
    buf = io.BytesIO(data)
    if fmt == "parquet":
        schema = pq.ParquetFile(buf).schema_arrow
        return {name: str(schema.field(name).type) for name in schema.names}
    if fmt == "orc":
        schema = orc.ORCFile(buf).schema
        return {name: str(schema.field(name).type) for name in schema.names}
    if fmt == "csv":
        header = data.decode("utf-8").splitlines()[0]
        return {col.strip(): "string" for col in header.split(",")}
    if fmt == "json":
        first = data.decode("utf-8").splitlines()[0]
        return {k: "string" for k in json.loads(first).keys()}
    raise UnsupportedFormatError(fmt)


def _parse_check(fmt: str, data: bytes) -> None:
    """Raise if the bytes do not parse as ``fmt``."""
    buf = io.BytesIO(data)
    if fmt == "parquet":
        pq.ParquetFile(buf).metadata  # noqa: B018 — forces footer parse
    elif fmt == "orc":
        orc.ORCFile(buf).nrows
    elif fmt == "csv":
        text = data.decode("utf-8")
        if not text.strip():
            raise ValueError("empty CSV")
    elif fmt == "json":
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                json.loads(line)
    else:
        raise UnsupportedFormatError(fmt)


def validate_format(
    client: S3Client,
    bucket: str,
    key: str,
    fmt: str,
    expected_schema: dict[str, str] | None = None,
) -> FormatValidationResult:
    """Confirm an object parses as ``fmt``; optionally assert its schema.

    Parse failure -> FileFormatValidationError.
    Schema drift  -> SchemaValidationError (missing/extra/type mismatches).
    """
    if fmt not in {"csv", "json", "parquet", "orc"}:
        raise UnsupportedFormatError(fmt)

    data = client.get_object(bucket, key)
    try:
        _parse_check(fmt, data)
    except UnsupportedFormatError:
        raise
    except Exception as exc:
        raise FileFormatValidationError(bucket, key, fmt, exc) from exc

    if expected_schema is None:
        return FormatValidationResult(bucket=bucket, key=key, fmt=fmt, parsed=True)

    actual = _actual_schema(fmt, data)
    comparison = compare_expected_schema(expected_schema, actual)
    missing = comparison["missing_in_target"]
    extra = comparison["extra_in_target"]
    type_mismatches = comparison["type_mismatches"]
    if missing or extra or type_mismatches:
        raise SchemaValidationError(
            query_name=f"s3://{bucket}/{key}",
            missing_in_target=missing,
            extra_in_target=extra,
            type_mismatches=type_mismatches,
        )
    return FormatValidationResult(
        bucket=bucket, key=key, fmt=fmt, parsed=True, schema_ok=True,
    )
