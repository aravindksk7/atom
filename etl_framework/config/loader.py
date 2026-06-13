import os
import re
import yaml
from pydantic import ValidationError
from etl_framework.config.models import EnvironmentConfig
from etl_framework.exceptions import ConfigurationError


class ConfigLoader:

    def load(self, config_path: str) -> dict[str, EnvironmentConfig]:
        if not os.path.exists(config_path):
            raise ConfigurationError(
                f"Config file not found: {config_path}", file_path=config_path
            )
        with open(config_path) as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            raise ConfigurationError(
                f"Config file '{config_path}' must be a YAML mapping at the top level, "
                f"got {type(raw).__name__}",
                file_path=config_path,
            )
        envs = {}
        for env_name, env_raw in (raw.get("environments") or {}).items():
            resolved = {k: self._resolve_env_vars(str(v)) if isinstance(v, str) else v
                        for k, v in env_raw.items()}
            try:
                env_config = EnvironmentConfig.model_validate({"name": env_name, **resolved})
            except ValidationError as exc:
                first_error = exc.errors()[0]
                field = ".".join(str(loc_part) for loc_part in first_error["loc"])
                msg = first_error["msg"]
                raise ConfigurationError(
                    f"Invalid value for field '{field}' in environment '{env_name}': {msg}",
                    field_name=field,
                ) from exc
            envs[env_name] = env_config
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
