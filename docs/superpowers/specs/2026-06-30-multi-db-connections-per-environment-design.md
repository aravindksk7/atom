# Multi-DB Connections Per Environment

**Date:** 2026-06-30  
**Status:** Approved for implementation

## Problem

A saved config today holds a single set of DB credentials. When one environment (e.g. "Production") spans multiple SQL Server instances — an HR server, a Finance server, a Sales server — users must create one config record per database, multiplying config management overhead and losing the concept of a unified environment.

## Goal

Allow a single saved config to define any number of named DB connections. When launching a run (or SQL compare), the user picks which named connection to use for source and which for target. All jobs in that run share the selected pair. Configs without named connections continue to work exactly as before.

## Non-Goals

- Per-job connection selection (all jobs in a run share one source connection and one target connection)
- Non-DB connections (Automic, SAP BO) — those stay as single credentials on the config
- Migration of existing configs — old single-connection configs are unchanged

---

## Data Model

Named connections live under an optional `connections` key inside `config_json`. The existing top-level `db_*` fields remain the default connection.

```json
{
  "db_host": "default-server",
  "db_name": "default_db",
  "db_user": "sa",
  "db_password": "secret",
  "db_driver": "ODBC Driver 17 for SQL Server",
  "connections": {
    "hr_db": {
      "db_host": "hr-server",
      "db_name": "HR",
      "db_user": "hr_user",
      "db_password": "hr_secret"
    },
    "finance_db": {
      "db_host": "finance-server",
      "db_name": "FIN",
      "db_user": "fin_user",
      "db_password": "fin_secret"
    }
  }
}
```

Each named connection entry specifies only the fields that differ from the top-level defaults. Any unset field inherits from the parent. Connection names are arbitrary strings (keys of the dict); callers reference them by name.

---

## Backend

### `etl_framework/config/models.py`

Add `ConnectionEntry` — a Pydantic model with all `EnvironmentConfig` DB fields optional (used for partial overrides):

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
```

Add `resolve_connection(config_json: dict, name: str | None, env_name: str = "") -> EnvironmentConfig`:

- Always strip the `connections` key from `config_json` before passing to `EnvironmentConfig` (it doesn't accept unknown fields).
- If `name` is `None` or `name` is not a key in `config_json.get("connections", {})`, return `EnvironmentConfig(name=env_name, **top_level_fields)` — existing behavior.
- Otherwise, merge: start with top-level fields (minus `connections`), update with the named entry's non-`None` fields, set `EnvironmentConfig.name = f"{env_name}/{name}"` for clarity in logs.
- Raises `ValueError` if the merged result fails `EnvironmentConfig` validation (e.g. `db_host` missing from both parent and override).

### `api/routes/configs.py`

**`_mask(data)`** — recurse into `connections`:
```python
def _mask(data: dict) -> dict:
    result = {k: (_MASK if k in _SENSITIVE_KEYS and v is not None else v)
              for k, v in data.items() if k != "connections"}
    if "connections" in data and isinstance(data["connections"], dict):
        result["connections"] = {
            name: {k: (_MASK if k in _SENSITIVE_KEYS and v is not None else v) for k, v in entry.items()}
            for name, entry in data["connections"].items()
        }
    return result
