# SQLite Three-Day Schema Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a safe, idempotent CLI that upgrades SQLite databases from commit `7eeb90b49eb8ddbd4fadcd3d3afe1465918ec3f5` to the current repository schema.

**Architecture:** A standalone standard-library script owns schema inspection, upgrade planning, SQLite online backup, transactional DDL, and post-upgrade verification. Unit tests construct a representative baseline database and exercise both the Python API and CLI without importing application startup migrations.

**Tech Stack:** Python 3, standard-library `sqlite3`, `argparse`, `pathlib`, `datetime`, pytest.

## Global Constraints

- Support SQLite only.
- Require an existing database path.
- Normal execution must create a timestamped SQLite backup before mutation.
- `--dry-run` must not create a backup or modify the database.
- Upgrade only the delta from baseline commit `7eeb90b49eb8ddbd4fadcd3d3afe1465918ec3f5`.
- Preserve all existing rows and values.
- Make every schema operation idempotent.
- Execute DDL in one explicit transaction and roll back on failure.
- Verify target objects, row-count preservation, `PRAGMA integrity_check`, and `PRAGMA foreign_key_check`.
- Never print row contents or credentials.
- Add no dependencies and do not call application `init_db()`.

---

## File Structure

- Create `scripts/upgrade_db_20260913_to_current.py`: CLI, schema manifest, inspection, backup, migration, and verification.
- Create `tests/unit/test_upgrade_db_20260913_to_current.py`: baseline fixture, API tests, rollback test, backup test, and CLI smoke test.
- Reference `etl_framework/repository/models.py:155-525` and `etl_framework/repository/database.py:217-405` for target schema; do not modify them.

### Task 1: Implement the validated, idempotent SQLite upgrade

**Files:**
- Create: `scripts/upgrade_db_20260913_to_current.py`
- Create: `tests/unit/test_upgrade_db_20260913_to_current.py`

**Interfaces:**
- Produces: `UpgradeError`, `Change`, `inspect_schema(connection)`, `plan_upgrade(connection)`, `create_backup(source, backup_dir=None)`, `apply_upgrade(connection, changes)`, `verify_upgrade(connection, baseline_counts)`, `upgrade_database(path, dry_run=False, backup_dir=None)`, and `main(argv=None) -> int`.
- Consumes: baseline tables `test_runs`, `scheduled_runs`, and `run_steps`; target contracts from current repository models.

- [ ] **Step 1: Write baseline construction and failing dry-run test**

Create `tests/unit/test_upgrade_db_20260913_to_current.py` with imports via `importlib.util` so the standalone script remains outside a package. Add `create_baseline_db(path)` that creates the three required tables with representative rows:

```python
connection.executescript("""
CREATE TABLE test_runs (
    id INTEGER PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL
);
CREATE TABLE scheduled_runs (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    cron_expr VARCHAR(100) NOT NULL
);
CREATE TABLE run_steps (
    id INTEGER PRIMARY KEY,
    run_id VARCHAR(36),
    job_name VARCHAR(255) NOT NULL,
    step_index INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
);
INSERT INTO test_runs VALUES (1, 'baseline-run', 'PASSED');
INSERT INTO scheduled_runs VALUES (1, 'baseline-schedule', '0 1 * * *');
INSERT INTO run_steps VALUES (1, 'baseline-run', 'job-a', 0, 'PASSED');
""")
```

Write `test_dry_run_reports_changes_without_mutating_or_backing_up` that calls `upgrade_database(database, dry_run=True)`, asserts the plan includes both new tables and all new columns/indexes, then confirms `sqlite_master` and file siblings are unchanged.

- [ ] **Step 2: Run the dry-run test to verify failure**

Run:

```powershell
pytest -q tests/unit/test_upgrade_db_20260913_to_current.py::test_dry_run_reports_changes_without_mutating_or_backing_up
```

