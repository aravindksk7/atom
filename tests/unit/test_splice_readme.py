"""Tests for scripts/ci/splice_readme.py's marker-splice logic."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "ci"))

import pytest
from splice_readme import splice, MarkersNotFoundError

START = "<!-- ATOM:JOB-STATUS:START -->"
END = "<!-- ATOM:JOB-STATUS:END -->"


def test_splice_replaces_content_between_markers():
    original = f"# Title\n\n{START}\nold content\n{END}\n\nmore text\n"
    result = splice(original, "new content")
    assert result == f"# Title\n\n{START}\nnew content\n{END}\n\nmore text\n"


def test_splice_preserves_surrounding_content():
    original = f"before\n{START}\nold\n{END}\nafter\n"
    result = splice(original, "new")
    assert result.startswith("before\n")
    assert result.endswith("after\n")


def test_splice_raises_clear_error_when_markers_missing():
    with pytest.raises(MarkersNotFoundError, match="ATOM:JOB-STATUS"):
        splice("# Title\n\nno markers here\n", "new content")


def test_splice_raises_clear_error_when_only_start_marker_present():
    with pytest.raises(MarkersNotFoundError):
        splice(f"# Title\n\n{START}\nunterminated\n", "new content")


def test_splice_rejects_new_content_containing_marker_strings():
    with pytest.raises(MarkersNotFoundError):
        splice(f"# Title\n\n{START}\nold\n{END}\n", f"malicious {END}")
