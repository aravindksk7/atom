# Multi-DB Connections Per Environment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a single saved config to define multiple named DB connections; users pick source/target connection at run launch time.

**Architecture:** Add a `resolve_connection(config_json, name)` helper that merges a named connection entry on top of the top-level `config_json` fields and returns a full `EnvironmentConfig`. Wire this into the run snapshot builder and SQL compare service. Add named-connection cards to the config modal and inline dropdowns to the run launch and SQL compare forms.

**Tech Stack:** Python 3.14, FastAPI, Pydantic v2, Alpine.js (frontend)

---

## File Map

| File | What changes |
|---|---|
| `etl_framework/config/models.py` | Add `ConnectionEntry` model + `resolve_connection()` |
| `api/routes/configs.py` | Recurse `_mask` into `connections`; `_build_env` uses `resolve_connection`; `/validate` validates named entries |
| `api/schemas.py` | Add `source_connection`/`target_connection` to `RunTrigger`; add `connection_a`/`connection_b` to `SQLCompareRequest` |
| `api/routes/runs.py` | `_snapshot_from_trigger` uses `resolve_connection` + validates connection names |
| `api/services/compare_service.py` | `run_sql_comparison` uses `resolve_connection` |
| `frontend/app.js` | Named-connection state + helpers; connection dropdowns in launch + SQL compare |
| `frontend/index.html` | Named-connection cards in config modal; pickers in run launch + SQL compare |
| `tests/unit/test_resolve_connection.py` | New — unit tests for `resolve_connection` |
| `tests/unit/test_compare_api.py` | Add SQL compare connection tests |
| `tests/unit/test_run_trigger.py` | New — unit tests for `_snapshot_from_trigger` with connections |

---

## Task 1: `resolve_connection` helper

**Files:**
- Modify: `etl_framework/config/models.py`
- Create: `tests/unit/test_resolve_connection.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_resolve_connection.py`:

```python
import pytest
from etl_framework.config.models import resolve_connection, EnvironmentConfig

BASE = {
    "db_host": "default-server",
    "db_port": 1433,
    "db_name": "default_db",
    "db_user": "sa",
    "db_password": "secret",
    "db_driver": "ODBC Driver 17 for SQL Server",
    "db_pool_size": 5,
    "db_pool_overflow": 10,
    "db_pool_timeout": 30,
    "db_pool_recycle": 3600,
    "db_connect_timeout": 15,
    "automic_url": "",
    "automic_user": "",
    "automic_password": "",
    "automic_timeout": 30,
    "automic_max_retries": 3,
    "bo_url": "",
    "bo_user": "",
    "bo_password": "",
    "bo_timeout": 60,
    "connections": {
        "hr_db": {
            "db_host": "hr-server",
            "db_name": "HR",
            "db_user": "hr_user",
            "db_password": "hr_secret",
        },
        "finance_db": {
            "db_host": "finance-server",
            "db_name": "FIN",
            "db_user": "fin_user",
            "db_password": "fin_secret",
        },
    },
}


def test_none_name_returns_default_connection():
    env = resolve_connection(BASE, None, env_name="prod")
    assert env.db_host == "default-server"
    assert env.db_name == "default_db"
    assert env.name == "prod"


def test_unknown_name_falls_back_to_default():
    env = resolve_connection(BASE, "nonexistent", env_name="prod")
    assert env.db_host == "default-server"


def test_named_connection_overrides_host_and_db():
    env = resolve_connection(BASE, "hr_db", env_name="prod")
    assert env.db_host == "hr-server"
    assert env.db_name == "HR"
    assert env.db_user == "hr_user"
    assert env.db_password == "hr_secret"


def test_named_connection_inherits_unset_fields():
    env = resolve_connection(BASE, "hr_db", env_name="prod")
    assert env.db_port == 1433
    assert env.db_driver == "ODBC Driver 17 for SQL Server"
    assert env.db_pool_size == 5


def test_named_connection_name_is_qualified():
    env = resolve_connection(BASE, "hr_db", env_name="prod")
    assert env.name == "prod/hr_db"


def test_connections_key_not_passed_to_env_config():
    # Should not raise even though config_json has a 'connections' key
    env = resolve_connection(BASE, None, env_name="prod")
    assert isinstance(env, EnvironmentConfig)


def test_config_without_connections_key():
    plain = {k: v for k, v in BASE.items() if k != "connections"}
    env = resolve_connection(plain, None, env_name="dev")
    assert env.db_host == "default-server"
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/unit/test_resolve_connection.py -v
```

