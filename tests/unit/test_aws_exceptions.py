from __future__ import annotations

from etl_framework.exceptions import (
    ETLFrameworkError,
    AWSError,
    S3ObjectNotFoundError,
    S3SelectError,
    UnsupportedFormatError,
    FileFormatValidationError,
)


def test_aws_error_is_framework_error():
    assert issubclass(AWSError, ETLFrameworkError)


def test_subtypes_inherit_aws_error():
    for exc in (
        S3ObjectNotFoundError,
        S3SelectError,
        UnsupportedFormatError,
        FileFormatValidationError,
    ):
        assert issubclass(exc, AWSError)


def test_object_not_found_carries_bucket_and_key():
    err = S3ObjectNotFoundError(bucket="b", key="k/x.csv")
    assert err.bucket == "b"
    assert err.key == "k/x.csv"
    assert "b" in str(err) and "k/x.csv" in str(err)


def test_unsupported_format_carries_fmt():
    err = UnsupportedFormatError(fmt="avro")
    assert err.fmt == "avro"
    assert "avro" in str(err)
