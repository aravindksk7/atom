from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, ConfigDict

# Credential fields across EnvironmentConfig, ConnectionEntry, and
# ApiEndpointEntry. Shared by api/routes/configs.py (response masking) and
# ConfigRepository (encryption at rest) so both stay in sync with one list.
SECRET_FIELDS = frozenset({
    "db_password", "automic_password", "bo_password",
    "api_key", "bearer_token", "basic_password", "sap_bo_logon_token",
})


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


class ApiEndpointEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = ""
    base_url: str = ""
    path: str = ""
    method: Literal["GET", "POST"] = "GET"

    auth_type: Literal[
        "none",
        "api_key",
        "bearer",
        "basic",
        "sap_bo_logontoken",
        "sap_bo_basic",
    ] = "none"
    api_key_header: str = "X-API-Key"
    api_key: str = ""
    bearer_token: str = ""
    basic_username: str = ""
    basic_password: str = ""
    sap_bo_logon_token: str = ""
    sap_bo_auth_type: Literal["secEnterprise", "secWinAD", "secLDAP", "secSAPR3"] = "secEnterprise"
    sap_bo_logon_url: str = ""

    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | None = None

    timeout: int = 30
    verify_ssl: bool = True

    response_format: Literal["json", "csv", "xlsx", "xls"] = "json"
    json_root_path: str = ""

    pagination_type: Literal["none", "cursor", "page"] = "none"
    pagination_cursor_path: str = ""
    pagination_cursor_param: str = "cursor"
    pagination_page_param: str = "page"
    pagination_size_param: str = "limit"
    pagination_page_size: int = 100
    pagination_max_pages: int = Field(default=50, ge=1, le=1000)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        from urllib.parse import urlparse
        if v and not urlparse(v).scheme:
            raise ValueError("base_url must include http:// or https://")
        return v

    @field_validator("sap_bo_logon_url")
    @classmethod
    def validate_sap_bo_logon_url(cls, v: str) -> str:
        from urllib.parse import urlparse
        if v and not urlparse(v).scheme:
            raise ValueError("sap_bo_logon_url must include http:// or https://")
        return v

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"must be > 0, got {v}")
        return v


def resolve_api_endpoint(config_json: dict, name: str) -> ApiEndpointEntry:
    """Return the named API endpoint entry from a config's JSON blob."""
    endpoints = config_json.get("api_endpoints") or {}
    if name not in endpoints:
        raise ValueError(f"api_endpoints entry '{name}' not found in config")
    entry = dict(endpoints[name])
    if not entry.get("base_url"):
        base_host = config_json.get("api_base_host") or ""
        path = entry.get("path") or ""
        if not base_host or not path:
            raise ValueError(
                f"api_endpoints entry '{name}' must define base_url, "
                "or path plus a top-level api_base_host"
            )
        entry["base_url"] = base_host.rstrip("/") + "/" + path.lstrip("/")
    return ApiEndpointEntry(name=name, **entry)
