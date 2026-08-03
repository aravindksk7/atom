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

`fetch_dataframe` accepts one `on_response`, and Test and Preview need two
observers, so `AdapterService._api_sinks(entry, config_id, endpoint_name)`
builds both — the artifact sink from `build_api_response_sink` and this
capture sink — and returns a `fan_out(raw_bytes, page_index, response)` closure
that calls the storage sink first and the capture sink second, plus the `seen`
list. Both Test and Preview pass that one closure as `on_response`.

The fan-out provides **no isolation**: it is a plain sequential call with no
`try`, so an exception escaping the first sink would skip the second and
propagate into the client. It is safe only because each sink already catches
everything internally and returns normally by contract. Anything added to this
fan-out later must hold to that same contract, or the wrapper needs a guard of
its own.

### Payload

```json
{
  "request":  {"method": "POST",
               "url": "https://api.example.com/v1/orders/search?region=us",
               "headers": {"Content-Type": "application/json",
                           "Authorization": "<32 chars, redacted>"},
               "body": "{\"filter\":\"recent\"}",
               "truncated": false},
  "response": {"status": 200, "elapsed_ms": 412, "bytes": 1834,
               "content_type": "text/html", "redirects": 0,
               "headers": {"content-type": "text/html"},
               "body": "<!DOCTYPE html>...", "truncated": true,
               "binary": false}
}
```

- `"body": null` on the request means **no body was sent**. The UI renders
  `<NONE SENT>`. This is the dropped-body case, visible at a glance.
- `BODY_LIMIT` is 8 KB and applies to **both** bodies, each reporting its own
  `truncated` flag — `request.truncated` as well as `response.truncated`.
  Without the request-side cap a 2 MB POST body became a 2,000,000-character
  string inside the payload. For raw bytes the limit is a budget in *bytes*
  applied **before** the decode, so it bounds the work of the decode itself
  rather than trimming a string already built — a 15 KB CJK body used to decode
  whole and report `truncated: false`. A request body handed over already as a
  `str` has nothing to decode and is capped by character count.
- A body whose content type is declared and non-textual is never decoded:
  `binary: true`, and the body carries the first 200 bytes as hex. Two
  deliberate widenings, both found as blockers in review, decide the other way:
  - **An absent or empty content type counts as textual.** The headline
    failure this inspector exists to diagnose is described as carrying "no
    status, no content type and no body" — so the single response a human most
    needs to read is exactly the one that declares nothing. Hex-dumping it
    would hide the answer. The mojibake risk is bounded by
    `decode("utf-8", "replace")`.
  - **RFC 6839 structured suffixes `+json` and `+xml` count as textual.**
    `application/problem+json` (RFC 7807) is the standard machine-readable
    error document and therefore the likeliest content type on precisely the
    failure being diagnosed. Same for `application/vnd.api+json`,
    `application/hal+json`, `application/ld+json` and `application/xhtml+xml`.
- Redirects are followed as today; only the final response is captured, with a
  `redirects` count so a login bounce is not invisible.

There is no `artifact_path` — see "Not built".

## Redaction

Request headers are the one genuinely new exposure. `api_key` and `bearer_token`
are masked by `_mask` everywhere else in the config API, so echoing them raw
here would undo that.

A redacted header renders as **`<N chars, redacted>`** — its length and nothing
else. An earlier draft of this spec showed the last four characters
(`<32 chars, ...a91f>`); that was replaced because it is unsafe for base64.
`Basic` credentials for the password `pw` render as `Basic cHc=`, whose last
four characters `cHc=` are a complete base64 quad decoding straight back to
`pw` — the whole password. Quads decode independently, so a visible 4-character
tail is always 1-3 plaintext bytes of the credential, and for a short password
it is all of it. A `len > 4` guard does not help, because an `Authorization`
header is long even when the secret inside it is short. Length alone still
answers the question the inspector is for — "is my token the same length as the
one Bruno sends?" — without emitting any credential byte.

