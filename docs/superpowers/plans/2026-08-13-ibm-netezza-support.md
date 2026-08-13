# IBM Netezza Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add IBM Netezza database connection and query support to the framework via `nzpy` and `pyodbc`.

**Architecture:** Extend `EnvironmentConfig` with `db_type` ("mssql" | "netezza", defaulting to "mssql") and update default port behavior (5480 for Netezza). Extend `DBEngine` to construct Netezza SQLAlchemy connection strings dynamically based on `db_type` and `db_driver`.

**Tech Stack:** Python 3.10+, Pydantic v2, SQLAlchemy 2.0+, `nzpy`, `pyodbc`, `pytest`.

## Global Constraints

- Python compatibility: 3.10+
- Backward compatibility: Default `db_type` is "mssql", default port for mssql remains 1433
- Netezza default port: 5480 when `db_type` is "netezza"
- Dynamic connection strings: `netezza+nzpy://` for `nzpy` driver, `netezza+pyodbc://` or ODBC connect string for `pyodbc` drivers

---

### Task 1: Extend EnvironmentConfig for IBM Netezza

**Files:**
- Modify: `etl_framework/config/models.py:18-35`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: Pydantic `BaseModel` in `EnvironmentConfig`
- Produces: `EnvironmentConfig.db_type` attribute with default "mssql", default port 5480 for "netezza"

- [ ] **Step 1: Write the failing test**

Add test to `tests/unit/test_config.py`:
```python
def test_environment_config_netezza_defaults():
    config = EnvironmentConfig(
        name="qa_netezza",
        db_type="netezza",
        db_host="netezza.example.com",
        db_name="testdb",
        db_user="admin",
        db_password="secretpassword",
        db_driver="nzpy",
    )
    assert config.db_type == "netezza"
    assert config.db_port == 5480
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config.py::test_environment_config_netezza_defaults -v`
Expected: FAIL with `ValidationError` or `AttributeError` / port assertion mismatch (`1433 != 5480`).

- [ ] **Step 3: Write minimal implementation**

In `etl_framework/config/models.py`, add `db_type: Literal["mssql", "netezza"] = "mssql"` and a validator to set default port 5480 if `db_type == "netezza"` and `db_port` was not explicitly provided:

```python
    db_type: Literal["mssql", "netezza"] = "mssql"
    db_host: str
    db_port: int = 1433

    @model_validator(mode="before")
    @classmethod
    def set_netezza_default_port(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if data.get("db_type") == "netezza" and "db_port" not in data:
                data["db_port"] = 5480
        return data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/config/models.py tests/unit/test_config.py
git commit -m "feat(config): add db_type and netezza default port handling"
```

---

### Task 2: Extend DBEngine for IBM Netezza Connection Strings

**Files:**
- Modify: `etl_framework/db/engine.py:15-42`
- Test: `tests/unit/test_db_engine.py`

**Interfaces:**
- Consumes: `EnvironmentConfig` with `db_type` and `db_driver`
- Produces: SQLAlchemy connection engine targeting SQL Server or Netezza (`netezza+nzpy://` or `netezza+pyodbc://`)

- [ ] **Step 1: Write the failing test**

Add unit tests to `tests/unit/test_db_engine.py`:
```python
def test_db_engine_netezza_nzpy_connection_url(monkeypatch):
    captured_urls = []

    def fake_create_engine(url, **kwargs):
        captured_urls.append(url)
        return MagicMock()

    monkeypatch.setattr("etl_framework.db.engine.create_engine", fake_create_engine)

    config = EnvironmentConfig(
        name="netezza_dev",
        db_type="netezza",
        db_host="netezza.host",
        db_port=5480,
        db_name="analytics",
        db_user="nz_user",
        db_password="nz_password",
        db_driver="nzpy",
    )
    engine = DBEngine(config)
    assert captured_urls[0] == "netezza+nzpy://nz_user:nz_password@netezza.host:5480/analytics"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_db_engine.py::test_db_engine_netezza_nzpy_connection_url -v`
Expected: FAIL (connection string uses `mssql+pyodbc:///?odbc_connect=...`).

- [ ] **Step 3: Write minimal implementation**

Update `DBEngine.__init__` in `etl_framework/db/engine.py`:
```python
        if _engine is not None:
            self._engine = _engine
        else:
            if getattr(env_config, "db_type", "mssql") == "netezza":
                if env_config.db_driver.lower() == "nzpy":
                    connection_url = (
                        f"netezza+nzpy://{env_config.db_user}:{env_config.db_password}"
                        f"@{env_config.db_host}:{env_config.db_port}/{env_config.db_name}"
                    )
                else:
                    params = urllib.parse.quote_plus(
                        f"DRIVER={{{env_config.db_driver}}};"
                        f"SERVER={env_config.db_host};"
                        f"PORT={env_config.db_port};"
                        f"DATABASE={env_config.db_name};"
                        f"UID={env_config.db_user};"
                        f"PWD={env_config.db_password};"
                    )
                    connection_url = f"netezza+pyodbc:///?odbc_connect={params}"
            else:
                trust_cert = "TrustServerCertificate=yes;" if "18" in env_config.db_driver else ""
                params = urllib.parse.quote_plus(
                    f"DRIVER={{{env_config.db_driver}}};"
                    f"SERVER={env_config.db_host},{env_config.db_port};"
                    f"DATABASE={env_config.db_name};"
                    f"UID={env_config.db_user};"
                    f"PWD={env_config.db_password};"
                    f"Connect Timeout={env_config.db_connect_timeout};"
                    f"{trust_cert}"
                )
                connection_url = f"mssql+pyodbc:///?odbc_connect={params}"

            self._engine = create_engine(
                connection_url,
                pool_size=env_config.db_pool_size,
                max_overflow=env_config.db_pool_overflow,
                pool_timeout=env_config.db_pool_timeout,
                pool_recycle=env_config.db_pool_recycle,
                echo=False,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_db_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/db/engine.py tests/unit/test_db_engine.py
git commit -m "feat(db): add Netezza connection URL assembly to DBEngine"
```

---

### Task 3: Add Dependency & Documentation Updates

**Files:**
- Modify: `pyproject.toml`
- Modify: `.kiro/specs/etl-sapbo-testing-framework/requirements.md`
- Test: `pytest`

**Interfaces:**
- Consumes: Optional Netezza python driver dependency
- Produces: Updated `pyproject.toml` and requirement specification

- [ ] **Step 1: Write the failing test / check**

Check requirements specification does not yet document Requirement for Netezza support.

- [ ] **Step 2: Modify dependencies and docs**

Add `nzpy` as optional dependency in `pyproject.toml` or main dependencies if standard. Update `.kiro/specs/etl-sapbo-testing-framework/requirements.md` under Requirement 3 to state support for IBM Netezza (`db_type: netezza`).

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `pytest`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .kiro/specs/etl-sapbo-testing-framework/requirements.md
git commit -m "docs: document IBM Netezza support requirement and optional dependencies"
```
