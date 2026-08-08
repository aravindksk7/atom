"""Write a copy of a SAP BO export into a directory the operator nominated.

Separate from api_artifact.py on purpose. That module manages the app's own
artifact root, keyed by run and config, and may prune it. This one writes into
a path someone typed on the Config tab — possibly a shared drive holding
unrelated files — so it only ever creates, never deletes, and never raises.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

# Same mapping the download routes apply (api/routes/adapters.py). Carried
# here rather than imported because routes -> service -> archive would become
# circular, and it is one line.
_EXT_MAP = {"pdf": "pdf", "xlsx": "xlsx", "csv": "csv"}

# doc_id and report_id come straight off the URL path.
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def _safe(value: str) -> str:
    return _UNSAFE.sub("_", str(value))


def save_bo_download(content: bytes, *, doc_id: str, report_id: str, fmt: str,
                     directory: str, now: datetime | None = None
                     ) -> tuple[Path | None, str | None]:
    """Write a BO export to `directory`. Never raises.

    (path, None)  wrote it
    (None, error) tried and failed
    (None, None)  disabled, because `directory` is empty
    """
    if not (directory or "").strip():
        return None, None

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    ext = _EXT_MAP.get(fmt, "bin")
    parts = ["report", _safe(doc_id)]
    if str(report_id or "").strip():
        parts.append(_safe(report_id))
    stem = "_".join(parts) + "_" + stamp

    try:
        target_dir = Path(directory)
        candidate = target_dir / f"{stem}.{ext}"
        # Never overwrite: two downloads inside one second share a stamp.
        suffix = 0
        while candidate.exists():
            suffix += 1
            candidate = target_dir / f"{stem}-{suffix}.{ext}"
        candidate.write_bytes(content)
        return candidate, None
    except OSError as exc:
        return None, str(exc)
