from unittest.mock import patch

from etl_framework.config.models import EnvironmentConfig
from etl_framework.db.engine import DBEngine
from etl_framework.db.sql_utils import quote_identifier
from etl_framework.reconciliation.chunker import build_chunk_query, build_hash_query


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


def test_quote_identifier_oracle():
    assert quote_identifier("id", "oracle") == '"id"'
    assert quote_identifier("MY_COL", "oracle") == '"MY_COL"'


def test_oracle_chunk_query():
    query = build_chunk_query("SELECT id, name FROM users", ["id"], 0, 100, dialect="oracle")
    assert '"id"' in query
    assert "OFFSET 0 ROWS FETCH NEXT 100 ROWS ONLY" in query


def test_oracle_connection_model_serialization():
    from etl_framework.repository.models import ConnectionConfig

    conn = ConnectionConfig(
        db_type="oracle",
        db_host="oracle.example.com",
        db_port=1521,
        db_name="ORCLPDB1",
        db_user="sys",
        db_password="password",
        db_driver="oracledb",
    )
    data = conn.model_dump()
    assert data["db_type"] == "oracle"
    assert data["db_port"] == 1521
    assert data["db_driver"] == "oracledb"


