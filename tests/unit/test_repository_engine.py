"""ATOM's own SQLite repository must keep answering status polls during a long run."""
from __future__ import annotations

from sqlalchemy import text

from etl_framework.repository.database import _create_repository_engine


def _sqlite_engine(tmp_path):
    return _create_repository_engine(f"sqlite:///{(tmp_path / 'repo.db').as_posix()}")


def test_sqlite_connections_wait_for_a_busy_database(tmp_path):
    engine = _sqlite_engine(tmp_path)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30000
    finally:
        engine.dispose()


def test_sqlite_defaults_to_wal(tmp_path):
    engine = _sqlite_engine(tmp_path)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    finally:
        engine.dispose()


def test_journal_mode_can_be_overridden_for_network_filesystems(tmp_path, monkeypatch):
    monkeypatch.setenv("ETL_SQLITE_JOURNAL_MODE", "DELETE")
    engine = _sqlite_engine(tmp_path)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "delete"
    finally:
        engine.dispose()


def test_reader_is_not_blocked_while_a_writer_commits(tmp_path):
    """In the default rollback journal a writer's EXCLUSIVE lock blocks every
    reader, which is how a status poll got "database is locked"."""
    engine = _sqlite_engine(tmp_path)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE runs (status TEXT)"))
            conn.execute(text("INSERT INTO runs VALUES ('RUNNING')"))

        writer = engine.raw_connection()
        reader = engine.raw_connection()
        try:
            reader.execute("PRAGMA busy_timeout = 0")
            writer.execute("BEGIN EXCLUSIVE")
            writer.execute("UPDATE runs SET status = 'PASSED'")

            assert reader.execute("SELECT status FROM runs").fetchone()[0] == "RUNNING"

            writer.rollback()
        finally:
            writer.close()
            reader.close()
    finally:
        engine.dispose()


def test_other_databases_get_no_sqlite_settings():
    engine = _create_repository_engine("postgresql+psycopg://user:pw@localhost/atom")
    assert "timeout" not in engine.dialect.create_connect_args(engine.url)[1]
    engine.dispose()
