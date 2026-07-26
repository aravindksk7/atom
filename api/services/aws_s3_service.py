from __future__ import annotations

import pyarrow.fs as pafs
from fastapi import HTTPException

from api.schemas import (
    FormatValidationOut,
    ObjectMetadataOut,
    PartitionEntryOut,
    PartitionSchemeOut,
    RowCountOut,
)
from etl_framework.aws.config import aws_config_from_env
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.formats import validate_format
from etl_framework.aws_s3.metadata import read_object_metadata
from etl_framework.aws_s3.partitions import discover_partitions
from etl_framework.aws_s3.row_count import RowCounter, select_row_count
from etl_framework.config.models import EnvironmentConfig
from etl_framework.repository.repository import ConfigRepository


class AwsS3Service:
    """Resolve AWS creds from a saved config and run aws_s3 checks."""

    def __init__(self, config_repo: ConfigRepository) -> None:
        self._config_repo = config_repo
        # Set in tests to bypass real boto3 session/client construction.
        self._s3_client_override = None

    def _env(self, config_id: int) -> EnvironmentConfig:
        cfg = self._config_repo.get(config_id)
        if cfg is None:
            raise HTTPException(status_code=404, detail="Config not found")
        return EnvironmentConfig(name=cfg.env_name, **cfg.config_json)

    def _client(self, config_id: int) -> S3Client:
        if self._s3_client_override is not None:
            session = AWSSession.__new__(AWSSession)
            session._cfg = None
            session._clients = {"s3": self._s3_client_override}
            return S3Client(session)
        env = self._env(config_id)
        return S3Client(AWSSession(aws_config_from_env(env)))

    def _fs(self, config_id: int) -> pafs.FileSystem:
        env = self._env(config_id)
        cfg = aws_config_from_env(env)
        kwargs: dict = {}
        if cfg.region:
            kwargs["region"] = cfg.region
        if cfg.access_key_id:
            kwargs["access_key"] = cfg.access_key_id
            kwargs["secret_key"] = cfg.secret_access_key
            if cfg.session_token:
                kwargs["session_token"] = cfg.session_token
        if cfg.endpoint_url:
            kwargs["endpoint_override"] = cfg.endpoint_url
        return pafs.S3FileSystem(**kwargs)

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
