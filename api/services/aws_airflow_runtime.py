from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from etl_framework.airflow.client import AirflowRestClient
from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession
from etl_framework.config.models import EnvironmentConfig
from etl_framework.repository.repository import ConfigRepository


class AwsAirflowRuntime:
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

    def client(self, config_ref: int | str, override: Any | None = None) -> AirflowRestClient:
        if override is not None:
            return override
        cfg = self._config_repo.get(self.config_id(config_ref))
        if cfg is None:
            raise HTTPException(status_code=404, detail="Config not found")
        data = cfg.config_json or {}
        mwaa_environment = data.get("mwaa_environment")
        if mwaa_environment:
            return self._mwaa_client(data, str(mwaa_environment))
        return self._standalone_client(data)

    def _standalone_client(self, data: dict[str, Any]) -> AirflowRestClient:
        base_url = data.get("airflow_url") or data.get("airflow_base_url")
        if not base_url:
            raise HTTPException(
                status_code=400,
                detail="airflow_url is required for standalone Airflow config",
            )
        return AirflowRestClient(
            base_url=str(base_url),
            username=str(data["airflow_username"]) if data.get("airflow_username") else None,
            password=str(data["airflow_password"]) if data.get("airflow_password") else None,
        )

    def _mwaa_client(self, data: dict[str, Any], mwaa_environment: str) -> AirflowRestClient:
        aws_cfg = AWSConfig(
            region=data.get("aws_region") or "",
            access_key_id=data.get("aws_access_key_id") or "",
            secret_access_key=data.get("aws_secret_access_key") or "",
            session_token=data.get("aws_session_token") or "",
            endpoint_url=data.get("aws_endpoint_url") or "",
            verify_ssl=bool(data.get("aws_verify_ssl", True)),
        )
        mwaa = AWSSession(aws_cfg).client("mwaa")
        token = mwaa.create_web_login_token(Name=mwaa_environment)
        return AirflowRestClient(
            base_url=f"https://{token['WebServerHostname']}",
            token=token["WebToken"],
        )