Expected: `ImportError` or `AttributeError` — `resolve_connection` not defined yet.

- [ ] **Step 3: Add `ConnectionEntry` and `resolve_connection` to `etl_framework/config/models.py`**

Append after the existing `EnvironmentConfig` class:

```python
class ConnectionEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    db_host: str | None = None
    db_port: int | None = None
    db_name: str | None = None
    db_user: str | None = None
    db_password: str | None = None
    db_driver: str | None = None
    db_pool_size: int | None = None
    db_pool_overflow: int | None = None
    db_pool_timeout: int | None = None
    db_pool_recycle: int | None = None
    db_connect_timeout: int | None = None


def resolve_connection(
    config_json: dict,
    name: str | None,
    env_name: str = "",
) -> EnvironmentConfig:
    """Return an EnvironmentConfig for a named connection, merging with top-level defaults."""
    base = {k: v for k, v in config_json.items() if k != "connections"}
    connections = config_json.get("connections") or {}
    if name is not None and name in connections:
        entry = connections[name]
        override = {k: v for k, v in entry.items() if v is not None}
        base.update(override)
        resolved_name = f"{env_name}/{name}" if env_name else name
    else:
        resolved_name = env_name
    return EnvironmentConfig(name=resolved_name, **base)
```

- [ ] **Step 4: Run tests to confirm they pass**

```
python -m pytest tests/unit/test_resolve_connection.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```
git add etl_framework/config/models.py tests/unit/test_resolve_connection.py
git commit -m "feat: add ConnectionEntry and resolve_connection helper"
```

---

## Task 2: Config API — mask, build_env, validate

**Files:**
- Modify: `api/routes/configs.py`

- [ ] **Step 1: Replace `_mask` to recurse into `connections`**

Replace the existing `_mask` function (lines 31–33) with:

```python
def _mask(data: dict) -> dict:
    result = {
        k: (_MASK if k in _SENSITIVE_KEYS and v is not None else v)
        for k, v in data.items()
        if k != "connections"
    }
    if "connections" in data and isinstance(data["connections"], dict):
        result["connections"] = {
            conn_name: {
                k: (_MASK if k in _SENSITIVE_KEYS and v is not None else v)
                for k, v in entry.items()
            }
            for conn_name, entry in data["connections"].items()
        }
    return result
```

- [ ] **Step 2: Update `_build_env` to accept `connection_name`**

Replace the existing `_build_env` function (lines 211–217) with:

```python
def _build_env(cfg, connection_name: str | None = None) -> EnvironmentConfig:
    from etl_framework.config.models import resolve_connection
    return resolve_connection(
        cfg.config_json or {},
        connection_name,
        env_name=cfg.env_name or cfg.name,
    )
```

- [ ] **Step 3: Update `/validate` to validate named connections**

In `validate_config` (lines 62–83), after the existing `try/except ValidationError` block that validates the top-level config, add validation of each named connection entry. Replace the function body:

```python
@router.post("/validate", response_model=ConfigValidationOut)
def validate_config(body: ConfigValidationRequest):
    from etl_framework.config.models import resolve_connection
    try:
        env_config = EnvironmentConfig.model_validate(
            {"name": body.env_name, **body.config_data}
        )
    except ValidationError as exc:
        errors = [
            FrameworkErrorOut(
                error_type="validation_error",
                message=err["msg"],
                field_name=".".join(str(part) for part in err["loc"]),
                details={"input": err.get("input")},
            )
            for err in exc.errors()
        ]
        return ConfigValidationOut(ok=False, env_name=body.env_name, errors=errors)

    # Validate each named connection by attempting to resolve it
    connection_errors: list[FrameworkErrorOut] = []
    for conn_name in (body.config_data.get("connections") or {}):
        try:
            resolve_connection(body.config_data, conn_name, env_name=body.env_name)
        except Exception as exc:
            connection_errors.append(FrameworkErrorOut(
                error_type="validation_error",
                message=str(exc),
                field_name=f"connections.{conn_name}",
                details={},
            ))
    if connection_errors:
        return ConfigValidationOut(ok=False, env_name=body.env_name, errors=connection_errors)

    return ConfigValidationOut(
        ok=True,
        env_name=body.env_name,
        config_data=_mask(env_config.model_dump(exclude={"name"})),
    )
