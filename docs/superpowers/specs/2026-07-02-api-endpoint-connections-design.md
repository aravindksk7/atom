# API Endpoint Connections (Configuration, Jobs, Comparison)

**Date:** 2026-07-02
**Status:** Approved for implementation

## Problem

Configurations today only model DB, Automic, and SAP BO connections. There is no way to point a job or a comparison at a REST API — teams reconciling data that only exists behind an HTTP endpoint (a microservice, a partner API, an internal reporting API) have to manually download a response and upload it as a file.

## Goal

Let a saved config define any number of named REST API endpoints (URL, auth, headers, response parsing, pagination). Those endpoints can then be used:

- As either side of a Compare-tab comparison (alongside the existing Live BO / Path / Upload sources).
- As the source and/or target of a new `api_reconciliation` job type, run through the standard reconciliation pipeline (key columns, DQ rules, pass conditions, mismatch reporting).

## Non-Goals

- Health-check/ping-only usage (out of scope per user decision — API endpoints are tabular data sources).
- Mixing one DB side with one API side within the existing `reconciliation` job type — `api_reconciliation` is a separate, self-contained job type instead.
- OAuth2 client-credentials or other advanced auth flows — only `none` / `api_key` header / `bearer` / `basic`.
- Database schema changes — `SavedConfig.config_json` is already a free-form JSON blob.

---

## Data Model