Expected: FAIL because `scripts/upgrade_db_20260913_to_current.py` does not exist.

- [ ] **Step 3: Implement schema manifest, inspection, validation, and planning**

Create the script with:

```python
BASELINE_COMMIT = "7eeb90b49eb8ddbd4fadcd3d3afe1465918ec3f5"
REQUIRED_BASELINE_TABLES = frozenset({"test_runs", "scheduled_runs", "run_steps"})

@dataclass(frozen=True)
class Change:
    kind: str
    name: str
    ddl: str
    table: str | None = None

class UpgradeError(RuntimeError):
    pass
```

Define ordered `Change` entries for:

- `run_batches`, plus unique `ix_run_batches_batch_id`, non-unique `ix_run_batches_status`, and `ix_run_batches_target_id`.
- `file_server_profiles`, plus unique `ix_file_server_profiles_name`.
- `test_runs.run_batch_id`, `test_runs.restarted_from_run_id`, and both indexes.
- Seven `scheduled_runs` batch columns and their exact defaults.
- `run_steps.carried_over`.

Use these exact table shapes:

```sql
CREATE TABLE run_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id VARCHAR(36) NOT NULL UNIQUE,
  target_type VARCHAR(20) NOT NULL,
  target_id INTEGER NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'RUNNING',
  variable_name VARCHAR(100) NOT NULL,
  start_value VARCHAR(10) NOT NULL,
  iterations INTEGER NOT NULL,
  step_days INTEGER NOT NULL DEFAULT 1,
  weekend_policy VARCHAR(10) NOT NULL DEFAULT 'skip',
  stop_on_failure BOOLEAN NOT NULL DEFAULT 0,
  completed INTEGER NOT NULL DEFAULT 0,
  current_iteration INTEGER,
  current_value VARCHAR(10),
  created_at DATETIME NOT NULL,
  completed_at DATETIME
)
```

```sql
CREATE TABLE file_server_profiles (
  id INTEGER PRIMARY KEY,
  name VARCHAR(255) NOT NULL UNIQUE,
  kind VARCHAR(10) NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  host VARCHAR(255),
  port INTEGER NOT NULL DEFAULT 22,
  username VARCHAR(255),
  auth_method VARCHAR(20),
  password TEXT,
  private_key TEXT,
  key_passphrase TEXT,
  host_key_fingerprint VARCHAR(128),
  aws_access_key_id VARCHAR(255),
  aws_secret_access_key TEXT,
  aws_session_token TEXT,
  region_name VARCHAR(50),
  endpoint_url VARCHAR(1024),
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
)
```

Implement metadata helpers using `sqlite_master` and `PRAGMA table_info`, validate `PRAGMA integrity_check == 'ok'`, reject absent core tables, and filter already-present objects from `plan_upgrade`.

- [ ] **Step 4: Run dry-run test to green**

Run the same focused pytest command. Expected: PASS.

- [ ] **Step 5: Write failing schema/data/default/idempotency tests**

Add tests that perform a normal upgrade and assert:

- Both new tables exist.
- Every listed target column and index exists.
- Existing three baseline rows remain byte-for-byte equivalent by selected values.
- Existing scheduled row has `batch_step_days == 1` and `firings_completed == 0`.
- Existing run step has `carried_over == 0`.
- Calling upgrade a second time returns no applied changes and leaves schema/data unchanged.

- [ ] **Step 6: Run new tests to verify failure**

Run:

```powershell
pytest -q tests/unit/test_upgrade_db_20260913_to_current.py -k "schema or preserves or defaults or idempotent"
```

Expected: FAIL because mutation, backup, and verification are not implemented.

- [ ] **Step 7: Implement backup, transactional migration, and verification**

Implement:

```python
def create_backup(source: Path, backup_dir: Path | None = None) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination_dir = backup_dir or source.parent
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{source.name}.{timestamp}.bak"
    if destination.exists():
        raise UpgradeError(f"Backup already exists: {destination}")
    with sqlite3.connect(source) as source_connection, sqlite3.connect(destination) as backup_connection:
        source_connection.backup(backup_connection)
    return destination
```

