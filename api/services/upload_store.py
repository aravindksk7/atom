from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("api.services.upload_store")


UPLOAD_ROOT = Path(os.environ.get("COMPARE_UPLOAD_ROOT", "reports/uploads")).resolve()

# Cap on a single persisted run data artifact. Report downloads can be very large
# and these files live under the same retention sweep as compare uploads, so skip
# anything past the cap rather than filling the on-prem disk.
RUN_DATA_ARTIFACT_MAX_BYTES = int(
    os.environ.get("RUN_DATA_ARTIFACT_MAX_MB", "256")
) * 1024 * 1024


# Windows treats these device stems as reserved regardless of extension or
# case (NUL.txt, com1.dat, ... all resolve to the device). Path.exists() is
# always True for them and writes silently go nowhere instead of raising.
_WINDOWS_RESERVED_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(name: str | None, fallback: str) -> str:
    """Return a bare, filesystem-safe basename derived from `name`.

    This is a security boundary for untrusted, remote-supplied names (e.g.
    an uploaded file name or a Content-Disposition header from a remote
    server): the result contains no path separators or drive/UNC syntax,
    is restricted to `[A-Za-z0-9._-]`, is at most 160 characters, and
    Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9) are
    defused with a leading underscore so they can never resolve to a device
    file. Falls back to `fallback` if nothing usable survives sanitization.
    """
    raw = Path(name or fallback).name or fallback
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    safe = safe or fallback
    stem = safe.split(".", 1)[0]
    if stem.upper() in _WINDOWS_RESERVED_STEMS:
        safe = f"_{safe}"
    return safe[:160]


# Precautionary alias in case something outside this module still imports
# the old private name; no current caller depends on it.
_safe_filename = safe_filename


def unique_path(directory: Path, name: str) -> Path:
    """A path under `directory` that does not exist yet, suffixing _2, _3, ...

    This only checks existence at call time (TOCTOU): a concurrent writer
    could create the returned path between this check and when the caller
    writes to it. Callers needing a hard guarantee must use an exclusive
    create (e.g. open with O_EXCL) rather than relying on this alone.
    """
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


def _persist_b64(run_id: str, raw_b64: str, filename: str | None, fallback: str) -> str:
    return _persist_bytes(run_id, base64.b64decode(raw_b64), filename, fallback)


def persist_run_data_artifact(run_id: str, data: bytes, file_name: str) -> str | None:
    """Store the raw data a run fetched, so later compares can re-read it as a frame.

    Returns the path written, or None when the payload exceeds
    RUN_DATA_ARTIFACT_MAX_BYTES. Callers must treat this as best-effort: a run
    must still succeed when its artifact cannot be kept.
    """
    if len(data) > RUN_DATA_ARTIFACT_MAX_BYTES:
        logger.warning(
            "Run %s data artifact %s is %d bytes, past the %d-byte cap — not persisted",
            run_id, file_name, len(data), RUN_DATA_ARTIFACT_MAX_BYTES,
        )
        return None
    return _persist_bytes(run_id, data, file_name, "run_data.dat")


def resolve_run_data_artifact(path: str | None) -> Path | None:
    """Resolve a stored artifact path, or None if it escaped UPLOAD_ROOT or is gone.

    The containment check keeps a tampered database value from turning into an
    arbitrary-file read on a later compare.
    """
    if not path:
        return None
    root = UPLOAD_ROOT.resolve()
    try:
        resolved = Path(path).resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _persist_source_config(run_id: str, source: dict[str, Any], label: str) -> dict[str, Any]:
    sanitized = dict(source or {})
    raw_b64 = sanitized.get("file_content_b64")
    if sanitized.get("source_type") == "upload" and raw_b64:
        path = _persist_b64(
            run_id,
            str(raw_b64),
            sanitized.get("file_name"),
            f"{label}.dat",
        )
        sanitized["source_type"] = "path"
        sanitized["file_path"] = path
        sanitized["file_content_b64"] = None
    return sanitized


def sanitize_compare_request(run_id: str, request_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist upload bytes and return a config_snapshot-safe compare payload."""
    sanitized = dict(payload or {})
    if request_type in {"bo_report", "column_stats"}:
        if isinstance(sanitized.get("source_a"), dict):
            sanitized["source_a"] = _persist_source_config(run_id, sanitized["source_a"], "source_a")
        if isinstance(sanitized.get("source_b"), dict):
            sanitized["source_b"] = _persist_source_config(run_id, sanitized["source_b"], "source_b")
    elif request_type == "recon_file":
        for side in ("a", "b"):
            content_key = f"file_{side}_content_b64"
            path_key = f"file_{side}_path"
            name_key = f"file_{side}_name"
            raw_b64 = sanitized.get(content_key)
            if raw_b64:
                path = _persist_b64(
                    run_id,
                    str(raw_b64),
                    sanitized.get(name_key),
                    f"file_{side}.dat",
                )
                sanitized[path_key] = path
                sanitized[content_key] = None
    return {
        "compare_request_type": request_type,
        "request": sanitized,
        "upload_root": str((UPLOAD_ROOT / run_id).resolve()),
    }


def cleanup_expired_uploads(retention_days: int, root: Path = UPLOAD_ROOT) -> int:
    """Delete per-run upload directories older than the configured retention."""
    if retention_days < 1 or not root.exists():
        return 0
    cutoff = time.time() - (retention_days * 86400)
    removed = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child)
                removed += 1
        except OSError:
            continue
    return removed
