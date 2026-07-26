from __future__ import annotations

from typing import Any

import pyarrow.fs as pafs
from fastapi import HTTPException

from etl_framework.aws.config import aws_config_from_env
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.config.models import EnvironmentConfig
from etl_framework.repository.repository import ConfigRepository


class AwsS3Runtime:
    def __init__(self, config_repo: ConfigRepository) -> None:
        self._config_repo = config_repo

    def env(self, config_id: int) -> EnvironmentConfig:
        cfg = self._config_repo.get(config_id)
        if cfg is None:
            raise HTTPException(status_code=404, detail="Config not found")
        return EnvironmentConfig(name=cfg.env_name, **cfg.config_json)

    def client(self, config_id: int, override: Any | None = None) -> S3Client:
        if override is not None:
            session = AWSSession.__new__(AWSSession)
            session._cfg = None
            session._clients = {"s3": override}
            return S3Client(session)
        return S3Client(AWSSession(aws_config_from_env(self.env(config_id))))

    def filesystem(self, config_id: int) -> pafs.FileSystem:
        env = self.env(config_id)
        cfg = aws_config_from_env(env)
        kwargs: dict[str, Any] = {}
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
