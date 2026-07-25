from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.formats import validate_format
from etl_framework.exceptions import FileFormatValidationError, SchemaValidationError
from tests.helpers.s3_fixtures import write_parquet, SAMPLE_ROWS


@pytest.fixture
def s3(tmp_path):
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="data")
        raw.put_object(Bucket="data", Key="ok.csv", Body=b"id,name\n1,alice\n")
        raw.put_object(Bucket="data", Key="bad.parquet", Body=b"not a parquet file")
        p = write_parquet(tmp_path / "ok.parquet")
        raw.put_object(Bucket="data", Key="ok.parquet", Body=p.read_bytes())
        session = AWSSession(AWSConfig(region="us-east-1"))
        session._clients["s3"] = raw
        yield S3Client(session)


def test_valid_csv_parses(s3):
    r = validate_format(s3, "data", "ok.csv", "csv")
    assert r.parsed is True
    assert r.schema_ok is None


def test_corrupt_parquet_raises(s3):
    with pytest.raises(FileFormatValidationError):
        validate_format(s3, "data", "bad.parquet", "parquet")


def test_schema_assert_passes(s3):
    r = validate_format(s3, "data", "ok.parquet", "parquet",
                        expected_schema={"id": "int64", "name": "string"})
    assert r.parsed is True
    assert r.schema_ok is True


def test_schema_drift_raises_with_missing_and_extra(s3):
    with pytest.raises(SchemaValidationError) as exc:
        validate_format(s3, "data", "ok.parquet", "parquet",
                        expected_schema={"id": "int64", "email": "string"})
    assert "email" in exc.value.missing_in_target
    assert "name" in exc.value.extra_in_target
