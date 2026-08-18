# Oracle Connection, Reconciliation, and Web UI Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Oracle database connection capabilities (`oracledb` Thin mode), engine query execution, chunked reconciliation/comparison, and Web UI selection across the ETL framework.

**Architecture:** Extend `EnvironmentConfig` and `DBEngine` to handle `db_type="oracle"`, configure `oracle+oracledb` SQLAlchemy connection strings using `service_name`, update identifier quoting in `sql_utils.py` and chunking queries in `chunker.py` for Oracle ANSI SQL, and update frontend selection dropdowns and API validators.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0+, `oracledb>=2.0.0` (Thin mode), FastAPI, Alpine.js, Tailwind CSS.

## Global Constraints

- `db_type` must support `"mssql"`, `"netezza"`, and `"oracle"`.
- Default port for Oracle: `1521`. Default driver: `"oracledb"`.
- Oracle connection string format: `oracle+oracledb://{user}:{password}@{host}:{port}/?service_name={db_name}` with URL-encoded user and password.
- Identifier quoting for Oracle: double quotes (`"COLUMN"`).
- Pagination for Oracle: `OFFSET {offset} ROWS FETCH NEXT {chunk_size} ROWS ONLY`.

---

### Task 1: Add `oracledb` Optional Dependency and Update `EnvironmentConfig`

**Files:**
- Modify: `pyproject.toml`
- Modify: `etl_framework/config/models.py`
- Test: `tests/test_oracle_integration.py`

**Interfaces:**
- Consumes: None
- Produces: `EnvironmentConfig` accepting `db_type="oracle"`, auto-setting `db_port=1521` and `db_driver="oracledb"` when `db_type="oracle"`.

- [ ] **Step 1: Write failing test for `EnvironmentConfig` with Oracle**

Create `tests/test_oracle_integration.py`:

```python
from etl_framework.config.models import EnvironmentConfig


def test_oracle_environment_config_defaults():
    config = EnvironmentConfig(
        name="test_oracle",
        db_type="oracle",
        db_host="localhost",
        db_name="ORCLPDB1",
        db_user="sys",
        db_password="password",
    )
    assert config.db_type == "oracle"
    assert config.db_port == 1521
    assert config.db_driver == "oracledb"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_oracle_integration.py -v`  
Expected: FAIL with validation error (`Literal["mssql", "netezza"]` mismatch or default value mismatch).

- [ ] **Step 3: Modify `pyproject.toml` and `etl_framework/config/models.py`**

In `pyproject.toml`, add `oracle` to `[project.optional-dependencies]`:
```toml
oracle = ["oracledb>=2.0.0"]
```

In `etl_framework/config/models.py`, update `db_type` definitions and defaults:
```python
db_type: Literal["mssql", "netezza", "oracle"] = "mssql"
```
In `set_db_type_defaults`:
```python
if data.get("db_type") == "oracle":
    if not data.get("db_port"):
        data["db_port"] = 1521
    if not data.get("db_driver"):
        data["db_driver"] = "oracledb"
```
And update `EnvironmentOverrideConfig`:
```python
db_type: Literal["mssql", "netezza", "oracle"] | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_oracle_integration.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml etl_framework/config/models.py tests/test_oracle_integration.py
git commit -m "feat(config): add oracle to db_type in EnvironmentConfig"
```

---

### Task 2: Implement Oracle URL Construction in `DBEngine`

**Files:**
- Modify: `etl_framework/db/engine.py`
- Test: `tests/test_oracle_integration.py`

**Interfaces:**
- Consumes: `EnvironmentConfig` with `db_type="oracle"`
- Produces: `DBEngine` instance initializing SQLAlchemy engine with `oracle+oracledb://` driver URL.

- [ ] **Step 1: Write failing test for `DBEngine` with Oracle config**

Add to `tests/test_oracle_integration.py`:

```python
from unittest.mock import patch
from etl_framework.config.models import EnvironmentConfig
from etl_framework.db.engine import DBEngine


def test_db_engine_oracle_connection_string():
    config = EnvironmentConfig(
        name="test_oracle",
        db_type="oracle",
        db_host="oracle-server",
        db_port=1521,
        db_name="ORCLPDB1",
        db_user="admin",
        db_password="p@ssword#123",
    )
    with patch("etl_framework.db.engine.create_engine") as mock_create_engine:
        engine = DBEngine(config)
        mock_create_engine.assert_called_once()
        connection_url = mock_create_engine.call_args[0][0]
        assert connection_url.startswith("oracle+oracledb://admin:p%40ssword%23123@oracle-server:1521/?service_name=ORCLPDB1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_oracle_integration.py::test_db_engine_oracle_connection_string -v`  