```

**`_build_env(cfg, connection_name=None)`** — add optional arg, call `resolve_connection`.

**`POST /configs/validate`** — after validating the top-level config, validate each named connection entry by calling `resolve_connection` for each name and catching `ValueError`.

### `api/schemas.py`

Add to `RunTrigger`:
```python
source_connection: str | None = None
target_connection: str | None = None
```

Add to `SQLCompareRequest`:
```python
connection_a: str | None = None
connection_b: str | None = None
```

### `api/routes/runs.py`

When building the run snapshot, replace the bare credential copy with:
```python
snapshot["source_credentials"] = resolve_connection(
    cfg.config_json, body.source_connection
).model_dump()
snapshot["target_credentials"] = resolve_connection(
    cfg.config_json, body.target_connection
).model_dump()
```

If `body.source_connection` names a key that does not exist in `connections`, return HTTP 422 with message `"source_connection '{name}' not found in config connections"` and list the available names.

### `api/services/compare_service.py`

In `run_sql_comparison`, replace:
```python
env_a = EnvironmentConfig(name=cfg_a.env_name, **cfg_a.config_json)
env_b = EnvironmentConfig(name=cfg_b.env_name, **cfg_b.config_json)
```
with:
```python
env_a = resolve_connection(cfg_a.config_json, req.connection_a)
env_b = resolve_connection(cfg_b.config_json, req.connection_b)
```

---

## Frontend

### Config modal — Named Connections section

Below the existing default DB credential fields, add a "Named Connections" section:

- Header row: label "NAMED CONNECTIONS" + small description + "**+ Add Connection**" button.
- Each named connection is a collapsible card:
  - **Header**: editable name input (monospace), host/db summary, expand/collapse toggle (▲/▼), remove button (✕).
  - **Expanded body**: four fields in a 2-column grid — DB Host, DB Name, DB User, DB Password.
  - Collapsed by default after the first expand.
- "Add Connection" creates a new blank card, expanded, with a placeholder name like `connection_1`.
- State stored in `configModal.connections` — an array of `{ name, db_host, db_name, db_user, db_password }` objects.
- On save, serialize to `{ [name]: { db_host, db_name, db_user, db_password } }` and include under the `connections` key in `config_data`.

### Run launch form — Connection pickers

When a config is selected and its `config_data.connections` is non-empty, show two dropdowns immediately below the config selector, side by side in a highlighted panel:

```
Source Connection   [hr_db ▾]
Target Connection   [hr_db ▾]
```

Options: one per named connection, plus `— default —` (value `null`). Hidden entirely when the config has no named connections.

The selected values are sent as `source_connection` and `target_connection` in the run trigger payload.

### SQL Compare tab — per-side connection picker

Below each config selector in the SQL compare form, show a connection dropdown when the selected config has named connections. Sends as `connection_a` / `connection_b` in the SQL compare payload.

---

## Error Handling

| Scenario | Response |
|---|---|
| `source_connection` names a key not in `connections` | HTTP 422, lists available names |
| `target_connection` names a key not in `connections` | HTTP 422, lists available names |
| Named connection provided but config has no `connections` | HTTP 422 |
| Merged connection fails `EnvironmentConfig` validation | HTTP 422 with field-level detail |
| Existing configs with no `connections` key | Unchanged — `resolve_connection(config_json, None)` returns current behavior |

---

## Backward Compatibility

- All existing `SavedConfig` records continue to work unchanged.
- `resolve_connection` with `name=None` is identical to the current `EnvironmentConfig(name=..., **config_json)` call.
- Run snapshots already store fully-resolved credentials, so historical runs are unaffected.
- YAML import: the `connections` key in a YAML env block passes through to `config_json` unchanged.

---

## Files Changed

| File | Change |
|---|---|
| `etl_framework/config/models.py` | Add `ConnectionEntry`, `resolve_connection()` |
| `api/routes/configs.py` | Recurse `_mask` into connections; add `connection_name` arg to `_build_env`; validate named entries in `/validate` |
| `api/schemas.py` | Add `source_connection`/`target_connection` to `RunTrigger`; add `connection_a`/`connection_b` to `SQLCompareRequest` |
| `api/routes/runs.py` | Use `resolve_connection` when building snapshot |
| `api/services/compare_service.py` | Use `resolve_connection` in `run_sql_comparison` |
| `frontend/app.js` | Named connections state in `configModal`; reactive connection dropdowns; serialization on save |
| `frontend/index.html` | Connections section in config modal; inline pickers in run launch and SQL compare forms |
