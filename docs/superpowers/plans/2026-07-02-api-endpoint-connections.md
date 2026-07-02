# API Endpoint Connections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a saved config define named REST API endpoints (auth, headers, pagination, JSON/CSV parsing), then use those endpoints as either side of a Compare-tab comparison and as the source/target of a new `api_reconciliation` job type.

**Architecture:** A new `ApiEndpointEntry` Pydantic model stored under `config_json["api_endpoints"]` (no DB migration — that column is already free-form JSON). A new `APIEndpointClient` (in a new `etl_framework/rest_api` package) fetches and parses paginated JSON/CSV responses into a `DataFrame`. That client is wired into three existing extension points: `CompareService._load_bo_source`'s source-type dispatch, `RunExecutor._build_case`'s job-type dispatch, and `configs.py`'s secret-masking helpers.

**Tech Stack:** FastAPI, Pydantic v2, `requests`, `pandas`, pytest, Alpine.js (frontend, no test harness — manual browser verification).

**Reference spec:** `docs/superpowers/specs/2026-07-02-api-endpoint-connections-design.md`

**Deviations from the spec you should know about before starting:**
- `fetch_dataframe(overrides: dict | None = None)` in the spec is implemented as `fetch_dataframe(max_pages: int | None = None)` — a concrete need identified while planning the "test connection" endpoint (which should only fetch one page, not follow full pagination).
- The moved `_FrameEngine` class is renamed to `FrameEngine` (no leading underscore) when it moves into its own shared module, since a leading underscore signals module-private and it's no longer private once two modules import it.
- The job-modal fields for `api_reconciliation` (Section 5 of the spec) are plain text inputs, not dropdowns populated from "the selected config's `api_endpoints`" — there is no config-selection concept in the job modal today (jobs are config-agnostic; credentials come from whichever config is picked at Run-launch time). This matches how `bo_report`'s `bo_report_id`/`bo_page_id` fields already work as plain text, not config-scoped dropdowns.
- The Compare tab has *two* independent source pickers that both use `SourceConfig` under the hood: the BO/recon tab (`boSourceAType`/`boSourceBType`, pill buttons) and the Column Stats tab (`colStatsSourceAType`/`colStatsSourceBType`, `<select>` dropdown). Both share the same `_buildBOSource()` payload builder, so one task (18) updates the builder and both tabs' markup gets `api` options (Tasks 18 and 19).
- **Course correction after Task 1's code-quality review:** `pyproject.toml` sets `testpaths = ["tests"]` and CI runs `pytest tests/`, so tests co-located under `etl_framework/**/test_*.py` are invisible to CI even though they pass when invoked by direct file path. Task 1's test file was moved from `tests/unit/test_resolve_api_endpoint.py` to `tests/unit/test_resolve_api_endpoint.py` to match the existing `tests/unit/test_resolve_connection.py` convention for that same module. **Tasks 3-6 below have been updated accordingly**: the `APIEndpointClient` tests live in `tests/unit/test_rest_api_client.py`, not `tests/unit/test_rest_api_client.py`.

---

## File Structure

| File | Responsibility |
|---|---|
| `etl_framework/config/models.py` | Add `ApiEndpointEntry`, `resolve_api_endpoint()` |
| `tests/unit/test_resolve_api_endpoint.py` (new) | Tests for the above |
| `etl_framework/exceptions.py` | Add `APIRequestError` |
| `etl_framework/rest_api/__init__.py` (new) | Package init (empty) |
| `etl_framework/rest_api/client.py` (new) | `APIEndpointClient` — auth, fetch, pagination, JSON/CSV parsing |
| `tests/unit/test_rest_api_client.py` (new) | Tests for `APIEndpointClient` |
| `api/schemas.py` | Extend `SourceConfig`, `JobDefinition.job_type`; add `RestApiTestRequest`, `RestApiPreviewRequest` |
| `api/routes/configs.py` | Extend `_SENSITIVE_KEYS`, `_mask`, `_preserve_masked_secrets`, `/validate` |
| `api/services/adapter_service.py` | Add `test_api_endpoint`, `preview_api_endpoint` |
| `api/routes/adapters.py` | Add `POST /rest-api/test`, `POST /rest-api/preview` |
| `api/services/frame_engine.py` (new) | `FrameEngine`, extracted from `compare_service.py` |
| `api/services/compare_service.py` | Add `_load_api_source`, wire into `_load_bo_source`; import `FrameEngine` |
| `api/services/run_executor.py` | Add `_build_case_api_reconciliation`, wire into `_build_case`; import `FrameEngine` |
| `tests/unit/test_api.py` | `SourceConfig`/`JobDefinition`/config-masking/adapter-route tests |
| `tests/unit/test_tabular_file_compare.py` | `CompareService._load_api_source` tests |
| `tests/unit/test_run_executor_api_reconciliation.py` (new) | `RunExecutor._build_case_api_reconciliation` tests |
| `frontend/app.js` / `frontend/index.html` | Config modal API Endpoints section; job modal `api_reconciliation` fields; Compare-tab `api` source type (both sub-tabs) |

---

## Task 1: Config model — `ApiEndpointEntry` + `resolve_api_endpoint`

**Files:**
- Modify: `etl_framework/config/models.py`
- Test: `tests/unit/test_resolve_api_endpoint.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_resolve_api_endpoint.py`:

```python
import pytest
from pydantic import ValidationError

from etl_framework.config.models import ApiEndpointEntry, resolve_api_endpoint


def test_api_endpoint_entry_defaults():
    entry = ApiEndpointEntry(base_url="https://api.example.com/orders")
    assert entry.method == "GET"
    assert entry.auth_type == "none"
    assert entry.response_format == "json"
    assert entry.pagination_type == "none"
    assert entry.pagination_max_pages == 50
    assert entry.timeout == 30
    assert entry.verify_ssl is True


def test_api_endpoint_entry_requires_url_scheme():
    with pytest.raises(ValidationError):
        ApiEndpointEntry(base_url="api.example.com/orders")


def test_api_endpoint_entry_rejects_non_positive_timeout():
    with pytest.raises(ValidationError):
        ApiEndpointEntry(base_url="https://api.example.com", timeout=0)


def test_api_endpoint_entry_rejects_max_pages_out_of_range():
    with pytest.raises(ValidationError):
        ApiEndpointEntry(base_url="https://api.example.com", pagination_max_pages=0)


def test_resolve_api_endpoint_returns_entry_with_name():
    config_json = {
        "api_endpoints": {
            "orders": {"base_url": "https://api.example.com/orders", "method": "GET"}
        }
    }
    entry = resolve_api_endpoint(config_json, "orders")
    assert entry.name == "orders"
    assert entry.base_url == "https://api.example.com/orders"


def test_resolve_api_endpoint_raises_for_missing_name():
    with pytest.raises(ValueError, match="not found"):
        resolve_api_endpoint({"api_endpoints": {}}, "missing")


def test_resolve_api_endpoint_raises_when_no_api_endpoints_key():
    with pytest.raises(ValueError, match="not found"):
        resolve_api_endpoint({}, "orders")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_resolve_api_endpoint.py -v`
Expected: FAIL with `ImportError: cannot import name 'ApiEndpointEntry'`

- [ ] **Step 3: Implement `ApiEndpointEntry` and `resolve_api_endpoint`**

In `etl_framework/config/models.py`, change the top imports from:

```python
from __future__ import annotations

from pydantic import BaseModel, field_validator, ConfigDict
```

to:

```python
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, ConfigDict
```

Then append at the end of the file (after `resolve_connection`):

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


def resolve_api_endpoint(config_json: dict, name: str) -> ApiEndpointEntry:
    """Return the named API endpoint entry from a config's JSON blob."""
    endpoints = config_json.get("api_endpoints") or {}
    if name not in endpoints:
        raise ValueError(f"api_endpoints entry '{name}' not found in config")
    return ApiEndpointEntry(name=name, **endpoints[name])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_resolve_api_endpoint.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add etl_framework/config/models.py tests/unit/test_resolve_api_endpoint.py
git commit -m "feat: add ApiEndpointEntry config model and resolve_api_endpoint helper"
```

---

## Task 2: `APIRequestError` exception

**Files:**
- Modify: `etl_framework/exceptions.py`

- [ ] **Step 1: Add the exception class**

Append to `etl_framework/exceptions.py`:

```python
class APIRequestError(ETLFrameworkError):
    def __init__(self, url: str, http_status: int | None, message: str) -> None:
        self.url = url
        self.http_status = http_status
        self.message = message
        status_part = f" (status {http_status})" if http_status is not None else ""
        super().__init__(f"API request to '{url}' failed{status_part}: {message}")
```

There's no dedicated test file for `exceptions.py` in this codebase (existing exceptions are exercised indirectly through the modules that raise them) — `APIRequestError` will be covered by Task 3-6's client tests.

- [ ] **Step 2: Verify it imports cleanly**

Run: `python -c "from etl_framework.exceptions import APIRequestError; print(APIRequestError('https://x', 500, 'boom'))"`
Expected: `API request to 'https://x' failed (status 500): boom`

- [ ] **Step 3: Commit**

```bash
git add etl_framework/exceptions.py
git commit -m "feat: add APIRequestError exception"
```

---

## Task 3: REST API client — auth + basic JSON fetch (no pagination)

**Files:**
- Create: `etl_framework/rest_api/__init__.py`
- Create: `etl_framework/rest_api/client.py`
- Test: `tests/unit/test_rest_api_client.py` (new)

- [ ] **Step 1: Create the empty package init**

Create `etl_framework/rest_api/__init__.py` with empty content.

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_rest_api_client.py`:

