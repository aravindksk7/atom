"""Tests for DBEngine — real SQLAlchemy connection wrapper."""
from __future__ import annotations

import urllib.parse
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine, text

from etl_framework.config.models import EnvironmentConfig
from etl_framework.db.engine import DBEngine


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


def test_db_engine_netezza_nzpy_connection_url(monkeypatch):
    captured_urls = []

    def fake_create_engine(url, **kwargs):
        captured_urls.append(url)
        return MagicMock()

    monkeypatch.setattr("etl_framework.db.engine.create_engine", fake_create_engine)

    config = EnvironmentConfig(
        name="netezza_dev",
        db_type="netezza",
        db_host="netezza.host",
        db_port=5480,
        db_name="analytics",
        db_user="nz_user",
        db_password="nz_password",
        db_driver="nzpy",
    )
    engine = DBEngine(config)
    assert captured_urls[0] == "netezza+nzpy://nz_user:nz_password@netezza.host:5480/analytics"


def test_db_engine_netezza_pyodbc_connection_url(monkeypatch):
    captured_urls = []

    def fake_create_engine(url, **kwargs):
        captured_urls.append(url)
        return MagicMock()

    monkeypatch.setattr("etl_framework.db.engine.create_engine", fake_create_engine)

    config = EnvironmentConfig(
        name="netezza_dev",
        db_type="netezza",
        db_host="netezza.host",
        db_port=5480,
        db_name="analytics",
        db_user="nz_user",
        db_password="nz_password",
        db_driver="NetezzaSQL",
    )
    engine = DBEngine(config)
    assert captured_urls[0].startswith("netezza+pyodbc:///?odbc_connect=")
    assert "DRIVER%3D%7BNetezzaSQL%7D" in captured_urls[0] or "DRIVER={NetezzaSQL}" in urllib.parse.unquote(captured_urls[0])
    assert "SERVER%3Dnetezza.host" in captured_urls[0] or "SERVER=netezza.host" in urllib.parse.unquote(captured_urls[0])

