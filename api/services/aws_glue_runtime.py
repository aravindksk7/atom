from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from etl_framework.aws.config import aws_config_from_env
from etl_framework.aws.session import AWSSession
from etl_framework.config.models import EnvironmentConfig
from etl_framework.repository.repository import ConfigRepository


class AwsGlueRuntime:
    def __init__(self, config_repo: ConfigRepository) -> None:
        self._config_repo = config_repo

    def env(self, config_id: int) -> EnvironmentConfig:
        cfg = self._config_repo.get(config_id)
        if cfg is None:
            raise HTTPException(status_code=404, detail="Config not found")
        return EnvironmentConfig(name=cfg.env_name, **cfg.config_json)

    def client(self, config_id: int, override: Any | None = None) -> Any:
        if override is not None:
            return override
        return AWSSession(aws_config_from_env(self.env(config_id))).client("glue")
