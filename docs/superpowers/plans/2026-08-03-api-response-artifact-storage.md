# API Response Artifact Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the raw bytes of every REST API endpoint pull under the server's existing artifact root, so API runs keep what they fetched and a failed pull leaves the response behind as evidence.

**Architecture:** `APIEndpointClient.fetch_dataframe` gains an optional `on_response` callback and passes it to `_request`, which invokes it right after a response arrives and *before* the `status_code >= 400` check — so 4xx and 5xx bodies are captured too. The client never touches the filesystem: `etl_framework/` must not import `api/services/`. A new `api/services/api_artifact.py` builds the sink that writes files, reusing `upload_store`'s root, size cap, filename sanitiser and retention sweep.

**Tech Stack:** Python 3, `requests`, `pandas`, `pytest`, `unittest.mock`.

Spec: `docs/superpowers/specs/2026-08-03-api-response-artifact-storage-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `api/services/upload_store.py` (modify) | Owns `UPLOAD_ROOT`, the size cap and filename safety. Gains a public `safe_filename` and a reusable `unique_path`. |
| `api/services/api_artifact.py` (create) | Builds the response sink: filename derivation, destination directories, best-effort writes. The only new module. |
| `etl_framework/rest_api/client.py` (modify) | Threads `on_response` from `fetch_dataframe` into `_request` and invokes it. No filesystem knowledge. |
| `api/services/adapter_service.py` (modify) | Test/Preview pass an ad-hoc-directory sink. |
| `api/services/compare_service.py` (modify) | Threads `run_id` down to `_load_api_source`; passes a run or ad-hoc sink. |
| `api/services/run_executor.py` (modify) | `api_reconciliation` job passes a run-directory sink per side. |
| `api/services/difference_export.py` (modify) | Explicitly passes no sink, with a comment saying why. |
| `tests/unit/test_api_artifact.py` (create) | Filename derivation, traversal safety, caps, error swallowing, ad-hoc directory. |
| `tests/unit/test_rest_api_client.py` (modify) | Sink ordering: fires on 4xx, on parse failure, not on connection error. |

---

### Task 1: Make `upload_store` helpers reusable

`api_artifact` needs the filename sanitiser and the collision-suffix loop that
are currently private and welded into `_persist_bytes`.

**Files:**
- Modify: `api/services/upload_store.py:25-44`
- Test: `tests/unit/test_api_artifact.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_artifact.py`:

```python
from __future__ import annotations

from api.services.upload_store import safe_filename, unique_path


def test_safe_filename_strips_traversal():
    assert safe_filename("../../../etc/passwd", "fallback") == "passwd"


def test_safe_filename_strips_windows_path():
    assert safe_filename(r"C:\Windows\System32\evil.dll", "fallback") == "evil.dll"


def test_safe_filename_falls_back_when_everything_stripped():
    assert safe_filename("...", "fallback.bin") == "fallback.bin"


def test_unique_path_suffixes_on_collision(tmp_path):
    (tmp_path / "page.json").write_bytes(b"{}")
    assert unique_path(tmp_path, "page.json").name == "page_2.json"


def test_unique_path_returns_name_when_free(tmp_path):
    assert unique_path(tmp_path, "page.json").name == "page.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_api_artifact.py -v`
Expected: FAIL with `ImportError: cannot import name 'safe_filename'`

- [ ] **Step 3: Write minimal implementation**

In `api/services/upload_store.py`, rename `_safe_filename` to `safe_filename`,
add `unique_path`, and make `_persist_bytes` use both. Replace lines 25-44 with:

```python
def safe_filename(name: str | None, fallback: str) -> str:
    raw = Path(name or fallback).name or fallback
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return (safe or fallback)[:160]


# Kept so any external caller of the old private name keeps working.
_safe_filename = safe_filename


def unique_path(directory: Path, name: str) -> Path:
    """A path under `directory` that does not exist yet, suffixing _2, _3, ..."""
    path = directory / name
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    idx = 2
    while path.exists():
        path = directory / f"{stem}_{idx}{suffix}"
        idx += 1
    return path


def _persist_bytes(run_id: str, data: bytes, filename: str | None, fallback: str) -> str:
    run_dir = UPLOAD_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = unique_path(run_dir, safe_filename(filename, fallback))
    path.write_bytes(data)
    return str(path)
```

