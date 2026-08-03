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
from urllib.parse import unquote

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

# RFC 6266 defines two filename parameters on Content-Disposition:
#   filename=value          (a quoted-string or a token; RFC 2183/6266 legacy form)
#   filename*=ext-value      (RFC 5987: charset'language'percent-encoded-value)
# filename* takes precedence when present and parseable (RFC 6266 section 4.3).
_FILENAME_STAR_RE = re.compile(r"filename\*\s*=\s*([^;]+)", re.IGNORECASE)
_FILENAME_QUOTED_RE = re.compile(r'filename(?!\*)\s*=\s*"([^"]*)"', re.IGNORECASE)
_FILENAME_UNQUOTED_RE = re.compile(r"filename(?!\*)\s*=\s*([^;\s]+)", re.IGNORECASE)


def _decode_extended_value(ext_value: str) -> str | None:
    """Decode an RFC 5987 ext-value (`charset'language'percent-encoded`).

    Returns None on any parse or decode failure — an unknown charset or a
    malformed value must fall back to the plain `filename`, never raise and
    break the pull.
    """
    parts = ext_value.strip().split("'", 2)
    if len(parts) != 3:
        return None
    charset, _language, encoded_value = parts
    charset = charset.strip() or "utf-8"
    try:
        decoded = unquote(encoded_value, encoding=charset, errors="strict")
    except (LookupError, UnicodeDecodeError, UnicodeError):
        return None
    return decoded or None


def _plain_filename(raw: str) -> str | None:
    match = _FILENAME_QUOTED_RE.search(raw)
    if match:
        return match.group(1)
    match = _FILENAME_UNQUOTED_RE.search(raw)
    return match.group(1).strip() if match else None


def _disposition_filename(response) -> str | None:
    """Filename from Content-Disposition, honoring RFC 6266 precedence.

    `filename*` (percent-encoded, with an explicit charset) wins over the
    plain `filename` when present and decodable; a malformed or
    unrecognised-charset `filename*` falls back to the plain `filename`
    rather than being treated as absent-and-fatal.
    """
    raw = (getattr(response, "headers", None) or {}).get("Content-Disposition") or ""
    star_match = _FILENAME_STAR_RE.search(raw)
    if star_match:
        decoded = _decode_extended_value(star_match.group(1))
        if decoded is not None:
            return decoded
    return _plain_filename(raw)


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
