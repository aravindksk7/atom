# PostgreSQL / Aurora + Bulk Compare Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add PostgreSQL and Aurora PostgreSQL as config DB types, make SQL-to-SQL compares saveable as jobs, replace OFFSET paging with cursor-batched loading, and let users bulk-import many table compare jobs from a CSV/XLSX mapping sheet.

**Architecture:** New DB types flow through the existing `EnvironmentConfig` → `DBEngine` path with a shared `postgresql+psycopg` branch. Chunked loading becomes one query read in cursor batches (`DBEngine.iter_query_batches`), removing per-dialect paging SQL. A new `compare_type: "sql"` wraps the existing `SQLCompareRequest`; a new service `api/services/compare_bulk_import.py` compiles sheet rows into such jobs, exposed via preview/commit endpoints and a Job Catalog modal.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, pandas, psycopg 3, openpyxl; Alpine.js frontend built by `scripts/build-html.js`; pytest; Playwright.

**Spec:** `docs/superpowers/specs/2026-10-19-postgres-aurora-bulk-compare-design.md`

---

## Ground rules for every task

- Run Python tests with raw pytest: `python -m pytest ...` (the rtk wrapper caches stale summaries).
- The working tree has an unrelated, uncommitted user change in `frontend/features/diff-search.js`. **Never stage or commit it.** `node scripts/build-html.js` regenerates that file, so every build step below backs it up first and restores it afterwards.
- Commit with explicit paths only (`git add <paths>`), never `git add -A`/`git add .`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

## File map

| File | Change |
|---|---|
| `etl_framework/config/models.py` | `DbType` alias with two new types, `db_sslmode`, Postgres defaults |
| `etl_framework/db/engine.py` | Postgres URL branch, `_ensure_psycopg`, `iter_query_batches` |
| `etl_framework/reconciliation/chunker.py` | `load_in_chunks` uses cursor batches; delete `build_chunk_query` |
| `api/services/run_executor.py` | Remove OFFSET emulation from `DataFrameQueryEngine`; `sql` compare branch |
| `api/routes/configs.py` | Postgres schema discovery + preview |
| `api/schemas.py` | `compare_type: "sql"` validation |
| `etl_framework/runner/job_validation.py` | `compare_type: "sql"` validation |
| `api/services/compare_service.py` | `compare_sql()` core extracted from `run_sql_comparison` |
| `api/services/compare_bulk_import.py` | **New**: sheet parsing, SQL generation, row validation, job build |
| `api/routes/jobs.py` | `bulk-compare/template`, `/preview`, `/commit` |
| `frontend/features/config.js`, `frontend/partials/tab-config.html` | New DB types, SSL mode, Aurora hint |
| `frontend/features/compare.js`, `frontend/features/launch.js`, `frontend/partials/tab-compare.html` | SQL compare Save as Job + edit prefill |
| `frontend/features/bulk-compare.js` | **New**: bulk import modal state/actions |
| `frontend/partials/tab-launch.html`, `frontend/index.template.html`, `frontend/app.js` | Modal markup, script tag, slice registration |
| `pyproject.toml`, `requirements.txt` | `psycopg[binary]` |
| `docker-compose.integration.yml` | `postgres` service |
| Tests | see each task |

---

### Task 1: Config model — PostgreSQL and Aurora PostgreSQL types

**Files:**
- Modify: `etl_framework/config/models.py:18-82` (EnvironmentConfig + validator), `:163-166` (ConnectionEntry)
- Test: `tests/unit/test_config_models_postgres.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""PostgreSQL / Aurora PostgreSQL defaults on EnvironmentConfig and ConnectionEntry."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from etl_framework.config.models import ConnectionEntry, EnvironmentConfig, resolve_connection


def _env(**overrides) -> EnvironmentConfig:
    base = {"db_host": "pg.local", "db_password": "pw"}
    return EnvironmentConfig(**{**base, **overrides})


def test_postgresql_defaults_port_driver_and_prefer_sslmode():
    env = _env(db_type="postgresql")
    assert env.db_port == 5432
    assert env.db_driver == "psycopg"
    assert env.db_sslmode == "prefer"


def test_aurora_postgresql_defaults_to_require_sslmode():
    env = _env(db_type="aurora_postgresql")
    assert env.db_port == 5432
    assert env.db_driver == "psycopg"
    assert env.db_sslmode == "require"


def test_explicit_port_and_sslmode_are_kept():
    env = _env(db_type="aurora_postgresql", db_port=6543, db_sslmode="verify-full")
    assert env.db_port == 6543
    assert env.db_sslmode == "verify-full"


def test_sql_server_odbc_driver_is_replaced_for_postgres():
    env = _env(db_type="postgresql", db_driver="ODBC Driver 18 for SQL Server")
    assert env.db_driver == "psycopg"


def test_mssql_sslmode_stays_blank():
    assert _env().db_sslmode == ""


def test_unknown_db_type_rejected():
    with pytest.raises(ValidationError):
        _env(db_type="mysql")


def test_connection_entry_accepts_postgres_types():
    assert ConnectionEntry(db_type="aurora_postgresql", db_sslmode="require").db_type == "aurora_postgresql"


def test_named_connection_switches_base_mssql_config_to_postgres_defaults():
    config_json = {
        "db_type": "mssql", "db_host": "sql.local", "db_port": 1433, "db_password": "pw",
        "connections": {"warehouse": {"db_type": "postgresql", "db_host": "pg.local"}},
    }
    env = resolve_connection(config_json, "warehouse")
    assert env.db_type == "postgresql"
    assert env.db_port == 5432
    assert env.db_host == "pg.local"
    assert env.db_sslmode == "prefer"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_config_models_postgres.py -v`
Expected: FAIL — `db_type` Literal rejects `"postgresql"`, `db_sslmode` attribute missing.

- [ ] **Step 3: Implement**

In `etl_framework/config/models.py`, add after the `SECRET_FIELDS` block:

```python
DbType = Literal["mssql", "netezza", "oracle", "postgresql", "aurora_postgresql"]
POSTGRES_DB_TYPES = frozenset({"postgresql", "aurora_postgresql"})
```

In `EnvironmentConfig`, replace the `db_type` line and add `db_sslmode` right after `db_connect_timeout`:

```python
    db_type: DbType = "mssql"
```

```python
    db_connect_timeout: int = 15
    # libpq sslmode for postgresql / aurora_postgresql; ignored by other types.
    db_sslmode: str = ""
```

In `set_db_type_defaults`, add a branch after the `oracle` branch (before `return data`):

```python
            elif data.get("db_type") in POSTGRES_DB_TYPES:
                if "db_port" not in data or data.get("db_port") == 1433:
                    data["db_port"] = 5432
                if "db_driver" not in data or "SQL Server" in str(data.get("db_driver")):
                    data["db_driver"] = "psycopg"
                if not data.get("db_sslmode"):
                    data["db_sslmode"] = (
                        "require" if data["db_type"] == "aurora_postgresql" else "prefer"
                    )
```

In `ConnectionEntry`, replace the `db_type` line and add `db_sslmode` after `db_connect_timeout`:

```python
    db_type: DbType | None = None
```

```python
    db_connect_timeout: int | None = None
    db_sslmode: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_config_models_postgres.py tests/unit/test_config_models_ds.py tests/test_oracle_integration.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/config/models.py tests/unit/test_config_models_postgres.py
git commit -m "feat: postgresql and aurora postgresql config db types"
```

---

### Task 2: DBEngine — Postgres connection URL and driver dependency

**Files:**
- Modify: `etl_framework/db/engine.py` (add `_ensure_psycopg`, new branch in `DBEngine.__init__`)
- Modify: `pyproject.toml` (optional extra), `requirements.txt`
- Test: `tests/unit/test_db_engine.py` (append)

- [ ] **Step 1: Install the driver**

Run: `python -m pip install "psycopg[binary]>=3.1"`
Expected: `Successfully installed psycopg-...` (or already satisfied).

- [ ] **Step 2: Write the failing tests** (append to `tests/unit/test_db_engine.py`)

```python
def _pg_cfg(**overrides):
    base = dict(
        name="pg", db_type="postgresql", db_host="pg.local",
        db_name="sales db", db_user="etl@corp", db_password="p@ss:word",
        db_connect_timeout=7,
    )
    return EnvironmentConfig(**{**base, **overrides})


def test_postgresql_url_quotes_credentials_and_sets_ssl_and_timeout():
    with patch("etl_framework.db.engine.create_engine") as mock_create:
        DBEngine(_pg_cfg())
    url = mock_create.call_args[0][0]
    assert url == (
        "postgresql+psycopg://etl%40corp:p%40ss%3Aword@pg.local:5432/sales%20db"
        "?sslmode=prefer&connect_timeout=7"
    )


def test_aurora_postgresql_url_requires_ssl():
    with patch("etl_framework.db.engine.create_engine") as mock_create:
        DBEngine(_pg_cfg(db_type="aurora_postgresql", db_host="c.cluster-x.eu-west-1.rds.amazonaws.com"))
    url = mock_create.call_args[0][0]
    assert url.startswith("postgresql+psycopg://")
    assert "@c.cluster-x.eu-west-1.rds.amazonaws.com:5432/" in url
    assert "sslmode=require" in url


def test_postgresql_passes_pool_settings():
    with patch("etl_framework.db.engine.create_engine") as mock_create:
        DBEngine(_pg_cfg(db_pool_size=3, db_pool_overflow=4))
    kwargs = mock_create.call_args[1]
    assert kwargs["pool_size"] == 3
    assert kwargs["max_overflow"] == 4


def test_postgresql_without_psycopg_names_the_extra(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(ImportError, match=r"etl-framework\[postgres\]"):
        DBEngine(_pg_cfg())
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_db_engine.py -k "postgres" -v`
Expected: FAIL — URL built as `mssql+pyodbc://...`.

- [ ] **Step 4: Implement**

In `etl_framework/db/engine.py`, add the import `from etl_framework.config.models import EnvironmentConfig, POSTGRES_DB_TYPES` (replacing the existing single-name import), and add this function after `_ensure_netezza_dialect`:

```python
def _ensure_psycopg() -> None:
    """Fail with an actionable message when the PostgreSQL driver is missing."""
    try:
        import psycopg  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "PostgreSQL / Aurora PostgreSQL support requires the 'psycopg' driver. "
            "Install it with: pip install 'etl-framework[postgres]'."
        ) from exc
```

In `DBEngine.__init__`, add a branch between the `netezza` branch and the final `else`:

```python
            elif db_type in POSTGRES_DB_TYPES:
                _ensure_psycopg()
                user = urllib.parse.quote(env_config.db_user, safe="")
                pwd = urllib.parse.quote(env_config.db_password, safe="")
                database = urllib.parse.quote(env_config.db_name, safe="")
                query = urllib.parse.urlencode({
                    "sslmode": env_config.db_sslmode or "prefer",
                    "connect_timeout": env_config.db_connect_timeout,
                })
                connection_url = (
                    f"postgresql+psycopg://{user}:{pwd}"
                    f"@{env_config.db_host}:{env_config.db_port}/{database}?{query}"
                )
```

In `pyproject.toml` under `[project.optional-dependencies]`, after the `oracle = [...]` line:

```toml
postgres = ["psycopg[binary]>=3.1"]
```

In `requirements.txt`, after `duckdb>=0.10`:

```
psycopg[binary]>=3.1
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_db_engine.py tests/test_oracle_integration.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add etl_framework/db/engine.py pyproject.toml requirements.txt tests/unit/test_db_engine.py
git commit -m "feat: postgres connection url for postgresql and aurora configs"
```

---

### Task 3: DBEngine.iter_query_batches — one query, cursor batches

**Files:**
- Modify: `etl_framework/db/engine.py` (`DBEngine` class)
- Test: `tests/unit/test_db_engine.py` (append)

- [ ] **Step 1: Write the failing tests** (append; reuses the existing `cfg` and `sqlite_engine` fixtures at the top of the file)

```python
@pytest.fixture
def sqlite_five_rows():
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE nums (id INTEGER PRIMARY KEY, v TEXT)"))
        for i in range(1, 6):
            conn.execute(text("INSERT INTO nums VALUES (:i, :v)"), {"i": i, "v": f"v{i}"})
        conn.commit()
    return engine


def test_iter_query_batches_yields_batches_of_requested_size(cfg, sqlite_five_rows):
    with patch("etl_framework.db.engine.create_engine", return_value=sqlite_five_rows):
        engine = DBEngine(cfg)
    batches = list(engine.iter_query_batches("SELECT id, v FROM nums ORDER BY id", batch_size=2))
    assert [len(b) for b in batches] == [2, 2, 1]
    assert [int(i) for b in batches for i in b["id"]] == [1, 2, 3, 4, 5]


def test_iter_query_batches_binds_params(cfg, sqlite_five_rows):
    with patch("etl_framework.db.engine.create_engine", return_value=sqlite_five_rows):
        engine = DBEngine(cfg)
    batches = list(engine.iter_query_batches("SELECT id FROM nums WHERE id > :lo", {"lo": 3}, batch_size=10))
    assert sorted(int(i) for i in batches[0]["id"]) == [4, 5]


def test_iter_query_batches_empty_result_yields_nothing(cfg, sqlite_five_rows):
    with patch("etl_framework.db.engine.create_engine", return_value=sqlite_five_rows):
        engine = DBEngine(cfg)
    assert list(engine.iter_query_batches("SELECT id FROM nums WHERE id > 99", batch_size=2)) == []


def test_iter_query_batches_rejects_non_positive_batch_size(cfg, sqlite_five_rows):
    with patch("etl_framework.db.engine.create_engine", return_value=sqlite_five_rows):
        engine = DBEngine(cfg)
    with pytest.raises(ValueError, match="batch_size"):
        list(engine.iter_query_batches("SELECT 1", batch_size=0))


def test_iter_query_batches_closing_early_returns_connection(cfg, sqlite_five_rows):
    with patch("etl_framework.db.engine.create_engine", return_value=sqlite_five_rows):
        engine = DBEngine(cfg)
    gen = engine.iter_query_batches("SELECT id FROM nums", batch_size=1)
    next(gen)
    gen.close()
    assert sqlite_five_rows.pool.checkedout() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_db_engine.py -k "iter_query_batches" -v`