```

- [ ] **Step 4: Run existing config tests**

```
python -m pytest tests/unit/test_config.py tests/unit/test_compare_api.py -v
```

Expected: all existing tests pass.

- [ ] **Step 5: Commit**

```
git add api/routes/configs.py
git commit -m "feat: mask nested connections; build_env uses resolve_connection; validate named entries"
```

---

## Task 3: Schema additions

**Files:**
- Modify: `api/schemas.py`

- [ ] **Step 1: Add `source_connection` and `target_connection` to `RunTrigger`**

In `api/schemas.py`, find the `RunTrigger` class (around line 185). Add two optional fields after `target_env`:

```python
class RunTrigger(BaseModel):
    source_env: str
    target_env: str
    source_connection: str | None = None
    target_connection: str | None = None
    job_names: list[str] = Field(default_factory=list)
    job_sequence: list[str | SequenceStep] = Field(default_factory=list)
    # ... rest unchanged
```

- [ ] **Step 2: Add `connection_a` and `connection_b` to `SQLCompareRequest`**

Find `SQLCompareRequest` (around line 537). Add two optional fields after `label_b`:

```python
class SQLCompareRequest(BaseModel):
    config_id_a: int
    config_id_b: int
    query_a: str
    query_b: str
    label_a: str = "Source A"
    label_b: str = "Source B"
    connection_a: str | None = None
    connection_b: str | None = None
    key_columns: list[str] = Field(default_factory=list)
    exclude_columns: list[str] = Field(default_factory=list)
```

- [ ] **Step 3: Verify imports still work**

```
python -c "from api.schemas import RunTrigger, SQLCompareRequest; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```
git add api/schemas.py
git commit -m "feat: add source_connection/target_connection to RunTrigger; connection_a/b to SQLCompareRequest"
```

---

## Task 4: Run snapshot — use `resolve_connection`

**Files:**
- Modify: `api/routes/runs.py`
- Create: `tests/unit/test_run_trigger.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_run_trigger.py`:

```python
from __future__ import annotations
import pytest
from unittest.mock import MagicMock
from fastapi import HTTPException
from api.routes.runs import _snapshot_from_trigger
from api.schemas import RunTrigger


def _make_cfg(config_json: dict):
    cfg = MagicMock()
    cfg.id = 1
    cfg.name = "prod"
    cfg.env_name = "prod"
    cfg.config_json = config_json
    return cfg


def _make_db(cfg):
    db = MagicMock()
    repo = MagicMock()
    repo.get.return_value = cfg
    db.__enter__ = lambda s: s
    db.__exit__ = MagicMock(return_value=False)
    return db, repo


BASE_CFG = {
    "db_host": "default-server",
    "db_port": 1433,
    "db_name": "default_db",
    "db_user": "sa",
    "db_password": "secret",
    "db_driver": "ODBC Driver 17 for SQL Server",
    "db_pool_size": 5,
    "db_pool_overflow": 10,
    "db_pool_timeout": 30,
    "db_pool_recycle": 3600,
    "db_connect_timeout": 15,
    "automic_url": "",
    "automic_user": "",
    "automic_password": "",
    "automic_timeout": 30,
    "automic_max_retries": 3,
    "bo_url": "",
    "bo_user": "",
    "bo_password": "",
    "bo_timeout": 60,
    "connections": {
        "hr_db": {"db_host": "hr-server", "db_name": "HR",
                  "db_user": "hr_user", "db_password": "hr_secret"},
    },
}


def test_no_connection_uses_default(monkeypatch):
    cfg = _make_cfg(BASE_CFG)
    db = MagicMock()

    from etl_framework.repository.repository import ConfigRepository
    monkeypatch.setattr(ConfigRepository, "get", lambda self, id: cfg)

    body = RunTrigger(source_env="dev", target_env="prod",
                      job_names=[], config_id=1)
    snapshot = _snapshot_from_trigger(body, db)

    assert snapshot["source_credentials"]["db_host"] == "default-server"
    assert snapshot["source_credentials"]["name"] == "dev"


