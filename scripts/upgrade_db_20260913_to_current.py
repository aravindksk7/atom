from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


BASELINE_COMMIT = "7eeb90b49eb8ddbd4fadcd3d3afe1465918ec3f5"
REQUIRED_BASELINE_TABLES = frozenset({"test_runs", "scheduled_runs", "run_steps"})


class UpgradeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Change:
    kind: str
    name: str
    ddl: str
    table: str | None = None


@dataclass(frozen=True)
class UpgradeResult:
    planned: tuple[Change, ...]
    applied: tuple[Change, ...]
    already_present: tuple[Change, ...]
    backup_path: Path | None


@dataclass(frozen=True)
class ColumnSpec:
    affinity: str
    not_null: bool = False
    default: str | None = None
    primary_key: int = 0


BASELINE_COLUMNS = {
    "test_runs": {
        "id": "INTEGER", "run_id": "TEXT", "status": "TEXT", "started_at": "NUMERIC",
        "completed_at": "NUMERIC", "source_env": "TEXT", "target_env": "TEXT",
        "config_snapshot": "NUMERIC", "total_tests": "INTEGER", "passed": "INTEGER",
        "failed": "INTEGER", "slow": "INTEGER", "error": "INTEGER", "run_type": "TEXT",
        "pair_id": "TEXT", "is_baseline": "NUMERIC", "cancel_requested": "NUMERIC",
        "selection_id": "INTEGER", "selection_version": "INTEGER", "ci_context": "NUMERIC",
    },
    "scheduled_runs": {
        "id": "INTEGER", "name": "TEXT", "cron_expr": "TEXT", "job_sequence": "NUMERIC",
        "source_env": "TEXT", "target_env": "TEXT", "run_settings_json": "NUMERIC",
        "enabled": "NUMERIC", "last_run_at": "NUMERIC", "next_run_at": "NUMERIC",
        "created_at": "NUMERIC", "selection_id": "INTEGER", "selection_version": "INTEGER",
        "sequence_id": "INTEGER", "sequence_version": "INTEGER",
    },
    "run_steps": {
        "id": "INTEGER", "run_id": "TEXT", "job_name": "TEXT", "step_index": "INTEGER",
        "status": "TEXT", "hold_after": "NUMERIC", "condition": "NUMERIC",
        "wait_seconds": "INTEGER", "step_id": "TEXT", "depends_on": "NUMERIC",
        "trigger_rule": "TEXT", "attempt": "INTEGER", "max_retries": "INTEGER",
        "on_failure": "TEXT", "held_at": "NUMERIC", "released_at": "NUMERIC",
        "released_by": "TEXT", "release_note": "TEXT", "release_action": "TEXT",
    },
}

RUN_BATCH_COLUMNS = {
    "id": ColumnSpec("INTEGER", primary_key=1),
    "batch_id": ColumnSpec("TEXT", True),
    "target_type": ColumnSpec("TEXT", True),
    "target_id": ColumnSpec("INTEGER", True),
    "status": ColumnSpec("TEXT", True, "'RUNNING'"),
    "variable_name": ColumnSpec("TEXT", True),
    "start_value": ColumnSpec("TEXT", True),
    "iterations": ColumnSpec("INTEGER", True),
    "step_days": ColumnSpec("INTEGER", True, "1"),
    "weekend_policy": ColumnSpec("TEXT", True, "'skip'"),
    "stop_on_failure": ColumnSpec("NUMERIC", True, "0"),
    "completed": ColumnSpec("INTEGER", True, "0"),
    "current_iteration": ColumnSpec("INTEGER"),
    "current_value": ColumnSpec("TEXT"),
    "created_at": ColumnSpec("NUMERIC", True),
    "completed_at": ColumnSpec("NUMERIC"),
}

