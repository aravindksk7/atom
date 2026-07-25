from __future__ import annotations

from etl_framework.config.models import EnvironmentConfig, SECRET_FIELDS
from etl_framework.aws.config import AWSConfig, aws_config_from_env


def _env(**aws):
    return EnvironmentConfig(name="e", db_host="h", db_password="p", **aws)


def test_maps_all_aws_fields():
    env = _env(
        aws_region="us-east-1",
        aws_access_key_id="AKIA",
        aws_secret_access_key="secret",
        aws_session_token="tok",
        aws_endpoint_url="http://localhost:5000",
        aws_verify_ssl=False,
    )
    cfg = aws_config_from_env(env)
    assert isinstance(cfg, AWSConfig)
    assert cfg.region == "us-east-1"
    assert cfg.access_key_id == "AKIA"
    assert cfg.secret_access_key == "secret"
    assert cfg.session_token == "tok"
    assert cfg.endpoint_url == "http://localhost:5000"
    assert cfg.verify_ssl is False


def test_defaults_when_unset():
    cfg = aws_config_from_env(_env())
    assert cfg.region == ""
    assert cfg.verify_ssl is True


def test_prefixed_aws_secret_fields_registered():
    assert "aws_secret_access_key" in SECRET_FIELDS
    assert "aws_session_token" in SECRET_FIELDS