def test_named_connection_overrides_credentials(monkeypatch):
    cfg = _make_cfg(BASE_CFG)
    db = MagicMock()

    from etl_framework.repository.repository import ConfigRepository
    monkeypatch.setattr(ConfigRepository, "get", lambda self, id: cfg)

    body = RunTrigger(source_env="dev", target_env="prod",
                      job_names=[], config_id=1,
                      source_connection="hr_db", target_connection="hr_db")
    snapshot = _snapshot_from_trigger(body, db)

    assert snapshot["source_credentials"]["db_host"] == "hr-server"
    assert snapshot["source_credentials"]["db_name"] == "HR"
    assert snapshot["source_credentials"]["name"] == "dev"
    assert snapshot["target_credentials"]["db_host"] == "hr-server"


def test_unknown_connection_name_raises_422(monkeypatch):
    cfg = _make_cfg(BASE_CFG)
    db = MagicMock()

    from etl_framework.repository.repository import ConfigRepository
    monkeypatch.setattr(ConfigRepository, "get", lambda self, id: cfg)

    body = RunTrigger(source_env="dev", target_env="prod",
                      job_names=[], config_id=1,
                      source_connection="nonexistent")
    with pytest.raises(HTTPException) as exc_info:
        _snapshot_from_trigger(body, db)
    assert exc_info.value.status_code == 422
    assert "nonexistent" in str(exc_info.value.detail)
    assert "hr_db" in str(exc_info.value.detail)
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/unit/test_run_trigger.py -v
```

Expected: FAIL — `_snapshot_from_trigger` doesn't use `resolve_connection` yet.

- [ ] **Step 3: Update `_snapshot_from_trigger` in `api/routes/runs.py`**

Add import at the top of the file (with the other imports):

```python
from etl_framework.config.models import resolve_connection as _resolve_connection
```

Replace the `_snapshot_from_trigger` function:

```python
def _snapshot_from_trigger(body: RunTrigger, db: Session) -> dict:
    cfg_data = dict(body.config_data or {})
    cfg = ConfigRepository(db).get(body.config_id) if body.config_id is not None else None
    if body.config_id is not None and cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")
    if cfg is not None:
        cfg_data = {**(cfg.config_json or {}), **cfg_data}

    snapshot = dict(cfg_data)
    if cfg is not None:
        snapshot.update({
            "config_id": cfg.id,
            "config_name": cfg.name,
            "env_name": cfg.env_name,
        })

    if "source_credentials" not in snapshot:
        _validate_connection_name(cfg, body.source_connection, "source_connection")
        src = _resolve_connection(
            cfg.config_json if cfg else cfg_data,
            body.source_connection,
            env_name=body.source_env,
        )
        snapshot["source_credentials"] = {**src.model_dump(), "name": body.source_env}
    if "target_credentials" not in snapshot:
        _validate_connection_name(cfg, body.target_connection, "target_connection")
        tgt = _resolve_connection(
            cfg.config_json if cfg else cfg_data,
            body.target_connection,
            env_name=body.target_env,
        )
        snapshot["target_credentials"] = {**tgt.model_dump(), "name": body.target_env}
    if "bo_credentials" not in snapshot:
        snapshot["bo_credentials"] = {"name": "bo", **cfg_data}
    if "automic_credentials" not in snapshot:
        snapshot["automic_credentials"] = {"name": "automic", **cfg_data}
    return snapshot


def _validate_connection_name(cfg, name: str | None, field: str) -> None:
    if name is None or cfg is None:
        return
    available = list((cfg.config_json or {}).get("connections", {}).keys())
    if name not in available:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"{field} '{name}' not found in config connections",
                "available": available,
            },
        )
```

- [ ] **Step 4: Run all run/compare tests**

```
python -m pytest tests/unit/test_run_trigger.py tests/unit/test_compare_api.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```
git add api/routes/runs.py tests/unit/test_run_trigger.py
git commit -m "feat: snapshot builder resolves named DB connections"
```

---

## Task 5: SQL compare — use `resolve_connection`

**Files:**
- Modify: `api/services/compare_service.py`
- Modify: `tests/unit/test_compare_api.py`

- [ ] **Step 1: Write failing test**

Add this test to `tests/unit/test_compare_api.py`:

