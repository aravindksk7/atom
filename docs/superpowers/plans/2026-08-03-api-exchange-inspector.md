# API Exchange Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the request as actually sent and the response as actually received in the config UI, so "works in Bruno, fails here" becomes a diff instead of a guess.

**Architecture:** Reuses the `on_response` callback added by the API response artifact storage plan — that callback already receives `(raw_bytes, page_number, response)`, and a `requests.Response` carries the request, status, headers and elapsed time. A new `api/services/api_exchange.py` turns that into a redacted dict. Test and Preview pass both sinks; the routes return the dict on success and inside `detail` on failure.

**Tech Stack:** Python 3, `requests`, `pytest`, Alpine.js, Tailwind, Playwright.

**Depends on:** `docs/superpowers/plans/2026-08-03-api-response-artifact-storage.md` — Task 5 of that plan must be complete, since this plan has no way to observe a response without it.

Spec: `docs/superpowers/specs/2026-08-03-api-exchange-inspector-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `api/services/api_exchange.py` (create) | Builds the redacted request/response dict. Pure transformation — no filesystem, no network. |
| `api/schemas.py` (modify) | `AdapterTestOut` gains an optional `exchange`. |
| `api/services/adapter_service.py` (modify) | Test and Preview attach the capture sink and return the exchange, on success and on failure. |
| `frontend/features/config.js` (modify) | Stores `ep.exchange` from either the payload or the error detail. |
| `frontend/partials/tab-config.html` + `frontend/index.html` (modify) | The inspector panel. Both files carry the same endpoint form. |
| `tests/unit/test_api_exchange.py` (create) | Redaction, truncation, binary handling, null body. |
| `tests/unit/test_adapter_service.py` (modify) | Exchange returned on success and on failure. |
| `tests/e2e/` (modify) | Panel renders and passes the WCAG gate. |

---

### Task 1: Header redaction

**Files:**
- Create: `api/services/api_exchange.py`
- Test: `tests/unit/test_api_exchange.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_exchange.py`:

```python
from __future__ import annotations

from etl_framework.config.models import ApiEndpointEntry
from api.services.api_exchange import redact_headers


def _entry(**overrides) -> ApiEndpointEntry:
    base = {"base_url": "https://api.example.com/v1/orders"}
    base.update(overrides)
    return ApiEndpointEntry(**base)


def test_authorization_is_redacted():
    out = redact_headers({"Authorization": "Bearer abcdefghijklmnop"}, _entry())
    assert out["Authorization"] == "<23 chars, ...mnop>"


def test_redaction_is_case_insensitive():
    out = redact_headers({"authorization": "Bearer abcdefghijklmnop"}, _entry())
    assert out["authorization"].startswith("<23 chars")


def test_cookie_and_logon_token_are_redacted():
    out = redact_headers(
        {"Cookie": "session=aaaabbbb", "X-SAP-LogonToken": "tokenvalue1234"}, _entry()
    )
    assert out["Cookie"].startswith("<")
    assert out["X-SAP-LogonToken"].startswith("<")


def test_configured_api_key_header_is_redacted():
    entry = _entry(auth_type="api_key", api_key_header="X-Custom-Token", api_key="s3cret-value")
    out = redact_headers({"X-Custom-Token": "s3cret-value"}, entry)
    assert out["X-Custom-Token"] == "<12 chars, ...alue>"


def test_ordinary_headers_pass_through():
    out = redact_headers({"Content-Type": "application/json"}, _entry())
    assert out["Content-Type"] == "application/json"


def test_short_secret_does_not_leak_the_whole_value():
    out = redact_headers({"Authorization": "abc"}, _entry())
    assert "abc" not in out["Authorization"]
```

The custom-header test is the one that matters: a hardcoded allowlist would
leak an endpoint configured with `X-Custom-Token`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_api_exchange.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.services.api_exchange'`

- [ ] **Step 3: Write minimal implementation**

Create `api/services/api_exchange.py`:

