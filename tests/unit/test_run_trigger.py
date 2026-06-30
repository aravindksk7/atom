from __future__ import annotations
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from api.schemas import RunTrigger


BASE_CFG_JSON = {
    "db_host": "default-server",
    "db_port": 1433,
    "db_name": "default_db",
    "db_user": "sa",
    "db_password": "secret",
    "db_driver": "ODBC Driver 17 for SQL Server",
    "db_pool_size": 5,
    "db_pool_overflow": 10,
    "db_pool_timeout": 30,
    "db_pool_recycle": 3600,
    "db_connect_timeout": 15,
    "automic_url": "",
    "automic_user": "",
    "automic_password": "",
    "automic_timeout": 30,
    "automic_max_retries": 3,
    "bo_url": "",
    "bo_user": "",
    "bo_password": "",
    "bo_timeout": 60,
    "connections": {
        "hr_db": {
            "db_host": "hr-server",
            "db_name": "HR",
            "db_user": "hr_user",
            "db_password": "hr_secret",
        },
    },
}


def _make_cfg(config_json: dict):
    cfg = MagicMock()
    cfg.id = 1
    cfg.name = "prod"
    cfg.env_name = "prod"
    cfg.config_json = config_json
    return cfg


def _call_snapshot(body, cfg):
    from api.routes.runs import _snapshot_from_trigger
    db = MagicMock()
    with patch("api.routes.runs.ConfigRepository") as MockRepo:
        MockRepo.return_value.get.return_value = cfg
        return _snapshot_from_trigger(body, db)


def test_no_connection_uses_default():
    cfg = _make_cfg(BASE_CFG_JSON)
    body = RunTrigger(source_env="dev", target_env="prod", config_id=1)
    snapshot = _call_snapshot(body, cfg)
    assert snapshot["source_credentials"]["db_host"] == "default-server"
    assert snapshot["source_credentials"]["name"] == "dev"


def test_named_source_connection_overrides_host():
    cfg = _make_cfg(BASE_CFG_JSON)
    body = RunTrigger(source_env="dev", target_env="prod", config_id=1, source_connection="hr_db")
    snapshot = _call_snapshot(body, cfg)
    assert snapshot["source_credentials"]["db_host"] == "hr-server"
    assert snapshot["source_credentials"]["db_name"] == "HR"


def test_named_target_connection_overrides_host():
    cfg = _make_cfg(BASE_CFG_JSON)
    body = RunTrigger(source_env="dev", target_env="prod", config_id=1, target_connection="hr_db")
    snapshot = _call_snapshot(body, cfg)
    assert snapshot["target_credentials"]["db_host"] == "hr-server"


def test_default_connection_preserves_env_name():
    cfg = _make_cfg(BASE_CFG_JSON)
    body = RunTrigger(source_env="dev", target_env="prod", config_id=1)
    snapshot = _call_snapshot(body, cfg)
    assert snapshot["source_credentials"]["name"] == "dev"
    assert snapshot["target_credentials"]["name"] == "prod"


def test_unknown_connection_name_raises_422():
    cfg = _make_cfg(BASE_CFG_JSON)
    body = RunTrigger(source_env="dev", target_env="prod", config_id=1, source_connection="nonexistent")
    with pytest.raises(HTTPException) as exc_info:
        _call_snapshot(body, cfg)
    assert exc_info.value.status_code == 422
    assert "nonexistent" in str(exc_info.value.detail)
    assert "hr_db" in str(exc_info.value.detail)
