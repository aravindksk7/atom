import pytest
from etl_framework.config.models import resolve_connection, EnvironmentConfig

BASE = {
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
        "finance_db": {
            "db_host": "finance-server",
            "db_name": "FIN",
            "db_user": "fin_user",
            "db_password": "fin_secret",
        },
    },
}


def test_none_name_returns_default_connection():
    env = resolve_connection(BASE, None, env_name="prod")
    assert env.db_host == "default-server"
    assert env.db_name == "default_db"
    assert env.name == "prod"


def test_unknown_name_falls_back_to_default():
    env = resolve_connection(BASE, "nonexistent", env_name="prod")
    assert env.db_host == "default-server"


def test_named_connection_overrides_host_and_db():
    env = resolve_connection(BASE, "hr_db", env_name="prod")
    assert env.db_host == "hr-server"
    assert env.db_name == "HR"
    assert env.db_user == "hr_user"
    assert env.db_password == "hr_secret"


def test_named_connection_inherits_unset_fields():
    env = resolve_connection(BASE, "hr_db", env_name="prod")
    assert env.db_port == 1433
    assert env.db_driver == "ODBC Driver 17 for SQL Server"
    assert env.db_pool_size == 5


def test_named_connection_name_is_qualified():
    env = resolve_connection(BASE, "hr_db", env_name="prod")
    assert env.name == "prod/hr_db"


def test_connections_key_not_passed_to_env_config():
    env = resolve_connection(BASE, None, env_name="prod")
    assert isinstance(env, EnvironmentConfig)


def test_config_without_connections_key():
    plain = {k: v for k, v in BASE.items() if k != "connections"}
    env = resolve_connection(plain, None, env_name="dev")
    assert env.db_host == "default-server"