Note the `safe_filename("...", "fallback.bin")` case: `Path("...").name` is
`"..."`, the regex leaves it alone, `.strip("._")` empties it, so the fallback is
returned. That is why the strip happens before the fallback check.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api_artifact.py -v`
Expected: 5 passed

Run: `python -m pytest tests/unit/ -q -k "upload or compare_api or run_executor"`
Expected: no new failures — `_persist_bytes` behaviour is unchanged.

- [ ] **Step 5: Commit**

```bash
git add api/services/upload_store.py tests/unit/test_api_artifact.py
git commit -m "refactor(uploads): make filename safety and collision suffixing reusable"
```

---

### Task 2: Filename derivation

**Files:**
- Create: `api/services/api_artifact.py`
- Test: `tests/unit/test_api_artifact.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_api_artifact.py`:

```python
from unittest.mock import MagicMock

from api.services.api_artifact import artifact_filename

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _resp(content_type="application/json", disposition=None):
    resp = MagicMock()
    headers = {"Content-Type": content_type}
    if disposition is not None:
        headers["Content-Disposition"] = disposition
    resp.headers = headers
    return resp


def test_filename_from_content_type_json():
    assert artifact_filename(_resp(), "orders", 1) == "orders_p1.json"


def test_filename_from_content_type_csv():
    assert artifact_filename(_resp("text/csv"), "orders", 2) == "orders_p2.csv"


def test_filename_from_content_type_xlsx():
    assert artifact_filename(_resp(_XLSX), "orders", 1) == "orders_p1.xlsx"


def test_filename_unknown_content_type_is_bin():
    assert artifact_filename(_resp("application/octet-stream"), "orders", 1) == "orders_p1.bin"


def test_filename_ignores_charset_parameter():
    assert artifact_filename(_resp("application/json; charset=utf-8"), "orders", 1) == "orders_p1.json"


def test_content_disposition_filename_wins():
    resp = _resp(_XLSX, 'attachment; filename="Q3 report.xlsx"')
    assert artifact_filename(resp, "orders", 1) == "Q3_report.xlsx"


def test_content_disposition_traversal_is_neutralised():
    resp = _resp("application/json", 'attachment; filename="../../../etc/passwd"')
    assert artifact_filename(resp, "orders", 1) == "passwd"


def test_content_disposition_absolute_windows_path_is_neutralised():
    resp = _resp("application/json", r'attachment; filename="C:\Windows\evil.dll"')
    assert artifact_filename(resp, "orders", 1) == "evil.dll"


def test_endpoint_name_with_separators_is_neutralised():
    assert artifact_filename(_resp(), "../../orders", 1) == "orders_p1.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_api_artifact.py -v -k filename or disposition`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.services.api_artifact'`

- [ ] **Step 3: Write minimal implementation**

Create `api/services/api_artifact.py`:

```python
"""Persist raw REST API endpoint responses under the server's artifact root.

The HTTP client itself stays filesystem-agnostic: `etl_framework/` must not
import `api/services/`. This module builds the callback the client invokes per
response, so layout, size caps and retention stay in the layer that owns them.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from api.services.upload_store import (
    RUN_DATA_ARTIFACT_MAX_BYTES,
    UPLOAD_ROOT,
    safe_filename,
    unique_path,
)

logger = logging.getLogger("api.services.api_artifact")

_EXT_BY_CONTENT_TYPE = {
    "application/json": ".json",
    "text/json": ".json",
    "text/csv": ".csv",
    "application/csv": ".csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "text/plain": ".txt",
}

_FILENAME_RE = re.compile(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", re.IGNORECASE)


def _disposition_filename(response) -> str | None:
    raw = (getattr(response, "headers", None) or {}).get("Content-Disposition") or ""
    match = _FILENAME_RE.search(raw)
    return match.group(1).strip() if match else None


def _extension_for(response) -> str:
    raw = (getattr(response, "headers", None) or {}).get("Content-Type") or ""
    return _EXT_BY_CONTENT_TYPE.get(raw.split(";")[0].strip().lower(), ".bin")


def artifact_filename(response, endpoint_name: str, page_number: int) -> str:
    """Name for one stored response.

    A `Content-Disposition` filename is chosen by the remote server and is
    therefore hostile input: it goes through `safe_filename`, which reduces it
    to a basename and strips everything outside [A-Za-z0-9._-].
    """
    disposition = _disposition_filename(response)
    if disposition:
        return safe_filename(disposition, f"page_p{page_number}.bin")
    safe_endpoint = safe_filename(endpoint_name, "endpoint")
    return safe_filename(
        f"{safe_endpoint}_p{page_number}{_extension_for(response)}",
        f"page_p{page_number}.bin",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api_artifact.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add api/services/api_artifact.py tests/unit/test_api_artifact.py
git commit -m "feat(api): derive artifact filenames from content type and disposition"
```

