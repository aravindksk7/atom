import os
import re
import yaml
from etl_framework.config.models import EnvironmentConfig
from etl_framework.exceptions import ConfigurationError


class ConfigLoader:
    REQUIRED_DB_FIELDS = ["db_host", "db_name", "db_user", "db_password"]

    def load(self, config_path: str) -> dict[str, EnvironmentConfig]:
        if not os.path.exists(config_path):
            raise ConfigurationError(
                f"Config file not found: {config_path}", file_path=config_path
            )
        with open(config_path) as f:
            raw = yaml.safe_load(f)
        envs = {}
        for env_name, env_raw in (raw.get("environments") or {}).items():
            resolved = {k: self._resolve_env_vars(str(v)) if isinstance(v, str) else v
                        for k, v in env_raw.items()}
            self._validate_env_config(env_name, resolved)
            envs[env_name] = EnvironmentConfig(name=env_name, **resolved)
        return envs

    def _resolve_env_vars(self, value: str) -> str:
        def resolver(match):
            var = match.group(1)
            val = os.environ.get(var)
            if val is None:
                raise ConfigurationError(
                    f"Environment variable '${{{var}}}' is not set",
                    field_name=var,
                )
            return val
        return re.sub(r'\$\{(\w+)\}', resolver, value)

    def _validate_env_config(self, name: str, raw: dict) -> None:
        for field in self.REQUIRED_DB_FIELDS:
            if field not in raw:
                raise ConfigurationError(
                    f"Missing required field '{field}' in environment '{name}'",
                    field_name=field,
                )
