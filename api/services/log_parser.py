"""Log-parsing utilities for the runs API.

Functions here are pure (no I/O, no DB) so they can be unit-tested in isolation.
"""
from __future__ import annotations

import re


def detect_log_level(line: str) -> str:
    for level in ("ERROR", "WARNING", "WARN", "INFO", "DEBUG"):
        if f"| {level}" in line or line.startswith(level) or f" {level} " in line:
            return level
    return ""


def parse_log_events(text: str) -> list[dict]:
    events: list[dict] = []
    current: dict | None = None
    for idx, line in enumerate(text.splitlines(), start=1):
        detected = detect_log_level(line)
        starts_event = bool(
            detected or re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", line)
        )
        if starts_event:
            if current is not None:
                current["text"] = "\n".join(current.pop("lines"))
                events.append(current)
            current = {"number": idx, "level": detected or "TRACE", "lines": [line]}
        elif current is not None:
            current["lines"].append(line)
        else:
            current = {"number": idx, "level": "TRACE", "lines": [line]}
    if current is not None:
        current["text"] = "\n".join(current.pop("lines"))
        events.append(current)
    return events


def filter_log_events(
    text: str,
    run_id: str = "",
    query: str = "",
    level: str = "",
    limit: int = 500,
) -> list[dict]:
    query_l = query.lower().strip()
    level_u = level.upper().strip()
    run_l = run_id.lower().strip()
    matches: list[dict] = []
    for event in parse_log_events(text):
        body_l = event["text"].lower()
        detected = event["level"]
        if run_l and run_l not in body_l:
            continue
        if query_l and query_l not in body_l:
            continue
        if level_u and detected != level_u and not (level_u == "WARN" and detected == "WARNING"):
            continue
        matches.append(event)
    return matches[-max(1, min(limit, 5000)):]