Expected: FAIL — `AttributeError: 'DBEngine' object has no attribute 'iter_query_batches'`.

- [ ] **Step 3: Implement** — add to `DBEngine` right after `execute_query`, and add `from collections.abc import Iterator` to the imports:

```python
    def iter_query_batches(
        self, query: str, params: dict | None = None, *, batch_size: int,
    ) -> Iterator[pd.DataFrame]:
        """Run ``query`` once and yield its rows in DataFrames of ``batch_size``.

        ``stream_results`` asks the driver for a server-side cursor where it has
        one (psycopg); pyodbc, oracledb and nzpy fall back to ``fetchmany``
        batching. Either way there is no ORDER BY/OFFSET paging, so no
        per-dialect SQL and no need for unique keys. The connection is held
        until the generator is exhausted or closed.
        """
        if batch_size <= 0:
            raise ValueError(f"batch_size must be > 0, got {batch_size}")
        with self._engine.connect().execution_options(stream_results=True) as conn:
            yield from pd.read_sql(text(query), conn, params=params or {}, chunksize=batch_size)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_db_engine.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/db/engine.py tests/unit/test_db_engine.py
git commit -m "feat: cursor-batched query reads on DBEngine"
```

---

### Task 4: load_in_chunks uses cursor batches; remove OFFSET paging

**Files:**
- Modify: `etl_framework/reconciliation/chunker.py` (delete `build_chunk_query`, rewrite `load_in_chunks`)
- Modify: `api/services/run_executor.py:122-150` (`DataFrameQueryEngine`)
- Modify: `tests/unit/test_chunker.py`, `tests/test_oracle_integration.py`

- [ ] **Step 1: Replace the chunker tests**

In `tests/unit/test_chunker.py`:
- Remove `build_chunk_query` from the import list.
- Delete every `test_build_chunk_query_*` test (they assert OFFSET output, and the bad-key / empty-key validation tests that call `build_chunk_query`).
- Replace the `_WindowEngine` class and the three `test_load_in_chunks_*` tests with:

```python
class _BatchEngine:
    """Fake engine with cursor batching, recording how it was called."""

    def __init__(self, df: pd.DataFrame) -> None:
        self._env = type("E", (), {"name": "dev"})()
        self._df = df
        self.batch_calls: list[tuple[str, dict | None, int]] = []
        self.execute_calls: list[str] = []

    def execute_query(self, query: str, params=None) -> pd.DataFrame:
        self.execute_calls.append(query)
        return self._df.copy()

    def iter_query_batches(self, query: str, params=None, *, batch_size: int):
        self.batch_calls.append((query, params, batch_size))
        for start in range(0, len(self._df), batch_size):
            yield self._df.iloc[start:start + batch_size].reset_index(drop=True)


class _PlainEngine:
    """Fake engine without iter_query_batches (file-backed engines, test doubles)."""

    def __init__(self, df: pd.DataFrame) -> None:
        self._env = type("E", (), {"name": "dev"})()
        self._df = df
        self.queries: list[str] = []

    def execute_query(self, query: str, params=None) -> pd.DataFrame:
        self.queries.append(query)
        return self._df.copy()


def test_load_in_chunks_reads_one_query_in_batches():
    df = pd.DataFrame({"id": [1, 2, 3, 4, 5], "v": list("abcde")})
    engine = _BatchEngine(df)
    out = load_in_chunks(engine, "SELECT * FROM t", ["id"], 2)
    assert list(out["id"]) == [1, 2, 3, 4, 5]
    assert engine.batch_calls == [("SELECT * FROM t", None, 2)]
    assert engine.execute_calls == []


def test_load_in_chunks_does_not_rewrite_the_query():
    engine = _BatchEngine(pd.DataFrame({"id": [1]}))
    load_in_chunks(engine, "SELECT id FROM t", ["id"], 10)
    sent = engine.batch_calls[0][0]
    assert "OFFSET" not in sent.upper()
    assert "ORDER BY" not in sent.upper()


def test_load_in_chunks_batches_without_key_columns():
    engine = _BatchEngine(pd.DataFrame({"id": [1, 2, 3]}))
    out = load_in_chunks(engine, "SELECT id FROM t", [], 2)
    assert len(out) == 3
    assert len(engine.batch_calls) == 1


def test_load_in_chunks_passes_params():
    engine = _BatchEngine(pd.DataFrame({"id": [1]}))
    load_in_chunks(engine, "SELECT id FROM t WHERE d = :d", ["id"], 5, params={"d": "2026-10-19"})
    assert engine.batch_calls[0][1] == {"d": "2026-10-19"}


def test_load_in_chunks_single_read_when_disabled():
    engine = _BatchEngine(pd.DataFrame({"id": [1, 2]}))
    assert len(load_in_chunks(engine, "q", ["id"], 0)) == 2
    assert engine.batch_calls == []
    assert engine.execute_calls == ["q"]


def test_load_in_chunks_single_read_for_engine_without_batching():
    engine = _PlainEngine(pd.DataFrame({"id": [1, 2, 3]}))
    out = load_in_chunks(engine, "SELECT * FROM t", ["id"], 2)
    assert len(out) == 3
    assert engine.queries == ["SELECT * FROM t"]


def test_load_in_chunks_applies_normalize_per_batch():
    engine = _BatchEngine(pd.DataFrame({"id": [1, 2, 3], "v": [1, 2, 3]}))
    out = load_in_chunks(
        engine, "SELECT * FROM t", ["id"], 2,
        normalize=lambda d: d.assign(v=d["v"] * 10),
    )
    assert list(out["v"]) == [10, 20, 30]


def test_load_in_chunks_empty_result_returns_empty_frame():
    engine = _BatchEngine(pd.DataFrame({"id": []}))
    out = load_in_chunks(engine, "SELECT * FROM t", ["id"], 2)
    assert out.empty
```

