from unittest.mock import patch

from etl_framework.config.models import EnvironmentConfig
from etl_framework.db.engine import DBEngine


def test_oracle_environment_config_defaults():
    config = EnvironmentConfig(
        name="test_oracle",
        db_type="oracle",
        db_host="localhost",
        db_name="ORCLPDB1",
        db_user="sys",
        db_password="password",
    )
    assert config.db_type == "oracle"
    assert config.db_port == 1521
    assert config.db_driver == "oracledb"


def test_db_engine_oracle_connection_string():
    config = EnvironmentConfig(
        name="test_oracle",
        db_type="oracle",
        db_host="oracle-server",
        db_port=1521,
        db_name="ORCLPDB1",
        db_user="admin",
        db_password="p@ssword#123",
    )
    with patch("etl_framework.db.engine.create_engine") as mock_create_engine:
        engine = DBEngine(config)
        mock_create_engine.assert_called_once()
        connection_url = mock_create_engine.call_args[0][0]
        assert connection_url.startswith("oracle+oracledb://admin:p%40ssword%23123@oracle-server:1521/?service_name=ORCLPDB1")

