from __future__ import annotations

from pydantic import BaseModel, field_validator, ConfigDict


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = ""
    db_host: str
    db_port: int = 1433
    db_name: str = ""
    db_user: str = ""
    db_password: str
    db_driver: str = "ODBC Driver 17 for SQL Server"
    db_pool_size: int = 5
    db_pool_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 3600
    db_connect_timeout: int = 15
    automic_url: str = ""
    automic_user: str = ""
    automic_password: str = ""
    automic_timeout: int = 30
    automic_max_retries: int = 3
    bo_url: str = ""
    bo_user: str = ""
    bo_password: str = ""
    bo_auth_type: str = "secEnterprise"
    bo_timeout: int = 60
    bo_proxy_url: str = ""
    bo_verify_ssl: bool = True

    @field_validator("db_port")
    @classmethod
    def validate_db_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"must be 1-65535, got {v}")
        return v

    @field_validator("db_pool_size")
    @classmethod
    def validate_pool_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"must be >= 1, got {v}")
        return v

    @field_validator("db_pool_overflow")
    @classmethod
    def validate_pool_overflow(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"must be >= 0, got {v}")
        return v

    @field_validator("automic_max_retries")
    @classmethod
    def validate_max_retries(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"must be >= 0, got {v}")
        return v

    @field_validator("bo_timeout")
    @classmethod
    def validate_bo_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"must be > 0, got {v}")
        return v

    @field_validator("bo_auth_type")
    @classmethod
    def validate_bo_auth_type(cls, v: str) -> str:
        valid = {"secEnterprise", "secWinAD", "secLDAP", "secSAPR3"}
        if v not in valid:
            raise ValueError(f"must be one of {sorted(valid)}, got {v!r}")
        return v

    @field_validator("automic_timeout", "db_connect_timeout", "db_pool_timeout")
    @classmethod
    def validate_positive_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"must be > 0, got {v}")
        return v

    @field_validator("db_pool_recycle")
    @classmethod
    def validate_pool_recycle(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"must be >= 0, got {v}")
        return v


class ConnectionEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    db_host: str | None = None
    db_port: int | None = None
    db_name: str | None = None
    db_user: str | None = None
    db_password: str | None = None
    db_driver: str | None = None
    db_pool_size: int | None = None
    db_pool_overflow: int | None = None
    db_pool_timeout: int | None = None
    db_pool_recycle: int | None = None
    db_connect_timeout: int | None = None


def resolve_connection(
    config_json: dict,
    name: str | None,
    env_name: str = "",
) -> EnvironmentConfig:
    """Return an EnvironmentConfig for a named connection, merging with top-level defaults."""
    base = {k: v for k, v in config_json.items() if k != "connections"}
    connections = config_json.get("connections") or {}
    if name is not None and name in connections:
        entry = connections[name]
        override = {k: v for k, v in entry.items() if v is not None}
        base.update(override)
        resolved_name = f"{env_name}/{name}" if env_name else name
    else:
        resolved_name = env_name
    return EnvironmentConfig(name=resolved_name, **base)