---

### Task 3: Destination directories

**Files:**
- Modify: `api/services/api_artifact.py`
- Test: `tests/unit/test_api_artifact.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_api_artifact.py`:

```python
from datetime import datetime, timezone

from api.services.api_artifact import adhoc_artifact_dir, run_artifact_dir
from api.services.upload_store import UPLOAD_ROOT

_WHEN = datetime(2026, 8, 3, 21, 14, 8, tzinfo=timezone.utc)


def test_adhoc_dir_is_direct_child_of_upload_root():
    path = adhoc_artifact_dir(3, "orders", now=_WHEN)
    assert path.parent == UPLOAD_ROOT
    assert path.name == "adhoc_3_orders_20260803T211408Z"


def test_adhoc_dir_neutralises_endpoint_name():
    path = adhoc_artifact_dir(3, "../../evil", now=_WHEN)
    assert path.parent == UPLOAD_ROOT
    assert ".." not in path.name


def test_run_dir_is_direct_child_of_upload_root():
    path = run_artifact_dir("run-abc")
    assert path.parent == UPLOAD_ROOT
    assert path.name == "run-abc"
```

The direct-child assertion is the point: `cleanup_expired_uploads` only sweeps
direct children of `UPLOAD_ROOT`, so a nested directory would never be cleaned.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_api_artifact.py -v -k dir`
Expected: FAIL with `ImportError: cannot import name 'adhoc_artifact_dir'`

- [ ] **Step 3: Write minimal implementation**

Append to `api/services/api_artifact.py`:

```python
def run_artifact_dir(run_id: str) -> Path:
    return UPLOAD_ROOT / safe_filename(run_id, "run")


