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


def _safe_filename(name: str | None, fallback: str) -> str:
    raw = Path(name or fallback).name or fallback
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return (safe or fallback)[:160]


def _persist_bytes(run_id: str, data: bytes, filename: str | None, fallback: str) -> str:
    run_dir = UPLOAD_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_filename(filename, fallback)
    path = run_dir / name
    if path.exists():
        stem = path.stem
        suffix = path.suffix
        idx = 2
        while path.exists():
            path = run_dir / f"{stem}_{idx}{suffix}"
            idx += 1
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
