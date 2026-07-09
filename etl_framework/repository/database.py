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
    from etl_framework.repository import contract_models  # noqa: F401 — registers contract ORM models
    Base.metadata.create_all(bind=engine)
    _ensure_compare_columns(engine)
    _backfill_schedule_selections(engine)


def _ensure_compare_columns(bind) -> None:
    """Add new columns to existing SQLite databases (backward-compat shim)."""
    if bind.dialect.name != "sqlite":
        return

    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if not {"test_runs", "test_results", "mismatch_details"}.issubset(tables):
        return

    test_run_cols = {col["name"] for col in inspector.get_columns("test_runs")}
    test_result_cols = {col["name"] for col in inspector.get_columns("test_results")}
    mismatch_cols = {col["name"] for col in inspector.get_columns("mismatch_details")}
    scheduled_run_cols = (
        {col["name"] for col in inspector.get_columns("scheduled_runs")}
        if "scheduled_runs" in tables else set()
    )

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

        # --- pass-with-agreed-actions columns ---
        if "override_status" not in test_result_cols:
            conn.execute(text("ALTER TABLE test_results ADD COLUMN override_status VARCHAR(20)"))
        if "override_reason" not in test_result_cols:
            conn.execute(text("ALTER TABLE test_results ADD COLUMN override_reason TEXT"))
        if "override_by" not in test_result_cols:
            conn.execute(text("ALTER TABLE test_results ADD COLUMN override_by VARCHAR(255)"))
        if "override_at" not in test_result_cols:
            conn.execute(text("ALTER TABLE test_results ADD COLUMN override_at DATETIME"))
        if "sample_rows" not in test_result_cols:
            conn.execute(text("ALTER TABLE test_results ADD COLUMN sample_rows JSON"))
        if "segment_summary" not in test_result_cols:
            conn.execute(text("ALTER TABLE test_results ADD COLUMN segment_summary JSON"))
        if "mismatch_summary" not in test_result_cols:
            conn.execute(text("ALTER TABLE test_results ADD COLUMN mismatch_summary JSON"))

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
        for _ddl in [
            "ALTER TABLE api_tokens ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE api_tokens ADD COLUMN token_hint VARCHAR(8) NOT NULL DEFAULT ''",
        ]:
            try:
                conn.execute(text(_ddl))
            except Exception:
                pass  # column already exists (fresh install or repeated startup)
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

        # --- Execution Sequence Scheduler: run_steps table ---
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS run_steps ("
            "id INTEGER PRIMARY KEY, "
            "run_id VARCHAR(36) REFERENCES test_runs(run_id) ON DELETE CASCADE, "
            "job_name VARCHAR(255) NOT NULL, "
            "step_index INTEGER NOT NULL, "
            "status VARCHAR(20) NOT NULL DEFAULT 'PENDING', "
            "hold_after BOOLEAN NOT NULL DEFAULT 0, "
            "condition JSON, "
            "wait_seconds INTEGER NOT NULL DEFAULT 0, "
            "held_at DATETIME, "
            "released_at DATETIME, "
            "released_by VARCHAR(255), "
            "release_note TEXT, "
            "release_action VARCHAR(20))"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_run_steps_run_id ON run_steps (run_id)"
        ))

        # --- P2: is_baseline column on test_runs ---
        if "is_baseline" not in test_run_cols:
            conn.execute(text(
                "ALTER TABLE test_runs ADD COLUMN is_baseline BOOLEAN NOT NULL DEFAULT 0"
            ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_test_runs_is_baseline ON test_runs (is_baseline)"
        ))

        # --- Run cancellation: cancel_requested column on test_runs ---
        if "cancel_requested" not in test_run_cols:
            conn.execute(text(
                "ALTER TABLE test_runs ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT 0"
            ))

        # --- ETL Capabilities: column_profiles + schema_snapshots tables ---
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS column_profiles ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "job_name TEXT NOT NULL, "
            "run_id TEXT, "
            "column_name TEXT NOT NULL, "
            "null_rate REAL, "
            "distinct_count INTEGER, "
            "min_val TEXT, "
            "max_val TEXT, "
            "mean_val REAL, "
            "std_val REAL, "
            "p25 REAL, "
            "p50 REAL, "
            "p75 REAL, "
            "p95 REAL, "
            "captured_at DATETIME NOT NULL)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_column_profiles_job_name ON column_profiles (job_name)"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "job_name TEXT NOT NULL, "
            "environment TEXT NOT NULL DEFAULT 'both', "
            "run_id TEXT, "
            "captured_at DATETIME NOT NULL, "
            "columns TEXT NOT NULL DEFAULT '[]')"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_schema_snapshots_job_name ON schema_snapshots (job_name)"
        ))

        # --- Data Contracts tables ---
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS contracts ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name VARCHAR(255) NOT NULL UNIQUE, "
            "version VARCHAR(50) NOT NULL DEFAULT '1.0', "
            "source_job VARCHAR(255) NOT NULL, "
            "owner VARCHAR(255) NOT NULL, "
            "sla_hours REAL NOT NULL, "
            "consumers TEXT NOT NULL DEFAULT '[]', "
            "breach_severity VARCHAR(10) NOT NULL DEFAULT 'error', "
            "active BOOLEAN NOT NULL DEFAULT 1, "
            "created_at DATETIME, "
            "updated_at DATETIME)"
        ))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_contracts_name ON contracts (name)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_contracts_source_job ON contracts (source_job)"))

        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS contract_versions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "contract_id INTEGER NOT NULL REFERENCES contracts(id), "
            "version VARCHAR(50) NOT NULL, "
            "bump_type VARCHAR(10) NOT NULL, "
            "note TEXT, "
            "bumped_at DATETIME NOT NULL)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_contract_versions_contract_id "
            "ON contract_versions (contract_id)"
        ))

        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS contract_breaches ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "contract_id INTEGER NOT NULL REFERENCES contracts(id), "
            "run_id VARCHAR(36) NOT NULL, "
            "breach_type VARCHAR(30) NOT NULL, "
            "opened_at DATETIME NOT NULL, "
            "resolved_at DATETIME, "
            "resolution_run_id VARCHAR(36), "
            "escalated BOOLEAN NOT NULL DEFAULT 0, "
            "escalated_at DATETIME, "
            "duration_hours REAL)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_contract_breaches_contract_id "
            "ON contract_breaches (contract_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_contract_breaches_run_id "
            "ON contract_breaches (run_id)"
        ))

        # --- Job Selections ---
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS job_selections ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name VARCHAR(255) NOT NULL UNIQUE, "
            "description TEXT NOT NULL DEFAULT '', "
            "tags JSON, "
            "archived BOOLEAN NOT NULL DEFAULT 0, "
            "created_at DATETIME, "
            "updated_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_job_selections_name ON job_selections (name)"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS job_selection_versions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "selection_id INTEGER NOT NULL REFERENCES job_selections(id) ON DELETE CASCADE, "
            "version_number INTEGER NOT NULL, "
            "job_sequence JSON, "
            "run_settings_json JSON, "
            "created_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_job_selection_versions_selection_id "
            "ON job_selection_versions (selection_id)"
        ))

        if "selection_id" not in test_run_cols:
            conn.execute(text("ALTER TABLE test_runs ADD COLUMN selection_id INTEGER"))
        if "selection_version" not in test_run_cols:
            conn.execute(text("ALTER TABLE test_runs ADD COLUMN selection_version INTEGER"))
        if "ci_context" not in test_run_cols:
            conn.execute(text("ALTER TABLE test_runs ADD COLUMN ci_context JSON"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_test_runs_selection_id ON test_runs (selection_id)"
        ))

        if scheduled_run_cols:
            if "selection_id" not in scheduled_run_cols:
                conn.execute(text("ALTER TABLE scheduled_runs ADD COLUMN selection_id INTEGER"))
            if "selection_version" not in scheduled_run_cols:
                conn.execute(text("ALTER TABLE scheduled_runs ADD COLUMN selection_version INTEGER"))

        # --- App-wide settings (single row) ---
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS app_settings ("
            "id INTEGER PRIMARY KEY, "
            "timezone VARCHAR(64) NOT NULL DEFAULT 'UTC', "
            "updated_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT OR IGNORE INTO app_settings (id, timezone) VALUES (1, 'UTC')"
        ))


