from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "upgrade_db_20260913_to_current.py"
SPEC = importlib.util.spec_from_file_location("sqlite_three_day_upgrade", SCRIPT)
assert SPEC and SPEC.loader
upgrade = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = upgrade
SPEC.loader.exec_module(upgrade)


def create_baseline_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE test_runs (
                id INTEGER PRIMARY KEY, run_id VARCHAR(36) NOT NULL UNIQUE,
                status VARCHAR(20) NOT NULL DEFAULT 'PENDING', started_at DATETIME,
                completed_at DATETIME, source_env VARCHAR(100), target_env VARCHAR(100),
                config_snapshot JSON, total_tests INTEGER NOT NULL DEFAULT 0,
                passed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
                slow INTEGER NOT NULL DEFAULT 0, error INTEGER NOT NULL DEFAULT 0,
                run_type VARCHAR(50) NOT NULL DEFAULT 'reconciliation', pair_id VARCHAR(36),
                is_baseline BOOLEAN NOT NULL DEFAULT 0,
                cancel_requested BOOLEAN NOT NULL DEFAULT 0, selection_id INTEGER,
                selection_version INTEGER, ci_context JSON
            );
            CREATE TABLE scheduled_runs (
                id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL UNIQUE,
                cron_expr VARCHAR(100) NOT NULL, job_sequence JSON NOT NULL DEFAULT '[]',
                source_env VARCHAR(100) NOT NULL DEFAULT '',
                target_env VARCHAR(100) NOT NULL DEFAULT '',
                run_settings_json JSON NOT NULL DEFAULT '{}',
                enabled BOOLEAN NOT NULL DEFAULT 1, last_run_at DATETIME,
                next_run_at DATETIME, created_at DATETIME NOT NULL,
                selection_id INTEGER, selection_version INTEGER, sequence_id INTEGER,
                sequence_version INTEGER
            );
            CREATE TABLE run_steps (
                id INTEGER PRIMARY KEY, run_id VARCHAR(36) NOT NULL,
                job_name VARCHAR(255) NOT NULL, step_index INTEGER NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                hold_after BOOLEAN NOT NULL DEFAULT 0, condition JSON,
                wait_seconds INTEGER NOT NULL DEFAULT 0, step_id VARCHAR(255),
                depends_on JSON, trigger_rule VARCHAR(20) NOT NULL DEFAULT 'all_success',
                attempt INTEGER NOT NULL DEFAULT 0, max_retries INTEGER,
                on_failure VARCHAR(20) NOT NULL DEFAULT 'skip_downstream', held_at DATETIME,
                released_at DATETIME, released_by VARCHAR(255), release_note TEXT,
                release_action VARCHAR(20),
                FOREIGN KEY(run_id) REFERENCES test_runs(run_id) ON DELETE CASCADE
            );
            INSERT INTO test_runs (id, run_id, status) VALUES (1, 'baseline-run', 'PASSED');
            INSERT INTO scheduled_runs (id, name, cron_expr, created_at)
                VALUES (1, 'baseline-schedule', '0 1 * * *', '2026-09-13');
            INSERT INTO run_steps (id, run_id, job_name, step_index, status)
                VALUES (1, 'baseline-run', 'job-a', 0, 'PASSED');
            """
        )


def schema_snapshot(path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()


def columns(path: Path, table: str) -> dict[str, tuple]:
    with sqlite3.connect(path) as connection:
        return {row[1]: row for row in connection.execute(f'PRAGMA table_info("{table}")')}


def object_names(path: Path, kind: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ?", (kind,)
            )
        }


def test_dry_run_reports_changes_without_mutating_or_backing_up(tmp_path: Path):
    database = tmp_path / "baseline.db"
    create_baseline_db(database)
    before = schema_snapshot(database)

    result = upgrade.upgrade_database(database, dry_run=True)

    names = {change.name for change in result.planned}
    assert {"run_batches", "file_server_profiles"} <= names
    assert {
        "test_runs.run_batch_id",
        "test_runs.restarted_from_run_id",
        "scheduled_runs.batch_variable_name",
        "scheduled_runs.batch_start_value",
        "scheduled_runs.batch_step_days",
        "scheduled_runs.batch_max_firings",
        "scheduled_runs.batch_weekend_policy",
        "scheduled_runs.batch_next_value",
        "scheduled_runs.firings_completed",
        "run_steps.carried_over",
        "ix_run_batches_target_id",
    } <= names
    assert result.backup_path is None
    assert schema_snapshot(database) == before
    assert list(tmp_path.iterdir()) == [database]


def test_upgrade_schema_defaults_and_preserves_data(tmp_path: Path):
    database = tmp_path / "baseline.db"
    create_baseline_db(database)

    result = upgrade.upgrade_database(database)

    assert result.backup_path and result.backup_path.exists()
    assert {"run_batches", "file_server_profiles"} <= object_names(database, "table")
    assert {
        "ix_run_batches_id",
        "ix_run_batches_batch_id",
        "ix_run_batches_status",
        "ix_run_batches_target_id",
        "ix_file_server_profiles_id",
        "ix_file_server_profiles_name",
        "ix_test_runs_run_batch_id",
        "ix_test_runs_restarted_from_run_id",
    } <= object_names(database, "index")
    assert set(columns(database, "run_batches")) == {
        "id", "batch_id", "target_type", "target_id", "status", "variable_name",
        "start_value", "iterations", "step_days", "weekend_policy", "stop_on_failure",
        "completed", "current_iteration", "current_value", "created_at", "completed_at",
    }
    assert set(columns(database, "file_server_profiles")) == {
        "id", "name", "kind", "description", "host", "port", "username", "auth_method",
        "password", "private_key", "key_passphrase", "host_key_fingerprint",
        "aws_access_key_id", "aws_secret_access_key", "aws_session_token", "region_name",
        "endpoint_url", "created_at", "updated_at",
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id, run_id, status FROM test_runs").fetchall() == [(1, "baseline-run", "PASSED")]
        assert connection.execute("SELECT id, name, cron_expr, batch_step_days, firings_completed FROM scheduled_runs").fetchall() == [(1, "baseline-schedule", "0 1 * * *", 1, 0)]
        assert connection.execute("SELECT id, run_id, job_name, step_index, status, carried_over FROM run_steps").fetchall() == [(1, "baseline-run", "job-a", 0, "PASSED", 0)]
        assert "run_batch_id" in {row[1] for row in connection.execute("PRAGMA table_info(test_runs)")}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_rejects_unrelated_same_named_tables_before_backup(tmp_path: Path):
    database = tmp_path / "unrelated.db"
    with sqlite3.connect(database) as connection:
        connection.executescript("CREATE TABLE test_runs (id INTEGER); CREATE TABLE scheduled_runs (id INTEGER); CREATE TABLE run_steps (id INTEGER);")

    with pytest.raises(upgrade.UpgradeError, match="baseline schema"):
        upgrade.upgrade_database(database)

    assert not list(tmp_path.glob("*.bak"))


@pytest.mark.parametrize(
    ("ddl", "object_name"),
    [
        ("CREATE TABLE run_batches (id INTEGER PRIMARY KEY)", "run_batches"),
        ("ALTER TABLE scheduled_runs ADD COLUMN batch_step_days TEXT NOT NULL DEFAULT 'wrong'", "scheduled_runs.batch_step_days"),
        ("CREATE INDEX ix_test_runs_run_batch_id ON test_runs(run_id)", "ix_test_runs_run_batch_id"),
    ],
)
def test_rejects_incompatible_partial_target_schema_before_backup(
    tmp_path: Path, ddl: str, object_name: str
):
    database = tmp_path / "partial.db"
    create_baseline_db(database)
    with sqlite3.connect(database) as connection:
        if "ix_test_runs_run_batch_id" in ddl:
            connection.execute("ALTER TABLE test_runs ADD COLUMN run_batch_id VARCHAR(36)")
        connection.execute(ddl)

    with pytest.raises(upgrade.UpgradeError, match=object_name):
        upgrade.upgrade_database(database)

    assert not list(tmp_path.glob("*.bak"))


def test_accepts_compatible_partial_upgrade(tmp_path: Path):
    database = tmp_path / "partial.db"
    create_baseline_db(database)
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE test_runs ADD COLUMN run_batch_id VARCHAR(36)")
        connection.execute("CREATE INDEX ix_test_runs_run_batch_id ON test_runs(run_batch_id)")

    result = upgrade.upgrade_database(database, dry_run=True)

    assert {change.name for change in result.already_present} >= {
        "test_runs.run_batch_id", "ix_test_runs_run_batch_id"
    }


def test_rejects_target_table_with_malformed_primary_key(tmp_path: Path):
    database = tmp_path / "malformed-pk.db"
    create_baseline_db(database)
    table_change = next(change for change in upgrade.CHANGES if change.name == "run_batches")
    malformed = table_change.ddl.replace("id INTEGER PRIMARY KEY AUTOINCREMENT", "id INTEGER")
    with sqlite3.connect(database) as connection:
        connection.execute(malformed)

    with pytest.raises(upgrade.UpgradeError, match="run_batches.id.*primary key"):
        upgrade.upgrade_database(database, dry_run=True)


def test_upgrade_holds_write_lock_across_backup_and_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    database = tmp_path / "locked.db"
    create_baseline_db(database)
    original = upgrade.create_backup
    observed_locked = False

    def checked_backup(source: Path, backup_dir=None, lock_connection=None):
        nonlocal observed_locked
        assert lock_connection is not None and lock_connection.in_transaction
        competing = sqlite3.connect(source, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competing.execute("BEGIN IMMEDIATE")
            observed_locked = True
        finally:
            competing.close()
        return original(source, backup_dir, lock_connection=lock_connection)

    monkeypatch.setattr(upgrade, "create_backup", checked_backup)
    upgrade.upgrade_database(database)

    assert observed_locked
    assert "run_batches" in object_names(database, "table")


def test_noop_upgrade_executes_full_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "noop.db"
    create_baseline_db(database)
    upgrade.upgrade_database(database)
    original = upgrade.verify_upgrade
    calls = []

    def recording_verify(connection: sqlite3.Connection, baseline_counts: dict[str, int]):
        calls.append(dict(baseline_counts))
        return original(connection, baseline_counts)

    monkeypatch.setattr(upgrade, "verify_upgrade", recording_verify)
    result = upgrade.upgrade_database(database)

    assert result.applied == ()
    assert len(calls) == 1
    assert calls[0]["test_runs"] == 1


def test_upgrade_is_idempotent(tmp_path: Path):
    database = tmp_path / "baseline.db"
    create_baseline_db(database)
    upgrade.upgrade_database(database)
    before = schema_snapshot(database)

    result = upgrade.upgrade_database(database)

    assert result.planned == ()
    assert result.applied == ()
    assert result.backup_path is None
    assert len(result.already_present) == len(upgrade.CHANGES)
    assert schema_snapshot(database) == before


def test_backup_retains_baseline_state(tmp_path: Path):
    database = tmp_path / "baseline.db"
    backups = tmp_path / "backups"
    create_baseline_db(database)

    result = upgrade.upgrade_database(database, backup_dir=backups)

    assert result.backup_path and result.backup_path.parent == backups
    assert "run_batches" not in object_names(result.backup_path, "table")
    with sqlite3.connect(result.backup_path) as connection:
        assert connection.execute("SELECT run_id, status FROM test_runs").fetchall() == [("baseline-run", "PASSED")]


@pytest.mark.parametrize("kind", ["empty", "text"])
def test_invalid_database_rejected_before_backup(tmp_path: Path, kind: str):
    database = tmp_path / "invalid.db"
    if kind == "empty":
        sqlite3.connect(database).close()
    else:
        database.write_text("not sqlite", encoding="utf-8")

    with pytest.raises(upgrade.UpgradeError):
        upgrade.upgrade_database(database)

    assert list(tmp_path.iterdir()) == [database]


def test_missing_path_rejected(tmp_path: Path):
    with pytest.raises(upgrade.UpgradeError, match="does not exist"):
        upgrade.upgrade_database(tmp_path / "missing.db")


def test_invalid_backup_dir_api_is_concise_and_preserves_source(tmp_path: Path):
    database = tmp_path / "baseline.db"
    backup_file = tmp_path / "not-a-directory"
    create_baseline_db(database)
    backup_file.write_text("occupied", encoding="utf-8")
    before = schema_snapshot(database)

    with pytest.raises(upgrade.UpgradeError, match="backup directory"):
        upgrade.upgrade_database(database, backup_dir=backup_file)

    assert schema_snapshot(database) == before
    assert "run_batches" not in object_names(database, "table")


def test_invalid_backup_dir_cli_is_concise_and_preserves_source(tmp_path: Path):
    database = tmp_path / "baseline.db"
    backup_file = tmp_path / "not-a-directory"
    create_baseline_db(database)
    backup_file.write_text("occupied", encoding="utf-8")
    before = schema_snapshot(database)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(database), "--backup-dir", str(backup_file)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "backup directory" in result.stderr.lower()
    assert "Traceback" not in result.stderr
    assert schema_snapshot(database) == before
    assert "run_batches" not in object_names(database, "table")


def test_failed_ddl_rolls_back_all_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "baseline.db"
    create_baseline_db(database)
    changes = (
        upgrade.Change("table", "temporary_table", "CREATE TABLE temporary_table (id INTEGER)"),
        upgrade.Change("table", "broken_table", "CREATE TABL broken_table (id INTEGER)"),
    )
    monkeypatch.setattr(upgrade, "CHANGES", changes)

    with pytest.raises(upgrade.UpgradeError):
        upgrade.upgrade_database(database)

    assert "temporary_table" not in object_names(database, "table")
    assert len(list(tmp_path.glob("*.bak"))) == 1


def test_cli_dry_run_and_normal_upgrade(tmp_path: Path):
    database = tmp_path / "baseline.db"
    create_baseline_db(database)
    dry_run = subprocess.run(
        [sys.executable, str(SCRIPT), str(database), "--dry-run"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert dry_run.returncode == 0
    assert "PLAN run_batches" in dry_run.stdout
    assert "7eeb90b49eb8ddbd4fadcd3d3afe1465918ec3f5" in dry_run.stdout
    assert "Source validation: ok; upgrade plan only" in dry_run.stdout
    assert "Verification: ok" not in dry_run.stdout
    assert not list(tmp_path.glob("*.bak"))

    applied = subprocess.run(
        [sys.executable, str(SCRIPT), str(database)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert applied.returncode == 0
    assert "APPLY run_batches" in applied.stdout
    assert "Verification: ok" in applied.stdout
    assert len(list(tmp_path.glob("*.bak"))) == 1


def test_cli_missing_path_has_concise_error(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "missing.db")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "does not exist" in result.stderr
    assert "Traceback" not in result.stderr