FILE_SERVER_PROFILE_COLUMNS = {
    "id": ColumnSpec("INTEGER", primary_key=1),
    "name": ColumnSpec("TEXT", True),
    "kind": ColumnSpec("TEXT", True),
    "description": ColumnSpec("TEXT", True, "''"),
    "host": ColumnSpec("TEXT"),
    "port": ColumnSpec("INTEGER", True, "22"),
    "username": ColumnSpec("TEXT"),
    "auth_method": ColumnSpec("TEXT"),
    "password": ColumnSpec("TEXT"),
    "private_key": ColumnSpec("TEXT"),
    "key_passphrase": ColumnSpec("TEXT"),
    "host_key_fingerprint": ColumnSpec("TEXT"),
    "aws_access_key_id": ColumnSpec("TEXT"),
    "aws_secret_access_key": ColumnSpec("TEXT"),
    "aws_session_token": ColumnSpec("TEXT"),
    "region_name": ColumnSpec("TEXT"),
    "endpoint_url": ColumnSpec("TEXT"),
    "created_at": ColumnSpec("NUMERIC", True),
    "updated_at": ColumnSpec("NUMERIC", True),
}

ADDED_COLUMN_SPECS = {
    "test_runs.run_batch_id": ColumnSpec("TEXT"),
    "test_runs.restarted_from_run_id": ColumnSpec("TEXT"),
    "scheduled_runs.batch_variable_name": ColumnSpec("TEXT"),
    "scheduled_runs.batch_start_value": ColumnSpec("TEXT"),
    "scheduled_runs.batch_step_days": ColumnSpec("INTEGER", True, "1"),
    "scheduled_runs.batch_max_firings": ColumnSpec("INTEGER"),
    "scheduled_runs.batch_weekend_policy": ColumnSpec("TEXT"),
    "scheduled_runs.batch_next_value": ColumnSpec("TEXT"),
    "scheduled_runs.firings_completed": ColumnSpec("INTEGER", True, "0"),
    "run_steps.carried_over": ColumnSpec("NUMERIC", True, "0"),
}

INDEX_SPECS = {
    "ix_run_batches_id": ("run_batches", ("id",), False),
    "ix_run_batches_batch_id": ("run_batches", ("batch_id",), True),
    "ix_run_batches_status": ("run_batches", ("status",), False),
    "ix_run_batches_target_id": ("run_batches", ("target_id",), False),
    "ix_file_server_profiles_id": ("file_server_profiles", ("id",), False),
    "ix_file_server_profiles_name": ("file_server_profiles", ("name",), True),
    "ix_test_runs_run_batch_id": ("test_runs", ("run_batch_id",), False),
    "ix_test_runs_restarted_from_run_id": ("test_runs", ("restarted_from_run_id",), False),
}

