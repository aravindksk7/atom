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



# --- Netezza dialect resolution -------------------------------------------
#
# The two tests above monkeypatch create_engine, so they assert only that the
# URL *string* is shaped right — they never ask SQLAlchemy whether a "netezza"
# dialect actually exists. It does not: nzpy is a bare PEP-249 driver with no
# sqlalchemy.dialects entry point, so on-prem these URLs died with
# "NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:netezza.nzpy"
# while CI stayed green. These tests resolve the dialect for real.

_NZ_DIALECT_HINT = (
    "Netezza support requires the 'nzalchemy' SQLAlchemy dialect "
    "(pip install 'etl-framework[netezza]'), not just the nzpy driver."
)


def test_netezza_nzpy_dialect_is_registered():
    """netezza+nzpy must resolve to a real dialect, not just build a URL."""
    pytest.importorskip("nzalchemy", reason=_NZ_DIALECT_HINT)
    from etl_framework.db import engine as engine_module

    engine_module._ensure_netezza_dialect()
    engine = create_engine("netezza+nzpy://u:p@nz.host:5480/analytics")
    assert engine.dialect is not None


def test_netezza_pyodbc_dialect_is_registered():
    """netezza+pyodbc must resolve too.

    nzalchemy's pyodbc module does `from sqlalchemy import processors`, which
    SQLAlchemy 2.0 removed — so this fails with ImportError unless
    _ensure_netezza_dialect() installs the compatibility alias first.
    """
    pytest.importorskip("nzalchemy", reason=_NZ_DIALECT_HINT)
    pytest.importorskip("pyodbc")
    from etl_framework.db import engine as engine_module

    engine_module._ensure_netezza_dialect()
    params = urllib.parse.quote_plus("DRIVER={NetezzaSQL};SERVER=nz.host;")
    engine = create_engine(f"netezza+pyodbc:///?odbc_connect={params}")
    assert engine.dialect is not None


# --- nzalchemy logging containment ----------------------------------------
#
# nzalchemy/base.py calls logging.basicConfig(level=DEBUG,
# filename='nzalchemy.log') at *import* time. That attaches a DEBUG FileHandler
# to the ROOT logger and drops a log file into whatever directory the process
# happens to be running from — so importing the dialect would silently
# reconfigure the whole application's logging and litter the working
# directory. _preserve_root_logging() must undo both.

def test_preserve_root_logging_restores_handlers_and_level(tmp_path):
    import logging
    from etl_framework.db.engine import _preserve_root_logging

    root = logging.getLogger()
    before_handlers = root.handlers[:]
    before_level = root.level
    log_file = tmp_path / "nzalchemy.log"

    with _preserve_root_logging():
        logging.basicConfig(
            level=logging.DEBUG, filename=str(log_file), force=True,
        )
        assert root.handlers != before_handlers  # basicConfig did take effect

    assert root.handlers == before_handlers
    assert root.level == before_level


def test_preserve_root_logging_closes_the_handler_it_removes(tmp_path):
    """An unclosed FileHandler keeps the log file open for writing."""
    import logging
    from etl_framework.db.engine import _preserve_root_logging

    log_file = tmp_path / "nzalchemy.log"
    with _preserve_root_logging():
        logging.basicConfig(
            level=logging.DEBUG, filename=str(log_file), force=True,
        )
        added = [h for h in logging.getLogger().handlers
                 if isinstance(h, logging.FileHandler)]
        assert added

    assert all(h.stream is None or h.stream.closed for h in added)


def test_preserve_root_logging_prevents_the_log_file_from_being_created(tmp_path, monkeypatch):
    """Restoring handlers afterwards is not enough — basicConfig opens the
    file as soon as its FileHandler is built. Park a handler so basicConfig
    no-ops and the file is never created."""
    import logging
    from etl_framework.db.engine import _preserve_root_logging

    monkeypatch.chdir(tmp_path)
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers[:] = []  # simulate a CLI entrypoint before logging is set up
    try:
        with _preserve_root_logging():
            # exactly what nzalchemy/base.py does at import time
            logging.basicConfig(level=logging.DEBUG, filename="nzalchemy.log")
            logging.getLogger().debug("isolation_level : READ COMMITTED")
    finally:
        root.handlers[:] = saved

    assert not (tmp_path / "nzalchemy.log").exists()
