from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.exceptions import S3ObjectNotFoundError


@pytest.fixture
def s3_client():
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="data")
        raw.put_object(Bucket="data", Key="a/1.csv", Body=b"id\n1\n")
        raw.put_object(Bucket="data", Key="a/2.csv", Body=b"id\n2\n")
        session = AWSSession(AWSConfig(region="us-east-1"))
        session._clients["s3"] = raw  # inject the moto-backed client
        yield S3Client(session)


def test_list_objects_paginates(s3_client):
    keys = [o["Key"] for o in s3_client.list_objects("data", "a/")]
    assert keys == ["a/1.csv", "a/2.csv"]


def test_head_object_returns_dict(s3_client):
    head = s3_client.head_object("data", "a/1.csv")
    assert head["ContentLength"] == 5


def test_head_object_missing_raises_typed(s3_client):
    with pytest.raises(S3ObjectNotFoundError):
        s3_client.head_object("data", "a/missing.csv")


def test_get_object_body(s3_client):
    body = s3_client.get_object("data", "a/1.csv")
    assert body == b"id\n1\n"
