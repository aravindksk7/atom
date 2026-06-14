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
    """Add Compare-tab columns to existing SQLite databases."""
    if bind.dialect.name != "sqlite":
        return

    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "test_runs" not in tables or "mismatch_details" not in tables:
        return

    test_run_cols = {col["name"] for col in inspector.get_columns("test_runs")}
    mismatch_cols = {col["name"] for col in inspector.get_columns("mismatch_details")}

    with bind.begin() as conn:
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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
