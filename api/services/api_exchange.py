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
