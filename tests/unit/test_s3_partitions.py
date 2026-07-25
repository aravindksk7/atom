from __future__ import annotations

from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.models import RowCountResult
from etl_framework.aws_s3.partitions import discover_partitions


@pytest.fixture
def s3_client():
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="lake")
        for key in [
            "t/dt=2026-01-01/region=us/part-0.parquet",
            "t/dt=2026-01-01/region=us/part-1.parquet",
            "t/dt=2026-01-01/region=eu/part-0.parquet",
            "t/dt=2026-01-02/region=us/part-0.parquet",
        ]:
            raw.put_object(Bucket="lake", Key=key, Body=b"x")
        session = AWSSession(AWSConfig(region="us-east-1"))
        session._clients["s3"] = raw
        yield S3Client(session)


def test_discovers_partition_columns_in_order(s3_client):
    scheme = discover_partitions(s3_client, "lake", "t/")
    assert scheme.columns == ["dt", "region"]


def test_counts_objects_per_leaf_partition(s3_client):
    scheme = discover_partitions(s3_client, "lake", "t/")
    by_values = {tuple(e.values.items()): e.object_count for e in scheme.entries}
    assert by_values[(("dt", "2026-01-01"), ("region", "us"))] == 2
    assert by_values[(("dt", "2026-01-01"), ("region", "eu"))] == 1
    assert by_values[(("dt", "2026-01-02"), ("region", "us"))] == 1


def test_ignores_non_hive_keys(s3_client):
    # a stray non-partitioned object under the prefix must not create a column
    s3_client._s3.put_object(Bucket="lake", Key="t/_SUCCESS", Body=b"")
    scheme = discover_partitions(s3_client, "lake", "t/")
    assert scheme.columns == ["dt", "region"]


def test_attaches_summed_row_counts_per_partition(s3_client):
    # each object reports 10 rows; the 2-object leaf partition sums to 20.
    counter = MagicMock()
    counter.count.side_effect = lambda bucket, key, fmt: RowCountResult(
        bucket=bucket, key=key, fmt=fmt, row_count=10, engine="pyarrow_footer"
    )
    scheme = discover_partitions(s3_client, "lake", "t/", fmt="parquet", row_counter=counter)
    by_values = {tuple(e.values.items()): e.row_count for e in scheme.entries}
    assert by_values[(("dt", "2026-01-01"), ("region", "us"))] == 20
    assert by_values[(("dt", "2026-01-01"), ("region", "eu"))] == 10


def test_fmt_without_row_counter_raises(s3_client):
    with pytest.raises(ValueError, match="together"):
        discover_partitions(s3_client, "lake", "t/", fmt="parquet")
