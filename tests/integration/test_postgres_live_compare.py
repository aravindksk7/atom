"""Live PostgreSQL: engine, cursor batches, and a bulk-imported sql compare job end to end.

Start: docker compose -f docker-compose.integration.yml up -d postgres
Run:   RUN_LIVE_POSTGRES_TESTS=1 python -m pytest tests/integration/test_postgres_live_compare.py -v
"""
from __future__ import annotations

import os
import time

import pytest

psycopg = pytest.importorskip("psycopg")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import RunSettings
from api.services import compare_bulk_import as bulk
from api.services.run_executor import RunExecutor
from etl_framework.config.models import EnvironmentConfig
from etl_framework.db.engine import DBEngine
from etl_framework.reconciliation.chunker import load_in_chunks
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import ConfigRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_POSTGRES_TESTS") != "1",
    reason="set RUN_LIVE_POSTGRES_TESTS=1 and start the postgres service in docker-compose.integration.yml",
)

HOST = os.getenv("LIVE_POSTGRES_HOST", "127.0.0.1")
PORT = int(os.getenv("LIVE_POSTGRES_PORT", "25432"))
USER, PASSWORD, DB = "atom", "Atom_Test_12345", "atom_live"


def _connect():
    return psycopg.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=DB, autocommit=True)


@pytest.fixture(scope="module", autouse=True)
def seeded():
    last: Exception | None = None
    for _ in range(60):
        try:
            with _connect():
                break
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2)
    else:
        raise AssertionError(f"Postgres did not become ready: {last}")
    with _connect() as conn:
        for schema, rows in (
            ("src", [(i, f"C{i}", float(i)) for i in range(1, 8)]),
            ("tgt", [(i, f"C{i}", float(i) + (0.5 if i == 3 else 0)) for i in range(1, 7)] + [(9, "C9", 9.0)]),
        ):
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            conn.execute(f"CREATE SCHEMA {schema}")
            conn.execute(f"CREATE TABLE {schema}.customer (cust_id int PRIMARY KEY, name text, amount numeric(10,2))")
            with conn.cursor() as cur:
                cur.executemany(f'INSERT INTO {schema}.customer VALUES (%s, %s, %s)', rows)


def _env(**overrides) -> EnvironmentConfig:
    base = dict(name="live-pg", db_type="postgresql", db_host=HOST, db_port=PORT,
                db_user=USER, db_password=PASSWORD, db_name=DB, db_sslmode="disable")
    return EnvironmentConfig(**{**base, **overrides})


def test_cursor_batches_read_every_row():
    engine = DBEngine(_env())
    try:
        df = load_in_chunks(engine, "SELECT * FROM src.customer", [], 3)
    finally:
        engine.dispose()
    assert len(df) == 7


def test_aurora_type_connects_with_sslmode_override():
    engine = DBEngine(_env(db_type="aurora_postgresql", db_sslmode="disable"))
    try:
        assert int(engine.execute_query("SELECT 1 AS one")["one"][0]) == 1
    finally:
        engine.dispose()


def test_bulk_imported_sql_compare_job_runs_end_to_end():
    app_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(app_engine)
    db = Session(app_engine)
    cfg_json = {"db_type": "postgresql", "db_host": HOST, "db_port": PORT, "db_user": USER,
                "db_password": PASSWORD, "db_name": DB, "db_sslmode": "disable"}
    ConfigRepository(db).create("PG_SRC", "dev", cfg_json)
    ConfigRepository(db).create("PG_TGT", "dev", {**cfg_json, "db_type": "aurora_postgresql"})
    configs = {c.name: c for c in ConfigRepository(db).list()}

    sheet = (
        "job_name,source_config,source_table,target_config,target_table,key_columns,compare_columns,chunk_size\n"
        "cust_cmp,PG_SRC,src.customer,PG_TGT,tgt.customer,cust_id,name;amount,2\n"
    )
    rows = bulk.read_sheet("m.csv", sheet.encode())
    preview = bulk.preview_rows(rows, configs, existing_job_names=set())
    assert preview["invalid_count"] == 0, preview["rows"][0]["errors"]
    job = bulk.JobDefinition.model_validate(preview["rows"][0]["job"])

    result = RunExecutor(
        db=db, run_id="live-1", source_env="src", target_env="tgt", job_sequence=[],
        run_settings=RunSettings(use_live_connections=True), config_snapshot={},
    )._build_case(job)()

    assert result.query_name == "cust_cmp"
    assert result.missing_in_target_count == 1   # cust_id 7
    assert result.missing_in_source_count == 1   # cust_id 9
    assert result.value_mismatch_count == 1      # cust_id 3 amount