Expected: FAIL (connection URL generated as `mssql+pyodbc` instead of `oracle+oracledb`).

- [ ] **Step 3: Modify `etl_framework/db/engine.py`**

In `DBEngine.__init__`, add branch for `oracle`:

```python
            if getattr(env_config, "db_type", "mssql") == "netezza":
                _ensure_netezza_dialect()
                ...
            elif getattr(env_config, "db_type", "mssql") == "oracle":
                user = urllib.parse.quote_plus(env_config.db_user)
                pwd = urllib.parse.quote_plus(env_config.db_password)
                connection_url = (
                    f"oracle+oracledb://{user}:{pwd}"
                    f"@{env_config.db_host}:{env_config.db_port}/?service_name={env_config.db_name}"
                )
            else:
                ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_oracle_integration.py::test_db_engine_oracle_connection_string -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/db/engine.py tests/test_oracle_integration.py
git commit -m "feat(db): add oracle connection URL support in DBEngine"
```

---

### Task 3: Support Oracle Identifier Quoting and Reconciliation Chunk Queries

**Files:**
- Modify: `etl_framework/db/sql_utils.py`
- Modify: `etl_framework/reconciliation/chunker.py`
- Test: `tests/test_oracle_integration.py`

**Interfaces:**
- Consumes: `quote_identifier(identifier, dialect="oracle")`, `build_chunk_query(..., dialect="oracle")`
- Produces: Correct double-quoted Oracle identifiers and ANSI `OFFSET ... FETCH NEXT ...` chunking queries.

- [ ] **Step 1: Write failing test for Oracle identifier quoting and chunk queries**

Add to `tests/test_oracle_integration.py`:

```python
from etl_framework.db.sql_utils import quote_identifier
from etl_framework.reconciliation.chunker import build_chunk_query, build_hash_query


def test_quote_identifier_oracle():
    assert quote_identifier("id", "oracle") == '"id"'
    assert quote_identifier("MY_COL", "oracle") == '"MY_COL"'


def test_oracle_chunk_query():
    query = build_chunk_query("SELECT id, name FROM users", ["id"], 0, 100, dialect="oracle")
    assert '"id"' in query
    assert "OFFSET 0 ROWS FETCH NEXT 100 ROWS ONLY" in query
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_oracle_integration.py::test_quote_identifier_oracle -v`  
Expected: FAIL (`quote_identifier` uses default ANSI quotes or fails parameter check in `build_chunk_query`).

- [ ] **Step 3: Modify `etl_framework/db/sql_utils.py` and `etl_framework/reconciliation/chunker.py`**

In `etl_framework/db/sql_utils.py`:
```python
def quote_identifier(identifier: str, dialect: str = "sqlserver") -> str:
    d = dialect.lower()
    if d in {"sqlserver", "mssql"}:
        cleaned = identifier.strip()
        if not _BRACKET_SAFE_RE.fullmatch(cleaned):
            raise ValueError(f"Invalid SQL identifier: {identifier!r}")
        return f"[{cleaned}]"
    validate_identifier(identifier)
    if d == "mysql":
        return f"`{identifier}`"
    # oracle and ansi use double quotes
    return '"' + identifier.replace('"', '""') + '"'
```

In `etl_framework/reconciliation/chunker.py`:
Update `_validate_columns`, `_quote_col`, `build_hash_query`, and `build_chunk_query` to accept an optional `dialect: str = "sqlserver"` parameter, passing `dialect` down to `quote_identifier`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_oracle_integration.py::test_quote_identifier_oracle tests/test_oracle_integration.py::test_oracle_chunk_query -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/db/sql_utils.py etl_framework/reconciliation/chunker.py tests/test_oracle_integration.py
git commit -m "feat(reconciliation): add oracle dialect support to sql_utils and chunker"
```

---

### Task 4: Web UI Dropdowns & Frontend Connection Handlers

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/partials/tab-config.html`
- Modify: `frontend/partials/tab-compare.html`
- Modify: `frontend/features/config.js`
- Modify: `frontend/features/compare.js`
- Test: `tests/e2e/test_oracle_ui_options.spec.ts` or unit test in python

