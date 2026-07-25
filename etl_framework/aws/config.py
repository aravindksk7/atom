from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator


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
