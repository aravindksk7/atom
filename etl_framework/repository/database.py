from __future__ import annotations
import os
from pathlib import Path
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from etl_framework.repository.migrations import execute_once, ensure_column, ensure_index, ensure_table

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SQLITE_PATH = _REPO_ROOT / "etl_framework.db"
DATABASE_URL = os.environ.get(
    "ETL_DATABASE_URL", f"sqlite:///{_DEFAULT_SQLITE_PATH.as_posix()}"
)

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

    scheduled_run_cols = (
        {col["name"] for col in inspector.get_columns("scheduled_runs")}
        if "scheduled_runs" in tables else set()
    )

    with bind.begin() as conn:
        # --- original compare-tab columns ---
        ensure_column(conn, "test_runs", "run_type", "ALTER TABLE test_runs ADD COLUMN run_type VARCHAR(50) NOT NULL DEFAULT 'reconciliation'")
        ensure_column(conn, "test_runs", "pair_id", "ALTER TABLE test_runs ADD COLUMN pair_id VARCHAR(36)")
        ensure_index(conn, "ix_test_runs_pair_id", "CREATE INDEX IF NOT EXISTS ix_test_runs_pair_id ON test_runs (pair_id)")

        ensure_column(conn, "mismatch_details", "accepted", "ALTER TABLE mismatch_details ADD COLUMN accepted BOOLEAN NOT NULL DEFAULT 0")
        ensure_column(conn, "mismatch_details", "accepted_note", "ALTER TABLE mismatch_details ADD COLUMN accepted_note TEXT")
        ensure_column(conn, "mismatch_details", "accepted_at", "ALTER TABLE mismatch_details ADD COLUMN accepted_at DATETIME")
        ensure_column(conn, "mismatch_details", "accepted_by", "ALTER TABLE mismatch_details ADD COLUMN accepted_by VARCHAR(255)")
        ensure_column(conn, "mismatch_details", "rejected", "ALTER TABLE mismatch_details ADD COLUMN rejected BOOLEAN NOT NULL DEFAULT 0")
        ensure_column(conn, "mismatch_details", "rejected_note", "ALTER TABLE mismatch_details ADD COLUMN rejected_note TEXT")
        ensure_column(conn, "mismatch_details", "rejected_at", "ALTER TABLE mismatch_details ADD COLUMN rejected_at DATETIME")
        ensure_column(conn, "mismatch_details", "rejected_by", "ALTER TABLE mismatch_details ADD COLUMN rejected_by VARCHAR(255)")

        # --- pass-with-agreed-actions columns ---
        ensure_column(conn, "test_results", "override_status", "ALTER TABLE test_results ADD COLUMN override_status VARCHAR(20)")
        ensure_column(conn, "test_results", "override_reason", "ALTER TABLE test_results ADD COLUMN override_reason TEXT")
        ensure_column(conn, "test_results", "override_by", "ALTER TABLE test_results ADD COLUMN override_by VARCHAR(255)")
        ensure_column(conn, "test_results", "override_at", "ALTER TABLE test_results ADD COLUMN override_at DATETIME")
        ensure_column(conn, "test_results", "source_file_name", "ALTER TABLE test_results ADD COLUMN source_file_name VARCHAR(1024)")
        ensure_column(conn, "test_results", "target_file_name", "ALTER TABLE test_results ADD COLUMN target_file_name VARCHAR(1024)")
        ensure_column(conn, "test_results", "sample_rows", "ALTER TABLE test_results ADD COLUMN sample_rows JSON")
        ensure_column(conn, "test_results", "segment_summary", "ALTER TABLE test_results ADD COLUMN segment_summary JSON")
        ensure_column(conn, "test_results", "mismatch_summary", "ALTER TABLE test_results ADD COLUMN mismatch_summary JSON")
        ensure_column(conn, "test_results", "schema_diff", "ALTER TABLE test_results ADD COLUMN schema_diff JSON")
        ensure_column(conn, "mismatch_details", "delta", "ALTER TABLE mismatch_details ADD COLUMN delta FLOAT")
        ensure_column(conn, "mismatch_details", "relative_delta", "ALTER TABLE mismatch_details ADD COLUMN relative_delta FLOAT")

        ensure_table(conn, "difference_export_jobs",
            "CREATE TABLE IF NOT EXISTS difference_export_jobs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "export_id VARCHAR(36) NOT NULL UNIQUE, "
            "run_id VARCHAR(36) NOT NULL REFERENCES test_runs(run_id) ON DELETE CASCADE, "
            "format VARCHAR(20) NOT NULL, "
            "status VARCHAR(20) NOT NULL DEFAULT 'PENDING', "
            "artifact_path TEXT, "
            "row_count INTEGER NOT NULL DEFAULT 0, "
            "error_message TEXT, "
            "metadata_json JSON, "
            "created_at DATETIME NOT NULL, "
            "started_at DATETIME, "
            "completed_at DATETIME, "
            "recomputed_at DATETIME)"
        )
        ensure_index(conn, "ix_difference_export_jobs_export_id", "CREATE UNIQUE INDEX IF NOT EXISTS ix_difference_export_jobs_export_id ON difference_export_jobs (export_id)")
        ensure_index(conn, "ix_difference_export_jobs_run_id", "CREATE INDEX IF NOT EXISTS ix_difference_export_jobs_run_id ON difference_export_jobs (run_id)")

        # --- P0: new tables (created by create_all; ensure idempotent) ---
        ensure_table(conn, "api_tokens",
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
        )
        ensure_index(conn, "ix_api_tokens_token_hash", "CREATE INDEX IF NOT EXISTS ix_api_tokens_token_hash ON api_tokens (token_hash)")
        # --- Token auth hardening: is_admin + token_hint ---
        ensure_column(conn, "api_tokens", "is_admin", "ALTER TABLE api_tokens ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")
        ensure_column(conn, "api_tokens", "token_hint", "ALTER TABLE api_tokens ADD COLUMN token_hint VARCHAR(8) NOT NULL DEFAULT ''")
        ensure_table(conn, "notification_hooks",
            "CREATE TABLE IF NOT EXISTS notification_hooks ("
            "id INTEGER PRIMARY KEY, "
            "name VARCHAR(255) NOT NULL, "
            "url TEXT NOT NULL, "
            "events JSON, "
            "enabled BOOLEAN NOT NULL DEFAULT 1, "
            "secret TEXT, "
            "created_at DATETIME)"
        )
        ensure_table(conn, "scheduled_runs",
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
        )
        ensure_index(conn, "ix_scheduled_runs_name", "CREATE UNIQUE INDEX IF NOT EXISTS ix_scheduled_runs_name ON scheduled_runs (name)")
        ensure_table(conn, "scheduler_telemetry_events",
            "CREATE TABLE IF NOT EXISTS scheduler_telemetry_events ("
            "id INTEGER PRIMARY KEY, "
            "schedule_id INTEGER REFERENCES scheduled_runs(id) ON DELETE SET NULL, "
            "schedule_name VARCHAR(255) NOT NULL, "
            "job_name VARCHAR(255), "
            "selection_id INTEGER, "
            "selection_version INTEGER, "
            "run_id VARCHAR(36), "
            "event_state VARCHAR(32) NOT NULL, "
            "status VARCHAR(32) NOT NULL, "
            "exit_code INTEGER, "
            "started_at DATETIME, "
            "finished_at DATETIME, "
            "duration_ms INTEGER, "
            "error_summary TEXT, "
            "metadata_json JSON, "
            "created_at DATETIME NOT NULL)"
        )
        ensure_index(conn, "ix_scheduler_telemetry_events_schedule_id", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_schedule_id ON scheduler_telemetry_events (schedule_id)")
        ensure_index(conn, "ix_scheduler_telemetry_events_schedule_name", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_schedule_name ON scheduler_telemetry_events (schedule_name)")
        ensure_index(conn, "ix_scheduler_telemetry_events_job_name", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_job_name ON scheduler_telemetry_events (job_name)")
        ensure_index(conn, "ix_scheduler_telemetry_events_selection_id", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_selection_id ON scheduler_telemetry_events (selection_id)")
        ensure_index(conn, "ix_scheduler_telemetry_events_run_id", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_run_id ON scheduler_telemetry_events (run_id)")
        ensure_index(conn, "ix_scheduler_telemetry_events_event_state", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_event_state ON scheduler_telemetry_events (event_state)")
        ensure_index(conn, "ix_scheduler_telemetry_events_status", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_status ON scheduler_telemetry_events (status)")
        ensure_index(conn, "ix_scheduler_telemetry_events_exit_code", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_exit_code ON scheduler_telemetry_events (exit_code)")
        ensure_index(conn, "ix_scheduler_telemetry_events_started_at", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_started_at ON scheduler_telemetry_events (started_at)")
        ensure_index(conn, "ix_scheduler_telemetry_events_created_at", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_events_created_at ON scheduler_telemetry_events (created_at)")
        ensure_index(conn, "ix_scheduler_telemetry_schedule_created", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_schedule_created ON scheduler_telemetry_events (schedule_id, created_at)")
        ensure_index(conn, "ix_scheduler_telemetry_status_created", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_status_created ON scheduler_telemetry_events (status, created_at)")
        ensure_index(conn, "ix_scheduler_telemetry_state_created", "CREATE INDEX IF NOT EXISTS ix_scheduler_telemetry_state_created ON scheduler_telemetry_events (event_state, created_at)")

        # --- P3: job lineage table ---
        ensure_table(conn, "job_lineage_edges",
            "CREATE TABLE IF NOT EXISTS job_lineage_edges ("
            "id INTEGER PRIMARY KEY, "
            "upstream_job VARCHAR(255) NOT NULL, "
            "downstream_job VARCHAR(255) NOT NULL, "
            "edge_type VARCHAR(50) NOT NULL DEFAULT 'depends_on', "
            "created_at DATETIME)"
        )
        ensure_index(conn, "ix_job_lineage_upstream", "CREATE INDEX IF NOT EXISTS ix_job_lineage_upstream ON job_lineage_edges (upstream_job)")
        ensure_index(conn, "ix_job_lineage_downstream", "CREATE INDEX IF NOT EXISTS ix_job_lineage_downstream ON job_lineage_edges (downstream_job)")

        # --- Execution Sequence Scheduler: run_steps table ---
        ensure_table(conn, "run_steps",
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
        )
        ensure_index(conn, "ix_run_steps_run_id", "CREATE INDEX IF NOT EXISTS ix_run_steps_run_id ON run_steps (run_id)")

        # --- P2: is_baseline column on test_runs ---
        ensure_column(conn, "test_runs", "is_baseline", "ALTER TABLE test_runs ADD COLUMN is_baseline BOOLEAN NOT NULL DEFAULT 0")
        ensure_index(conn, "ix_test_runs_is_baseline", "CREATE INDEX IF NOT EXISTS ix_test_runs_is_baseline ON test_runs (is_baseline)")

        # --- Run cancellation: cancel_requested column on test_runs ---
        ensure_column(conn, "test_runs", "cancel_requested", "ALTER TABLE test_runs ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT 0")

        # --- ETL Capabilities: column_profiles + schema_snapshots tables ---
        ensure_table(conn, "column_profiles",
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
        )
        ensure_index(conn, "ix_column_profiles_job_name", "CREATE INDEX IF NOT EXISTS ix_column_profiles_job_name ON column_profiles (job_name)")
        ensure_table(conn, "schema_snapshots",
            "CREATE TABLE IF NOT EXISTS schema_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "job_name TEXT NOT NULL, "
            "environment TEXT NOT NULL DEFAULT 'both', "
            "run_id TEXT, "
            "captured_at DATETIME NOT NULL, "
            "columns TEXT NOT NULL DEFAULT '[]')"
        )
        ensure_index(conn, "ix_schema_snapshots_job_name", "CREATE INDEX IF NOT EXISTS ix_schema_snapshots_job_name ON schema_snapshots (job_name)")

        # --- Data Contracts tables ---
        ensure_table(conn, "contracts",
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
        )
        ensure_index(conn, "ix_contracts_name", "CREATE INDEX IF NOT EXISTS ix_contracts_name ON contracts (name)")
        ensure_index(conn, "ix_contracts_source_job", "CREATE INDEX IF NOT EXISTS ix_contracts_source_job ON contracts (source_job)")

        ensure_table(conn, "contract_versions",
            "CREATE TABLE IF NOT EXISTS contract_versions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "contract_id INTEGER NOT NULL REFERENCES contracts(id), "
            "version VARCHAR(50) NOT NULL, "
            "bump_type VARCHAR(10) NOT NULL, "
            "note TEXT, "
            "bumped_at DATETIME NOT NULL)"
        )
        ensure_index(conn, "ix_contract_versions_contract_id", "CREATE INDEX IF NOT EXISTS ix_contract_versions_contract_id ON contract_versions (contract_id)")

        ensure_table(conn, "contract_breaches",
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
        )
        ensure_index(conn, "ix_contract_breaches_contract_id", "CREATE INDEX IF NOT EXISTS ix_contract_breaches_contract_id ON contract_breaches (contract_id)")
        ensure_index(conn, "ix_contract_breaches_run_id", "CREATE INDEX IF NOT EXISTS ix_contract_breaches_run_id ON contract_breaches (run_id)")

        # --- Job Selections ---
        ensure_table(conn, "job_selections",
            "CREATE TABLE IF NOT EXISTS job_selections ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name VARCHAR(255) NOT NULL UNIQUE, "
            "description TEXT NOT NULL DEFAULT '', "
            "tags JSON, "
            "archived BOOLEAN NOT NULL DEFAULT 0, "
            "created_at DATETIME, "
            "updated_at DATETIME)"
        )
        ensure_index(conn, "ix_job_selections_name", "CREATE UNIQUE INDEX IF NOT EXISTS ix_job_selections_name ON job_selections (name)")
        ensure_table(conn, "job_selection_versions",
            "CREATE TABLE IF NOT EXISTS job_selection_versions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "selection_id INTEGER NOT NULL REFERENCES job_selections(id) ON DELETE CASCADE, "
            "version_number INTEGER NOT NULL, "
            "job_sequence JSON, "
            "run_settings_json JSON, "
            "created_at DATETIME)"
        )
        ensure_index(conn, "ix_job_selection_versions_selection_id", "CREATE INDEX IF NOT EXISTS ix_job_selection_versions_selection_id ON job_selection_versions (selection_id)")

        ensure_column(conn, "test_runs", "selection_id", "ALTER TABLE test_runs ADD COLUMN selection_id INTEGER")
        ensure_column(conn, "test_runs", "selection_version", "ALTER TABLE test_runs ADD COLUMN selection_version INTEGER")
        ensure_column(conn, "test_runs", "ci_context", "ALTER TABLE test_runs ADD COLUMN ci_context JSON")
        ensure_index(conn, "ix_test_runs_selection_id", "CREATE INDEX IF NOT EXISTS ix_test_runs_selection_id ON test_runs (selection_id)")

        if scheduled_run_cols:
            ensure_column(conn, "scheduled_runs", "selection_id", "ALTER TABLE scheduled_runs ADD COLUMN selection_id INTEGER")
            ensure_column(conn, "scheduled_runs", "selection_version", "ALTER TABLE scheduled_runs ADD COLUMN selection_version INTEGER")

        # --- App-wide settings (single row) ---
        ensure_table(conn, "app_settings",
            "CREATE TABLE IF NOT EXISTS app_settings ("
            "id INTEGER PRIMARY KEY, "
            "timezone VARCHAR(64) NOT NULL DEFAULT 'UTC', "
            "upload_retention_days INTEGER NOT NULL DEFAULT 30, "
            "updated_at DATETIME)"
        )
        execute_once(conn, "INSERT OR IGNORE INTO app_settings (id, timezone) VALUES (1, 'UTC')")
        ensure_column(conn, "app_settings", "upload_retention_days", "ALTER TABLE app_settings ADD COLUMN upload_retention_days INTEGER NOT NULL DEFAULT 30")


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