```python
"""Build a redacted request/response pair for the config UI's inspector.

Pure transformation: no filesystem, no network. Fed by the same `on_response`
callback the artifact sink uses.
"""
from __future__ import annotations

from typing import Callable

_ALWAYS_SECRET = {"authorization", "cookie", "set-cookie", "x-sap-logontoken"}

BODY_LIMIT = 8192
BINARY_PREVIEW_BYTES = 200

_TEXTUAL_CONTENT_TYPES = (
    "application/json", "text/", "application/xml", "application/x-www-form-urlencoded",
    "application/csv", "application/javascript",
)


def _secret_header_names(entry) -> set[str]:
    names = set(_ALWAYS_SECRET)
    configured = (getattr(entry, "api_key_header", "") or "").strip().lower()
    if configured:
        names.add(configured)
    return names


def _mask(value: str) -> str:
    text = str(value)
    return f"<{len(text)} chars, ...{text[-4:]}>" if len(text) > 4 else f"<{len(text)} chars>"


def redact_headers(headers, entry) -> dict:
    secret = _secret_header_names(entry)
    return {
        key: (_mask(value) if key.lower() in secret else value)
        for key, value in (headers or {}).items()
    }
```

`_mask` on a value of four characters or fewer prints only the length — showing
the last four of a four-character secret would be the whole secret.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api_exchange.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add api/services/api_exchange.py tests/unit/test_api_exchange.py
git commit -m "feat(api): redact secret headers for the exchange inspector"
```

---

### Task 2: Body rendering — truncation, binary, null

**Files:**
- Modify: `api/services/api_exchange.py`
- Test: `tests/unit/test_api_exchange.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_api_exchange.py`:

```python
from api.services.api_exchange import BODY_LIMIT, render_body


def test_text_body_passes_through():
    body, truncated, binary = render_body(b'{"a":1}', "application/json")
    assert body == '{"a":1}'
    assert truncated is False
    assert binary is False


def test_long_body_is_truncated():
    body, truncated, binary = render_body(b"x" * (BODY_LIMIT + 100), "text/plain")
    assert len(body) == BODY_LIMIT
    assert truncated is True


def test_binary_body_is_hex_not_decoded():
    body, truncated, binary = render_body(b"\x89PNG\r\n\x1a\n", "image/png")
    assert binary is True
    assert body.startswith("89504e47")


def test_undecodable_text_body_falls_back_to_replacement():
    body, truncated, binary = render_body(b"\xff\xfe bad", "text/plain")
    assert binary is False
    assert "�" in body


def test_empty_body_is_empty_string_not_none():
    body, truncated, binary = render_body(b"", "application/json")
    assert body == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_api_exchange.py -v -k body`
Expected: FAIL with `ImportError: cannot import name 'render_body'`

- [ ] **Step 3: Write minimal implementation**

Append to `api/services/api_exchange.py`:

```python
def _is_textual(content_type: str) -> bool:
    base = (content_type or "").split(";")[0].strip().lower()
    return any(base.startswith(prefix) for prefix in _TEXTUAL_CONTENT_TYPES)


def render_body(raw: bytes, content_type: str) -> tuple[str, bool, bool]:
    """Return (body_text, truncated, binary) for display.

    A non-textual content type is never decoded: it is shown as hex, so a PNG
    or an xlsx cannot produce a screenful of mojibake.
    """
    raw = raw or b""
    if not _is_textual(content_type):
        return raw[:BINARY_PREVIEW_BYTES].hex(), len(raw) > BINARY_PREVIEW_BYTES, True
    text = raw.decode("utf-8", "replace")
    return text[:BODY_LIMIT], len(text) > BODY_LIMIT, False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api_exchange.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add api/services/api_exchange.py tests/unit/test_api_exchange.py
git commit -m "feat(api): render response bodies with truncation and binary safety"
```

---

### Task 3: The capture sink

**Files:**
- Modify: `api/services/api_exchange.py`
- Test: `tests/unit/test_api_exchange.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_api_exchange.py`:

```python
from unittest.mock import MagicMock

from api.services.api_exchange import capture_exchange