**Interfaces:**
- Consumes: User selecting `oracle` in DB Type dropdown.
- Produces: Auto-setting `db_port=1521` and `db_driver='oracledb'` in Config modal & Compare connections.

- [ ] **Step 1: Write test for Oracle frontend connection fields**

Add to `tests/test_oracle_integration.py`:

```python
from etl_framework.repository.models import ConnectionConfig


def test_oracle_connection_model_serialization():
    conn = ConnectionConfig(
        db_type="oracle",
        db_host="oracle.example.com",
        db_port=1521,
        db_name="ORCLPDB1",
        db_user="sys",
        db_password="password",
        db_driver="oracledb",
    )
    data = conn.model_dump()
    assert data["db_type"] == "oracle"
    assert data["db_port"] == 1521
    assert data["db_driver"] == "oracledb"
```

- [ ] **Step 2: Run test to verify model accepts Oracle**

Run: `pytest tests/test_oracle_integration.py::test_oracle_connection_model_serialization -v`  
Expected: PASS/FAIL depending on model validations.

- [ ] **Step 3: Modify Frontend HTML & JS files**

In `frontend/index.html` and `frontend/partials/tab-config.html`:
Add option to `<select>` elements:
```html
<option value="oracle">Oracle Database</option>
```
Update `@change` handlers:
```js
if (configModal.db_type === 'oracle') { configModal.db_port = 1521; configModal.db_driver = 'oracledb'; }
```

In `frontend/partials/tab-compare.html`:
Add option to connection `<select>` elements:
```html
<option value="oracle">Oracle Database</option>
```

In `frontend/features/config.js` and `frontend/features/compare.js`:
Update helper logic to handle `db_type === 'oracle'` for setting default ports (1521) and drivers (`oracledb`).

- [ ] **Step 4: Verify syntax and linting**

Run: `npm run lint` or `node -c frontend/features/config.js`  
Expected: Clean execution.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/partials/tab-config.html frontend/partials/tab-compare.html frontend/features/config.js frontend/features/compare.js
git commit -m "feat(ui): add Oracle option to database selection dropdowns and auto-fill logic"
```

---

### Task 5: End-to-End Test Suite for Oracle Connection & Reconciliation

**Files:**
- Modify: `tests/test_oracle_integration.py`

**Interfaces:**
- Consumes: All Oracle components (Config, Engine, SQL Utils, Chunker, Repository models).
- Produces: Verified complete test coverage for Oracle integration.

- [ ] **Step 1: Add full integration test suite in `tests/test_oracle_integration.py`**

```python
import pytest
from etl_framework.config.models import EnvironmentConfig
from etl_framework.db.engine import DBEngine
from etl_framework.db.sql_utils import quote_identifier
from etl_framework.reconciliation.chunker import build_chunk_query, build_hash_query


def test_oracle_full_reconciliation_workflow_dryrun():
    env = EnvironmentConfig(
        name="prod_oracle",
        db_type="oracle",
        db_host="oracle.internal",
        db_port=1521,
        db_name="ORCLSERVICE",
        db_user="etl_user",
        db_password="secret_password",
    )
    assert env.db_driver == "oracledb"
    assert env.db_port == 1521

    # Verify query builder for Oracle
    hash_q = build_hash_query("SELECT id, amount FROM sales", ["id"], dialect="oracle")
    chunk_q = build_chunk_query("SELECT id, amount FROM sales", ["id"], 0, 500, dialect="oracle")

    assert '"id"' in hash_q
    assert "OFFSET 0 ROWS FETCH NEXT 500 ROWS ONLY" in chunk_q
```

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/test_oracle_integration.py -v`  
Expected: PASS (all tests pass green).

- [ ] **Step 3: Run existing unit test suite to ensure no regression**

Run: `pytest tests/ -v`  
Expected: PASS (no regressions).

- [ ] **Step 4: Commit**

```bash
git add tests/test_oracle_integration.py
git commit -m "test: add comprehensive unit test suite for Oracle connection and reconciliation"
```