def _backfill_schedule_selections(bind) -> None:
    """One-time backfill: give every pre-existing ScheduledRun row a JobSelection.

    Idempotent — only touches rows where selection_id is still NULL, so this
    is a no-op once every schedule has been migrated or created fresh.
    Disambiguates the synthesized name with a numeric suffix on collision,
    rather than crashing on the UNIQUE constraint or skipping the migration.
    """
    if bind.dialect.name != "sqlite":
        return
    inspector = inspect(bind)
    if "scheduled_runs" not in set(inspector.get_table_names()):
        return
    cols = {col["name"] for col in inspector.get_columns("scheduled_runs")}
    if "selection_id" not in cols:
        return

    from sqlalchemy.orm import Session
    from etl_framework.repository.models import ScheduledRun, JobSelection, JobSelectionVersion

    with Session(bind) as db:
        legacy = db.query(ScheduledRun).filter(ScheduledRun.selection_id.is_(None)).all()
        if not legacy:
            return
        for sched in legacy:
            base_name = f"{sched.name} (migrated)"
            candidate_name = base_name
            suffix = 2
            while db.query(JobSelection).filter_by(name=candidate_name).first() is not None:
                candidate_name = f"{sched.name} (migrated {suffix})"
                suffix += 1
            selection = JobSelection(
                name=candidate_name,
                description="Auto-created from a pre-existing schedule.",
            )
            db.add(selection)
            db.flush()
            db.add(JobSelectionVersion(
                selection_id=selection.id,
                version_number=1,
                job_sequence=sched.job_sequence or [],
                run_settings_json=sched.run_settings_json or {},
            ))
            sched.selection_id = selection.id
            sched.selection_version = 1
        db.commit()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