```python
def test_sql_compare_rejects_unknown_connection(client, monkeypatch):
    """connection_a that doesn't exist in config.connections → 422."""
    from etl_framework.repository.models import SavedConfig
    from etl_framework.repository.repository import ConfigRepository

    fake_cfg = MagicMock(spec=SavedConfig)
    fake_cfg.id = 7
    fake_cfg.env_name = "prod"
    fake_cfg.config_json = {
        "db_host": "server", "db_port": 1433, "db_name": "db",
        "db_user": "u", "db_password": "p",
        "db_driver": "ODBC Driver 17 for SQL Server",
        "db_pool_size": 5, "db_pool_overflow": 10, "db_pool_timeout": 30,
        "db_pool_recycle": 3600, "db_connect_timeout": 15,
        "automic_url": "", "automic_user": "", "automic_password": "",
        "automic_timeout": 30, "automic_max_retries": 3,
        "bo_url": "", "bo_user": "", "bo_password": "", "bo_timeout": 60,
        "connections": {"hr_db": {"db_host": "hr-server", "db_name": "HR",
                                   "db_user": "u", "db_password": "p"}},
    }
    monkeypatch.setattr(ConfigRepository, "get", lambda self, id: fake_cfg)

    import api.routes.compare as compare_module
    monkeypatch.setattr(compare_module, "_run_sql_bg", lambda *a, **kw: None)

    resp = client.post("/api/compare/sql", json={
        "config_id_a": 7, "config_id_b": 7,
        "query_a": "SELECT 1", "query_b": "SELECT 1",
        "connection_a": "does_not_exist",
    })
    assert resp.status_code == 422
```

- [ ] **Step 2: Run to confirm it fails**

```
python -m pytest tests/unit/test_compare_api.py::test_sql_compare_rejects_unknown_connection -v
```

Expected: FAIL — no connection validation in `run_sql_comparison` yet.

- [ ] **Step 3: Update `run_sql_comparison` in `api/services/compare_service.py`**

Replace the body of `run_sql_comparison`:

```python
def run_sql_comparison(self, req: SQLCompareRequest, run_id: str) -> None:
    """Execute two SQL queries against their respective DB configs and diff the results."""
    from etl_framework.db.engine import DBEngine
    from etl_framework.config.models import resolve_connection

    try:
        self._repo.update_run_status(run_id, "RUNNING", started_at=datetime.now(timezone.utc))

        cfg_a = self._config_repo.get(req.config_id_a)
        if cfg_a is None:
            raise HTTPException(status_code=404, detail="Config A not found")
        cfg_b = self._config_repo.get(req.config_id_b)
        if cfg_b is None:
            raise HTTPException(status_code=404, detail="Config B not found")

        def _check(cfg, name, field):
            if name is None:
                return
            available = list((cfg.config_json or {}).get("connections", {}).keys())
            if name not in available:
                raise HTTPException(
                    status_code=422,
                    detail={"message": f"{field} '{name}' not found", "available": available},
                )

        _check(cfg_a, req.connection_a, "connection_a")
        _check(cfg_b, req.connection_b, "connection_b")

        env_a = resolve_connection(cfg_a.config_json or {}, req.connection_a, env_name=cfg_a.env_name or "")
        env_b = resolve_connection(cfg_b.config_json or {}, req.connection_b, env_name=cfg_b.env_name or "")

        engine_a = DBEngine(env_a)
        engine_b = DBEngine(env_b)
        try:
            df_a = engine_a.execute_query(req.query_a)
            df_b = engine_b.execute_query(req.query_b)
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
        )
        self._run_tabular_file_compare(recon_req, run_id, df_a, df_b)
    except Exception:
        logger.exception("SQL comparison failed for run %s", run_id)
        self._repo.update_run_status(run_id, "ERROR", completed_at=datetime.now(timezone.utc), error=1)
        raise
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/unit/test_compare_api.py tests/unit/test_resolve_connection.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```
git add api/services/compare_service.py tests/unit/test_compare_api.py
git commit -m "feat: SQL compare resolves named connections; validates connection names"
```

---

## Task 6: Frontend — Named Connections in config modal

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`

- [ ] **Step 1: Add `connections` array to `configModal` initial state in `app.js`**

In `openNewConfig` (around line 659), add `connections: []` to the `configModal` object:

