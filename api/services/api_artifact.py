"""Persist raw REST API endpoint responses under the server's artifact root.

The HTTP client itself stays filesystem-agnostic: `etl_framework/` must not
import `api/services/`. This module derives the artifact filename for a
response and provides `build_api_response_sink`, the callback the client
invokes to actually write it. Layout, size caps and retention all live here.
"""
from __future__ import annotations

import logging
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
#
# A regex .search() over the whole header can't implement this correctly: it
# has no notion of "parameter boundary" (so a vendor param like
# `original-filename` gets matched as if it were `filename`) and no notion of
# quote state (so a ';' or "filename*=" embedded inside a quoted value looks
# like a real delimiter/parameter). A small left-to-right tokenizer that
# tracks quote state fixes both by construction.


def _iter_disposition_params(raw: str):
    """Yield (name, value) pairs from a Content-Disposition header.

    Splits on ';' only when not inside a double-quoted value, so a ';'
    embedded in a quoted filename is data, not a delimiter. The leading
    disposition-type token (e.g. "attachment", "inline") is skipped.
    Whitespace around '=' is tolerated; a quoted value has its surrounding
    quotes stripped (no backslash-escape processing — real servers don't use
    it here, and everything downstream still goes through `safe_filename`).
    """
    segments: list[str] = []
    current: list[str] = []
    in_quotes = False
    for ch in raw:
        if ch == '"':
            in_quotes = not in_quotes
            current.append(ch)
        elif ch == ";" and not in_quotes:
            segments.append("".join(current))
            current = []
        else:
            current.append(ch)
    segments.append("".join(current))

    for segment in segments[1:]:  # segments[0] is the disposition type
        if "=" not in segment:
            continue
        name, _, value = segment.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        if name:
            yield name, value


def _decode_extended_value(ext_value: str) -> str | None:
    """Decode an RFC 5987 ext-value (`charset'language'percent-encoded`).

    Returns None on any parse or decode failure — an unknown charset or a
    malformed value must fall back to the plain `filename`, never raise and
    break the pull. Guarantee holds for `str` input, which is all this ever
    receives from `_iter_disposition_params`.
    """
    if not isinstance(ext_value, str):
        return None
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


def _disposition_filename(response) -> str | None:
    """Filename from Content-Disposition, honoring RFC 6266 precedence.

    `filename*` (percent-encoded, with an explicit charset) wins over the
    plain `filename` when present and decodable; a malformed or
    unrecognised-charset `filename*` falls back to the plain `filename`
    rather than being treated as absent-and-fatal. Parameter names are
    matched case-insensitively. On a duplicate parameter, the first
    occurrence wins.
    """
    raw = (getattr(response, "headers", None) or {}).get("Content-Disposition") or ""
    star_value: str | None = None
    plain_value: str | None = None
    for name, value in _iter_disposition_params(raw):
        lname = name.lower()
        if lname == "filename*" and star_value is None:
            star_value = value
        elif lname == "filename" and plain_value is None:
            plain_value = value
    if star_value is not None:
        decoded = _decode_extended_value(star_value)
        if decoded is not None:
            return decoded
    return plain_value


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
