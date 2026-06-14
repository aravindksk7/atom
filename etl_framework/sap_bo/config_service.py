import logging
import os
import tempfile
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from etl_framework.config.models import EnvironmentConfig
from etl_framework.config.loader import ConfigLoader
from etl_framework.exceptions import ConfigurationError

logger = logging.getLogger("api.services.config_service")

class ConfigService:
    def __init__(self, repository):
        self._repository = repository
        self._loader = ConfigLoader()

    def list_configs(self) -> list[dict[str, Any]]:
        return self._repository.list_configs()

    def get_config(self, name: str) -> dict[str, Any]:
        config = self._repository.get_config(name)
        if not config:
            raise HTTPException(status_code=404, detail=f"Configuration '{name}' not found.")
        return config

    def validate_config(self, name: str, raw_data: dict[str, Any]) -> None:
        try:
            EnvironmentConfig(name=name, **raw_data)
        except ValidationError as exc:
            first_error = exc.errors()[0]
            field = ".".join(str(l) for l in first_error["loc"])
            msg = first_error["msg"]
            raise ConfigurationError(
                f"Invalid value for field '{field}' in environment '{name}': {msg}",
                field_name=field,
            ) from exc

    def save_config(self, name: str, raw_data: dict[str, Any]) -> dict[str, Any]:
        self.validate_config(name, raw_data)
        self._repository.save_config(name, raw_data)
        return self.get_config(name)

    def delete_config(self, name: str) -> None:
        if not self._repository.get_config(name):
            raise HTTPException(status_code=404, detail=f"Configuration '{name}' not found.")
        self._repository.delete_config(name)

    def import_yaml(self, yaml_content: str) -> dict[str, dict[str, Any]]:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as tmp:
            tmp.write(yaml_content)
            tmp_path = tmp.name
            
        try:
            environments = self._loader.load(tmp_path)
            saved_envs = {}
            for env_name, env_config in environments.items():
                raw_data = env_config.model_dump()
                self._repository.save_config(env_name, raw_data)
                saved_envs[env_name] = raw_data
            return saved_envs
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)