```javascript
this.configModal = {
  id: null, name: '', env_name: 'dev',
  db_host: 'localhost', db_port: 1433, db_name: '', db_user: '', db_password: '',
  db_connect_timeout: 15,
  bo_url: '', bo_user: '', bo_password: '', bo_timeout: 60,
  automic_url: '', automic_user: '', automic_password: '',
  connections: [],
};
```

- [ ] **Step 2: Populate `connections` array when editing an existing config in `editConfig`**

In `editConfig` (around line 672), add after the existing fields assignment:

```javascript
this.configModal = {
  id: cfg.id, name: cfg.name, env_name: cfg.env_name,
  db_host: d.db_host || '', db_port: d.db_port || 1433,
  db_name: d.db_name || '', db_user: d.db_user || '', db_password: d.db_password || '',
  db_connect_timeout: d.db_connect_timeout || 15,
  bo_url: d.bo_url || '', bo_user: d.bo_user || '', bo_password: d.bo_password || '',
  bo_timeout: d.bo_timeout || 60,
  automic_url: d.automic_url || '', automic_user: d.automic_user || '',
  automic_password: d.automic_password || '',
  connections: Object.entries(d.connections || {}).map(([name, entry]) => ({
    name,
    db_host: entry.db_host || '',
    db_name: entry.db_name || '',
    db_user: entry.db_user || '',
    db_password: entry.db_password || '',
    expanded: false,
  })),
};
```

- [ ] **Step 3: Serialize `connections` in `_configDataFromModal`**

In `_configDataFromModal` (around line 686), add `connections` to the returned object:

```javascript
_configDataFromModal() {
  const m = this.configModal;
  const data = {
    db_host: m.db_host || 'localhost',
    db_port: Number(m.db_port) || 1433,
    db_name: m.db_name || '',
    db_user: m.db_user || '',
    db_password: m.db_password || '',
    db_driver: 'ODBC Driver 17 for SQL Server',
    db_pool_size: 5, db_pool_overflow: 10, db_pool_timeout: 30,
    db_pool_recycle: 3600,
    db_connect_timeout: Number(m.db_connect_timeout) || 15,
    bo_url: m.bo_url || '', bo_user: m.bo_user || '',
    bo_password: m.bo_password || '',
    bo_timeout: Number(m.bo_timeout) || 60,
    automic_url: m.automic_url || '', automic_user: m.automic_user || '',
    automic_password: m.automic_password || '',
    automic_timeout: 30, automic_max_retries: 3,
  };
  if (m.connections && m.connections.length > 0) {
    data.connections = Object.fromEntries(
      m.connections
        .filter(c => c.name.trim())
        .map(c => [c.name.trim(), {
          db_host: c.db_host || undefined,
          db_name: c.db_name || undefined,
          db_user: c.db_user || undefined,
          db_password: c.db_password || undefined,
        }])
    );
  }
  return data;
},
```

- [ ] **Step 4: Add named-connection helper functions to `app.js`**

Add after `_configDataFromModal`:

```javascript
addNamedConnection() {
  const idx = this.configModal.connections.length + 1;
  this.configModal.connections.push({
    name: `connection_${idx}`,
    db_host: '', db_name: '', db_user: '', db_password: '',
    expanded: true,
  });
},

removeNamedConnection(idx) {
  this.configModal.connections.splice(idx, 1);
},

toggleNamedConnection(idx) {
  this.configModal.connections[idx].expanded = !this.configModal.connections[idx].expanded;
},

namedConnectionSummary(conn) {
  const parts = [conn.db_host, conn.db_name].filter(Boolean);
  return parts.length ? parts.join(' / ') : 'not configured';
},
```

- [ ] **Step 5: Add Named Connections section to config modal in `index.html`**

In `frontend/index.html`, after the closing `</div>` of the `grid-2` for the DB fields section (line 412), and before the `<div class="divider"></div>` that precedes the SAP BO section (line 413), insert:

