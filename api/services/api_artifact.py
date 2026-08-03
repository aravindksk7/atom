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