CHANGES = (
    Change("table", "run_batches", """CREATE TABLE run_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id VARCHAR(36) NOT NULL UNIQUE,
        target_type VARCHAR(20) NOT NULL, target_id INTEGER NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'RUNNING', variable_name VARCHAR(100) NOT NULL,
        start_value VARCHAR(10) NOT NULL, iterations INTEGER NOT NULL,
        step_days INTEGER NOT NULL DEFAULT 1, weekend_policy VARCHAR(10) NOT NULL DEFAULT 'skip',
        stop_on_failure BOOLEAN NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
        current_iteration INTEGER, current_value VARCHAR(10), created_at DATETIME NOT NULL,
        completed_at DATETIME
    )"""),
    Change("index", "ix_run_batches_id", "CREATE INDEX ix_run_batches_id ON run_batches(id)"),
    Change("index", "ix_run_batches_batch_id", "CREATE UNIQUE INDEX ix_run_batches_batch_id ON run_batches(batch_id)"),
    Change("index", "ix_run_batches_status", "CREATE INDEX ix_run_batches_status ON run_batches(status)"),
    Change("index", "ix_run_batches_target_id", "CREATE INDEX ix_run_batches_target_id ON run_batches(target_id)"),
    Change("table", "file_server_profiles", """CREATE TABLE file_server_profiles (
        id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL UNIQUE, kind VARCHAR(10) NOT NULL,
        description TEXT NOT NULL DEFAULT '', host VARCHAR(255), port INTEGER NOT NULL DEFAULT 22,
        username VARCHAR(255), auth_method VARCHAR(20), password TEXT, private_key TEXT,
        key_passphrase TEXT, host_key_fingerprint VARCHAR(128), aws_access_key_id VARCHAR(255),
        aws_secret_access_key TEXT, aws_session_token TEXT, region_name VARCHAR(50),
        endpoint_url VARCHAR(1024), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
    )"""),
    Change("index", "ix_file_server_profiles_id", "CREATE INDEX ix_file_server_profiles_id ON file_server_profiles(id)"),
    Change("index", "ix_file_server_profiles_name", "CREATE UNIQUE INDEX ix_file_server_profiles_name ON file_server_profiles(name)"),
    Change("column", "test_runs.run_batch_id", "ALTER TABLE test_runs ADD COLUMN run_batch_id VARCHAR(36)", "test_runs"),
    Change("column", "test_runs.restarted_from_run_id", "ALTER TABLE test_runs ADD COLUMN restarted_from_run_id VARCHAR(36)", "test_runs"),
    Change("index", "ix_test_runs_run_batch_id", "CREATE INDEX ix_test_runs_run_batch_id ON test_runs(run_batch_id)"),
    Change("index", "ix_test_runs_restarted_from_run_id", "CREATE INDEX ix_test_runs_restarted_from_run_id ON test_runs(restarted_from_run_id)"),
    Change("column", "scheduled_runs.batch_variable_name", "ALTER TABLE scheduled_runs ADD COLUMN batch_variable_name VARCHAR(100)", "scheduled_runs"),
    Change("column", "scheduled_runs.batch_start_value", "ALTER TABLE scheduled_runs ADD COLUMN batch_start_value VARCHAR(10)", "scheduled_runs"),
    Change("column", "scheduled_runs.batch_step_days", "ALTER TABLE scheduled_runs ADD COLUMN batch_step_days INTEGER NOT NULL DEFAULT 1", "scheduled_runs"),
    Change("column", "scheduled_runs.batch_max_firings", "ALTER TABLE scheduled_runs ADD COLUMN batch_max_firings INTEGER", "scheduled_runs"),
    Change("column", "scheduled_runs.batch_weekend_policy", "ALTER TABLE scheduled_runs ADD COLUMN batch_weekend_policy VARCHAR(10)", "scheduled_runs"),
    Change("column", "scheduled_runs.batch_next_value", "ALTER TABLE scheduled_runs ADD COLUMN batch_next_value VARCHAR(10)", "scheduled_runs"),
    Change("column", "scheduled_runs.firings_completed", "ALTER TABLE scheduled_runs ADD COLUMN firings_completed INTEGER NOT NULL DEFAULT 0", "scheduled_runs"),
    Change("column", "run_steps.carried_over", "ALTER TABLE run_steps ADD COLUMN carried_over BOOLEAN NOT NULL DEFAULT 0", "run_steps"),
)