def adhoc_artifact_dir(config_id: int, endpoint_name: str, now: datetime | None = None) -> Path:
    """Directory for a pull with no run behind it (Test, Preview, column stats).

    Deliberately a direct child of UPLOAD_ROOT: `cleanup_expired_uploads`
    iterates direct children only, so this is swept by the existing retention
    with no new code.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    safe_endpoint = safe_filename(endpoint_name, "endpoint")
    return UPLOAD_ROOT / safe_filename(
        f"adhoc_{int(config_id)}_{safe_endpoint}_{stamp}", f"adhoc_{stamp}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api_artifact.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add api/services/api_artifact.py tests/unit/test_api_artifact.py
git commit -m "feat(api): destination directories for run-scoped and ad-hoc pulls"
```

---

### Task 4: The sink itself

**Files:**
- Modify: `api/services/api_artifact.py`
- Test: `tests/unit/test_api_artifact.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_api_artifact.py`:

```python
import pytest

from api.services import api_artifact
from api.services.api_artifact import build_api_response_sink


def test_sink_writes_the_bytes(tmp_path):
    sink = build_api_response_sink(tmp_path, "orders")
    sink(b'{"a":1}', 1, _resp())
    assert (tmp_path / "orders_p1.json").read_bytes() == b'{"a":1}'


def test_sink_creates_the_directory(tmp_path):
    dest = tmp_path / "nested" / "dir"
    sink = build_api_response_sink(dest, "orders")
    sink(b"{}", 1, _resp())
    assert (dest / "orders_p1.json").exists()


def test_sink_suffixes_on_collision(tmp_path):
    sink = build_api_response_sink(tmp_path, "orders")
    sink(b"a", 1, _resp())
    sink(b"b", 1, _resp())
    assert (tmp_path / "orders_p1.json").read_bytes() == b"a"
    assert (tmp_path / "orders_p1_2.json").read_bytes() == b"b"


def test_sink_skips_over_cap_payload(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(api_artifact, "RUN_DATA_ARTIFACT_MAX_BYTES", 4)
    sink = build_api_response_sink(tmp_path, "orders")
    with caplog.at_level("WARNING"):
        sink(b"way too long", 1, _resp())
    assert list(tmp_path.iterdir()) == []
    assert "cap" in caplog.text.lower()


def test_sink_swallows_write_errors(tmp_path, monkeypatch):
    sink = build_api_response_sink(tmp_path, "orders")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", boom)
    sink(b"{}", 1, _resp())  # must not raise


def test_sink_swallows_directory_errors(tmp_path, monkeypatch):
    sink = build_api_response_sink(tmp_path / "x", "orders")

    def boom(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "mkdir", boom)
    sink(b"{}", 1, _resp())  # must not raise
```

Add `from pathlib import Path` to the test file's imports if not already there.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_api_artifact.py -v -k sink`
Expected: FAIL with `ImportError: cannot import name 'build_api_response_sink'`

- [ ] **Step 3: Write minimal implementation**

Append to `api/services/api_artifact.py`:

```python
def build_api_response_sink(dest_dir: Path, endpoint_name: str) -> Callable:
    """A callback the HTTP client invokes per response, writing it to disk.

    Best-effort by contract: over-cap payloads are skipped and every error is
    swallowed. A pull that already succeeded must never fail because a file
    could not be written.
    """
    def sink(raw_bytes: bytes, page_number: int, response) -> None:
        try:
            if len(raw_bytes) > RUN_DATA_ARTIFACT_MAX_BYTES:
                logger.warning(
                    "API response for %s page %d is %d bytes, past the %d-byte cap "
                    "— not persisted",
                    endpoint_name, page_number, len(raw_bytes),
                    RUN_DATA_ARTIFACT_MAX_BYTES,
                )
                return
            dest_dir.mkdir(parents=True, exist_ok=True)
            path = unique_path(dest_dir, artifact_filename(response, endpoint_name, page_number))
            path.write_bytes(raw_bytes)
        except Exception:  # noqa: BLE001 - storage must never break a pull
            logger.warning(
                "Could not persist API response for %s page %d",
                endpoint_name, page_number, exc_info=True,
            )

    return sink
```

`RUN_DATA_ARTIFACT_MAX_BYTES` must be read through the module global at call
time for the monkeypatch test to work — it is, because the reference inside
`sink` resolves at call time against the module namespace.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api_artifact.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add api/services/api_artifact.py tests/unit/test_api_artifact.py
git commit -m "feat(api): best-effort sink that stores raw API responses"
```

---

### Task 5: Thread the callback through the HTTP client

This is the task where ordering matters. The callback fires inside `_request`,
before the status check, or every 4xx and 5xx body is lost.

**Files:**
- Modify: `etl_framework/rest_api/client.py:19-58` and `:135-153`
- Test: `tests/unit/test_rest_api_client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_rest_api_client.py`:

```python
import requests

from etl_framework.rest_api.client import APIEndpointClient


def _record_sink():
    seen = []

    def sink(raw_bytes, page_number, response):
        seen.append((raw_bytes, page_number, response.status_code))

    return sink, seen


def test_sink_fires_on_success():
    client = APIEndpointClient(_entry())
    sink, seen = _record_sink()
    with patch.object(client._session, "request", return_value=_fake_response(json_data=[{"id": 1}])):
        client.fetch_dataframe(on_response=sink)
    assert len(seen) == 1
    assert seen[0][1] == 1


def test_sink_fires_before_the_status_check():
    """A 500 body is the most valuable one to keep; _request raises on it."""
    client = APIEndpointClient(_entry())
    sink, seen = _record_sink()
    with patch.object(client._session, "request", return_value=_fake_response(status_code=500, text="boom")):
        with pytest.raises(APIRequestError):
            client.fetch_dataframe(on_response=sink)
    assert [status for _, _, status in seen] == [500]
    assert seen[0][0] == b"boom"


def test_sink_fires_when_json_parsing_fails():
    client = APIEndpointClient(_entry())
    sink, seen = _record_sink()
    with patch.object(client._session, "request", return_value=_fake_response(text="<html>nope</html>")):
        with pytest.raises(APIRequestError):
            client.fetch_dataframe(on_response=sink)
    assert seen[0][0] == b"<html>nope</html>"


def test_sink_does_not_fire_when_no_response_exists():
    client = APIEndpointClient(_entry())
    sink, seen = _record_sink()
    with patch.object(client._session, "request", side_effect=requests.exceptions.ConnectionError("no route")):
        with pytest.raises(APIRequestError):
            client.fetch_dataframe(on_response=sink)
    assert seen == []


def test_sink_numbers_pages_for_cursor_pagination():
    entry = _entry(pagination_type="cursor", pagination_cursor_path="next")
    client = APIEndpointClient(entry)
    sink, seen = _record_sink()
    first = _fake_response(json_data={"next": "abc", "rows": []})
    second = _fake_response(json_data={"rows": []})
    with patch.object(client._session, "request", side_effect=[first, second]):
        client.fetch_dataframe(on_response=sink)
    assert [page for _, page, _ in seen] == [1, 2]


def test_a_broken_sink_cannot_break_a_pull():
    client = APIEndpointClient(_entry())

    def boom(*args, **kwargs):
        raise RuntimeError("sink is broken")

    with patch.object(client._session, "request", return_value=_fake_response(json_data=[{"id": 1}])):
        df = client.fetch_dataframe(on_response=boom)
    assert len(df) == 1


def test_omitting_the_sink_keeps_the_old_behaviour():
    client = APIEndpointClient(_entry())
    with patch.object(client._session, "request", return_value=_fake_response(json_data=[{"id": 1}])):
        df = client.fetch_dataframe()
    assert len(df) == 1
```

`test_sink_numbers_pages_for_cursor_pagination` is the one that catches the real
trap: the existing `page_number` variable is only maintained for
`pagination_type == "page"`, so reusing it would label every cursor page `1`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_rest_api_client.py -v -k sink`
Expected: FAIL with `TypeError: fetch_dataframe() got an unexpected keyword argument 'on_response'`

- [ ] **Step 3: Write minimal implementation**

In `etl_framework/rest_api/client.py`, change the `fetch_dataframe` signature
and loop (lines 19-58) to:

```python
    def fetch_dataframe(self, max_pages: int | None = None, on_response=None) -> pd.DataFrame:
        """Fetch every page and return one frame.

        `on_response(raw_bytes, page_number, response)` is invoked once per
        response received, before the status check, so a 4xx or 5xx body
        reaches the caller too. It must never raise; the client guards it
        anyway so a caller-supplied callback cannot break a working pull.
        """
        entry = self._entry
        page_cap = max_pages if max_pages is not None else entry.pagination_max_pages
        frames: list[pd.DataFrame] = []
        query_params = dict(entry.query_params)
        url = entry.base_url
        page_number = 1
        page_index = 1

        try:
            for _ in range(page_cap):
                if entry.pagination_type == "page":
                    query_params[entry.pagination_page_param] = page_number
                    query_params[entry.pagination_size_param] = entry.pagination_page_size

                response = self._request(url, query_params, page_index, on_response)
                frame = self._parse_response(response)
                frames.append(frame)
                page_index += 1

                if entry.pagination_type == "none":
                    break
                if entry.pagination_type == "page":
                    if len(frame) < entry.pagination_page_size:
                        break
                    page_number += 1
                    continue
                if entry.pagination_type == "cursor":
                    cursor_value = self._extract_cursor(response)
                    if not cursor_value:
                        break
                    if urlparse(cursor_value).scheme:
                        url = cursor_value
                        query_params = {}
                    else:
                        query_params[entry.pagination_cursor_param] = cursor_value

            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True)
        finally:
            self._logout_sap_bo()
```

And `_request` (lines 135-153) to:

```python
    def _request(self, url: str, query_params: dict, page_index: int = 1, on_response=None) -> requests.Response:
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
        # Before the status check on purpose: an error response body is the
        # most useful one to keep, and raising first would discard it.
        if on_response is not None:
            try:
                on_response(response.content, page_index, response)
            except Exception:  # noqa: BLE001 - an observer cannot break a pull
                pass
        if response.status_code >= 400:
            body = response.text[:1000] if response.text else ""
            raise APIRequestError(url=url, http_status=response.status_code, message=body)
        return response
```

`_get_sap_bo_token` is left untouched: it gets no sink, because its body is a
plaintext password.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_rest_api_client.py -v`
Expected: all passed, including the pre-existing tests

- [ ] **Step 5: Commit**

```bash
git add etl_framework/rest_api/client.py tests/unit/test_rest_api_client.py
git commit -m "feat(rest-api): optional per-response callback, fired before the status check"
```

---

### Task 6: Wire Test and Preview to the ad-hoc directory

**Files:**
- Modify: `api/services/adapter_service.py:108-134`
- Test: `tests/unit/test_adapter_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_adapter_service.py`:

```python
def test_test_api_endpoint_passes_an_adhoc_sink(monkeypatch):
    from api.services import adapter_service

    captured = {}

    class FakeClient:
        def __init__(self, entry):
            pass

        def fetch_dataframe(self, max_pages=None, on_response=None):
            captured["on_response"] = on_response
            import pandas as pd
            return pd.DataFrame()

    monkeypatch.setattr(adapter_service, "APIEndpointClient", FakeClient)
    monkeypatch.setattr(
        adapter_service.AdapterService, "_get_api_endpoint",
        lambda self, cfg, name: __import__(
            "etl_framework.config.models", fromlist=["ApiEndpointEntry"]
        ).ApiEndpointEntry(name=name, base_url="https://x.example.com/a"),
    )
    service = adapter_service.AdapterService(db=None)
    out = service.test_api_endpoint(1, "orders")
    assert out.ok is True
    assert callable(captured["on_response"])
```

Check the real `AdapterService.__init__` signature in
`api/services/adapter_service.py` and construct it the way the neighbouring
tests in this file already do — match them rather than the sketch above if they
differ.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_adapter_service.py -v -k adhoc_sink`
Expected: FAIL — `captured["on_response"]` is `None`

- [ ] **Step 3: Write minimal implementation**

In `api/services/adapter_service.py`, add the import at the top:

```python
from api.services.api_artifact import adhoc_artifact_dir, build_api_response_sink
```

Replace line 112:

```python
            APIEndpointClient(entry).fetch_dataframe(
                max_pages=1,
                on_response=build_api_response_sink(
                    adhoc_artifact_dir(config_id, endpoint_name), endpoint_name
                ),
            )
```

Replace line 129:

```python
            df = APIEndpointClient(entry).fetch_dataframe(
                max_pages=1,
                on_response=build_api_response_sink(
                    adhoc_artifact_dir(config_id, endpoint_name), endpoint_name
                ),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_adapter_service.py tests/unit/test_adapters_routes.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add api/services/adapter_service.py tests/unit/test_adapter_service.py
git commit -m "feat(api): store Test and Preview responses in an ad-hoc artifact directory"
```

---

### Task 7: Wire the compare source

**Files:**
- Modify: `api/services/compare_service.py:138-139`, `:211`, `:249`, `:256-266`, `:757-758`
- Modify: `api/services/difference_export.py:525-526`
- Test: `tests/unit/test_compare_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_compare_api.py`:

```python
def test_load_api_source_uses_the_run_directory(monkeypatch, tmp_path):
    import pandas as pd
    from api.services import compare_service as cs

    captured = {}

    class FakeClient:
        def __init__(self, entry):
            pass

        def fetch_dataframe(self, max_pages=None, on_response=None):
            captured["on_response"] = on_response
            return pd.DataFrame({"id": [1]})

    monkeypatch.setattr(cs, "APIEndpointClient", FakeClient, raising=False)
    monkeypatch.setattr(
        "etl_framework.rest_api.client.APIEndpointClient", FakeClient
    )
    monkeypatch.setattr(
        cs, "build_api_response_sink",
        lambda dest, name: captured.setdefault("dest", dest) or (lambda *a: None),
    )
    ...
```

Because `_load_api_source` imports `APIEndpointClient` inside the function body
(line 261), patch `etl_framework.rest_api.client.APIEndpointClient`, not the
`compare_service` attribute. Finish this test by constructing a `SourceConfig`
with `source_type="api"` the way the existing tests in this file build compare
requests, calling `CompareService(db, repo)._load_api_source(src, "run-abc")`,
and asserting `captured["dest"].name == "run-abc"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_compare_api.py -v -k run_directory`
Expected: FAIL with `TypeError: _load_api_source() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: Write minimal implementation**

In `api/services/compare_service.py`, add near the other imports:

```python
from api.services.api_artifact import (
    adhoc_artifact_dir,
    build_api_response_sink,
    run_artifact_dir,
)
```

Line 138-139 becomes:

```python
            df_a = self._load_bo_source(req.source_a, req.doc_id, req.report_id, run_id)
            df_b = self._load_bo_source(req.source_b, req.doc_id, req.report_id, run_id)
```

Line 211 signature becomes:

```python
    def _load_bo_source(self, src, fallback_doc_id: str | None, fallback_report_id: str | None,
                        run_id: str | None = None):
```

Line 249 becomes:

```python
            return self._load_api_source(src, run_id)
```

Lines 256-266 become:

```python
    def _load_api_source(self, src, run_id: str | None = None) -> pd.DataFrame:
        cfg = self._config_repo.get(src.config_id)
        if cfg is None:
            raise HTTPException(status_code=404, detail="Config not found")
        from etl_framework.config.models import resolve_api_endpoint
        from etl_framework.rest_api.client import APIEndpointClient
        try:
            entry = resolve_api_endpoint(cfg.config_json or {}, src.api_endpoint_name or "")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        endpoint_name = src.api_endpoint_name or "endpoint"
        # A run_id of None means "no run behind this pull" (column stats), not
        # "store nothing" — it selects the ad-hoc directory instead.
        dest = (
            run_artifact_dir(run_id) if run_id
            else adhoc_artifact_dir(src.config_id, endpoint_name)
        )
        return APIEndpointClient(entry).fetch_dataframe(
            on_response=build_api_response_sink(dest, endpoint_name),
        )
```

`run_column_stats` at lines 757-758 needs no change: it already calls
`_load_bo_source` without a `run_id`, which now resolves to the ad-hoc
directory.

In `api/services/difference_export.py`, lines 525-526 stay as they are, but add
the comment above them so the omission reads as deliberate:

```python
    # No response sink here on purpose: this re-pulls the sources from a stored
    # payload to build an export, and those bytes were already persisted by the
    # run that produced the payload. Storing them again is pure duplication.
    df_a = svc._load_bo_source(req.source_a, req.doc_id, req.report_id)
    df_b = svc._load_bo_source(req.source_b, req.doc_id, req.report_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_api.py tests/unit/test_bo_compare_prompts.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add api/services/compare_service.py api/services/difference_export.py tests/unit/test_compare_api.py
git commit -m "feat(compare): store API source responses under the run's artifact directory"
```

---

### Task 8: Wire the api_reconciliation job

**Files:**
- Modify: `api/services/run_executor.py:1795-1804`
- Test: `tests/unit/test_run_executor_api_reconciliation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_run_executor_api_reconciliation.py`:

```python
def test_api_reconciliation_stores_both_sides(monkeypatch):
    import pandas as pd
    from etl_framework.rest_api import client as client_module

    destinations = []

    class FakeClient:
        def __init__(self, entry):
            self.entry = entry

        def fetch_dataframe(self, max_pages=None, on_response=None):
            destinations.append(on_response)
            return pd.DataFrame({"id": [1]})

    monkeypatch.setattr(client_module, "APIEndpointClient", FakeClient)
    ...
```

Finish by building the executor and job the way the existing tests in this file
do, running the job, and asserting `len(destinations) == 2` and that both
entries are callable — one sink per side.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_run_executor_api_reconciliation.py -v -k both_sides`
Expected: FAIL — both entries are `None`

- [ ] **Step 3: Write minimal implementation**

In `api/services/run_executor.py`, replace lines 1803-1804 with:

```python
            from api.services.api_artifact import build_api_response_sink, run_artifact_dir

            run_dir = run_artifact_dir(self._run_id)
            src_name = job.params["source_api_endpoint"]
            tgt_name = job.params["target_api_endpoint"]
            df_a = APIEndpointClient(src_entry).fetch_dataframe(
                on_response=build_api_response_sink(run_dir, src_name),
            )
            df_b = APIEndpointClient(tgt_entry).fetch_dataframe(
                on_response=build_api_response_sink(run_dir, tgt_name),
            )
```

Do **not** set `data_artifact_path` on the result. This job has two sources, and
`resolve_row_diffable_artifact` returns `None` unless a run has exactly one
artifact path — recording one side would misrepresent what the run consumed.
The raw pages are still on disk and discoverable.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_run_executor_api_reconciliation.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_executor_api_reconciliation.py
git commit -m "feat(runs): store both API sides of a reconciliation run"
```

---

### Task 9: Retention coverage and full suite

**Files:**
- Test: `tests/unit/test_api_artifact.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_api_artifact.py`:

```python
import os
import time

from api.services.upload_store import cleanup_expired_uploads


def test_adhoc_directory_is_swept_by_existing_retention(tmp_path):
    adhoc = tmp_path / "adhoc_3_orders_20260803T211408Z"
    adhoc.mkdir()
    (adhoc / "orders_p1.json").write_bytes(b"{}")
    old = time.time() - (40 * 86400)
    os.utime(adhoc, (old, old))
    removed = cleanup_expired_uploads(30, root=tmp_path)
    assert removed == 1
    assert not adhoc.exists()


def test_recent_adhoc_directory_survives_retention(tmp_path):
    adhoc = tmp_path / "adhoc_3_orders_20260803T211408Z"
    adhoc.mkdir()
    assert cleanup_expired_uploads(30, root=tmp_path) == 0
    assert adhoc.exists()
```

- [ ] **Step 2: Run tests to verify they fail or pass**

Run: `python -m pytest tests/unit/test_api_artifact.py -v -k retention`
Expected: PASS immediately. That is the point of the naming decision — the
directory is a direct child of the root, so the existing sweep already handles
it. These tests lock that in so a future nested-directory refactor breaks
loudly instead of silently leaking disk.

- [ ] **Step 3: Run the full unit suite**

Run: `python -m pytest tests/unit/ -q`
Expected: no failures. Use raw `python -m pytest`, not a cached wrapper.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_api_artifact.py
git commit -m "test(api): lock in that ad-hoc artifact directories are swept by retention"
```

---

## Self-review notes

- Spec coverage: root and directories (Tasks 1, 3), one file per page (Tasks 2, 4, 5), filename derivation including `Content-Disposition` (Task 2), size cap and error swallowing (Task 4), callback placement before the status check (Task 5), all five call sites plus the `difference_export` exclusion (Tasks 6, 7, 8), traversal safety (Tasks 1, 2), retention (Task 9).
- The spec's assembled-CSV-per-source is **not** implemented by this plan. Every current call site is one of two sources, so the assembled frame has no consumer until a single-source API run exists. Raw pages are stored; the CSV is deferred rather than written and left unused. Flag this when reviewing the plan if you disagree.
- Names used consistently throughout: `safe_filename`, `unique_path`, `artifact_filename`, `run_artifact_dir`, `adhoc_artifact_dir`, `build_api_response_sink`, `on_response`, `page_index`.