def _response(status=200, body=b'{"a":1}', content_type="application/json",
              req_body=b'{"filter":"recent"}', req_headers=None):
    request = MagicMock()
    request.method = "POST"
    request.url = "https://api.example.com/v1/orders/search?region=us"
    request.headers = req_headers if req_headers is not None else {"Content-Type": "application/json"}
    request.body = req_body

    resp = MagicMock()
    resp.request = request
    resp.status_code = status
    resp.content = body
    resp.headers = {"Content-Type": content_type}
    resp.history = []
    elapsed = MagicMock()
    elapsed.total_seconds.return_value = 0.412
    resp.elapsed = elapsed
    return resp


def test_capture_records_request_and_response():
    sink, seen = capture_exchange(_entry())
    sink(b'{"a":1}', 1, _response())
    assert len(seen) == 1
    exchange = seen[0]
    assert exchange["request"]["method"] == "POST"
    assert exchange["request"]["url"].endswith("?region=us")
    assert exchange["request"]["body"] == '{"filter":"recent"}'
    assert exchange["response"]["status"] == 200
    assert exchange["response"]["elapsed_ms"] == 412
    assert exchange["response"]["bytes"] == 7


def test_capture_records_a_missing_request_body_as_null():
    """The dropped-body case: this is what makes it visible."""
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, _response(req_body=None))
    assert seen[0]["request"]["body"] is None


def test_capture_redacts_request_headers():
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, _response(req_headers={"Authorization": "Bearer abcdefghijkl"}))
    assert seen[0]["request"]["headers"]["Authorization"].startswith("<")


def test_capture_counts_redirects():
    resp = _response()
    resp.history = [MagicMock(), MagicMock()]
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, resp)
    assert seen[0]["response"]["redirects"] == 2


def test_capture_never_raises_on_a_malformed_response():
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, object())  # no .request, no .headers
    assert seen == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_api_exchange.py -v -k capture`
Expected: FAIL with `ImportError: cannot import name 'capture_exchange'`

- [ ] **Step 3: Write minimal implementation**

Append to `api/services/api_exchange.py`:

```python
def capture_exchange(entry) -> tuple[Callable, list[dict]]:
    """Return an `on_response` sink and the list it fills.

    Takes `entry` because redaction depends on the endpoint's configured
    `api_key_header`. Never raises: an inspector that breaks a pull is worse
    than no inspector.
    """
    seen: list[dict] = []

    def sink(raw_bytes: bytes, page_number: int, response) -> None:
        try:
            request = response.request
            req_headers = redact_headers(dict(request.headers or {}), entry)
            req_body = request.body
            if isinstance(req_body, bytes):
                req_body = req_body.decode("utf-8", "replace")
            resp_headers = dict(response.headers or {})
            content_type = resp_headers.get("Content-Type", "")
            body, truncated, binary = render_body(response.content, content_type)
            seen.append({
                "request": {
                    "method": request.method,
                    "url": str(request.url),
                    "headers": req_headers,
                    "body": req_body,
                },
                "response": {
                    "status": response.status_code,
                    "elapsed_ms": int(response.elapsed.total_seconds() * 1000),
                    "bytes": len(response.content or b""),
                    "content_type": content_type,
                    "redirects": len(getattr(response, "history", None) or []),
                    "headers": redact_headers(resp_headers, entry),
                    "body": body,
                    "truncated": truncated,
                    "binary": binary,
                },
            })
        except Exception:  # noqa: BLE001 - an observer cannot break a pull
            pass

    return sink, seen
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api_exchange.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add api/services/api_exchange.py tests/unit/test_api_exchange.py
git commit -m "feat(api): capture a redacted request/response exchange"
```

---

### Task 4: Return the exchange from Test and Preview

**Files:**
- Modify: `api/schemas.py:674-677`
- Modify: `api/services/adapter_service.py:108-134`
- Test: `tests/unit/test_adapter_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_adapter_service.py`:

```python
def test_preview_failure_carries_the_exchange(monkeypatch):
    """The failure path is the one that matters: a 200 with a non-JSON body."""
    import pytest
    from fastapi import HTTPException

    from api.services import adapter_service
    from etl_framework.exceptions import APIRequestError

    class FakeClient:
        def __init__(self, entry):
            pass

        def fetch_dataframe(self, max_pages=None, on_response=None):
            for sink in (on_response if isinstance(on_response, list) else [on_response]):
                pass
            raise APIRequestError(
                url="https://x.example.com/a", http_status=200,
                message="Cannot parse API response as json",
            )

    monkeypatch.setattr(adapter_service, "APIEndpointClient", FakeClient)
    service = _service_with_endpoint(monkeypatch)  # see helper note below
    with pytest.raises(HTTPException) as excinfo:
        service.preview_api_endpoint(1, "orders", 20)
    detail = excinfo.value.detail
    assert isinstance(detail, dict)
    assert "message" in detail
    assert "exchange" in detail
