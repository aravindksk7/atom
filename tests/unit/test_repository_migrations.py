from __future__ import annotations

from sqlalchemy import create_engine, text

from etl_framework.repository.migrations import (
    column_exists,
    ensure_column,
    ensure_index,
    ensure_table,
    execute_once,
    index_exists,
    table_exists,
)


def test_migration_helpers_are_idempotent():
    engine = create_engine("sqlite:///:memory:")

    assert ensure_table(engine, "items", "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY)")
    assert not ensure_table(engine, "items", "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY)")

    assert table_exists(engine, "items")
    assert not column_exists(engine, "items", "name")
    assert ensure_column(engine, "items", "name", "ALTER TABLE items ADD COLUMN name TEXT")
    assert column_exists(engine, "items", "name")
    assert not ensure_column(engine, "items", "name", "ALTER TABLE items ADD COLUMN name TEXT")

    assert ensure_index(engine, "ix_items_name", "CREATE INDEX ix_items_name ON items(name)")
    assert index_exists(engine, "ix_items_name")
    assert not ensure_index(engine, "ix_items_name", "CREATE INDEX ix_items_name ON items(name)")


def test_execute_once_accepts_active_connection():
    engine = create_engine("sqlite:///:memory:")
    ensure_table(engine, "items", "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)")
    with engine.begin() as conn:
        execute_once(conn, "INSERT INTO items (id, name) VALUES (1, 'one')")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name FROM items WHERE id = 1")).scalar_one() == "one"
