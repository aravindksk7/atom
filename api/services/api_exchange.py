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
#
# RESIDUAL RISK — name-pattern matching cannot be exhaustive. A user who parks a
# credential in an arbitrarily-named header will have it displayed. These header
# names were measured against this list and slip through in cleartext:
#
#   X-Client-Id, X-Signed-Blob, X-Bearer, X-Hmac, X-Nonce, X-Access, X-Jwt,
#   X-Api-Sig, X-Csrf, X-Otp, X-Pin, X-Salt, X-Refresh, X-Sso,
#   X-Saml-Assertion, X-Private-Cert, X-Client-Certificate, X-Bearer-Assertion,
#   X-Tenant-Pw, X-License
#
# (Several of those are now covered by the patterns below; the list is recorded
# as measured, and the general point stands regardless of how far the list is
# extended — this is mitigation, not a guarantee.)
_SECRET_NAME_PATTERNS = (
    "secret", "token", "key", "auth", "password", "passwd",
    "credential", "signature", "session", "cookie",
    "bearer", "jwt", "sig", "hmac", "otp", "nonce", "cert", "pw",
)

# A budget in BYTES, applied to the raw payload before it is decoded, so the
# cap bounds memory on the decode itself rather than after it.
BODY_LIMIT = 8192
BINARY_PREVIEW_BYTES = 200

_TEXTUAL_CONTENT_TYPES = (
    "application/json", "text/", "application/xml", "application/x-www-form-urlencoded",
    "application/csv", "application/javascript",
)

# Structured syntax suffixes (RFC 6839). `application/problem+json` is RFC 7807,
# the standard machine-readable error document and so the likeliest content type
# on exactly the failure this inspector exists to diagnose; hex-dumping it would
# defeat the purpose. Same for `application/xhtml+xml`, `application/vnd.api+json`,
# `application/hal+json` and `application/ld+json`.
_TEXTUAL_CONTENT_SUFFIXES = ("+json", "+xml")


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
    """Replace a secret with its length only — never any of its bytes.

    An earlier version showed the last four characters. That is unsafe for
    base64: `Basic` credentials for the password `pw` render as
    `Basic cHc=`, whose last four characters `cHc=` are a complete base64
    quad decoding back to `pw` — the entire password. Quads decode
    independently, so a visible tail is always 1-3 plaintext bytes of the
    credential, and for a short password it is all of it. The old
    `len > 4` guard never helped, because an `Authorization` header is long
    even when the secret inside it is not. Length alone still answers the
    question the inspector is for ("is my token the same length as the one
    Bruno sends?") without emitting credential bytes.
    """
    return f"<{len(str(value))} chars, redacted>"


def redact_headers(headers, entry) -> dict:
    explicit = _secret_header_names(entry)
    return {
        key: (_mask(value) if _is_secret_name(key, explicit) else value)
        for key, value in (headers or {}).items()
    }


def _is_textual(content_type: str) -> bool:
    """Whether a body should be shown as text rather than hex.

    An absent or empty content type counts as textual. The failure this
    inspector exists to diagnose is described as carrying "no status, no
    content type and no body" — so the one response a human most needs to
    read is precisely the one with nothing declared. Showing its bytes as
    text is what is wanted there; the mojibake risk is bounded by
    `errors="replace"` and is a fair price.
    """
    base = (content_type or "").split(";")[0].strip().lower()
    if not base:
        return True
    if any(base.startswith(prefix) for prefix in _TEXTUAL_CONTENT_TYPES):
        return True
    return any(base.endswith(suffix) for suffix in _TEXTUAL_CONTENT_SUFFIXES)


def _decode_within_budget(raw: bytes) -> tuple[str, bool]:
    """Decode at most BODY_LIMIT bytes, reporting whether input exceeded it.

    The slice happens before the decode so the cap is a real bound on the
    work done, not a trim applied after a whole multi-megabyte payload has
    already been turned into a str. A multi-byte character straddling the
    cut decodes to replacement characters rather than corrupting anything.
    """
    return raw[:BODY_LIMIT].decode("utf-8", "replace"), len(raw) > BODY_LIMIT


def render_body(raw: bytes, content_type: str) -> tuple[str, bool, bool]:
    """Return (body_text, truncated, binary) for display.

    A body with a declared non-textual content type is never decoded: it is
    shown as hex, so a PNG or an xlsx cannot produce a screenful of mojibake.
    An undeclared type takes the text path — see `_is_textual`.
    """
    raw = raw or b""
    if not _is_textual(content_type):
        return raw[:BINARY_PREVIEW_BYTES].hex(), len(raw) > BINARY_PREVIEW_BYTES, True
    text, truncated = _decode_within_budget(raw)
    return text, truncated, False


def render_request_body(raw) -> tuple[str | None, bool]:
    """Return (body_text, truncated) for a request body, or (None, False).

    None is preserved rather than coerced to "": a dropped request body is
    the failure this inspector exists to make visible. The same BODY_LIMIT
    applies here as to responses — without it a 2 MB POST body became a
    2,000,000-character string in the payload.
    """
    if raw is None:
        return None, False
    if isinstance(raw, bytes):
        return _decode_within_budget(raw)
    text = str(raw)
    return text[:BODY_LIMIT], len(text) > BODY_LIMIT


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
            req_body, req_truncated = render_request_body(request.body)
            resp_headers = dict(response.headers or {})
            content_type = resp_headers.get("Content-Type", "")
            body, truncated, binary = render_body(raw_bytes, content_type)
            seen.append({
                "request": {
                    "method": request.method,
                    # The URL is captured whole, query string included, so a
                    # credential in `entry.query_params` (?api_key=…) shows in
                    # cleartext. Deliberate, on the spec's "same user, same
                    # modal" rationale. Noted because that same rationale was
                    # judged insufficient for `entry.headers`, which is
                    # pattern-redacted above — the asymmetry is real and worth
                    # revisiting if these exchanges ever leave the modal.
                    "url": str(request.url),
                    "headers": req_headers,
                    "body": req_body,
                    "truncated": req_truncated,
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
