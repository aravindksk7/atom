#!/usr/bin/env python3
"""Splice a markdown block into README.md between marker comments.

Usage:
    python splice_readme.py <readme_path> <markdown_content_path>

Exits non-zero with a clear message if the marker comments are not both
present in the target file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

START_MARKER = "<!-- ATOM:JOB-STATUS:START -->"
END_MARKER = "<!-- ATOM:JOB-STATUS:END -->"


class MarkersNotFoundError(Exception):
    pass


def splice(original: str, new_content: str) -> str:
    if START_MARKER in new_content or END_MARKER in new_content:
        raise MarkersNotFoundError(
            "new_content must not contain the ATOM:JOB-STATUS marker comments"
        )
    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        re.DOTALL,
    )
    if not pattern.search(original):
        raise MarkersNotFoundError(
            f"Could not find both {START_MARKER} and {END_MARKER} markers in the target file."
        )
    replacement = f"{START_MARKER}\n{new_content}\n{END_MARKER}"
    return pattern.sub(replacement, original, count=1)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: splice_readme.py <readme_path> <markdown_content_path>", file=sys.stderr)
        return 2
    readme_path = Path(sys.argv[1])
    content_path = Path(sys.argv[2])

    try:
        original = readme_path.read_text(encoding="utf-8")
        new_content = content_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        updated = splice(original, new_content)
    except MarkersNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    readme_path.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
