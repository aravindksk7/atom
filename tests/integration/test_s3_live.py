"""Live-AWS smoke test for the aws_s3 module.

Skipped unless ATOM_AWS_LIVE=1. Requires a real, readable bucket and a small
object set. Env vars:
  ATOM_AWS_LIVE=1
  ATOM_AWS_S3_BUCKET=<bucket>
  ATOM_AWS_S3_CSV_KEY=<key of a small csv object>
  AWS_REGION / standard boto3 credential env vars
"""
from __future__ import annotations

import os

import pyarrow.fs as pafs
import pytest

from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.metadata import read_object_metadata
from etl_framework.aws_s3.row_count import RowCounter

pytestmark = pytest.mark.skipif(
    os.environ.get("ATOM_AWS_LIVE") != "1",
    reason="live AWS tests disabled (set ATOM_AWS_LIVE=1 to enable)",
)


@pytest.fixture
def live_client():
    cfg = AWSConfig(region=os.environ.get("AWS_REGION", "us-east-1"))
    return S3Client(AWSSession(cfg))


def test_live_metadata_and_row_count(live_client):
    bucket = os.environ["ATOM_AWS_S3_BUCKET"]
    key = os.environ["ATOM_AWS_S3_CSV_KEY"]

    meta = read_object_metadata(live_client, bucket, key)
    assert meta.size_bytes > 0

    counter = RowCounter(live_client, fs=pafs.LocalFileSystem())
    result = counter.count(bucket, key, "csv")
    assert result.engine == "s3_select"
    assert result.row_count >= 0
