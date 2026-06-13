"""Tests for DBEngine — real SQLAlchemy connection wrapper."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine, text

from etl_framework.config.models import EnvironmentConfig


@pytest.fixture
def cfg():
    return EnvironmentConfig(
        name="test_env",
        db_host="localhost",
        db_password="secret",
        db_name="testdb",
        db_user="sa",
    )


@pytest.fixture
def sqlite_engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO items VALUES (1, 'alpha')"))
        conn.execute(text("INSERT INTO items VALUES (2, 'beta')"))
        conn.commit()
    return engine


def test_db_engine_execute_query_returns_dataframe(cfg, sqlite_engine):
    from etl_framework.db.engine import DBEngine
    with patch("etl_framework.db.engine.create_engine", return_value=sqlite_engine):
        engine = DBEngine(cfg)
        df = engine.execute_query("SELECT * FROM items")
    assert len(df) == 2
    assert list(df.columns) == ["id", "name"]


def test_db_engine_execute_query_filters_correctly(cfg, sqlite_engine):
    from etl_framework.db.engine import DBEngine
    with patch("etl_framework.db.engine.create_engine", return_value=sqlite_engine):
        engine = DBEngine(cfg)
        df = engine.execute_query("SELECT * FROM items WHERE id = 1")
    assert len(df) == 1
    assert df.iloc[0]["name"] == "alpha"


def test_db_engine_env_name_set_from_config(cfg, sqlite_engine):
    from etl_framework.db.engine import DBEngine
    with patch("etl_framework.db.engine.create_engine", return_value=sqlite_engine):
        engine = DBEngine(cfg)
    assert engine._env.name == "test_env"


def test_db_engine_dispose_closes_pool(cfg, sqlite_engine):
    from etl_framework.db.engine import DBEngine
    with patch("etl_framework.db.engine.create_engine", return_value=sqlite_engine):
        engine = DBEngine(cfg)
    engine.dispose()  # must not raise


def test_db_engine_context_manager_returns_self(cfg, sqlite_engine):
    from etl_framework.db.engine import DBEngine
    with patch("etl_framework.db.engine.create_engine", return_value=sqlite_engine):
        engine = DBEngine(cfg)
    with engine as e:
        assert e is engine


def test_db_engine_build_connection_string_uses_config_fields(cfg):
    from etl_framework.db.engine import DBEngine
    captured = {}
    def fake_create_engine(conn_str, **kwargs):
        captured["conn_str"] = conn_str
        return MagicMock()
    with patch("etl_framework.db.engine.create_engine", side_effect=fake_create_engine):
        DBEngine(cfg)
    assert "localhost" in captured["conn_str"]
    assert "testdb" in captured["conn_str"]
    assert "sa" in captured["conn_str"]