```python
from __future__ import annotations
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from etl_framework.config.models import ApiEndpointEntry
from etl_framework.exceptions import APIRequestError
from etl_framework.rest_api.client import APIEndpointClient


def _entry(**overrides) -> ApiEndpointEntry:
    base = {"base_url": "https://api.example.com/v1/orders"}
    base.update(overrides)
    return ApiEndpointEntry(**base)


def _fake_response(status_code=200, json_data=None, text="", url="https://api.example.com/v1/orders"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.url = url
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


def test_fetch_dataframe_parses_json_with_root_path():
    entry = _entry(json_root_path="data.items")
    client = APIEndpointClient(entry)
    payload = {"data": {"items": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]}}
    with patch.object(client._session, "request", return_value=_fake_response(json_data=payload)):
        df = client.fetch_dataframe()
    assert list(df["id"]) == [1, 2]


def test_fetch_dataframe_parses_json_without_root_path():
    entry = _entry(json_root_path="")
    client = APIEndpointClient(entry)
    payload = [{"id": 1}, {"id": 2}]
    with patch.object(client._session, "request", return_value=_fake_response(json_data=payload)):
        df = client.fetch_dataframe()
    assert len(df) == 2


def test_fetch_dataframe_raises_on_error_status():
    entry = _entry()
    client = APIEndpointClient(entry)
    with patch.object(client._session, "request", return_value=_fake_response(status_code=500, text="boom")):
        with pytest.raises(APIRequestError):
            client.fetch_dataframe()


def test_fetch_dataframe_bearer_auth_header():
    entry = _entry(auth_type="bearer", bearer_token="tok123")
    client = APIEndpointClient(entry)
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return _fake_response(json_data=[{"id": 1}])

    with patch.object(client._session, "request", side_effect=fake_request):
        client.fetch_dataframe()
    assert captured["headers"]["Authorization"] == "Bearer tok123"


def test_fetch_dataframe_basic_auth():
    entry = _entry(auth_type="basic", basic_username="user", basic_password="pw")
    client = APIEndpointClient(entry)
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return _fake_response(json_data=[{"id": 1}])

    with patch.object(client._session, "request", side_effect=fake_request):
        client.fetch_dataframe()
    assert captured["auth"] == ("user", "pw")


def test_fetch_dataframe_api_key_header():
    entry = _entry(auth_type="api_key", api_key_header="X-API-Key", api_key="k1")
    client = APIEndpointClient(entry)
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return _fake_response(json_data=[{"id": 1}])

    with patch.object(client._session, "request", side_effect=fake_request):
        client.fetch_dataframe()
    assert captured["headers"]["X-API-Key"] == "k1"


def test_fetch_dataframe_no_auth_sends_no_auth_tuple():
    entry = _entry(auth_type="none")
    client = APIEndpointClient(entry)
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return _fake_response(json_data=[{"id": 1}])

    with patch.object(client._session, "request", side_effect=fake_request):
        client.fetch_dataframe()
    assert captured["auth"] is None
    assert "Authorization" not in captured["headers"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_rest_api_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'etl_framework.rest_api.client'`

- [ ] **Step 4: Implement the client (auth + single-page JSON fetch)**

Create `etl_framework/rest_api/client.py`:

```python
from __future__ import annotations

import io
import logging

import pandas as pd
import requests

from etl_framework.config.models import ApiEndpointEntry
from etl_framework.exceptions import APIRequestError

logger = logging.getLogger("etl_framework.rest_api.client")


class APIEndpointClient:
    def __init__(self, entry: ApiEndpointEntry) -> None:
        self._entry = entry
        self._session = requests.Session()

    def fetch_dataframe(self, max_pages: int | None = None) -> pd.DataFrame:
        entry = self._entry
        response = self._request(entry.base_url, dict(entry.query_params))
        return self._parse_response(response)

    def _auth_kwargs(self) -> dict:
        entry = self._entry
        headers = dict(entry.headers)
        auth = None
        if entry.auth_type == "api_key":
            headers[entry.api_key_header] = entry.api_key
        elif entry.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {entry.bearer_token}"
        elif entry.auth_type == "basic":
            auth = (entry.basic_username, entry.basic_password)
        return {"headers": headers, "auth": auth}

    def _request(self, url: str, query_params: dict) -> requests.Response:
        entry = self._entry
        kwargs = self._auth_kwargs()
        try:
            response = self._session.request(
                entry.method,
                url,
                params=query_params,
                json=entry.body if entry.method == "POST" else None,
                timeout=entry.timeout,
                verify=entry.verify_ssl,
                **kwargs,
            )
        except requests.exceptions.RequestException as exc:
            raise APIRequestError(url=url, http_status=None, message=str(exc)) from exc
        if response.status_code >= 400:
            raise APIRequestError(url=url, http_status=response.status_code, message=response.text)
        return response

    def _parse_response(self, response: requests.Response) -> pd.DataFrame:
        entry = self._entry
        if entry.response_format == "csv":
            try:
                return pd.read_csv(io.StringIO(response.text))
            except Exception as exc:
                raise APIRequestError(
                    url=response.url, http_status=response.status_code,
                    message=f"Cannot parse API response as csv: {exc}",
                ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise APIRequestError(
                url=response.url, http_status=response.status_code,
                message="Cannot parse API response as json",
            ) from exc
        records = self._walk_json_path(payload, entry.json_root_path, response.url)
        if not isinstance(records, list):
            raise APIRequestError(
                url=response.url, http_status=response.status_code,
                message=f"json_root_path '{entry.json_root_path}' did not resolve to a list of records",
            )
        return pd.json_normalize(records) if records else pd.DataFrame()

    @staticmethod
    def _walk_json_path(payload, path: str, url: str):
        if not path:
            return payload
        current = payload
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise APIRequestError(
                    url=url, http_status=None,
                    message=f"json_root_path '{path}' did not resolve to a list of records",
                )
        return current
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_rest_api_client.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add etl_framework/rest_api/__init__.py etl_framework/rest_api/client.py tests/unit/test_rest_api_client.py
git commit -m "feat: add APIEndpointClient with auth handling and single-page JSON fetch"
```

---

## Task 4: REST API client — CSV parsing + `json_root_path` edge cases

**Files:**
- Modify: `tests/unit/test_rest_api_client.py`