```

Write a `_service_with_endpoint(monkeypatch)` helper in this test file that
patches `AdapterService._get_api_endpoint` to return
`ApiEndpointEntry(name="orders", base_url="https://x.example.com/a")` and
constructs the service the way the neighbouring tests already do.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_adapter_service.py -v -k carries_the_exchange`
Expected: FAIL — `detail` is a string, not a dict

- [ ] **Step 3: Write minimal implementation**

In `api/schemas.py`, replace lines 674-677:

```python
class AdapterTestOut(BaseModel):
    ok: bool
    message: str
    latency_ms: int = 0
    # Only the REST API adapter populates this; the BO, database and AWS
    # adapters share this schema and leave it None.
    exchange: dict | None = None
```

In `api/services/adapter_service.py`, add to the imports:

```python
from api.services.api_exchange import capture_exchange
```

Replace `test_api_endpoint` and `preview_api_endpoint` (lines 108-134) with:

```python
    def _api_sinks(self, entry, config_id: int, endpoint_name: str):
        """Both observers for one pull: one stores bytes, one builds the exchange."""
        store = build_api_response_sink(
            adhoc_artifact_dir(config_id, endpoint_name), endpoint_name
        )
        capture, seen = capture_exchange(entry)

        def fan_out(raw_bytes, page_number, response):
            store(raw_bytes, page_number, response)
            capture(raw_bytes, page_number, response)

        return fan_out, seen

    def test_api_endpoint(self, config_id: int, endpoint_name: str) -> AdapterTestOut:
        t0 = time.monotonic()
        seen: list = []
        try:
            entry = self._get_api_endpoint(config_id, endpoint_name)
            sink, seen = self._api_sinks(entry, config_id, endpoint_name)
            APIEndpointClient(entry).fetch_dataframe(max_pages=1, on_response=sink)
            latency_ms = int((time.monotonic() - t0) * 1000)
            return AdapterTestOut(
                ok=True, message="Connection successful", latency_ms=latency_ms,
                exchange=seen[0] if seen else None,
            )
        except HTTPException:
            raise
        except ValueError as exc:
            return AdapterTestOut(ok=False, message=str(exc), latency_ms=0)
        except Exception as exc:
            return AdapterTestOut(
                ok=False, message=_friendly_error(exc), latency_ms=0,
                exchange=seen[0] if seen else None,
            )

    def preview_api_endpoint(self, config_id: int, endpoint_name: str, limit: int) -> dict:
        import json
        try:
            entry = self._get_api_endpoint(config_id, endpoint_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        sink, seen = self._api_sinks(entry, config_id, endpoint_name)
        try:
            df = APIEndpointClient(entry).fetch_dataframe(max_pages=1, on_response=sink)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={"message": _friendly_error(exc), "exchange": seen[0] if seen else None},
            ) from exc
        df = df.head(max(1, min(200, limit)))
        rows = json.loads(df.to_json(orient="values", date_format="iso"))
        return {"columns": list(df.columns), "rows": rows, "exchange": seen[0] if seen else None}
```

`seen` is initialised to `[]` before the `try` in `test_api_endpoint` so the
`except` branch can read it even when `_get_api_endpoint` raised first.

