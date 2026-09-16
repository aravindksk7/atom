# SQLite Three-Day Schema Upgrade Design

**Date:** 2026-09-16
**Status:** Approved
**Baseline commit:** `7eeb90b49eb8ddbd4fadcd3d3afe1465918ec3f5`
**Target:** current repository schema

## Purpose

Provide an explicit, operator-friendly SQLite upgrade script for databases created from the repository state three days before 2026-09-16. The script upgrades schema only, preserves existing data, and can be rerun safely.

## Interface

Create `scripts/upgrade_db_20260913_to_current.py` with this command-line interface:

```text
python scripts/upgrade_db_20260913_to_current.py PATH [--dry-run] [--backup-dir DIRECTORY]
```

`PATH` is required and must identify an existing SQLite database. Normal execution creates a timestamped backup before opening a write transaction. `--backup-dir` changes the backup destination. `--dry-run` performs validation and reports planned/already-applied changes without creating a backup or modifying the database.

The command exits nonzero for a missing/unreadable/non-SQLite database, an unsupported baseline, backup failure, migration failure, or failed post-upgrade verification.

## Baseline and Scope

The baseline is commit `7eeb90b49eb8ddbd4fadcd3d3afe1465918ec3f5`, the last commit before 2026-09-14. The schema delta to current consists of:

- New `run_batches` table and its indexes.
- New `file_server_profiles` table and its indexes.
- `test_runs.run_batch_id` and `test_runs.restarted_from_run_id`, with indexes.
- `scheduled_runs.batch_variable_name`, `batch_start_value`, `batch_step_days`, `batch_max_firings`, `batch_weekend_policy`, `batch_next_value`, and `firings_completed`.
- `run_steps.carried_over`.

No data transformation is required. Existing rows receive SQLite defaults for the new non-null columns: `batch_step_days = 1`, `firings_completed = 0`, and `carried_over = 0`.

## Safety Model

The script uses Python's standard `sqlite3` module and has no new dependencies. It validates SQLite identity using `PRAGMA schema_version`, `PRAGMA integrity_check`, and required baseline tables. A database is accepted when the core baseline tables exist; individual target objects may already exist because the upgrade is idempotent.

Before mutation, the script uses SQLite's backup API rather than copying a potentially active database file. The backup filename includes the source filename and a UTC timestamp and must not overwrite an existing file.

All DDL executes within one explicit transaction. Each table, column, and index is inspected before creation. On any error the transaction rolls back, leaving the original database unchanged; the backup remains available.

The script will not call application `init_db()` because that routine applies every historical compatibility shim and can perform data backfills outside this upgrade's defined delta.

## Verification and Reporting

After migration, the script verifies every target table, column, and index using SQLite metadata and runs `PRAGMA foreign_key_check` plus `PRAGMA integrity_check`. It also records baseline row counts for all existing tables and verifies that none decreased.

Output lists:

- Source path and detected baseline.
- Every planned, applied, or already-present schema object.
- Backup path.
- Verification results.
- Final success/failure status.

The script never prints row contents or credentials. This is especially important for encrypted fields in `file_server_profiles`.

## Testing

Add unit tests that construct a minimal baseline SQLite database, insert representative rows, and invoke the upgrade functions directly. Coverage must prove:

- All target tables, columns, defaults, and indexes are created.
- Existing rows and values are preserved.
- The upgrade is idempotent.
- Dry-run makes no changes and creates no backup.
- A normal run creates a valid backup containing the baseline schema/data.
- Unsupported databases fail before backup/mutation.
- A forced DDL failure rolls back all changes.

A focused CLI smoke test will run the script against a temporary baseline database and validate a zero exit code and expected summary output.

## Non-Goals

- PostgreSQL or other SQLAlchemy engines.
- Downgrade support.
- Migrating from arbitrary historical commits.
- Rotating or populating file-server credentials.
- Replacing the repository's startup compatibility migration system.
