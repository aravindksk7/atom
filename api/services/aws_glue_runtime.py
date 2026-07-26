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

    def config_id(self, config_ref: int | str) -> int:
        try:
            return int(config_ref)
        except (TypeError, ValueError):
            cfg = self._config_repo.get_by_name(str(config_ref))
            if cfg is None:
                raise HTTPException(status_code=404, detail="Config not found")
            return int(cfg.id)

    def env(self, config_ref: int | str) -> EnvironmentConfig:
        cfg = self._config_repo.get(self.config_id(config_ref))
        if cfg is None:
            raise HTTPException(status_code=404, detail="Config not found")
        return EnvironmentConfig(name=cfg.env_name, **cfg.config_json)

    def client(self, config_ref: int | str, override: Any | None = None) -> Any:
        if override is not None:
            return override
        return AWSSession(aws_config_from_env(self.env(config_ref))).client("glue")