Named API endpoints live under an optional `api_endpoints` key inside `config_json`, parallel to the existing `connections` key (multi-DB-connections feature). Each entry is independent and self-contained (unlike `connections`, endpoints do not inherit from top-level fields — there's no meaningful "default" API endpoint).

```json
{
  "db_host": "...",
  "connections": { "hr_db": { "...": "..." } },
  "api_endpoints": {
    "orders_api": {
      "base_url": "https://api.example.com/v1/orders",
      "method": "GET",
      "auth_type": "bearer",
      "bearer_token": "secret-token",
      "headers": { "Accept": "application/json" },
      "query_params": { "status": "active" },
      "body": null,
      "timeout": 30,
      "verify_ssl": true,
      "response_format": "json",
      "json_root_path": "data.items",
      "pagination_type": "cursor",
      "pagination_cursor_path": "meta.next_cursor",
      "pagination_cursor_param": "cursor",
      "pagination_page_param": "page",
      "pagination_size_param": "limit",
      "pagination_page_size": 100,
      "pagination_max_pages": 50
    }
  }
}
```

### `etl_framework/config/models.py`

Add `ApiEndpointEntry`:

```python
class ApiEndpointEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = ""
    base_url: str
    method: Literal["GET", "POST"] = "GET"

    auth_type: Literal["none", "api_key", "bearer", "basic"] = "none"
    api_key_header: str = "X-API-Key"
    api_key: str = ""
    bearer_token: str = ""
    basic_username: str = ""
    basic_password: str = ""

    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | None = None

    timeout: int = 30
    verify_ssl: bool = True

    response_format: Literal["json", "csv"] = "json"
    json_root_path: str = ""

    pagination_type: Literal["none", "cursor", "page"] = "none"
    pagination_cursor_path: str = ""
    pagination_cursor_param: str = "cursor"
    pagination_page_param: str = "page"
    pagination_size_param: str = "limit"
    pagination_page_size: int = 100
    pagination_max_pages: int = Field(default=50, ge=1, le=1000)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        from urllib.parse import urlparse
        if not urlparse(v).scheme:
            raise ValueError("base_url must include http:// or https://")
        return v

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"must be > 0, got {v}")
        return v
```

Add `resolve_api_endpoint(config_json: dict, name: str) -> ApiEndpointEntry`:

```python
def resolve_api_endpoint(config_json: dict, name: str) -> ApiEndpointEntry:
    endpoints = config_json.get("api_endpoints") or {}
    if name not in endpoints:
        raise ValueError(f"api_endpoints entry '{name}' not found in config")
    return ApiEndpointEntry(name=name, **endpoints[name])
```

Raises `ValidationError`/`ValueError` on missing/malformed entries — callers convert to HTTP 422, same convention as `resolve_connection`.

---

## Backend

### `etl_framework/rest_api/client.py` (new package)

`APIEndpointClient(entry: ApiEndpointEntry)`:

- `_build_auth_headers() -> dict[str, str]` — builds headers/auth kwargs from `auth_type` (API key → custom header, bearer → `Authorization: Bearer <token>`, basic → `requests` `auth=(user, pass)` tuple, none → nothing).
- `fetch_dataframe(overrides: dict | None = None) -> pd.DataFrame`:
  - Issues the first request (`GET`/`POST`, with `headers`, `query_params`, `body`, `timeout`, `verify_ssl`).
  - Parses the page: JSON → walk `json_root_path` (dot-separated, e.g. `"data.items"`; empty path means the response body itself is the record array) → `pd.DataFrame(records)`. CSV → `pd.read_csv(io.StringIO(response.text))`, reusing the same parsing tolerance as `file_source.read_tabular`.
  - If `pagination_type == "cursor"`: read the cursor value at `pagination_cursor_path` from the response; if present, either follow it directly (if it's a full URL) or set it as `pagination_cursor_param` on the next request; repeat until absent or `pagination_max_pages` reached.
  - If `pagination_type == "page"`: increment `pagination_page_param` starting at 1 (page size fixed via `pagination_size_param`/`pagination_page_size`); stop when a page returns fewer than `pagination_page_size` records or `pagination_max_pages` is reached.
  - Concatenates all pages into one `DataFrame` (`pd.concat(..., ignore_index=True)`).
  - Raises a framework exception (new `APIRequestError` in `etl_framework/exceptions.py`, following `BOAPIError`'s shape) on non-2xx responses, timeouts, or unparsable bodies.

### `api/services/adapter_service.py` / `api/routes/adapters.py`

New endpoints, mirroring the SAP BO adapter pattern:

- `POST /api/adapters/rest-api/test` — body `{config_id, endpoint_name}` → `AdapterTestOut` (issues the configured request with `pagination_type` forced to `"none"` and measures latency/status).
- `POST /api/adapters/rest-api/preview` — body `{config_id, endpoint_name, limit}` → `{columns, rows}` (calls `fetch_dataframe()`, truncates to `limit` rows client-side after fetch — same shape as `GET /configs/{id}/preview-query`).

### `api/schemas.py`

```python
class RestApiTestRequest(BaseModel):
    config_id: int
    endpoint_name: str

class RestApiPreviewRequest(BaseModel):
    config_id: int
    endpoint_name: str
    limit: int = 50
```

Add `"api_reconciliation"` to `JobDefinition.job_type` Literal. Extend `validate_reconciliation_contract`:

```python
elif self.job_type == "api_reconciliation":
    if not self.params.get("source_api_endpoint") or not self.params.get("target_api_endpoint"):
        raise ValueError("api_reconciliation jobs require 'source_api_endpoint' and 'target_api_endpoint' in params")
    if not self.key_columns:
        raise ValueError("api_reconciliation jobs require key_columns")
```

Extend `SourceConfig`:

```python
source_type: Literal["live", "path", "upload", "api"]
api_endpoint_name: str | None = None
```

```python
if self.source_type == "api" and (self.config_id is None or not self.api_endpoint_name):
    raise ValueError("config_id and api_endpoint_name required for api source")
```

### `api/routes/configs.py`

- Add `api_key`, `bearer_token`, `basic_password` to `_SENSITIVE_KEYS`.
- Extend `_mask` to recurse into `api_endpoints` the same way it already recurses into `connections` (mask any `_SENSITIVE_KEYS` field per entry).
- Extend `_preserve_masked_secrets` symmetrically, so an update payload that echoes the mask back for an endpoint's secret field restores the stored value instead of overwriting it with `********`.
- Extend `POST /configs/validate`: after validating top-level fields and named connections, validate each `api_endpoints` entry via `ApiEndpointEntry.model_validate`, collecting per-entry errors the same way connection errors are collected.

### `api/services/compare_service.py`

In `_load_bo_source` (already dispatches on `source_type`), add:

```python
if src.source_type == "api":
    return self._load_api_source(src)
```

```python
def _load_api_source(self, src: "SourceConfig") -> pd.DataFrame:
    cfg = self._config_repo.get(src.config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")
    from etl_framework.config.models import resolve_api_endpoint
    from etl_framework.rest_api.client import APIEndpointClient
    try:
        entry = resolve_api_endpoint(cfg.config_json or {}, src.api_endpoint_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return APIEndpointClient(entry).fetch_dataframe()
```

Used identically by `run_bo_comparison` and `run_column_stats` (both already call `_load_bo_source` for each side), so both flows gain API sources with no further changes.

### `api/services/run_executor.py`

Add `"api_reconciliation"` to the `_build_case` dispatch (gated on `use_live_connections`, same as `bo_report`/`automic_job`):

```python
if job.job_type == "api_reconciliation" and self._settings.use_live_connections:
    return self._build_case_api_reconciliation(job)
```

```python
def _build_case_api_reconciliation(self, job: JobDefinition):
    def run_job() -> ReconciliationResult:
        from etl_framework.config.models import resolve_api_endpoint
        from etl_framework.rest_api.client import APIEndpointClient

        api_endpoints = self._config_snapshot.get("api_endpoints") or {}
        src_entry = resolve_api_endpoint({"api_endpoints": api_endpoints}, job.params["source_api_endpoint"])
        tgt_entry = resolve_api_endpoint({"api_endpoints": api_endpoints}, job.params["target_api_endpoint"])

        df_a = APIEndpointClient(src_entry).fetch_dataframe()
        df_b = APIEndpointClient(tgt_entry).fetch_dataframe()

        reconciler = ReconciliationEngine(
            source_engine=_FrameEngine(df_a, self._source_env),
            target_engine=_FrameEngine(df_b, self._target_env),
            key_columns=job.key_columns,
            exclude_columns=job.exclude_columns,
            float_tolerance=self._settings.float_tolerance,
            mismatch_row_limit=self._settings.mismatch_row_limit,
            backend=self._build_backend(job),
        )
        return reconciler.reconcile(query="__api_source__", query_name=job.name)
    return run_job
```

(`_FrameEngine` currently lives in `api/services/compare_service.py`; it moves to a small shared module — e.g. `api/services/frame_engine.py` — imported by both `compare_service.py` and `run_executor.py`, rather than duplicating the class.)

No changes needed to `api/routes/runs.py` — `_snapshot_from_trigger` already copies the full merged `config_json` (including `api_endpoints`) into `config_snapshot` today.

---

## Frontend (`frontend/app.js` + `frontend/index.html`)

### Config modal — API Endpoints section

Parallel to the existing "Named Connections" cards:

- Header row: "API ENDPOINTS" + "**+ Add Endpoint**".
- Each endpoint is a collapsible card:
  - **Header**: editable name, base_url summary, method badge, expand/collapse, remove (✕).
  - **Expanded body**:
    - Base URL, Method (GET/POST)
    - Auth Type dropdown; conditionally reveals API Key Header + API Key, or Bearer Token, or Basic Username + Password
    - Headers editor (key/value rows, add/remove)
    - Query Params editor (key/value rows, add/remove)
    - Body (JSON textarea, shown only when Method = POST)
    - Timeout, Verify SSL checkbox
    - Response Format (JSON/CSV); JSON Root Path text field (shown when JSON)
    - Pagination Type dropdown; conditionally reveals cursor fields (cursor path, cursor param) or page fields (page param, size param, page size), plus Max Pages always shown when pagination is not "none"
  - "Test" button → `POST /adapters/rest-api/test`; "Preview" button → `POST /adapters/rest-api/preview`, rendering a small sample table.
- State: `configModal.apiEndpoints` — array of entry objects, serialized to `{ [name]: {...} }` under `api_endpoints` on save, same pattern as `configModal.connections`.

### Job modal — api_reconciliation fields

When `job_type === 'api_reconciliation'` is selected:

- Two dropdowns, "Source API Endpoint" / "Target API Endpoint", populated from the selected config's `api_endpoints` (falls back to a text input if no config is selected yet, same pattern used elsewhere for env-dependent dropdowns).
- Reuse the existing `key_columns` / `exclude_columns` fields (already shared by the `reconciliation` and `bo_report` branches).
- Extend the job-modal validity check (`app.js` ~line 1215-1224):
  ```js
  if (m.job_type === 'api_reconciliation') return Boolean(m.source_api_endpoint && m.target_api_endpoint && m.key_columns?.length);
  ```

### Compare tab — API source option

The existing per-side source-type selector (`live` / `path` / `upload`) gains `api`:

- Selecting "API" reveals a config dropdown (reuses the config picker already used for "Live" BO source) and, once a config is chosen, an endpoint dropdown populated from that config's `api_endpoints`.
- `_toSourceConfig`-style serialization (`app.js` ~line 1920) gains:
  ```js
  if (type === 'api') return { source_type: 'api', config_id: src.configId, api_endpoint_name: src.endpointName };
  ```

---

## Error Handling

| Scenario | Response |
|---|---|
| `api_endpoint_name` not found in config's `api_endpoints` | HTTP 404, `"api_endpoints entry '{name}' not found in config"` |
| `base_url` missing scheme | HTTP 422 (Pydantic validation error) at config save/validate time |
| API request fails (non-2xx, timeout, connection error) | HTTP 400/422 with the upstream status/message, same convention as `download_bo_report` failures |
| Response body doesn't match `response_format` (unparsable JSON/CSV) | HTTP 422, `"Cannot parse API response as {format}"` |
| `json_root_path` doesn't resolve to a list | HTTP 422, `"json_root_path '{path}' did not resolve to a list of records"` |
| Pagination exceeds `pagination_max_pages` | Stops silently and returns what was fetched (not an error — same "soft cap" behavior as other bounded loops in the codebase) |
| `api_reconciliation` job without `use_live_connections` | Falls back to the existing simulated `DataFrameQueryEngine` path (same as DB-backed `reconciliation` jobs today) |

---

## Backward Compatibility

- Existing `SavedConfig` records without `api_endpoints` are unaffected — the key is optional everywhere it's read.
- `SourceConfig.source_type` gains a new literal value; existing `"live"`/`"path"`/`"upload"` requests are unchanged.
- `JobDefinition.job_type` gains a new literal value; existing job types and their validation are unchanged.
- YAML config import: an `api_endpoints` block in a YAML env passes through to `config_json` unchanged, same as `connections` does today (no `ConfigLoader` changes needed since it stores the raw dict).

---

## Files Changed

| File | Change |
|---|---|
| `etl_framework/config/models.py` | Add `ApiEndpointEntry`, `resolve_api_endpoint()` |
| `etl_framework/rest_api/client.py` (new) | `APIEndpointClient` — auth, fetch, pagination, JSON/CSV parsing |
| `etl_framework/rest_api/__init__.py` (new) | Package init |
| `etl_framework/exceptions.py` | Add `APIRequestError` |
| `api/schemas.py` | Add `RestApiTestRequest`, `RestApiPreviewRequest`; extend `SourceConfig`, `JobDefinition.job_type` |
| `api/routes/configs.py` | Extend `_SENSITIVE_KEYS`, `_mask`, `_preserve_masked_secrets`, `/validate` |
| `api/routes/adapters.py` | Add `/rest-api/test`, `/rest-api/preview` |
| `api/services/adapter_service.py` | Add `test_api_endpoint`, `preview_api_endpoint` |
| `api/services/compare_service.py` | Add `_load_api_source`, wire into `_load_bo_source` dispatch |
| `api/services/frame_engine.py` (new) | `_FrameEngine`, extracted from `compare_service.py` for reuse in `run_executor.py` |
| `api/services/run_executor.py` | Add `_build_case_api_reconciliation`, wire into `_build_case` |
| `frontend/app.js` | API Endpoints config-modal state/serialization; job-modal fields for `api_reconciliation`; Compare-tab `api` source type |
| `frontend/index.html` | API Endpoints section markup; job modal fields; Compare tab source-type option |
| `etl_framework/rest_api/test_client.py` (new) | Unit tests for `APIEndpointClient` |
| `tests/unit/test_api.py` | `SourceConfig`/`JobDefinition` validation tests for the new fields |
| `tests/unit/test_tabular_file_compare.py` | `CompareService._load_api_source` tests |
