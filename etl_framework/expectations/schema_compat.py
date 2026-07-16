"""Classify schema-snapshot diffs by consumer compatibility.

Levels, worst wins: full < non_breaking < risky < breaking.
Widening numeric changes keep every representable value, so consumers keep
working; narrowing can silently truncate — that's breaking.
"""
from __future__ import annotations

from typing import Any

_SEVERITY_ORDER = ["full", "non_breaking", "risky", "breaking"]

# Partial order of "safely widenable" numeric dtypes (pandas dtype strings).
_NUMERIC_WIDTH = {
    "int8": 0, "int16": 1, "int32": 2, "int64": 3,
    "uint8": 0, "uint16": 1, "uint32": 2, "uint64": 3,
    "float32": 4, "float64": 5,
}


def classify_type_change(from_dtype: str, to_dtype: str) -> str:
    old = _NUMERIC_WIDTH.get(from_dtype.lower())
    new = _NUMERIC_WIDTH.get(to_dtype.lower())
    if old is not None and new is not None:
        return "non_breaking" if new >= old else "breaking"
    return "risky"


def classify_diff(diff: dict[str, Any]) -> dict[str, Any]:
    """Return *diff* with per-change and overall ``compatibility`` keys added."""
    result = dict(diff)
    levels = ["full"]
    if diff.get("added"):
        levels.append("non_breaking")
    if diff.get("removed"):
        levels.append("breaking")
    changed = []
    for change in diff.get("changed") or []:
        level = classify_type_change(change["from"], change["to"])
        changed.append({**change, "compatibility": level})
        levels.append(level)
    result["changed"] = changed
    result["compatibility"] = max(levels, key=_SEVERITY_ORDER.index)
    return result
