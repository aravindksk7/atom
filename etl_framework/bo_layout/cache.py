"""Disk-backed JSON parse cache for BO PDF read and preview results."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from etl_framework.bo_layout.model import PdfReadOptions

PARSER_VERSION = "1"


def _cache_dir() -> Path:
    raw = os.environ.get("ATOM_PDF_CACHE_DIR")
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "atom_pdf_cache"


def _max_entries() -> int:
    raw = os.environ.get("ATOM_PDF_CACHE_MAX_ENTRIES", "64")
    try:
        val = int(raw)
    except ValueError:
        return 64
    return max(0, val)


def cache_key(raw_sha256: str, mode: str, options: PdfReadOptions) -> str:
    """Compute a deterministic cache key from parser version, mode, PDF sha256, and options."""
    opts_json = json.dumps(options.to_dict(), sort_keys=True)
    material = f"{PARSER_VERSION}|{mode}|{raw_sha256}|{opts_json}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def cache_get(key: str) -> dict[str, Any] | None:
    """Return cached payload for `key`, deleting corrupt entries as misses."""
    if _max_entries() <= 0:
        return None
    path = _cache_dir() / f"{key}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None
    if not isinstance(data, dict) or data.get("ok") is not True:
        path.unlink(missing_ok=True)
        return None
    return data


def _prune_old_entries(cache_dir: Path, max_entries: int) -> None:
    entries: list[tuple[int, Path]] = []
    for item in cache_dir.iterdir():
        if item.suffix != ".json" or not item.is_file():
            continue
        try:
            entries.append((item.stat().st_mtime_ns, item))
        except OSError:
            continue
    if len(entries) <= max_entries:
        return
    entries.sort(key=lambda pair: pair[0])
    for _, old_path in entries[: len(entries) - max_entries]:
        old_path.unlink(missing_ok=True)


def cache_put(key: str, payload: dict[str, Any]) -> None:
    """Atomically store an `ok: True` payload and prune oldest entries past the limit."""
    max_entries = _max_entries()
    if max_entries <= 0:
        return
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return

    cache_dir = _cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{key}.json"
    encoded = json.dumps(payload)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{key}.", suffix=".tmp", dir=str(cache_dir))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(encoded)
        os.replace(tmp_path, target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    # Ensure strictly monotonic mtime_ns even under sub-tick writes on Windows
    try:
        target_mtime = target.stat().st_mtime_ns
        max_other = 0
        for item in cache_dir.iterdir():
            if item == target or item.suffix != ".json":
                continue
            try:
                max_other = max(max_other, item.stat().st_mtime_ns)
            except OSError:
                continue
        if max_other >= target_mtime:
            now_ns = time.time_ns()
            os.utime(target, ns=(now_ns, max_other + 1_000_000))
    except OSError:
        pass

    _prune_old_entries(cache_dir, max_entries)
