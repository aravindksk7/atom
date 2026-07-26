from __future__ import annotations

import pyarrow.fs as pafs

from api.schemas import (
    FormatValidationOut,
    ObjectMetadataOut,
    PartitionEntryOut,
    PartitionSchemeOut,
    RowCountOut,
)
from api.services.aws_s3_runtime import AwsS3Runtime
from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.formats import validate_format
from etl_framework.aws_s3.metadata import read_object_metadata
from etl_framework.aws_s3.partitions import discover_partitions
from etl_framework.aws_s3.row_count import RowCounter, select_row_count
from etl_framework.repository.repository import ConfigRepository


class AwsS3Service:
    """Resolve AWS creds from a saved config and run aws_s3 checks."""

    def __init__(self, config_repo: ConfigRepository) -> None:
        self._runtime = AwsS3Runtime(config_repo)
        # Set in tests to bypass real boto3 session/client construction.
        self._s3_client_override = None

    def _client(self, config_id: int) -> S3Client:
        return self._runtime.client(config_id, self._s3_client_override)

    def _fs(self, config_id: int) -> pafs.FileSystem:
        return self._runtime.filesystem(config_id)

    def metadata(self, config_id: int, bucket: str, key: str) -> ObjectMetadataOut:
        m = read_object_metadata(self._client(config_id), bucket, key)
        return ObjectMetadataOut(**m.model_dump())

    def row_count(self, config_id: int, bucket: str, key: str, fmt: str) -> RowCountOut:
        client = self._client(config_id)
        if fmt in ("csv", "json"):
            # Call the module-level select_row_count directly (rather than via
            # RowCounter.count()) so it resolves through this module's globals —
            # RowCounter.count() would instead call the name bound in
            # etl_framework.aws_s3.row_count, which tests can't patch from here.
            n = select_row_count(client, bucket, key, fmt)
            return RowCountOut(bucket=bucket, key=key, fmt=fmt, row_count=n, engine="s3_select")
        r = RowCounter(client, fs=self._fs(config_id)).count(bucket, key, fmt)
        return RowCountOut(**r.model_dump())

    def partitions(self, config_id: int, bucket: str, prefix: str) -> PartitionSchemeOut:
        s = discover_partitions(self._client(config_id), bucket, prefix)
        return PartitionSchemeOut(
            columns=s.columns,
            entries=[PartitionEntryOut(**e.model_dump()) for e in s.entries],
        )

    def validate_format(
        self, config_id: int, bucket: str, key: str, fmt: str,
        expected_schema: dict[str, str] | None = None,
    ) -> FormatValidationOut:
        r = validate_format(self._client(config_id), bucket, key, fmt, expected_schema)
        return FormatValidationOut(**r.model_dump())