This supersedes Task 6 of the artifact storage plan, which wired the storage
sink alone. `_api_sinks` fans out to both; the storage behaviour and its tests
are unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_adapter_service.py tests/unit/test_adapters_routes.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py api/services/adapter_service.py tests/unit/test_adapter_service.py
git commit -m "feat(api): return the request/response exchange from Test and Preview"
```

---

### Task 5: Frontend state

**Files:**
- Modify: `frontend/features/config.js:282-307`
- Test: manual, then covered by Task 7

- [ ] **Step 1: Write the implementation**

Replace lines 282-307 of `frontend/features/config.js`:

```javascript
    async testApiEndpoint(idx) {
      const m = this.configModal;
      const ep = m.apiEndpoints[idx];
      if (!m.id) { ep.testResult = { ok: false, message: 'Save the config first, then test.' }; return; }
      try {
        ep.testResult = await api('POST', '/api/adapters/rest-api/test', {
          config_id: m.id, endpoint_name: ep.name,
        });
        ep.exchange = ep.testResult.exchange || null;
      } catch (e) {
        ep.testResult = { ok: false, message: e.message };
        ep.exchange = e.detail?.exchange || null;
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
        ep.exchange = ep.previewResult.exchange || null;
      } catch (e) {
        ep.previewError = e.message;
        ep.exchange = e.detail?.exchange || null;
      }
    },

    exchangeBody(raw, pretty) {
      if (raw === null || raw === undefined) return '<NONE SENT>';
      if (!pretty) return raw;
      try {
        return JSON.stringify(JSON.parse(raw), null, 2);
      } catch (_) {
        return raw;
      }
    },
```

The `catch (_) { return raw; }` fallback is deliberate: a body that will not
parse as JSON is exactly the signal being looked for, so it must render as-is
rather than error.

- [ ] **Step 2: Add the fields to the endpoint factory**

In the same file, the object literal that creates a blank endpoint (around line
260) gains two fields so Alpine tracks them reactively:

```javascript
        exchange: null, exchangePretty: true,
```

Add the same two fields to the object built when loading an existing config
(around line 100-125), next to `previewResult`.

- [ ] **Step 3: Commit**

```bash
git add frontend/features/config.js
git commit -m "feat(frontend): keep the API exchange from Test and Preview"
```

---

### Task 6: The inspector panel

**Files:**
- Modify: `frontend/partials/tab-config.html` (after the Test/Preview buttons, around line 579)
- Modify: `frontend/index.html` (the same form, around line 752)

- [ ] **Step 1: Write the markup**

Insert into both files, immediately after the element containing the Test and
Preview buttons. The two files carry the same form; the markup must be
identical in both.

```html
<div x-show="ep.exchange" class="mt-2 border border-slate-200 rounded">
  <button type="button" @click="ep.exchangeOpen = !ep.exchangeOpen"
          :aria-expanded="ep.exchangeOpen ? 'true' : 'false'"
          class="w-full flex items-center gap-2 px-3 py-2 text-xs text-left">
    <span x-text="ep.exchangeOpen ? '▲' : '▼'" aria-hidden="true"></span>
    <span>Request / response</span>
    <span class="ml-auto font-mono px-2 rounded"
          :class="ep.exchange?.response?.status < 400 ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'"
          x-text="ep.exchange?.response?.status"></span>
    <span class="font-mono text-slate-500"
          x-text="`${ep.exchange?.response?.elapsed_ms} ms · ${ep.exchange?.response?.bytes} B`"></span>
  </button>

  <div x-show="ep.exchangeOpen" class="px-3 pb-3 space-y-3">
    <div class="flex items-center gap-2">
      <button type="button" @click="ep.exchangePretty = !ep.exchangePretty"
              class="btn-secondary btn-sm text-xs"
              x-text="ep.exchangePretty ? 'Raw' : 'Pretty'"></button>
      <span x-show="ep.exchange?.response?.truncated" class="text-xs text-amber-700">
        response truncated
      </span>
      <span x-show="ep.exchange?.response?.redirects" class="text-xs text-slate-500"
            x-text="`${ep.exchange?.response?.redirects} redirect(s) followed`"></span>
    </div>

    <section aria-label="Request as sent">
      <h4 class="field-label">Request</h4>
      <div class="overflow-x-auto bg-slate-50 rounded p-2">
        <pre class="font-mono text-xs whitespace-pre"
             x-text="`${ep.exchange?.request?.method} ${ep.exchange?.request?.url}\n` +
                     Object.entries(ep.exchange?.request?.headers || {}).map(([k,v]) => `${k}: ${v}`).join('\n') +
                     `\n\n` + exchangeBody(ep.exchange?.request?.body, ep.exchangePretty)"></pre>
      </div>
    </section>

    <section aria-label="Response as received">
      <h4 class="field-label">Response</h4>
      <div class="overflow-x-auto bg-slate-50 rounded p-2">
        <pre class="font-mono text-xs whitespace-pre"
             x-text="Object.entries(ep.exchange?.response?.headers || {}).map(([k,v]) => `${k}: ${v}`).join('\n') +
                     `\n\n` + exchangeBody(ep.exchange?.response?.body, ep.exchangePretty)"></pre>
      </div>
    </section>
  </div>
