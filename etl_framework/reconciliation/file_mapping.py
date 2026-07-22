# etl_framework/reconciliation/file_mapping.py
"""Shared file discovery, pairing, and result-aggregation for multi-file
(1:M / M:N) reconciliation jobs.

This module owns the file-mapping logic that ``api/schemas.py``,
``etl_framework/runner/job_validation.py``, and ``api/services/run_executor.py``
all need, so those three call sites share one implementation instead of each
re-deriving the same "source_mode" file-path rules (see the architecture doc
in docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md
for why that triplication existed before this module).
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"\{(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)(?::(?P<spec>[^}]*))?\}")

_STRFTIME_DIGIT_WIDTH = {"%Y": 4, "%m": 2, "%d": 2, "%H": 2, "%M": 2, "%S": 2}


def _spec_to_regex(spec: str | None) -> str:
    if not spec:
        return r"[^_./\\]+"
    out: list[str] = []
    i = 0
    while i < len(spec):
        two = spec[i:i + 2]
        if two in _STRFTIME_DIGIT_WIDTH:
            out.append(r"\d{%d}" % _STRFTIME_DIGIT_WIDTH[two])
            i += 2
        else:
            out.append(re.escape(spec[i]))
            i += 1
    return "".join(out)


def _glob_segment_to_regex(segment: str) -> str:
    """Translate bare glob characters (``*``, ``?``) outside any ``{token}``
    into regex, escaping everything else literally."""
    out: list[str] = []
    for ch in segment:
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def compile_token_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a filename pattern into a named-group regex.

    Two placeholder kinds are supported and may be mixed:
    - ``{token}`` / ``{token:%Y%m%d}`` -- a named capture group used for
      pairing (see ``pair_files``). ``%Y``/``%m``/``%d``/``%H``/``%M``/``%S``
      in the spec become fixed-width digit groups; any other spec text is
      matched literally.
    - bare ``*`` / ``?`` -- plain glob wildcards, for patterns that need
      dynamic discovery but no pairing key (see ``FileMappingSpec``).
    """
    regex_parts: list[str] = []
    pos = 0
    for match in _TOKEN_RE.finditer(pattern):
        regex_parts.append(_glob_segment_to_regex(pattern[pos:match.start()]))
        name = match.group("name")
        spec = match.group("spec")
        regex_parts.append(f"(?P<{name}>{_spec_to_regex(spec)})")
        pos = match.end()
    regex_parts.append(_glob_segment_to_regex(pattern[pos:]))
    return re.compile("^" + "".join(regex_parts) + "$")
