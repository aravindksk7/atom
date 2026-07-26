from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator

if TYPE_CHECKING:
    from etl_framework.config.models import EnvironmentConfig


class AWSConfig(BaseModel):
    """Connection/credential config for AWS service clients.

    Mirrors EnvironmentConfig style. Leave keys empty to fall back to the
    default boto3 credential chain (env vars, shared config, instance role).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    region: str = ""
    profile: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    session_token: str = ""
    endpoint_url: str = ""
    verify_ssl: bool = True

    @field_validator("endpoint_url")
    @classmethod
    def validate_endpoint_url(cls, v: str) -> str:
        if v:
            parsed = urlparse(v)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError("endpoint_url must include a scheme (http:// or https://)")
        return v


def aws_config_from_env(env: "EnvironmentConfig") -> AWSConfig:
    """Build an AWSConfig from the aws_* fields of a saved EnvironmentConfig."""
    return AWSConfig(
        region=env.aws_region,
        access_key_id=env.aws_access_key_id,
        secret_access_key=env.aws_secret_access_key,
        session_token=env.aws_session_token,
        endpoint_url=env.aws_endpoint_url,
        verify_ssl=env.aws_verify_ssl,
    )