Header names are matched case-insensitively, in two layers.

**Explicit names**, always secret:

- `Authorization`
- `Cookie`
- `Set-Cookie`
- `X-SAP-LogonToken`
- **`entry.api_key_header`, read from config** — not a hardcoded name. An
  endpoint configured with `X-Custom-Token` must not leak.

**Name-pattern matching**, on top of that list: a header is redacted if its
lowercased name *contains* any of 18 substrings — `secret`, `token`, `key`,
`auth`, `password`, `passwd`, `credential`, `signature`, `session`, `cookie`,
`bearer`, `jwt`, `sig`, `hmac`, `otp`, `nonce`, `cert`, `pw`. A fixed
allowlist cannot be complete, because `ApiEndpointEntry.headers` is a free-form
dict the user fills in — a credential parked under `X-Client-Secret` would
otherwise render in cleartext in a browser. Matching is on the header **name**
only, never the value: inferring secrecy from a value's shape would redact real
response data, which is the one thing this inspector exists to show.

This deliberately over-redacts. `Idempotency-Key` and `X-Request-Signature` are
masked despite not being credentials, costing a little diff fidelity against
Bruno. That is the accepted trade — the opposite failure prints a live
credential into a web page. The headers that actually matter for a Bruno
comparison (Content-Type, Accept, Accept-Encoding, User-Agent, Content-Length)
contain none of these substrings and stay visible.

**Residual risk**: name-pattern matching cannot be exhaustive. A credential in
an arbitrarily-named header still displays. This is mitigation, not a
guarantee; the measured leak list is kept in the module's own comment, next to
the pattern tuple, so the two cannot drift apart.

The **whole matcher runs over response headers too**, not only `Set-Cookie`.
Response and request headers go through the same `redact_headers(headers,
entry)` call.

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
| DNS / timeout / proxy refused | `_request`, no response object exists | No — there is no response object to capture. The list stays empty and the user gets the friendly error only; see "Not built" |

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
- The SAP BO logon exclusion is **not** pinned by a test. The property holds by
  construction — `_get_sap_bo_token` posts on `self._session` directly and is
  never handed a sink, so there is nothing to suppress — but nothing fails if a
  future edit threads one in. Recorded as a known gap rather than claimed as
  covered.

Routes:

- Preview failure returns 502 carrying both `detail.message` and
  `detail.exchange`.
- `AdapterTestOut.exchange` round-trips.

End to end:

- The inspector panel passes the existing WCAG gate.

## Not built

Specified above in an earlier draft, and **not** in the shipped build. Listed
here so the spec cannot be read as a description of working behaviour.

- **`artifact_path` on the exchange payload.** `capture_exchange` emits
  `request` and `response` and nothing else. The artifact sink and the capture
  sink run side by side and never exchange the path that was written, so there
  is no path to report.

  The consequence, plainly: **nothing in the product tells a user that raw
  responses are written to disk, or where.** A body over `BODY_LIMIT` shows as
  "truncated" in the inspector with no way to reach the full bytes from the
  UI — the untruncated response exists on the server, under a directory name
  the user has never been shown. Recovering it means server filesystem access.

- **A "no response received" message on a DNS / timeout / proxy failure.** No
  such string exists anywhere in the frontend. When no response object was ever
  created the exchange stays `null`, and `x-show="ep.exchange"` simply hides
  the whole inspector panel; the user sees the friendly error alone, with no
  statement that the absence of an exchange is itself the diagnosis.

## Out of scope

Explicitly excluded from this spec, after scoping:

- Validating the request body in the config UI. The silent
  `catch { body = null }` in `frontend/features/config.js` stays. **If that is
  the root cause of a given failure, this inspector shows the empty body rather
  than preventing it.**
- Methods beyond GET and POST.
- Importing from cURL or a Bruno `.bru` file.
