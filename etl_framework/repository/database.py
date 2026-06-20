from __future__ import annotations
import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.environ.get("ETL_DATABASE_URL", "sqlite:///./etl_framework.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from etl_framework.repository import models  # noqa: F401 — registers all ORM models
    Base.metadata.create_all(bind=engine)
    _ensure_compare_columns(engine)


def _ensure_compare_columns(bind) -> None:
    """Add new columns to existing SQLite databases (backward-compat shim)."""
    if bind.dialect.name != "sqlite":
        return

    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "test_runs" not in tables or "mismatch_details" not in tables:
        return

    test_run_cols = {col["name"] for col in inspector.get_columns("test_runs")}
    mismatch_cols = {col["name"] for col in inspector.get_columns("mismatch_details")}

    with bind.begin() as conn:
        # --- original compare-tab columns ---
        if "run_type" not in test_run_cols:
            conn.execute(text(
                "ALTER TABLE test_runs "
                "ADD COLUMN run_type VARCHAR(50) NOT NULL DEFAULT 'reconciliation'"
            ))
        if "pair_id" not in test_run_cols:
            conn.execute(text("ALTER TABLE test_runs ADD COLUMN pair_id VARCHAR(36)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_test_runs_pair_id ON test_runs (pair_id)"
        ))

        if "accepted" not in mismatch_cols:
            conn.execute(text(
                "ALTER TABLE mismatch_details "
                "ADD COLUMN accepted BOOLEAN NOT NULL DEFAULT 0"
            ))
        if "accepted_note" not in mismatch_cols:
            conn.execute(text("ALTER TABLE mismatch_details ADD COLUMN accepted_note TEXT"))
        if "accepted_at" not in mismatch_cols:
            conn.execute(text("ALTER TABLE mismatch_details ADD COLUMN accepted_at DATETIME"))
        if "accepted_by" not in mismatch_cols:
            conn.execute(text(
                "ALTER TABLE mismatch_details ADD COLUMN accepted_by VARCHAR(255)"
            ))

        # --- P0: new tables (created by create_all; ensure idempotent) ---
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS api_tokens ("
            "id INTEGER PRIMARY KEY, "
            "token_hash VARCHAR(64) NOT NULL UNIQUE, "
            "name VARCHAR(255) NOT NULL, "
            "created_at DATETIME, "
            "last_used_at DATETIME, "
            "expires_at DATETIME, "
            "enabled BOOLEAN NOT NULL DEFAULT 1, "
            "is_admin BOOLEAN NOT NULL DEFAULT 0, "
            "token_hint VARCHAR(8) NOT NULL DEFAULT '')"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_api_tokens_token_hash ON api_tokens (token_hash)"
        ))
        # --- Token auth hardening: is_admin + token_hint ---
        api_token_cols = {col["name"] for col in inspector.get_columns("api_tokens")} \
            if "api_tokens" in tables else set()
        if "is_admin" not in api_token_cols:
            conn.execute(text(
                "ALTER TABLE api_tokens ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"
            ))
        if "token_hint" not in api_token_cols:
            conn.execute(text(
                "ALTER TABLE api_tokens ADD COLUMN token_hint VARCHAR(8) NOT NULL DEFAULT ''"
            ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS notification_hooks ("
            "id INTEGER PRIMARY KEY, "
            "name VARCHAR(255) NOT NULL, "
            "url TEXT NOT NULL, "
            "events JSON, "
            "enabled BOOLEAN NOT NULL DEFAULT 1, "
            "secret TEXT, "
            "created_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS scheduled_runs ("
            "id INTEGER PRIMARY KEY, "
            "name VARCHAR(255) NOT NULL UNIQUE, "
            "cron_expr VARCHAR(100) NOT NULL, "
            "job_sequence JSON, "
            "source_env VARCHAR(100) NOT NULL DEFAULT '', "
            "target_env VARCHAR(100) NOT NULL DEFAULT '', "
            "run_settings_json JSON, "
            "enabled BOOLEAN NOT NULL DEFAULT 1, "
            "last_run_at DATETIME, "
            "next_run_at DATETIME, "
            "created_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_scheduled_runs_name ON scheduled_runs (name)"
        ))

        # --- P3: job lineage table ---
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS job_lineage_edges ("
            "id INTEGER PRIMARY KEY, "
            "upstream_job VARCHAR(255) NOT NULL, "
            "downstream_job VARCHAR(255) NOT NULL, "
            "edge_type VARCHAR(50) NOT NULL DEFAULT 'depends_on', "
            "created_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_job_lineage_upstream ON job_lineage_edges (upstream_job)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_job_lineage_downstream ON job_lineage_edges (downstream_job)"
        ))

        # --- P2: is_baseline column on test_runs ---
        if "is_baseline" not in test_run_cols:
            conn.execute(text(
                "ALTER TABLE test_runs ADD COLUMN is_baseline BOOLEAN NOT NULL DEFAULT 0"
            ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_test_runs_is_baseline ON test_runs (is_baseline)"
        ))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
