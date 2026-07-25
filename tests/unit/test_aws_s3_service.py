from __future__ import annotations

from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from api.services.aws_s3_service import AwsS3Service
from etl_framework.exceptions import SchemaValidationError


class _FakeConfigRecord:
    def __init__(self, config_json):
        self.env_name = "test"
        self.config_json = config_json


def _service_with_moto(raw):
    repo = MagicMock()
    repo.get.return_value = _FakeConfigRecord({
        "db_host": "h", "db_password": "p", "aws_region": "us-east-1",
    })
    svc = AwsS3Service(repo)
    # Inject the moto-backed s3 client so no real AWS/session is built.
    svc._s3_client_override = raw
    return svc


@pytest.fixture
def svc_and_bucket():
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="data")
        raw.put_object(Bucket="data", Key="a/1.csv", Body=b"id,name\n1,alice\n",
                       ContentType="text/csv")
        yield _service_with_moto(raw), "data"


def test_metadata(svc_and_bucket):
    svc, bucket = svc_and_bucket
    out = svc.metadata(1, bucket, "a/1.csv")
    assert out.size_bytes > 0
    assert out.content_type == "text/csv"


def test_row_count_csv_uses_select(svc_and_bucket, monkeypatch):
    svc, bucket = svc_and_bucket
    # S3 Select isn't supported by moto; stub the RowCounter select path.
    monkeypatch.setattr(
        "api.services.aws_s3_service.select_row_count", lambda *a, **k: 1
    )
    out = svc.row_count(1, bucket, "a/1.csv", "csv")
    assert out.row_count == 1
    assert out.engine == "s3_select"


def test_validate_format_drift_raises(svc_and_bucket):
    svc, bucket = svc_and_bucket
    with pytest.raises(SchemaValidationError):
        svc.validate_format(1, bucket, "a/1.csv", "csv",
                            expected_schema={"id": "int", "missing": "int"})
