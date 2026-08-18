from etl_framework.config.models import EnvironmentConfig


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