```html
        <div class="divider"></div>
        <div>
          <div class="flex items-center justify-between mb-2">
            <div>
              <span class="field-label">NAMED CONNECTIONS</span>
              <span class="text-xs text-slate-400 ml-2">Override DB credentials per connection. Unset fields inherit from defaults above.</span>
            </div>
            <button @click="addNamedConnection()" type="button" class="btn-secondary btn-sm text-xs">+ Add Connection</button>
          </div>
          <template x-for="(conn, idx) in configModal.connections" :key="idx">
            <div class="border border-slate-200 rounded-lg mb-2 overflow-hidden">
              <div class="flex items-center gap-2 px-3 py-2 bg-slate-50 border-b border-slate-200">
                <input x-model="conn.name" class="field-input font-mono text-xs font-semibold py-1 px-2" style="width:140px" placeholder="connection_name" />
                <span class="text-xs text-slate-400 flex-1" x-text="namedConnectionSummary(conn)"></span>
                <button @click="toggleNamedConnection(idx)" type="button" class="text-slate-400 text-xs px-1" x-text="conn.expanded ? '▲' : '▼'"></button>
                <button @click="removeNamedConnection(idx)" type="button" class="text-red-400 text-xs px-1">✕</button>
              </div>
              <div x-show="conn.expanded" class="p-3 grid-2">
                <div><label class="field-label">DB Host</label><input x-model="conn.db_host" class="field-input" placeholder="inherits default" /></div>
                <div><label class="field-label">DB Name</label><input x-model="conn.db_name" class="field-input" placeholder="inherits default" /></div>
                <div><label class="field-label">DB User</label><input x-model="conn.db_user" class="field-input" placeholder="inherits default" /></div>
                <div><label class="field-label">DB Password</label><input x-model="conn.db_password" type="password" class="field-input" placeholder="inherits default" /></div>
              </div>
            </div>
          </template>
        </div>
```

- [ ] **Step 6: Smoke-test by running the dev server and opening the config modal**

```
python -m uvicorn api.main:app --port 8000 --reload
```

Open http://localhost:8000, go to Settings → Configurations, create or edit a config, confirm the "Named Connections" section appears with "+ Add Connection". Add a connection, confirm it shows the four fields. Save and reopen — confirm connections persist.

- [ ] **Step 7: Commit**

```
git add frontend/app.js frontend/index.html
git commit -m "feat: named connections section in config modal"
```

---

## Task 7: Frontend — Run launch connection pickers

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`

- [ ] **Step 1: Add `source_connection` and `target_connection` to `launchSettings` in `app.js`**

Find the `launchSettings` initializer (around line 110). Add two fields:

```javascript
source_connection: null,
target_connection: null,
```

- [ ] **Step 2: Add a helper to get available connections for the selected launch config**

Add this function near the other launch helpers in `app.js`:

```javascript
launchConfigConnections() {
  const cfg = this.configs.find(c => String(c.id) === String(this.launchSettings.config_id));
  if (!cfg || !cfg.config_data || !cfg.config_data.connections) return [];
  return Object.keys(cfg.config_data.connections);
},
```

- [ ] **Step 3: Include connection fields in the run payload in `runTests`**

In `runTests` (around line 1088), update the `api('POST', '/api/runs', {...})` call to include:

```javascript
const run = await api('POST', '/api/runs', {
  source_env: this.launchSettings.source_env,
  target_env: this.launchSettings.target_env,
  job_sequence: this._buildJobSequence(),
  config_id: cfg ? cfg.id : null,
  run_settings: this._runSettingsPayload(),
  config_data: cfg ? cfg.config_data : {},
  source_connection: this.launchSettings.source_connection || null,
  target_connection: this.launchSettings.target_connection || null,
});
```

- [ ] **Step 4: Reset connection selections when config changes**

Find where `launchSettings.config_id` is updated (the `<select>` in HTML uses `x-model="launchSettings.config_id"`). Add a watcher. In `app.js`, add a watch inside the Alpine component's `init` method (or wherever other watches are registered):

```javascript
this.$watch('launchSettings.config_id', () => {
  this.launchSettings.source_connection = null;
  this.launchSettings.target_connection = null;
});
```

- [ ] **Step 5: Add inline connection pickers to run launch form in `index.html`**

In `frontend/index.html`, find the Connection settings group (around line 485–514). After the "Saved Config" `<div>` (the third column in the 3-column grid), add a new row that appears only when the selected config has named connections:

```html
        <template x-if="launchConfigConnections().length > 0">
          <div class="grid grid-cols-2 gap-4 mt-3 p-3 bg-emerald-50 border border-emerald-200 rounded-lg">
            <div>
              <label class="field-label text-emerald-700">Source Connection</label>
              <select x-model="launchSettings.source_connection" class="field-input field-select">
                <option :value="null">— default —</option>
                <template x-for="name in launchConfigConnections()" :key="name">
                  <option :value="name" x-text="name"></option>
                </template>
              </select>
            </div>
            <div>
              <label class="field-label text-emerald-700">Target Connection</label>
              <select x-model="launchSettings.target_connection" class="field-input field-select">
                <option :value="null">— default —</option>
                <template x-for="name in launchConfigConnections()" :key="name">
                  <option :value="name" x-text="name"></option>
                </template>
              </select>
            </div>
          </div>
        </template>
