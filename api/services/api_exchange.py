"""Build a redacted request/response pair for the config UI's inspector.

Pure transformation: no filesystem, no network. Fed by the same `on_response`
callback the artifact sink uses.
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger("api.services.api_exchange")

_ALWAYS_SECRET = {"authorization", "cookie", "set-cookie", "x-sap-logontoken"}

# Substrings that make a header name suspicious. `ApiEndpointEntry.headers` is a
# free-form dict the user fills in, so no enumeration of names can be complete —
# a credential parked under `X-Client-Secret` would otherwise render in cleartext
# in a browser. Matching is on the header NAME only, never the value: guessing
# secrecy from a value's shape would redact real response data, which is the one
# thing this inspector exists to show.
#
# This deliberately over-redacts. `Idempotency-Key`, `X-Request-Signature` and
# similar get masked despite not being credentials, costing a little diff
# fidelity against Bruno. That is the accepted trade: the alternative failure is
# printing a live credential into a web page. The headers that actually matter
# for a Bruno comparison — Content-Type, Accept, Accept-Encoding, User-Agent,
# Content-Length — contain none of these substrings and stay visible.
_SECRET_NAME_PATTERNS = (
    "secret", "token", "key", "auth", "password", "passwd",
    "credential", "signature", "session", "cookie",
)

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


def _is_secret_name(name: str, explicit: set[str]) -> bool:
    lowered = str(name).lower()
    if lowered in explicit:
        return True
    return any(pattern in lowered for pattern in _SECRET_NAME_PATTERNS)


def _mask(value: str) -> str:
    text = str(value)
    return f"<{len(text)} chars, ...{text[-4:]}>" if len(text) > 4 else f"<{len(text)} chars>"


def redact_headers(headers, entry) -> dict:
    explicit = _secret_header_names(entry)
    return {
        key: (_mask(value) if _is_secret_name(key, explicit) else value)
        for key, value in (headers or {}).items()
    }


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


def capture_exchange(entry) -> tuple[Callable, list[dict]]:
    """Return an `on_response` sink and the list it fills.

    Takes `entry` because redaction depends on the endpoint's configured
    `api_key_header`. Never raises: an inspector that breaks a pull is worse
    than no inspector. Failures are logged with a traceback rather than
    swallowed silently — because this sink returns normally after catching,
    the client's own `on_response` guard never fires, so a bare `pass` here
    would make a systematic capture failure completely invisible and
    reproduce the no-status/no-body blindness this feature exists to cure.

    The body and byte count come from `raw_bytes`, the callback's own
    contract; `response` is used only for headers, status, elapsed and
    history.
    """
    seen: list[dict] = []

    def sink(raw_bytes: bytes, page_index: int, response) -> None:
        try:
            raw_bytes = raw_bytes or b""
            request = response.request
            req_headers = redact_headers(dict(request.headers or {}), entry)
            req_body = request.body
            if isinstance(req_body, bytes):
                req_body = req_body.decode("utf-8", "replace")
            resp_headers = dict(response.headers or {})
            content_type = resp_headers.get("Content-Type", "")
            body, truncated, binary = render_body(raw_bytes, content_type)
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
                    "bytes": len(raw_bytes),
                    "content_type": content_type,
                    "redirects": len(getattr(response, "history", None) or []),
                    "headers": redact_headers(resp_headers, entry),
                    "body": body,
                    "truncated": truncated,
                    "binary": binary,
                },
            })
        except Exception:  # noqa: BLE001 - an observer cannot break a pull
            logger.warning(
                "Could not capture the request/response exchange for page %d; "
                "it will be missing from the inspector",
                page_index, exc_info=True,
            )

    return sink, seen