(No production code changes — Task 3's implementation already handles these cases. This task locks the behavior in with tests.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_rest_api_client.py`:

```python
def test_fetch_dataframe_parses_csv():
    entry = _entry(response_format="csv")
    client = APIEndpointClient(entry)
    with patch.object(client._session, "request", return_value=_fake_response(text="id,name\n1,a\n2,b\n")):
        df = client.fetch_dataframe()
    assert list(df.columns) == ["id", "name"]
    assert len(df) == 2


def test_fetch_dataframe_raises_when_root_path_missing():
    entry = _entry(json_root_path="missing.path")
    client = APIEndpointClient(entry)
    with patch.object(client._session, "request", return_value=_fake_response(json_data={"data": []})):
        with pytest.raises(APIRequestError, match="did not resolve to a list"):
            client.fetch_dataframe()


def test_fetch_dataframe_raises_when_root_path_resolves_to_non_list():
    entry = _entry(json_root_path="data")
    client = APIEndpointClient(entry)
    with patch.object(client._session, "request", return_value=_fake_response(json_data={"data": {"not": "a list"}})):
        with pytest.raises(APIRequestError, match="did not resolve to a list"):
            client.fetch_dataframe()


def test_fetch_dataframe_empty_json_list_returns_empty_dataframe():
    entry = _entry(json_root_path="items")
    client = APIEndpointClient(entry)
    with patch.object(client._session, "request", return_value=_fake_response(json_data={"items": []})):
        df = client.fetch_dataframe()
    assert df.empty


def test_fetch_dataframe_raises_on_unparsable_json():
    entry = _entry()
    client = APIEndpointClient(entry)
    with patch.object(client._session, "request", return_value=_fake_response(text="not json")):
        with pytest.raises(APIRequestError, match="Cannot parse API response as json"):
            client.fetch_dataframe()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_rest_api_client.py -v`
Expected: 12 passed (all Task 3 + Task 4 tests — this confirms Task 3's implementation already handles these edge cases correctly)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_rest_api_client.py
git commit -m "test: cover CSV parsing and json_root_path edge cases in APIEndpointClient"
```

---

## Task 5: REST API client — page pagination

**Files:**
- Modify: `etl_framework/rest_api/client.py`
- Modify: `tests/unit/test_rest_api_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_rest_api_client.py`:

```python
def test_fetch_dataframe_page_pagination_stops_on_short_page():
    entry = _entry(pagination_type="page", pagination_page_size=2, pagination_max_pages=10)
    client = APIEndpointClient(entry)
    pages = [
        _fake_response(json_data=[{"id": 1}, {"id": 2}]),
        _fake_response(json_data=[{"id": 3}]),
    ]
    with patch.object(client._session, "request", side_effect=pages):
        df = client.fetch_dataframe()
    assert list(df["id"]) == [1, 2, 3]


def test_fetch_dataframe_page_pagination_sends_page_and_size_params():
    entry = _entry(pagination_type="page", pagination_page_param="pg",
                    pagination_size_param="sz", pagination_page_size=50, pagination_max_pages=10)
    client = APIEndpointClient(entry)
    captured_params = []

    def fake_request(method, url, **kwargs):
        captured_params.append(dict(kwargs["params"]))
        return _fake_response(json_data=[])  # empty page stops the loop immediately

    with patch.object(client._session, "request", side_effect=fake_request):
        client.fetch_dataframe()
    assert captured_params[0]["pg"] == 1
    assert captured_params[0]["sz"] == 50


def test_fetch_dataframe_page_pagination_stops_at_max_pages():
    entry = _entry(pagination_type="page", pagination_page_size=2, pagination_max_pages=3)
    client = APIEndpointClient(entry)
    # Every page returns exactly page_size rows, so without the cap this would loop forever
    with patch.object(client._session, "request", return_value=_fake_response(json_data=[{"id": 1}, {"id": 2}])):
        df = client.fetch_dataframe()
    assert len(df) == 6  # 3 pages * 2 rows
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_rest_api_client.py -k page_pagination -v`
Expected: FAIL — `fetch_dataframe()` currently only requests one page, so `test_fetch_dataframe_page_pagination_stops_on_short_page` gets only `[{"id": 1}, {"id": 2}]` (2 rows, not 3), and the params tests never get a `page`/`limit` param sent.

- [ ] **Step 3: Implement pagination in `fetch_dataframe`**

In `etl_framework/rest_api/client.py`, replace the `fetch_dataframe` method:

```python
    def fetch_dataframe(self, max_pages: int | None = None) -> pd.DataFrame:
        entry = self._entry
        page_cap = max_pages if max_pages is not None else entry.pagination_max_pages
        frames: list[pd.DataFrame] = []
        query_params = dict(entry.query_params)
        url = entry.base_url
        page_number = 1

        for _ in range(page_cap):
            if entry.pagination_type == "page":
                query_params[entry.pagination_page_param] = page_number
                query_params[entry.pagination_size_param] = entry.pagination_page_size

            response = self._request(url, query_params)
            frame = self._parse_response(response)
            frames.append(frame)

            if entry.pagination_type == "none":
                break
            if entry.pagination_type == "page":
                if len(frame) < entry.pagination_page_size:
                    break
                page_number += 1
                continue
            if entry.pagination_type == "cursor":
                break  # cursor pagination implemented in Task 6

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_rest_api_client.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add etl_framework/rest_api/client.py tests/unit/test_rest_api_client.py
git commit -m "feat: add page/limit pagination to APIEndpointClient"
```

---

## Task 6: REST API client — cursor pagination + `max_pages` cap

**Files:**
- Modify: `etl_framework/rest_api/client.py`
- Modify: `tests/unit/test_rest_api_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_rest_api_client.py`:

```python
def test_fetch_dataframe_cursor_pagination_follows_next_cursor():
    entry = _entry(json_root_path="items", pagination_type="cursor",
                    pagination_cursor_path="next_cursor", pagination_cursor_param="cursor",
                    pagination_max_pages=10)
    client = APIEndpointClient(entry)
    page1 = _fake_response(json_data={"items": [{"id": 1}], "next_cursor": "abc"})
    page2 = _fake_response(json_data={"items": [{"id": 2}], "next_cursor": None})
    with patch.object(client._session, "request", side_effect=[page1, page2]):
        df = client.fetch_dataframe()
    assert list(df["id"]) == [1, 2]


def test_fetch_dataframe_cursor_pagination_sends_cursor_as_query_param():
    entry = _entry(json_root_path="items", pagination_type="cursor",
                    pagination_cursor_path="next_cursor", pagination_cursor_param="cursor",
                    pagination_max_pages=10)
    client = APIEndpointClient(entry)
    page1 = _fake_response(json_data={"items": [{"id": 1}], "next_cursor": "abc"})
    page2 = _fake_response(json_data={"items": [{"id": 2}], "next_cursor": None})
    captured_params = []

    def fake_request(method, url, **kwargs):
        captured_params.append(dict(kwargs["params"]))
        return [page1, page2][len(captured_params) - 1]

    with patch.object(client._session, "request", side_effect=fake_request):
        client.fetch_dataframe()
    assert "cursor" not in captured_params[0]
    assert captured_params[1]["cursor"] == "abc"


def test_fetch_dataframe_cursor_pagination_follows_full_url_cursor():
    entry = _entry(json_root_path="items", pagination_type="cursor",
                    pagination_cursor_path="next_url", pagination_max_pages=10)
    client = APIEndpointClient(entry)
    page1 = _fake_response(json_data={"items": [{"id": 1}], "next_url": "https://api.example.com/v1/orders?page=2"})
    page2 = _fake_response(json_data={"items": [{"id": 2}], "next_url": None})
    captured_urls = []

    def fake_request(method, url, **kwargs):
        captured_urls.append(url)
        return [page1, page2][len(captured_urls) - 1]

    with patch.object(client._session, "request", side_effect=fake_request):
        client.fetch_dataframe()
    assert captured_urls == [
        "https://api.example.com/v1/orders",
        "https://api.example.com/v1/orders?page=2",
    ]


def test_fetch_dataframe_cursor_pagination_respects_max_pages_cap():
    entry = _entry(json_root_path="items", pagination_type="cursor",
                    pagination_cursor_path="next_cursor", pagination_cursor_param="cursor",
                    pagination_max_pages=2)
    client = APIEndpointClient(entry)
    # Every page returns a next_cursor, so without the cap this would loop forever
    with patch.object(client._session, "request",
                       return_value=_fake_response(json_data={"items": [{"id": 1}], "next_cursor": "more"})):
        df = client.fetch_dataframe()
    assert len(df) == 2


def test_fetch_dataframe_max_pages_override_wins_over_entry_default():
    entry = _entry(pagination_type="page", pagination_page_size=1, pagination_max_pages=50)
    client = APIEndpointClient(entry)
    with patch.object(client._session, "request", return_value=_fake_response(json_data=[{"id": 1}])):
        df = client.fetch_dataframe(max_pages=1)
    assert len(df) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_rest_api_client.py -k cursor -v`
Expected: FAIL — cursor pagination currently `break`s after the first page unconditionally.

- [ ] **Step 3: Implement cursor pagination**

In `etl_framework/rest_api/client.py`, replace the `if entry.pagination_type == "cursor": break` line in `fetch_dataframe` with:

```python
            if entry.pagination_type == "cursor":
                cursor_value = self._extract_cursor(response)
                if not cursor_value:
                    break
                from urllib.parse import urlparse
                if urlparse(cursor_value).scheme:
                    url = cursor_value
                    query_params = {}
                else:
                    query_params[entry.pagination_cursor_param] = cursor_value
```

Then add the `_extract_cursor` helper method (below `_walk_json_path`):

```python
    def _extract_cursor(self, response: requests.Response) -> str | None:
        entry = self._entry
        if not entry.pagination_cursor_path or entry.response_format == "csv":
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        current = payload
        for part in entry.pagination_cursor_path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return str(current) if current else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_rest_api_client.py -v`
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add etl_framework/rest_api/client.py tests/unit/test_rest_api_client.py
git commit -m "feat: add cursor pagination and max_pages override to APIEndpointClient"
```

---

## Task 7: `SourceConfig` gains the `api` source type

**Files:**
- Modify: `api/schemas.py:466-484`
- Test: `tests/unit/test_api.py` (new tests appended)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_api.py`:

```python
# --- SourceConfig api source_type ---

def test_source_config_api_requires_config_id_and_endpoint_name():
    from pydantic import ValidationError
    from api.schemas import SourceConfig
    with pytest.raises(ValidationError):
        SourceConfig(source_type="api", config_id=1)  # missing api_endpoint_name
    with pytest.raises(ValidationError):
        SourceConfig(source_type="api", api_endpoint_name="orders")  # missing config_id


def test_source_config_api_accepts_config_id_and_endpoint_name():
    from api.schemas import SourceConfig
    src = SourceConfig(source_type="api", config_id=1, api_endpoint_name="orders")
    assert src.source_type == "api"
    assert src.api_endpoint_name == "orders"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_api.py -k source_config_api -v`
Expected: FAIL — `source_type="api"` is rejected by the current `Literal["live", "path", "upload"]`

- [ ] **Step 3: Extend `SourceConfig`**

In `api/schemas.py`, replace the `SourceConfig` class (currently lines 466-484):

```python
class SourceConfig(BaseModel):
    source_type: Literal["live", "path", "upload"]
    config_id: int | None = None
    doc_id: str | None = None
    report_id: str | None = None
    format: Literal["csv", "xlsx", "xls"] = "xlsx"
    file_path: str | None = None
    file_content_b64: str | None = None
    file_name: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "SourceConfig":
        if self.source_type == "live" and self.config_id is None:
            raise ValueError("config_id required for live source")
        if self.source_type == "path" and not self.file_path:
            raise ValueError("file_path required for path source")
        if self.source_type == "upload" and not self.file_content_b64:
            raise ValueError("file_content_b64 required for upload source")
        return self
```

with:

```python
class SourceConfig(BaseModel):
    source_type: Literal["live", "path", "upload", "api"]
    config_id: int | None = None
    doc_id: str | None = None
    report_id: str | None = None
    format: Literal["csv", "xlsx", "xls"] = "xlsx"
    file_path: str | None = None
    file_content_b64: str | None = None
    file_name: str | None = None
    api_endpoint_name: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "SourceConfig":
        if self.source_type == "live" and self.config_id is None:
            raise ValueError("config_id required for live source")
        if self.source_type == "path" and not self.file_path:
            raise ValueError("file_path required for path source")
        if self.source_type == "upload" and not self.file_content_b64:
            raise ValueError("file_content_b64 required for upload source")
        if self.source_type == "api" and (self.config_id is None or not self.api_endpoint_name):
            raise ValueError("config_id and api_endpoint_name required for api source")
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api.py -k source_config_api -v`
Expected: 2 passed

- [ ] **Step 5: Run the full schema test module to check for regressions**

Run: `python -m pytest tests/unit/test_api.py -v`
Expected: all passing (no regressions from the `SourceConfig` change)

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py tests/unit/test_api.py
git commit -m "feat: add api source_type to SourceConfig"
```

---

## Task 8: `JobDefinition` gains the `api_reconciliation` job type

**Files:**
- Modify: `api/schemas.py:309-353`
- Test: `tests/unit/test_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_api.py`:

```python
# --- api_reconciliation job type ---

def test_create_api_reconciliation_job_requires_endpoint_params(client):
    resp = client.post(
        "/api/jobs",
        json={
            "name": "bad_api_job",
            "job_type": "api_reconciliation",
            "query": "",
            "key_columns": ["id"],
            "params": {},
        },
    )
    assert resp.status_code == 422


def test_create_api_reconciliation_job_requires_key_columns(client):
    resp = client.post(
        "/api/jobs",
        json={
            "name": "bad_api_job2",
            "job_type": "api_reconciliation",
            "query": "",
            "key_columns": [],
            "params": {"source_api_endpoint": "orders_a", "target_api_endpoint": "orders_b"},
        },
    )
    assert resp.status_code == 422


def test_create_api_reconciliation_job_succeeds(client):
    resp = client.post(
        "/api/jobs",
        json={
            "name": "good_api_job",
            "job_type": "api_reconciliation",
            "query": "",
            "key_columns": ["id"],
            "params": {"source_api_endpoint": "orders_a", "target_api_endpoint": "orders_b"},
        },
    )
    assert resp.status_code == 201
    assert resp.json()["job_type"] == "api_reconciliation"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_api.py -k api_reconciliation_job -v`
Expected: FAIL — `"api_reconciliation"` is not a valid `job_type` literal yet, so all three requests get `422` for the wrong reason (or the third gets rejected instead of accepted); specifically `test_create_api_reconciliation_job_succeeds` fails because the literal doesn't include `"api_reconciliation"`.

- [ ] **Step 3: Extend `JobDefinition`**

In `api/schemas.py`, change the `job_type` field (currently lines 313-316):

```python
    job_type: Literal[
        "reconciliation", "health_check", "bo_report", "automic_job", "dbt_artifact",
        "freshness", "cross_job_assertion", "schema_snapshot", "profile",
    ] = "reconciliation"
```

to:

```python
    job_type: Literal[
        "reconciliation", "health_check", "bo_report", "automic_job", "dbt_artifact",
        "freshness", "cross_job_assertion", "schema_snapshot", "profile", "api_reconciliation",
    ] = "reconciliation"
```

Then, in `validate_reconciliation_contract` (currently lines 328-353), add a new `elif` branch. Insert it right after the `bo_report` branch:

```python
        if self.job_type == "bo_report":
            if not self.params.get("report_id"):
                raise ValueError("bo_report jobs require 'report_id' in params")
        elif self.job_type == "api_reconciliation":
            if not self.params.get("source_api_endpoint") or not self.params.get("target_api_endpoint"):
                raise ValueError(
                    "api_reconciliation jobs require 'source_api_endpoint' and 'target_api_endpoint' in params"
                )
            if not self.key_columns:
                raise ValueError("api_reconciliation jobs require key_columns")
        elif self.job_type == "automic_job":
```

(This just inserts the new `elif` between the existing `bo_report` `if` and `automic_job` `elif` — the rest of the chain is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api.py -k api_reconciliation_job -v`
Expected: 3 passed

- [ ] **Step 5: Run the full job test coverage to check for regressions**

Run: `python -m pytest tests/unit/test_api.py -v`
Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py tests/unit/test_api.py
git commit -m "feat: add api_reconciliation job type"
```

---

## Task 9: `RestApiTestRequest` / `RestApiPreviewRequest` schemas

**Files:**
- Modify: `api/schemas.py` (near `BOTestRequest`, currently line 397-399)

- [ ] **Step 1: Add the schemas**

In `api/schemas.py`, right after the `BOTestRequest` class:

```python
class BOTestRequest(BaseModel):
    config_id: int


class RestApiTestRequest(BaseModel):
    config_id: int
    endpoint_name: str


class RestApiPreviewRequest(BaseModel):
    config_id: int
    endpoint_name: str
    limit: int = 50
```

There's no standalone test for this step — these schemas are exercised by Task 12's adapter-route tests. This task exists on its own so Task 12's diff is smaller and easier to review.

- [ ] **Step 2: Verify the module still imports cleanly**

Run: `python -c "from api.schemas import RestApiTestRequest, RestApiPreviewRequest; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add api/schemas.py
git commit -m "feat: add RestApiTestRequest and RestApiPreviewRequest schemas"
```

---

## Task 10: Config masking covers `api_endpoints` secrets

**Files:**
- Modify: `api/routes/configs.py:27-74`
- Test: `tests/unit/test_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_api.py`:

```python
# --- api_endpoints secret masking ---

def test_config_masks_api_endpoint_secrets(client):
    resp = client.post(
        "/api/configs",
        json={
            "name": "api-cfg",
            "env_name": "dev",
            "config_data": {
                "api_endpoints": {
                    "orders": {
                        "base_url": "https://api.example.com/orders",
                        "auth_type": "bearer",
                        "bearer_token": "super-secret-token",
                    }
                }
            },
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["config_data"]["api_endpoints"]["orders"]["bearer_token"] == "********"
    assert data["config_data"]["api_endpoints"]["orders"]["base_url"] == "https://api.example.com/orders"


def test_update_config_preserves_masked_api_endpoint_secret(client):
    created = client.post(
        "/api/configs",
        json={
            "name": "api-cfg2",
            "env_name": "dev",
            "config_data": {
                "api_endpoints": {
                    "orders": {
                        "base_url": "https://api.example.com/orders",
                        "auth_type": "bearer",
                        "bearer_token": "super-secret-token",
                    }
                }
            },
        },
    ).json()

    # Simulate the frontend echoing back the masked value on update
    masked_data = created["config_data"]
    masked_data["api_endpoints"]["orders"]["base_url"] = "https://api.example.com/orders-v2"
    resp = client.put(f"/api/configs/{created['id']}", json={"config_data": masked_data})
    assert resp.status_code == 200

    detail = client.get(f"/api/configs/{created['id']}").json()
    # base_url change went through, but the mask did NOT clobber the real secret
    assert detail["config_data"]["api_endpoints"]["orders"]["base_url"] == "https://api.example.com/orders-v2"
    assert detail["config_data"]["api_endpoints"]["orders"]["bearer_token"] == "********"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_api.py -k api_endpoint_secret -v`
Expected: FAIL — `bearer_token` is currently returned in plaintext (not in `_SENSITIVE_KEYS`, and `api_endpoints` isn't recursed into).

- [ ] **Step 3: Extend `_SENSITIVE_KEYS`, `_mask`, `_preserve_masked_secrets`**

In `api/routes/configs.py`, change:

```python
_SENSITIVE_KEYS = {"db_password", "automic_password", "bo_password"}
```

to:

```python
_SENSITIVE_KEYS = {
    "db_password", "automic_password", "bo_password",
    "api_key", "bearer_token", "basic_password",
}
```

Replace the `_mask` function:

```python
def _mask(data: dict) -> dict:
    """Replace sensitive credential values with a fixed mask before returning to callers."""
    result = {
        k: (_MASK if k in _SENSITIVE_KEYS and v is not None else v)
        for k, v in data.items()
        if k not in ("connections", "api_endpoints")
    }
    if "connections" in data and isinstance(data["connections"], dict):
        result["connections"] = {
            conn_name: {
                k: (_MASK if k in _SENSITIVE_KEYS and v is not None else v)
                for k, v in entry.items()
            }
            for conn_name, entry in data["connections"].items()
        }
    if "api_endpoints" in data and isinstance(data["api_endpoints"], dict):
        result["api_endpoints"] = {
            ep_name: {
                k: (_MASK if k in _SENSITIVE_KEYS and v is not None else v)
                for k, v in entry.items()
            }
            for ep_name, entry in data["api_endpoints"].items()
        }
    return result
```

Replace the `_preserve_masked_secrets` function:

```python
def _preserve_masked_secrets(incoming: dict, existing: dict | None) -> dict:
    """Keep stored secret values when the client submits the display mask."""
    if not existing:
        return incoming
    result = dict(incoming)
    for key in _SENSITIVE_KEYS:
        if result.get(key) == _MASK:
            result[key] = existing.get(key, "")

    incoming_connections = result.get("connections")
    existing_connections = existing.get("connections")
    if isinstance(incoming_connections, dict) and isinstance(existing_connections, dict):
        merged_connections = {}
        for conn_name, entry in incoming_connections.items():
            if not isinstance(entry, dict):
                merged_connections[conn_name] = entry
                continue
            merged_entry = dict(entry)
            existing_entry = existing_connections.get(conn_name, {})
            if isinstance(existing_entry, dict):
                for key in _SENSITIVE_KEYS:
                    if merged_entry.get(key) == _MASK:
                        merged_entry[key] = existing_entry.get(key, "")
            merged_connections[conn_name] = merged_entry
        result["connections"] = merged_connections

    incoming_endpoints = result.get("api_endpoints")
    existing_endpoints = existing.get("api_endpoints")
    if isinstance(incoming_endpoints, dict) and isinstance(existing_endpoints, dict):
        merged_endpoints = {}
        for ep_name, entry in incoming_endpoints.items():
            if not isinstance(entry, dict):
                merged_endpoints[ep_name] = entry
                continue
            merged_entry = dict(entry)
            existing_entry = existing_endpoints.get(ep_name, {})
            if isinstance(existing_entry, dict):
                for key in _SENSITIVE_KEYS:
                    if merged_entry.get(key) == _MASK:
                        merged_entry[key] = existing_entry.get(key, "")
            merged_endpoints[ep_name] = merged_entry
        result["api_endpoints"] = merged_endpoints
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api.py -k api_endpoint_secret -v`
Expected: 2 passed

- [ ] **Step 5: Run the full config test coverage to check for regressions**

Run: `python -m pytest tests/unit/test_api.py -v`
Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add api/routes/configs.py tests/unit/test_api.py
git commit -m "feat: mask api_endpoints secrets in config responses"
```

---

## Task 11: Config `/validate` validates `api_endpoints` entries

**Files:**
- Modify: `api/routes/configs.py:9-21` (imports), `103-139` (`validate_config`)
- Test: `tests/unit/test_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_api.py`:

```python
# --- api_endpoints validation ---

def test_validate_config_accepts_valid_api_endpoint(client):
    resp = client.post(
        "/api/configs/validate",
        json={
            "env_name": "dev",
            "config_data": {
                "db_host": "localhost",
                "db_password": "secret",
                "api_endpoints": {
                    "orders": {"base_url": "https://api.example.com/orders"},
                },
            },
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_validate_config_rejects_api_endpoint_missing_scheme(client):
    resp = client.post(
        "/api/configs/validate",
        json={
            "env_name": "dev",
            "config_data": {
                "db_host": "localhost",
                "db_password": "secret",
                "api_endpoints": {
                    "orders": {"base_url": "api.example.com/orders"},
                },
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert any("api_endpoints.orders" in err["field_name"] for err in data["errors"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_api.py -k validate_config_.*api_endpoint -v`
Expected: FAIL — `/validate` currently ignores `api_endpoints` entirely, so both requests return `ok: true`.

- [ ] **Step 3: Add `ApiEndpointEntry` to the imports and extend `validate_config`**

In `api/routes/configs.py`, change the import line (currently line 20):

```python
from etl_framework.config.models import EnvironmentConfig, resolve_connection
```

to:

```python
from etl_framework.config.models import ApiEndpointEntry, EnvironmentConfig, resolve_connection
```

Then in `validate_config` (currently lines 103-139), insert a new validation block between the `connection_errors` loop and the final `if connection_errors:` check:

```python
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

    for ep_name, ep_data in (body.config_data.get("api_endpoints") or {}).items():
        try:
            ApiEndpointEntry.model_validate({"name": ep_name, **(ep_data or {})})
        except ValidationError as exc:
            for err in exc.errors():
                connection_errors.append(FrameworkErrorOut(
                    error_type="validation_error",
                    message=err["msg"],
                    field_name=f"api_endpoints.{ep_name}." + ".".join(str(p) for p in err["loc"]),
                    details={"input": err.get("input")},
                ))

    if connection_errors:
        return ConfigValidationOut(ok=False, env_name=body.env_name, errors=connection_errors)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api.py -k validate_config_.*api_endpoint -v`
Expected: 2 passed

- [ ] **Step 5: Run the full config test coverage to check for regressions**

Run: `python -m pytest tests/unit/test_api.py -v`
Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add api/routes/configs.py tests/unit/test_api.py
git commit -m "feat: validate api_endpoints entries in /api/configs/validate"
```

---

## Task 12: Adapter test/preview endpoints for REST API connections

**Files:**
- Modify: `api/services/adapter_service.py`
- Modify: `api/routes/adapters.py`
- Test: `tests/unit/test_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_api.py`:

```python
# --- REST API adapter endpoints ---

def test_rest_api_test_endpoint_success(client, monkeypatch):
    cfg = client.post(
        "/api/configs",
        json={
            "name": "api-adapter-cfg",
            "env_name": "dev",
            "config_data": {
                "api_endpoints": {"orders": {"base_url": "https://api.example.com/orders"}},
            },
        },
    ).json()

    import pandas as pd
    from api.services import adapter_service

    class _FakeClient:
        def __init__(self, entry):
            pass

        def fetch_dataframe(self, max_pages=None):
            return pd.DataFrame({"id": [1, 2]})

    monkeypatch.setattr(adapter_service, "APIEndpointClient", _FakeClient)

    resp = client.post(
        "/api/adapters/rest-api/test",
        json={"config_id": cfg["id"], "endpoint_name": "orders"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_rest_api_test_endpoint_missing_endpoint_returns_ok_false(client):
    cfg = client.post(
        "/api/configs",
        json={"name": "api-adapter-cfg2", "env_name": "dev", "config_data": {"api_endpoints": {}}},
    ).json()
    resp = client.post(
        "/api/adapters/rest-api/test",
        json={"config_id": cfg["id"], "endpoint_name": "missing"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_rest_api_preview_endpoint_returns_sample_rows(client, monkeypatch):
    cfg = client.post(
        "/api/configs",
        json={
            "name": "api-adapter-cfg3",
            "env_name": "dev",
            "config_data": {
                "api_endpoints": {"orders": {"base_url": "https://api.example.com/orders"}},
            },
        },
    ).json()

    import pandas as pd
    from api.services import adapter_service

    class _FakeClient:
        def __init__(self, entry):
            pass

        def fetch_dataframe(self, max_pages=None):
            return pd.DataFrame({"id": [1, 2, 3], "amount": [10, 20, 30]})

    monkeypatch.setattr(adapter_service, "APIEndpointClient", _FakeClient)

    resp = client.post(
        "/api/adapters/rest-api/preview",
        json={"config_id": cfg["id"], "endpoint_name": "orders", "limit": 2},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["columns"] == ["id", "amount"]
    assert len(data["rows"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_api.py -k rest_api_ -v`
Expected: FAIL with `404 Not Found` — the routes don't exist yet.

- [ ] **Step 3: Add `test_api_endpoint` / `preview_api_endpoint` to `AdapterService`**

In `api/services/adapter_service.py`, change the imports at the top:

```python
from api.schemas import AdapterTestOut, AutomicJobStatusOut, BODocOut, BOReportOut
from etl_framework.automic.client import AutomicClient
from etl_framework.config.models import EnvironmentConfig
from etl_framework.exceptions import BOAPIError, ReportNotFoundError
from etl_framework.repository.repository import ConfigRepository
from etl_framework.sap_bo.client import BORestClient
```

to:

```python
from api.schemas import AdapterTestOut, AutomicJobStatusOut, BODocOut, BOReportOut
from etl_framework.automic.client import AutomicClient
from etl_framework.config.models import EnvironmentConfig, resolve_api_endpoint
from etl_framework.exceptions import BOAPIError, ReportNotFoundError
from etl_framework.repository.repository import ConfigRepository
from etl_framework.rest_api.client import APIEndpointClient
from etl_framework.sap_bo.client import BORestClient
```

Then add these methods to `AdapterService`, right after `_get_env_config`:

```python
    def _get_api_endpoint(self, config_id: int, endpoint_name: str):
        cfg = self._config_repo.get(config_id)
        if cfg is None:
            raise HTTPException(status_code=404, detail="Config not found")
        try:
            return resolve_api_endpoint(cfg.config_json or {}, endpoint_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # REST API endpoints
    # ------------------------------------------------------------------

    def test_api_endpoint(self, config_id: int, endpoint_name: str) -> AdapterTestOut:
        entry = self._get_api_endpoint(config_id, endpoint_name)
        t0 = time.monotonic()
        try:
            APIEndpointClient(entry).fetch_dataframe(max_pages=1)
            latency_ms = int((time.monotonic() - t0) * 1000)
            return AdapterTestOut(ok=True, message="Connection successful", latency_ms=latency_ms)
        except Exception as exc:
            return AdapterTestOut(ok=False, message=_friendly_error(exc), latency_ms=0)

    def preview_api_endpoint(self, config_id: int, endpoint_name: str, limit: int) -> dict:
        import json
        entry = self._get_api_endpoint(config_id, endpoint_name)
        try:
            df = APIEndpointClient(entry).fetch_dataframe(max_pages=1)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_friendly_error(exc)) from exc
        df = df.head(max(1, min(200, limit)))
        rows = json.loads(df.to_json(orient="values", date_format="iso"))
        return {"columns": list(df.columns), "rows": rows}
```

- [ ] **Step 4: Add the routes**

In `api/routes/adapters.py`, change the imports:

```python
from api.schemas import (
    AdapterTestOut,
    AutomicBulkImportRequest,
    AutomicBulkImportResponse,
    AutomicJobStatusOut,
    AutomicJobSummary,
    AutomicJobCreateRequest,
    AutomicLookupRequest,
    BODocOut,
    BOJobCreateRequest,
    BOReportOut,
    JobDefinition,
    BOTestRequest,
)
```

to:

```python
from api.schemas import (
    AdapterTestOut,
    AutomicBulkImportRequest,
    AutomicBulkImportResponse,
    AutomicJobStatusOut,
    AutomicJobSummary,
    AutomicJobCreateRequest,
    AutomicLookupRequest,
    BODocOut,
    BOJobCreateRequest,
    BOReportOut,
    JobDefinition,
    BOTestRequest,
    RestApiPreviewRequest,
    RestApiTestRequest,
)
```

Then add a new section right after the "Automic" section (after `search_automic_jobs`, before "Job creation from adapters"):

```python
# ---------------------------------------------------------------------------
# REST API endpoints
# ---------------------------------------------------------------------------

@router.post("/rest-api/test", response_model=AdapterTestOut)
def test_rest_api_endpoint(
    body: RestApiTestRequest,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.test_api_endpoint(body.config_id, body.endpoint_name)


@router.post("/rest-api/preview")
def preview_rest_api_endpoint(
    body: RestApiPreviewRequest,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.preview_api_endpoint(body.config_id, body.endpoint_name, body.limit)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api.py -k rest_api_ -v`
Expected: 3 passed

- [ ] **Step 6: Run the full test file to check for regressions**

Run: `python -m pytest tests/unit/test_api.py -v`
Expected: all passing

- [ ] **Step 7: Commit**

```bash
git add api/services/adapter_service.py api/routes/adapters.py tests/unit/test_api.py
git commit -m "feat: add REST API test-connection and preview adapter endpoints"
```

---

## Task 13: Extract `FrameEngine` into a shared module

**Files:**
- Create: `api/services/frame_engine.py`
- Modify: `api/services/compare_service.py:68-78` (remove class), `165-166`, `297-298` (call sites), imports

- [ ] **Step 1: Create the shared module**

Create `api/services/frame_engine.py`:

```python
from __future__ import annotations
import types

import pandas as pd


class FrameEngine:
    """Wrap a pre-loaded DataFrame so ReconciliationEngine can consume it."""

    def __init__(self, df: pd.DataFrame, env_name: str) -> None:
        self._df = df
        self._env = types.SimpleNamespace(name=env_name)

    def execute_query(self, query: str, params: dict | None = None) -> pd.DataFrame:
        return self._df
```

- [ ] **Step 2: Remove the class from `compare_service.py` and import it instead**

In `api/services/compare_service.py`, delete the `_FrameEngine` class definition (currently lines 68-78):

```python
class _FrameEngine:
    """Wrap a pre-loaded DataFrame so ReconciliationEngine can consume it."""

    def __init__(self, df, env_name: str):
        import types
        self._df = df
        self._env = types.SimpleNamespace(name=env_name)

    def execute_query(self, query: str, params=None):
        return self._df
```

Add the import near the top of the file, alongside the other `api.services` import:

```python
from api.services.file_source import read_tabular
from api.services.frame_engine import FrameEngine
```

Then replace the two call sites. In `run_bo_comparison` (currently lines 165-166):

```python
            engine_a = _FrameEngine(df_a, req.label_a)
            engine_b = _FrameEngine(df_b, req.label_b)
```

becomes:

```python
            engine_a = FrameEngine(df_a, req.label_a)
            engine_b = FrameEngine(df_b, req.label_b)
```

In `_run_tabular_file_compare` (currently lines 297-298):

```python
        engine_a = _FrameEngine(df_a, req.label_a)
        engine_b = _FrameEngine(df_b, req.label_b)
```

becomes:

```python
        engine_a = FrameEngine(df_a, req.label_a)
        engine_b = FrameEngine(df_b, req.label_b)
```

- [ ] **Step 3: Run the existing compare_service test suite to confirm no regression**

Run: `python -m pytest tests/unit/test_tabular_file_compare.py -v`
Expected: all passing (this is a pure refactor — behavior is unchanged)

- [ ] **Step 4: Run the broader API test suite too**

Run: `python -m pytest tests/unit/test_api.py -v`
Expected: all passing

- [ ] **Step 5: Commit**

```bash
git add api/services/frame_engine.py api/services/compare_service.py
git commit -m "refactor: extract FrameEngine into a shared module"
```

---

## Task 14: `CompareService._load_api_source`

**Files:**
- Modify: `api/services/compare_service.py:218-246` (`_load_bo_source`)
- Test: `tests/unit/test_tabular_file_compare.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_tabular_file_compare.py`:

```python
def test_load_bo_source_dispatches_to_api_source():
    from api.schemas import SourceConfig

    svc = _svc()
    svc._config_repo = MagicMock()
    svc._config_repo.get.return_value = SimpleNamespace(
        config_json={"api_endpoints": {"orders": {"base_url": "https://api.example.com/orders"}}}
    )
    src = SourceConfig(source_type="api", config_id=1, api_endpoint_name="orders")
    fake_df = pd.DataFrame({"id": [1, 2]})

    with patch("etl_framework.rest_api.client.APIEndpointClient") as MockClient:
        MockClient.return_value.fetch_dataframe.return_value = fake_df
        result = svc._load_bo_source(src, None, None)

    assert result is fake_df
    MockClient.return_value.fetch_dataframe.assert_called_once_with()


def test_load_api_source_404_when_config_missing():
    from fastapi import HTTPException
    from api.schemas import SourceConfig

    svc = _svc()
    svc._config_repo = MagicMock()
    svc._config_repo.get.return_value = None
    src = SourceConfig(source_type="api", config_id=999, api_endpoint_name="orders")

    with pytest.raises(HTTPException) as exc:
        svc._load_bo_source(src, None, None)
    assert exc.value.status_code == 404


def test_load_api_source_404_when_endpoint_missing():
    from fastapi import HTTPException
    from api.schemas import SourceConfig

    svc = _svc()
    svc._config_repo = MagicMock()
    svc._config_repo.get.return_value = SimpleNamespace(config_json={"api_endpoints": {}})
    src = SourceConfig(source_type="api", config_id=1, api_endpoint_name="missing")

    with pytest.raises(HTTPException) as exc:
        svc._load_bo_source(src, None, None)
    assert exc.value.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_tabular_file_compare.py -k api_source -v`
Expected: FAIL — `_load_bo_source` has no `"api"` branch, so it falls through to `read_tabular(...)` and raises an `HTTPException` about missing path/content instead of dispatching correctly.

- [ ] **Step 3: Implement `_load_api_source` and wire it in**

In `api/services/compare_service.py`, replace the `_load_bo_source` method:

```python
    def _load_bo_source(self, src, fallback_doc_id: str | None, fallback_report_id: str | None):
        if src.source_type == "live":
            doc_id = src.doc_id or fallback_doc_id
            report_id = src.report_id or fallback_report_id
            if not doc_id or not report_id:
                raise HTTPException(
                    status_code=422,
                    detail="doc_id and report_id are required for live BO sources",
                )
            cfg = self._config_repo.get(src.config_id)
            if cfg is None:
                raise HTTPException(status_code=404, detail="Config not found")
            from etl_framework.config.models import EnvironmentConfig
            env = EnvironmentConfig(name=cfg.env_name, **cfg.config_json)
            from etl_framework.sap_bo.client import BORestClient
            client = BORestClient(env)
            try:
                raw = client.download_report(doc_id, report_id, src.format)
                return read_tabular(
                    content_b64=base64.b64encode(raw).decode("ascii"),
                    file_name=f"bo_report_{doc_id}_{report_id}.{src.format}",
                )
            finally:
                client.logout()
        if src.source_type == "api":
            return self._load_api_source(src)
        return read_tabular(
            path=src.file_path,
            content_b64=src.file_content_b64,
            file_name=src.file_name,
        )

    def _load_api_source(self, src) -> pd.DataFrame:
        cfg = self._config_repo.get(src.config_id)
        if cfg is None:
            raise HTTPException(status_code=404, detail="Config not found")
        from etl_framework.config.models import resolve_api_endpoint
        from etl_framework.rest_api.client import APIEndpointClient
        try:
            entry = resolve_api_endpoint(cfg.config_json or {}, src.api_endpoint_name or "")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return APIEndpointClient(entry).fetch_dataframe()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_tabular_file_compare.py -v`
Expected: all passing (the 3 new tests plus the existing ones)

- [ ] **Step 5: Run the full API test suite to check for regressions**

Run: `python -m pytest tests/unit/test_api.py -v`
Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add api/services/compare_service.py tests/unit/test_tabular_file_compare.py
git commit -m "feat: support api source_type in CompareService via _load_api_source"
```

---

## Task 15: `RunExecutor._build_case_api_reconciliation`

**Files:**
- Modify: `api/services/run_executor.py` (imports, `_build_case`, new method)
- Test: `tests/unit/test_run_executor_api_reconciliation.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_run_executor_api_reconciliation.py`:

```python
from __future__ import annotations
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.reconciliation.models import ReconciliationResult
from etl_framework.runner.state import TestStatus


def _executor(config_snapshot: dict, use_live_connections: bool = True) -> RunExecutor:
    return RunExecutor(
        db=MagicMock(),
        run_id="run-api-1",
        source_env="src",
        target_env="tgt",
        job_sequence=[],
        run_settings=RunSettings(use_live_connections=use_live_connections),
        config_snapshot=config_snapshot,
    )


def _job(**overrides) -> JobDefinition:
    base = dict(
        name="api_orders_check",
        job_type="api_reconciliation",
        query="",
        key_columns=["id"],
        params={"source_api_endpoint": "orders_a", "target_api_endpoint": "orders_b"},
    )
    base.update(overrides)
    return JobDefinition(**base)


def test_build_case_api_reconciliation_flags_row_mismatch():
    snapshot = {
        "api_endpoints": {
            "orders_a": {"base_url": "https://a.example.com/orders"},
            "orders_b": {"base_url": "https://b.example.com/orders"},
        }
    }
    ex = _executor(snapshot)
    job = _job()

    df_a = pd.DataFrame({"id": [1, 2], "amount": [10, 20]})
    df_b = pd.DataFrame({"id": [1, 2], "amount": [10, 25]})  # row 2 mismatches

    with patch("etl_framework.rest_api.client.APIEndpointClient") as MockClient:
        MockClient.return_value.fetch_dataframe.side_effect = [df_a, df_b]
        case_fn = ex._build_case(job)
        result = case_fn()

    assert isinstance(result, ReconciliationResult)
    assert result.source_row_count == 2
    assert result.status == TestStatus.FAILED
    assert result.value_mismatch_count == 1


def test_build_case_api_reconciliation_passes_when_identical():
    snapshot = {
        "api_endpoints": {
            "orders_a": {"base_url": "https://a.example.com/orders"},
            "orders_b": {"base_url": "https://b.example.com/orders"},
        }
    }
    ex = _executor(snapshot)
    job = _job()

    df = pd.DataFrame({"id": [1, 2], "amount": [10, 20]})

    with patch("etl_framework.rest_api.client.APIEndpointClient") as MockClient:
        MockClient.return_value.fetch_dataframe.side_effect = [df.copy(), df.copy()]
        case_fn = ex._build_case(job)
        result = case_fn()

    assert result.status == TestStatus.PASSED


def test_build_case_api_reconciliation_not_used_without_live_connections():
    ex = _executor({}, use_live_connections=False)
    job = _job()
    with patch("etl_framework.rest_api.client.APIEndpointClient") as MockClient:
        case_fn = ex._build_case(job)
        result = case_fn()
    # The live-endpoint path is never taken, so the client is never constructed
    MockClient.assert_not_called()
    assert isinstance(result, ReconciliationResult)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_run_executor_api_reconciliation.py -v`
Expected: FAIL — `_build_case` has no `api_reconciliation` branch yet, so it falls through to the default DB-simulation path and `APIEndpointClient` is never called; `test_build_case_api_reconciliation_flags_row_mismatch` fails because `MockClient.return_value.fetch_dataframe` is never consulted (the assertions about `result.source_row_count`/`status` will not match the simulated single-row default).

- [ ] **Step 3: Wire in the new job type and implement the case builder**

In `api/services/run_executor.py`, add the import near the top, alongside the other `api.services` imports:

```python
from api.services.frame_engine import FrameEngine
```

In `_build_case`, insert a new dispatch line right after the `automic_job` check:

```python
        if job.job_type == "bo_report" and self._settings.use_live_connections:
            return self._build_case_bo_report(job)
        if job.job_type == "automic_job" and self._settings.use_live_connections:
            return self._build_case_automic(job)
        if job.job_type == "api_reconciliation" and self._settings.use_live_connections:
            return self._build_case_api_reconciliation(job)
```

Then add the new method, right after `_build_case_automic`:

```python
    def _build_case_api_reconciliation(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            from etl_framework.config.models import resolve_api_endpoint
            from etl_framework.rest_api.client import APIEndpointClient

            api_endpoints = self._config_snapshot.get("api_endpoints") or {}
            endpoints_snapshot = {"api_endpoints": api_endpoints}
            src_entry = resolve_api_endpoint(endpoints_snapshot, job.params["source_api_endpoint"])
            tgt_entry = resolve_api_endpoint(endpoints_snapshot, job.params["target_api_endpoint"])

            df_a = APIEndpointClient(src_entry).fetch_dataframe()
            df_b = APIEndpointClient(tgt_entry).fetch_dataframe()

            reconciler = ReconciliationEngine(
                source_engine=FrameEngine(df_a, self._source_env),
                target_engine=FrameEngine(df_b, self._target_env),
                key_columns=job.key_columns,
                exclude_columns=job.exclude_columns,
                float_tolerance=self._settings.float_tolerance,
                mismatch_row_limit=self._settings.mismatch_row_limit,
                backend=self._build_backend(job),
            )
            return reconciler.reconcile(query="__api_source__", query_name=job.name)
        return run_job
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_run_executor_api_reconciliation.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `python -m pytest tests/unit/ -v`
Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_executor_api_reconciliation.py
git commit -m "feat: add api_reconciliation job execution to RunExecutor"
```

---

## Task 16: Frontend — Config modal API Endpoints section

**Files:**
- Modify: `frontend/app.js` (config modal state functions: `openNewConfigModal`, `editConfig`, `_configDataFromModal`, plus new methods)
- Modify: `frontend/index.html` (config modal markup, after the Named Connections section)

No JS test harness exists in this repo — verify manually per Step 4.

- [ ] **Step 1: Add `apiEndpoints` state to the config modal open/edit functions**

In `frontend/app.js`, in `openNewConfigModal()` (~line 826-838), add `apiEndpoints: []` after `connections: []`:

```js
    openNewConfigModal() {
      this.configModal = {
        id: null, name: '', env_name: 'dev',
        db_host: 'localhost', db_port: 1433, db_name: '', db_user: '', db_password: '',
        db_connect_timeout: 15,
        bo_url: '', bo_user: '', bo_password: '', bo_auth_type: 'secEnterprise', bo_timeout: 60,
        bo_proxy_url: '', bo_verify_ssl: true,
        automic_url: '', automic_user: '', automic_password: '',
        connections: [],
        apiEndpoints: [],
      };
      this.configValidation = null;
      this.showConfigModal = true;
    },
```

In `editConfig(cfg)` (~line 840-865), add endpoint deserialization after the `connections:` block:

```js
        connections: Object.entries(d.connections || {}).map(([name, entry]) => ({
          name,
          db_host: entry.db_host || '',
          db_name: entry.db_name || '',
          db_user: entry.db_user || '',
          db_password: entry.db_password || '',
          expanded: false,
        })),
        apiEndpoints: Object.entries(d.api_endpoints || {}).map(([name, entry]) => ({
          name,
          base_url: entry.base_url || '',
          method: entry.method || 'GET',
          auth_type: entry.auth_type || 'none',
          api_key_header: entry.api_key_header || 'X-API-Key',
          api_key: entry.api_key || '',
          bearer_token: entry.bearer_token || '',
          basic_username: entry.basic_username || '',
          basic_password: entry.basic_password || '',
          headers_raw: Object.entries(entry.headers || {}).map(([k, v]) => `${k}: ${v}`).join('\n'),
          query_params_raw: Object.entries(entry.query_params || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
          body_raw: entry.body ? JSON.stringify(entry.body, null, 2) : '',
          timeout: entry.timeout ?? 30,
          verify_ssl: entry.verify_ssl !== false,
          response_format: entry.response_format || 'json',
          json_root_path: entry.json_root_path || '',
          pagination_type: entry.pagination_type || 'none',
          pagination_cursor_path: entry.pagination_cursor_path || '',
          pagination_cursor_param: entry.pagination_cursor_param || 'cursor',
          pagination_page_param: entry.pagination_page_param || 'page',
          pagination_size_param: entry.pagination_size_param || 'limit',
          pagination_page_size: entry.pagination_page_size ?? 100,
          pagination_max_pages: entry.pagination_max_pages ?? 50,
          expanded: false,
          previewResult: null,
          previewError: '',
          testResult: null,
        })),
      };
```

(Note: this replaces the closing `};` of the object — keep everything else in `editConfig` unchanged, just insert the new `apiEndpoints:` array before the final `};`.)

- [ ] **Step 2: Serialize `apiEndpoints` on save, add CRUD helper methods**

In `_configDataFromModal()` (~line 867-902), add serialization after the existing `connections` block, right before `return data;`:

```js
      if (m.apiEndpoints && m.apiEndpoints.length > 0) {
        data.api_endpoints = Object.fromEntries(
          m.apiEndpoints
            .filter(e => e.name.trim() && e.base_url.trim())
            .map(e => {
              const headers = {};
              (e.headers_raw || '').split('\n').forEach(line => {
                const idx = line.indexOf(':');
                if (idx > 0) headers[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
              });
              const query_params = {};
              (e.query_params_raw || '').split('\n').forEach(line => {
                const idx = line.indexOf('=');
                if (idx > 0) query_params[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
              });
              let body = null;
              if (e.body_raw && e.body_raw.trim()) {
                try { body = JSON.parse(e.body_raw); } catch { body = null; }
              }
              return [e.name.trim(), {
                base_url: e.base_url.trim(),
                method: e.method || 'GET',
                auth_type: e.auth_type || 'none',
                api_key_header: e.api_key_header || 'X-API-Key',
                api_key: e.api_key || '',
                bearer_token: e.bearer_token || '',
                basic_username: e.basic_username || '',
                basic_password: e.basic_password || '',
                headers, query_params, body,
                timeout: Number(e.timeout) || 30,
                verify_ssl: e.verify_ssl !== false,
                response_format: e.response_format || 'json',
                json_root_path: e.json_root_path || '',
                pagination_type: e.pagination_type || 'none',
                pagination_cursor_path: e.pagination_cursor_path || '',
                pagination_cursor_param: e.pagination_cursor_param || 'cursor',
                pagination_page_param: e.pagination_page_param || 'page',
                pagination_size_param: e.pagination_size_param || 'limit',
                pagination_page_size: Number(e.pagination_page_size) || 100,
                pagination_max_pages: Number(e.pagination_max_pages) || 50,
              }];
            })
        );
      }
      return data;
    },
```

Add CRUD/preview helper methods right after `namedConnectionSummary` (~line 921-924):

```js
    addApiEndpoint() {
      const idx = this.configModal.apiEndpoints.length + 1;
      this.configModal.apiEndpoints.push({
        name: `endpoint_${idx}`, base_url: '', method: 'GET',
        auth_type: 'none', api_key_header: 'X-API-Key', api_key: '',
        bearer_token: '', basic_username: '', basic_password: '',
        headers_raw: '', query_params_raw: '', body_raw: '',
        timeout: 30, verify_ssl: true,
        response_format: 'json', json_root_path: '',
        pagination_type: 'none', pagination_cursor_path: '',
        pagination_cursor_param: 'cursor', pagination_page_param: 'page',
        pagination_size_param: 'limit', pagination_page_size: 100, pagination_max_pages: 50,
        expanded: true, previewResult: null, previewError: '', testResult: null,
      });
    },

    removeApiEndpoint(idx) {
      this.configModal.apiEndpoints.splice(idx, 1);
    },

    toggleApiEndpoint(idx) {
      this.configModal.apiEndpoints[idx].expanded = !this.configModal.apiEndpoints[idx].expanded;
    },

    async testApiEndpoint(idx) {
      const m = this.configModal;
      const ep = m.apiEndpoints[idx];
      if (!m.id) { ep.testResult = { ok: false, message: 'Save the config first, then test.' }; return; }
      try {
        ep.testResult = await api('POST', '/api/adapters/rest-api/test', {
          config_id: m.id, endpoint_name: ep.name,
        });
      } catch (e) {
        ep.testResult = { ok: false, message: e.message };
      }
    },

    async previewApiEndpoint(idx) {
      const m = this.configModal;
      const ep = m.apiEndpoints[idx];
      if (!m.id) { ep.previewError = 'Save the config first, then preview.'; return; }
      ep.previewError = '';
      try {
        ep.previewResult = await api('POST', '/api/adapters/rest-api/preview', {
          config_id: m.id, endpoint_name: ep.name, limit: 20,
        });
      } catch (e) {
        ep.previewError = e.message;
      }
    },
```

- [ ] **Step 3: Add the markup**

In `frontend/index.html`, after the Named Connections `</div>` (currently closing at line 438) and before the next `<div class="divider"></div>` (line 439), insert:

```html
        <div class="divider"></div>
        <div>
          <div class="flex items-center justify-between mb-2">
            <div>
              <span class="field-label">API ENDPOINTS</span>
              <span class="text-xs text-slate-400 ml-2">REST endpoints usable as job/comparison data sources.</span>
            </div>
            <button @click="addApiEndpoint()" type="button" class="btn-secondary btn-sm text-xs">+ Add Endpoint</button>
          </div>
          <template x-for="(ep, idx) in configModal.apiEndpoints" :key="idx">
            <div class="border border-slate-200 rounded-lg mb-2 overflow-hidden">
              <div class="flex items-center gap-2 px-3 py-2 bg-slate-50 border-b border-slate-200">
                <input x-model="ep.name" class="field-input font-mono text-xs font-semibold py-1 px-2" style="width:140px" placeholder="endpoint_name" />
                <span class="text-xs text-slate-400 flex-1" x-text="ep.base_url || 'not configured'"></span>
                <span class="text-xs text-slate-400 uppercase" x-text="ep.method"></span>
                <button @click="toggleApiEndpoint(idx)" type="button" class="text-slate-400 text-xs px-1" x-text="ep.expanded ? '▲' : '▼'"></button>
                <button @click="removeApiEndpoint(idx)" type="button" class="text-red-400 text-xs px-1">✕</button>
              </div>
              <div x-show="ep.expanded" class="p-3 space-y-3">
                <div class="grid-2">
                  <div><label class="field-label">Base URL</label><input x-model="ep.base_url" class="field-input" placeholder="https://api.example.com/v1/orders" /></div>
                  <div>
                    <label class="field-label">Method</label>
                    <select x-model="ep.method" class="field-input field-select">
                      <option value="GET">GET</option>
                      <option value="POST">POST</option>
                    </select>
                  </div>
                </div>
                <div>
                  <label class="field-label">Auth Type</label>
                  <select x-model="ep.auth_type" class="field-input field-select">
                    <option value="none">None</option>
                    <option value="api_key">API Key Header</option>
                    <option value="bearer">Bearer Token</option>
                    <option value="basic">Basic Auth</option>
                  </select>
                </div>
                <div x-show="ep.auth_type === 'api_key'" class="grid-2">
                  <div><label class="field-label">Header Name</label><input x-model="ep.api_key_header" class="field-input" placeholder="X-API-Key" /></div>
                  <div><label class="field-label">API Key</label><input x-model="ep.api_key" type="password" class="field-input" /></div>
                </div>
                <div x-show="ep.auth_type === 'bearer'">
                  <label class="field-label">Bearer Token</label><input x-model="ep.bearer_token" type="password" class="field-input" />
                </div>
                <div x-show="ep.auth_type === 'basic'" class="grid-2">
                  <div><label class="field-label">Username</label><input x-model="ep.basic_username" class="field-input" /></div>
                  <div><label class="field-label">Password</label><input x-model="ep.basic_password" type="password" class="field-input" /></div>
                </div>
                <div class="grid-2">
                  <div><label class="field-label">Headers (one per line, "Name: Value")</label><textarea x-model="ep.headers_raw" rows="2" class="field-input font-mono text-xs" placeholder="Accept: application/json"></textarea></div>
                  <div><label class="field-label">Query Params (one per line, "name=value")</label><textarea x-model="ep.query_params_raw" rows="2" class="field-input font-mono text-xs" placeholder="status=active"></textarea></div>
                </div>
                <div x-show="ep.method === 'POST'">
                  <label class="field-label">Body (JSON)</label>
                  <textarea x-model="ep.body_raw" rows="2" class="field-input font-mono text-xs" placeholder='{"filter": "active"}'></textarea>
                </div>
                <div class="grid-2">
                  <div><label class="field-label">Timeout (s)</label><input x-model="ep.timeout" type="number" class="field-input" /></div>
                  <label class="flex items-center gap-2 text-sm text-slate-700 mt-6">
                    <input x-model="ep.verify_ssl" type="checkbox" class="rounded border-slate-300" /> Verify SSL certificate
                  </label>
                </div>
                <div class="grid-2">
                  <div>
                    <label class="field-label">Response Format</label>
                    <select x-model="ep.response_format" class="field-input field-select">
                      <option value="json">JSON</option>
                      <option value="csv">CSV</option>
                    </select>
                  </div>
                  <div x-show="ep.response_format === 'json'">
                    <label class="field-label">JSON Root Path</label>
                    <input x-model="ep.json_root_path" class="field-input" placeholder="data.items" />
                  </div>
                </div>
                <div>
                  <label class="field-label">Pagination</label>
                  <select x-model="ep.pagination_type" class="field-input field-select">
                    <option value="none">None</option>
                    <option value="cursor">Cursor</option>
                    <option value="page">Page / Limit</option>
                  </select>
                </div>
                <div x-show="ep.pagination_type === 'cursor'" class="grid-2">
                  <div><label class="field-label">Cursor Path</label><input x-model="ep.pagination_cursor_path" class="field-input" placeholder="meta.next_cursor" /></div>
                  <div><label class="field-label">Cursor Query Param</label><input x-model="ep.pagination_cursor_param" class="field-input" placeholder="cursor" /></div>
                </div>
                <div x-show="ep.pagination_type === 'page'" class="grid-2">
                  <div><label class="field-label">Page Param</label><input x-model="ep.pagination_page_param" class="field-input" placeholder="page" /></div>
                  <div><label class="field-label">Size Param</label><input x-model="ep.pagination_size_param" class="field-input" placeholder="limit" /></div>
                  <div><label class="field-label">Page Size</label><input x-model="ep.pagination_page_size" type="number" class="field-input" /></div>
                </div>
                <div x-show="ep.pagination_type !== 'none'">
                  <label class="field-label">Max Pages</label>
                  <input x-model="ep.pagination_max_pages" type="number" class="field-input" style="width:120px" />
                </div>
                <div class="flex items-center gap-2">
                  <button @click="testApiEndpoint(idx)" type="button" class="btn-secondary btn-sm text-xs">Test</button>
                  <button @click="previewApiEndpoint(idx)" type="button" class="btn-secondary btn-sm text-xs">Preview</button>
                  <span x-show="ep.testResult" :class="ep.testResult && ep.testResult.ok ? 'text-emerald-600' : 'text-red-600'" class="text-xs" x-text="ep.testResult ? ep.testResult.message : ''"></span>
                </div>
                <p x-show="ep.previewError" x-text="ep.previewError" class="text-xs text-red-600"></p>
                <div x-show="ep.previewResult" class="overflow-x-auto max-h-40 border border-slate-200 rounded">
                  <table class="text-xs w-full">
                    <thead class="bg-slate-100 sticky top-0">
                      <tr><template x-for="col in (ep.previewResult?.columns || [])" :key="col"><th x-text="col" class="px-2 py-1 text-left font-medium text-slate-600 whitespace-nowrap"></th></template></tr>
                    </thead>
                    <tbody>
                      <template x-for="(row, ri) in (ep.previewResult?.rows || [])" :key="ri">
                        <tr :class="ri % 2 === 0 ? 'bg-white' : 'bg-slate-50'">
                          <template x-for="(cell, ci) in row" :key="ci"><td x-text="cell ?? 'NULL'" class="px-2 py-1 whitespace-nowrap max-w-xs truncate"></td></template>
                        </tr>
                      </template>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </template>
        </div>
```

- [ ] **Step 4: Manually verify in the browser**

Start the app (check `README.md` or an existing project skill for the run command — e.g. `uvicorn api.main:app --reload` if no dedicated script exists), open the Configuration screen, create/edit a config, and confirm:
- "+ Add Endpoint" adds a new collapsible card
- Auth-type selection reveals the matching credential fields
- Saving persists the endpoint (reopen the config and confirm fields survive)
- Secrets show as `********` after save/reload
- "Test" and "Preview" buttons call the new adapter endpoints (check the Network tab) and render a result/error

- [ ] **Step 5: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat: add API Endpoints section to the config modal"
```

---

## Task 17: Frontend — Job modal `api_reconciliation` fields

**Files:**
- Modify: `frontend/app.js` (`openNewJobModal`, `openEditJobModal`, `saveJob`, `canSaveJob`)
- Modify: `frontend/index.html` (job type `<select>`, new conditional fields block)

- [ ] **Step 1: Add job-type option and modal state**

In `frontend/index.html`, add an option to the Job Type `<select>` (currently lines 809-818):

```html
          <select x-model="jobModal.job_type" class="field-input field-select">
            <option value="reconciliation">reconciliation</option>
            <option value="bo_report">bo_report</option>
            <option value="automic_job">automic_job</option>
            <option value="dbt_artifact">dbt_artifact</option>
            <option value="freshness">freshness</option>
            <option value="schema_snapshot">schema_snapshot</option>
            <option value="profile">profile</option>
            <option value="cross_job_assertion">cross_job_assertion</option>
            <option value="api_reconciliation">api_reconciliation</option>
          </select>
```

In `frontend/app.js`, in `openNewJobModal()` (~line 1000-1025), add fields after `automic_job_name: '', automic_run_id: '',`:

```js
        automic_job_name: '', automic_run_id: '',
        api_source_endpoint: '', api_target_endpoint: '',
```

In `openEditJobModal(job)` (~line 1034-1073), add after `automic_run_id: job.params?.run_id || '',`:

```js
        automic_run_id: job.params?.run_id || '',
        api_source_endpoint: job.params?.source_api_endpoint || '',
        api_target_endpoint: job.params?.target_api_endpoint || '',
```

- [ ] **Step 2: Add the field markup**

In `frontend/index.html`, right after the `automic_job` fields block (currently lines 907-916, ending `</div>` before `dbt_artifact`), insert:

```html
        <div x-show="jobModal.job_type === 'api_reconciliation'" class="grid-2">
          <div>
            <label class="field-label">Source API Endpoint</label>
            <input x-model="jobModal.api_source_endpoint" class="field-input" placeholder="orders_api" />
          </div>
          <div>
            <label class="field-label">Target API Endpoint</label>
            <input x-model="jobModal.api_target_endpoint" class="field-input" placeholder="orders_api_v2" />
          </div>
          <p class="text-xs text-slate-400 col-span-2">
            Endpoint names must match entries in the api_endpoints section of whichever config is selected when this job runs.
          </p>
        </div>
```

Also extend the key-columns field visibility (currently line 885 `<div x-show="['reconciliation','bo_report'].includes(jobModal.job_type)">`) so `api_reconciliation` shows Key Columns too:

```html
        <div x-show="['reconciliation','bo_report','api_reconciliation'].includes(jobModal.job_type)">
          <label class="field-label">Key Columns (comma-separated)</label>
          <input x-model="jobModal.key_columns_raw" class="field-input" placeholder="id" @input="validateJobModal()" />
        </div>
```

- [ ] **Step 3: Wire save/validation logic**

In `frontend/app.js`, in `saveJob()` (~line 1137-1210), add a new `if` block after the `automic_job` block:

```js
      if (m.job_type === 'automic_job') {
        if (m.automic_job_name) params.job_name = m.automic_job_name;
        if (m.automic_run_id) params.run_id = m.automic_run_id;
      }
      if (m.job_type === 'api_reconciliation') {
        params.source_api_endpoint = m.api_source_endpoint;
        params.target_api_endpoint = m.api_target_endpoint;
      }
```

Update the `keyColumns` computation (currently line 1175):

```js
      const keyColumns = ['reconciliation', 'bo_report', 'api_reconciliation'].includes(m.job_type)
        ? m.key_columns_raw.split(',').map(s => s.trim()).filter(Boolean)
        : [];
```

In `canSaveJob()` (~line 1212-1226), add a check after the `automic_job` line:

```js
      if (m.job_type === 'automic_job') return Boolean(m.automic_job_name || m.automic_run_id);
      if (m.job_type === 'api_reconciliation') {
        return Boolean(
          m.api_source_endpoint && m.api_target_endpoint &&
          m.key_columns_raw?.split(',').map(s => s.trim()).filter(Boolean).length
        );
      }
```

- [ ] **Step 4: Manually verify in the browser**

Open the Jobs screen, create a new job, select "api_reconciliation" as the job type, and confirm:
- Source/Target API Endpoint fields and Key Columns field appear
- "Save" is disabled until both endpoint names and at least one key column are filled in
- Saving creates the job and `GET /api/jobs` shows `job_type: "api_reconciliation"` with `params.source_api_endpoint`/`params.target_api_endpoint` set
- Editing the job re-populates the fields correctly

- [ ] **Step 5: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat: add api_reconciliation fields to the job modal"
```

---

## Task 18: Frontend — Compare tab (BO/recon) `api` source type

**Files:**
- Modify: `frontend/app.js` (`_buildBOSource`, new `apiEndpointNames` helper, `loadCompareApiEndpoints`-less design since endpoints come straight from `configs`)
- Modify: `frontend/index.html` (Source A / Source B pill rows + conditional block in the `compareSubTab === 'bo'` panel)

- [ ] **Step 1: Add an `api` branch to `_buildBOSource`**

In `frontend/app.js`, replace `_buildBOSource` (currently lines 1917-1929):

```js
    _buildBOSource(type, src) {
      if (type === 'live') {
        return {
          source_type: 'live',
          config_id: Number(src.configId),
          doc_id: src.docId || null,
          report_id: src.reportId || null,
          format: 'xlsx',
        };
      }
      if (type === 'path') return { source_type: 'path', file_path: src.filePath };
      if (type === 'api') {
        return {
          source_type: 'api',
          config_id: Number(src.configId),
          api_endpoint_name: src.endpointName,
        };
      }
      return { source_type: 'upload', file_content_b64: src.fileB64, file_name: src.fileName };
    },
```

Add an `endpointName` field to the source state objects. In the state declarations (~line 390-391, 484-485), change all four:

```js
    boSourceA: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source A', endpointName: '' },
    boSourceB: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source B', endpointName: '' },
```

and

```js
    colStatsSourceA: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source A', endpointName: '' },
    colStatsSourceB: { configId: '', docId: '', reportId: '', filePath: '', fileB64: '', fileName: '', label: 'Source B', endpointName: '' },
```

Add a helper method next to `launchConfigConnections()` (~line 1300-1304):

```js
    configApiEndpointNames(configId) {
      const cfg = this.configs.find(c => String(c.id) === String(configId));
      if (!cfg || !cfg.config_data || !cfg.config_data.api_endpoints) return [];
      return Object.keys(cfg.config_data.api_endpoints);
    },
```

- [ ] **Step 2: Add the pill option and conditional block to the BO/recon tab**

In `frontend/index.html`, in the Source A block (currently lines 2748-2775), add an "API" pill after "Upload" and a new conditional block:

```html
        <div class="mode-row mb-3">
          <button @click="boSourceAType = 'live'" :class="boSourceAType === 'live' ? 'pill active' : 'pill'">Live</button>
          <button @click="boSourceAType = 'path'" :class="boSourceAType === 'path' ? 'pill active' : 'pill'">Path</button>
          <button @click="boSourceAType = 'upload'" :class="boSourceAType === 'upload' ? 'pill active' : 'pill'">Upload</button>
          <button @click="boSourceAType = 'api'" :class="boSourceAType === 'api' ? 'pill active' : 'pill'">API</button>
        </div>
        <input x-model="boSourceA.label" class="field-input mb-2" placeholder="Source A label" />
        <template x-if="boSourceAType === 'live'">
          <div class="space-y-2">
            <select x-model="boSourceA.configId" @change="loadCompareBODocuments('a')" class="field-input field-select">
              <option value="">Select config</option>
              <template x-for="cfg in configs" :key="cfg.id"><option :value="cfg.id" x-text="cfg.name"></option></template>
            </select>
            <select x-model="boSourceA.docId" @change="loadCompareBOReports('a')" class="field-input field-select">
              <option value="">Select document</option>
              <template x-for="d in boDocsA" :key="d.id"><option :value="d.id" x-text="d.name"></option></template>
            </select>
            <select x-model="boSourceA.reportId" class="field-input field-select">
              <option value="">Select report</option>
              <template x-for="r in boReportsA" :key="r.id"><option :value="r.id" x-text="r.name"></option></template>
            </select>
          </div>
        </template>
        <template x-if="boSourceAType === 'path'">
          <input x-model="boSourceA.filePath" class="field-input" placeholder="C:\reports\a.csv" />
        </template>
        <template x-if="boSourceAType === 'upload'">
          <input type="file" accept=".csv,.xlsx,.xls" @change="handleBOFileUpload($event, 'a')" class="field-input" />
        </template>
        <template x-if="boSourceAType === 'api'">
          <div class="space-y-2">
            <select x-model="boSourceA.configId" class="field-input field-select">
              <option value="">Select config</option>
              <template x-for="cfg in configs" :key="cfg.id"><option :value="cfg.id" x-text="cfg.name"></option></template>
            </select>
            <select x-model="boSourceA.endpointName" class="field-input field-select">
              <option value="">Select endpoint</option>
              <template x-for="name in configApiEndpointNames(boSourceA.configId)" :key="name"><option :value="name" x-text="name"></option></template>
            </select>
          </div>
        </template>
      </div>
```

Apply the mirror-image change to the Source B block (currently lines 2780-2812ish — same structure with `boSourceB`/`boSourceBType`/`boDocsB`/`boReportsB`).

- [ ] **Step 3: Manually verify in the browser**

Open Compare → BO/Recon tab, select "API" for Source A, pick a config that has API endpoints configured, pick an endpoint, do the same for Source B (or use Upload), and run a comparison. Confirm the request payload sent to `/api/compare/bo` has `source_a.source_type === "api"` with `config_id`/`api_endpoint_name` set (check the Network tab), and that the run completes.

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat: add API source type to the Compare tab (BO/recon)"
```

---

## Task 19: Frontend — Column Stats compare tab `api` source type

**Files:**
- Modify: `frontend/index.html` (Column Stats source pickers, currently lines 3586-3640ish)

This reuses `_buildBOSource` and `configApiEndpointNames` from Task 18 — only markup changes here.

- [ ] **Step 1: Add the `api` option and conditional block**

In `frontend/index.html`, in the Source A `<select>` (currently lines 3591-3595):

```html
          <select x-model="colStatsSourceAType" class="field-input field-select mb-2">
            <option value="upload">Upload file</option>
            <option value="live">Live BO</option>
            <option value="path">File path</option>
            <option value="api">API endpoint</option>
          </select>
```

Add a new conditional block after the `live` block (currently lines 3605-3614):

```html
          <template x-if="colStatsSourceAType === 'api'">
            <div class="space-y-2">
              <select x-model="colStatsSourceA.configId" class="field-input field-select">
                <option value="">Select config</option>
                <template x-for="cfg in configs" :key="cfg.id"><option :value="cfg.id" x-text="cfg.name"></option></template>
              </select>
              <select x-model="colStatsSourceA.endpointName" class="field-input field-select">
                <option value="">Select endpoint</option>
                <template x-for="name in configApiEndpointNames(colStatsSourceA.configId)" :key="name"><option :value="name" x-text="name"></option></template>
              </select>
            </div>
          </template>
```

Apply the mirror-image change to the Source B `<select>` and block (currently lines 3621-3625, and after 3635-3639).

- [ ] **Step 2: Manually verify in the browser**

Open Compare → Column Stats tab, select "API endpoint" for Source A, pick a config + endpoint, run a comparison, and confirm the payload/behavior matches Task 18's verification.

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat: add API source type to the Column Stats compare tab"
```

---

## Final Verification

- [ ] Run the full backend test suite: `python -m pytest tests/ etl_framework/ -v`
- [ ] Expected: all tests passing, no regressions
- [ ] Manually smoke-test the three integration points end-to-end in the browser: create a config with an API endpoint pointing at a real or mock JSON API, create an `api_reconciliation` job referencing it, run it with `use_live_connections: true`, and separately run a Compare-tab comparison using the "API" source type.