```

Place this `<template>` immediately after the closing `</div>` of the 3-column grid (after line 513, before the closing `</div>` of the `settings-group`).

- [ ] **Step 6: Smoke-test**

Open the run launch form, select a config that has named connections — confirm the green connection picker panel appears. Select a config with no named connections — confirm the panel is hidden.

- [ ] **Step 7: Commit**

```
git add frontend/app.js frontend/index.html
git commit -m "feat: connection pickers in run launch form"
```

---

## Task 8: Frontend — SQL Compare connection pickers

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`

- [ ] **Step 1: Add `sqlConnectionA` and `sqlConnectionB` to state in `app.js`**

In the SQL compare state block (near `sqlConfigA`, `sqlConfigB`), add:

```javascript
sqlConnectionA: null,
sqlConnectionB: null,
```

- [ ] **Step 2: Add helpers for available connections per SQL compare config**

```javascript
sqlConfigAConnections() {
  const cfg = this.configs.find(c => String(c.id) === String(this.sqlConfigA));
  if (!cfg || !cfg.config_data || !cfg.config_data.connections) return [];
  return Object.keys(cfg.config_data.connections);
},

sqlConfigBConnections() {
  const cfg = this.configs.find(c => String(c.id) === String(this.sqlConfigB));
  if (!cfg || !cfg.config_data || !cfg.config_data.connections) return [];
  return Object.keys(cfg.config_data.connections);
},
```

- [ ] **Step 3: Reset connection selections when config changes**

In the `init` method, add watchers:

```javascript
this.$watch('sqlConfigA', () => { this.sqlConnectionA = null; });
this.$watch('sqlConfigB', () => { this.sqlConnectionB = null; });
```

- [ ] **Step 4: Include `connection_a`/`connection_b` in SQL compare payload**

In `runSQLComparison` (the function added in the previous feature), update the payload:

```javascript
const payload = {
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
};
```

- [ ] **Step 5: Add connection dropdowns to SQL compare form in `index.html`**

In the SQL compare panel (the `x-show="compareSubTab === 'sql'"` section), find the Source A card. After the config `<select>` and before the SQL textarea `<label>`, add:

```html
        <template x-if="sqlConfigAConnections().length > 0">
          <div class="mb-3">
            <label class="field-label">Connection</label>
            <select x-model="sqlConnectionA" class="field-input field-select">
              <option :value="null">— default —</option>
              <template x-for="name in sqlConfigAConnections()" :key="name">
                <option :value="name" x-text="name"></option>
              </template>
            </select>
          </div>
        </template>
```

Do the same for Source B using `sqlConfigBConnections()` and `sqlConnectionB`.

- [ ] **Step 6: Smoke-test**

Open Compare → SQL tab. Select a config with named connections — confirm a "Connection" dropdown appears below the config picker. Select `— default —` or a named connection, run a query pair, confirm the payload includes `connection_a`/`connection_b`.

- [ ] **Step 7: Commit**

```
git add frontend/app.js frontend/index.html
git commit -m "feat: connection pickers in SQL compare tab"
```

---

## Task 9: Full regression test

- [ ] **Step 1: Run all unit tests**

```
python -m pytest tests/unit/ -v
```

Expected: all pass.

- [ ] **Step 2: Confirm backward compatibility**

With the dev server running, open a config that has **no** named connections and launch a run — confirm it works exactly as before (no connection pickers appear, run succeeds).

- [ ] **Step 3: Confirm named connections end-to-end**

1. Edit a config, add two named connections (`hr_db`, `finance_db`).
2. Save the config.
3. Reopen the config — confirm both named connections appear with their values.
4. Launch a run with that config — confirm the green connection picker panel appears.
5. Select `hr_db` for source and `finance_db` for target.
6. Launch — confirm the run snapshot stored in the DB has the correct `source_credentials.db_host` and `target_credentials.db_host`.