def _affinity(declared_type: str) -> str:
    value = declared_type.upper()
    if "INT" in value:
        return "INTEGER"
    if any(token in value for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in value or not value:
        return "BLOB"
    if any(token in value for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _integrity(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "missing result"


def inspect_schema(connection: sqlite3.Connection) -> dict[str, object]:
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    indexes = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        )
    }
    columns = {
        table: {row[1]: row for row in connection.execute(f'PRAGMA table_info("{table}")')}
        for table in tables
    }
    return {"tables": tables, "indexes": indexes, "columns": columns}


def _normalize_default(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    while len(text) > 1 and text[0] == "(" and text[-1] == ")":
        text = text[1:-1].strip()
    return text.casefold()


def _validate_column(name: str, row: tuple, spec: ColumnSpec) -> None:
    if _affinity(row[2]) != spec.affinity:
        raise UpgradeError(f"Incompatible object {name}: expected {spec.affinity} affinity")
    if bool(row[3]) != spec.not_null:
        raise UpgradeError(f"Incompatible object {name}: NOT NULL does not match")
    if _normalize_default(row[4]) != _normalize_default(spec.default):
        raise UpgradeError(f"Incompatible object {name}: default does not match")
    if int(row[5]) != spec.primary_key:
        raise UpgradeError(f"Incompatible object {name}: primary key ordinal does not match")


def _validate_baseline(schema: dict[str, object]) -> None:
    missing_tables = REQUIRED_BASELINE_TABLES - schema["tables"]
    if missing_tables:
        raise UpgradeError(f"Database is missing required baseline tables: {', '.join(sorted(missing_tables))}")
    for table, required in BASELINE_COLUMNS.items():
        actual = schema["columns"][table]
        missing = set(required) - set(actual)
        if missing:
            raise UpgradeError(f"Incompatible baseline schema for {table}: missing {', '.join(sorted(missing))}")
        for column, affinity in required.items():
            if _affinity(actual[column][2]) != affinity:
                raise UpgradeError(f"Incompatible baseline schema for {table}.{column}")


def _validate_index(connection: sqlite3.Connection, name: str) -> None:
    table, expected_columns, expected_unique = INDEX_SPECS[name]
    listed = {row[1]: bool(row[2]) for row in connection.execute(f'PRAGMA index_list("{table}")')}
    columns = tuple(row[2] for row in connection.execute(f'PRAGMA index_info("{name}")'))
    if name not in listed or listed[name] != expected_unique or columns != expected_columns:
        raise UpgradeError(f"Incompatible object {name}: index definition does not match")


def _validate_existing_targets(connection: sqlite3.Connection, schema: dict[str, object]) -> None:
    table_specs = {"run_batches": RUN_BATCH_COLUMNS, "file_server_profiles": FILE_SERVER_PROFILE_COLUMNS}
    for table, specs in table_specs.items():
        if table not in schema["tables"]:
            continue
        actual = schema["columns"][table]
        if set(actual) != set(specs):
            raise UpgradeError(f"Incompatible object {table}: columns do not exactly match")
        for column, spec in specs.items():
            _validate_column(f"{table}.{column}", actual[column], spec)
    for name, spec in ADDED_COLUMN_SPECS.items():
        table, column = name.split(".", 1)
        row = schema["columns"][table].get(column)
        if row is not None:
            _validate_column(name, row, spec)
    for name in INDEX_SPECS:
        if name in schema["indexes"]:
            _validate_index(connection, name)


def _validate(connection: sqlite3.Connection) -> dict[str, object]:
    if _integrity(connection).lower() != "ok":
        raise UpgradeError("Database integrity check failed")
    schema = inspect_schema(connection)
    _validate_baseline(schema)
    _validate_existing_targets(connection, schema)
    return schema


def _is_present(change: Change, schema: dict[str, object]) -> bool:
    if change.kind == "table":
        return change.name in schema["tables"]
    if change.kind == "index":
        return change.name in schema["indexes"]
    return change.name.rsplit(".", 1)[1] in schema["columns"][change.table]


def plan_upgrade(connection: sqlite3.Connection) -> tuple[Change, ...]:
    schema = _validate(connection)
    return tuple(change for change in CHANGES if not _is_present(change, schema))


def create_backup(
    source: Path,
    backup_dir: Path | None = None,
    *,
    lock_connection: sqlite3.Connection | None = None,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination_dir = backup_dir or source.parent
    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
        if not destination_dir.is_dir():
            raise OSError(f"not a directory: {destination_dir}")
        destination = destination_dir / f"{source.name}.{timestamp}.bak"
        if destination.exists():
            raise UpgradeError(f"Backup already exists: {destination}")
    except UpgradeError:
        raise
    except OSError as exc:
        raise UpgradeError(f"Could not prepare backup directory {destination_dir}: {exc}") from exc
    if lock_connection is not None and not lock_connection.in_transaction:
        raise UpgradeError("Backup lock connection has no active transaction")
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as backup_source:
            with sqlite3.connect(destination) as backup_connection:
                backup_source.backup(backup_connection)
    except (sqlite3.Error, OSError) as exc:
        try:
            destination.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            raise UpgradeError(f"Could not create or clean up backup {destination}: {cleanup_exc}") from exc
        raise UpgradeError(f"Could not create backup {destination}: {exc}") from exc
    return destination


def _row_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in inspect_schema(connection)["tables"]
    }


def verify_upgrade(connection: sqlite3.Connection, baseline_counts: dict[str, int]) -> None:
    schema = _validate(connection)
    missing = [change.name for change in CHANGES if not _is_present(change, schema)]
    if missing:
        raise UpgradeError(f"Upgrade verification found missing objects: {', '.join(missing)}")
    for table, previous_count in baseline_counts.items():
        current_count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        if current_count < previous_count:
            raise UpgradeError(f"Row count decreased for table {table}")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise UpgradeError("Foreign key check failed after upgrade")


def apply_upgrade(connection: sqlite3.Connection, changes: tuple[Change, ...], baseline_counts: dict[str, int] | None = None) -> None:
    counts = baseline_counts if baseline_counts is not None else _row_counts(connection)
    try:
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        for change in changes:
            connection.execute(change.ddl)
        verify_upgrade(connection, counts)
        connection.commit()
    except Exception as exc:
        connection.rollback()
        if isinstance(exc, UpgradeError):
            raise
        raise UpgradeError(f"Upgrade failed and was rolled back: {exc}") from exc


def upgrade_database(path: str | Path, dry_run: bool = False, backup_dir: str | Path | None = None) -> UpgradeResult:
    source = Path(path)
    if not source.exists():
        raise UpgradeError(f"Database path does not exist: {source}")
    if not source.is_file():
        raise UpgradeError(f"Database path is not a file: {source}")
    try:
        connection = sqlite3.connect(f"file:{source}?mode=rw", uri=True, isolation_level=None)
        try:
            baseline_counts = _row_counts(connection)
            planned = plan_upgrade(connection)
            schema = inspect_schema(connection)
            present = tuple(change for change in CHANGES if _is_present(change, schema))
        except sqlite3.Error as exc:
            raise UpgradeError(f"Invalid SQLite database: {exc}") from exc
        if dry_run:
            connection.close()
            return UpgradeResult(planned, (), present, None)
        if not planned:
            verify_upgrade(connection, baseline_counts)
            connection.close()
            return UpgradeResult(planned, (), present, None)
        backup_path = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            backup_path = create_backup(
                source,
                Path(backup_dir) if backup_dir is not None else None,
                lock_connection=connection,
            )
            apply_upgrade(connection, planned, baseline_counts)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return UpgradeResult(planned, planned, present, backup_path)
    except sqlite3.Error as exc:
        raise UpgradeError(f"Invalid SQLite database: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upgrade a baseline SQLite database to the current schema")
    parser.add_argument("PATH", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        result = upgrade_database(args.PATH, dry_run=args.dry_run, backup_dir=args.backup_dir)
    except UpgradeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Source: {args.PATH}")
    print(f"Baseline commit: {BASELINE_COMMIT}")
    action = "PLAN" if args.dry_run else "APPLY"
    for change in result.planned:
        print(f"{action} {change.name}")
    for change in result.already_present:
        print(f"PRESENT {change.name}")
    if result.backup_path is not None:
        print(f"Backup: {result.backup_path}")
    if args.dry_run:
        print("Source validation: ok; upgrade plan only")
    else:
        print("Verification: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
