"""PostgreSQL / Aurora PostgreSQL defaults on EnvironmentConfig and ConnectionEntry."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from etl_framework.config.models import ConnectionEntry, EnvironmentConfig, resolve_connection


def _env(**overrides) -> EnvironmentConfig:
    base = {"db_host": "pg.local", "db_password": "pw"}
    return EnvironmentConfig(**{**base, **overrides})


def test_postgresql_defaults_port_driver_and_prefer_sslmode():
    env = _env(db_type="postgresql")
    assert env.db_port == 5432
    assert env.db_driver == "psycopg"
    assert env.db_sslmode == "prefer"


def test_aurora_postgresql_defaults_to_require_sslmode():
    env = _env(db_type="aurora_postgresql")
    assert env.db_port == 5432
    assert env.db_driver == "psycopg"
    assert env.db_sslmode == "require"


def test_explicit_port_and_sslmode_are_kept():
    env = _env(db_type="aurora_postgresql", db_port=6543, db_sslmode="verify-full")
    assert env.db_port == 6543
    assert env.db_sslmode == "verify-full"


def test_sql_server_odbc_driver_is_replaced_for_postgres():
    env = _env(db_type="postgresql", db_driver="ODBC Driver 18 for SQL Server")
    assert env.db_driver == "psycopg"


def test_mssql_sslmode_stays_blank():
    assert _env().db_sslmode == ""


def test_unknown_db_type_rejected():
    with pytest.raises(ValidationError):
        _env(db_type="mysql")


def test_connection_entry_accepts_postgres_types():
    assert ConnectionEntry(db_type="aurora_postgresql", db_sslmode="require").db_type == "aurora_postgresql"


def test_named_connection_switches_base_mssql_config_to_postgres_defaults():
    config_json = {
        "db_type": "mssql", "db_host": "sql.local", "db_port": 1433, "db_password": "pw",
        "connections": {"warehouse": {"db_type": "postgresql", "db_host": "pg.local"}},
    }
    env = resolve_connection(config_json, "warehouse")
    assert env.db_type == "postgresql"
    assert env.db_port == 5432
    assert env.db_host == "pg.local"
    assert env.db_sslmode == "prefer"
