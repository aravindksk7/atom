from __future__ import annotations

import pytest

from etl_framework.aws.config import AWSConfig


def test_defaults_are_empty_and_ssl_on():
    cfg = AWSConfig()
    assert cfg.region == ""
    assert cfg.profile == ""
    assert cfg.access_key_id == ""
    assert cfg.secret_access_key == ""
    assert cfg.session_token == ""
    assert cfg.endpoint_url == ""
    assert cfg.verify_ssl is True


def test_strips_whitespace():
    cfg = AWSConfig(region="  us-east-1  ")
    assert cfg.region == "us-east-1"


def test_endpoint_url_requires_scheme():
    with pytest.raises(ValueError, match="scheme"):
        AWSConfig(endpoint_url="localhost:5000")


def test_endpoint_url_with_scheme_ok():
    cfg = AWSConfig(endpoint_url="http://localhost:5000")
    assert cfg.endpoint_url == "http://localhost:5000"


def test_aws_secret_fields_registered_for_masking():
    from etl_framework.config.models import SECRET_FIELDS
    assert "secret_access_key" in SECRET_FIELDS
    assert "session_token" in SECRET_FIELDS