In `tests/test_oracle_integration.py`:
- Change the import to `from etl_framework.reconciliation.chunker import build_hash_query`.
- Delete `test_oracle_chunk_query`.
- In `test_oracle_full_reconciliation_workflow_dryrun`, delete the `chunk_q = build_chunk_query(...)` line and the `assert "OFFSET 0 ROWS FETCH NEXT 500 ROWS ONLY" in chunk_q` line.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_chunker.py -v`
Expected: FAIL — `test_load_in_chunks_reads_one_query_in_batches` (current code calls `execute_query` with OFFSET SQL), `test_load_in_chunks_batches_without_key_columns`.

- [ ] **Step 3: Implement**

In `etl_framework/reconciliation/chunker.py`, delete `build_chunk_query` entirely and replace `load_in_chunks` with:

```python
def load_in_chunks(
    engine,
    query: str,
    key_columns: list[str],
    chunk_size: int,
    params: dict | None = None,
    normalize=None,
) -> pd.DataFrame:
    """Load a query fully, reading it in cursor batches of ``chunk_size`` rows.

    One query, no ORDER BY/OFFSET paging: engines with ``iter_query_batches``
    (DBEngine) stream it; anything else, or ``chunk_size`` 0, gets a single
    read. ``key_columns`` is no longer needed for loading and is kept for
    callers. ``normalize``, when given, is applied to every batch.
    """
    _n = normalize or (lambda df: df)
    iter_batches = getattr(engine, "iter_query_batches", None)
    if not chunk_size or iter_batches is None:
        if params is not None:
            return _n(engine.execute_query(query, params))
        return _n(engine.execute_query(query))
    parts = [_n(batch) for batch in iter_batches(query, params, batch_size=chunk_size)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
```

The module still needs `quote_identifier` / `reject_mutating_sql` for `build_hash_query`; keep those imports and `_validate_columns` / `_quote_col`.

In `api/services/run_executor.py`, simplify `DataFrameQueryEngine` (the OFFSET window and `_base` unwrapping only existed for `build_chunk_query`, and this class has no `iter_query_batches`, so it now always gets the raw query):

```python
class DataFrameQueryEngine:
    def __init__(self, env_name: str, frames: dict[str, pd.DataFrame]) -> None:
        self._env = _Env(env_name)
        self._frames = frames

    def execute_query(self, query: str, params: dict | None = None) -> pd.DataFrame:
        if query in self._frames:
            return self._frames[query].copy()
        if "__default__" in self._frames:
            return self._frames["__default__"].copy()
        return pd.DataFrame()

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False
```

Then check whether `re` is still used in `run_executor.py`: run `python -m pyflakes api/services/run_executor.py` if available, otherwise search `re\.` in the file; remove `import re` only if no other use remains.

- [ ] **Step 4: Confirm no other users of the removed code**

Use Grep for `build_chunk_query|_apply_window|_extract_base_query` across `api`, `etl_framework`, `tests` (exclude `__pycache__`).
Expected: no matches.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_chunker.py tests/test_oracle_integration.py tests/unit/test_run_executor.py tests/unit/test_run_executor_compare.py -q`
Then the wider reconciliation/compare suites: `python -m pytest tests/unit -q -k "chunk or reconcil or compare or engine"`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add etl_framework/reconciliation/chunker.py api/services/run_executor.py tests/unit/test_chunker.py tests/test_oracle_integration.py
git commit -m "refactor: chunked loading reads one query in cursor batches instead of offset paging"
```

---

### Task 5: Config routes — Postgres schema discovery and preview

**Files:**
- Modify: `api/routes/configs.py:358-470` (`schema` and `preview_query` handlers)
- Test: `tests/unit/test_config_routes_postgres.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Schema discovery and preview SQL for postgresql / aurora_postgresql configs."""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import ConfigRepository, TokenRepository


class _RecordingEngine:
    queries: list[str] = []

    def __init__(self, env) -> None:
        self.env = env

    def execute_query(self, query: str, params=None) -> pd.DataFrame:
        _RecordingEngine.queries.append(query)
        return pd.DataFrame({
            "table_schema": ["public"], "table_name": ["orders"],
            "column_name": ["id"], "data_type": ["integer"],
        })

    def dispose(self) -> None:
        pass


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("etl_framework.db.engine.DBEngine", _RecordingEngine)
    _RecordingEngine.queries = []
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("admin", is_admin=True)
        for name, db_type in (("pg", "postgresql"), ("aurora", "aurora_postgresql")):
            ConfigRepository(db).create(name, "dev", {
                "db_type": db_type, "db_host": "h", "db_name": "d",
                "db_user": "u", "db_password": "p",
            })
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c


def _config_id(client, name: str) -> int:
    return next(c["id"] for c in client.get("/api/configs").json() if c["name"] == name)


@pytest.mark.parametrize("name", ["pg", "aurora"])
def test_schema_discovery_uses_information_schema_without_system_schemas(client, name):
    resp = client.get(f"/api/configs/{_config_id(client, name)}/schema")
    assert resp.status_code == 200, resp.text
    sql = _RecordingEngine.queries[-1]
    assert "information_schema.columns" in sql.lower()
    assert "'pg_catalog'" in sql and "'information_schema'" in sql
    assert resp.json() == [{"schema": "public", "table": "orders", "columns": [{"name": "id", "type": "integer"}]}]


@pytest.mark.parametrize("name", ["pg", "aurora"])
def test_preview_uses_limit(client, name):
    resp = client.post(
        f"/api/configs/{_config_id(client, name)}/preview-query",
        json={"query": "SELECT * FROM orders", "limit": 7},
    )
    assert resp.status_code == 200, resp.text
    assert _RecordingEngine.queries[-1] == "SELECT * FROM (SELECT * FROM orders) AS _preview LIMIT 7"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_config_routes_postgres.py -v`
Expected: FAIL — schema query is the SQL Server form (no `pg_catalog` exclusion); preview uses `TOP 7`.

- [ ] **Step 3: Implement**

At the top of `api/routes/configs.py` add `from etl_framework.config.models import POSTGRES_DB_TYPES` (next to the existing `etl_framework.config.models` import).

In the schema handler, add a branch between the `netezza` branch and the `else`:

```python
        elif db_type in POSTGRES_DB_TYPES:
            query = (
                "SELECT table_schema, table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY table_schema, table_name, ordinal_position"
            )
```

(The existing column lookup upper-cases result column names, so lower-case Postgres column names map correctly.)

In `preview_query`, change the `netezza` branch to cover Postgres too:

```python
        elif db_type == "netezza" or db_type in POSTGRES_DB_TYPES:
            safe_sql = f"SELECT * FROM ({body.query}) AS _preview LIMIT {limit}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_config_routes_postgres.py -v` and `python -m pytest tests/unit -q -k "config"`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routes/configs.py tests/unit/test_config_routes_postgres.py
git commit -m "feat: postgres schema discovery and query preview"
```

---

### Task 6: `compare_type: "sql"` — job validation

**Files:**
- Modify: `api/schemas.py:898-930` (`JobDefinition.validate_reconciliation_contract`, compare branch)
- Modify: `etl_framework/runner/job_validation.py:9` (import), `:351-427` (compare branch)
- Modify: `tests/unit/test_compare_job_type.py`, `tests/unit/test_run_executor_compare.py` (they use `"sql"` as the example of an *unknown* type)
- Test: `tests/unit/test_compare_job_type.py` (append), `tests/unit/test_job_validation.py` (append)

- [ ] **Step 1: Repoint the "unknown compare_type" tests**

In `tests/unit/test_compare_job_type.py::test_compare_job_requires_a_known_compare_type` change `"compare_type": "sql"` to `"compare_type": "bogus"`.
In `tests/unit/test_run_executor_compare.py::test_compare_job_with_an_unknown_compare_type_raises` change `params={"compare_type": "sql", "request": {}}` to `params={"compare_type": "bogus", "request": {}}` (and any `match=` string mentioning `'sql'` to `'bogus'`).

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_compare_job_type.py`:

```python
def _sql_request(**overrides) -> dict:
    request = {
        "config_id_a": 1,
        "config_id_b": 2,
        "query_a": "SELECT id, amount FROM dbo.orders",
        "query_b": "SELECT id, amount FROM public.orders",
        "key_columns": ["id"],
    }
    return {**request, **overrides}


def test_compare_job_accepts_a_sql_request():
    job = JobDefinition(
        name="orders_cmp", job_type="compare",
        params={"compare_type": "sql", "request": _sql_request()},
    )
    assert job.key_columns == ["id"]


def test_compare_job_sql_request_requires_both_config_ids():
    with pytest.raises(ValidationError, match="config_id_b"):
        JobDefinition(
            name="orders_cmp", job_type="compare",
            params={"compare_type": "sql", "request": {
                "config_id_a": 1, "query_a": "SELECT 1", "query_b": "SELECT 1",
            }},
        )


def test_compare_job_sql_request_rejects_mutating_sql():
    with pytest.raises(ValidationError, match="Source B"):
        JobDefinition(
            name="orders_cmp", job_type="compare",
            params={"compare_type": "sql", "request": _sql_request(query_b="DELETE FROM public.orders")},
        )
```

Append to `tests/unit/test_job_validation.py` (it already imports `validate_job_definition` and `JobDefinition`; check its imports and add them if missing):

```python
def test_compare_sql_job_has_no_validation_errors():
    job = JobDefinition(
        name="orders_cmp", job_type="compare",
        params={"compare_type": "sql", "request": {
            "config_id_a": 1, "config_id_b": 2,
            "query_a": "SELECT id FROM a", "query_b": "SELECT id FROM b",
        }},
    )
    errors = [i for i in validate_job_definition(job) if i.severity.value == "error"]
    assert errors == []


def test_compare_sql_job_with_invalid_request_reports_params_request():
    job = JobDefinition.model_construct(
        name="orders_cmp", job_type="compare", description="", tags=[], query="",
        key_columns=[], exclude_columns=[], source_env=None, target_env=None,
        params={"compare_type": "sql", "request": {"config_id_a": 1}},
        enabled=True, rules=[], depends_on=[], pass_condition=None,
    )
    fields = [i.field for i in validate_job_definition(job)]
    assert "params.request" in fields
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_job_type.py tests/unit/test_job_validation.py -k "sql or known_compare_type" -v`
Expected: FAIL — `compare jobs require params.compare_type of 'bo', 'recon_file', or 'matrix'`.

- [ ] **Step 4: Implement**

In `api/schemas.py`, compare branch of `validate_reconciliation_contract`:

```python
        elif self.job_type == "compare":
            compare_type = self.params.get("compare_type")
            if compare_type not in ("bo", "recon_file", "matrix", "sql"):
                raise ValueError(
                    "compare jobs require params.compare_type of 'bo', 'recon_file', 'matrix', or 'sql'"
                )
```

and add an `elif` before the final `else:` (the matrix branch):

```python
            elif compare_type == "sql":
                from etl_framework.db.sql_utils import is_read_only_sql

                parsed_sql = SQLCompareRequest.model_validate(request)
                for side, query in (("A", parsed_sql.query_a), ("B", parsed_sql.query_b)):
                    if not is_read_only_sql(query):
                        raise ValueError(
                            f"compare job Source {side} query must be a single read-only SELECT or WITH query"
                        )
```

In `etl_framework/runner/job_validation.py`, change the import to:

```python
from api.schemas import BOCompareRequest, MatrixCompareRequest, ReconFileCompareRequest, SQLCompareRequest
```

and in the compare branch replace the compare_type check:

```python
        if compare_type not in ("bo", "recon_file", "matrix", "sql"):
            issues.append(ValidationIssue(
                "params.compare_type",
                "compare jobs require compare_type of 'bo', 'recon_file', 'matrix', or 'sql'",
            ))
```

and add, after the `elif compare_type == "matrix":` block (before the `for field in ("rules", ...)` loop):

```python
        elif compare_type == "sql":
            try:
                SQLCompareRequest.model_validate(request)
            except ValidationError as exc:
                issues.append(ValidationIssue(
                    "params.request",
                    f"compare sql request is invalid: {_validation_error_message(exc)}",
                ))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_job_type.py tests/unit/test_job_validation.py tests/unit/test_run_executor_compare.py -v`
Expected: all PASS (the executor unknown-type test still raises, now for `'bogus'`).

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py etl_framework/runner/job_validation.py tests/unit/test_compare_job_type.py tests/unit/test_job_validation.py tests/unit/test_run_executor_compare.py
git commit -m "feat: accept sql compare jobs"
```

---

### Task 7: `CompareService.compare_sql` and the executor `sql` branch

**Files:**
- Modify: `api/services/compare_service.py:555-601` (`run_sql_comparison`)
- Modify: `api/services/run_executor.py` (`_build_case_compare`, ~line 2355-2392)
- Test: `tests/unit/test_run_executor_compare.py` (append)

- [ ] **Step 1: Write the failing tests** (append)

```python
class _FrameDBEngine:
    """Stands in for DBEngine: serves a DataFrame per db_name, no batching."""

    frames: dict = {}

    def __init__(self, env) -> None:
        self._env = env
        self._frame = _FrameDBEngine.frames[env.db_name]

    def execute_query(self, query: str, params=None):
        return self._frame.copy()

    def dispose(self) -> None:
        pass


def _seed_sql_configs(db) -> tuple[int, int]:
    from etl_framework.repository.repository import ConfigRepository

    repo = ConfigRepository(db)
    base = {"db_host": "h", "db_user": "u", "db_password": "p"}
    a = repo.create("src_cfg", "dev", {**base, "db_type": "mssql", "db_name": "src"})
    b = repo.create("tgt_cfg", "dev", {**base, "db_type": "postgresql", "db_name": "tgt"})
    return a.id, b.id


def _live_executor(db) -> RunExecutor:
    return RunExecutor(
        db=db, run_id="run-1", source_env="Source A", target_env="Source B",
        job_sequence=[], run_settings=RunSettings(use_live_connections=True), config_snapshot={},
    )


def test_compare_job_runs_a_sql_compare_and_names_the_result_after_the_job(monkeypatch):
    import pandas as pd

    monkeypatch.setattr("etl_framework.db.engine.DBEngine", _FrameDBEngine)
    _FrameDBEngine.frames = {
        "src": pd.DataFrame({"id": [1, 2], "amount": [10.0, 20.0]}),
        "tgt": pd.DataFrame({"id": [1, 2], "amount": [10.0, 25.0]}),
    }
    db = _session()
    id_a, id_b = _seed_sql_configs(db)
    job = JobDefinition(
        name="orders_cmp", job_type="compare",
        params={"compare_type": "sql", "request": {
            "config_id_a": id_a, "config_id_b": id_b,
            "query_a": "SELECT id, amount FROM dbo.orders",
            "query_b": "SELECT id, amount FROM public.orders",
            "key_columns": ["id"],
        }},
    )

    result = _live_executor(db)._build_case(job)()

    assert result.query_name == "orders_cmp"
    assert result.value_mismatch_count == 1


def test_compare_sql_job_requires_live_connections():
    job = JobDefinition(
        name="orders_cmp", job_type="compare",
        params={"compare_type": "sql", "request": {
            "config_id_a": 1, "config_id_b": 2,
            "query_a": "SELECT 1 AS id", "query_b": "SELECT 1 AS id",
        }},
    )
    with pytest.raises(ValueError, match="live connections"):
        _executor(_session())._build_case(job)()


def test_compare_sql_job_with_missing_config_raises(monkeypatch):
    monkeypatch.setattr("etl_framework.db.engine.DBEngine", _FrameDBEngine)
    job = JobDefinition(
        name="orders_cmp", job_type="compare",
        params={"compare_type": "sql", "request": {
            "config_id_a": 999, "config_id_b": 998,
            "query_a": "SELECT 1 AS id", "query_b": "SELECT 1 AS id",
        }},
    )
    with pytest.raises(Exception, match="Config A not found"):
        _live_executor(_session())._build_case(job)()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_run_executor_compare.py -k sql -v`
Expected: FAIL — `unknown compare_type 'sql'`.

- [ ] **Step 3: Implement `compare_sql`** — in `api/services/compare_service.py`, replace `run_sql_comparison` with the extracted core plus a thin persisting wrapper:

```python
    def compare_sql(self, req: SQLCompareRequest) -> "ReconciliationResult":
        """Run both queries against their configs and reconcile; returns ONE result."""
        from etl_framework.db.engine import DBEngine
        from etl_framework.config.models import resolve_connection

        cfg_a = self._config_repo.get(req.config_id_a)
        if cfg_a is None:
            raise HTTPException(status_code=404, detail="Config A not found")
        cfg_b = self._config_repo.get(req.config_id_b)
        if cfg_b is None:
            raise HTTPException(status_code=404, detail="Config B not found")

        env_a = resolve_connection(cfg_a.config_json or {}, req.connection_a, env_name=cfg_a.env_name or "")
        env_b = resolve_connection(cfg_b.config_json or {}, req.connection_b, env_name=cfg_b.env_name or "")
        engine_a = DBEngine(env_a)
        engine_b = DBEngine(env_b)
        key_cols = req.key_columns or []
        try:
            df_a = _load_in_chunks(engine_a, req.query_a, key_cols, req.chunk_size)
            df_b = _load_in_chunks(engine_b, req.query_b, key_cols, req.chunk_size)
        finally:
            engine_a.dispose()
            engine_b.dispose()

        recon_req = ReconFileCompareRequest(
            file_a_path="__sql__",
            file_b_path="__sql__",
            label_a=req.label_a,
            label_b=req.label_b,
            key_columns=req.key_columns or None,
            exclude_columns=req.exclude_columns,
            advanced=req.advanced,
        )
        return self._tabular_file_result(recon_req, df_a, df_b)

    def run_sql_comparison(self, req: SQLCompareRequest, run_id: str) -> None:
        """Execute two SQL queries against their respective DB configs and diff the results."""
        try:
            self._repo.update_run_status(run_id, "RUNNING", started_at=datetime.now(timezone.utc))
            self._persist_single_result(run_id, self.compare_sql(req))
        except Exception as exc:
            logger.exception("SQL comparison failed for run %s", run_id)
            self._add_error_result(run_id, req.label_a or "sql_comparison", exc)
            self._repo.update_run_status(
                run_id, "ERROR",
                completed_at=datetime.now(timezone.utc),
                total_tests=1, error=1,
            )
            raise
```

(`_run_tabular_file_compare` was `self._persist_single_result(run_id, self._tabular_file_result(...))`, so behaviour is identical.)

- [ ] **Step 4: Implement the executor branch** — in `api/services/run_executor.py::_build_case_compare`, extend the local import and add a branch before the final `else`:

```python
            from api.schemas import BOCompareRequest, MatrixCompareRequest, ReconFileCompareRequest, SQLCompareRequest
```

```python
            elif compare_type == "sql":
                if not self._settings.use_live_connections:
                    raise ValueError(
                        "compare jobs with SQL sources require live connections to be enabled"
                    )
                result = service.compare_sql(SQLCompareRequest.model_validate(request))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_run_executor_compare.py -v` and `python -m pytest tests/unit -q -k "compare_sql or sql_compare or difference_export"`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add api/services/compare_service.py api/services/run_executor.py tests/unit/test_run_executor_compare.py
git commit -m "feat: run sql compare jobs through the executor"
```

---

### Task 8: Bulk import — read the mapping sheet

**Files:**
- Create: `api/services/compare_bulk_import.py`
- Test: `tests/unit/test_compare_bulk_import.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Bulk compare import: sheet parsing, SQL generation, row validation."""
from __future__ import annotations

import io

import pandas as pd
import pytest

from api.services import compare_bulk_import as bulk

HEADER = "job_name,source_config,source_table,target_config,target_table"


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


# -- read_sheet ---------------------------------------------------------------

def test_read_sheet_csv_normalizes_headers_and_trims_cells():
    content = _csv(" Job_Name ,SOURCE_CONFIG,source_table,target_config,target_table",
                   " cust , src , dbo.customer , tgt , public.customer ")
    rows = bulk.read_sheet("map.csv", content)
    assert rows == [{
        "job_name": "cust", "source_config": "src", "source_table": "dbo.customer",
        "target_config": "tgt", "target_table": "public.customer",
    }]


def test_read_sheet_csv_with_utf8_bom():
    rows = bulk.read_sheet("map.csv", b"\xef\xbb\xbf" + _csv(HEADER, "a,s,t1,t,t2"))
    assert rows[0]["job_name"] == "a"


def test_read_sheet_skips_blank_lines():
    rows = bulk.read_sheet("map.csv", _csv(HEADER, "a,s,t1,t,t2", ",,,,", "b,s,t3,t,t4"))
    assert [r["job_name"] for r in rows] == ["a", "b"]


def test_read_sheet_xlsx_first_sheet():
    buf = io.BytesIO()
    pd.DataFrame([{
        "job_name": "x", "source_config": "s", "source_table": "a",
        "target_config": "t", "target_table": "b", "numeric_tolerance": 0.5,
    }]).to_excel(buf, index=False)
    rows = bulk.read_sheet("map.xlsx", buf.getvalue())
    assert rows[0]["job_name"] == "x"
    assert rows[0]["numeric_tolerance"] == "0.5"


def test_read_sheet_rejects_other_extensions():
    with pytest.raises(bulk.SheetError, match=r"\.csv or \.xlsx"):
        bulk.read_sheet("map.txt", b"x")


def test_read_sheet_rejects_unknown_headers():
    with pytest.raises(bulk.SheetError, match="Unknown column.*colour"):
        bulk.read_sheet("map.csv", _csv(HEADER + ",colour", "a,s,t1,t,t2,red"))


def test_read_sheet_rejects_missing_required_headers():
    with pytest.raises(bulk.SheetError, match="Missing required column.*target_table"):
        bulk.read_sheet("map.csv", _csv("job_name,source_config,source_table,target_config", "a,s,t1,t"))


def test_read_sheet_rejects_empty_sheet():
    with pytest.raises(bulk.SheetError, match="no data rows"):
        bulk.read_sheet("map.csv", _csv(HEADER))


def test_read_sheet_rejects_too_many_rows(monkeypatch):
    monkeypatch.setattr(bulk, "MAX_ROWS", 2)
    with pytest.raises(bulk.SheetError, match="at most 2"):
        bulk.read_sheet("map.csv", _csv(HEADER, "a,s,1,t,1", "b,s,2,t,2", "c,s,3,t,3"))


def test_read_sheet_unreadable_xlsx():
    with pytest.raises(bulk.SheetError, match="Could not read"):
        bulk.read_sheet("map.xlsx", b"not a zip")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_bulk_import.py -v`
Expected: FAIL — `ImportError: cannot import name 'compare_bulk_import'`.

- [ ] **Step 3: Implement** — create `api/services/compare_bulk_import.py`:

```python
"""Compile a CSV/XLSX mapping sheet into ``compare_type: "sql"`` compare jobs.

One sheet row = one table-to-table compare. Each side's SELECT is generated
for that side's database dialect, with source columns aliased to the
target-side names so both frames carry identical column names.
"""
from __future__ import annotations

import io

import pandas as pd

MAX_ROWS = 1000

REQUIRED_COLUMNS = (
    "job_name", "source_config", "source_table", "target_config", "target_table",
)
# Order used by the downloadable template; uploaded files may order headers freely.
ALL_COLUMNS = (
    "job_name", "source_config", "source_connection", "source_table",
    "target_config", "target_connection", "target_table",
    "key_columns", "compare_columns", "exclude_columns", "source_where", "target_where",
    "numeric_tolerance", "ignore_case", "chunk_size", "tags", "description",
)


class SheetError(ValueError):
    """The file as a whole cannot be used (as opposed to one bad row)."""


def read_sheet(filename: str, content: bytes) -> list[dict[str, str]]:
    """Parse a mapping sheet into row dicts keyed by normalized header names."""
    lower = (filename or "").lower()
    if not (lower.endswith(".csv") or lower.endswith(".xlsx")):
        raise SheetError("Upload a .csv or .xlsx file")
    try:
        if lower.endswith(".csv"):
            df = pd.read_csv(
                io.BytesIO(content), dtype=str, keep_default_na=False, encoding="utf-8-sig",
            )
        else:
            df = pd.read_excel(io.BytesIO(content), sheet_name=0, dtype=str)
    except Exception as exc:
        raise SheetError(f"Could not read {filename}: {exc}") from exc

    headers = [str(c).strip().lower() for c in df.columns]
    unknown = [h for h in headers if h not in ALL_COLUMNS]
    if unknown:
        raise SheetError(f"Unknown column(s): {', '.join(unknown)}")
    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise SheetError(f"Missing required column(s): {', '.join(missing)}")
    df.columns = headers
    df = df.fillna("")

    rows = [
        {key: str(value).strip() for key, value in record.items()}
        for record in df.to_dict(orient="records")
    ]
    rows = [row for row in rows if any(row.values())]
    if not rows:
        raise SheetError("The sheet has no data rows")
    if len(rows) > MAX_ROWS:
        raise SheetError(f"A sheet can hold at most {MAX_ROWS} rows; this one has {len(rows)}")
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_bulk_import.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/compare_bulk_import.py tests/unit/test_compare_bulk_import.py
git commit -m "feat: read bulk compare mapping sheets"
```

---

### Task 9: Bulk import — column mapping and per-dialect SQL generation

**Files:**
- Modify: `api/services/compare_bulk_import.py`
- Test: `tests/unit/test_compare_bulk_import.py` (append)

- [ ] **Step 1: Write the failing tests** (append)

```python
# -- column lists ---------------------------------------------------------------

def test_parse_column_list_pairs_and_bare_names():
    assert bulk.parse_column_list("CUST_ID:customer_id; email ;", field="key_columns") == [
        bulk.ColumnPair("CUST_ID", "customer_id"), bulk.ColumnPair("email", "email"),
    ]


def test_parse_column_list_blank_is_empty():
    assert bulk.parse_column_list("", field="compare_columns") == []


@pytest.mark.parametrize("cell", ["a:b:c", ":x", "x:"])
def test_parse_column_list_rejects_malformed_entries(cell):
    with pytest.raises(ValueError, match="compare_columns"):
        bulk.parse_column_list(cell, field="compare_columns")


# -- dialect + SQL ----------------------------------------------------------------

@pytest.mark.parametrize("db_type,dialect", [
    ("mssql", "sqlserver"), ("oracle", "oracle"), ("netezza", "netezza"),
    ("postgresql", "postgresql"), ("aurora_postgresql", "postgresql"),
])
def test_dialect_for_db_type(db_type, dialect):
    assert bulk.dialect_for(db_type) == dialect


def test_is_query_detects_select_and_with_only():
    assert bulk.is_query("  select * from t")
    assert bulk.is_query("WITH x AS (SELECT 1) SELECT * FROM x")
    assert not bulk.is_query("dbo.selections")
    assert not bulk.is_query("public.with_history")


def test_build_select_star_when_no_columns():
    assert bulk.build_select("dbo.customer", [], "", "sqlserver") == "SELECT * FROM dbo.customer"


def test_build_select_aliases_source_to_target_names_sqlserver():
    cols = [bulk.ColumnPair("CUST_ID", "customer_id"), bulk.ColumnPair("NAME", "full_name")]
    assert bulk.build_select("dbo.customer", cols, "active = 1", "sqlserver") == (
        "SELECT CUST_ID AS [customer_id], NAME AS [full_name] FROM dbo.customer WHERE active = 1"
    )


def test_build_select_quotes_aliases_for_postgres_and_oracle():
    cols = [bulk.ColumnPair("customer_id", "customer_id")]
    assert bulk.build_select("public.customer", cols, "", "postgresql") == (
        'SELECT customer_id AS "customer_id" FROM public.customer'
    )
    assert bulk.build_select("SALES.CUSTOMER", [bulk.ColumnPair("CUST_ID", "customer_id")], "", "oracle") == (
        'SELECT CUST_ID AS "customer_id" FROM SALES.CUSTOMER'
    )


def test_build_select_quotes_non_plain_names_on_sqlserver():
    cols = [bulk.ColumnPair("Order Date", "order_date")]
    assert bulk.build_select("dbo.Order Lines", cols, "", "sqlserver") == (
        "SELECT [Order Date] AS [order_date] FROM dbo.[Order Lines]"
    )


def test_build_select_rejects_non_plain_names_on_postgres():
    with pytest.raises(ValueError, match="Invalid SQL identifier"):
        bulk.build_select("public.orders", [bulk.ColumnPair("order date", "order_date")], "", "postgresql")


def test_build_select_rejects_mutating_where():
    with pytest.raises(ValueError, match="read-only"):
        bulk.build_select("dbo.t", [], "1=1; DROP TABLE dbo.t", "sqlserver")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_bulk_import.py -k "column_list or dialect or is_query or build_select" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'parse_column_list'`.

- [ ] **Step 3: Implement** — append to `api/services/compare_bulk_import.py` and extend its imports:

```python
import re
from dataclasses import dataclass

from etl_framework.db.sql_utils import quote_identifier, reject_mutating_sql
```

```python
DIALECT_BY_DB_TYPE = {
    "mssql": "sqlserver",
    "oracle": "oracle",
    "netezza": "netezza",
    "postgresql": "postgresql",
    "aurora_postgresql": "postgresql",
}

_PLAIN_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")
_QUERY_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ColumnPair:
    source: str
    target: str


def parse_column_list(cell: str, *, field: str) -> list[ColumnPair]:
    """Parse ``"SRC:tgt;name"`` into pairs; a bare name maps to itself."""
    pairs: list[ColumnPair] = []
    for raw in (cell or "").split(";"):
        item = raw.strip()
        if not item:
            continue
        if item.count(":") > 1:
            raise ValueError(f"{field}: '{item}' has more than one ':'")
        if ":" in item:
            source, target = (part.strip() for part in item.split(":"))
        else:
            source = target = item
        if not source or not target:
            raise ValueError(f"{field}: '{item}' must be 'source:target' or a column name")
        pairs.append(ColumnPair(source, target))
    return pairs


def dialect_for(db_type: str) -> str:
    return DIALECT_BY_DB_TYPE[db_type]


def is_query(cell: str) -> bool:
    return bool(_QUERY_START.match(cell or ""))


def _ref(name: str, dialect: str) -> str:
    """Leave plain identifiers unquoted so the database's own case folding applies."""
    name = name.strip()
    return name if _PLAIN_IDENTIFIER.fullmatch(name) else quote_identifier(name, dialect)


def build_select(table: str, columns: list[ColumnPair], where: str, dialect: str) -> str:
    """SELECT one side's columns, aliased to the target names, from ``table``.

    Aliases are always quoted so they keep the exact spelling from the sheet
    (Postgres would lower-case and Oracle upper-case an unquoted alias).
    """
    if columns:
        select_list = ", ".join(
            f"{_ref(c.source, dialect)} AS {quote_identifier(c.target, dialect)}" for c in columns
        )
    else:
        select_list = "*"
    table_ref = ".".join(_ref(part, dialect) for part in table.split("."))
    sql = f"SELECT {select_list} FROM {table_ref}"
    if where:
        sql = f"{sql} WHERE {where}"
    return reject_mutating_sql(sql)
```

Note: `reject_mutating_sql` raises `ValueError("SQL must be a single read-only SELECT or WITH query")` — the `match="read-only"` test relies on that wording.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_bulk_import.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/compare_bulk_import.py tests/unit/test_compare_bulk_import.py
git commit -m "feat: bulk compare column mapping and per-dialect select generation"
```

---

### Task 10: Bulk import — row validation, job build, preview

**Files:**
- Modify: `api/services/compare_bulk_import.py`
- Test: `tests/unit/test_compare_bulk_import.py` (append)

- [ ] **Step 1: Write the failing tests** (append)

```python
# -- build_job / preview ------------------------------------------------------------

class _Cfg:
    def __init__(self, id, name, config_json):
        self.id, self.name, self.config_json = id, name, config_json


CONFIGS = {
    "SQL_PROD": _Cfg(1, "SQL_PROD", {"db_type": "mssql", "connections": {"dw": {"db_type": "oracle"}}}),
    "PG_PROD": _Cfg(2, "PG_PROD", {"db_type": "aurora_postgresql"}),
    "S3_ONLY": _Cfg(3, "S3_ONLY", {"db_type": "snowflake"}),
}


def _row(**overrides) -> dict:
    row = {
        "job_name": "cust_cmp", "source_config": "SQL_PROD", "source_table": "dbo.customer",
        "target_config": "PG_PROD", "target_table": "public.customer",
        "key_columns": "CUST_ID:customer_id", "compare_columns": "NAME:full_name;email",
    }
    return {**row, **overrides}


def test_build_job_generates_sql_compare_job():
    job, query_a, query_b = bulk.build_job(_row(tags="nightly;crm", description="Customer"), CONFIGS)
    assert query_a == "SELECT CUST_ID AS [customer_id], NAME AS [full_name], email AS [email] FROM dbo.customer"
    assert query_b == 'SELECT customer_id AS "customer_id", full_name AS "full_name", email AS "email" FROM public.customer'
    assert job.name == "cust_cmp"
    assert job.job_type == "compare"
    assert job.tags == ["nightly", "crm"]
    assert job.description == "Customer"
    req = job.params["request"]
    assert job.params["compare_type"] == "sql"
    assert (req["config_id_a"], req["config_id_b"]) == (1, 2)
    assert req["key_columns"] == ["customer_id"]
    assert req["label_a"] == "SQL_PROD dbo.customer"
    assert req["label_b"] == "PG_PROD public.customer"
    assert req["chunk_size"] == 10000
    assert req["advanced"]["float_tolerance"] == 1e-9


def test_build_job_uses_named_connection_dialect():
    _, query_a, _ = bulk.build_job(_row(source_connection="dw"), CONFIGS)
    assert query_a.startswith('SELECT CUST_ID AS "customer_id"')


def test_build_job_key_only_without_rename_selects_star():
    _, query_a, query_b = bulk.build_job(_row(key_columns="id", compare_columns=""), CONFIGS)
    assert query_a == "SELECT * FROM dbo.customer"
    assert query_b == "SELECT * FROM public.customer"


def test_build_job_options_tolerance_ignore_case_chunk_exclude():
    job, _, _ = bulk.build_job(_row(
        numeric_tolerance="0.01", ignore_case="yes", chunk_size="500", exclude_columns="load_ts;etl_id",
    ), CONFIGS)
    req = job.params["request"]
    assert req["advanced"]["float_tolerance"] == 0.01
    assert req["advanced"]["case_insensitive_columns"] == ["customer_id", "full_name", "email"]
    assert req["chunk_size"] == 500
    assert req["exclude_columns"] == ["load_ts", "etl_id"]


def test_build_job_wheres_apply_per_side():
    _, query_a, query_b = bulk.build_job(_row(source_where="active = 1", target_where="is_active"), CONFIGS)
    assert query_a.endswith("FROM dbo.customer WHERE active = 1")
    assert query_b.endswith("FROM public.customer WHERE is_active")


def test_build_job_query_cell_passes_through():
    _, query_a, _ = bulk.build_job(_row(
        source_table="SELECT CUST_ID AS customer_id, NAME AS full_name, email FROM dbo.customer",
        key_columns="customer_id", compare_columns="",
    ), CONFIGS)
    assert query_a == "SELECT CUST_ID AS customer_id, NAME AS full_name, email FROM dbo.customer"


@pytest.mark.parametrize("overrides,message", [
    ({"job_name": ""}, "job_name is required"),
    ({"source_config": "NOPE"}, "source_config 'NOPE' does not exist"),
    ({"target_connection": "missing"}, "target_connection 'missing' does not exist in config 'PG_PROD'"),
    ({"target_config": "S3_ONLY"}, "is not a SQL database"),
    ({"key_columns": "a:b:c"}, "key_columns"),
    ({"compare_columns": "", "key_columns": "CUST_ID:customer_id"}, "renamed key"),
    ({"compare_columns": "", "key_columns": "id", "ignore_case": "true"}, "ignore_case needs compare_columns"),
    ({"source_table": "SELECT * FROM dbo.customer"}, "source_table is a query"),
    ({"target_table": "SELECT * FROM public.customer", "key_columns": "id", "compare_columns": "",
      "target_where": "x = 1"}, "target_where"),
    ({"numeric_tolerance": "abc"}, "numeric_tolerance"),
    ({"numeric_tolerance": "-1"}, "numeric_tolerance"),
    ({"chunk_size": "-5"}, "chunk_size"),
    ({"ignore_case": "maybe"}, "ignore_case"),
    ({"source_where": "1=1; DELETE FROM x"}, "read-only"),
])
def test_build_job_row_errors(overrides, message):
    with pytest.raises(bulk.RowError) as exc_info:
        bulk.build_job(_row(**overrides), CONFIGS)
    assert any(message in err for err in exc_info.value.errors), exc_info.value.errors


def test_build_job_collects_several_errors():
    with pytest.raises(bulk.RowError) as exc_info:
        bulk.build_job(_row(source_config="", target_config=""), CONFIGS)
    assert len(exc_info.value.errors) >= 2


def test_preview_rows_flags_duplicates_updates_and_counts():
    rows = [
        _row(job_name="a"), _row(job_name="a"),
        _row(job_name="b"), _row(job_name="c", source_config="NOPE"),
    ]
    out = bulk.preview_rows(rows, CONFIGS, existing_job_names={"b"})
    by_row = {r["row_number"]: r for r in out["rows"]}
    assert by_row[2]["status"] == "invalid" and "appears 2 times" in by_row[2]["errors"][0]
    assert by_row[3]["status"] == "invalid"
    assert by_row[4]["status"] == "valid" and by_row[4]["will_update"] is True
    assert by_row[4]["job"]["name"] == "b"
    assert by_row[4]["query_a"].startswith("SELECT ")
    assert by_row[5]["status"] == "invalid" and by_row[5]["job"] is None
    assert (out["valid_count"], out["invalid_count"]) == (1, 3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_bulk_import.py -k "build_job or preview_rows" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'build_job'`.

- [ ] **Step 3: Implement** — append to `api/services/compare_bulk_import.py`, adding imports:

```python
from collections import Counter
from typing import Any, Mapping

from pydantic import ValidationError

from api.schemas import AdvancedCompareOptions, JobDefinition, SQLCompareRequest
```

```python
DEFAULT_CHUNK_SIZE = 10_000
_TRUE = {"true", "yes", "y", "1"}
_FALSE = {"false", "no", "n", "0", ""}


class RowError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class _Side:
    config: Any
    connection: str | None
    dialect: str


def _resolve_side(row: Mapping[str, str], prefix: str, configs: Mapping[str, Any], errors: list[str]) -> _Side | None:
    config_name = row.get(f"{prefix}_config", "")
    connection = row.get(f"{prefix}_connection", "") or None
    if not config_name:
        errors.append(f"{prefix}_config is required")
        return None
    cfg = configs.get(config_name)
    if cfg is None:
        errors.append(f"{prefix}_config '{config_name}' does not exist")
        return None
    config_json = cfg.config_json or {}
    entry: Mapping[str, Any] = {}
    if connection:
        entry = (config_json.get("connections") or {}).get(connection)
        if entry is None:
            errors.append(f"{prefix}_connection '{connection}' does not exist in config '{config_name}'")
            return None
    db_type = entry.get("db_type") or config_json.get("db_type") or "mssql"
    if db_type not in DIALECT_BY_DB_TYPE:
        errors.append(f"{prefix}_config '{config_name}' ({db_type}) is not a SQL database")
        return None
    return _Side(cfg, connection, dialect_for(db_type))


def _parse_bool(cell: str, field: str, errors: list[str]) -> bool:
    value = (cell or "").strip().lower()
    if value in _TRUE:
        return True
    if value not in _FALSE:
        errors.append(f"{field} must be true or false, got '{cell}'")
    return False


def _parse_number(cell: str, field: str, cast, default, errors: list[str]):
    if not (cell or "").strip():
        return default
    try:
        value = cast(cell)
    except ValueError:
        errors.append(f"{field} must be a number, got '{cell}'")
        return default
    if value < 0:
        errors.append(f"{field} must be >= 0, got '{cell}'")
        return default
    return value


def _side_label(cfg: Any, table: str) -> str:
    return cfg.name if is_query(table) else f"{cfg.name} {table}"


def build_job(row: Mapping[str, str], configs: Mapping[str, Any]) -> tuple[JobDefinition, str, str]:
    """Compile one sheet row; raises RowError listing every problem found."""
    errors: list[str] = []
    for field in ("job_name", "source_table", "target_table"):
        if not row.get(field):
            errors.append(f"{field} is required")
    source = _resolve_side(row, "source", configs, errors)
    target = _resolve_side(row, "target", configs, errors)

    keys: list[ColumnPair] = []
    compares: list[ColumnPair] = []
    for field, sink in (("key_columns", keys), ("compare_columns", compares)):
        try:
            sink.extend(parse_column_list(row.get(field, ""), field=field))
        except ValueError as exc:
            errors.append(str(exc))
    excludes = [c.strip() for c in row.get("exclude_columns", "").split(";") if c.strip()]
    tolerance = _parse_number(row.get("numeric_tolerance", ""), "numeric_tolerance", float, 0.0, errors)
    chunk_size = _parse_number(row.get("chunk_size", ""), "chunk_size", int, DEFAULT_CHUNK_SIZE, errors)
    ignore_case = _parse_bool(row.get("ignore_case", ""), "ignore_case", errors)

    source_table, target_table = row.get("source_table", ""), row.get("target_table", "")
    renamed_key = any(k.source != k.target for k in keys)
    if not compares and renamed_key:
        errors.append(
            "a renamed key needs an explicit compare_columns list (SELECT * would duplicate the key)"
        )
    if ignore_case and not compares:
        errors.append("ignore_case needs compare_columns (the case-insensitive column list)")
    for prefix, table in (("source", source_table), ("target", target_table)):
        if is_query(table):
            if compares or renamed_key:
                errors.append(
                    f"{prefix}_table is a query; column mapping only applies to table references - "
                    "alias the columns inside the query instead"
                )
            if row.get(f"{prefix}_where"):
                errors.append(f"{prefix}_where only applies to a table reference; put the filter in the query")

    if errors or source is None or target is None:
        raise RowError(errors)

    key_targets = {k.target for k in keys}
    columns = keys + [c for c in compares if c.target not in key_targets] if compares else []
    queries: list[str] = []
    for prefix, side, table, side_columns in (
        ("source", source, source_table, columns),
        ("target", target, target_table, [ColumnPair(c.target, c.target) for c in columns]),
    ):
        try:
            if is_query(table):
                queries.append(reject_mutating_sql(table))
            else:
                queries.append(build_select(table, side_columns, row.get(f"{prefix}_where", ""), side.dialect))
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
    if errors:
        raise RowError(errors)
    query_a, query_b = queries

    try:
        request = SQLCompareRequest(
            config_id_a=source.config.id,
            config_id_b=target.config.id,
            query_a=query_a,
            query_b=query_b,
            label_a=_side_label(source.config, source_table),
            label_b=_side_label(target.config, target_table),
            connection_a=source.connection,
            connection_b=target.connection,
            key_columns=[k.target for k in keys],
            exclude_columns=excludes,
            chunk_size=chunk_size,
            advanced=AdvancedCompareOptions(
                float_tolerance=tolerance if tolerance > 0 else 1e-9,
                case_insensitive_columns=[c.target for c in columns] if ignore_case else [],
            ),
        )
        job = JobDefinition(
            name=row["job_name"],
            description=row.get("description", ""),
            tags=[t.strip() for t in row.get("tags", "").split(";") if t.strip()],
            job_type="compare",
            params={"compare_type": "sql", "request": request.model_dump()},
        )
    except ValidationError as exc:
        raise RowError([err["msg"] for err in exc.errors()]) from exc
    return job, query_a, query_b


def preview_rows(
    rows: list[dict[str, str]],
    configs: Mapping[str, Any],
    existing_job_names: set[str],
) -> dict[str, Any]:
    """Validate every row; nothing is saved. Row numbers match the sheet (header = 1)."""
    name_counts = Counter(row.get("job_name", "") for row in rows)
    results: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=2):
        name = row.get("job_name", "")
        errors: list[str] = []
        if name and name_counts[name] > 1:
            errors.append(f"job_name '{name}' appears {name_counts[name]} times in the file")
        job = query_a = query_b = None
        try:
            job, query_a, query_b = build_job(row, configs)
        except RowError as exc:
            errors.extend(exc.errors)
        valid = not errors
        results.append({
            "row_number": row_number,
            "job_name": name,
            "source": f"{row.get('source_config', '')} {row.get('source_table', '')}".strip(),
            "target": f"{row.get('target_config', '')} {row.get('target_table', '')}".strip(),
            "status": "valid" if valid else "invalid",
            "will_update": valid and name in existing_job_names,
            "errors": errors,
            "job": job.model_dump() if valid else None,
            "query_a": query_a,
            "query_b": query_b,
        })
    valid_count = sum(1 for r in results if r["status"] == "valid")
    return {"rows": results, "valid_count": valid_count, "invalid_count": len(results) - valid_count}


TEMPLATE_CSV = (
    ",".join(ALL_COLUMNS) + "\n"
    + "customer_cmp,PROD_MSSQL,,dbo.customer,PROD_AURORA,,public.customer,"
      "CUST_ID:customer_id,NAME:full_name;EMAIL:email,,active = 1,,0,false,10000,nightly,Customer master\n"
    + "orders_cmp,PROD_ORACLE,,SALES.ORDERS,PROD_PG,,sales.orders,"
      "ORDER_ID:order_id;LINE_NO:line_no,AMOUNT:amount,load_ts,,,0.01,false,10000,nightly,Order lines\n"
)
```

`TEMPLATE_CSV` rows follow `ALL_COLUMNS` order (defined in Task 8). Add this test to lock the template:

```python
def test_template_parses_and_matches_columns():
    rows = bulk.read_sheet("t.csv", bulk.TEMPLATE_CSV.encode())
    assert len(rows) == 2
    assert list(rows[0]) == list(bulk.ALL_COLUMNS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_bulk_import.py -v`
Expected: all PASS. If `test_build_job_row_errors[...renamed key...]` fails because the row also trips another rule, inspect `exc_info.value.errors` — the assertion only needs one matching message.

- [ ] **Step 5: Commit**

```bash
git add api/services/compare_bulk_import.py tests/unit/test_compare_bulk_import.py
git commit -m "feat: validate bulk compare rows and build sql compare jobs"
```

---

### Task 11: Bulk import API — template, preview, commit

**Files:**
- Modify: `api/routes/jobs.py` (new routes + refactor `import_jobs` loop into a helper)
- Test: `tests/unit/test_bulk_compare_routes.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""/api/jobs/bulk-compare/{template,preview,commit}."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from api.routes import runs as runs_module
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    ConfigRepository, JobRepository, JobSelectionRepository, TokenRepository,
)

HEADER = "job_name,source_config,source_table,target_config,target_table,key_columns"


@pytest.fixture
def engine(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(runs_module, "_execute_run", lambda *args, **kwargs: None)
    with Session(engine) as db:
        base = {"db_host": "h", "db_name": "d", "db_user": "u", "db_password": "p"}
        ConfigRepository(db).create("SQL_PROD", "dev", {**base, "db_type": "mssql"})
        ConfigRepository(db).create("PG_PROD", "dev", {**base, "db_type": "postgresql"})
    return engine


@pytest.fixture
def client(engine):
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("admin", is_admin=True)
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c


def _upload(text: str, filename: str = "map.csv") -> dict:
    return {"filename": filename, "content_b64": base64.b64encode(text.encode()).decode()}


VALID = HEADER + "\ncust_cmp,SQL_PROD,dbo.customer,PG_PROD,public.customer,id\n"


def test_template_downloads_csv(client):
    resp = client.get("/api/jobs/bulk-compare/template")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.text.startswith("job_name,source_config,")


def test_preview_returns_rows_and_saves_nothing(client, engine):
    resp = client.post("/api/jobs/bulk-compare/preview", json=_upload(VALID))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid_count"] == 1 and body["invalid_count"] == 0
    assert body["rows"][0]["job"]["params"]["compare_type"] == "sql"
    with Session(engine) as db:
        assert JobRepository(db).get("cust_cmp") is None


def test_preview_flags_existing_job_as_update(client, engine):
    with Session(engine) as db:
        JobRepository(db).create({"name": "cust_cmp", "job_type": "reconciliation", "query": "SELECT 1",
                                  "key_columns": ["id"], "params": {}})
    rows = client.post("/api/jobs/bulk-compare/preview", json=_upload(VALID)).json()["rows"]
    assert rows[0]["will_update"] is True


def test_preview_file_level_error_is_422(client):
    resp = client.post("/api/jobs/bulk-compare/preview", json=_upload("colour\nred\n"))
    assert resp.status_code == 422
    assert "Unknown column" in resp.json()["detail"]


def test_preview_bad_base64_is_422(client):
    resp = client.post("/api/jobs/bulk-compare/preview", json={"filename": "m.csv", "content_b64": "%%%"})
    assert resp.status_code == 422


def _preview_jobs(client, text=VALID) -> list[dict]:
    return [r["job"] for r in client.post("/api/jobs/bulk-compare/preview", json=_upload(text)).json()["rows"]]


def test_commit_saves_jobs_and_creates_selection(client, engine):
    two = VALID + "ord_cmp,SQL_PROD,dbo.orders,PG_PROD,public.orders,id\n"
    resp = client.post("/api/jobs/bulk-compare/commit",
                       json={"jobs": _preview_jobs(client, two), "selection_name": "nightly_compares"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert [j["name"] for j in body["jobs"]] == ["cust_cmp", "ord_cmp"]
    assert body["selection"]["name"] == "nightly_compares"
    with Session(engine) as db:
        assert JobRepository(db).get("ord_cmp").job_type == "compare"
        selection = JobSelectionRepository(db).get_by_name("nightly_compares")
        version = JobSelectionRepository(db).latest_version(selection.id)
        assert version.job_sequence == ["cust_cmp", "ord_cmp"]
        assert version.run_settings_json["use_live_connections"] is True


def test_commit_without_selection(client):
    resp = client.post("/api/jobs/bulk-compare/commit", json={"jobs": _preview_jobs(client)})
    assert resp.status_code == 201, resp.text
    assert resp.json()["selection"] is None


def test_commit_existing_selection_is_409_and_saves_nothing(client, engine):
    with Session(engine) as db:
        JobSelectionRepository(db).create(name="taken", description="", tags=[], job_sequence=[], run_settings={})
    resp = client.post("/api/jobs/bulk-compare/commit",
                       json={"jobs": _preview_jobs(client), "selection_name": "taken"})
    assert resp.status_code == 409
    with Session(engine) as db:
        assert JobRepository(db).get("cust_cmp") is None


def test_commit_rejects_non_sql_compare_jobs(client):
    job = {"name": "x", "job_type": "reconciliation", "query": "SELECT 1", "key_columns": ["id"]}
    resp = client.post("/api/jobs/bulk-compare/commit", json={"jobs": [job]})
    assert resp.status_code == 422


def test_commit_rejects_duplicate_names(client):
    jobs = _preview_jobs(client)
    resp = client.post("/api/jobs/bulk-compare/commit", json={"jobs": jobs + jobs})
    assert resp.status_code == 422
    assert "Duplicate" in resp.text


def test_commit_rechecks_configs_and_saves_nothing(client, engine):
    jobs = _preview_jobs(client)
    jobs[0]["params"]["request"]["config_id_b"] = 999
    resp = client.post("/api/jobs/bulk-compare/commit", json={"jobs": jobs})
    assert resp.status_code == 422
    assert "999" in resp.text
    with Session(engine) as db:
        assert JobRepository(db).get("cust_cmp") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_bulk_compare_routes.py -v`
Expected: FAIL — 404/405 on `/api/jobs/bulk-compare/...`.

- [ ] **Step 3: Implement** — in `api/routes/jobs.py`:

Imports (top of file):

```python
import base64
import binascii

from fastapi import Response
from pydantic import Field

from api.schemas import RunSettings
from api.services import compare_bulk_import
from etl_framework.repository.repository import ConfigRepository, JobSelectionRepository
```

(Merge into the existing `from fastapi import ...`, `from pydantic import ...`, `from api.schemas import ...` and `from etl_framework.repository.repository import ...` lines rather than duplicating them.)

Refactor `import_jobs` so validation for the whole payload happens before any write, shared with commit:

```python
def _save_job_definitions(
    jobs: list[JobDefinition], request: Request, db: Session, source: str,
) -> list[JobDefinition]:
    """Admin-check and validate every job first, then upsert them all."""
    repo = JobRepository(db)
    for job_def in jobs:
        existing = repo.get(job_def.name)
        require_admin_for_write_capable_job(
            request, job_def.job_type, existing.job_type if existing else None,
        )
    for job_def in jobs:
        _raise_validation_errors(job_def)
    saved = []
    for job_def in jobs:
        existed = repo.get(job_def.name) is not None
        job = repo.upsert(_job_to_data(job_def))
        AuditService(db).log(
            request,
            "job.updated" if existed else "job.created",
            "job",
            job.name,
            {"source": source, "job_type": job.job_type},
        )
        saved.append(_job_to_schema(job))
    return saved


@router.post("/import", response_model=list[JobDefinition], status_code=201)
def import_jobs(body: list[JobDefinition], request: Request, db: Session = Depends(get_session)):
    return _save_job_definitions(body, request, db, "import")
```

(This keeps `/import` behaviour except that validation of every job now precedes the first write, which is strictly safer; the old comment about failing the whole request still holds.)

New routes — place them **above** `@router.put("/{name}")` / `@router.delete("/{name}")` so the literal path wins:

```python
class BulkComparePreviewRequest(BaseModel):
    filename: str = Field(min_length=1)
    content_b64: str


class BulkCompareCommitRequest(BaseModel):
    jobs: list[JobDefinition] = Field(min_length=1)
    selection_name: str | None = None


@router.get("/bulk-compare/template")
def bulk_compare_template():
    return Response(
        content=compare_bulk_import.TEMPLATE_CSV,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="bulk-compare-template.csv"'},
    )


@router.post("/bulk-compare/preview")
def bulk_compare_preview(body: BulkComparePreviewRequest, db: Session = Depends(get_session)):
    try:
        content = base64.b64decode(body.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="content_b64 is not valid base64")
    try:
        rows = compare_bulk_import.read_sheet(body.filename, content)
    except compare_bulk_import.SheetError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    configs = {cfg.name: cfg for cfg in ConfigRepository(db).list()}
    existing = {job.name for job in JobRepository(db).list()}
    return compare_bulk_import.preview_rows(rows, configs, existing)


@router.post("/bulk-compare/commit", status_code=201)
def bulk_compare_commit(
    body: BulkCompareCommitRequest, request: Request, db: Session = Depends(get_session),
):
    errors: list[str] = []
    names = [job.name for job in body.jobs]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        errors.append(f"Duplicate job names: {', '.join(duplicates)}")
    config_repo = ConfigRepository(db)
    for job in body.jobs:
        if job.job_type != "compare" or job.params.get("compare_type") != "sql":
            errors.append(f"{job.name}: bulk compare import only saves sql compare jobs")
            continue
        req = job.params.get("request") or {}
        for side in ("a", "b"):
            config_id = req.get(f"config_id_{side}")
            if config_repo.get(config_id) is None:
                errors.append(f"{job.name}: config {config_id} (source {side.upper()}) no longer exists")
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    selection_repo = JobSelectionRepository(db)
    selection_name = (body.selection_name or "").strip() or None
    if selection_name and selection_repo.get_by_name(selection_name) is not None:
        raise HTTPException(status_code=409, detail="A job selection with this name already exists")

    saved = _save_job_definitions(body.jobs, request, db, "bulk_compare_import")
    selection_out = None
    if selection_name:
        selection = selection_repo.create(
            name=selection_name,
            description="Created by bulk compare import",
            tags=[],
            job_sequence=[job.name for job in saved],
            run_settings=RunSettings(use_live_connections=True).model_dump(),
        )
        AuditService(db).log(
            request, "selection.created", "job_selection", selection.id,
            {"name": selection.name, "job_count": len(saved), "source": "bulk_compare_import"},
        )
        selection_out = {"id": selection.id, "name": selection.name}
    return {"jobs": [job.model_dump() for job in saved], "selection": selection_out}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_bulk_compare_routes.py tests/unit/test_jobs_admin_gate.py tests/unit/test_api.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routes/jobs.py tests/unit/test_bulk_compare_routes.py
git commit -m "feat: bulk compare import template, preview and commit endpoints"
```

---

### Task 12: Frontend — PostgreSQL / Aurora in the config form

**Files:**
- Modify: `frontend/features/config.js` (`openNewConfigModal`, `editConfig`, `_configDataFromModal`, new helpers)
- Modify: `frontend/partials/tab-config.html:633-650` (main DB fields), `:673-678` (named connection DB type)
- Regenerate: `frontend/index.html`

- [ ] **Step 1: Add helpers to `ETL_FEATURE_CONFIG`** (`frontend/features/config.js`, inside the returned object, next to `openNewConfigModal`):

```js
    dbTypeDefaults(type) {
      switch (type) {
        case 'oracle': return { port: 1521, driver: 'oracledb', sslmode: '' };
        case 'netezza': return { port: 5480, driver: 'nzpy', sslmode: '' };
        case 'postgresql': return { port: 5432, driver: 'psycopg', sslmode: 'prefer' };
        case 'aurora_postgresql': return { port: 5432, driver: 'psycopg', sslmode: 'require' };
        default: return { port: 1433, driver: 'ODBC Driver 17 for SQL Server', sslmode: '' };
      }
    },

    isPostgresType(type) {
      return type === 'postgresql' || type === 'aurora_postgresql';
    },

    // A driver the user typed for a different DB type is a stale default, not a choice.
    _isDefaultDriver(driver) {
      return !driver || ['SQL Server', 'nzpy', 'oracledb', 'psycopg'].some(d => driver.includes(d));
    },

    onConfigDbTypeChange() {
      const m = this.configModal;
      const d = this.dbTypeDefaults(m.db_type);
      m.db_port = d.port;
      if (this._isDefaultDriver(m.db_driver)) m.db_driver = d.driver;
      m.db_sslmode = d.sslmode;
    },

    onConnectionDbTypeChange(conn) {
      if (!conn.db_type) return;
      const d = this.dbTypeDefaults(conn.db_type);
      if (!conn.db_port) conn.db_port = d.port;
      if (this._isDefaultDriver(conn.db_driver)) conn.db_driver = d.driver;
      if (!this.isPostgresType(conn.db_type)) conn.db_sslmode = '';
      else if (!conn.db_sslmode) conn.db_sslmode = d.sslmode;
    },
```

- [ ] **Step 2: Use the helpers in the modal state**

`openNewConfigModal`: add `db_sslmode: '',` after `db_connect_timeout: 15,`.

`editConfig`: replace the `db_driver` / `db_host, db_port` lines with:

```js
        db_driver: d.db_driver || this.dbTypeDefaults(d.db_type).driver,
        db_host: d.db_host || '', db_port: d.db_port || this.dbTypeDefaults(d.db_type).port,
        db_sslmode: d.db_sslmode || this.dbTypeDefaults(d.db_type).sslmode,
```

and in its `connections` mapping add `db_sslmode: entry.db_sslmode || '',` after `db_password`.

`_configDataFromModal`: replace the `db_port` and `db_driver` lines with:

```js
        db_port: Number(m.db_port) || this.dbTypeDefaults(m.db_type).port,
```

```js
        db_driver: m.db_driver || this.dbTypeDefaults(m.db_type).driver,
        ...(this.isPostgresType(m.db_type)
          ? { db_sslmode: m.db_sslmode || this.dbTypeDefaults(m.db_type).sslmode }
          : {}),
```

and in the `connections` mapping add after the `db_password` spread:

```js
              ...(c.db_sslmode ? { db_sslmode: c.db_sslmode } : {}),
```

- [ ] **Step 3: Update `frontend/partials/tab-config.html`**

Main DB type select (line ~636): replace the whole `@change="..."` attribute with `@change="onConfigDbTypeChange()"` and add two options after Oracle:

```html
              <option value="postgresql">PostgreSQL</option>
              <option value="aurora_postgresql">Amazon Aurora PostgreSQL</option>
```

Driver input placeholder (line ~644): `:placeholder="dbTypeDefaults(configModal.db_type).driver"`.
Port input placeholder (line ~647): `:placeholder="String(dbTypeDefaults(configModal.db_type).port)"`.

After the Connect Timeout `<div>` (last child of that `grid-2`), add:

```html
          <div x-show="isPostgresType(configModal.db_type)">
            <label class="field-label" for="a11y-config-db-sslmode">SSL Mode</label>
            <select x-model="configModal.db_sslmode" class="field-input field-select" data-testid="config-modal-db-sslmode-select" id="a11y-config-db-sslmode">
              <option value="disable">disable</option>
              <option value="prefer">prefer</option>
              <option value="require">require</option>
              <option value="verify-ca">verify-ca</option>
              <option value="verify-full">verify-full</option>
            </select>
          </div>
          <p x-show="configModal.db_type === 'aurora_postgresql'" class="text-xs text-slate-500" data-testid="config-modal-aurora-hint">
            DB Host is the Aurora cluster endpoint (writer) or reader endpoint, e.g. mycluster.cluster-abc123.eu-west-1.rds.amazonaws.com.
          </p>
```

Named connection DB type select (line ~673): replace its `@change="..."` with `@change="onConnectionDbTypeChange(conn)"` and add the same two `<option>`s after Oracle. After the DB Password `<div>` in that connection grid, add:

```html
                <div x-show="isPostgresType(conn.db_type)"><label class="field-label">SSL Mode</label>
                  <select x-model="conn.db_sslmode" class="field-input field-select" aria-label="connection ssl mode">
                    <option value="">(type default)</option>
                    <option value="disable">disable</option>
                    <option value="prefer">prefer</option>
                    <option value="require">require</option>
                    <option value="verify-ca">verify-ca</option>
                    <option value="verify-full">verify-full</option>
                  </select>
                </div>
```

- [ ] **Step 4: Rebuild `frontend/index.html` without touching the user's `diff-search.js` edit**

Run (Bash tool):

```bash
cd /c/atom
BAK="/c/Users/aravi/AppData/Local/Temp/claude/c--atom/7fdfd29d-794b-428f-8123-5ffc1529b8a7/scratchpad/diff-search.js.bak"; cp frontend/features/diff-search.js "$BAK"
node scripts/build-html.js
cp "$BAK" frontend/features/diff-search.js
git status --short frontend/
```

Expected: `frontend/index.html`, `frontend/features/config.js`, `frontend/partials/tab-config.html` modified; `frontend/features/diff-search.js` still shows ` M` exactly as before (the user's change), nothing else.

- [ ] **Step 5: Smoke-check in a browser**

Start the app (use the `run` skill or the project's usual `uvicorn api.main:app --port <free port>`), open Config → New Config, switch DB Type to "Amazon Aurora PostgreSQL": port becomes 5432, driver `psycopg`, SSL Mode `require`, Aurora hint visible. Switch back to SQL Server: port 1433, driver ODBC, SSL Mode hidden. Save and re-open: values persist.

- [ ] **Step 6: Commit**

```bash
git add frontend/features/config.js frontend/partials/tab-config.html frontend/index.html
git commit -m "feat: postgresql and aurora options in the config form"
```

---

### Task 13: Frontend — save and edit SQL compares as jobs

**Files:**
- Modify: `frontend/features/compare.js` (state, `runSQLComparison`, `_compareJobBody`, `_assertCompareJobSourcesAreRepeatable`)
- Modify: `frontend/features/launch.js:1610-1613` (`_compareSubTabForJob`), `openCompareForJob` (new prefill branch)
- Modify: `frontend/partials/tab-compare.html:946-949`
- Regenerate: `frontend/index.html`

- [ ] **Step 1: Extract the SQL payload builder** — in `compare.js` add state `sqlChunkSize: 10000,` next to `sqlExcludeColumns: '',` and add this method above `runSQLComparison`:

```js
    _buildSqlComparePayload() {
      if (!this.sqlConfigA) throw new Error('Select a config for Source A');
      if (!this.sqlConfigB) throw new Error('Select a config for Source B');
      if (!this.sqlQueryA.trim()) throw new Error('Enter a SQL query for Source A');
      if (!this.sqlQueryB.trim()) throw new Error('Enter a SQL query for Source B');
      return {
        config_id_a: parseInt(this.sqlConfigA),
        config_id_b: parseInt(this.sqlConfigB),
        query_a: this.sqlQueryA.trim(),
        query_b: this.sqlQueryB.trim(),
        label_a: this.sqlLabelA || 'Source A',
        label_b: this.sqlLabelB || 'Source B',
        connection_a: this.sqlConnectionA || null,
        connection_b: this.sqlConnectionB || null,
        key_columns: this.sqlKeyColumns.split(',').map(s => s.trim()).filter(Boolean),
        exclude_columns: this.sqlExcludeColumns.split(',').map(s => s.trim()).filter(Boolean),
        chunk_size: Number(this.sqlChunkSize) >= 0 ? Number(this.sqlChunkSize) : 10000,
        advanced: this._buildAdvanced('sql'),
      };
    },
```

In `runSQLComparison`, replace the four `if (...) { this.toast('warn', ...); return; }` lines and the inline `const payload = {...}` with:

```js
      let payload;
      try {
        payload = this._buildSqlComparePayload();
      } catch (e) {
        this.toast('warn', 'SQL compare', e.message);
        return;
      }
```

placed before `this.sqlCompareLoading = true;`, and delete the old `const payload = {...};` inside the `try`.

- [ ] **Step 2: Save-as-job support** — in `_assertCompareJobSourcesAreRepeatable`, add at the top:

```js
      if (compareType === 'sql') return; // config-backed queries always re-run
```

In `_compareJobBody`, add before the `if (this.saveJobCompareType === 'matrix')` block:

```js
      if (this.saveJobCompareType === 'sql') {
        const payload = this._buildSqlComparePayload();
        return {
          job_type: 'compare',
          key_columns: payload.key_columns,
          exclude_columns: payload.exclude_columns,
          params: { compare_type: 'sql', request: payload },
        };
      }
```

- [ ] **Step 3: Edit prefill** — in `launch.js::_compareSubTabForJob` add after the matrix line:

```js
        if (job.params?.compare_type === 'sql') return 'sql';
```

In `openCompareForJob`, add a branch before `} else if (subTab === 'bo') {`:

```js
      } else if (job.job_type === 'compare' && subTab === 'sql') {
        const req = job.params?.request || {};
        this.sqlConfigA = req.config_id_a != null ? String(req.config_id_a) : '';
        this.sqlConfigB = req.config_id_b != null ? String(req.config_id_b) : '';
        this.sqlConnectionA = req.connection_a || null;
        this.sqlConnectionB = req.connection_b || null;
        this.sqlQueryA = req.query_a || '';
        this.sqlQueryB = req.query_b || '';
        this.sqlLabelA = req.label_a || 'Source A';
        this.sqlLabelB = req.label_b || 'Source B';
        this.sqlKeyColumns = (req.key_columns || []).join(', ');
        this.sqlExcludeColumns = (req.exclude_columns || []).join(', ');
        this.sqlChunkSize = req.chunk_size ?? 10000;
        this._applyAdvancedToPrefix('sql', req.advanced);
```

- [ ] **Step 4: Button** — in `frontend/partials/tab-compare.html`, wrap the SQL run button (line ~946) so a Save as Job button sits beside it:

```html
      <div class="flex items-center gap-2">
        <button data-testid="compare-sql-run-btn" @click="runSQLComparison()" :disabled="sqlCompareLoading" class="btn-primary">
          <span x-show="!sqlCompareLoading">Run SQL Compare</span>
          <span x-show="sqlCompareLoading">Comparing...</span>
        </button>
        <button data-testid="compare-sql-save-job-btn" @click="openSaveCompareAsJob('sql')"
                class="btn-secondary btn-sm text-xs">Save as Job</button>
      </div>
```

- [ ] **Step 5: Rebuild** (same protected build as Task 12 Step 4).

- [ ] **Step 6: Smoke-check** — Compare → SQL: pick two configs, enter queries, Save as Job → job appears in Job Catalog with type compare. From the Job Catalog, open its Compare action: SQL sub-tab opens with configs, queries, keys prefilled; Save as Job shows the update-in-place name.

- [ ] **Step 7: Commit**

```bash
git add frontend/features/compare.js frontend/features/launch.js frontend/partials/tab-compare.html frontend/index.html
git commit -m "feat: save and edit sql compares as jobs"
```

---

### Task 14: Frontend — bulk compare import modal

**Files:**
- Create: `frontend/features/bulk-compare.js`
- Modify: `frontend/index.template.html:561` (script tag), `frontend/app.js:1505` (slice list), `frontend/partials/tab-launch.html:211-215` (button) and before `<!-- Job Modal -->` (~line 288, modal markup)
- Regenerate: `frontend/index.html`

- [ ] **Step 1: Create `frontend/features/bulk-compare.js`**

```js
(function (global) {
  'use strict';
  const blankState = () => ({
    open: false, fileName: '', preview: null, loading: false, committing: false,
    error: '', createSelection: false, selectionName: '', expanded: {},
  });

  global.ETL_FEATURE_BULK_COMPARE = function () {
    return {
      bulkCompare: blankState(),

      openBulkCompareModal() {
        this.bulkCompare = { ...blankState(), open: true };
      },

      closeBulkCompareModal() {
        this.bulkCompare = blankState();
      },

      async downloadBulkCompareTemplate() {
        try {
          const { blob } = await apiBlob('/api/jobs/bulk-compare/template');
          triggerDownload(blob, 'bulk-compare-template.csv');
        } catch (e) {
          this.toast('error', 'Template download failed', e.message);
        }
      },

      _bulkFileToBase64(file) {
        return new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
          reader.onerror = () => reject(reader.error);
          reader.readAsDataURL(file);
        });
      },

      async previewBulkCompareFile(event) {
        const file = event.target.files && event.target.files[0];
        event.target.value = '';
        if (!file) return;
        this.bulkCompare.fileName = file.name;
        this.bulkCompare.preview = null;
        this.bulkCompare.expanded = {};
        this.bulkCompare.error = '';
        this.bulkCompare.loading = true;
        try {
          const content_b64 = await this._bulkFileToBase64(file);
          this.bulkCompare.preview = await api('POST', '/api/jobs/bulk-compare/preview', {
            filename: file.name, content_b64,
          });
        } catch (e) {
          this.bulkCompare.error = e.message;
        } finally {
          this.bulkCompare.loading = false;
        }
      },

      toggleBulkCompareRow(rowNumber) {
        this.bulkCompare.expanded = {
          ...this.bulkCompare.expanded, [rowNumber]: !this.bulkCompare.expanded[rowNumber],
        };
      },

      bulkCompareCanImport() {
        const s = this.bulkCompare;
        return Boolean(
          s.preview && s.preview.rows.length && s.preview.invalid_count === 0 && !s.committing
          && (!s.createSelection || s.selectionName.trim())
        );
      },

      async commitBulkCompare() {
        if (!this.bulkCompareCanImport()) return;
        const s = this.bulkCompare;
        s.committing = true;
        s.error = '';
        try {
          const out = await api('POST', '/api/jobs/bulk-compare/commit', {
            jobs: s.preview.rows.map(r => r.job),
            selection_name: s.createSelection ? s.selectionName.trim() : null,
          });
          const selectionNote = out.selection ? ` and selection "${out.selection.name}"` : '';
          this.toast('success', 'Compare jobs imported', `${out.jobs.length} job(s)${selectionNote} saved.`);
          this.closeBulkCompareModal();
          if (this.loadJobs) await this.loadJobs();
          if (out.selection && this.loadJobSelections) await this.loadJobSelections();
        } catch (e) {
          s.error = e.message;
        } finally {
          s.committing = false;
        }
      },
    };
  };
})(window);
```

- [ ] **Step 2: Register the slice**

`frontend/index.template.html`: after `<script src="features/import-export.js"></script>` add `<script src="features/bulk-compare.js"></script>`.
`frontend/app.js:1505`: append `, ETL_FEATURE_BULK_COMPARE()` inside the `FEATURE_SLICES` array (after `ETL_FEATURE_IMPORT_EXPORT()`).

- [ ] **Step 3: Button** — in `frontend/partials/tab-launch.html`, Job Catalog header, before the `+ New Job` button:

```html
        <button @click="openBulkCompareModal()" class="btn-secondary btn-sm" data-testid="bulk-compare-open-btn">Bulk import compares</button>
```

- [ ] **Step 4: Modal markup** — insert immediately before `<!-- Job Modal -->`:

```html
  <!-- Bulk compare import modal -->
  <div x-show="bulkCompare.open" x-cloak class="modal-backdrop" @click.self="closeBulkCompareModal()" data-testid="bulk-compare-modal">
    <div class="modal-box w-full max-w-5xl" role="dialog" aria-modal="true" aria-labelledby="bulkCompareTitle">
      <div class="flex items-center mb-3">
        <h2 id="bulkCompareTitle" class="text-lg font-bold">Bulk import compare jobs</h2>
        <button @click="closeBulkCompareModal()" class="ml-auto text-slate-400" aria-label="close bulk compare import">✕</button>
      </div>
      <p class="text-sm text-slate-600 mb-3">
        One row per table pair. Configs are referenced by name; columns as <code>source:target</code> or a shared name, separated by <code>;</code>.
        <button @click="downloadBulkCompareTemplate()" class="text-indigo-600 underline" data-testid="bulk-compare-template-btn">Download template</button>
      </p>
      <label class="field-label" for="bulk-compare-file">Mapping sheet (.csv or .xlsx)</label>
      <input id="bulk-compare-file" type="file" accept=".csv,.xlsx" @change="previewBulkCompareFile($event)" class="field-input mb-3" data-testid="bulk-compare-file-input" />
      <p x-show="bulkCompare.loading" class="text-sm text-slate-500">Validating…</p>
      <p x-show="bulkCompare.error" x-text="bulkCompare.error" class="text-sm text-red-600 mb-2" data-testid="bulk-compare-error"></p>

      <template x-if="bulkCompare.preview">
        <div>
          <p class="text-sm mb-2" data-testid="bulk-compare-summary"
             x-text="`${bulkCompare.preview.valid_count} valid, ${bulkCompare.preview.invalid_count} invalid`"></p>
          <div class="overflow-auto border border-slate-200 rounded" style="max-height: 50vh">
            <table class="w-full text-xs">
              <thead class="bg-slate-50 text-left">
                <tr><th class="px-2 py-1">Row</th><th class="px-2 py-1">Job</th><th class="px-2 py-1">Source</th><th class="px-2 py-1">Target</th><th class="px-2 py-1">Status</th><th class="px-2 py-1"></th></tr>
              </thead>
              <tbody>
                <template x-for="row in bulkCompare.preview.rows" :key="row.row_number">
                  <tr class="border-t border-slate-100 align-top" :data-testid="`bulk-compare-row-${row.row_number}`">
                    <td class="px-2 py-1" x-text="row.row_number"></td>
                    <td class="px-2 py-1 font-mono" x-text="row.job_name"></td>
                    <td class="px-2 py-1" x-text="row.source"></td>
                    <td class="px-2 py-1" x-text="row.target"></td>
                    <td class="px-2 py-1">
                      <span class="compare-chip" :class="row.status === 'valid' ? 'chip-passed' : 'chip-regressed'" x-text="row.status"></span>
                      <span x-show="row.will_update" class="compare-chip" data-testid="bulk-compare-will-update">will update</span>
                      <div x-show="bulkCompare.expanded[row.row_number]" class="mt-1">
                        <template x-for="err in row.errors" :key="err"><p class="text-red-600" x-text="err"></p></template>
                        <template x-if="row.query_a"><pre class="font-mono whitespace-pre-wrap text-slate-600" x-text="`A: ${row.query_a}\nB: ${row.query_b}`"></pre></template>
                      </div>
                    </td>
                    <td class="px-2 py-1">
                      <button @click="toggleBulkCompareRow(row.row_number)" class="text-indigo-600" x-text="bulkCompare.expanded[row.row_number] ? 'Hide' : 'Details'"></button>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
          <div class="flex items-center gap-2 mt-3">
            <input type="checkbox" id="bulk-compare-create-selection" x-model="bulkCompare.createSelection" data-testid="bulk-compare-create-selection" />
            <label for="bulk-compare-create-selection" class="text-sm">Create a job selection with these jobs</label>
            <input x-show="bulkCompare.createSelection" x-model="bulkCompare.selectionName" class="field-input" style="width:260px" placeholder="Selection name" aria-label="selection name" data-testid="bulk-compare-selection-name" />
          </div>
        </div>
      </template>

      <div class="flex justify-end gap-2 mt-4">
        <button @click="closeBulkCompareModal()" class="btn-secondary">Cancel</button>
        <button @click="commitBulkCompare()" :disabled="!bulkCompareCanImport()" class="btn-primary disabled:opacity-40" data-testid="bulk-compare-import-btn">
          <span x-show="!bulkCompare.committing">Import</span>
          <span x-show="bulkCompare.committing">Importing…</span>
        </button>
      </div>
    </div>
  </div>

```

- [ ] **Step 5: Rebuild** (same protected build as Task 12 Step 4; `git status --short frontend/` should now also list `frontend/features/bulk-compare.js` as untracked and `frontend/app.js`, `frontend/index.template.html`, `frontend/partials/tab-launch.html` modified).

- [ ] **Step 6: Smoke-check** — Job Catalog → Bulk import compares → Download template → upload it with config names edited to two real configs → preview shows 2 valid rows, Details shows generated SQL → tick selection, name it → Import → toast, jobs in catalog, selection listed.

- [ ] **Step 7: Commit**

```bash
git add frontend/features/bulk-compare.js frontend/app.js frontend/index.template.html frontend/partials/tab-launch.html frontend/index.html
git commit -m "feat: bulk compare import modal in the job catalog"
```

---

### Task 15: Live Postgres verification

**Files:**
- Modify: `docker-compose.integration.yml` (add `postgres` service)
- Create: `tests/integration/test_postgres_live_compare.py`

- [ ] **Step 1: Pick a free host port**

Run (PowerShell tool): `netsh interface ipv4 show excludedportrange protocol=tcp`
Use `25432` unless it falls in an excluded range; otherwise pick another port above 20000 outside every range and use it below and in `LIVE_POSTGRES_PORT`.

- [ ] **Step 2: Add the service** — append under `services:` in `docker-compose.integration.yml`, matching the indentation of `sqlserver:`:

```yaml
  postgres:
    image: postgres:16
    container_name: atom-postgres-integration
    environment:
      POSTGRES_USER: "atom"
      POSTGRES_PASSWORD: "Atom_Test_12345"
      POSTGRES_DB: "atom_live"
    ports:
      - "25432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U atom -d atom_live"]
      interval: 5s
      timeout: 3s
      retries: 30
```

- [ ] **Step 3: Write the live test**

```python
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
```

The sheet exercises quoted aliases (`name;amount`) and cursor batches (`chunk_size=2`) against real Postgres.

- [ ] **Step 4: Run it**

```bash
docker compose -f docker-compose.integration.yml up -d postgres
RUN_LIVE_POSTGRES_TESTS=1 python -m pytest tests/integration/test_postgres_live_compare.py -v
```

Expected: 3 passed. If the end-to-end counts differ, print `result.mismatches` before changing assertions — the seed data defines the expected answer.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.integration.yml tests/integration/test_postgres_live_compare.py
git commit -m "test: live postgres verification for cursor batches and bulk sql compare jobs"
```

---

### Task 16: E2E — config form and bulk import flow

**Files:**
- Create: `tests/e2e/59-postgres-bulk-compare.spec.ts`

- [ ] **Step 1: Write the spec**

```ts
import { test, expect } from './fixtures';
import { authedContext, createConfig, deleteConfig, deleteJob } from './api-helpers';

const stamp = Date.now();
const SRC = `e2e-bulk-src-${stamp}`;
const TGT = `e2e-bulk-tgt-${stamp}`;
const JOB_A = `e2e_bulk_a_${stamp}`;
const JOB_B = `e2e_bulk_b_${stamp}`;
const SELECTION = `e2e-bulk-sel-${stamp}`;

function sheet(rows: string[]): { name: string; mimeType: string; buffer: Buffer } {
  const header = 'job_name,source_config,source_table,target_config,target_table,key_columns,compare_columns';
  return { name: 'map.csv', mimeType: 'text/csv', buffer: Buffer.from([header, ...rows].join('\n') + '\n') };
}

test.describe('59 postgres / aurora config and bulk compare import', () => {
  let srcId: number;
  let tgtId: number;

  test.beforeAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    const base = { db_host: '127.0.0.1', db_name: 'd', db_user: 'u', db_password: 'p' };
    try {
      srcId = (await createConfig(ctx, SRC, 'dev', { ...base, db_type: 'mssql' })).id;
      tgtId = (await createConfig(ctx, TGT, 'dev', { ...base, db_type: 'aurora_postgresql' })).id;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      await deleteJob(ctx, JOB_A);
      await deleteJob(ctx, JOB_B);
      const selections = await (await ctx.get('/api/selections')).json();
      for (const s of selections.filter((x: { name: string }) => x.name === SELECTION)) {
        await ctx.delete(`/api/selections/${s.id}`);
      }
      if (srcId) await deleteConfig(ctx, srcId);
      if (tgtId) await deleteConfig(ctx, tgtId);
    } finally {
      await ctx.dispose();
    }
  });

  test('aurora db type sets postgres defaults and shows ssl mode', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-config"]').click();
    await authedPage.getByRole('button', { name: /new config/i }).first().click();
    await authedPage.locator('[data-testid="config-modal-db-type-select"]').selectOption('aurora_postgresql');
    await expect(authedPage.locator('[data-testid="config-modal-db-port-input"]')).toHaveValue('5432');
    await expect(authedPage.locator('[data-testid="config-modal-db-driver-input"]')).toHaveValue('psycopg');
    await expect(authedPage.locator('[data-testid="config-modal-db-sslmode-select"]')).toHaveValue('require');
    await expect(authedPage.locator('[data-testid="config-modal-aurora-hint"]')).toBeVisible();
    await authedPage.locator('[data-testid="config-modal-db-type-select"]').selectOption('mssql');
    await expect(authedPage.locator('[data-testid="config-modal-db-sslmode-select"]')).toBeHidden();
  });

  test('invalid row blocks import', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-launch"]').click();
    await authedPage.locator('[data-testid="bulk-compare-open-btn"]').click();
    await authedPage.locator('[data-testid="bulk-compare-file-input"]').setInputFiles(sheet([
      `${JOB_A},${SRC},dbo.customer,${TGT},public.customer,id,`,
      `${JOB_B},NO_SUCH_CONFIG,dbo.orders,${TGT},public.orders,id,`,
    ]));
    await expect(authedPage.locator('[data-testid="bulk-compare-summary"]')).toHaveText('1 valid, 1 invalid');
    await expect(authedPage.locator('[data-testid="bulk-compare-import-btn"]')).toBeDisabled();
  });

  test('valid sheet imports jobs and a selection', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-launch"]').click();
    await authedPage.locator('[data-testid="bulk-compare-open-btn"]').click();
    await authedPage.locator('[data-testid="bulk-compare-file-input"]').setInputFiles(sheet([
      `${JOB_A},${SRC},dbo.customer,${TGT},public.customer,CUST_ID:customer_id,NAME:full_name`,
      `${JOB_B},${SRC},dbo.orders,${TGT},public.orders,id,`,
    ]));
    await expect(authedPage.locator('[data-testid="bulk-compare-summary"]')).toHaveText('2 valid, 0 invalid');
    await authedPage.locator('[data-testid="bulk-compare-row-2"]').getByRole('button', { name: 'Details' }).click();
    await expect(authedPage.locator('[data-testid="bulk-compare-row-2"]')).toContainText('CUST_ID AS [customer_id]');
    await authedPage.locator('[data-testid="bulk-compare-create-selection"]').check();
    await authedPage.locator('[data-testid="bulk-compare-selection-name"]').fill(SELECTION);
    await authedPage.locator('[data-testid="bulk-compare-import-btn"]').click();
    await expect(authedPage.locator('[data-testid="bulk-compare-modal"]')).toBeHidden();
    await expect(authedPage.getByText(JOB_A).first()).toBeVisible();
  });
});
```

Before running, verify the selectors this spec assumes against the real UI and adjust them in the spec (not the app): the Launch nav tab testid (`grep -n 'nav-tab-' frontend/partials/*.html frontend/index.template.html`), the New Config button (`grep -n "openNewConfigModal()" frontend/partials/tab-config.html` — use its `data-testid` if it has one), and `GET /api/selections` / `DELETE /api/selections/{id}` (both exist in `api/routes/selections.py`).

- [ ] **Step 2: Run it**

Per the repo's Playwright notes: `node node_modules/@playwright/test/cli.js test tests/e2e/59-postgres-bulk-compare.spec.ts --reporter=line`
Expected: 3 passed. If the web server port is in an excluded range, set the port override the config already supports (see `playwright.config.ts`).

- [ ] **Step 3: Run the existing compare / config / accessibility specs that touch the edited partials**

`node node_modules/@playwright/test/cli.js test tests/e2e/01-config.spec.ts tests/e2e/02-launch-jobs.spec.ts tests/e2e/21-accessibility.spec.ts tests/e2e/43-live-docker-matrix-save-as-job.spec.ts --reporter=line`
Expected: same pass/skip set as on master (skips for live-backend gates are fine).

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/59-postgres-bulk-compare.spec.ts
git commit -m "test(e2e): postgres config options and bulk compare import"
```

---

### Task 17: Docs and full verification

**Files:**
- Modify: `README.md` (Configuration section ~line 851, Compare Tab section ~line 2702)

- [ ] **Step 1: README** — in `## Configuration`, where DB types are described (search `netezza` / `db_type` in that section), add `postgresql` and `aurora_postgresql` with: default port 5432, driver `psycopg` (`pip install 'etl-framework[postgres]'`), `db_sslmode` (`prefer` / `require` defaults; Aurora host = cluster or reader endpoint). Note that `chunk_size` now means rows per cursor fetch from a single query (no ORDER BY/OFFSET paging) and works without key columns.

In `## Compare Tab`, add a "Bulk import compare jobs" subsection: where the button is, the column table from the spec (copy it), `src:tgt` mapping and `;` lists, query-in-table-cell rule, that preview saves nothing, all-or-nothing import, optional selection (created with live connections on), and that SQL compares can now be saved as jobs from the SQL sub-tab.

- [ ] **Step 2: Full Python suite**

Run: `python -m pytest tests -q -p no:cacheprovider -x --ignore=tests/e2e`
Expected: no failures (live tests skip without their env vars).

- [ ] **Step 3: Confirm the user's file is untouched and nothing stray is staged**

Run: `git status --short`
Expected: only ` M frontend/features/diff-search.js` (the user's pre-existing change) remains uncommitted.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: postgres and aurora configs, bulk compare import"
```

---

## Spec coverage check

| Spec item | Task |
|---|---|
| `db_type` Literal + `db_sslmode` + defaults | 1 |
| `DBEngine` psycopg URL, pool, missing-driver error, dependency extra | 2 |
| Discovery / preview / test connection | 5 |
| Cursor batches (`iter_query_batches`, `load_in_chunks`), remove `build_chunk_query` + fake window | 3, 4 |
| Frontend config options, SSL select, Aurora hint | 12 |
| `compare_type: "sql"` validation (schema + job_validation), `compare_sql`, executor branch | 6, 7 |
| SQL compare editor (save/edit as job) | 13 |
| Sheet columns, header normalization, unknown/missing headers, 1000-row cap | 8 |
| SQL generation: aliasing, quoting, where, query passthrough | 9 |
| Row validation rules, duplicates, `will_update`, job build | 10 |
| Template / preview / commit endpoints, 409, all-or-nothing, selection | 11 |
| Bulk import UI | 14 |
| Live Postgres test, docker service | 15 |
| E2E | 16 |
| Error message naming `[postgres]` extra | 2 |
