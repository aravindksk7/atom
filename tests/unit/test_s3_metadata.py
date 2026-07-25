from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.metadata import read_object_metadata


@pytest.fixture
def s3_client():
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="data")
        raw.put_object(
            Bucket="data", Key="a/1.csv", Body=b"id\n1\n", ContentType="text/csv"
        )
        session = AWSSession(AWSConfig(region="us-east-1"))
        session._clients["s3"] = raw
        yield S3Client(session)


def test_reads_core_metadata(s3_client):
    m = read_object_metadata(s3_client, "data", "a/1.csv")
    assert m.bucket == "data"
    assert m.key == "a/1.csv"
    assert m.size_bytes == 5
    assert m.content_type == "text/csv"
    assert m.etag  # non-empty
    assert m.storage_class == "STANDARD"
    assert m.last_modified is not None
