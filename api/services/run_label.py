"""Human-readable handle for a run.

A run's identity is a bare uuid4 (`00a638ef-5743-401c-9324-182b9427c914`), which
tells a reader nothing: not what kind of comparison it was, not which
environments it touched, and not which of a dozen similar runs it is. Widening
or reformatting that identifier is expensive -- it is a foreign key in six
tables, an export directory name on disk, and a URL parameter -- so this derives
a display label from data the run already carries instead.

The label is computed here once, server-side, and shipped to the browser on the
run DTO, so the UI and the downloadable HTML report cannot drift apart.
"""
from __future__ import annotations

from typing import Any

# The specific comparison a run performed lives in config_snapshot, not in
# run_type (which is "reconciliation" for all of them). Prefer it when present.
_REQUEST_TYPE_NAMES = {
    "sql_compare": "SQL compare",
    "bo_compare": "BO compare",
    "recon_file": "file compare",
    "multi_file": "multi-file compare",
    "stored_run": "stored-run compare",
}

_RUN_TYPE_NAMES = {
    "reconciliation": "recon",
    "test_suite": "test suite",
}

SEPARATOR = " · "
SHORT_ID_LENGTH = 8


def short_run_id(run_id: Any) -> str:
    """First segment of the uuid -- long enough to be unique in practice, short
    enough to read aloud."""
    text = str(run_id or "")
    return text[:SHORT_ID_LENGTH]


def _kind(run_type: Any, config_snapshot: Any) -> str:
    if isinstance(config_snapshot, dict):
        request_type = config_snapshot.get("compare_request_type")
        if request_type and request_type != "unknown":
            named = _REQUEST_TYPE_NAMES.get(str(request_type))
            if named:
                return named
            # An unmapped request type is still more specific than run_type.
            return str(request_type).replace("_", " ")
    raw = str(run_type or "").strip()
    if not raw:
        return "run"
    return _RUN_TYPE_NAMES.get(raw, raw.replace("_", " "))


def _environments(source_env: Any, target_env: Any) -> str | None:
    source = str(source_env).strip() if source_env else ""
    target = str(target_env).strip() if target_env else ""
    if source and target:
        return f"{source} → {target}"
    return source or target or None


def run_display_label(
    run_id: Any,
    run_type: Any = None,
    source_env: Any = None,
    target_env: Any = None,
    config_snapshot: Any = None,
) -> str:
    """Build the label, e.g. "file compare · dev → prod · 00a638ef".

    Deliberately carries no timestamp: both surfaces already show the run's start
    time next to the label, and a time here would need timezone plumbing to stay
    consistent with it. The short id keeps same-kind, same-env runs distinct.
    """
    parts = [_kind(run_type, config_snapshot)]
    environments = _environments(source_env, target_env)
    if environments:
        parts.append(environments)
    short = short_run_id(run_id)
    if short:
        parts.append(short)
    return SEPARATOR.join(parts)


def run_display_label_for(run: Any) -> str:
    """Same, read off any object exposing the run's attributes."""
    return run_display_label(
        run_id=getattr(run, "run_id", None),
        run_type=getattr(run, "run_type", None),
        source_env=getattr(run, "source_env", None),
        target_env=getattr(run, "target_env", None),
        config_snapshot=getattr(run, "config_snapshot", None),
    )
