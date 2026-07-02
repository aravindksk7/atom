from __future__ import annotations

import os
import time

import pytest

pyodbc = pytest.importorskip("pyodbc")

from etl_framework.config.models import EnvironmentConfig
from etl_framework.db.engine import DBEngine
from etl_framework.reconciliation.engine import ReconciliationEngine
from etl_framework.runner.state import TestStatus


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_SQLSERVER_TESTS") != "1",
    reason="set RUN_LIVE_SQLSERVER_TESTS=1 and start docker-compose.integration.yml",
)


HOST = os.getenv("LIVE_SQLSERVER_HOST", "127.0.0.1")
PORT = int(os.getenv("LIVE_SQLSERVER_PORT", "14333"))
USER = os.getenv("LIVE_SQLSERVER_USER", "sa")
PASSWORD = os.getenv("LIVE_SQLSERVER_PASSWORD", "Atom_Test_12345!")
DRIVER = os.getenv("LIVE_SQLSERVER_ODBC_DRIVER", "SQL Server")


def _connect(database: str = "master", *, autocommit: bool = True):
    return pyodbc.connect(
        (
            f"DRIVER={{{DRIVER}}};"
            f"SERVER={HOST},{PORT};"
            f"DATABASE={database};"
            f"UID={USER};"
            f"PWD={PASSWORD};"
            "Connect Timeout=5;"
        ),
        autocommit=autocommit,
    )


def _wait_for_sqlserver() -> None:
    if DRIVER not in pyodbc.drivers():
        pytest.skip(f"ODBC driver {DRIVER!r} is not installed")

    last_error: Exception | None = None
    for _ in range(60):
        try:
            with _connect():
                return
        except Exception as exc:
            last_error = exc
            time.sleep(2)
    raise AssertionError(f"SQL Server did not become ready: {last_error}")


def _seed_database(db_name: str, rows: list[tuple[int, str, float]]) -> None:
    with _connect(db_name) as conn:
        cursor = conn.cursor()
        cursor.execute("IF OBJECT_ID('dbo.orders', 'U') IS NOT NULL DROP TABLE dbo.orders")
        cursor.execute(
            "CREATE TABLE dbo.orders ("
            "id INT NOT NULL PRIMARY KEY, "
            "sku NVARCHAR(50) NOT NULL, "
            "amount DECIMAL(10,2) NOT NULL)"
        )
        cursor.executemany(
            "INSERT INTO dbo.orders (id, sku, amount) VALUES (?, ?, ?)",
            rows,
        )


def test_reconciliation_uses_live_sqlserver_databases():
    _wait_for_sqlserver()

    source_db = "atom_live_src"
    target_db = "atom_live_tgt"
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(f"IF DB_ID('{source_db}') IS NULL CREATE DATABASE {source_db}")
        cursor.execute(f"IF DB_ID('{target_db}') IS NULL CREATE DATABASE {target_db}")

    _seed_database(
        source_db,
        [(1, "A100", 25.50), (2, "B200", 50.00), (3, "C300", 75.00)],
    )
    _seed_database(
        target_db,
        [(1, "A100", 25.50), (2, "B200", 55.00), (4, "D400", 99.00)],
    )

    base_config = {
        "db_host": HOST,
        "db_port": PORT,
        "db_user": USER,
        "db_password": PASSWORD,
        "db_driver": DRIVER,
        "db_connect_timeout": 5,
    }
    source_engine = DBEngine(
        EnvironmentConfig(name="live-src", db_name=source_db, **base_config)
    )
    target_engine = DBEngine(
        EnvironmentConfig(name="live-tgt", db_name=target_db, **base_config)
    )

    try:
        result = ReconciliationEngine(
            source_engine=source_engine,
            target_engine=target_engine,
            key_columns=["id"],
        ).reconcile(
            "SELECT id, sku, amount FROM dbo.orders",
            query_name="orders_live_sqlserver",
        )
    finally:
        source_engine.dispose()
        target_engine.dispose()

    assert result.status == TestStatus.FAILED
    assert result.source_row_count == 3
    assert result.target_row_count == 3
    assert result.matched_count == 2
    assert result.missing_in_target_count == 1
    assert result.missing_in_source_count == 1
    assert result.value_mismatch_count == 1
    assert {m.mismatch_type for m in result.mismatches} == {
        "missing_in_target",
        "missing_in_source",
        "value_diff",
    }
