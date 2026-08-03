# API exchange inspector

Date: 2026-08-03

## Problem

An API endpoint that works in Bruno can fail in this application, and the
application cannot say why.

`AdapterService.test_api_endpoint` returns only `ok` and a message.
`preview_api_endpoint` returns only parsed columns and rows. Neither ever shows
the raw response — no status, no headers, no body. Every failure path in
`etl_framework/rest_api/client.py` discards the response: `Cannot parse API
response as json` is raised with no status, no content type and no body
attached, so a 200 carrying an HTML proxy interstitial, a 200 carrying an empty
body after a dropped request body, and a genuinely non-JSON endpoint are
indistinguishable from the outside.

Diagnosing "works in Bruno, fails here" therefore requires a standalone probe
script. It should require reading the screen.

## Decision

Test and Preview return the full exchange — the request as actually sent and the
response as actually received — and the config UI renders both.

Showing the **request** is what makes this a diff rather than a guess: a dropped
body is visible directly, not inferred from an empty result.

## Capture mechanism

Reuses the `on_response` sink introduced by the API response artifact storage
design rather than adding a second hook. That sink already receives
`(raw_bytes, page_number, response)`, and a `requests.Response` carries
everything needed: `.request` (method, final URL, headers, body),
`.status_code`, `.headers`, `.elapsed`, `.content`. The artifact sink writes
bytes; this one builds a dict. Test and Preview pass both.

New module `api/services/api_exchange.py` — pure transformation, no filesystem,
no network:

```python
def capture_exchange(entry) -> tuple[Callable, list[dict]]
```

Returns a sink and the list it fills. It takes `entry` because redaction depends
on `entry.api_key_header`, which is configurable per endpoint.

### Payload

```json
{
  "request":  {"method": "POST",
               "url": "https://api.example.com/v1/orders/search?region=us",
               "headers": {"Content-Type": "application/json",
                           "Authorization": "<32 chars, ...a91f>"},
               "body": "{\"filter\":\"recent\"}"},
  "response": {"status": 200, "elapsed_ms": 412, "bytes": 1834,
               "content_type": "text/html", "redirects": 0,
               "headers": {"content-type": "text/html"},
               "body": "<!DOCTYPE html>...", "truncated": true,
               "binary": false},
  "artifact_path": "reports/uploads/adhoc_3_orders_20260803T211408Z/api_orders_p1.json"
}
```

- `"body": null` on the request means **no body was sent**. The UI renders
  `<NONE SENT>`. This is the dropped-body case, visible at a glance.
- The response body truncates at 8 KB and sets `truncated`.
- A non-text content type is never decoded: `binary: true`, and the body carries
  the first 200 bytes as hex.
- Redirects are followed as today; only the final response is captured, with a
  `redirects` count so a login bounce is not invisible.
- `artifact_path` is populated when the artifact sink stored the response, so
  the inspector links to the full untruncated bytes.

## Redaction

Request headers are the one genuinely new exposure. `api_key` and `bearer_token`
are masked by `_mask` everywhere else in the config API, so echoing them raw
here would undo that.

Redacted to `<N chars, ...last4>`, matched case-insensitively:

- `Authorization`
- `Cookie`
- `X-SAP-LogonToken`
- **`entry.api_key_header`, read from config** — not a hardcoded name. An
  endpoint configured with `X-Custom-Token` must not leak.

Response `Set-Cookie` gets the same treatment.

The request **body is echoed verbatim** (truncated). This leaks nothing new: it
is the same text sitting in the Body textarea directly above it in the same
modal, visible to the same user. Redacting it would defeat the entire purpose of
diffing against Bruno. This is a deliberate decision, not an oversight.

The response body is the user's own data: truncated, never redacted.

## Where the sink fires

The sink is invoked **inside `_request`, immediately after the response is
received and before the `status_code >= 400` check** — not in
`fetch_dataframe`. Ordering is the design, not a detail:

| Failure | Raised where | Exchange captured |
|---|---|---|
| 4xx / 5xx | inside `_request`, after the status check | Yes, the sink already fired |
| Body not JSON | `_parse_response`, after `_request` returned | Yes |
| `json_root_path` miss | `_parse_response` | Yes |
| DNS / timeout / proxy refused | `_request`, no response object exists | No. The list stays empty and the UI shows the friendly error plus "no response received", which is itself the diagnosis |

Capturing in `fetch_dataframe` instead would lose the body of every 4xx and 5xx
— the exact hole this design exists to close.

### Excluded from capture

The SAP BO logon sub-request in `_get_sap_bo_token` gets no sink. Its body is
`{"password": ...}` in plaintext, and the rule above echoes request bodies
verbatim. Recorded here so the exclusion is not later "unified" away by someone
tidying the client.

## API contract

- `POST /api/adapters/rest-api/test` — `AdapterTestOut` gains
  `exchange: dict | None = None`. Optional, so the BO, database and AWS adapters
  that share this schema are unaffected.
- `POST /api/adapters/rest-api/preview` — success returns
  `{columns, rows, exchange}`. Failure keeps `HTTPException(502, ...)` but
  `detail` becomes `{"message": <friendly error>, "exchange": {...}}`.

No frontend helper change is needed: `apiErrorMessage` in `frontend/app.js`
already prefers `detail.message`, and `api()` already preserves
`error.detail`, so `e.detail.exchange` is available.

## UI

`frontend/features/config.js`: `testApiEndpoint` and `previewApiEndpoint` set
`ep.exchange` — from the payload on success, from `e.detail?.exchange` on
failure. The existing "Save the config first" guard is unchanged.

`frontend/partials/tab-config.html`, and the duplicate of the same form in
`frontend/index.html`: a collapsible panel under the endpoint card with two
stacked panes, REQUEST and RESPONSE.

- Monospace, each pane in its own `overflow-x: auto` container so a long URL
  never scrolls the modal sideways.
- Status badge coloured by class, with elapsed milliseconds and byte size.
- Pretty/raw toggle: `JSON.parse` then `JSON.stringify(_, null, 2)`, falling
  back to raw when the body is not JSON — that fallback is itself the signal
  being looked for.
- Copy button per pane.
- `<NONE SENT>` rendered distinctly in the request pane when `body` is null.

The repository has an automated WCAG 2.1 AA gate. The disclosure control, the
pretty/raw toggle and the copy buttons need labels, roles and keyboard
reachability, and the panel must pass that gate. This is a build requirement,
not a follow-up.

## Testing

`api/services/api_exchange.py`:

- Redaction of `Authorization`, `Cookie`, `X-SAP-LogonToken` and a custom
  `api_key_header` read from the entry, matched case-insensitively.
- A request with `body=None` is recorded as null.
- The 8 KB truncation sets `truncated`.
- A binary content type yields `binary: true` with hex and never attempts a
  decode.

Client:

- Exchange present when `_parse_response` raises.
- Exchange present when `_request` raises on a 502.
- Exchange empty and the original `APIRequestError` preserved on a
  `ConnectionError`.
- The SAP BO logon sub-request produces no exchange entry.

Routes:

- Preview failure returns 502 carrying both `detail.message` and
  `detail.exchange`.
- `AdapterTestOut.exchange` round-trips.

End to end:

- The inspector panel passes the existing WCAG gate.

## Out of scope

Explicitly excluded from this spec, after scoping:

- Validating the request body in the config UI. The silent
  `catch { body = null }` in `frontend/features/config.js` stays. **If that is
  the root cause of a given failure, this inspector shows the empty body rather
  than preventing it.**
- Methods beyond GET and POST.
- Importing from cURL or a Bruno `.bru` file.
