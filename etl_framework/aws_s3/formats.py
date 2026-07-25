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
    Schema drift  -> SchemaValidationError (missing/extra columns).
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
    missing = sorted(set(expected_schema) - set(actual))
    extra = sorted(set(actual) - set(expected_schema))
    if missing or extra:
        raise SchemaValidationError(
            query_name=f"s3://{bucket}/{key}",
            missing_in_target=missing,
            extra_in_target=extra,
        )
    return FormatValidationResult(
        bucket=bucket, key=key, fmt=fmt, parsed=True, schema_ok=True,
    )