Record row counts for every pre-existing non-system table. Use `connection.execute('BEGIN IMMEDIATE')`, execute all planned DDL, verify while the transaction is active, and call `commit`; call `rollback` on every exception and re-raise as `UpgradeError`. Verify every target object, baseline counts did not decrease, integrity is `ok`, and `foreign_key_check` returns no rows.

`upgrade_database` must validate and plan before backup. Return a structured result containing planned/applied/already-present changes and optional backup path so tests and CLI output do not need to parse logs.

- [ ] **Step 8: Run schema/data tests to green**

Run the Step 6 command. Expected: all selected tests PASS.

- [ ] **Step 9: Write failing backup, invalid-input, and rollback tests**

Add tests proving:

- The backup database retains baseline schema/data and lacks target objects.
- An empty SQLite database raises before creating any backup.
- A text file and missing path raise clear `UpgradeError`/CLI errors.
- Monkeypatching the ordered change list with a valid first change and invalid second DDL leaves neither change after failure.

- [ ] **Step 10: Implement CLI and error handling**

Implement `main(argv=None) -> int` with `argparse`, path existence/file checks, `--dry-run`, and `--backup-dir`. Print source, baseline commit, each change prefixed `PLAN`, `APPLY`, or `PRESENT`, backup path, and verification summary. Catch `UpgradeError`, print one concise message to stderr, and return `1`; return `0` on successful dry-run or upgrade. Add:

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 11: Run all upgrade-script unit tests**

Run:

```powershell
pytest -q tests/unit/test_upgrade_db_20260913_to_current.py
```

Expected: all tests PASS.

- [ ] **Step 12: Run CLI smoke test against a temporary baseline database**

Use pytest's CLI test plus direct help validation:

```powershell
python scripts/upgrade_db_20260913_to_current.py --help
pytest -q tests/unit/test_upgrade_db_20260913_to_current.py -k cli
```

Expected: help displays `PATH`, `--dry-run`, and `--backup-dir`; CLI test PASSes with exit code zero and upgrade summary.

- [ ] **Step 13: Run adjacent repository migration tests**

Run:

```powershell
pytest -q tests/unit/test_repository_migrations.py tests/unit/test_upgrade_db_20260913_to_current.py
```

Expected: all tests PASS.

- [ ] **Step 14: Run repository lint/typecheck commands if defined**

Inspect available scripts/automation. If no Python lint/typecheck command is defined, record that fact and use this syntax validation:

```powershell
python -m py_compile scripts/upgrade_db_20260913_to_current.py tests/unit/test_upgrade_db_20260913_to_current.py
```

Expected: exit code 0 with no output.

- [ ] **Step 15: Review final diff and generated files**

Run:

```powershell
git diff --check
git status --short
git diff -- scripts/upgrade_db_20260913_to_current.py tests/unit/test_upgrade_db_20260913_to_current.py docs/superpowers/specs/2026-09-16-sqlite-three-day-upgrade-design.md docs/superpowers/plans/2026-09-16-sqlite-three-day-upgrade.md
```

Expected: only the script, tests, design, and plan are associated with this feature; no temporary databases or backup files are tracked.

- [ ] **Step 16: Commit only if explicitly requested**

No commit is authorized by the current request. If explicitly requested later, inspect status/diff/log, stage only the four intended files, and commit with:

```powershell
git add scripts/upgrade_db_20260913_to_current.py tests/unit/test_upgrade_db_20260913_to_current.py docs/superpowers/specs/2026-09-16-sqlite-three-day-upgrade-design.md docs/superpowers/plans/2026-09-16-sqlite-three-day-upgrade.md
git commit -m "feat: add SQLite three-day schema upgrade"
```