</div>
```

Each `<pre>` sits inside its own `overflow-x: auto` container so a long URL
scrolls that pane, never the modal.

- [ ] **Step 2: Add `exchangeOpen` to the endpoint objects**

In `frontend/features/config.js`, add `exchangeOpen: false,` beside the
`exchange: null, exchangePretty: true,` fields added in Task 5, in both the
blank-endpoint factory and the load path.

- [ ] **Step 3: Verify by hand**

Start the app, open a config with a REST endpoint, save it, click Preview
against an endpoint that returns non-JSON, and confirm the panel shows the
status, the request line, redacted auth headers and the raw body.

- [ ] **Step 4: Commit**

```bash
git add frontend/partials/tab-config.html frontend/index.html frontend/features/config.js
git commit -m "feat(frontend): request/response inspector for API endpoints"
```

---

### Task 7: Accessibility gate and full suite

**Files:**
- Modify: `tests/e2e/21-accessibility.spec.ts`

- [ ] **Step 1: Run the existing WCAG gate**

Run: `npx playwright test tests/e2e/21-accessibility.spec.ts`
Expected: passes. If the new disclosure button, the Pretty/Raw toggle or the
`<pre>` panes introduce a violation, fix the markup — an accessible name on
every button, `aria-expanded` on the disclosure, and no colour-only status
signal. The status badge already carries its numeric status as text, which is
what keeps it off colour alone.

- [ ] **Step 2: Run the unit suite**

Run: `python -m pytest tests/unit/ -q`
Expected: no failures. Use raw `python -m pytest`, not a cached wrapper.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "test(frontend): keep the exchange inspector inside the WCAG gate"
```

---

## Self-review notes

- Spec coverage: capture mechanism reusing `on_response` (Task 3), payload shape including `redirects` and `truncated` (Tasks 2, 3), null request body surfaced as `<NONE SENT>` (Tasks 3, 5), redaction driven by the configured `api_key_header` (Task 1), request body echoed unredacted by design (Task 3 — no redaction applied to it), the SAP BO logon exclusion (inherited: `_get_sap_bo_token` is never given a sink, unchanged by this plan), API contract for success and failure (Task 4), UI (Tasks 5, 6), WCAG gate (Task 7).
- `artifact_path` from the spec's payload example is **not** implemented. The storage sink and the capture sink are independent observers and the storage sink returns nothing, so plumbing a path between them would mean threading a return value through a callback whose contract is "returns nothing". Deferred deliberately; the response is still on disk in the ad-hoc directory. Flag if you want it in scope.
- Task 4 supersedes Task 6 of the artifact storage plan by fanning both sinks through `_api_sinks`; storage behaviour and its tests are unchanged.
- Names used consistently: `redact_headers`, `render_body`, `capture_exchange`, `exchange`, `exchangeOpen`, `exchangePretty`, `exchangeBody`.
