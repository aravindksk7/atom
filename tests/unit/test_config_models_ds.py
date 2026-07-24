from __future__ import annotations

import pytest

from etl_framework.config.models import EnvironmentConfig, SECRET_FIELDS


def test_ds_fields_default_to_empty():
    cfg = EnvironmentConfig(name="test", db_host="localhost", db_password="secret")
    assert cfg.ds_url == ""
    assert cfg.ds_user == ""
    assert cfg.ds_password == ""
    assert cfg.ds_repository == ""
    assert cfg.ds_timeout == 60
    assert cfg.ds_verify_ssl is True
    assert cfg.ds_proxy_url == ""


def test_ds_fields_can_be_set():
    cfg = EnvironmentConfig(
        name="test", db_host="localhost", db_password="secret",
        ds_url="http://ds-server:8080", ds_user="admin", ds_password="dspass",
        ds_repository="DS_REPO", ds_timeout=30, ds_verify_ssl=False,
        ds_proxy_url="http://proxy:8080",
    )
    assert cfg.ds_url == "http://ds-server:8080"
    assert cfg.ds_repository == "DS_REPO"
    assert cfg.ds_timeout == 30
    assert cfg.ds_verify_ssl is False


def test_ds_password_is_a_secret_field():
    assert "ds_password" in SECRET_FIELDS


def test_ds_timeout_must_be_positive():
    with pytest.raises(ValueError, match="must be > 0"):
        EnvironmentConfig(name="test", db_host="localhost", db_password="secret", ds_timeout=0)
